
"""Milestone 07: audited, in-memory EURUSD M5 research features.

Only historical UTC candidates. No certified receipt times,
model training, trade admission, database mutations, or persisted
feature outputs.

Every indicator uses the current completed candle and earlier
contiguous candles. Gaps reset all rolling calculations.
"""

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from math import log
from pathlib import Path
from statistics import fmean, pstdev

from backend.models.audit_historical_utc_research_export import (
    ResearchAuditError,
    audit_research_export,
)

M5 = timedelta(minutes=5)
PIP = 0.0001

FEATURE_VERSION = "EURUSD_M5_UTC_CANDIDATE_FEATURES_V1"


class ResearchFeatureError(RuntimeError):
    """Reject unsafe or inconsistent research feature inputs."""


def _json(value):
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _rounded(value, digits=12):
    return round(float(value), digits)


@dataclass(frozen=True, slots=True)
class ResearchFeatureRow:
    source_row_id: int
    broker_label_time: str
    candidate_utc_open: str
    candidate_bar_end_utc: str
    candidate_offset_hours: int

    segment_id: int
    gap_before_m5: int

    close: float
    log_return_1: float | None
    range_pips: float
    body_pips: float

    sma_close_12: float | None
    volatility_log_return_12: float | None
    atr_14_pips: float | None

    tick_volume: int
    spread_points: int

    def record(self):
        return {
            "source_row_id": self.source_row_id,
            "broker_label_time": self.broker_label_time,
            "candidate_utc_open": self.candidate_utc_open,
            "candidate_bar_end_utc": self.candidate_bar_end_utc,
            "candidate_offset_hours": self.candidate_offset_hours,
            "segment_id": self.segment_id,
            "gap_before_m5": self.gap_before_m5,
            "close": self.close,
            "log_return_1": self.log_return_1,
            "range_pips": self.range_pips,
            "body_pips": self.body_pips,
            "sma_close_12": self.sma_close_12,
            "volatility_log_return_12":
                self.volatility_log_return_12,
            "atr_14_pips": self.atr_14_pips,
            "tick_volume": self.tick_volume,
            "spread_points": self.spread_points,
            "first_available_at": None,
            "research_only": True,
            "training_authorized": False,
            "execution_authorized": False,
        }


@dataclass(frozen=True, slots=True)
class ResearchFeatureSet:
    rows: tuple[ResearchFeatureRow, ...]
    source_lineage_sha256: str
    source_rows_sha256: str
    feature_rows_sha256: str
    segment_count: int
    gap_count: int
    unobserved_intervals: int

    def manifest(self):
        return {
            "feature_version": FEATURE_VERSION,
            "symbol": "EURUSD",
            "timeframe": "M5",
            "feature_count": len(self.rows),
            "segment_count": self.segment_count,
            "gap_count": self.gap_count,
            "unobserved_intervals":
                self.unobserved_intervals,
            "source_lineage_sha256":
                self.source_lineage_sha256,
            "source_rows_sha256": self.source_rows_sha256,
            "feature_rows_sha256":
                self.feature_rows_sha256,
            "sma_window_candles": 12,
            "volatility_window_returns": 12,
            "atr_window_candles": 14,
            "rolling_state_reset_on_gap": True,
            "features_at_candidate_bar_end_only": True,
            "historical_utc_certified": False,
            "source_receipt_certified": False,
            "first_available_at": None,
            "research_only": True,
            "historical_admission": False,
            "training_authorized": False,
            "execution_authorized": False,
        }


def build_clock_aware_research_features(
    directory: Path,
    *,
    max_rows: int = 100_000,
) -> ResearchFeatureSet:
    """Build audited, past-only, in-memory research features.

    Candidate bar-end timestamps are hypothetical.
    Actual historical market-data availability remains unknown.
    """

    if (
        type(max_rows) is not int
        or not 1 <= max_rows <= 500_000
    ):
        raise ResearchFeatureError(
            "max_rows must be from 1 to 500000."
        )

    folder = Path(directory)

    try:
        audited = audit_research_export(folder)
    except (ResearchAuditError, OSError) as exc:
        raise ResearchFeatureError(
            "Input research dataset failed audit."
        ) from exc

    if audited.row_count > max_rows:
        raise ResearchFeatureError(
            "Dataset exceeds the configured row limit."
        )

    rows = []
    seen = 0

    data_digest = sha256()
    feature_digest = sha256()

    closes = deque(maxlen=12)
    returns = deque(maxlen=12)
    true_ranges = deque(maxlen=14)

    previous_time = None
    previous_close = None

    segment = 0
    gaps = 0
    missing = 0

    try:
        with (folder / "candles.jsonl").open("rb") as stream:
            for raw in stream:
                data_digest.update(raw)
                seen += 1

                if seen > audited.row_count:
                    raise ResearchFeatureError(
                        "Unexpected additional dataset rows."
                    )

                candidate = json.loads(raw)
                source = candidate["source_row"]

                time = datetime.fromisoformat(
                    candidate["candidate_utc_open"]
                )

                bar_close = time + M5

                opening = float(source["open"])
                high = float(source["high"])
                low = float(source["low"])
                close = float(source["close"])

                gap_before = 0

                if previous_time is None:
                    segment = 1

                else:
                    elapsed = time - previous_time

                    if elapsed < M5 or elapsed % M5:
                        raise ResearchFeatureError(
                            "Feature candle order is invalid."
                        )

                    if elapsed > M5:
                        gap_before = (
                            int(elapsed // M5) - 1
                        )

                        gaps += 1
                        missing += gap_before
                        segment += 1

                        closes.clear()
                        returns.clear()
                        true_ranges.clear()

                        previous_close = None

                # Return is defined only when the preceding
                # candle is contiguous and already complete.

                single_return = (
                    _rounded(
                        log(close / previous_close)
                    )
                    if previous_close is not None
                    else None
                )

                # True range uses this bar's OHLC and
                # the previous contiguous bar's close.

                tr = max(
                    high - low,
                    (
                        abs(high - previous_close)
                        if previous_close is not None
                        else 0.0
                    ),
                    (
                        abs(low - previous_close)
                        if previous_close is not None
                        else 0.0
                    ),
                )

                closes.append(close)
                true_ranges.append(tr)

                if single_return is not None:
                    returns.append(single_return)

                feature = ResearchFeatureRow(
                    source_row_id=source["row_id"],
                    broker_label_time=source[
                        "broker_label_time"
                    ],
                    candidate_utc_open=candidate[
                        "candidate_utc_open"
                    ],
                    candidate_bar_end_utc=(
                        bar_close.isoformat()
                    ),
                    candidate_offset_hours=candidate[
                        "candidate_offset_hours"
                    ],
                    segment_id=segment,
                    gap_before_m5=gap_before,
                    close=close,
                    log_return_1=single_return,
                    range_pips=_rounded(
                        (high - low) / PIP, 8
                    ),
                    body_pips=_rounded(
                        (close - opening) / PIP, 8
                    ),
                    sma_close_12=(
                        _rounded(
                            fmean(closes), 8
                        )
                        if len(closes) == 12
                        else None
                    ),
                    volatility_log_return_12=(
                        _rounded(
                            pstdev(returns)
                        )
                        if len(returns) == 12
                        else None
                    ),
                    atr_14_pips=(
                        _rounded(
                            fmean(true_ranges) / PIP,
                            8,
                        )
                        if len(true_ranges) == 14
                        else None
                    ),
                    tick_volume=source[
                        "tick_volume"
                    ],
                    spread_points=source[
                        "spread_points"
                    ],
                )

                encoded = (
                    _json(feature.record()) + "\n"
                ).encode("utf-8")

                feature_digest.update(encoded)
                rows.append(feature)

                previous_time = time
                previous_close = close

    except (
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
    ) as exc:
        raise ResearchFeatureError(
            "Dataset changed or became invalid while reading."
        ) from exc

    if (
        seen != audited.row_count
        or data_digest.hexdigest()
        != audited.rows_sha256
    ):
        raise ResearchFeatureError(
            "Input bytes changed since audit."
        )

    if (
        gaps != audited.gap_count
        or missing != audited.unobserved_intervals
    ):
        raise ResearchFeatureError(
            "Observed gaps disagree with the audited dataset."
        )

    return ResearchFeatureSet(
        rows=tuple(rows),
        source_lineage_sha256=audited.lineage_sha256,
        source_rows_sha256=audited.rows_sha256,
        feature_rows_sha256=feature_digest.hexdigest(),
        segment_count=segment,
        gap_count=gaps,
        unobserved_intervals=missing,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Build research-only candidate features in memory."
        )
    )

    parser.add_argument(
        "--directory",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--max-rows",
        type=int,
        default=100_000,
    )

    args = parser.parse_args(argv)

    try:
        result = build_clock_aware_research_features(
            args.directory,
            max_rows=args.max_rows,
        )

    except ResearchFeatureError as exc:
        print(f"FEATURE BUILD BLOCKED: {exc}")
        return 1

    info = result.manifest()

    print(
        "EURUSD ORACLE | UTC-candidate research features"
    )

    print(
        f"Rows: {info['feature_count']} | "
        f"Segments: {info['segment_count']}"
    )

    print(
        f"Gaps: {info['gap_count']} | "
        "Unobserved M5 slots: "
        f"{info['unobserved_intervals']}"
    )

    print(
        f"Features SHA-256: "
        f"{info['feature_rows_sha256']}"
    )

    print(
        "NO FILES WRITTEN | UTC CERTIFICATION: NO | "
        "TRAINING: BLOCKED | EXECUTION: BLOCKED"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
