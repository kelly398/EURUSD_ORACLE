from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.agents.contracts import (
    AgentCategory,
    AgentContext,
    AgentDirection,
    AgentSignalType
)

from backend.agents.dealer_behaviour_features import (
    DealerBehaviourFeatureEngine
)

from backend.agents.implementations.dealer_behaviour_agent import (
    DealerBehaviourAgent
)

from backend.market_state.schemas import (
    TickSample
)


def make_tick(
    *,
    timestamp,
    bid,
    ask,
    source_time_msc
):

    return TickSample(

        symbol="EURUSD",

        timestamp=
            timestamp,

        source_time_msc=
            source_time_msc,

        bid=
            bid,

        ask=
            ask,

        spread_pips=(
            (
                ask
                - bid
            )
            * 10000.0
        )
    )


def make_ticks(
    as_of
):

    baseline_start = (
        as_of
        - timedelta(
            minutes=35
        )
    )


    baseline = tuple(

        make_tick(

            timestamp=(
                baseline_start
                + timedelta(
                    minutes=index
                )
            ),

            bid=1.1000,

            ask=1.1002,

            source_time_msc=
                index
        )

        for index
        in range(
            30
        )
    )


    recent_start = (
        as_of
        - timedelta(
            minutes=5
        )
    )


    recent = (

        make_tick(
            timestamp=
                recent_start,
            bid=1.1000,
            ask=1.1002,
            source_time_msc=100
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=30
                )
            ),
            bid=1.1001,
            ask=1.1002,
            source_time_msc=101
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=60
                )
            ),
            bid=1.1001,
            ask=1.1003,
            source_time_msc=102
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=90
                )
            ),
            bid=1.1002,
            ask=1.1004,
            source_time_msc=103
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=120
                )
            ),
            bid=1.1001,
            ask=1.1003,
            source_time_msc=104
        ),

        make_tick(
            timestamp=(
                recent_start
                + timedelta(
                    seconds=150
                )
            ),
            bid=1.1002,
            ask=1.1002,
            source_time_msc=105
        )
    )


    return (
        baseline
        + recent
    )


def make_context(
    *,
    as_of,
    ticks
):

    # Isolated unit-test context.
    #
    # Dealer Behaviour v1 consumes raw
    # ticks and the validated context
    # timestamp. Production contexts use
    # the normal fully validated
    # AgentContext constructor.
    return AgentContext.model_construct(

        symbol="EURUSD",

        timeframe="M5",

        as_of=
            as_of,

        market_state=None,

        candles=tuple(),

        ticks=tuple(
            ticks
        ),

        cot_data=tuple()
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "SEAT 23 DEALER BEHAVIOUR AGENT TEST"
    )

    print(
        "=" * 70
    )


    agent = (
        DealerBehaviourAgent()
    )


    # ==================================
    # METADATA
    # ==================================

    assert (
        agent.agent_id
        == "dealer_behaviour"
    )

    assert (
        agent.agent_name
        == "Dealer Behaviour"
    )

    assert (
        agent.category
        == AgentCategory.INSTITUTIONAL
    )

    assert (
        agent.signal_type
        == AgentSignalType.CONTEXTUAL
    )


    print(
        "\nSeat 23 metadata: PASS"
    )


    # ==================================
    # REQUIRED RAW TICKS
    # ==================================

    as_of = datetime(
        2026,
        1,
        5,
        12,
        10,
        tzinfo=timezone.utc
    )


    empty_context = (
        make_context(
            as_of=as_of,
            ticks=tuple()
        )
    )


    try:

        agent.run(
            empty_context
        )

    except RuntimeError:

        pass

    else:

        raise AssertionError(
            "Dealer Behaviour accepted "
            "missing raw ticks."
        )


    print(
        "Raw-tick requirement: PASS"
    )


    # ==================================
    # FEATURE-READY CONTEXT
    # ==================================

    ticks = (
        make_ticks(
            as_of
        )
    )


    context = (
        make_context(
            as_of=as_of,
            ticks=ticks
        )
    )


    opinion = (
        agent.run(
            context
        )
    )


    assert (
        opinion.signal_type
        == AgentSignalType.CONTEXTUAL
    )

    assert (
        opinion.direction
        == AgentDirection.NEUTRAL
    )

    assert (
        opinion.score
        == 0.0
    )

    assert (
        opinion.confidence
        == 0.0
    )


    print(
        "Contextual neutrality: PASS"
    )


    # ==================================
    # PROVENANCE / CLAIM LIMITS
    # ==================================

    assert (
        opinion.evidence[
            "dealer_inventory_observed"
        ]
        is False
    )

    assert (
        opinion.evidence[
            "dealer_identity_observed"
        ]
        is False
    )

    assert (
        opinion.evidence[
            "dealer_trades_observed"
        ]
        is False
    )

    assert (
        opinion.evidence[
            "centralized_order_book"
        ]
        is False
    )

    assert (
        opinion.evidence[
            "broker_quote_proxy"
        ]
        is True
    )

    assert (
        opinion.evidence[
            "directional_mapping_authorized"
        ]
        is False
    )


    print(
        "Dealer-provenance limits: PASS"
    )


    # ==================================
    # EXACT FEATURE PARITY
    # ==================================

    expected = (
        DealerBehaviourFeatureEngine()
        .analyze(

            ticks=
                tuple(
                    ticks
                ),

            as_of=
                as_of
        )
    )


    fields = (

        "recent_tick_count",
        "baseline_tick_count",
        "tick_activity_ratio",
        "average_spread_pips",
        "baseline_spread_pips",
        "spread_ratio",

        "bid_up_updates",
        "bid_down_updates",
        "bid_directional_updates",
        "bid_update_imbalance",

        "ask_up_updates",
        "ask_down_updates",
        "ask_directional_updates",
        "ask_update_imbalance",

        "side_update_asymmetry",

        "quote_change_updates",
        "one_sided_updates",
        "synchronized_updates",
        "opposed_updates",

        "one_sided_update_ratio",
        "synchronized_update_ratio",
        "opposed_update_ratio",

        "spread_widen_updates",
        "spread_narrow_updates",
        "spread_directional_updates",
        "spread_update_imbalance",

        "net_spread_change_pips",
        "mean_absolute_spread_change_pips"
    )


    for field_name in fields:

        expected_value = getattr(
            expected,
            field_name
        )

        actual_value = (
            opinion.evidence[
                field_name
            ]
        )


        if isinstance(
            expected_value,
            float
        ):

            assert abs(
                float(
                    actual_value
                )
                - expected_value
            ) < 1e-12

        else:

            assert (
                actual_value
                == expected_value
            )


    print(
        "Frozen feature-engine parity: PASS"
    )


    # ==================================
    # EXPECTED FIXTURE STRUCTURE
    # ==================================

    assert (
        opinion.evidence[
            "one_sided_updates"
        ]
        == 2
    )

    assert (
        opinion.evidence[
            "synchronized_updates"
        ]
        == 2
    )

    assert (
        opinion.evidence[
            "opposed_updates"
        ]
        == 1
    )

    assert abs(
        opinion.evidence[
            "side_update_asymmetry"
        ]
        - 0.0
    ) < 1e-12


    print(
        "Quote-management evidence: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "SEAT 23 DEALER BEHAVIOUR AGENT TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()