
"""Milestone 14: verified, metadata-only registry of disjoint research indexes.

Reads immutable Milestone 12 index manifests, not candles or PostgreSQL.
Inter-index time spans are deliberately excluded; they are not observed gaps.
No historical admission, training authorization, or execution authority.
"""

import argparse
from datetime import date, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from backend.models.consolidate_historical_utc_research import (
    VERSION as INDEX_VERSION,
    _canonical,
)

VERSION = "EURUSD_M5_UTC_RESEARCH_SEASON_REGISTRY_V1"
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
MAX_INDEXES = 16
MAX_ROWS = 500_000
MAX_MANIFEST_BYTES = 2_000_000


class SeasonRegistryError(RuntimeError):
    """Untrusted index or unsafe registry operation."""


def _digest(value):
    return isinstance(value, str) and DIGEST.fullmatch(value) is not None


def _number(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise SeasonRegistryError(f"Invalid {name}.")
    return value


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise SeasonRegistryError("Invalid broker-label date.")
    try:
        result = date.fromisoformat(value)
    except ValueError as exc:
        raise SeasonRegistryError("Invalid broker-label date.") from exc
    if not 2000 <= result.year <= 2100:
        raise SeasonRegistryError("Unsupported broker-label year.")
    return result


def _timestamp(value):
    if not isinstance(value, str):
        raise SeasonRegistryError("Invalid source timestamp.")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise SeasonRegistryError("Invalid source timestamp.") from exc
    if result.utcoffset() != timedelta(0):
        raise SeasonRegistryError("Source timestamp is not UTC-tagged.")
    return result


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SeasonRegistryError("Duplicate JSON key.")
        result[key] = value
    return result


def read_index(directory):
    """Check an immutable Milestone 12 index and its internal consistency."""
    root = Path(directory)
    manifest_file = root / "manifest.json"
    checksum_file = root / "checksums.sha256"
    if (root.is_symlink() or not root.is_dir()
            or any(p.is_symlink() or not p.is_file()
                   for p in (manifest_file, checksum_file))):
        raise SeasonRegistryError("Index directory or files are unsafe.")
    try:
        if (manifest_file.stat().st_size > MAX_MANIFEST_BYTES
                or checksum_file.stat().st_size > 200):
            raise SeasonRegistryError("Index manifest is too large.")
        raw = manifest_file.read_bytes()
        checksum = checksum_file.read_bytes()
        # Milestone 12 wrote this file in text mode. Windows therefore
        # published CRLF; POSIX published LF. Accept exactly either one,
        # without changing the manifest bytes used for the SHA-256 check.
        expected_lf = (
            f"{sha256(raw).hexdigest()}  manifest.json\n"
        ).encode("ascii")
        if checksum not in (expected_lf, expected_lf[:-1] + b"\r\n"):
            raise SeasonRegistryError("Index file checksum mismatch.")
        manifest = json.loads(raw, object_pairs_hook=_no_duplicates)
        if not isinstance(manifest, dict) or _canonical(manifest) != raw:
            raise SeasonRegistryError("Noncanonical index manifest.")
    except SeasonRegistryError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        raise SeasonRegistryError("Index files cannot be verified.") from exc

    index_sha = manifest.get("index_sha256")
    if (not _digest(index_sha)
            or root.name != "index_" + index_sha
            or sha256(_canonical({k: v for k, v in manifest.items()
                                  if k != "index_sha256"})).hexdigest() != index_sha):
        raise SeasonRegistryError("Index identity mismatch.")

    required = {
        "index_version": INDEX_VERSION,
        "schema_version": 1,
        "symbol": "EURUSD",
        "timeframe": "M5",
        "clock_basis": "uncertified_broker_wall_labels",
        "research_only": True,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "historical_utc_certified": False,
        "source_receipt_certified": False,
        "gaps_are_market_closures_certified": False,
        "all_windows_one_atomic_snapshot": False,
    }
    if any(type(manifest.get(k)) is not type(v) or manifest.get(k) != v
           for k, v in required.items()):
        raise SeasonRegistryError("Index version or safety policy mismatch.")

    rows = _number(manifest.get("row_count"), "row_count", 1)
    winters = _number(manifest.get("winter_count"), "winter_count")
    summers = _number(manifest.get("summer_count"), "summer_count")
    gaps = _number(manifest.get("gap_count"), "gap_count")
    missing = _number(manifest.get("unclassified_missing_m5_slots"), "missing slots")
    if (rows > MAX_ROWS or winters + summers != rows
            or gaps > missing or not _digest(manifest.get("combined_rows_jsonl_sha256"))):
        raise SeasonRegistryError("Inconsistent index totals.")

    parents = manifest.get("source_windows")
    if (not isinstance(parents, list) or not 1 <= len(parents) <= 64
            or _number(manifest.get("window_count"), "window_count", 1) != len(parents)):
        raise SeasonRegistryError("Invalid source window inventory.")

    previous_end = None
    parent_rows = 0
    parent_hashes = []
    for parent in parents:
        if not isinstance(parent, dict):
            raise SeasonRegistryError("Invalid source window record.")
        start = _date(parent.get("broker_label_window_start"))
        end = _date(parent.get("broker_label_window_end_exclusive"))
        if (not start < end or (previous_end is not None and start != previous_end)):
            raise SeasonRegistryError("Index source windows are not contiguous.")
        first = _timestamp(parent.get("first_broker_label"))
        last = _timestamp(parent.get("last_broker_label"))
        _timestamp(parent.get("first_candidate_utc"))
        _timestamp(parent.get("last_candidate_utc"))
        if not (start <= first.date() < end and start <= last.date() < end
                and first <= last):
            raise SeasonRegistryError("Source label outside declared window.")
        ident = parent.get("source_lineage_sha256")
        if not _digest(ident) or not _digest(parent.get("source_rows_sha256")):
            raise SeasonRegistryError("Invalid source digest.")
        parent_hashes.append(ident)
        parent_rows += _number(parent.get("source_row_count"), "source_row_count", 1)
        _number(parent.get("unclassified_boundary_missing_m5_slots"),
                "boundary missing slots")
        previous_end = end
    if parent_rows != rows or len(set(parent_hashes)) != len(parent_hashes):
        raise SeasonRegistryError("Duplicate lineage or mismatched parent rows.")
    return manifest


def build_registry(index_directories):
    """Construct a deterministic registry without joining disjoint periods."""
    directories = tuple(index_directories)
    if not 2 <= len(directories) <= MAX_INDEXES:
        raise SeasonRegistryError("Requires 2 to 16 independent indexes.")
    items = []
    used_indexes = set()
    used_parents = set()
    prior_end = None
    excluded_days = 0
    total_rows = total_gaps = total_missing = total_winter = total_summer = 0

    for directory in directories:
        source = read_index(directory)
        ident = source["index_sha256"]
        if ident in used_indexes:
            raise SeasonRegistryError("Duplicate index.")
        used_indexes.add(ident)
        for parent in source["source_windows"]:
            lineage = parent["source_lineage_sha256"]
            if lineage in used_parents:
                raise SeasonRegistryError("Source lineage reused across indexes.")
            used_parents.add(lineage)

        start = _date(source["source_windows"][0]["broker_label_window_start"])
        end = _date(source["source_windows"][-1]["broker_label_window_end_exclusive"])
        if prior_end is not None:
            if start <= prior_end:
                raise SeasonRegistryError("Indexes overlap or are out of order.")
            excluded_days += (start - prior_end).days
        prior_end = end
        total_rows += source["row_count"]
        total_gaps += source["gap_count"]
        total_missing += source["unclassified_missing_m5_slots"]
        total_winter += source["winter_count"]
        total_summer += source["summer_count"]
        if total_rows > MAX_ROWS:
            raise SeasonRegistryError("Registry row cap exceeded.")
        items.append({
            "index_sha256": ident,
            "start_broker_label_date": start.isoformat(),
            "end_broker_label_date_exclusive": end.isoformat(),
            "row_count": source["row_count"],
            "window_count": source["window_count"],
            "winter_count": source["winter_count"],
            "summer_count": source["summer_count"],
            "gap_count_within_index": source["gap_count"],
            "unclassified_missing_m5_slots_within_index":
                source["unclassified_missing_m5_slots"],
            "combined_rows_jsonl_sha256": source["combined_rows_jsonl_sha256"],
        })
    if total_winter == 0 or total_summer == 0:
        raise SeasonRegistryError("Both winter and summer regimes are required.")
    manifest = {
        "registry_version": VERSION,
        "schema_version": 1,
        "symbol": "EURUSD",
        "timeframe": "M5",
        "index_count": len(items),
        "indexes": items,
        "row_count": total_rows,
        "winter_count": total_winter,
        "summer_count": total_summer,
        "within_index_gap_count": total_gaps,
        "within_index_unclassified_missing_m5_slots": total_missing,
        "inter_index_calendar_days_excluded": excluded_days,
        "inter_index_periods_are_observations": False,
        "cross_index_candles_joined": False,
        "all_windows_one_atomic_snapshot": False,
        "research_only": True,
        "historical_admission": False,
        "historical_utc_certified": False,
        "training_authorized": False,
        "execution_authorized": False,
    }
    manifest["registry_sha256"] = sha256(_canonical(manifest)).hexdigest()
    return manifest


def publish_registry(manifest, project_root):
    """Publish immutable registry metadata. Never write any candle rows."""
    digest = manifest.get("registry_sha256")
    unsigned = {k: v for k, v in manifest.items() if k != "registry_sha256"}
    if (not _digest(digest) or sha256(_canonical(unsigned)).hexdigest() != digest
            or manifest.get("registry_version") != VERSION
            or manifest.get("research_only") is not True
            or manifest.get("training_authorized") is not False
            or manifest.get("execution_authorized") is not False):
        raise SeasonRegistryError("Registry proof or safety flags invalid.")
    root = Path(project_root).absolute()
    if root.is_symlink() or not (root / "backend" / "models").is_dir():
        raise SeasonRegistryError("Invalid project root.")
    for component in ("data", "research", "historical_utc_registries_v1"):
        root = root / component
        if root.is_symlink() or (root.exists() and not root.is_dir()):
            raise SeasonRegistryError("Unsafe registry directory.")
        root.mkdir(exist_ok=True)
    output = root / ("registry_" + digest)
    lock = root / ".registry_export.lock"
    if lock.is_symlink():
        raise SeasonRegistryError("Unsafe registry lock.")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except OSError as exc:
        raise SeasonRegistryError("Registry publication locked or unavailable.") from exc
    stage = None
    try:
        os.close(fd)
        if output.exists() or output.is_symlink():
            raise SeasonRegistryError("Registry already exists; refusing overwrite.")
        stage = Path(tempfile.mkdtemp(prefix=".stage_", dir=root))
        payload = _canonical(manifest)
        with (stage / "manifest.json").open("xb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        # Binary output avoids platform-dependent newline translation.
        with (stage / "checksums.sha256").open("xb") as stream:
            stream.write(
                f"{sha256(payload).hexdigest()}  manifest.json\n".encode("ascii")
            )
            stream.flush()
            os.fsync(stream.fileno())
        if output.exists() or output.is_symlink():
            raise SeasonRegistryError("Registry destination appeared during publication.")
        os.rename(stage, output)
        stage = None
        return output
    except OSError as exc:
        raise SeasonRegistryError("Registry publication failed.") from exc
    finally:
        if stage is not None:
            shutil.rmtree(stage)
        lock.unlink(missing_ok=True)


def run(argv=None, *, project_root=None):
    parser = argparse.ArgumentParser(description="Read-only multiseason research index registry")
    parser.add_argument("--index", action="append", required=True,
                        help="Milestone 12 index directory (chronological order; repeat)")
    parser.add_argument("--research-only", required=True, action="store_true")
    parser.add_argument("--publish-registry", action="store_true",
                        help="Publish immutable metadata; no candle files")
    args = parser.parse_args(argv)
    try:
        manifest = build_registry(args.index)
        print("EURUSD ORACLE | Multiseason research registry")
        print(f"INDEXES VERIFIED: {manifest['index_count']}")
        print(f"TOTAL RESEARCH ROWS: {manifest['row_count']}")
        print(f"WINTER: {manifest['winter_count']} | SUMMER: {manifest['summer_count']}")
        print(f"GAPS INSIDE INDEXES: {manifest['within_index_gap_count']}")
        print("UNCLASSIFIED M5 SLOTS INSIDE INDEXES: "
              f"{manifest['within_index_unclassified_missing_m5_slots']}")
        print(f"CALENDAR DAYS BETWEEN INDEXES EXCLUDED: "
              f"{manifest['inter_index_calendar_days_excluded']}")
        print(f"REGISTRY SHA-256: {manifest['registry_sha256']}")
        if args.publish_registry:
            root = project_root or Path(__file__).resolve().parents[2]
            print(f"REGISTRY PUBLISHED: {publish_registry(manifest, root)}")
        else:
            print("PREVIEW ONLY: no files written.")
        print("NO CROSS-INDEX JOIN | UTC UNCERTIFIED | TRAINING/EXECUTION BLOCKED")
        return 0
    except SeasonRegistryError as exc:
        print(f"SEASON REGISTRY BLOCKED: {exc}")
        return 1


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
