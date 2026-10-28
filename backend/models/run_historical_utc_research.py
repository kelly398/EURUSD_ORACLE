
"""Milestone 05: explicit, fail-closed EURUSD historical research CLI.

Default mode previews validated historical research data.
Add --export only after inspecting the preview.

Never certifies legacy broker timestamps, changes source data,
authorizes model training, or enables trading.
"""

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
from typing import Sequence

from backend.models.historical_readonly_adapter import (
    HistoricalArchiveReadError,
    HistoricalArchiveWindow,
    iter_reconstructed_archive_batches,
)

from backend.models.historical_utc_research_dataset import (
    build_historical_utc_research_dataset,
)

from backend.models.historical_utc_research_export import (
    export_research_dataset,
    verify_research_export,
)


_LABEL_FORMAT = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:Z|\+00:00)\Z"
)


def _broker_label(value: str) -> datetime:
    """Parse UTC-tagged broker labels, not certified UTC instants."""

    if not _LABEL_FORMAT.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "Expected an M5 broker label: YYYY-MM-DDTHH:MM:00Z"
        )

    try:
        result = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Invalid calendar date or time."
        ) from exc

    if (
        result.utcoffset() != timedelta(0)
        or result.minute % 5
    ):
        raise argparse.ArgumentTypeError(
            "Broker label must be M5-aligned in +00:00."
        )

    return result.astimezone(timezone.utc)


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Expected a positive integer."
        ) from exc

    if result < 1:
        raise argparse.ArgumentTypeError(
            "Expected a positive integer."
        )

    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read legacy EURUSD M5 broker labels as "
            "uncertified UTC research candidates."
        )
    )

    parser.add_argument(
        "--start-label",
        type=_broker_label,
        required=True,
    )

    parser.add_argument(
        "--end-label",
        type=_broker_label,
        required=True,
    )

    parser.add_argument(
        "--page-size",
        type=_positive_int,
        default=500,
    )

    parser.add_argument(
        "--max-rows",
        type=_positive_int,
        default=100_000,
    )

    parser.add_argument(
        "--research-only",
        action="store_true",
        required=True,
        help="Acknowledges that legacy UTC is not certified.",
    )

    parser.add_argument(
        "--export",
        action="store_true",
        help="Publish a new research-only dataset.",
    )

    return parser


def _require_research_manifest(meta: dict) -> None:
    """Prevent promotion of unsafe or uncertified datasets."""

    expected = {
        "dataset_version":
            "EURUSD_M5_HISTORICAL_UTC_RESEARCH_V2",
        "symbol": "EURUSD",
        "timeframe": "M5",
        "research_only": True,
        "historical_admission": False,
        "execution_authorized": False,
        "training_authorized": False,
        "source_receipt_certified": False,
        "first_available_at": None,
    }

    if not isinstance(meta, dict):
        raise RuntimeError(
            "Research manifest is not a dictionary."
        )

    for field, required in expected.items():
        if (
            type(meta.get(field)) is not type(required)
            or meta[field] != required
        ):
            raise RuntimeError(
                f"Research-only safety field invalid: {field}."
            )

    if (
        type(meta.get("record_count")) is not int
        or meta["record_count"] <= 0
    ):
        raise RuntimeError(
            "Research manifest has no valid record count."
        )


def run(
    argv: Sequence[str] | None = None,
    *,
    engine=None,
    project_root: Path | None = None,
) -> int:
    """Run one bounded, research-only historical extraction.

    Return codes:
        0: success
        1: operational failure
        2: invalid input
        130: interrupted
    """

    args = _parser().parse_args(argv)

    try:
        window = HistoricalArchiveWindow(
            start_label=args.start_label,
            end_label=args.end_label,
            page_size=args.page_size,
            max_rows=args.max_rows,
        )

    except (
        ValueError,
        HistoricalArchiveReadError,
    ) as exc:
        print(f"REJECTED: {exc}")
        return 2

    print(
        "EURUSD ORACLE | Historical UTC research only"
    )

    print(
        "Clock: UTC-tagged broker labels, "
        "NOT certified UTC event time"
    )

    print(
        f"Window: {window.start_label.isoformat()} "
        f"to {window.end_label.isoformat()} (exclusive)"
    )

    mode = "EXPORT" if args.export else "PREVIEW"

    print(
        f"Mode: {mode} | row cap: {window.max_rows}"
    )

    published = None

    try:
        if engine is None:
            # Import only after validation succeeds.
            from backend.database.connection import (
                engine as configured_engine,
            )

            engine = configured_engine

        # Exhaust the complete read-only PostgreSQL snapshot.
        # Failures prevent publication of partial datasets.
        dataset = build_historical_utc_research_dataset(
            iter_reconstructed_archive_batches(
                engine,
                window,
            ),
            max_rows=window.max_rows,
        )

        manifest = dataset.manifest()

        _require_research_manifest(manifest)

        print(
            f"Validated rows: {manifest['record_count']}"
        )

        print(
            "Unobserved M5 intervals "
            "(including market closures): "
            f"{manifest['unobserved_intervals']}"
        )

        print(
            "Candidate rows SHA-256: "
            f"{manifest['rows_jsonl_sha256']}"
        )

        print(
            "Admission: BLOCKED | "
            "Training: BLOCKED | "
            "Execution: BLOCKED"
        )

        if not args.export:
            print(
                "PREVIEW COMPLETE. "
                "No research dataset was written."
            )
            return 0

        root = (
            Path(project_root)
            if project_root is not None
            else Path(__file__).resolve().parents[2]
        )

        output = export_research_dataset(
            dataset,
            root,
        )

        published = output

        verified = verify_research_export(output)

        _require_research_manifest(verified)

        if (
            verified["rows_jsonl_sha256"]
            != manifest["rows_jsonl_sha256"]
            or verified["lineage_sha256"]
            != manifest["lineage_sha256"]
            or verified["record_count"]
            != manifest["record_count"]
        ):
            raise RuntimeError(
                "Published dataset did not match "
                "the source dataset."
            )

        print(f"EXPORT VERIFIED: {output}")

        return 0

    except KeyboardInterrupt:
        print(
            "STOPPED: Interrupted. "
            "No dataset promotion is permitted."
        )
        return 130

    except Exception as exc:
        # Never expose connection URLs or credentials.
        # Publication may have completed before verification.

        if published is not None:
            print(
                "STOPPED: Published export needs "
                "manual review after "
                f"{type(exc).__name__}. "
                f"Path: {published}"
            )
        else:
            print(
                f"STOPPED: {type(exc).__name__}. "
                "No research dataset was exported "
                "by this run."
            )

        return 1


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
