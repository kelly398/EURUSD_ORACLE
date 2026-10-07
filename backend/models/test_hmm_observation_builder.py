from datetime import (
    datetime,
    timedelta,
    timezone,
)

from math import (
    isfinite,
    log,
)

from statistics import (
    pstdev,
)

from backend.market_state.schemas import (
    CandleBar,
)

from backend.models.hmm_observation_builder import (
    HMMObservationBuilder,
)


def make_candles(
    count,
    start,
    base_price=1.10000,
    symbol="EURUSD",
    timeframe="M5",
):

    candles = []


    for index in range(
        count
    ):

        close = (

            base_price

            + index
            * 0.00003

            + (
                (
                    index % 7
                )
                - 3
            )
            * 0.00001
        )


        body_offset = (

            (
                (
                    index % 3
                )
                - 1
            )

            * 0.000015
        )


        open_price = (

            close
            - body_offset
        )


        high = (

            max(
                open_price,
                close,
            )

            + 0.00008
        )


        low = (

            min(
                open_price,
                close,
            )

            - 0.00007
        )


        candles.append(

            CandleBar(

                symbol=
                    symbol,

                timeframe=
                    timeframe,

                timestamp=(
                    start

                    + timedelta(
                        minutes=
                            5
                            * index
                    )
                ),

                open=
                    open_price,

                high=
                    high,

                low=
                    low,

                close=
                    close,

                tick_volume=
                    100
                    + index
                    * 3,

                spread_points=
                    12,

                real_volume=
                    0.0,

                source=
                    "TEST",
            )
        )


    return candles


def regression_slope(
    values
):

    count = len(
        values
    )


    x_mean = (

        count
        - 1

    ) / 2.0


    y_mean = (

        sum(
            values
        )

        / count
    )


    numerator = sum(

        (
            index
            - x_mean
        )

        * (
            value
            - y_mean
        )

        for index, value
        in enumerate(
            values
        )
    )


    denominator = sum(

        (
            index
            - x_mean
        )
        ** 2

        for index
        in range(
            count
        )
    )


    return (

        numerator
        / denominator
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "HMM OBSERVATION BUILDER TEST"
    )

    print(
        "=" * 70
    )


    builder = (
        HMMObservationBuilder()
    )


    start = datetime(
        2026,
        9,
        30,
        8,
        0,
        tzinfo=timezone.utc,
    )


    candles = make_candles(
        count=40,
        start=start,
    )


    sequence = (
        builder.build(
            candles
        )
    )


    print(
        f"\nObservations: "
        f"{sequence.observation_count}"
    )

    print(
        f"Segments: "
        f"{sequence.segment_lengths}"
    )

    print(
        f"First as-of: "
        f"{sequence.as_of_timestamps[0]}"
    )

    print(
        f"Last as-of: "
        f"{sequence.latest_as_of}"
    )


    # ==================================
    # CONTRACT
    # ==================================

    assert (
        sequence.symbol
        == "EURUSD"
    )


    assert (
        sequence.timeframe
        == "M5"
    )


    assert (
        sequence.timeframe_minutes
        == 5
    )


    assert (
        sequence.feature_version
        == "eurusd_m5_hmm_obs_v1"
    )


    assert (
        sequence.feature_names
        == (
            "return_1_pips",
            "return_3_pips",
            "body_pips",
            "range_pips",
            "close_location",
            "realized_volatility_12_pips",
            "trend_slope_12_pips_per_bar",
            "log_tick_volume_ratio_12",
        )
    )


    assert (
        sequence.observation_count
        == 28
    )


    assert (
        sequence.segment_lengths
        == (
            28,
        )
    )


    assert (
        sequence.as_of_timestamps[
            0
        ]
        == (
            candles[
                12
            ].timestamp
            + timedelta(
                minutes=5
            )
        )
    )


    assert (
        sequence.latest_as_of
        == (
            candles[
                -1
            ].timestamp
            + timedelta(
                minutes=5
            )
        )
    )


    # ==================================
    # INDEPENDENT FEATURE CHECK
    # ==================================

    latest_index = (
        len(candles)
        - 1
    )


    closes = [

        candle.close

        for candle
        in candles
    ]


    latest = (
        candles[
            latest_index
        ]
    )


    expected_return_1 = (

        closes[
            latest_index
        ]

        - closes[
            latest_index - 1
        ]

    ) * 10000.0


    expected_return_3 = (

        closes[
            latest_index
        ]

        - closes[
            latest_index - 3
        ]

    ) * 10000.0


    expected_body = (

        latest.close
        - latest.open

    ) * 10000.0


    expected_range = (

        latest.high
        - latest.low

    ) * 10000.0


    expected_close_location = (

        2.0

        * (
            (
                latest.close
                - latest.low
            )

            / (
                latest.high
                - latest.low
            )
        )

        - 1.0
    )


    expected_returns = [

        (
            closes[
                index
            ]

            - closes[
                index - 1
            ]
        )
        * 10000.0

        for index
        in range(
            latest_index - 11,
            latest_index + 1,
        )
    ]


    expected_volatility = (
        pstdev(
            expected_returns
        )
    )


    slope_window = (

        closes[
            latest_index - 11:
            latest_index + 1
        ]
    )


    expected_slope = (

        regression_slope(
            slope_window
        )

        * 10000.0
    )


    previous_volumes = [

        float(
            candle.tick_volume
        )

        for candle
        in candles[
            latest_index - 12:
            latest_index
        ]
    ]


    baseline_volume = (

        sum(
            previous_volumes
        )

        / len(
            previous_volumes
        )
    )


    expected_volume_ratio = log(

        (
            latest.tick_volume
            + 1.0
        )

        / (
            baseline_volume
            + 1.0
        )
    )


    expected_latest = (

        expected_return_1,

        expected_return_3,

        expected_body,

        expected_range,

        expected_close_location,

        expected_volatility,

        expected_slope,

        expected_volume_ratio,
    )


    actual_latest = (
        sequence.values[
            -1
        ]
    )


    for actual, expected in zip(
        actual_latest,
        expected_latest,
    ):

        assert (
            abs(
                actual
                - expected
            )
            < 1e-10
        )


    # ==================================
    # FINITE VALUES
    # ==================================

    for row in sequence.values:

        for value in row:

            assert isfinite(
                value
            )


    # ==================================
    # DETERMINISM
    # ==================================

    sequence_two = (
        builder.build(
            candles
        )
    )


    assert (
        sequence
        == sequence_two
    )


    # ==================================
    # CAUSALITY / FUTURE IMMUTABILITY
    # ==================================
    #
    # Adding a future completed candle
    # may append one new observation,
    # but it must never alter any
    # historical observation.
    # ==================================

    future_candle = (
        CandleBar(

            symbol="EURUSD",

            timeframe="M5",

            timestamp=(
                candles[
                    -1
                ].timestamp
                + timedelta(
                    minutes=5
                )
            ),

            open=1.3000,

            high=1.3100,

            low=1.2900,

            close=1.3050,

            tick_volume=999999,

            spread_points=50,

            real_volume=0.0,

            source="TEST",
        )
    )


    extended = (
        builder.build(

            candles
            + [
                future_candle
            ]
        )
    )


    assert (
        extended.values[
            :sequence.observation_count
        ]
        == sequence.values
    )


    assert (
        extended.as_of_timestamps[
            :sequence.observation_count
        ]
        == sequence.as_of_timestamps
    )


    assert (
        extended.observation_count
        == sequence.observation_count
        + 1
    )


    # ==================================
    # GAP SEGMENTATION
    # ==================================

    first_segment = make_candles(

        count=25,

        start=start,

        base_price=1.1000,
    )


    second_start = (

        first_segment[
            -1
        ].timestamp

        + timedelta(
            minutes=30
        )
    )


    second_segment = make_candles(

        count=20,

        start=second_start,

        base_price=1.1200,
    )


    segmented = (
        builder.build(

            first_segment
            + second_segment
        )
    )


    assert (
        segmented.segment_lengths
        == (
            13,
            8,
        )
    )


    assert (
        segmented.observation_count
        == 21
    )


    first_second_segment_as_of = (

        second_segment[
            12
        ].timestamp

        + timedelta(
            minutes=5
        )
    )


    assert (
        segmented.as_of_timestamps[
            13
        ]
        == first_second_segment_as_of
    )


    # ==================================
    # ZERO RANGE + ZERO VOLUME SAFETY
    # ==================================

    safe_candles = make_candles(
        count=20,
        start=start,
    )


    previous_close = (
        safe_candles[
            -2
        ].close
    )


    safe_candles[
        -1
    ] = CandleBar(

        symbol="EURUSD",

        timeframe="M5",

        timestamp=
            safe_candles[
                -1
            ].timestamp,

        open=
            previous_close,

        high=
            previous_close,

        low=
            previous_close,

        close=
            previous_close,

        tick_volume=0,

        spread_points=12,

        real_volume=0.0,

        source="TEST",
    )


    safe_sequence = (
        builder.build(
            safe_candles
        )
    )


    assert (
        safe_sequence.values[
            -1
        ][4]
        == 0.0
    )


    for value in (
        safe_sequence.values[
            -1
        ]
    ):

        assert isfinite(
            value
        )


    # ==================================
    # INVALID CADENCE REJECTION
    # ==================================

    invalid_cadence = make_candles(
        count=20,
        start=start,
    )


    invalid_cadence[
        10
    ] = (
        invalid_cadence[
            10
        ].model_copy(

            update={

                "timestamp":
                    invalid_cadence[
                        9
                    ].timestamp

                    + timedelta(
                        minutes=2
                    )
            }
        )
    )


    cadence_rejected = False


    try:

        builder.build(
            invalid_cadence
        )

    except ValueError:

        cadence_rejected = True


    assert cadence_rejected


    # ==================================
    # MIXED SYMBOL REJECTION
    # ==================================

    mixed_symbol = make_candles(
        count=20,
        start=start,
    )


    mixed_symbol[
        15
    ] = (
        mixed_symbol[
            15
        ].model_copy(

            update={
                "symbol":
                    "GBPUSD"
            }
        )
    )


    symbol_rejected = False


    try:

        builder.build(
            mixed_symbol
        )

    except ValueError:

        symbol_rejected = True


    assert symbol_rejected


    print(
        "\n8-feature contract: PASS"
    )

    print(
        "Exact feature formulas: PASS"
    )

    print(
        "Completed-bar timestamps: PASS"
    )

    print(
        "Finite-value contract: PASS"
    )

    print(
        "Deterministic output: PASS"
    )

    print(
        "Future observation isolation: PASS"
    )

    print(
        "Gap segmentation: PASS"
    )

    print(
        "Zero-range safety: PASS"
    )

    print(
        "Invalid cadence rejection: PASS"
    )

    print(
        "Mixed-symbol rejection: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "HMM OBSERVATION BUILDER "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()