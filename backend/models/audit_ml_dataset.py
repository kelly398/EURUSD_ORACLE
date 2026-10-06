import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from math import isfinite

from sqlalchemy import select

from backend.database.connection import SessionLocal
from backend.models.dataset_models import MLTrainingSample
from backend.models.dataset_partitioning import ChronologicalSplitPlan
from backend.models.feature_builder import MLFeatureBuilder
from backend.models.populate_ml_dataset import (
    FEATURE_VERSION,
    LABEL_VERSION,
    SYMBOL,
    TIMEFRAME,
    TIMEFRAME_MINUTES,
    HORIZON_BARS,
)


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Audit materialized EUR/USD ORACLE ML training samples."
    )
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    return parser.parse_args()


def main():
    arguments = parse_arguments()
    split_plan = ChronologicalSplitPlan.from_range(
        arguments.start,
        arguments.end,
    )
    expected_horizon = timedelta(
        minutes=TIMEFRAME_MINUTES * HORIZON_BARS
    )
    expected_feature_names = set(MLFeatureBuilder.FEATURE_NAMES)

    split_counts = Counter()
    label_counts = Counter()
    split_label_counts = Counter()
    integrity_errors = []
    total = 0
    first_feature = None
    last_feature = None
    previous_feature = None

    statement = (
        select(
            MLTrainingSample.id,
            MLTrainingSample.feature_timestamp,
            MLTrainingSample.target_timestamp,
            MLTrainingSample.target_label,
            MLTrainingSample.feature_json,
            MLTrainingSample.split,
        )
        .where(
            MLTrainingSample.symbol == SYMBOL,
            MLTrainingSample.timeframe == TIMEFRAME,
            MLTrainingSample.feature_version == FEATURE_VERSION,
            MLTrainingSample.label_version == LABEL_VERSION,
        )
        .order_by(MLTrainingSample.feature_timestamp.asc())
        .execution_options(yield_per=5000)
    )

    with SessionLocal() as session:
        for row in session.execute(statement):
            total += 1
            feature_timestamp = row.feature_timestamp
            target_timestamp = row.target_timestamp
            split = row.split
            target_label = row.target_label
            feature_json = row.feature_json

            first_feature = first_feature or feature_timestamp
            last_feature = feature_timestamp

            split_counts[split] += 1
            label_counts[target_label] += 1
            split_label_counts[(split, target_label)] += 1

            expected_split = split_plan.assign(
                feature_timestamp,
                target_timestamp,
            )
            def record_error(error):
                if len(integrity_errors) < 25:
                    integrity_errors.append(error)

            if expected_split != split:
                record_error(
                    (
                        row.id,
                        "split_mismatch",
                        split,
                        expected_split,
                    )
                )

            if target_timestamp - feature_timestamp != expected_horizon:
                record_error(
                    (
                        row.id,
                        "target_horizon_mismatch",
                        feature_timestamp,
                        target_timestamp,
                    )
                )

            if previous_feature == feature_timestamp:
                record_error(
                    (
                        row.id,
                        "duplicate_feature_timestamp",
                        feature_timestamp,
                    )
                )
            previous_feature = feature_timestamp

            feature_json = feature_json or {}
            keys = set(feature_json)
            if keys != expected_feature_names:
                record_error(
                    (
                        row.id,
                        "feature_schema_mismatch",
                        len(keys),
                        len(expected_feature_names),
                    )
                )

            if any(
                not isinstance(value, (int, float))
                or not isfinite(float(value))
                for value in feature_json.values()
            ):
                record_error(
                    (row.id, "non_finite_or_non_numeric_feature")
                )

    print("=" * 70)
    print("EUR/USD ORACLE ML DATASET AUDIT")
    print("=" * 70)
    print(f"Rows audited:         {total}")
    print(f"First feature:        {first_feature}")
    print(f"Last feature:         {last_feature}")
    print(f"Train boundary:       {split_plan.train_end}")
    print(f"Validation boundary:  {split_plan.validation_end}")
    print(f"Expected features:    {len(expected_feature_names)}")

    print("\nSplit counts")
    for split in ("train", "validation", "test"):
        print(f"  {split:10s}: {split_counts[split]}")

    print("\nLabel counts")
    for label in ("long", "neutral", "short"):
        print(f"  {label:10s}: {label_counts[label]}")

    print("\nSplit / label matrix")
    for split in ("train", "validation", "test"):
        values = " ".join(
            f"{label}={split_label_counts[(split, label)]}"
            for label in ("long", "neutral", "short")
        )
        print(f"  {split:10s}: {values}")

    if integrity_errors:
        print("\nINTEGRITY ERRORS (first 25)")
        for error in integrity_errors:
            print("  ", error)
        raise SystemExit("ML DATASET AUDIT: FAILED")

    if total == 0:
        raise SystemExit("ML DATASET AUDIT: FAILED - no rows found")

    print("\nSplit leakage:        NONE")
    print("Target horizon:       PASS")
    print("27-feature schema:    PASS")
    print("ML DATASET AUDIT:     PASS")


if __name__ == "__main__":
    main()
