
"""Milestone 04: local export of research-only EURUSD UTC candidates.

No database access, model training, broker execution, or modification of v1.
Outputs are isolated under <project>/data/research/historical_utc_v2/.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from backend.models.historical_utc_research_dataset import ResearchDatasetV2


class ResearchExportError(RuntimeError):
    """A research export could not be safely created or verified."""


_DATA_FILE = "candles.jsonl"
_MANIFEST_FILE = "manifest.json"
_CHECKSUM_FILE = "checksums.sha256"
_EXPECTED_FILES = {_DATA_FILE, _MANIFEST_FILE, _CHECKSUM_FILE}
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def _canonical(data: dict) -> bytes:
    return (
        json.dumps(
            data,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def _check_manifest(meta: dict) -> None:
    if not isinstance(meta, dict):
        raise ResearchExportError(
            "Dataset manifest must be a dictionary."
        )

    required = {
        "dataset_version": "EURUSD_M5_HISTORICAL_UTC_RESEARCH_V2",
        "symbol": "EURUSD",
        "timeframe": "M5",
        "research_only": True,
        "historical_admission": False,
        "execution_authorized": False,
        "training_authorized": False,
        "source_receipt_certified": False,
        "first_available_at": None,
    }

    for key, expected in required.items():
        if (
            type(meta.get(key)) is not type(expected)
            or meta[key] != expected
        ):
            raise ResearchExportError(
                f"Unsafe or missing manifest field: {key}."
            )

    if (
        type(meta.get("record_count")) is not int
        or not 1 <= meta["record_count"] <= 500_000
    ):
        raise ResearchExportError("Invalid record_count.")

    for key in ("rows_jsonl_sha256", "lineage_sha256"):
        if (
            not isinstance(meta.get(key), str)
            or not _HEX64.fullmatch(meta[key])
        ):
            raise ResearchExportError(f"Invalid {key}.")


def _safe_child(parent: Path, name: str) -> Path:
    result = parent / name

    if result.is_symlink():
        raise ResearchExportError(
            f"Refusing symbolic link: {result}"
        )

    if result.exists() and not result.is_dir():
        raise ResearchExportError(
            f"Expected a directory: {result}"
        )

    if not result.exists():
        try:
            result.mkdir()
        except OSError as exc:
            raise ResearchExportError(
                f"Could not create directory: {result}"
            ) from exc

    return result


def _output_root(project_root: Path) -> Path:
    root = Path(project_root).absolute()

    if root.is_symlink() or not root.is_dir():
        raise ResearchExportError(
            "project_root must be a real, existing directory."
        )

    if not (root / "backend" / "models").is_dir():
        raise ResearchExportError(
            "Not an EURUSD_ORACLE project root."
        )

    for name in (
        "data",
        "research",
        "historical_utc_v2",
    ):
        root = _safe_child(root, name)

    return root


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)

    return digest.hexdigest()


def verify_research_export(directory: Path) -> dict:
    """Verify exported payload checksums and research-only flags."""

    folder = Path(directory)

    if folder.is_symlink() or not folder.is_dir():
        raise ResearchExportError(
            "Export is not a regular directory."
        )

    if {p.name for p in folder.iterdir()} != _EXPECTED_FILES:
        raise ResearchExportError(
            "Missing or unexpected export files."
        )

    for filename in _EXPECTED_FILES:
        path = folder / filename

        if path.is_symlink() or not path.is_file():
            raise ResearchExportError(
                "Export contains a link or non-file."
            )

    try:
        meta = json.loads(
            (folder / _MANIFEST_FILE).read_text(
                encoding="utf-8"
            )
        )

        _check_manifest(meta)

        if meta.get("export_format") != "jsonl_manifest_v1":
            raise ResearchExportError(
                "Unsupported export format."
            )

        if meta.get("data_file") != _DATA_FILE:
            raise ResearchExportError(
                "Unexpected data file name."
            )

        checks = (
            (folder / _CHECKSUM_FILE)
            .read_text(encoding="ascii")
            .splitlines()
        )

        expected = [
            f"{meta['rows_jsonl_sha256']}  {_DATA_FILE}",
            f"{_digest_file(folder / _MANIFEST_FILE)}  {_MANIFEST_FILE}",
        ]

        if checks != expected:
            raise ResearchExportError(
                "Checksums inventory mismatch."
            )

        if (
            _digest_file(folder / _DATA_FILE)
            != meta["rows_jsonl_sha256"]
        ):
            raise ResearchExportError(
                "Candle payload fingerprint mismatch."
            )

        count = 0

        with (folder / _DATA_FILE).open(
            "r", encoding="utf-8"
        ) as stream:
            for line in stream:
                count += 1

                if (
                    count > meta["record_count"]
                    or not isinstance(json.loads(line), dict)
                ):
                    raise ResearchExportError(
                        "Invalid dataset row."
                    )

        if count != meta["record_count"]:
            raise ResearchExportError(
                "Dataset row count mismatch."
            )

        return meta

    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        raise ResearchExportError(
            "Export verification failed."
        ) from exc


def export_research_dataset(
    dataset: ResearchDatasetV2,
    project_root: Path,
) -> Path:
    """Publish a research dataset without replacing existing exports.

    A completed export contains candles.jsonl, manifest.json,
    and checksums.sha256.

    The writer uses a private staging directory and exclusive
    local lock. No v1 dataset file is opened for writing.
    """

    if not isinstance(dataset, ResearchDatasetV2):
        raise ResearchExportError(
            "Expected a verified ResearchDatasetV2."
        )

    if (
        dataset.research_only is not True
        or dataset.historical_admission is not False
        or dataset.execution_authorized is not False
    ):
        raise ResearchExportError(
            "Unapproved dataset status."
        )

    meta = dict(dataset.manifest())
    _check_manifest(meta)

    meta["export_format"] = "jsonl_manifest_v1"
    meta["data_file"] = _DATA_FILE

    output_root = _output_root(project_root)

    final = output_root / (
        "dataset_" + meta["lineage_sha256"]
    )

    lock = output_root / ".export.lock"

    if lock.is_symlink():
        raise ResearchExportError(
            "Unsafe export lock path."
        )

    try:
        fd = os.open(
            lock,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )

    except FileExistsError as exc:
        raise ResearchExportError(
            "An export is already in progress; "
            "do not remove its lock blindly."
        ) from exc

    except OSError as exc:
        raise ResearchExportError(
            "Cannot acquire the export lock."
        ) from exc

    stage = None

    try:
        with os.fdopen(
            fd, "w", encoding="ascii"
        ) as stream:
            stream.write(f"pid={os.getpid()}\n")

        if final.exists() or final.is_symlink():
            raise ResearchExportError(
                "Dataset version already exists; "
                "refusing overwrite."
            )

        stage = Path(
            tempfile.mkdtemp(
                prefix=".staging_",
                dir=output_root,
            )
        )

        digest = hashlib.sha256()
        count = 0

        with (stage / _DATA_FILE).open("xb") as output:
            for row in dataset.iter_jsonl():

                if (
                    not isinstance(row, str)
                    or "\n" in row
                    or "\r" in row
                ):
                    raise ResearchExportError(
                        "Invalid JSONL row encoding."
                    )

                payload = (row + "\n").encode("utf-8")

                digest.update(payload)
                output.write(payload)
                count += 1

                if count > meta["record_count"]:
                    raise ResearchExportError(
                        "Too many dataset rows."
                    )

            output.flush()
            os.fsync(output.fileno())

        if (
            count != meta["record_count"]
            or digest.hexdigest()
            != meta["rows_jsonl_sha256"]
        ):
            raise ResearchExportError(
                "Dataset changed after reconstruction."
            )

        manifest_bytes = _canonical(meta)

        with (stage / _MANIFEST_FILE).open(
            "xb"
        ) as output:
            output.write(manifest_bytes)
            output.flush()
            os.fsync(output.fileno())

        inventory = (
            f"{digest.hexdigest()}  {_DATA_FILE}\n"
            f"{hashlib.sha256(manifest_bytes).hexdigest()}"
            f"  {_MANIFEST_FILE}\n"
        )

        with (stage / _CHECKSUM_FILE).open(
            "x", encoding="ascii"
        ) as output:
            output.write(inventory)
            output.flush()
            os.fsync(output.fileno())

        verify_research_export(stage)

        if final.exists() or final.is_symlink():
            raise ResearchExportError(
                "Dataset version appeared during export; "
                "refusing overwrite."
            )

        os.rename(stage, final)
        stage = None

        return final

    finally:
        if stage is not None:
            shutil.rmtree(stage)

        lock.unlink(missing_ok=True)
