
"""Offline tests for Milestone 04. No database or broker required."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from backend.models.historical_utc_reconstruction import (
    LegacyM5Bar,
    reconstruct_historical_m5_bars,
)
from backend.models.historical_utc_research_dataset import (
    ResearchDatasetV2,
    build_historical_utc_research_dataset,
)
from backend.models.historical_utc_research_export import (
    ResearchExportError,
    export_research_dataset,
    verify_research_export,
)


def _project(tmp_path):
    root = tmp_path / "EURUSD_ORACLE"
    (root / "backend" / "models").mkdir(parents=True)

    # Simulate a frozen v1 artifact.
    legacy = (
        root / "backend" / "models" / "dataset_manifest.json"
    )

    legacy.write_bytes(
        b'{"frozen_dataset": "v1"}\n'
    )

    return root, legacy


def _dataset():
    start = datetime(
        2026, 1, 15, 14, 0,
        tzinfo=timezone.utc,
    )

    bars = [
        LegacyM5Bar(
            row_id=i + 1,
            broker_label_time=(
                start + timedelta(minutes=5 * i)
            ),
            open=1.10,
            high=1.12,
            low=1.09,
            close=1.11,
            tick_volume=20,
            spread_points=8,
            real_volume=0.0,
        )
        for i in range(3)
    ]

    batches = [
        reconstruct_historical_m5_bars(bars[:2]),
        reconstruct_historical_m5_bars(bars[2:]),
    ]

    return build_historical_utc_research_dataset(
        batches
    )


def test_export_and_verify_without_changing_v1(tmp_path):
    root, legacy = _project(tmp_path)
    before = legacy.read_bytes()
    dataset = _dataset()

    exported = export_research_dataset(
        dataset, root
    )

    metadata = verify_research_export(exported)

    assert exported.parent == (
        root / "data" / "research" / "historical_utc_v2"
    )

    assert {f.name for f in exported.iterdir()} == {
        "candles.jsonl",
        "manifest.json",
        "checksums.sha256",
    }

    assert metadata["record_count"] == 3
    assert metadata["research_only"] is True
    assert metadata["training_authorized"] is False
    assert metadata["execution_authorized"] is False
    assert metadata["source_receipt_certified"] is False
    assert legacy.read_bytes() == before


def test_repeat_export_never_overwrites(tmp_path):
    root, _ = _project(tmp_path)
    dataset = _dataset()

    exported = export_research_dataset(
        dataset, root
    )

    original = (
        exported / "candles.jsonl"
    ).read_bytes()

    with pytest.raises(
        ResearchExportError,
        match="exists",
    ):
        export_research_dataset(dataset, root)

    assert (
        exported / "candles.jsonl"
    ).read_bytes() == original


def test_payload_corruption_is_detected(tmp_path):
    root, _ = _project(tmp_path)

    exported = export_research_dataset(
        _dataset(), root
    )

    with (exported / "candles.jsonl").open("ab") as stream:
        stream.write(b"corrupted\n")

    with pytest.raises(ResearchExportError):
        verify_research_export(exported)


def test_manifest_corruption_is_detected(tmp_path):
    root, _ = _project(tmp_path)

    exported = export_research_dataset(
        _dataset(), root
    )

    with (exported / "manifest.json").open("ab") as stream:
        stream.write(b" ")

    with pytest.raises(ResearchExportError):
        verify_research_export(exported)


def test_forged_dataset_digest_fails_closed(tmp_path):
    root, _ = _project(tmp_path)

    forged = replace(
        _dataset(),
        rows_sha256="0" * 64,
    )

    with pytest.raises(
        ResearchExportError,
        match="changed",
    ):
        export_research_dataset(forged, root)

    output = (
        root / "data" / "research" / "historical_utc_v2"
    )

    assert not list(output.glob("dataset_*"))
    assert not list(output.glob(".staging_*"))
    assert not (output / ".export.lock").exists()


def test_interrupted_export_never_publishes(
    tmp_path, monkeypatch
):
    root, _ = _project(tmp_path)

    original = ResearchDatasetV2.iter_jsonl

    def interrupted(self):
        yield next(original(self))
        raise RuntimeError("source interrupted")

    monkeypatch.setattr(
        ResearchDatasetV2,
        "iter_jsonl",
        interrupted,
    )

    with pytest.raises(
        RuntimeError,
        match="source interrupted",
    ):
        export_research_dataset(
            _dataset(), root
        )

    output = (
        root / "data" / "research" / "historical_utc_v2"
    )

    assert not list(output.glob("dataset_*"))
    assert not list(output.glob(".staging_*"))
    assert not (output / ".export.lock").exists()


def test_lock_blocks_overlapping_writers(tmp_path):
    root, _ = _project(tmp_path)
    dataset = _dataset()

    output = (
        root / "data" / "research" / "historical_utc_v2"
    )

    output.mkdir(parents=True)

    lock = output / ".export.lock"
    lock.write_text(
        "another writer",
        encoding="ascii",
    )

    with pytest.raises(
        ResearchExportError,
        match="progress",
    ):
        export_research_dataset(dataset, root)

    assert (
        lock.read_text(encoding="ascii")
        == "another writer"
    )


def test_wrong_root_rejected(tmp_path):
    with pytest.raises(
        ResearchExportError,
        match="root",
    ):
        export_research_dataset(
            _dataset(),
            tmp_path / "not_a_project",
        )
