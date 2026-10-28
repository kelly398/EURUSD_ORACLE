"""Read-only research candidates for legacy EUR/USD M5 broker-clock bars.

The legacy BrokerCandle.timestamp values were stored as UTC-looking broker-wall
labels. They must not be silently reinterpreted as certified UTC timestamps.
This module derives *candidates* under the observed HFM seasonal hypothesis.
It never queries a broker, writes a database, creates training samples, or
upgrades a candidate to a production/admissible feed.

The historical hypothesis is NOT the expiring forward clock policy. The mapping
implementation fingerprint is retained for reproducibility, not admission.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
from typing import Iterable, Literal

from backend.data_engine.clock_policy import (
    ClockPolicyError,
    hfm_utc_to_wall,
    hfm_wall_to_utc_msc,
    implementation_sha256,
    instant,
    utc_msc,
)

UTC = timezone.utc
MAPPING_ID = "hfm_eu_wall_v1_historical_candidate"
FIVE_MINUTES = timedelta(minutes=5)


class HistoricalReconstructionError(ValueError):
    """An input cannot be safely represented as a research-only UTC candidate."""


def _require_utc(value: datetime, field: str) -> datetime:
    # Do not convert other time zones silently: legacy UTC-looking values
    # encode a broker wall label, not an actual instant.
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise HistoricalReconstructionError(f"{field} must be a UTC-tagged broker label.")
    return value.astimezone(UTC)


def _money(value: float, field: str, *, positive: bool) -> None:
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        raise HistoricalReconstructionError(f"{field} must be a numeric value.")
    try:
        valid = isfinite(value) and (value > 0 if positive else value >= 0)
    except (OverflowError, TypeError, ValueError):
        valid = False
    if not valid:
        raise HistoricalReconstructionError(f"{field} must be finite and valid.")


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class LegacyM5Bar:
    """Explicit input from legacy broker_candles, BEFORE UTC certification.

    broker_label_time is the *original* timezone-mislabelled value from the old
    table. It is intentionally not named `timestamp_utc`.
    """

    row_id: int
    broker_label_time: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread_points: int
    real_volume: float
    symbol: Literal["EURUSD"] = "EURUSD"
    timeframe: Literal["M5"] = "M5"
    source: Literal["MT5"] = "MT5"
    is_complete: Literal[True] = True

    def __post_init__(self) -> None:
        for name in ("row_id", "tick_volume", "spread_points"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise HistoricalReconstructionError(f"{name} must be an integer.")
        if self.row_id <= 0 or self.tick_volume < 0 or self.spread_points < 0:
            raise HistoricalReconstructionError("Invalid row ID, volume, or spread.")
        if self.symbol != "EURUSD" or self.timeframe != "M5" or self.source != "MT5":
            raise HistoricalReconstructionError("Unexpected legacy source/symbol/timeframe.")
        if self.is_complete is not True:
            raise HistoricalReconstructionError("Incomplete legacy bars are not admitted.")

        raw = _require_utc(self.broker_label_time, "broker_label_time")
        if raw.second or raw.microsecond or raw.minute % 5:
            raise HistoricalReconstructionError("Raw broker bar label must align to M5.")
        if raw.year < 2000:
            raise HistoricalReconstructionError("Out-of-range legacy bar label.")
        for name in ("open", "high", "low", "close"):
            _money(getattr(self, name), name, positive=True)
        _money(self.real_volume, "real_volume", positive=False)
        if self.high < max(self.open, self.close, self.low):
            raise HistoricalReconstructionError("Invalid OHLC high.")
        if self.low > min(self.open, self.close, self.high):
            raise HistoricalReconstructionError("Invalid OHLC low.")

    def record(self) -> dict:
        return {
            "row_id": self.row_id,
            "broker_label_time": self.broker_label_time.isoformat(),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "tick_volume": self.tick_volume,
            "spread_points": self.spread_points,
            "real_volume": self.real_volume,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "source": self.source,
            "is_complete": self.is_complete,
        }


@dataclass(frozen=True, slots=True)
class HistoricalUTCBarCandidate:
    original: LegacyM5Bar
    candidate_utc_open: datetime
    candidate_offset_hours: Literal[2, 3]
    clock_mapping_id: Literal["hfm_eu_wall_v1_historical_candidate"] = MAPPING_ID

    @property
    def historical_admission(self) -> Literal[False]:
        return False

    @property
    def execution_authorized(self) -> Literal[False]:
        return False

    @property
    def first_available_at(self) -> None:
        # Old ingestion time is not the original market receipt timestamp.
        # Historical causal availability therefore remains unknown.
        return None

    def record(self) -> dict:
        return {
            "source_row": self.original.record(),
            "candidate_utc_open": self.candidate_utc_open.isoformat(),
            "candidate_offset_hours": self.candidate_offset_hours,
            "clock_mapping_id": self.clock_mapping_id,
            "historical_admission": False,
            "execution_authorized": False,
            "first_available_at": None,
        }


@dataclass(frozen=True, slots=True)
class ReconstructionBatch:
    records: tuple[HistoricalUTCBarCandidate, ...]
    mapping_implementation_sha256: str
    input_sha256: str
    output_sha256: str
    evidence_sha256: str
    winter_count: int
    summer_count: int

    @property
    def research_only(self) -> Literal[True]:
        return True

    @property
    def historical_admission(self) -> Literal[False]:
        return False

    def manifest(self) -> dict:
        return {
            "schema_version": 1,
            "clock_mapping_id": MAPPING_ID,
            "clock_mapping_implementation_sha256": self.mapping_implementation_sha256,
            "input_sha256": self.input_sha256,
            "output_sha256": self.output_sha256,
            "evidence_sha256": self.evidence_sha256,
            "record_count": len(self.records),
            "winter_count": self.winter_count,
            "summer_count": self.summer_count,
            "historical_admission": False,
            "execution_authorized": False,
            "research_only": True,
            "source_receipt_certified": False,
        }


def reconstruct_historical_m5_bars(rows: Iterable[LegacyM5Bar]) -> ReconstructionBatch:
    """Convert ordered legacy bars, aborting rather than dropping risky rows.

    Caller must select `broker_candles` source=MT5, EURUSD M5, complete bars
    and supply the exact stored broker labels. This function performs no I/O.
    Results must not feed production, live model compatibility, or paper trades.
    """
    originals: list[LegacyM5Bar] = []
    converted: list[HistoricalUTCBarCandidate] = []
    seen_ids: set[int] = set()
    seen_utc: set[datetime] = set()
    previous_label: datetime | None = None
    previous_utc: datetime | None = None
    winter = summer = 0

    for raw in rows:
        if not isinstance(raw, LegacyM5Bar):
            raise HistoricalReconstructionError("Expected a LegacyM5Bar input.")
        # Dataclasses are frozen, but the caller could deliberately bypass the
        # constructor. Always verify the value on this trust boundary.
        raw.__post_init__()
        label = _require_utc(raw.broker_label_time, "broker_label_time")
        if raw.row_id in seen_ids:
            raise HistoricalReconstructionError("Duplicate source row ID.")
        if previous_label is not None and label <= previous_label:
            raise HistoricalReconstructionError("Legacy bars must be ordered and unique.")
        try:
            normalized_ms = hfm_wall_to_utc_msc(utc_msc(label))
            corrected = instant(normalized_ms)
            if hfm_utc_to_wall(corrected) != label:
                raise HistoricalReconstructionError("Historical clock mapping does not round-trip.")
        except ClockPolicyError as exc:
            raise HistoricalReconstructionError(f"Legacy broker clock candidate blocked: {exc}") from exc
        if corrected.second or corrected.microsecond or corrected.minute % 5:
            raise HistoricalReconstructionError("Candidate UTC bar is not aligned to M5.")
        if corrected in seen_utc or (previous_utc is not None and corrected <= previous_utc):
            raise HistoricalReconstructionError("Duplicated or nonmonotonic candidate UTC time.")
        hours = (label - corrected) // timedelta(hours=1)
        if hours not in (2, 3):
            raise HistoricalReconstructionError("Unsupported broker clock offset.")
        originals.append(raw)
        converted.append(HistoricalUTCBarCandidate(raw, corrected, hours))
        seen_ids.add(raw.row_id)
        seen_utc.add(corrected)
        previous_label = label
        previous_utc = corrected
        if hours == 2:
            winter += 1
        else:
            summer += 1
    if not converted:
        raise HistoricalReconstructionError("An empty input cannot produce a dataset candidate.")

    input_hash = _digest([x.record() for x in originals])
    output_hash = _digest([x.record() for x in converted])
    source_hash = implementation_sha256()
    return ReconstructionBatch(
        records=tuple(converted),
        mapping_implementation_sha256=source_hash,
        input_sha256=input_hash,
        output_sha256=output_hash,
        evidence_sha256=_digest({"mapping": MAPPING_ID, "mapping_source": source_hash,
                                 "input": input_hash, "output": output_hash}),
        winter_count=winter,
        summer_count=summer,
    )
