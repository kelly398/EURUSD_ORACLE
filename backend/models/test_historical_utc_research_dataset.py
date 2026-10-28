
"""Milestone 03: deterministic offline dataset safety tests."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256

import pytest

from backend.models.historical_utc_reconstruction import (
    LegacyM5Bar,
    reconstruct_historical_m5_bars,
)

from backend.models.historical_utc_research_dataset import (
    ResearchDatasetBuildError,
    build_historical_utc_research_dataset,
)


UTC = timezone.utc
BASE = datetime(2026, 1, 15, 14, tzinfo=UTC)


def bar(row_id, minute):
    return LegacyM5Bar(
        row_id=row_id,
        broker_label_time=BASE + timedelta(minutes=minute),
        open=1.10,
        high=1.12,
        low=1.09,
        close=1.11,
        tick_volume=20,
        spread_points=8,
        real_volume=0.0,
    )


def batch(*rows):
    return reconstruct_historical_m5_bars(rows)


def test_multiple_batches_and_provenance():
    data = build_historical_utc_research_dataset([
        batch(bar(1, 0), bar(2, 5)),
        batch(bar(3, 10)),
    ])

    meta = data.manifest()

    assert meta["record_count"] == 3
    assert meta["batch_count"] == 2
    assert meta["dataset_version"].endswith("_V2")
    assert meta["first_broker_label"] == BASE.isoformat()

    assert meta["first_candidate_utc"] == (
        BASE - timedelta(hours=2)
    ).isoformat()

    assert meta["first_available_at"] is None
    assert meta["historical_admission"] is False
    assert meta["execution_authorized"] is False
    assert meta["training_authorized"] is False

    assert data.winter_count == 3
    assert data.summer_count == 0

    payload = "".join(
        line + "\n" for line in data.iter_jsonl()
    ).encode("utf-8")

    assert sha256(payload).hexdigest() == (
        meta["rows_jsonl_sha256"]
    )


def test_reproducible_contents_across_batching():
    records = [
        bar(1, 0),
        bar(2, 5),
        bar(3, 10),
    ]

    a = build_historical_utc_research_dataset([
        batch(*records)
    ])

    b = build_historical_utc_research_dataset([
        batch(*records[:2]),
        batch(records[2]),
    ])

    assert a.rows_sha256 == b.rows_sha256
    assert a.lineage_sha256 != b.lineage_sha256

    repeated = build_historical_utc_research_dataset([
        batch(*records)
    ])

    assert a.manifest() == repeated.manifest()


def test_unobserved_intervals_not_filled():
    data = build_historical_utc_research_dataset([
        batch(bar(1, 0), bar(2, 15))
    ])

    assert len(data.records) == 2
    assert data.gap_count == 1
    assert data.unobserved_intervals == 2

    assert data.manifest()[
        "unobserved_intervals_include_market_closures"
    ]


def test_duplicate_source_ids_across_batches_rejected():
    with pytest.raises(
        ResearchDatasetBuildError,
        match="Duplicate",
    ):
        build_historical_utc_research_dataset([
            batch(bar(1, 0)),
            batch(bar(1, 5)),
        ])


def test_duplicate_broker_labels_across_batches_rejected():
    with pytest.raises(
        ResearchDatasetBuildError,
        match="Broker labels",
    ):
        build_historical_utc_research_dataset([
            batch(bar(1, 0)),
            batch(bar(2, 0)),
        ])


def test_backwards_candidate_across_batches_rejected():
    with pytest.raises(ResearchDatasetBuildError):
        build_historical_utc_research_dataset([
            batch(bar(1, 5)),
            batch(bar(2, 0)),
        ])


def test_forged_utc_candidate_rejected():
    valid = batch(bar(1, 0))

    forged = replace(
        valid.records[0],
        candidate_utc_open=BASE,
    )

    with pytest.raises(
        ResearchDatasetBuildError,
        match="mismatch",
    ):
        build_historical_utc_research_dataset([
            replace(valid, records=(forged,))
        ])


def test_forged_evidence_rejected():
    original = batch(bar(1, 0))

    with pytest.raises(
        ResearchDatasetBuildError,
        match="mismatch",
    ):
        build_historical_utc_research_dataset([
            replace(
                original,
                evidence_sha256="0" * 64,
            )
        ])


def test_bounds_and_empty_fail_closed():
    for count in (0, True, -1, 500001, 1.5):
        with pytest.raises(ResearchDatasetBuildError):
            build_historical_utc_research_dataset(
                [],
                max_rows=count,
            )

    with pytest.raises(
        ResearchDatasetBuildError,
        match="Empty",
    ):
        build_historical_utc_research_dataset([])

    with pytest.raises(
        ResearchDatasetBuildError,
        match="limit",
    ):
        build_historical_utc_research_dataset(
            [batch(bar(1, 0), bar(2, 5))],
            max_rows=1,
        )


def test_failed_generator_never_returns_dataset():
    def stream():
        yield batch(bar(1, 0))
        raise RuntimeError("archive interrupted")

    with pytest.raises(
        RuntimeError,
        match="archive interrupted",
    ):
        build_historical_utc_research_dataset(
            stream()
        )


def test_broker_clock_source_preserved():
    original = bar(1, 0)

    data = build_historical_utc_research_dataset([
        batch(original)
    ])

    assert (
        data.records[0].original.broker_label_time
        == original.broker_label_time
    )

    assert data.records[0].first_available_at is None
