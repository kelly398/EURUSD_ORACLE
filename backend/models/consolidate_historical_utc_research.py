
"""Milestone 12: audited, metadata-only consolidation of EURUSD research windows.

No database access, no re-exported candles, and no trading/model admission.
Each component remains a separate research export. The optional index points
at its content digests and declared broker-label ranges, not local paths.
Missing M5 slots remain UNCLASSIFIED; weekends are not automatically certified.
"""

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from backend.models.audit_historical_utc_research_export import (
    ResearchAuditError,
    audit_research_export,
)

M5 = timedelta(minutes=5)
MAX_WINDOWS = 64
MAX_ROWS = 500_000
VERSION = "EURUSD_M5_UTC_RESEARCH_CONSOLIDATION_INDEX_V1"
DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


class ConsolidationError(RuntimeError):
    """A consolidation input, proof, or publication is unsafe."""


def _canonical(value):
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def _day(value):
    if not isinstance(value, str) or not DATE.fullmatch(value):
        raise ConsolidationError("Date must be YYYY-MM-DD.")

    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise ConsolidationError(
            "Invalid calendar date."
        ) from exc

    if not 2000 <= result.year <= 2100:
        raise ConsolidationError(
            "Unsupported date year."
        )

    return result


@dataclass(frozen=True)
class ResearchWindow:
    start_date: date
    end_date: date
    directory: Path

    def __post_init__(self):
        if (
            type(self.start_date) is not date
            or type(self.end_date) is not date
        ):
            raise ConsolidationError(
                "Windows require dates, not datetimes."
            )

        if not 1 <= (
            self.end_date - self.start_date
        ).days <= 90:
            raise ConsolidationError(
                "Window length must be 1 to 90 days."
            )

        if not 2000 <= self.start_date.year <= 2100:
            raise ConsolidationError(
                "Window date outside supported range."
            )

        if not 2000 <= self.end_date.year <= 2100:
            raise ConsolidationError(
                "Window date outside supported range."
            )


def _parse_window(raw):
    # Two delimiters only. Windows drive letters in
    # the third field are safe.

    parts = raw.split(":", 2)

    if len(parts) != 3 or not parts[2].strip():
        raise argparse.ArgumentTypeError(
            "Use START_DATE:END_DATE:DATASET_DIRECTORY"
        )

    try:
        return ResearchWindow(
            _day(parts[0]),
            _day(parts[1]),
            Path(parts[2]),
        )

    except ConsolidationError as exc:
        raise argparse.ArgumentTypeError(
            str(exc)
        ) from exc


def inspect_windows(windows, *, max_rows=MAX_ROWS):
    """Re-audit each source and verify ordering across windows.

    The index is only a content-addressed inventory.
    It does not prove complete database capture or clock provenance.
    """

    if (
        type(max_rows) is not int
        or not 1 <= max_rows <= MAX_ROWS
    ):
        raise ConsolidationError(
            "max_rows must be between 1 and 500000."
        )

    windows = tuple(windows)

    if not 1 <= len(windows) <= MAX_WINDOWS:
        raise ConsolidationError(
            "Requires 1 to 64 input windows."
        )

    if any(
        not isinstance(window, ResearchWindow)
        for window in windows
    ):
        raise ConsolidationError(
            "Invalid window object."
        )

    seen_ids = set()
    seen_parent = set()

    previous_end = None
    previous_label = None
    previous_utc = None

    row_hash = sha256()

    total_rows = 0
    total_gaps = 0
    total_missing = 0

    winter = 0
    summer = 0

    parents = []

    for window in windows:

        if (
            previous_end is not None
            and window.start_date != previous_end
        ):
            raise ConsolidationError(
                "Window bounds are not contiguous and ordered."
            )

        previous_end = window.end_date

        try:
            audited = audit_research_export(
                window.directory
            )

        except (ResearchAuditError, OSError) as exc:
            raise ConsolidationError(
                "An input window failed its source audit."
            ) from exc

        if audited.lineage_sha256 in seen_parent:
            raise ConsolidationError(
                "Duplicate source export lineage."
            )

        seen_parent.add(audited.lineage_sha256)

        if total_rows + audited.row_count > max_rows:
            raise ConsolidationError(
                "Combined row limit exceeded."
            )

        input_hash = sha256()
        input_rows = 0
        boundary_missing = 0

        first_label = None
        first_candidate = None
        last_label = None
        last_candidate = None

        try:
            with (
                Path(window.directory) / "candles.jsonl"
            ).open("rb") as stream:

                for raw in stream:
                    input_hash.update(raw)
                    row_hash.update(raw)

                    entry = json.loads(raw)
                    source = entry["source_row"]

                    label = datetime.fromisoformat(
                        source["broker_label_time"]
                    )

                    candidate = datetime.fromisoformat(
                        entry["candidate_utc_open"]
                    )

                    row_id = source["row_id"]

                    if (
                        label.utcoffset() != timedelta(0)
                        or candidate.utcoffset() != timedelta(0)
                        or not (
                            window.start_date
                            <= label.date()
                            < window.end_date
                        )
                    ):
                        raise ConsolidationError(
                            "Candle outside its declared "
                            "broker-label window."
                        )

                    if (
                        type(row_id) is not int
                        or row_id in seen_ids
                    ):
                        raise ConsolidationError(
                            "Source row ID repeated across windows."
                        )

                    if (
                        previous_label is not None
                        and label <= previous_label
                    ):
                        raise ConsolidationError(
                            "Broker labels overlap or go backwards."
                        )

                    if previous_utc is not None:

                        delta = candidate - previous_utc

                        if delta < M5 or delta % M5:
                            raise ConsolidationError(
                                "Candidate UTC is nonmonotonic or non-M5."
                            )

                        if delta > M5:
                            total_gaps += 1

                            slots = int(
                                delta // M5
                            ) - 1

                            total_missing += slots

                            if input_rows == 0:
                                boundary_missing = slots

                    if input_rows == 0:
                        first_label = label.isoformat()
                        first_candidate = candidate.isoformat()

                    seen_ids.add(row_id)

                    previous_label = label
                    previous_utc = candidate

                    last_label = label.isoformat()
                    last_candidate = candidate.isoformat()

                    winter += (
                        entry["candidate_offset_hours"] == 2
                    )

                    summer += (
                        entry["candidate_offset_hours"] == 3
                    )

                    input_rows += 1

        except ConsolidationError:
            raise

        except (
            OSError,
            ValueError,
            TypeError,
            KeyError,
            UnicodeError,
        ) as exc:
            raise ConsolidationError(
                "Source changed or cannot be reread."
            ) from exc

        if (
            input_rows != audited.row_count
            or input_hash.hexdigest() != audited.rows_sha256
            or first_label != audited.first_broker_label
            or last_label != audited.last_broker_label
        ):
            raise ConsolidationError(
                "Source content changed after audit."
            )

        parents.append({
            "broker_label_window_start":
                window.start_date.isoformat(),

            "broker_label_window_end_exclusive":
                window.end_date.isoformat(),

            "source_lineage_sha256":
                audited.lineage_sha256,

            "source_rows_sha256":
                audited.rows_sha256,

            "source_row_count":
                input_rows,

            "first_broker_label":
                first_label,

            "last_broker_label":
                last_label,

            "first_candidate_utc":
                first_candidate,

            "last_candidate_utc":
                last_candidate,

            "unclassified_boundary_missing_m5_slots":
                boundary_missing,
        })

        total_rows += input_rows

    manifest = {
        "index_version": VERSION,
        "schema_version": 1,

        "symbol": "EURUSD",
        "timeframe": "M5",

        "clock_basis":
            "uncertified_broker_wall_labels",

        "window_count": len(parents),
        "row_count": total_rows,

        "source_windows": parents,

        "combined_rows_jsonl_sha256":
            row_hash.hexdigest(),

        "gap_count": total_gaps,

        "unclassified_missing_m5_slots":
            total_missing,

        "winter_count": winter,
        "summer_count": summer,

        "gaps_are_market_closures_certified": False,
        "all_windows_one_atomic_snapshot": False,

        "historical_utc_certified": False,
        "source_receipt_certified": False,

        "research_only": True,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
    }

    # Excludes machine-specific paths.
    # This makes the identity portable.

    manifest["index_sha256"] = sha256(
        _canonical(manifest)
    ).hexdigest()

    return manifest


def publish_index(manifest, project_root):
    """Publish an immutable metadata index, not a candle copy."""

    root = Path(project_root).absolute()

    if (
        root.is_symlink()
        or not (
            root / "backend" / "models"
        ).is_dir()
    ):
        raise ConsolidationError(
            "Invalid ORACLE project root."
        )

    if (
        manifest.get("index_version") != VERSION
        or manifest.get("research_only") is not True
        or manifest.get("training_authorized") is not False
        or manifest.get("execution_authorized") is not False
    ):
        raise ConsolidationError(
            "Unsafe index manifest."
        )

    proof = manifest.get("index_sha256")

    if (
        not isinstance(proof, str)
        or not re.fullmatch(
            r"[0-9a-f]{64}",
            proof,
        )
    ):
        raise ConsolidationError(
            "Missing or malformed index digest."
        )

    unsigned = {
        key: value
        for key, value in manifest.items()
        if key != "index_sha256"
    }

    if sha256(
        _canonical(unsigned)
    ).hexdigest() != proof:
        raise ConsolidationError(
            "Index digest mismatch."
        )

    for component in (
        "data",
        "research",
        "historical_utc_indexes_v1",
    ):
        child = root / component

        if (
            child.is_symlink()
            or (
                child.exists()
                and not child.is_dir()
            )
        ):
            raise ConsolidationError(
                "Unsafe research export folder."
            )

        child.mkdir(exist_ok=True)
        root = child

    output = root / (
        "index_" + proof
    )

    lock = root / ".index_export.lock"

    if lock.is_symlink():
        raise ConsolidationError(
            "Unsafe lock."
        )

    try:
        fd = os.open(
            lock,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )

    except OSError as exc:
        raise ConsolidationError(
            "Index export locked or unavailable."
        ) from exc

    stage = None

    try:
        os.close(fd)

        if output.exists() or output.is_symlink():
            raise ConsolidationError(
                "Index already exists; refusing overwrite."
            )

        stage = Path(
            tempfile.mkdtemp(
                prefix=".stage_",
                dir=root,
            )
        )

        payload = _canonical(manifest)

        with (
            stage / "manifest.json"
        ).open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

        with (
            stage / "checksums.sha256"
        ).open("x", encoding="ascii") as stream:
            stream.write(
                f"{sha256(payload).hexdigest()}"
                "  manifest.json\n"
            )

            stream.flush()
            os.fsync(stream.fileno())

        if output.exists() or output.is_symlink():
            raise ConsolidationError(
                "Index destination appeared during publication."
            )

        os.rename(stage, output)
        stage = None

        return output

    finally:
        if stage is not None:
            shutil.rmtree(stage)

        lock.unlink(missing_ok=True)


def run(argv=None, *, project_root=None):
    parser = argparse.ArgumentParser(
        description=(
            "Audited EURUSD research consolidation index"
        )
    )

    parser.add_argument(
        "--window",
        action="append",
        type=_parse_window,
        required=True,
        help=(
            "START_DATE:END_DATE:EXPORTED_DATASET_DIRECTORY "
            "(repeat)"
        ),
    )

    parser.add_argument(
        "--research-only",
        action="store_true",
        required=True,
    )

    parser.add_argument(
        "--publish-index",
        action="store_true",
        help=(
            "Save only an immutable metadata index; "
            "never duplicate candles"
        ),
    )

    args = parser.parse_args(argv)
    published = None

    try:
        manifest = inspect_windows(
            args.window
        )

        print(
            f"WINDOWS AUDITED: "
            f"{manifest['window_count']}"
        )

        print(
            f"RESEARCH CANDLES: "
            f"{manifest['row_count']}"
        )

        print(
            "CROSS-WINDOW + INTERNAL GAPS: "
            f"{manifest['gap_count']}"
        )

        print(
            "UNCLASSIFIED MISSING M5 SLOTS: "
            f"{manifest['unclassified_missing_m5_slots']}"
        )

        print(
            "COMBINED ROWS SHA-256: "
            f"{manifest['combined_rows_jsonl_sha256']}"
        )

        if args.publish_index:

            root = (
                project_root
                or Path(__file__).resolve().parents[2]
            )

            published = publish_index(
                manifest,
                root,
            )

            print(
                f"INDEX PUBLISHED: {published}"
            )

        else:
            print(
                "PREVIEW ONLY: no files written."
            )

        print(
            "UTC UNCERTIFIED | "
            "TRAINING BLOCKED | "
            "EXECUTION BLOCKED"
        )

        return 0

    except ConsolidationError as exc:

        if published is not None:
            print(
                f"INDEX NEEDS REVIEW: {published}"
            )

        else:
            print(
                f"CONSOLIDATION BLOCKED: {exc}"
            )

        return 1


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
