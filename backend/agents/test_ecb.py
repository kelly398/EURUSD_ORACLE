from datetime import (
    date,
    datetime,
    timezone
)

from backend.agents.contracts import (
    AgentCategory,
    AgentContext,
    AgentDirection,
    AgentSignalType
)

from backend.agents.implementations.ecb_agent import (
    ECBAgent
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
    EconomicCalendarEventSnapshot
)


INGESTED_AT = datetime(
    2026,
    10,
    8,
    0,
    0,
    tzinfo=timezone.utc
)


def make_rate(
    *,
    rate_name,
    value_percent,
    reference_date,
    available_at,
    key_suffix
):

    return CentralBankRateSnapshot(

        central_bank="ECB",

        rate_name=
            rate_name,

        reference_date=
            reference_date,

        available_at=
            available_at,

        value_percent=
            value_percent,

        source_record_id=(
            "FM.B.U2.EUR.4F.KR."
            + key_suffix
            + ".LEV|"
            + reference_date.isoformat()
        ),

        content_hash=(
            key_suffix[0].lower()
            * 64
        ),

        source="ECB_SDMX_FM",

        ingested_at=
            INGESTED_AT
    )


def make_policy_event(
    *,
    ordinal,
    scheduled_at
):

    return EconomicCalendarEventSnapshot(

        source_event_id=(
            "ECB|MONETARY_POLICY_DECISION|"
            "2026|"
            + f"{ordinal:02d}"
        ),

        event_name=(
            "ECB Monetary Policy Decision"
        ),

        jurisdiction="EURO_AREA",

        currency="EUR",

        scheduled_at=
            scheduled_at,

        available_at=datetime(
            2025,
            4,
            24,
            22,
            0,
            tzinfo=timezone.utc
        ),

        actual=None,

        forecast=None,

        previous=None,

        unit=None,

        content_hash=(
            f"{ordinal:x}"[-1]
            * 64
        ),

        source=(
            "ECB_GOVERNING_COUNCIL_CALENDAR"
        ),

        ingested_at=
            INGESTED_AT
    )


def make_context(
    *,
    as_of,
    ecb_data,
    economic_calendar
):

    # Isolated Seat 24 unit-test context.
    #
    # Production contexts pass through the
    # normal AgentContext validation layer.
    return AgentContext.model_construct(

        symbol="EURUSD",

        timeframe="M5",

        as_of=
            as_of,

        market_state=None,

        candles=tuple(),

        ticks=tuple(),

        cot_data=tuple(),

        ecb_data=tuple(
            ecb_data
        ),

        economic_calendar=tuple(
            economic_calendar
        )
    )


def assert_runtime_error(
    callable_
):

    try:

        callable_()

    except RuntimeError:

        return


    raise AssertionError(
        "Expected RuntimeError."
    )


def june_rates():

    reference_date = date(
        2026,
        6,
        17
    )

    available_at = datetime(
        2026,
        6,
        11,
        12,
        38,
        45,
        tzinfo=timezone.utc
    )


    return (

        make_rate(
            rate_name=(
                "deposit_facility_rate"
            ),
            value_percent=2.25,
            reference_date=
                reference_date,
            available_at=
                available_at,
            key_suffix="DFR"
        ),

        make_rate(
            rate_name=(
                "main_refinancing_operations_rate"
            ),
            value_percent=2.40,
            reference_date=
                reference_date,
            available_at=
                available_at,
            key_suffix="MRR_FR"
        ),

        make_rate(
            rate_name=(
                "marginal_lending_facility_rate"
            ),
            value_percent=2.65,
            reference_date=
                reference_date,
            available_at=
                available_at,
            key_suffix="MLFR"
        )
    )


def september_rates():

    reference_date = date(
        2026,
        9,
        16
    )

    available_at = datetime(
        2026,
        9,
        10,
        14,
        22,
        33,
        tzinfo=timezone.utc
    )


    return (

        make_rate(
            rate_name=(
                "deposit_facility_rate"
            ),
            value_percent=2.50,
            reference_date=
                reference_date,
            available_at=
                available_at,
            key_suffix="DFR"
        ),

        make_rate(
            rate_name=(
                "main_refinancing_operations_rate"
            ),
            value_percent=2.65,
            reference_date=
                reference_date,
            available_at=
                available_at,
            key_suffix="MRR_FR"
        ),

        make_rate(
            rate_name=(
                "marginal_lending_facility_rate"
            ),
            value_percent=2.90,
            reference_date=
                reference_date,
            available_at=
                available_at,
            key_suffix="MLFR"
        )
    )


def policy_calendar():

    return (

        make_policy_event(
            ordinal=4,
            scheduled_at=datetime(
                2026,
                6,
                11,
                12,
                15,
                tzinfo=timezone.utc
            )
        ),

        make_policy_event(
            ordinal=6,
            scheduled_at=datetime(
                2026,
                9,
                10,
                12,
                15,
                tzinfo=timezone.utc
            )
        ),

        make_policy_event(
            ordinal=7,
            scheduled_at=datetime(
                2026,
                10,
                29,
                13,
                15,
                tzinfo=timezone.utc
            )
        )
    )


def main():

    print("=" * 70)

    print(
        "EUR/USD ORACLE "
        "SEAT 24 ECB AGENT TEST"
    )

    print("=" * 70)


    agent = ECBAgent()


    # ========================================================
    # METADATA
    # ========================================================

    assert (
        agent.agent_id
        == "ecb"
    )

    assert (
        agent.agent_name
        == "ECB"
    )

    assert (
        agent.category
        == AgentCategory.MACRO
    )

    assert (
        agent.signal_type
        == AgentSignalType.CONTEXTUAL
    )


    print(
        "\nSeat 24 metadata: PASS"
    )


    # ========================================================
    # REQUIRED ECB RATE INPUT
    # ========================================================

    as_of = datetime(
        2026,
        9,
        10,
        11,
        15,
        tzinfo=timezone.utc
    )


    assert_runtime_error(

        lambda:
            agent.run(

                make_context(

                    as_of=
                        as_of,

                    ecb_data=
                        tuple(),

                    economic_calendar=
                        policy_calendar()
                )
            )
    )


    print(
        "ECB rate-data requirement: PASS"
    )


    # ========================================================
    # REQUIRED CALENDAR INPUT
    # ========================================================

    assert_runtime_error(

        lambda:
            agent.run(

                make_context(

                    as_of=
                        as_of,

                    ecb_data=
                        june_rates(),

                    economic_calendar=
                        tuple()
                )
            )
    )


    print(
        "ECB calendar requirement: PASS"
    )


    # ========================================================
    # PRE-DECISION CAUSAL STATE
    # ========================================================

    context = (
        make_context(

            as_of=
                as_of,

            ecb_data=
                june_rates(),

            economic_calendar=
                policy_calendar()
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
        == 1.0
    )


    print(
        "Contextual neutrality: PASS"
    )


    # ========================================================
    # CURRENT RATE STATE
    # ========================================================

    assert (
        opinion.evidence[
            "rate_state_complete"
        ]
        is True
    )

    assert (
        opinion.evidence[
            "deposit_facility_rate_percent"
        ]
        == 2.25
    )

    assert (
        opinion.evidence[
            "main_refinancing_rate_percent"
        ]
        == 2.40
    )

    assert (
        opinion.evidence[
            "marginal_lending_rate_percent"
        ]
        == 2.65
    )


    assert abs(

        opinion.evidence[
            "deposit_to_mro_spread_bps"
        ]

        - 15.0

    ) < 1e-12


    assert abs(

        opinion.evidence[
            "mro_to_marginal_spread_bps"
        ]

        - 25.0

    ) < 1e-12


    print(
        "Causal ECB rate-state summary: PASS"
    )


    # ========================================================
    # POLICY EVENT PROXIMITY
    # ========================================================

    assert (
        opinion.evidence[
            "previous_policy_event_id"
        ]
        == (
            "ECB|MONETARY_POLICY_DECISION|"
            "2026|04"
        )
    )


    assert (
        opinion.evidence[
            "next_policy_event_id"
        ]
        == (
            "ECB|MONETARY_POLICY_DECISION|"
            "2026|06"
        )
    )


    assert (
        opinion.evidence[
            "seconds_until_next_policy_event"
        ]
        == 3600.0
    )


    assert (
        opinion.evidence[
            "policy_event_window"
        ]
        == "within_1h"
    )


    assert (
        opinion.evidence[
            "policy_event_imminent"
        ]
        is True
    )


    print(
        "Policy-event proximity: PASS"
    )


    # ========================================================
    # CLAIM LIMITS
    # ========================================================

    assert (
        opinion.evidence[
            "directional_mapping_authorized"
        ]
        is False
    )


    assert (
        opinion.evidence[
            "relative_rate_differential_available"
        ]
        is False
    )


    print(
        "Seat 24 directional limits: PASS"
    )


    # ========================================================
    # PARTIAL RATE STATE FAILS CLOSED TO LOWER CONFIDENCE
    # ========================================================

    partial_context = (
        make_context(

            as_of=
                as_of,

            ecb_data=
                june_rates()[:2],

            economic_calendar=
                policy_calendar()
        )
    )


    partial_opinion = (
        agent.run(
            partial_context
        )
    )


    assert (
        partial_opinion.direction
        == AgentDirection.NEUTRAL
    )

    assert (
        partial_opinion.score
        == 0.0
    )

    assert (
        partial_opinion.evidence[
            "rate_state_complete"
        ]
        is False
    )

    assert (
        partial_opinion.evidence[
            "present_rate_count"
        ]
        == 2
    )

    assert (
        partial_opinion.evidence[
            "marginal_lending_rate_percent"
        ]
        is None
    )

    assert (
        partial_opinion.confidence
        < 1.0
    )

    assert (
        partial_opinion.confidence
        > 0.0
    )


    print(
        "Partial-rate confidence degradation: PASS"
    )


    # ========================================================
    # POST-PUBLICATION RATE STATE
    # ========================================================

    post_release_as_of = datetime(
        2026,
        9,
        10,
        15,
        0,
        tzinfo=timezone.utc
    )


    post_release_context = (
        make_context(

            as_of=
                post_release_as_of,

            ecb_data=(
                june_rates()
                + september_rates()
            ),

            economic_calendar=
                policy_calendar()
        )
    )


    post_release_opinion = (
        agent.run(
            post_release_context
        )
    )


    assert (
        post_release_opinion.evidence[
            "deposit_facility_rate_percent"
        ]
        == 2.50
    )

    assert (
        post_release_opinion.evidence[
            "main_refinancing_rate_percent"
        ]
        == 2.65
    )

    assert (
        post_release_opinion.evidence[
            "marginal_lending_rate_percent"
        ]
        == 2.90
    )


    assert (
        post_release_opinion.evidence[
            "deposit_facility_reference_date"
        ]
        == "2026-09-16"
    )


    assert (
        post_release_opinion.evidence[
            "previous_policy_event_id"
        ]
        == (
            "ECB|MONETARY_POLICY_DECISION|"
            "2026|06"
        )
    )


    assert (
        post_release_opinion.evidence[
            "next_policy_event_id"
        ]
        == (
            "ECB|MONETARY_POLICY_DECISION|"
            "2026|07"
        )
    )


    assert (
        post_release_opinion.direction
        == AgentDirection.NEUTRAL
    )

    assert (
        post_release_opinion.score
        == 0.0
    )


    print(
        "Post-publication causal state: PASS"
    )


    # ========================================================
    # NO ECB-SPECIFIC POLICY EVENTS
    # ========================================================

    foreign_event = (
        EconomicCalendarEventSnapshot(

            source_event_id="OTHER|EVENT|1",

            event_name="Other Event",

            jurisdiction="US",

            currency="USD",

            scheduled_at=datetime(
                2026,
                10,
                1,
                tzinfo=timezone.utc
            ),

            available_at=datetime(
                2026,
                1,
                1,
                tzinfo=timezone.utc
            ),

            actual=None,

            forecast=None,

            previous=None,

            unit=None,

            content_hash=(
                "f" * 64
            ),

            source="OTHER",

            ingested_at=
                INGESTED_AT
        )
    )


    assert_runtime_error(

        lambda:
            agent.run(

                make_context(

                    as_of=
                        as_of,

                    ecb_data=
                        june_rates(),

                    economic_calendar=(
                        foreign_event,
                    )
                )
            )
    )


    print(
        "ECB-specific calendar guard: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "SEAT 24 ECB AGENT TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()