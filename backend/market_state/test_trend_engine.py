from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.market_state.schemas import (
    CandleBar,
    MarketDirection
)

from backend.market_state.trend_engine import (
    TrendEngine
)

from backend.market_state.candle_repository import (
    CandleRepository
)


def create_test_candles(
    direction,
    count=80
):

    candles = []

    start = datetime(
        2026,
        1,
        1,
        tzinfo=timezone.utc
    )

    base_price = 1.10000


    for index in range(count):

        if direction == "bullish":

            close = (
                base_price
                + index * 0.00005
            )


        elif direction == "bearish":

            close = (
                base_price
                - index * 0.00005
            )


        else:

            close = base_price


        candle = CandleBar(

            symbol="EURUSD",

            timeframe="M5",

            timestamp=(
                start
                + timedelta(
                    minutes=
                    5 * index
                )
            ),

            open=close,

            high=(
                close
                + 0.00002
            ),

            low=(
                close
                - 0.00002
            ),

            close=close,

            tick_volume=100,

            spread_points=15,

            real_volume=0.0,

            source="MT5"
        )


        candles.append(
            candle
        )


    return candles


def print_result(
    label,
    result
):

    print(
        f"\n{label}"
    )

    print(
        "-" * 60
    )

    print(
        f"Direction: "
        f"{result.state.direction.value}"
    )

    print(
        f"Strength: "
        f"{result.state.strength:.4f}"
    )

    print(
        "Slope pips/bar: "
        f"{result.state.slope_pips_per_bar:.4f}"
    )

    print(
        "Composite score: "
        f"{result.composite_score:.4f}"
    )

    print(
        "Regression: "
        f"{result.regression_score:.4f}"
    )

    print(
        "EMA: "
        f"{result.ema_score:.4f}"
    )

    print(
        "Price location: "
        f"{result.price_location_score:.4f}"
    )

    print(
        "Persistence: "
        f"{result.persistence_score:.4f}"
    )

    print(
        "Structure: "
        f"{result.structure_score:.4f}"
    )

    print(
        "Average true range: "
        f"{result.average_true_range_pips:.4f} pips"
    )


def main():

    engine = TrendEngine()


    # =================================
    # SYNTHETIC BULLISH TEST
    # =================================

    bullish = create_test_candles(
        "bullish"
    )


    bullish_result = (
        engine.analyze_detailed(
            bullish
        )
    )


    print_result(
        "SYNTHETIC BULLISH TREND",
        bullish_result
    )


    assert (
        bullish_result
        .state
        .direction
        == MarketDirection.BULLISH
    )


    # =================================
    # SYNTHETIC BEARISH TEST
    # =================================

    bearish = create_test_candles(
        "bearish"
    )


    bearish_result = (
        engine.analyze_detailed(
            bearish
        )
    )


    print_result(
        "SYNTHETIC BEARISH TREND",
        bearish_result
    )


    assert (
        bearish_result
        .state
        .direction
        == MarketDirection.BEARISH
    )


    # =================================
    # SYNTHETIC NEUTRAL TEST
    # =================================

    neutral = create_test_candles(
        "neutral"
    )


    neutral_result = (
        engine.analyze_detailed(
            neutral
        )
    )


    print_result(
        "SYNTHETIC NEUTRAL MARKET",
        neutral_result
    )


    assert (
        neutral_result
        .state
        .direction
        == MarketDirection.NEUTRAL
    )


    # =================================
    # REAL HFM / MT5 DATA
    # =================================

    repository = CandleRepository(
        source="MT5"
    )


    live_candles = (
        repository.get_latest(

            symbol="EURUSD",

            timeframe="M5",

            limit=120
        )
    )


    print(
        "\n"
        + "=" * 60
    )

    print(
        "LIVE EUR/USD TREND TEST"
    )

    print(
        "=" * 60
    )


    if len(live_candles) < 60:

        print(
            "Not enough canonical "
            "candles for live trend test."
        )

    else:

        live_result = (
            engine.analyze_detailed(
                live_candles
            )
        )


        print_result(
            "CURRENT EUR/USD M5 TREND",
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
        "TREND ENGINE TEST: PASS"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()
    