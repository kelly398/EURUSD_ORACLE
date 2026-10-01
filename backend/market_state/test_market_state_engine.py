from datetime import timedelta

from backend.market_state.schemas import (
    DataQualityStatus
)

from backend.market_state.market_state_engine import (
    MarketStateEngine
)


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "UNIFIED MARKET STATE TEST"
    )

    print(
        "=" * 70
    )


    engine = (
        MarketStateEngine(

            symbol="EURUSD",

            timeframe="M5",

            timeframe_minutes=5,

            candle_limit=200
        )
    )


    result = (
        engine.build_snapshot()
    )


    snapshot = (
        result.snapshot
    )


    # ==================================
    # DISPLAY SNAPSHOT IDENTITY
    # ==================================

    print(
        f"\nSymbol: "
        f"{snapshot.symbol}"
    )

    print(
        f"Timeframe: "
        f"{snapshot.timeframe}"
    )

    print(
        f"Anchor candle: "
        f"{result.anchor_candle_timestamp}"
    )

    print(
        f"Snapshot as-of: "
        f"{snapshot.as_of}"
    )

    print(
        f"Candles used: "
        f"{snapshot.candle_count}"
    )

    print(
        f"Data quality: "
        f"{snapshot.data_quality.value}"
    )


    # ==================================
    # TREND
    # ==================================

    print(
        "\n"
        + "-" * 70
    )

    print(
        "TREND"
    )

    print(
        "-" * 70
    )

    print(
        f"Direction: "
        f"{snapshot.trend.direction.value}"
    )

    print(
        f"Strength: "
        f"{snapshot.trend.strength:.4f}"
    )

    print(
        "Slope pips/bar: "
        f"{snapshot.trend.slope_pips_per_bar:.4f}"
    )

    print(
        "Composite score: "
        f"{result.trend.composite_score:.4f}"
    )


    # ==================================
    # VOLATILITY
    # ==================================

    print(
        "\n"
        + "-" * 70
    )

    print(
        "VOLATILITY"
    )

    print(
        "-" * 70
    )

    print(
        f"Regime: "
        f"{snapshot.volatility.regime.value}"
    )

    print(
        f"ATR: "
        f"{snapshot.volatility.atr_pips:.4f} pips"
    )

    print(
        "Composite ratio: "
        f"{result.volatility.composite_ratio:.4f}"
    )


    # ==================================
    # MOMENTUM
    # ==================================

    print(
        "\n"
        + "-" * 70
    )

    print(
        "MOMENTUM"
    )

    print(
        "-" * 70
    )

    print(
        f"State: "
        f"{snapshot.momentum.state.value}"
    )

    print(
        f"Strength: "
        f"{snapshot.momentum.strength:.4f}"
    )

    print(
        "Composite score: "
        f"{result.momentum.composite_score:.4f}"
    )

    print(
        f"RSI: "
        f"{result.momentum.rsi:.2f}"
    )


    # ==================================
    # STRUCTURE
    # ==================================

    print(
        "\n"
        + "-" * 70
    )

    print(
        "MARKET STRUCTURE"
    )

    print(
        "-" * 70
    )

    print(
        f"Structure: "
        f"{snapshot.structure.label}"
    )

    print(
        f"Confidence: "
        f"{snapshot.structure.confidence:.4f}"
    )

    print(
        "Structure score: "
        f"{result.structure.structure_score:.4f}"
    )

    print(
        "High relation: "
        f"{result.structure.high_relation}"
    )

    print(
        "Low relation: "
        f"{result.structure.low_relation}"
    )


    # ==================================
    # LIQUIDITY
    # ==================================

    print(
        "\n"
        + "-" * 70
    )

    print(
        "LIQUIDITY"
    )

    print(
        "-" * 70
    )

    print(
        f"Spread state: "
        f"{snapshot.liquidity.spread_state.value}"
    )

    print(
        "Average spread: "
        f"{snapshot.liquidity.average_spread_pips:.4f} "
        "pips"
    )

    print(
        f"Tick activity: "
        f"{snapshot.liquidity.tick_activity}"
    )

    print(
        "Spread ratio: "
        f"{result.liquidity.spread_ratio:.4f}"
    )

    print(
        "Activity ratio: "
        f"{result.liquidity.tick_activity_ratio:.4f}"
    )

    print(
        f"Ticks used: "
        f"{result.tick_count}"
    )

    print(
        f"Tick window: "
        f"{result.tick_window_start}"
        f" -> "
        f"{result.tick_window_end}"
    )


    # ==================================
    # SESSION
    # ==================================

    print(
        "\n"
        + "-" * 70
    )

    print(
        "TRADING SESSION"
    )

    print(
        "-" * 70
    )

    print(
        f"Session: "
        f"{snapshot.session}"
    )

    print(
        "Active sessions: "
        f"{result.session.active_sessions}"
    )

    print(
        f"Market open: "
        f"{result.session.market_open}"
    )


    # ==================================
    # INTEGRATION ASSERTIONS
    # ==================================

    expected_as_of = (

        result.anchor_candle_timestamp

        + timedelta(
            minutes=5
        )
    )


    assert (
        snapshot.data_quality
        == DataQualityStatus.PASS
    )


    assert (
        result.quality_report[
            "status"
        ]
        == "PASS"
    )


    assert (
        result.anchor_candle_timestamp
        == result.quality_report[
            "latest_broker_candle"
        ]
    )


    assert (
        result.anchor_candle_timestamp
        == result.quality_report[
            "expected_latest_candle"
        ]
    )


    assert (
        snapshot.as_of
        == expected_as_of
    )


    assert (
        result.tick_window_end
        == snapshot.as_of
    )


    assert (
        snapshot.candle_count
        >= 140
    )


    assert (
        snapshot.trend
        is not None
    )


    assert (
        snapshot.volatility
        is not None
    )


    assert (
        snapshot.momentum
        is not None
    )


    assert (
        snapshot.structure
        is not None
    )


    assert (
        snapshot.liquidity
        is not None
    )


    assert (
        snapshot.session
        is not None
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "UNIFIED MARKET STATE "
        "ENGINE TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    