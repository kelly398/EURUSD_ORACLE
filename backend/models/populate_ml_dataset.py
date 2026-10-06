import argparse
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.database.connection import SessionLocal
from backend.database.models import BrokerCandle, Tick
from backend.models.dataset_models import MLTrainingSample
from backend.market_state.schemas import TickSample, CandleBar
from backend.models.dataset_partitioning import (
    ChronologicalSplitPlan,
    FeatureBatch,
    iter_feature_batches,
)
from backend.models.feature_builder import MLFeatureBuilder
from backend.models.historical_dataset_builder import HistoricalDatasetBuilder
from backend.models.training_contracts import LabelSpecification


SYMBOL = "EURUSD"
TIMEFRAME = "M5"
SOURCE = "MT5"
FEATURE_VERSION = MLFeatureBuilder.FEATURE_VERSION
LABEL_VERSION = "eurusd_m5_direction_v1"
DATASET_VERSION = "eurusd_m5_dataset_v1"
CANDLE_WINDOW = 200
TIMEFRAME_MINUTES = 5
TICK_LOOKBACK_MINUTES = 65
HORIZON_BARS = 3
NEUTRAL_BAND_PIPS = 1.0

@dataclass
class PopulationStats:
    batches: int = 0
    candidates: int = 0
    generated: int = 0
    inserted: int = 0
    purged_split_boundary: int = 0
    skipped_target_gap: int = 0
    skipped_replay_error: int = 0


def parse_date(value):
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Build EUR/USD M5 training samples in bounded batches. "
            "--end is exclusive."
        )
    )
    parser.add_argument("--start", required=True, type=parse_date)
    parser.add_argument("--end", required=True, type=parse_date)
    parser.add_argument(
        "--batch-days",
        type=int,
        default=1,
        help="Calendar days loaded per in-memory batch (default: 1).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Generate and validate samples without inserting rows.",
    )
    return parser.parse_args()


def _base_candle_filters():
    return (
        BrokerCandle.symbol == SYMBOL,
        BrokerCandle.timeframe == TIMEFRAME,
        BrokerCandle.source == SOURCE,
    )
def convert_ticks_to_samples(ticks):
    return [
        TickSample(
            symbol=tick.symbol,
            timestamp=tick.timestamp,
            source_time_msc=tick.source_time_msc,
            bid=tick.bid,
            ask=tick.ask,
            spread_pips=tick.spread_pips,
        )
        for tick in ticks
    ]  
def convert_candles_to_samples(candles):
    return [
        CandleBar(
            symbol=candle.symbol,
            timeframe=candle.timeframe,
            timestamp=candle.timestamp,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            tick_volume=candle.tick_volume,
            spread_points=candle.spread_points,
            real_volume=candle.real_volume,
            source=candle.source,
        )
        for candle in candles
    ]
def load_batch_data(
    batch: FeatureBatch,
    label_specification: LabelSpecification,
    candle_window: int = CANDLE_WINDOW,
    tick_lookback_minutes: int = TICK_LOOKBACK_MINUTES,
):
    anchor_start = batch.anchor_start(TIMEFRAME_MINUTES)
    candle_load_end = batch.candle_load_end(
        label_specification.horizon_bars,
        label_specification.timeframe_minutes,
    )
    tick_load_start = batch.tick_load_start(tick_lookback_minutes)

    with SessionLocal() as session:
        predecessor_statement = (
            select(BrokerCandle)
            .where(
                *_base_candle_filters(),
                BrokerCandle.timestamp < anchor_start,
            )
            .order_by(BrokerCandle.timestamp.desc())
            .limit(candle_window - 1)
        )
        predecessors = list(
            reversed(session.execute(predecessor_statement).scalars().all())
        )

        batch_candle_statement = (
            select(BrokerCandle)
            .where(
                *_base_candle_filters(),
                BrokerCandle.timestamp >= anchor_start,
                BrokerCandle.timestamp < candle_load_end,
            )
            .order_by(BrokerCandle.timestamp.asc())
        )
        batch_candles = session.execute(
            batch_candle_statement
        ).scalars().all()

        tick_statement = (
            select(Tick)
            .where(
                Tick.symbol == SYMBOL,
                Tick.timestamp >= tick_load_start,
                Tick.timestamp < batch.end,
            )
            .order_by(
                Tick.timestamp.asc(),
                Tick.source_time_msc.asc(),
                Tick.id.asc(),
            )
        )
        ticks = session.execute(tick_statement).scalars().all()

    return (
        convert_candles_to_samples(predecessors + batch_candles),
        convert_ticks_to_samples(ticks),
    )

def apply_split_plan(samples, batch: FeatureBatch, split_plan):
    assigned = []
    purged = 0

    for sample in samples:
        if not batch.start <= sample.feature_as_of < batch.end:
            continue

        split = split_plan.assign(
            sample.feature_as_of,
            sample.target_available_at,
        )

        if split is None:
            purged += 1
            continue

        assigned.append(sample.model_copy(update={"split": split}))

    return assigned, purged


def insert_samples(samples):
    rows = [
        {
            "symbol": sample.features.symbol,
            "timeframe": sample.features.timeframe,
            "feature_version": sample.features.feature_version,
            "label_version": sample.label_specification.label_version,
            "feature_timestamp": sample.feature_as_of,
            "dataset_version": DATASET_VERSION,
            "target_timestamp": sample.target_available_at,
            "target_move_pips": sample.target_move_pips,
            "target_label": sample.target_label.value,
            "feature_json": sample.features.as_dict(),
            "split": sample.split,
        }
        for sample in samples
    ]

    if not rows:
        return 0

    statement = (
        pg_insert(MLTrainingSample)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_ml_training_sample")
    )

    with SessionLocal.begin() as session:
        result = session.execute(statement)
        return result.rowcount or 0


def existing_sample_count():
    with SessionLocal() as session:
        statement = select(func.count(MLTrainingSample.id)).where(
            MLTrainingSample.symbol == SYMBOL,
            MLTrainingSample.timeframe == TIMEFRAME,
            MLTrainingSample.feature_version == FEATURE_VERSION,
            MLTrainingSample.dataset_version == DATASET_VERSION,
            MLTrainingSample.label_version == LABEL_VERSION,
        )
        return session.execute(statement).scalar_one()


def build_label_specification():
    return LabelSpecification(
        label_version=LABEL_VERSION,
        horizon_bars=HORIZON_BARS,
        timeframe_minutes=TIMEFRAME_MINUTES,
        neutral_band_pips=NEUTRAL_BAND_PIPS,
    )


def main():
    arguments = parse_arguments()

    if arguments.end <= arguments.start:
        raise ValueError("End must be after start.")
    if arguments.batch_days <= 0:
        raise ValueError("batch-days must be positive.")

    specification = build_label_specification()
    builder = HistoricalDatasetBuilder(
        label_specification=specification,
        candle_window=CANDLE_WINDOW,
        timeframe_minutes=TIMEFRAME_MINUTES,
    )
    split_plan = ChronologicalSplitPlan.from_range(
        arguments.start,
        arguments.end,
    )

    print("=" * 70)
    print("EUR/USD ORACLE ML DATASET POPULATION")
    print("=" * 70)
    print(f"Dataset start:       {arguments.start}")
    print(f"Dataset end:         {arguments.end} (exclusive)")
    print(f"Train boundary:      {split_plan.train_end}")
    print(f"Validation boundary: {split_plan.validation_end}")
    print(f"Batch size:          {arguments.batch_days} day(s)")
    print(f"Dry run:             {arguments.dry_run}")

    if not arguments.dry_run:
        print(f"Existing rows:       {existing_sample_count()}")

    stats = PopulationStats()

    for batch_number, batch in enumerate(
        iter_feature_batches(
            arguments.start,
            arguments.end,
            arguments.batch_days,
        ),
        start=1,
    ):
        candles, ticks = load_batch_data(batch, specification)

        if not candles:
            print(
                f"[{batch_number:03d}] {batch.start.date()} "
                "no candles; skipped"
            )
            stats.batches += 1
            continue

        result = builder.build(candles=candles, ticks=ticks)
        samples, purged = apply_split_plan(
            result.samples,
            batch,
            split_plan,
        )

        inserted = 0 if arguments.dry_run else insert_samples(samples)

        stats.batches += 1
        stats.candidates += result.candidate_count
        stats.generated += len(samples)
        stats.inserted += inserted
        stats.purged_split_boundary += purged
        stats.skipped_target_gap += result.skipped_target_gap
        stats.skipped_replay_error += result.skipped_replay_error

        print(
            f"[{batch_number:03d}] "
            f"{batch.start.isoformat()} -> {batch.end.isoformat()} | "
            f"candles={len(candles)} ticks={len(ticks)} "
            f"candidates={result.candidate_count} samples={len(samples)} "
            f"purged={purged} inserted={inserted}"
        )

        # Batch-local lists/results fall out of scope on the next iteration.
        # This is the key memory bound for the ~30M-tick historical corpus.

    print("\n" + "-" * 70)
    print(f"Batches processed:             {stats.batches}")
    print(f"Candidate rows:                {stats.candidates}")
    print(f"Generated rows:                {stats.generated}")
    print(f"Inserted rows:                 {stats.inserted}")
    print(f"Purged at split boundaries:    {stats.purged_split_boundary}")
    print(f"Skipped target gaps:           {stats.skipped_target_gap}")
    print(f"Skipped replay errors:         {stats.skipped_replay_error}")
    print("=" * 70)
    print("ML DATASET POPULATION: COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
