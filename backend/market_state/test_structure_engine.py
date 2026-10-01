from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.market_state.schemas import (
    CandleBar
)

from backend.market_state.structure_engine import (
    MarketStructureEngine
)

from backend.market_state.candle_repository import (
    CandleRepository
)


def interpolate_anchors(
    anchors,
    bars_per_leg=6
):

    prices = []

    for start, end in zip(
        anchors,
        anchors[1:]
    ):

        for step in range(
            bars_per_leg
        ):

            fraction = (
                step
                / bars_per_leg
            )

            value = (
                start
                + (
                    end - start
                )
                * fraction
            )

            prices.append(value)


    prices.append(
        anchors[-1]
    )


    return prices


def create_structure_candles(
    structure_type
):

    if structure_type == "bullish":

        anchors = [
            1.1000,
            1.1020,
            1.1010,
            1.1030,
            1.1020,
            1.1040,
            1.1030,
            1.1050,
            1.1040,
            1.1060,
            1.1050,
            1.1070
        ]


    elif structure_type == "bearish":

        anchors = [
            1.1070,
            1.1050,
            1.1060,
            1.1040,
            1.1050,
            1.1030,
            1.1040,
            1.1020,
            1.1030,
            1.1010,
            1.1020,
            1.1000
        ]


    else:

        anchors = [
            1.1010,
            1.1030,
            1.1010,
            1.1030,
            1.1010,
            1.1030,
            1.1010,
            1.1030,
            1.1010,
            1.1030,
            1.1010,
            1.1030
        ]


    prices = interpolate_anchors(
        anchors,
        bars_per_leg=6
    )


    candles = []


    start_time = datetime(
        2026,
        1,
        1,
        tzinfo=timezone.utc
    )


    previous_close = prices[0]


    for index, close_price in enumerate(
        prices
    ):

        open_price = (
            previous_close
        )


        high_price = (
            max(
                open_price,
                close_price
            )
            + 0.00002
        )


        low_price = (
            min(
                open_price,
                close_price
            )
            - 0.00002
        )


        candles.append(
            CandleBar(
                symbol="EURUSD",

                timeframe="M5",

                timestamp=(
                    start_time
                    + timedelta(
                        minutes=
                        index * 5
                    )
                ),

                open=open_price,

                high=high_price,

                low=low_price,

                close=close_price,

                tick_volume=100,

                spread_points=15,

                real_volume=0.0,

                source="MT5"
            )
        )


        previous_close = (
            close_price
        )


    return candles


def print_result(
    title,
    result
):

    print(
        f"\n{title}"
    )

    print(
        "-" * 60
    )

    print(
        "Structure: "
        f"{result.state.label}"
    )

    print(
        "Confidence: "
        f"{result.state.confidence:.4f}"
    )

    print(
        "Structure score: "
        f"{result.structure_score:.4f}"
    )

    print(
        "High relation: "
        f"{result.high_relation}"
    )

    print(
        "Low relation: "
        f"{result.low_relation}"
    )

    print(
        "Swing highs found: "
        f"{result.swing_high_count}"
    )

    print(
        "Swing lows found: "
        f"{result.swing_low_count}"
    )

    print(
        "ATR: "
        f"{result.average_true_range_pips:.4f} pips"
    )

    print(
        "Structure tolerance: "
        f"{result.tolerance_pips:.4f} pips"
    )


    if (
        result.previous_swing_high
        is not None
    ):

        print(
            "Previous swing high: "
            f"{result.previous_swing_high.price:.5f}"
        )


    if (
        result.latest_swing_high
        is not None
    ):

        print(
            "Latest swing high: "
            f"{result.latest_swing_high.price:.5f}"
        )


    if (
        result.previous_swing_low
        is not None
    ):

        print(
            "Previous swing low: "
            f"{result.previous_swing_low.price:.5f}"
        )


    if (
        result.latest_swing_low
        is not None
    ):

        print(
            "Latest swing low: "
            f"{result.latest_swing_low.price:.5f}"
        )


def main():

    engine = (
        MarketStructureEngine()
    )


    # =================================
    # BULLISH STRUCTURE
    # =================================

    bullish = (
        create_structure_candles(
            "bullish"
        )
    )


    bullish_result = (
        engine.analyze_detailed(
            bullish
        )
    )


    print_result(
        "SYNTHETIC BULLISH STRUCTURE",
        bullish_result
    )


    assert (
        bullish_result.state.label
        == "higher_highs_higher_lows"
    )


    # =================================
    # BEARISH STRUCTURE
    # =================================

    bearish = (
        create_structure_candles(
            "bearish"
        )
    )


    bearish_result = (
        engine.analyze_detailed(
            bearish
        )
    )


    print_result(
        "SYNTHETIC BEARISH STRUCTURE",
        bearish_result
    )


    assert (
        bearish_result.state.label
        == "lower_highs_lower_lows"
    )


    # =================================
    # RANGE STRUCTURE
    # =================================

    ranging = (
        create_structure_candles(
            "range"
        )
    )


    range_result = (
        engine.analyze_detailed(
            ranging
        )
    )


    print_result(
        "SYNTHETIC RANGE STRUCTURE",
        range_result
    )


    assert (
        range_result.state.label
        == "range"
    )


    # =================================
    # LIVE HFM / MT5 DATA
    # =================================

    repository = CandleRepository(
        source="MT5"
    )


    live_candles = (
        repository.get_latest(
            symbol="EURUSD",
            timeframe="M5",
            limit=200
        )
    )


    print(
        "\n"
        + "=" * 60
    )

    print(
        "LIVE EUR/USD "
        "MARKET STRUCTURE TEST"
    )

    print(
        "=" * 60
    )


    if len(live_candles) < 60:

        print(
            "Not enough canonical "
            "candles for live "
            "structure test."
        )

    else:

        live_result = (
            engine.analyze_detailed(
                live_candles
            )
        )


        print_result(
            "CURRENT EUR/USD M5 STRUCTURE",
            live_result
        )


        print(
            "\nLatest candle:"
        )

        print(
            live_candles[-1].timestamp
        )


    print(
        "\n"
        + "=" * 60
    )

    print(
        "MARKET STRUCTURE "
        "ENGINE TEST: PASS"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()
    