from datetime import timedelta

from backend.agents.microstructure_features import (
    MicrostructureFeatureEngine
)

from backend.market_state.market_state_engine import (
    MarketStateEngine
)

from backend.market_state.tick_repository import (
    TickRepository
)


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "MICROSTRUCTURE FEATURES TEST"
    )

    print(
        "=" * 70
    )


    market_engine = (
        MarketStateEngine(

            symbol="EURUSD",

            timeframe="M5",

            timeframe_minutes=5,

            candle_limit=200
        )
    )


    unified = (
        market_engine.build_snapshot()
    )


    as_of = (
        unified.snapshot.as_of
    )


    tick_repository = (
        TickRepository()
    )


    ticks = (
        tick_repository.get_between(

            start=
                as_of
                - timedelta(
                    minutes=65
                ),

            end=
                as_of,

            symbol="EURUSD"
        )
    )


    print(
        f"\nSnapshot as-of: "
        f"{as_of}"
    )

    print(
        f"Ticks loaded: "
        f"{len(ticks)}"
    )


    engine = (
        MicrostructureFeatureEngine()
    )


    result = (
        engine.analyze(

            ticks=tuple(ticks),

            as_of=as_of
        )
    )


    print(
        "\n"
        + "-" * 70
    )

    print(
        "QUOTE-FLOW FEATURES"
    )

    print(
        "-" * 70
    )


    print(
        "Recent ticks: "
        f"{result.recent_tick_count}"
    )


    print(
        "Baseline ticks: "
        f"{result.baseline_tick_count}"
    )


    print(
        "Recent ticks/min: "
        f"{result.recent_ticks_per_minute:.4f}"
    )


    print(
        "Baseline ticks/min: "
        f"{result.baseline_ticks_per_minute:.4f}"
    )


    print(
        "Activity ratio: "
        f"{result.tick_activity_ratio:.4f}"
    )


    print(
        "Recent spread: "
        f"{result.average_spread_pips:.4f} pips"
    )


    print(
        "Baseline spread: "
        f"{result.baseline_spread_pips:.4f} pips"
    )


    print(
        "Spread ratio: "
        f"{result.spread_ratio:.4f}"
    )


    print(
        f"Up updates: "
        f"{result.up_updates}"
    )


    print(
        f"Down updates: "
        f"{result.down_updates}"
    )


    print(
        f"Flat updates: "
        f"{result.flat_updates}"
    )


    print(
        "Update imbalance: "
        f"{result.update_imbalance:.4f}"
    )


    print(
        "Net mid move: "
        f"{result.net_mid_move_pips:.4f} pips"
    )


    print(
        "Mean absolute mid update: "
        f"{result.mean_absolute_mid_move_pips:.4f} pips"
    )


    # ==================================
    # BASIC INVARIANTS
    # ==================================

    assert (
        result.as_of
        == as_of
    )


    assert (
        result.recent_tick_count
        >= 5
    )


    assert (
        result.baseline_tick_count
        >= 20
    )


    assert (
        -1.0
        <= result.update_imbalance
        <= 1.0
    )


    assert (
        result.average_spread_pips
        >= 0.0
    )


    assert (
        result.baseline_spread_pips
        >= 0.0
    )


    assert (
        result.directional_updates
        == (
            result.up_updates
            + result.down_updates
        )
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "MICROSTRUCTURE FEATURES "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    