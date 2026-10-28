"""Offline regression tests for research-only historical clock reconstruction."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest

from backend.models.historical_utc_reconstruction import (
    HistoricalReconstructionError,
    LegacyM5Bar,
    reconstruct_historical_m5_bars,
)

UTC = timezone.utc


def bar(row_id=1, when="2026-01-15T14:00:00+00:00", **changes):
    fields = dict(
        row_id=row_id,
        broker_label_time=datetime.fromisoformat(when),
        open=1.10,
        high=1.12,
        low=1.09,
        close=1.11,
        tick_volume=3,
        spread_points=10,
        real_volume=0.0,
    )
    fields.update(changes)
    return LegacyM5Bar(**fields)


def test_candidate_winter_summer_and_research_only_admission():
    batch = reconstruct_historical_m5_bars([
        bar(1, "2026-01-15T14:00:00+00:00"),
        bar(2, "2026-07-15T14:05:00+00:00"),
    ])
    assert [x.candidate_utc_open.isoformat() for x in batch.records] == [
        "2026-01-15T12:00:00+00:00", "2026-07-15T11:05:00+00:00"
    ]
    assert batch.winter_count == 1 and batch.summer_count == 1
    assert batch.research_only and batch.historical_admission is False
    for candidate in batch.records:
        assert candidate.first_available_at is None
        assert candidate.historical_admission is False
        assert candidate.execution_authorized is False
    assert batch.manifest()["source_receipt_certified"] is False


def test_deterministic_manifest_and_original_not_modified():
    original = bar()
    a = reconstruct_historical_m5_bars([original])
    b = reconstruct_historical_m5_bars([original])
    assert a.manifest() == b.manifest()
    assert original.broker_label_time.isoformat() == "2026-01-15T14:00:00+00:00"
    assert a.records[0].original is original
    assert a.input_sha256 != a.output_sha256
    assert all(len(x) == 64 for x in (a.input_sha256, a.output_sha256, a.evidence_sha256))


@pytest.mark.parametrize("when", [
    "2026-03-29T02:00:00+00:00", "2026-10-25T05:55:00+00:00",
])
def test_transition_window_fails_closed(when):
    with pytest.raises(HistoricalReconstructionError, match="blocked"):
        reconstruct_historical_m5_bars([bar(when=when)])


@pytest.mark.parametrize("time", [
    datetime(2026, 1, 15, 14),
    datetime(2026, 1, 15, 14, tzinfo=timezone(timedelta(hours=3))),
    datetime(2026, 1, 15, 14, 2, tzinfo=UTC),
])
def test_naive_non_utc_and_unaligned_label_refused(time):
    with pytest.raises(HistoricalReconstructionError):
        bar(broker_label_time=time)


@pytest.mark.parametrize("changes", [
    {"open": float("nan")}, {"high": 1.08}, {"low": 1.13},
    {"tick_volume": -1}, {"spread_points": -1},
    {"real_volume": float("inf")}, {"open": 10**500}, {"row_id": True},
    {"is_complete": False}, {"symbol": "GBPUSD"}, {"source": "UNKNOWN"},
])
def test_invalid_or_untrusted_legacy_source_refused(changes):
    with pytest.raises(HistoricalReconstructionError):
        bar(**changes)


def test_duplicate_ids_and_clock_labels_are_refused():
    original = bar()
    with pytest.raises(HistoricalReconstructionError, match="Duplicate source row ID"):
        reconstruct_historical_m5_bars([original, replace(original, broker_label_time=original.broker_label_time + timedelta(minutes=5))])
    with pytest.raises(HistoricalReconstructionError, match="ordered and unique"):
        reconstruct_historical_m5_bars([original, replace(original, row_id=2)])


def test_out_of_order_and_bad_input_refused():
    with pytest.raises(HistoricalReconstructionError, match="ordered and unique"):
        reconstruct_historical_m5_bars([bar(1, "2026-01-15T14:05:00+00:00"), bar(2)])
    with pytest.raises(HistoricalReconstructionError, match="Expected a LegacyM5Bar"):
        reconstruct_historical_m5_bars([{"row_id": 1}])
    with pytest.raises(HistoricalReconstructionError, match="empty"):
        reconstruct_historical_m5_bars([])


def test_gaps_are_not_silently_filled_or_converted_into_fake_candles():
    batch = reconstruct_historical_m5_bars([
        bar(1, "2026-01-15T14:00:00+00:00"),
        bar(2, "2026-01-15T14:15:00+00:00"),
    ])
    assert len(batch.records) == 2
    assert batch.records[1].candidate_utc_open - batch.records[0].candidate_utc_open == timedelta(minutes=15)


def test_last_sunday_season_boundaries_outside_blocked_window():
    batch = reconstruct_historical_m5_bars([
        bar(1, "2026-03-28T23:55:00+00:00"),
        bar(2, "2026-03-29T06:00:00+00:00"),
        bar(3, "2026-10-24T23:55:00+00:00"),
        bar(4, "2026-10-25T06:00:00+00:00"),
    ])
    assert [x.candidate_offset_hours for x in batch.records] == [2, 3, 3, 2]
    assert batch.winter_count == 2 and batch.summer_count == 2
