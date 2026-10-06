import argparse

from datetime import (
    datetime,
    timezone
)

from sqlalchemy.dialects.postgresql import (
    insert as pg_insert
)

from sqlalchemy import (
    select
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    BrokerCandle,
    Tick
)

from backend.models.dataset_models import (
    MLTrainingSample
)

from backend.models.historical_dataset_builder import (
    HistoricalDatasetBuilder
)

from backend.models.training_contracts import (
    LabelSpecification
)


def parse_date(
    value
):

    return datetime.strptime(
        value,
        "%Y-%m-%d"
    ).replace(
        tzinfo=timezone.utc
    )


def parse_arguments():

    parser = argparse.ArgumentParser()


    parser.add_argument(
        "--start",
        required=True,
        type=parse_date
    )


    parser.add_argument(
        "--end",
        required=True,
        type=parse_date
    )


    return parser.parse_args()


def load_candles(
    start,
    end
):

    session = (
        SessionLocal()
    )

    try:

        statement = (

            select(
                BrokerCandle
            )

            .where(

                BrokerCandle.symbol
                == "EURUSD",

                BrokerCandle.timeframe
                == "M5",

                BrokerCandle.source
                == "MT5",

                BrokerCandle.timestamp
                >= start,

                BrokerCandle.timestamp
                < end
            )

            .order_by(
                BrokerCandle.timestamp.asc()
            )
        )


        return (

            session.execute(
                statement
            )

            .scalars()

            .all()
        )


    finally:

        session.close()


def load_ticks(
    start,
    end
):

    session = (
        SessionLocal()
    )

    try:

        statement = (

            select(
                Tick
            )

            .where(

                Tick.symbol
                == "EURUSD",

                Tick.timestamp
                >= start,

                Tick.timestamp
                < end
            )

            .order_by(
                Tick.timestamp.asc()
            )
        )


        return (

            session.execute(
                statement
            )

            .scalars()

            .all()
        )


    finally:

        session.close()


def determine_split(
    timestamp,
    start,
    end
):

    total_seconds = (

        end
        - start

    ).total_seconds()


    elapsed = (

        timestamp
        - start

    ).total_seconds()


    ratio = (
        elapsed
        /
        total_seconds
    )


    if ratio < 0.70:

        return "train"


    if ratio < 0.85:

        return "validation"


    return "test"


def insert_samples(
    samples
):

    rows = []


    for sample in samples:

        rows.append(

            {
                "symbol":
                    sample.features.symbol,

                "timeframe":
                    sample.features.timeframe,

                "feature_version":
                    sample.features.feature_version,

                "label_version":
                    sample.label_specification
                    .label_version,

                "feature_timestamp":
                    sample.feature_as_of,

                "target_timestamp":
                    sample.target_available_at,

                "target_move_pips":
                    sample.target_move_pips,

                "target_label":
                    sample.target_label.value,

                "feature_json":
                    sample.features.as_dict(),

                "split":
                    sample.split
            }
        )


    if not rows:

        return 0


    inserted = 0


    statement = (

        pg_insert(
            MLTrainingSample
        )

        .values(
            rows
        )

        .on_conflict_do_nothing(
            constraint=
            "uq_ml_training_sample"
        )
    )


    with SessionLocal.begin() as session:

        result = session.execute(
            statement
        )


        inserted = (
            result.rowcount
            or 0
        )


    return inserted


def main():

    arguments = (
        parse_arguments()
    )


    if (
        arguments.end
        <= arguments.start
    ):

        raise ValueError(
            "End must be after start."
        )


    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "ML DATASET POPULATION"
    )

    print(
        "=" * 70
    )


    print(
        f"\nStart: "
        f"{arguments.start}"
    )


    print(
        f"End: "
        f"{arguments.end}"
    )


    candles = (
        load_candles(

            arguments.start,

            arguments.end
        )
    )


    ticks = (
        load_ticks(

            arguments.start,

            arguments.end
        )
    )


    print(
        f"\nCandles loaded: "
        f"{len(candles)}"
    )


    print(
        f"Ticks loaded: "
        f"{len(ticks)}"
    )


    specification = (
        LabelSpecification(

            label_version=
            "eurusd_m5_direction_v1",

            horizon_bars=3,

            timeframe_minutes=5,

            neutral_band_pips=1.0
        )
    )


    builder = (
        HistoricalDatasetBuilder(

            label_specification=
                specification,

            candle_window=200,

            timeframe_minutes=5
        )
    )


    result = (
        builder.build(

            candles=candles,

            ticks=ticks
        )
    )


    samples = []


    for sample in result.samples:

        split = determine_split(

            sample.feature_as_of,

            arguments.start,

            arguments.end
        )


        sample = sample.model_copy(

            update={
                "split":
                    split
            }
        )


        samples.append(
            sample
        )


    inserted = (
        insert_samples(
            samples
        )
    )


    print(
        "\n"
        + "-" * 70
    )


    print(
        f"Candidate rows: "
        f"{result.candidate_count}"
    )


    print(
        f"Generated rows: "
        f"{len(samples)}"
    )


    print(
        f"Inserted rows: "
        f"{inserted}"
    )


    print(
        f"Replay skips: "
        f"{result.skipped_replay_error}"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "ML DATASET POPULATION: COMPLETE"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    