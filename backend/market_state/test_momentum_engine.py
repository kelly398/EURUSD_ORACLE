from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.market_state.schemas import (
    CandleBar,
    MomentumState
)

from backend.market_state.momentum_engine import (
    MomentumEngine
)

from backend.market_state.candle_repository import (
    CandleRepository
)


def create_momentum_test_candles(
    direction,
    count=100
):

    candles = []

    start = datetime(
        2026,
        1,
        1,
        tzinfo=timezone.utc
    )

    price = 1.10000


    for index in range(count):

        if direction == "positive":

            move = (
                0.00004
                + (
                    index
                    / count
                )
                * 0.00002
            )


        elif direction == "negative":

            move = -(
                0.00004
                + (
                    index
                    / count
                )
                * 0.00002
            )


        else:

            # Flat closes with a valid
            # intrabar range represent a
            # genuinely neutral momentum
            # environment.
            move = 0.0


        open_price = price

        close_price = (
            open_price
            + move
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

            open=open_price,

            high=high_price,

            low=low_price,

            close=close_price,

            tick_volume=100,

            spread_points=15,

            real_volume=0.0,

            source="MT5"
        )


        candles.append(
            candle
        )


        price = close_price


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
        "Momentum state: "
        f"{result.state.state.value}"
    )

    print(
        "Strength: "
        f"{result.state.strength:.4f}"
    )

    print(
        "Composite score: "
        f"{result.composite_score:.4f}"
    )

    print(
        "Impulse score: "
        f"{result.impulse_score:.4f}"
    )

    print(
        "RSI: "
        f"{result.rsi:.2f}"
    )

    print(
        "RSI score: "
        f"{result.rsi_score:.4f}"
    )

    print(
        "Acceleration score: "
        f"{result.acceleration_score:.4f}"
    )

    print(
        "Body pressure score: "
        f"{result.body_pressure_score:.4f}"
    )

    print(
        "Net impulse: "
        f"{result.net_impulse_pips:.4f} pips"
    )

    print(
        "Average true range: "
        f"{result.average_true_range_pips:.4f} pips"
    )


def main():

    engine = MomentumEngine()


    # =================================
    # POSITIVE MOMENTUM
    # =================================

    positive = (
        create_momentum_test_candles(
            "positive"
        )
    )


    positive_result = (
        engine.analyze_detailed(
            positive
        )
    )


    print_result(
        "SYNTHETIC POSITIVE MOMENTUM",
        positive_result
    )


    assert (
        positive_result
        .state
        .state
        == MomentumState.POSITIVE
    )


    # =================================
    # NEGATIVE MOMENTUM
    # =================================

    negative = (
        create_momentum_test_candles(
            "negative"
        )
    )


    negative_result = (
        engine.analyze_detailed(
            negative
        )
    )


    print_result(
        "SYNTHETIC NEGATIVE MOMENTUM",
        negative_result
    )


    assert (
        negative_result
        .state
        .state
        == MomentumState.NEGATIVE
    )


    # =================================
    # NEUTRAL MOMENTUM
    # =================================

    neutral = (
        create_momentum_test_candles(
            "neutral"
        )
    )


    neutral_result = (
        engine.analyze_detailed(
            neutral
        )
    )


    print_result(
        "SYNTHETIC NEUTRAL MOMENTUM",
        neutral_result
    )


    assert (
        neutral_result
        .state
        .state
        == MomentumState.NEUTRAL
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

            limit=120
        )
    )


    print(
        "\n"
        + "=" * 60
    )

    print(
        "LIVE EUR/USD MOMENTUM TEST"
    )

    print(
        "=" * 60
    )


    if len(live_candles) < 60:

        print(
            "Not enough canonical "
            "candles for live "
            "momentum test."
        )

    else:

        live_result = (
            engine.analyze_detailed(
                live_candles
            )
        )


        print_result(
            "CURRENT EUR/USD M5 MOMENTUM",
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
        "MOMENTUM ENGINE TEST: PASS"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()
