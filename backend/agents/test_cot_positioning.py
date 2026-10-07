from datetime import (
    date,
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

from backend.agents.implementations.cot_positioning_agent import (
    COTPositioningAgent
)

from backend.institutional.cot_feature_builder import (
    COTFeatureBuilder
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot,
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)


def make_snapshot(
    index
):

    report_date = (
        date(
            2025,
            1,
            7
        )
        + timedelta(
            days=(
                index
                * 7
            )
        )
    )


    available_at = (
        datetime(
            2025,
            1,
            10,
            20,
            30,
            tzinfo=timezone.utc
        )
        + timedelta(
            days=(
                index
                * 7
            )
        )
    )


    return COTPositioningSnapshot(

        report_type=(
            COT_REPORT_TYPE_TFF_FUTURES_ONLY
        ),

        cftc_contract_market_code=(
            EURO_FX_CFTC_CONTRACT_MARKET_CODE
        ),

        market_and_exchange_names=(
            "EURO FX - "
            "CHICAGO MERCANTILE EXCHANGE"
        ),

        contract_market_name=(
            "EURO FX"
        ),

        commodity_name=(
            "EUROPEAN CURRENCY UNIT"
        ),

        report_date=
            report_date,

        available_at=
            available_at,

        open_interest=(
            1_000_000
            + (
                index
                * 1_000
            )
        ),

        dealer_long=(
            100_000
            + (
                index
                * 300
            )
        ),

        dealer_short=(
            300_000
            + (
                index
                * 50
            )
        ),

        dealer_spread=
            8_000,

        asset_manager_long=(
            400_000
            + (
                index
                * 1_500
            )
        ),

        asset_manager_short=(
            300_000
            + (
                index
                * 200
            )
        ),

        asset_manager_spread=
            40_000,

        leveraged_money_long=(
            200_000
            + (
                index
                * 500
            )
        ),

        leveraged_money_short=(
            250_000
            + (
                index
                * 100
            )
        ),

        leveraged_money_spread=
            20_000,

        other_reportables_long=
            28_000,

        other_reportables_short=
            20_000,

        other_reportables_spread=
            4_000,

        nonreportable_long=
            80_000,

        nonreportable_short=
            58_000,

        source_record_id=(
            f"seat22-{index}"
        ),

        content_hash=(
            f"{index + 1:064x}"
        ),

        source="CFTC",

        ingested_at=
            available_at
    )


def make_context(
    cot_data
):

    cot_data = tuple(
        cot_data
    )


    if cot_data:

        as_of = (
            cot_data[-1]
            .available_at
            + timedelta(
                minutes=1
            )
        )

    else:

        as_of = datetime(
            2026,
            1,
            1,
            tzinfo=timezone.utc
        )


    # Isolated unit context:
    #
    # model_construct avoids requiring
    # unrelated market-state fixtures.
    #
    # Production contexts remain fully
    # validated AgentContext instances.
    return AgentContext.model_construct(

        symbol="EURUSD",

        timeframe="M5",

        as_of=
            as_of,

        market_state=None,

        candles=tuple(),

        ticks=tuple(),

        cot_data=
            cot_data
    )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "SEAT 22 COT POSITIONING AGENT TEST"
    )

    print(
        "=" * 70
    )


    agent = (
        COTPositioningAgent()
    )


    # ==================================
    # METADATA
    # ==================================

    assert (
        agent.agent_id
        == "cot_positioning"
    )

    assert (
        agent.agent_name
        == (
            "COT / Institutional Positioning"
        )
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
        "\nSeat 22 metadata: PASS"
    )


    # ==================================
    # EMPTY COT HISTORY
    # ==================================

    empty_context = (
        make_context(
            tuple()
        )
    )


    empty_opinion = (
        agent.run(
            empty_context
        )
    )


    assert (
        empty_opinion.direction
        == AgentDirection.NEUTRAL
    )

    assert (
        empty_opinion.score
        == 0.0
    )

    assert (
        empty_opinion.confidence
        == 0.0
    )

    assert (
        empty_opinion.evidence[
            "cot_status"
        ]
        == "insufficient_history"
    )

    assert (
        empty_opinion.evidence[
            "report_count"
        ]
        == 0
    )


    print(
        "Missing-history neutral fallback: PASS"
    )


    # ==================================
    # INSUFFICIENT COT HISTORY
    # ==================================

    short_history = tuple(

        make_snapshot(
            index
        )

        for index
        in range(
            52
        )
    )


    short_context = (
        make_context(
            short_history
        )
    )


    short_opinion = (
        agent.run(
            short_context
        )
    )


    assert (
        short_opinion.direction
        == AgentDirection.NEUTRAL
    )

    assert (
        short_opinion.score
        == 0.0
    )

    assert (
        short_opinion.confidence
        == 0.0
    )

    assert (
        short_opinion.evidence[
            "report_count"
        ]
        == 52
    )


    print(
        "Insufficient-history neutral fallback: PASS"
    )


    # ==================================
    # FEATURE-READY HISTORY
    # ==================================

    history = tuple(

        make_snapshot(
            index
        )

        for index
        in range(
            53
        )
    )


    context = (
        make_context(
            history
        )
    )


    opinion = (
        agent.run(
            context
        )
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

    assert (
        opinion.signal_type
        == AgentSignalType.CONTEXTUAL
    )


    print(
        "Contextual neutrality: PASS"
    )


    assert (
        opinion.evidence[
            "cot_status"
        ]
        == "feature_ready"
    )

    assert (
        opinion.evidence[
            "directional_mapping_authorized"
        ]
        is False
    )

    assert (
        opinion.evidence[
            "report_count"
        ]
        == 53
    )

    assert (
        opinion.evidence[
            "feature_count"
        ]
        == 11
    )


    print(
        "Feature-ready evidence contract: PASS"
    )


    # ==================================
    # EXACT FEATURE PARITY
    # ==================================

    expected = (
        COTFeatureBuilder()
        .build(
            history
        )
        .as_dict()
    )


    for (
        feature_name,
        expected_value
    ) in expected.items():

        actual_value = (
            opinion.evidence[
                feature_name
            ]
        )


        assert abs(
            float(
                actual_value
            )
            - float(
                expected_value
            )
        ) < 1e-12


    print(
        "Frozen feature-builder parity: PASS"
    )


    assert (
        opinion.evidence[
            "current_report_date"
        ]
        == (
            history[-1]
            .report_date
            .isoformat()
        )
    )

    assert (
        opinion.evidence[
            "current_available_at"
        ]
        == (
            history[-1]
            .available_at
            .isoformat()
        )
    )


    print(
        "Current causal report provenance: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "SEAT 22 COT POSITIONING AGENT TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()