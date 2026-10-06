from datetime import datetime, timedelta, timezone

from backend.models.dataset_partitioning import (
    ChronologicalSplitPlan,
    iter_feature_batches,
)


def _plan():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 11, tzinfo=timezone.utc)
    return start, end, ChronologicalSplitPlan.from_range(start, end)


def test_split_boundaries_and_purge():
    start, end, plan = _plan()

    assert plan.train_end == datetime(2026, 1, 8, tzinfo=timezone.utc)
    assert plan.validation_end == datetime(
        2026, 1, 9, 12, tzinfo=timezone.utc
    )

    assert plan.assign(
        plan.train_end - timedelta(minutes=20),
        plan.train_end - timedelta(minutes=5),
    ) == "train"

    assert plan.assign(
        plan.train_end - timedelta(minutes=10),
        plan.train_end + timedelta(minutes=5),
    ) is None

    assert plan.assign(
        plan.train_end,
        plan.train_end + timedelta(minutes=15),
    ) == "validation"

    assert plan.assign(
        plan.validation_end - timedelta(minutes=10),
        plan.validation_end + timedelta(minutes=5),
    ) is None

    assert plan.assign(
        plan.validation_end,
        plan.validation_end + timedelta(minutes=15),
    ) == "test"

    assert plan.assign(
        end - timedelta(minutes=10),
        end + timedelta(minutes=5),
    ) is None

    assert plan.assign(
        start - timedelta(minutes=5),
        start + timedelta(minutes=10),
    ) is None


def test_bounded_feature_batch_geometry():
    start, end, _ = _plan()
    batches = list(iter_feature_batches(start, end, batch_days=3))

    assert len(batches) == 4
    assert batches[0].start == start
    assert batches[0].end == start + timedelta(days=3)
    assert batches[-1].end == end

    final_batch = batches[-1]
    assert final_batch.anchor_start(5) == final_batch.start - timedelta(minutes=5)
    assert final_batch.tick_load_start(65) == final_batch.start - timedelta(minutes=65)
    assert final_batch.candle_load_end(3, 5) == final_batch.end + timedelta(minutes=10)


def main():
    test_split_boundaries_and_purge()
    test_bounded_feature_batch_geometry()
    print("Chronological split boundary purge: PASS")
    print("Bounded feature batching: PASS")
    print("DATASET PARTITIONING TEST: PASS")


if __name__ == "__main__":
    main()
