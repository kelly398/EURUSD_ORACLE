from backend.agents.context_factory import (
    AgentContextFactory
)

from backend.market_state.market_state_engine import (
    MarketStateEngine
)

from backend.models.feature_builder import (
    MLFeatureBuilder
)


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "ML FEATURE BUILDER TEST"
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


    context_factory = (
        AgentContextFactory(

            candle_limit=200,

            tick_lookback_minutes=65,

            require_ticks=True
        )
    )


    context = (
        context_factory.build(
            unified
        )
    )


    builder = (
        MLFeatureBuilder()
    )


    vector_one = (
        builder.build(
            context
        )
    )


    # Build the same snapshot twice.
    # The result must be deterministic.
    vector_two = (
        builder.build(
            context
        )
    )


    print(
        f"\nFeature version: "
        f"{vector_one.feature_version}"
    )

    print(
        f"Snapshot as-of: "
        f"{vector_one.as_of}"
    )

    print(
        f"Feature count: "
        f"{len(vector_one.values)}"
    )


    print(
        "\n"
        + "-" * 70
    )

    print(
        "FEATURE VECTOR"
    )

    print(
        "-" * 70
    )


    for name, value in zip(
        vector_one.feature_names,
        vector_one.values
    ):

        print(
            f"{name:<32} "
            f"{value: .6f}"
        )


    # ==================================
    # CONTRACT ASSERTIONS
    # ==================================

    assert (
        vector_one.symbol
        == "EURUSD"
    )


    assert (
        vector_one.timeframe
        == "M5"
    )


    assert (
        vector_one.as_of
        == context.as_of
    )


    assert (
        vector_one.feature_version
        == "eurusd_m5_v1"
    )


    assert (
        len(
            vector_one.feature_names
        )
        == 27
    )


    assert (
        len(
            vector_one.values
        )
        == 27
    )


    assert (
        vector_one.feature_names
        == vector_two.feature_names
    )


    assert (
        vector_one.values
        == vector_two.values
    )


    assert (
        context.candles[-1].timestamp
        < context.as_of
    )


    assert (
        context.ticks[-1].timestamp
        < context.as_of
    )


    feature_dict = (
        vector_one.as_dict()
    )


    assert (
        "tick_update_imbalance"
        in feature_dict
    )


    assert (
        "trend_score"
        in feature_dict
    )


    assert (
        "structure_score"
        in feature_dict
    )


    print(
        "\nDeterministic rebuild: PASS"
    )

    print(
        "Timestamp leakage guards: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "ML FEATURE BUILDER "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    