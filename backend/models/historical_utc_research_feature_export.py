
"""Milestone 08: versioned, immutable export of research-only M5 features.

Source candles and v1 artifacts are never written. No broker, model training,
trade authorization, or database operations. This is a local integrity check,
not independent certification of historical UTC timestamps.
"""

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from backend.models.historical_utc_research_features import (
    FEATURE_VERSION,
    ResearchFeatureSet,
    build_clock_aware_research_features,
)


class ResearchFeatureExportError(RuntimeError):
    """Feature publication or verification failed closed."""


FILES = {"features.jsonl", "manifest.json", "checksums.sha256"}
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
M5 = timedelta(minutes=5)

FEATURE_FIELDS = {
    "source_row_id", "broker_label_time", "candidate_utc_open",
    "candidate_bar_end_utc", "candidate_offset_hours", "segment_id",
    "gap_before_m5", "close", "log_return_1", "range_pips",
    "body_pips", "sma_close_12", "volatility_log_return_12",
    "atr_14_pips", "tick_volume", "spread_points",
    "first_available_at", "research_only", "training_authorized",
    "execution_authorized",
}


def _canonical(value):
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def _parse_json(raw):
    def reject_duplicate_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def reject_nonfinite(_):
        raise ValueError("Nonfinite JSON")

    return json.loads(
        raw,
        object_pairs_hook=reject_duplicate_keys,
        parse_constant=reject_nonfinite,
    )


def _require_manifest(meta):
    if not isinstance(meta, dict):
        raise ResearchFeatureExportError(
            "Invalid feature manifest."
        )

    expected = {
        "feature_version": FEATURE_VERSION,
        "symbol": "EURUSD",
        "timeframe": "M5",
        "research_only": True,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "historical_utc_certified": False,
        "source_receipt_certified": False,
        "first_available_at": None,
        "features_at_candidate_bar_end_only": True,
        "rolling_state_reset_on_gap": True,
        "sma_window_candles": 12,
        "volatility_window_returns": 12,
        "atr_window_candles": 14,
    }

    for field, value in expected.items():
        if (
            type(meta.get(field)) is not type(value)
            or meta[field] != value
        ):
            raise ResearchFeatureExportError(
                f"Unsafe feature manifest: {field}."
            )

    if (
        type(meta.get("feature_count")) is not int
        or not 1 <= meta["feature_count"] <= 500_000
    ):
        raise ResearchFeatureExportError(
            "Invalid feature count."
        )

    for field in (
        "feature_rows_sha256",
        "source_lineage_sha256",
        "source_rows_sha256",
    ):
        if (
            not isinstance(meta.get(field), str)
            or not HEX64.fullmatch(meta[field])
        ):
            raise ResearchFeatureExportError(
                f"Invalid digest: {field}."
            )

    for field in (
        "segment_count",
        "gap_count",
        "unobserved_intervals",
    ):
        if (
            type(meta.get(field)) is not int
            or meta[field] < 0
        ):
            raise ResearchFeatureExportError(
                f"Invalid statistic: {field}."
            )

    if meta["segment_count"] != meta["gap_count"] + 1:
        raise ResearchFeatureExportError(
            "Inconsistent segment count."
        )


def _safe_folder(parent, name):
    folder = parent / name

    if (
        folder.is_symlink()
        or (folder.exists() and not folder.is_dir())
    ):
        raise ResearchFeatureExportError(
            f"Unsafe export folder: {folder}"
        )

    folder.mkdir(exist_ok=True)
    return folder


def _output_root(project_root):
    root = Path(project_root).absolute()

    if (
        root.is_symlink()
        or not root.is_dir()
        or not (root / "backend" / "models").is_dir()
    ):
        raise ResearchFeatureExportError(
            "Invalid ORACLE project root."
        )

    for name in (
        "data",
        "research",
        "historical_utc_features_v1",
    ):
        root = _safe_folder(root, name)

    return root


def verify_feature_export(directory, source_directory=None):
    """Verify a feature export without modifying it.

    With source_directory, independently rebuild features from
    audited candles and compare the resulting manifest.

    Local checksums alone do not certify historical timestamps.
    """

    folder = Path(directory)

    if folder.is_symlink() or not folder.is_dir():
        raise ResearchFeatureExportError(
            "Feature export directory is unsafe."
        )

    if {p.name for p in folder.iterdir()} != FILES:
        raise ResearchFeatureExportError(
            "Missing or unexpected export files."
        )

    if any(
        (folder / name).is_symlink()
        or not (folder / name).is_file()
        for name in FILES
    ):
        raise ResearchFeatureExportError(
            "Export contains a link or non-file."
        )

    try:
        manifest_bytes = (
            folder / "manifest.json"
        ).read_bytes()

        meta = _parse_json(manifest_bytes)

        if manifest_bytes != _canonical(meta):
            raise ResearchFeatureExportError(
                "Manifest is not canonical JSON."
            )

        _require_manifest(meta)

        if (
            meta.get("export_format")
            != "jsonl_manifest_v1"
            or meta.get("data_file")
            != "features.jsonl"
        ):
            raise ResearchFeatureExportError(
                "Unexpected feature export format."
            )

        checks = (
            (folder / "checksums.sha256")
            .read_text(encoding="ascii")
            .splitlines()
        )

        expected_checks = [
            f"{meta['feature_rows_sha256']}  features.jsonl",
            f"{sha256(manifest_bytes).hexdigest()}  manifest.json",
        ]

        if checks != expected_checks:
            raise ResearchFeatureExportError(
                "Checksum inventory mismatch."
            )

        digest = sha256()
        count = 0
        seen_ids = set()
        last_time = None
        gap_count = 0
        missing = 0
        segment = 0

        with (folder / "features.jsonl").open("rb") as stream:
            for line in stream:

                if (
                    len(line) > 8192
                    or not line.endswith(b"\n")
                ):
                    raise ResearchFeatureExportError(
                        "Invalid feature JSONL line."
                    )

                digest.update(line)
                row = _parse_json(line)

                if (
                    not isinstance(row, dict)
                    or set(row) != FEATURE_FIELDS
                    or _canonical(row) != line
                ):
                    raise ResearchFeatureExportError(
                        "Noncanonical feature row or schema."
                    )

                if (
                    row.get("research_only") is not True
                    or row.get("training_authorized") is not False
                    or row.get("execution_authorized") is not False
                    or row.get("first_available_at") is not None
                ):
                    raise ResearchFeatureExportError(
                        "Unsafe feature row status."
                    )

                ident = row.get("source_row_id")

                if (
                    type(ident) is not int
                    or ident in seen_ids
                ):
                    raise ResearchFeatureExportError(
                        "Duplicate or invalid source row ID."
                    )

                seen_ids.add(ident)

                candidate = datetime.fromisoformat(
                    row["candidate_utc_open"]
                )

                end = datetime.fromisoformat(
                    row["candidate_bar_end_utc"]
                )

                if (
                    candidate.utcoffset() != timedelta(0)
                    or end.utcoffset() != timedelta(0)
                    or end - candidate != M5
                ):
                    raise ResearchFeatureExportError(
                        "Invalid candidate candle end."
                    )

                gap = 0

                if last_time is not None:
                    dt = candidate - last_time

                    if dt < M5 or dt % M5:
                        raise ResearchFeatureExportError(
                            "Unordered feature timestamps."
                        )

                    if dt > M5:
                        gap = dt // M5 - 1
                        gap_count += 1
                        missing += gap
                        segment += 1
                else:
                    segment = 1

                if (
                    type(row.get("gap_before_m5")) is not int
                    or row["gap_before_m5"] != gap
                    or type(row.get("segment_id")) is not int
                    or row["segment_id"] != segment
                ):
                    raise ResearchFeatureExportError(
                        "Feature gap/segment mismatch."
                    )

                last_time = candidate
                count += 1

                if count > meta["feature_count"]:
                    raise ResearchFeatureExportError(
                        "Too many feature rows."
                    )

        if (
            count != meta["feature_count"]
            or digest.hexdigest()
            != meta["feature_rows_sha256"]
            or gap_count != meta["gap_count"]
            or missing != meta["unobserved_intervals"]
            or segment != meta["segment_count"]
        ):
            raise ResearchFeatureExportError(
                "Feature content or statistics mismatch."
            )

        if source_directory is not None:
            rebuilt = build_clock_aware_research_features(
                source_directory,
                max_rows=meta["feature_count"],
            )

            if any(
                rebuilt.manifest().get(k) != meta.get(k)
                for k in rebuilt.manifest()
            ):
                raise ResearchFeatureExportError(
                    "Source-backed feature verification failed."
                )

        return meta

    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
    ) as exc:
        raise ResearchFeatureExportError(
            "Feature verification failed."
        ) from exc


def export_research_features(
    feature_set,
    source_directory,
    project_root,
):
    """Recompute from audited source and publish exactly once."""

    if not isinstance(feature_set, ResearchFeatureSet):
        raise ResearchFeatureExportError(
            "Expected a ResearchFeatureSet."
        )

    meta = dict(feature_set.manifest())
    _require_manifest(meta)

    rebuilt = build_clock_aware_research_features(
        source_directory,
        max_rows=meta["feature_count"],
    )

    if rebuilt.manifest() != meta:
        raise ResearchFeatureExportError(
            "Features do not match the audited source."
        )

    meta["export_format"] = "jsonl_manifest_v1"
    meta["data_file"] = "features.jsonl"

    output_root = _output_root(project_root)

    final = output_root / (
        "features_"
        + sha256(_canonical(meta)).hexdigest()
    )

    lock = output_root / ".feature_export.lock"

    try:
        fd = os.open(
            lock,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )

    except FileExistsError as exc:
        raise ResearchFeatureExportError(
            "Feature export is locked; "
            "do not remove the lock blindly."
        ) from exc

    except OSError as exc:
        raise ResearchFeatureExportError(
            "Cannot acquire export lock."
        ) from exc

    stage = None

    try:
        with os.fdopen(
            fd,
            "w",
            encoding="ascii",
        ) as handle:
            handle.write(f"pid={os.getpid()}\n")

        if final.exists() or final.is_symlink():
            raise ResearchFeatureExportError(
                "Feature version already exists; "
                "refusing overwrite."
            )

        stage = Path(
            tempfile.mkdtemp(
                prefix=".stage_",
                dir=output_root,
            )
        )

        digest = sha256()

        with (stage / "features.jsonl").open("xb") as handle:
            for row in feature_set.rows:
                content = _canonical(row.record())
                digest.update(content)
                handle.write(content)

            handle.flush()
            os.fsync(handle.fileno())

        if digest.hexdigest() != meta["feature_rows_sha256"]:
            raise ResearchFeatureExportError(
                "Feature payload changed before publication."
            )

        manifest_bytes = _canonical(meta)

        with (stage / "manifest.json").open("xb") as handle:
            handle.write(manifest_bytes)
            handle.flush()
            os.fsync(handle.fileno())

        inventory = (
            f"{digest.hexdigest()}  features.jsonl\n"
            f"{sha256(manifest_bytes).hexdigest()}  manifest.json\n"
        )

        with (stage / "checksums.sha256").open(
            "x",
            encoding="ascii",
        ) as handle:
            handle.write(inventory)
            handle.flush()
            os.fsync(handle.fileno())

        verify_feature_export(stage, source_directory)

        if final.exists() or final.is_symlink():
            raise ResearchFeatureExportError(
                "Destination appeared; refusing overwrite."
            )

        os.rename(stage, final)
        stage = None

        return final

    finally:
        if stage is not None:
            shutil.rmtree(stage)

        lock.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Immutable EURUSD UTC-candidate feature export"
    )

    parser.add_argument(
        "--source-directory",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )

    parser.add_argument(
        "--research-only",
        action="store_true",
        required=True,
    )

    parser.add_argument(
        "--export",
        action="store_true",
        required=True,
    )

    args = parser.parse_args(argv)
    published = None

    try:
        features = build_clock_aware_research_features(
            args.source_directory
        )

        target = export_research_features(
            features,
            args.source_directory,
            args.project_root,
        )

        published = target

        meta = verify_feature_export(
            target,
            args.source_directory,
        )

    except Exception as exc:
        if published is not None:
            print(
                f"EXPORT NEEDS REVIEW: {published} "
                f"({type(exc).__name__}). "
                "No training or trading authorized."
            )
        else:
            print(
                f"FEATURE EXPORT BLOCKED: {type(exc).__name__}. "
                "No completed export reported by this run."
            )

        return 1

    print(f"FEATURE EXPORT VERIFIED: {target}")

    print(
        f"Rows: {meta['feature_count']} | "
        f"Features SHA-256: {meta['feature_rows_sha256']}"
    )

    print(
        "RESEARCH ONLY | HISTORICAL UTC UNCERTIFIED | "
        "TRAINING BLOCKED | EXECUTION BLOCKED"
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
