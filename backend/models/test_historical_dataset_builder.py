from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.market_state.schemas import (
    CandleBar,
    TickSample
)

from backend.models.historical_dataset_builder import (
    HistoricalDatasetBuilder
)

from backend.models.training_contracts import (
    LabelSpecification,
    TargetLabel
)


def create_candles(
    count=230
):

    start = datetime(
        2026,
        1,
        5,
        0,
        0,
        tzinfo=timezone.utc
    )


    candles = []


    price = 1.10000


    for index in range(count):

        open_price = (
            price
        )


        # Gentle deterministic trend
        # plus alternating variation.
        move = (

            0.00001

            + (
                0.000005
                if index % 2 == 0
                else -0.000003
            )
        )


        close_price = (

            open_price
            + move
        )


        high_price = (

            max(
                open_price,
                close_price
            )

            + 0.00004
        )


        low_price = (

            min(
                open_price,
                close_price
            )

            - 0.00004
        )


        candles.append(

            CandleBar(

                symbol="EURUSD",

                timeframe="M5",

                timestamp=(

                    start

                    + timedelta(
                        minutes=
                            index * 5
                    )
                ),

                open=
                    open_price,

                high=
                    high_price,

                low=
                    low_price,

                close=
                    close_price,

                tick_volume=100,

                spread_points=15,

                real_volume=0.0,

                source="MT5"
            )
        )


        price = (
            close_price
        )


    return candles


def create_ticks(
    candles
):

    start = (
        candles[0].timestamp
    )


    end = (

        candles[-1].timestamp

        + timedelta(
            minutes=5
        )
    )


    ticks = []


    timestamp = (
        start
    )


    counter = 0


    while timestamp < end:

        elapsed_minutes = (

            timestamp
            - start

        ).total_seconds() / 60.0


        mid = (

            1.10000

            + elapsed_minutes
            * 0.000002
        )


        # Four quote updates per minute.
        for second in (
            5,
            20,
            35,
            50
        ):

            tick_time = (

                timestamp

                + timedelta(
                    seconds=second
                )
            )


            bid = (
                mid
                - 0.000075
            )


            ask = (
                mid
                + 0.000075
            )


            ticks.append(

                TickSample(

                    symbol="EURUSD",

                    timestamp=
                        tick_time,

                    source_time_msc=
                        int(
                            tick_time.timestamp()
                            * 1000
                        ),

                    bid=
                        bid,

                    ask=
                        ask,

                    spread_pips=
                        1.5
                )
            )


            counter += 1


        timestamp += timedelta(
            minutes=1
        )


    return ticks


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "HISTORICAL DATASET TEST"
    )

    print(
        "=" * 70
    )


    candles = (
        create_candles()
    )


    ticks = (
        create_ticks(
            candles
        )
    )


    specification = (
        LabelSpecification(

            label_version=
                "eurusd_m5_direction_v1",

            horizon_bars=3,

            timeframe_minutes=5,

            neutral_band_pips=0.10
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

            candles=
                candles,

            ticks=
                ticks
        )
    )


    print(
        f"\nCandles: "
        f"{len(candles)}"
    )

    print(
        f"Ticks: "
        f"{len(ticks)}"
    )

    print(
        f"Candidates: "
        f"{result.candidate_count}"
    )

    print(
        f"Created samples: "
        f"{result.created_count}"
    )

    print(
        f"Skipped target gaps: "
        f"{result.skipped_target_gap}"
    )

    print(
        f"Skipped replay errors: "
        f"{result.skipped_replay_error}"
    )


    assert (
        result.candidate_count
        == 28
    )


    assert (
        result.created_count
        == 28
    )


    assert (
        result.skipped_target_gap
        == 0
    )


    assert (
        result.skipped_replay_error
        == 0
    )


    first = (
        result.samples[0]
    )


    print(
        "\n"
        + "-" * 70
    )

    print(
        "FIRST TRAINING SAMPLE"
    )

    print(
        "-" * 70
    )


    print(
        f"Feature as-of: "
        f"{first.feature_as_of}"
    )

    print(
        f"Target available: "
        f"{first.target_available_at}"
    )

    print(
        f"Target move: "
        f"{first.target_move_pips:.4f} pips"
    )

    print(
        f"Target label: "
        f"{first.target_label.value}"
    )

    print(
        f"Feature count: "
        f"{len(first.features.values)}"
    )


    assert (
        first.target_available_at
        > first.feature_as_of
    )


    assert (
        len(
            first.features.values
        )
        == 27
    )


    assert (
        first.target_label
        in (
            TargetLabel.LONG,
            TargetLabel.SHORT,
            TargetLabel.NEUTRAL
        )
    )


    # ==================================
    # DETERMINISTIC REBUILD
    # ==================================

    second_result = (
        builder.build(

            candles=
                candles,

            ticks=
                ticks
        )
    )


    assert (
        result.samples
        == second_result.samples
    )


    print(
        "\nDeterministic dataset rebuild: PASS"
    )


    # ==================================
    # FUTURE-LEAKAGE TEST
    # ==================================
    #
    # First anchor = candle index 199.
    # Horizon = 3 bars.
    # Therefore target candle = index 202.
    #
    # Alter candle 202 dramatically.
    # Feature vector at index 199 MUST
    # remain identical because candle 202
    # did not exist at feature time.
    # ==================================

    modified_candles = list(
        candles
    )


    original_future = (
        modified_candles[202]
    )


    changed_close = (

        original_future.open
        - 0.00200
    )


    modified_candles[202] = (
        CandleBar(

            symbol=
                original_future.symbol,

            timeframe=
                original_future.timeframe,

            timestamp=
                original_future.timestamp,

            open=
                original_future.open,

            high=
                original_future.open
                + 0.00005,

            low=
                changed_close
                - 0.00005,

            close=
                changed_close,

            tick_volume=
                original_future.tick_volume,

            spread_points=
                original_future.spread_points,

            real_volume=
                original_future.real_volume,

            source=
                original_future.source
        )
    )


    modified_result = (
        builder.build(

            candles=
                modified_candles,

            ticks=
                ticks
        )
    )


    original_first = (
        result.samples[0]
    )


    modified_first = (
        modified_result.samples[0]
    )


    assert (
        original_first.features
        == modified_first.features
    )


    assert (
        original_first.target_move_pips
        != modified_first.target_move_pips
    )


    assert (
        original_first.target_label
        != modified_first.target_label
    )


    print(
        "Future-candle feature leakage: NONE"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "HISTORICAL DATASET "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    