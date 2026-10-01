from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.market_state.schemas import (
    CandleBar,
    VolatilityRegime
)

from backend.market_state.volatility_engine import (
    VolatilityEngine
)

from backend.market_state.candle_repository import (
    CandleRepository
)


def create_volatility_test_candles(
    recent_factor,
    count=180
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

        if index >= count - 30:

            factor = recent_factor

        else:

            factor = 1.0


        direction = (
            1
            if index % 2 == 0
            else -1
        )


        variation = (
            1
            + (
                index % 3
            ) * 0.1
        )


        move = (

            direction

            * 0.00003

            * factor

            * variation
        )


        open_price = price

        close_price = (
            open_price
            + move
        )


        padding = (
            0.00002
            * factor
        )


        high_price = (

            max(
                open_price,
                close_price
            )

            + padding
        )


        low_price = (

            min(
                open_price,
                close_price
            )

            - padding
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
        "Regime: "
        f"{result.state.regime.value}"
    )

    print(
        "Current ATR: "
        f"{result.current_atr_pips:.4f} pips"
    )

    print(
        "Baseline ATR: "
        f"{result.baseline_atr_pips:.4f} pips"
    )

    print(
        "ATR ratio: "
        f"{result.atr_ratio:.4f}"
    )

    print(
        "Realized volatility: "
        f"{result.realized_volatility_pips:.4f} pips"
    )

    print(
        "Realized volatility ratio: "
        f"{result.realized_volatility_ratio:.4f}"
    )

    print(
        "Range shock ratio: "
        f"{result.range_shock_ratio:.4f}"
    )

    print(
        "Composite ratio: "
        f"{result.composite_ratio:.4f}"
    )


def main():

    engine = VolatilityEngine()


    # =================================
    # LOW VOLATILITY
    # =================================

    low_candles = (
        create_volatility_test_candles(
            0.40
        )
    )


    low_result = (
        engine.analyze_detailed(
            low_candles
        )
    )


    print_result(
        "SYNTHETIC LOW VOLATILITY",
        low_result
    )


    assert (
        low_result
        .state
        .regime
        == VolatilityRegime.LOW
    )


    # =================================
    # NORMAL VOLATILITY
    # =================================

    normal_candles = (
        create_volatility_test_candles(
            1.0
        )
    )


    normal_result = (
        engine.analyze_detailed(
            normal_candles
        )
    )


    print_result(
        "SYNTHETIC NORMAL VOLATILITY",
        normal_result
    )


    assert (
        normal_result
        .state
        .regime
        == VolatilityRegime.NORMAL
    )


    # =================================
    # HIGH VOLATILITY
    # =================================

    high_candles = (
        create_volatility_test_candles(
            1.70
        )
    )


    high_result = (
        engine.analyze_detailed(
            high_candles
        )
    )


    print_result(
        "SYNTHETIC HIGH VOLATILITY",
        high_result
    )


    assert (
        high_result
        .state
        .regime
        == VolatilityRegime.HIGH
    )


    # =================================
    # EXTREME VOLATILITY
    # =================================

    extreme_candles = (
        create_volatility_test_candles(
            3.0
        )
    )


    extreme_result = (
        engine.analyze_detailed(
            extreme_candles
        )
    )


    print_result(
        "SYNTHETIC EXTREME VOLATILITY",
        extreme_result
    )


    assert (
        extreme_result
        .state
        .regime
        == VolatilityRegime.EXTREME
    )


    # =================================
    # LIVE HFM EUR/USD DATA
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
        "LIVE EUR/USD VOLATILITY TEST"
    )

    print(
        "=" * 60
    )


    if len(live_candles) < 140:

        print(
            "Not enough canonical "
            "candles for live "
            "volatility test."
        )

    else:

        live_result = (
            engine.analyze_detailed(
                live_candles
            )
        )


        print_result(
            "CURRENT EUR/USD M5 VOLATILITY",
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
        "VOLATILITY ENGINE TEST: PASS"
    )

    print(
        "=" * 60
    )


if __name__ == "__main__":

    main()
    