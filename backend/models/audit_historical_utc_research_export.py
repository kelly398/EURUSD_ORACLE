
"""Milestone 06: read-only structural audit of EURUSD research exports.

Does not certify historical UTC, causal availability, source authenticity,
model training suitability, or trading admission.
"""

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re

from backend.data_engine.clock_policy import implementation_sha256
from backend.models.historical_utc_reconstruction import (
    HistoricalReconstructionError,
    LegacyM5Bar,
    MAPPING_ID,
    reconstruct_historical_m5_bars,
)
from backend.models.historical_utc_research_export import (
    ResearchExportError,
    verify_research_export,
)

M5 = timedelta(minutes=5)
HEX64 = re.compile(r"[0-9a-f]{64}\Z")

TOP_FIELDS = frozenset({
    "source_row",
    "candidate_utc_open",
    "candidate_offset_hours",
    "clock_mapping_id",
    "historical_admission",
    "execution_authorized",
    "first_available_at",
})

SOURCE_FIELDS = frozenset({
    "row_id", "broker_label_time",
    "open", "high", "low", "close",
    "tick_volume", "spread_points", "real_volume",
    "symbol", "timeframe", "source", "is_complete",
})


class ResearchAuditError(RuntimeError):
    """Audit failed; dataset must not be admitted."""


def _sha_json(value):
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return sha256(payload.encode("utf-8")).hexdigest()


def _strict_json(raw: bytes):
    def no_constant(value):
        raise ValueError("Non-finite JSON constant")

    return json.loads(
        raw.decode("utf-8"),
        parse_constant=no_constant,
    )


def _safe_datetime(value):
    if not isinstance(value, str):
        raise ResearchAuditError(
            "Broker label must be an ISO timestamp string."
        )

    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ResearchAuditError(
            "Invalid broker label timestamp."
        ) from exc


@dataclass(frozen=True)
class AuditResult:
    row_count: int
    batch_count: int
    gap_count: int
    unobserved_intervals: int
    winter_count: int
    summer_count: int
    zero_tick_volume: int
    zero_spread: int
    first_broker_label: str
    last_broker_label: str
    rows_sha256: str
    lineage_sha256: str
    research_only: bool = True
    training_authorized: bool = False
    execution_authorized: bool = False


def audit_research_export(directory: Path) -> AuditResult:
    """Audit a saved research dataset without modifying it."""

    folder = Path(directory)

    try:
        manifest = verify_research_export(folder)
    except (ResearchExportError, OSError) as exc:
        raise ResearchAuditError(
            "Export file verification failed."
        ) from exc

    def require(condition, reason):
        if not condition:
            raise ResearchAuditError(reason)

    require(
        type(manifest.get("schema_version")) is int
        and manifest["schema_version"] == 2,
        "Unexpected dataset schema version.",
    )

    require(
        manifest.get("clock_mapping_id") == MAPPING_ID,
        "Unexpected historical clock mapping.",
    )

    source_hash = implementation_sha256()

    require(
        manifest.get("clock_mapping_implementation_sha256")
        == source_hash,
        "Clock implementation differs from exported evidence.",
    )

    count = manifest["record_count"]
    proof_list = manifest.get("batch_evidence_sha256")

    require(
        isinstance(proof_list, list)
        and 1 <= len(proof_list) <= count
        and all(
            isinstance(p, str) and HEX64.fullmatch(p)
            for p in proof_list
        ),
        "Invalid batch evidence list.",
    )

    require(
        type(manifest.get("batch_count")) is int
        and manifest["batch_count"] == len(proof_list),
        "Batch count does not match evidence.",
    )

    expected_lineage = _sha_json({
        "schema_version": 2,
        "dataset_version": manifest["dataset_version"],
        "mapping_id": MAPPING_ID,
        "mapping_implementation_sha256": source_hash,
        "batch_evidence_sha256": proof_list,
        "record_count": count,
        "rows_jsonl_sha256": manifest["rows_jsonl_sha256"],
    })

    require(
        expected_lineage == manifest["lineage_sha256"],
        "Dataset lineage fingerprint mismatch.",
    )

    observed = 0
    previous_label = None
    previous_utc = None

    first_label = None
    last_label = None
    first_utc = None
    last_utc = None
    first_id = None
    last_id = None

    seen_ids = set()

    winter = 0
    summer = 0
    gaps = 0
    missing = 0
    zeros = 0
    zero_spread = 0

    pending = []

    def check_chunk():
        nonlocal observed, previous_label, previous_utc
        nonlocal first_label, last_label, first_utc, last_utc
        nonlocal first_id, last_id
        nonlocal winter, summer, gaps, missing
        nonlocal zeros, zero_spread

        if not pending:
            return

        chunk_ids = [
            bar.row_id
            for _, bar in pending
        ]

        if len(chunk_ids) != len(set(chunk_ids)):
            raise ResearchAuditError(
                "Duplicate source row ID."
            )

        try:
            reconstructed = reconstruct_historical_m5_bars(
                bar for _, bar in pending
            )
        except HistoricalReconstructionError as exc:
            raise ResearchAuditError(
                "Source candle reconstruction rejected."
            ) from exc

        for (stored, original), calculated in zip(
            pending, reconstructed.records
        ):
            require(
                stored == calculated.record(),
                "Stored UTC candidate differs from "
                "reconstructed source.",
            )

            label = original.broker_label_time
            utc_time = calculated.candidate_utc_open

            require(
                original.row_id not in seen_ids,
                "Duplicate source row ID.",
            )

            require(
                previous_label is None
                or label > previous_label,
                "Broker labels are duplicated or out of order.",
            )

            if previous_utc is not None:
                difference = utc_time - previous_utc

                require(
                    difference >= M5
                    and difference % M5 == timedelta(0),
                    "UTC candidate timing is duplicated or unaligned.",
                )

                if difference > M5:
                    gaps += 1
                    missing += difference // M5 - 1

            if observed == 0:
                first_label = label.isoformat()
                first_utc = utc_time.isoformat()
                first_id = original.row_id

            last_label = label.isoformat()
            last_utc = utc_time.isoformat()
            last_id = original.row_id

            observed += 1
            seen_ids.add(original.row_id)

            previous_label = label
            previous_utc = utc_time

            winter += calculated.candidate_offset_hours == 2
            summer += calculated.candidate_offset_hours == 3

            zeros += original.tick_volume == 0
            zero_spread += original.spread_points == 0

        pending.clear()

    try:
        with (folder / "candles.jsonl").open("rb") as stream:
            for line in stream:

                require(
                    len(line) <= 16_384,
                    "JSONL row exceeds the size limit.",
                )

                require(
                    line.endswith(b"\n"),
                    "JSONL row missing newline.",
                )

                require(
                    observed + len(pending) < count,
                    "More rows than manifest declares.",
                )

                stored = _strict_json(line)

                require(
                    isinstance(stored, dict)
                    and set(stored) == TOP_FIELDS,
                    "Unexpected UTC candidate row schema.",
                )

                source = stored["source_row"]

                require(
                    isinstance(source, dict)
                    and set(source) == SOURCE_FIELDS,
                    "Unexpected legacy source row schema.",
                )

                canonical = (
                    json.dumps(
                        stored,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    ) + "\n"
                ).encode("utf-8")

                require(
                    canonical == line,
                    "Non-canonical JSONL data.",
                )

                require(
                    stored["historical_admission"] is False
                    and stored["execution_authorized"] is False
                    and stored["first_available_at"] is None,
                    "Unsafe authorization or causal availability field.",
                )

                require(
                    stored["clock_mapping_id"] == MAPPING_ID,
                    "Unexpected row clock mapping.",
                )

                bar = LegacyM5Bar(
                    row_id=source["row_id"],
                    broker_label_time=_safe_datetime(
                        source["broker_label_time"]
                    ),
                    open=source["open"],
                    high=source["high"],
                    low=source["low"],
                    close=source["close"],
                    tick_volume=source["tick_volume"],
                    spread_points=source["spread_points"],
                    real_volume=source["real_volume"],
                    symbol=source["symbol"],
                    timeframe=source["timeframe"],
                    source=source["source"],
                    is_complete=source["is_complete"],
                )

                pending.append((stored, bar))

                if len(pending) == 256:
                    check_chunk()

            check_chunk()

    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
        HistoricalReconstructionError,
    ) as exc:
        raise ResearchAuditError(
            "Dataset rows failed structural audit."
        ) from exc

    require(
        observed == count,
        "Record count mismatch.",
    )

    expected = {
        "first_source_row_id": first_id,
        "last_source_row_id": last_id,
        "first_broker_label": first_label,
        "last_broker_label": last_label,
        "first_candidate_utc": first_utc,
        "last_candidate_utc": last_utc,
        "winter_count": winter,
        "summer_count": summer,
        "gap_count": gaps,
        "unobserved_intervals": missing,
        "unobserved_intervals_include_market_closures": True,
    }

    for field, correct in expected.items():
        require(
            type(manifest.get(field)) is type(correct)
            and manifest[field] == correct,
            f"Dataset manifest statistic mismatch: {field}.",
        )

    return AuditResult(
        row_count=observed,
        batch_count=len(proof_list),
        gap_count=gaps,
        unobserved_intervals=missing,
        winter_count=winter,
        summer_count=summer,
        zero_tick_volume=zeros,
        zero_spread=zero_spread,
        first_broker_label=first_label,
        last_broker_label=last_label,
        rows_sha256=manifest["rows_jsonl_sha256"],
        lineage_sha256=manifest["lineage_sha256"],
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect a saved UTC-candidate research export. "
            "Read-only operation."
        )
    )

    parser.add_argument(
        "--directory",
        type=Path,
        required=True,
    )

    args = parser.parse_args(argv)

    try:
        report = audit_research_export(args.directory)
    except ResearchAuditError as exc:
        print(f"AUDIT FAILED: {exc}")
        return 1

    print("EURUSD ORACLE | Historical research export audit")

    print(
        f"Rows: {report.row_count} | "
        f"Batches: {report.batch_count}"
    )

    print(
        f"Winter: {report.winter_count} | "
        f"Summer: {report.summer_count}"
    )

    print(
        f"Gaps: {report.gap_count} | "
        f"Unobserved M5 slots: {report.unobserved_intervals}"
    )

    print(
        f"Zero tick volume: {report.zero_tick_volume} | "
        f"Zero spread: {report.zero_spread}"
    )

    print(
        f"Broker labels: {report.first_broker_label} "
        f"to {report.last_broker_label}"
    )

    print(f"Rows SHA-256: {report.rows_sha256}")

    print("AUDIT PASSED: local structural consistency only.")

    print(
        "UTC CERTIFICATION: NO | "
        "TRAINING: BLOCKED | "
        "EXECUTION: BLOCKED"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
