from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.agents.agent_registry import (
    AgentRegistry
)

from backend.agents.council_runner import (
    CouncilRunner
)

from backend.agents.register_core_agents import (
    register_core_agents
)

from backend.market_state.candle_repository import (
    CandleRepository
)

from backend.market_state.tick_repository import (
    TickRepository
)

from backend.models.historical_replay import (
    HistoricalReplayBuilder
)


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "HISTORICAL CORE COUNCIL TEST"
    )

    print(
        "=" * 70
    )


    # ==================================
    # FIXED HISTORICAL SNAPSHOT
    # ==================================

    anchor = datetime(
        2026,
        9,
        30,
        12,
        0,
        tzinfo=timezone.utc
    )


    as_of = (
        anchor
        + timedelta(
            minutes=5
        )
    )


    print(
        f"\nHistorical anchor: "
        f"{anchor}"
    )


    # ==================================
    # LOAD HISTORICAL MARKET DATA
    # ==================================

    candle_repository = (
        CandleRepository(
            source="MT5"
        )
    )


    tick_repository = (
        TickRepository()
    )


    candles = (
        candle_repository
        .get_latest_as_of(

            latest_timestamp=
                anchor,

            symbol=
                "EURUSD",

            timeframe=
                "M5",

            limit=
                200
        )
    )


    ticks = (
        tick_repository
        .get_between(

            start=(
                as_of
                - timedelta(
                    minutes=65
                )
            ),

            end=
                as_of,

            symbol=
                "EURUSD"
        )
    )


    print(
        f"Candles loaded: "
        f"{len(candles)}"
    )

    print(
        f"Ticks loaded: "
        f"{len(ticks)}"
    )


    assert (
        len(candles)
        == 200
    )


    assert (
        len(ticks)
        > 0
    )


    # ==================================
    # BUILD HISTORICAL AGENT CONTEXT
    # ==================================

    replay_builder = (
        HistoricalReplayBuilder(

            symbol="EURUSD",

            timeframe="M5",

            timeframe_minutes=5,

            candle_limit=200,

            tick_lookback_minutes=65
        )
    )


    replay = (
        replay_builder.build(

            candles=
                candles,

            ticks=
                ticks,

            anchor_timestamp=
                anchor
        )
    )


    context = (
        replay.context
    )


    print(
        f"Replay as-of: "
        f"{context.as_of}"
    )

    print(
        f"Replay candles: "
        f"{replay.candle_count}"
    )

    print(
        f"Replay ticks: "
        f"{replay.tick_count}"
    )


    # ==================================
    # REGISTER REAL COUNCIL AGENTS
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


    assert (
        registry.implemented_count
        == 22
    )


    assert (
        len(
            registry.missing_agent_ids
        )
        == 9
    )


    assert (
        registry.is_registered(
            "ml_tree_ensemble"
        )
    )
    assert (
        registry.is_registered(
            "neural_network"
        )
    )
    assert (
        registry.is_registered(
            "hidden_markov_model"
        )
    )


    # ==================================
    # RUN REAL PRIMARY COUNCIL
    # ==================================

    runner = (
        CouncilRunner(

            registry=registry,

            max_workers=4
        )
    )


    result = (
        runner.run_primary(

            context=context,

            require_all_registered=False
        )
    )


    print(
        f"\nSuccessful agents: "
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
        "HISTORICAL COUNCIL EXECUTIONS"
    )

    print(
        "-" * 70
    )


    for execution in result.executions:

        print(
            f"{execution.seat_number:02d} | "
            f"{execution.agent_name:<28} | "
            f"{execution.status.value:<7} | "
            f"{execution.duration_ms:9.2f} ms"
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
                f"direction="
                f"{opinion.direction.value} "
                f"score="
                f"{opinion.score:.4f} "
                f"confidence="
                f"{opinion.confidence:.4f}"
            )


        if (
            execution.error
            is not None
        ):

            print(
                "     "
                f"ERROR: "
                f"{execution.error}"
            )


    # ==================================
    # COUNCIL CONTRACT
    # ==================================

    assert (
        result.succeeded_count
        == 22
    )


    assert (
        result.failed_count
        == 0
    )


    assert (
        result.missing_agent_ids
        == (
            "dealer_behaviour",
            "ecb",
            "fed",
            "rate_differential",
            "economic_calendar",
            "risk_sentiment",
        )
    )


    # CouncilRunner must restore
    # deterministic seat order after
    # parallel execution.
    seat_numbers = [

        execution.seat_number

        for execution
        in result.executions
    ]

    assert (
        seat_numbers
        == list(
            range(
                1,
                23,
            )
        )
    )


    # ==================================
    # OPINION CONTRACT
    # ==================================

    assert (
        len(
            result.opinions
        )
        == 22
    )


    for opinion in result.opinions:

        assert (
            opinion.as_of
            == context.as_of
        )


    opinions_by_id = {
        opinion.agent_id: opinion
        for opinion in result.opinions
    }

    hmm_opinion = opinions_by_id["hidden_markov_model"]

    assert (
        hmm_opinion.signal_type.value
        == "contextual"
    )

    assert (
        hmm_opinion.direction.value
        == "neutral"
    )

    assert (
        hmm_opinion.score
        == 0.0
    )

    assert (
        hmm_opinion.evidence["regime_available"]
        is True
    )

    assert (
        0.0
        <= hmm_opinion.confidence
        <= 1.0
    )

    assert (
        0.0
        <= hmm_opinion.evidence["switch_probability"]
        <= 1.0
    )

    state_probability_sum = sum(
        hmm_opinion.evidence[f"state_{state_index}_probability"]
        for state_index in range(5)
    )

    assert (
        abs(
            state_probability_sum
            - 1.0
        )
        < 0.00001
    )


    assert (
        "ml_tree_ensemble"
        in opinions_by_id
    )

    assert (
        "hidden_markov_model"
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


    probability_sum = (

        ml_opinion.evidence[
            "short_probability"
        ]

        + ml_opinion.evidence[
            "neutral_probability"
        ]

        + ml_opinion.evidence[
            "long_probability"
        ]
    )


    assert (
        abs(
            probability_sum
            - 1.0
        )
        < 0.00001
    )

    # ==================================
    # SEAT 12 NEURAL CONTRACT
    # ==================================

    assert (
        "neural_network"
        in opinions_by_id
    )


    neural_opinion = (
        opinions_by_id[
            "neural_network"
        ]
    )


    assert (
        neural_opinion.evidence[
            "feature_count"
        ]
        == 27
    )


    assert (
        neural_opinion.evidence[
            "feature_version"
        ]
        == "eurusd_m5_v1"
    )


    assert (
        neural_opinion.evidence[
            "member_count"
        ]
        == 3
    )


    neural_probability_sum = (

        neural_opinion.evidence[
            "short_probability"
        ]

        + neural_opinion.evidence[
            "neutral_probability"
        ]

        + neural_opinion.evidence[
            "long_probability"
        ]
    )


    assert (
        abs(
            neural_probability_sum
            - 1.0
        )
        < 0.00001
    )


    assert (
        0.0
        <= neural_opinion.evidence[
            "ensemble_disagreement"
        ]
        <= 1.0
    )


    assert (
        neural_opinion.confidence
        == neural_opinion.evidence[
            "ensemble_adjusted_confidence"
        ]
    )


    assert (
        neural_opinion.evidence[
            "member_vote_agreement"
        ]
        in (
            1.0 / 3.0,
            2.0 / 3.0,
            1.0,
        )
    )


    # ==================================
    # ANTI-LOOKAHEAD
    # ==================================

    assert (
        context.candles[-1].timestamp
        == anchor
    )


    assert (
        context.candles[-1].timestamp
        < context.as_of
    )


    assert (
        context.ticks[-1].timestamp
        < context.as_of
    )


    print(
        "\nCouncil registration: PASS"
    )

    print(
        "Parallel Council execution: PASS"
    )

    print(
        "Deterministic seat ordering: PASS"
    )

    print(
        "Seat 11 Council integration: PASS"
    )

    print(
        "Seat 12 Council integration: PASS"
    )

    print(
        "Seat 13 hidden markov model: PASS"
    )

    print(
        "Historical anti-lookahead: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "HISTORICAL CORE COUNCIL "
        "TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()
    