from backend.agents.agent_registry import (
    AgentRegistry
)

from backend.agents.council_runner import (
    CouncilRunner
)

from backend.agents.register_core_agents import (
    register_core_agents
)

from backend.market_state.market_state_engine import (
    MarketStateEngine
)

from backend.agents.contracts import (
    AgentSignalType
)

from backend.agents.context_factory import (
    AgentContextFactory
)


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "CORE MARKET AGENTS TEST"
    )

    print(
        "=" * 70
    )


    # ==================================
    # SYNCHRONIZED MARKET STATE
    # ==================================

    market_engine = (
        MarketStateEngine(

            symbol="EURUSD",

            timeframe="M5",

            timeframe_minutes=5,

            candle_limit=200
        )
    )


    unified = (
        market_engine
        .build_snapshot()
    )


    snapshot = (
        unified.snapshot
    )



    # ==================================
    # SNAPSHOT-LOCKED AGENT CONTEXT
    # ==================================

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


    print(
        f"\nContext candles: "
        f"{len(context.candles)}"
    )


    print(
        f"Context ticks: "
        f"{len(context.ticks)}"
    )


    # ==================================
    # REGISTER REAL AGENTS
    # ==================================

    registry = (
        AgentRegistry()
    )


    register_core_agents(
        registry
    )


    print(
        f"\nImplemented Council agents: "
        f"{registry.implemented_count}"
    )


    print(
        f"Remaining Council agents: "
        f"{len(registry.missing_agent_ids)}"
    )


    # ==================================
    # RUN REAL PRIMARY AGENTS
    # ==================================

    runner = CouncilRunner(

        registry=registry,

        max_workers=4
    )


    result = (
        runner.run_primary(

            context=context,

            require_all_registered=False
        )
    )


    print(
        f"\nSnapshot as-of: "
        f"{context.as_of}"
    )


    print(
        f"Successful agents: "
        f"{result.succeeded_count}"
    )


    print(
        f"Failed agents: "
        f"{result.failed_count}"
    )


    print(
        f"Missing primary agents: "
        f"{len(result.missing_agent_ids)}"
    )


    print(
        "\n"
        + "-" * 70
    )

    print(
        "REAL COUNCIL OPINIONS"
    )

    print(
        "-" * 70
    )


    for execution in (
        result.executions
    ):

        print(
            f"{execution.seat_number:02d} | "
            f"{execution.agent_name:<20} | "
            f"{execution.status.value:<7}"
        )


        if (
            execution.opinion
            is not None
        ):

            opinion = (
                execution.opinion
            )

            print(
                "     "
                f"signal_type="
                f"{opinion.signal_type.value}"
            )


            print(
                "     "
                f"direction="
                f"{opinion.direction.value}"
            )


            print(
                "     "
                f"score="
                f"{opinion.score:.4f}"
            )


            print(
                "     "
                f"confidence="
                f"{opinion.confidence:.4f}"
            )


            print(
                "     "
                f"rationale="
                f"{opinion.rationale}"
            )


        if (
            execution.error
            is not None
        ):

            print(
                "     "
                f"error="
                f"{execution.error}"
            )


    # ==================================
    # ASSERTIONS
    # ==================================

    assert (
        result.succeeded_count
        == 19
    )


    assert (
        result.failed_count
        == 0
    )


    assert (
        len(
            result.missing_agent_ids
        )
        == 9
    )


    seat_numbers = [

        execution.seat_number

        for execution
        in result.executions
    ]


    assert (
        seat_numbers
        == [
            1,
            2,
            3,
            4,
            5,
            6,
            7,
            8,
            9,
            10,
            11,
            14,
            15,
            16,
            17,
            18,
            19,
            20,
            21
        ]
    )

    assert (
        len(
            context.ticks
        )
        > 0
    )


    assert (
        context.ticks[-1].timestamp
        < context.as_of
    )


    for opinion in (
        result.opinions
    ):

        assert (
            opinion.as_of
            == snapshot.as_of
        )


    opinions_by_id = {

        opinion.agent_id:
            opinion

        for opinion
        in result.opinions
    }

    assert (
        "ml_tree_ensemble"
        in opinions_by_id
    )

    ml_opinion = (
        opinions_by_id[
            "ml_tree_ensemble"
        ]
    )

    assert (
        ml_opinion.evidence[
            "feature_count"
        ]
        == 27
    )

    assert (
        ml_opinion.evidence[
            "feature_version"
        ]
        == "eurusd_m5_v1"
    )

    london_opinion = (
        opinions_by_id[
            "london_session"
        ]
    )

    new_york_opinion = (
        opinions_by_id[
            "new_york_session"
        ]
    )

    assert (
        london_opinion.as_of
        == context.as_of
    )

    assert (
        new_york_opinion.as_of
        == context.as_of
    )


    assert (
        london_opinion.evidence[
            "session"
        ]
        == "london"
    )


    assert (
        new_york_opinion.evidence[
            "session"
        ]
        == "new_york"
    )


    # Volatility must remain
    # non-directional.
    assert (
        opinions_by_id[
            "volatility"
        ].score
        == 0.0
    )


    assert (
        opinions_by_id[
            "volatility"
        ].direction.value
        == "neutral"
    )

    assert (
        opinions_by_id[
            "volatility"
        ].signal_type
        == AgentSignalType.CONTEXTUAL
    )


    assert (
        opinions_by_id[
            "anomaly_detection"
        ].signal_type
        == AgentSignalType.CONTEXTUAL
    )


    assert (
        opinions_by_id[
            "trend_master"
        ].signal_type
        == AgentSignalType.DIRECTIONAL
    )


    assert (
        opinions_by_id[
            "mean_reversion"
        ].signal_type
        == AgentSignalType.DIRECTIONAL
    )

        # Anomaly Detection is also
    # contextual rather than directional.
    assert (
        opinions_by_id[
            "anomaly_detection"
        ].score
        == 0.0
    )


    assert (
        opinions_by_id[
            "anomaly_detection"
        ].direction.value
        == "neutral"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "CORE MARKET AGENTS "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    