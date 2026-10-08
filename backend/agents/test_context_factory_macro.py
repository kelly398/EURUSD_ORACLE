from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.agents.context_factory import (
    AgentContextFactory
)

from backend.macro.ecb_rate_protocol import (
    ECB_RATE_SOURCE
)

from backend.macro.ecb_calendar_protocol import (
    ECB_CALENDAR_SOURCE,
    ECB_POLICY_EVENT_CURRENCY,
    ECB_POLICY_EVENT_JURISDICTION
)


AS_OF = datetime(
    2026,
    9,
    30,
    12,
    5,
    tzinfo=timezone.utc
)


class FakeRateRepository:

    def __init__(
        self
    ):

        self.calls = []


    def get_history_as_of(
        self,
        *,
        as_of,
        limit
    ):

        self.calls.append(
            {
                "as_of":
                    as_of,

                "limit":
                    limit
            }
        )


        return [
            "rate-a",
            "rate-b"
        ]


class FakeCalendarRepository:

    def __init__(
        self
    ):

        self.calls = []


    def get_window_as_of(
        self,
        *,
        as_of,
        scheduled_start,
        scheduled_end
    ):

        self.calls.append(
            {
                "as_of":
                    as_of,

                "scheduled_start":
                    scheduled_start,

                "scheduled_end":
                    scheduled_end
            }
        )


        return [
            "event-a",
            "event-b"
        ]


def assert_value_error(
    callable_
):

    try:

        callable_()

    except ValueError:

        return


    raise AssertionError(
        "Expected ValueError."
    )


def main():

    print("=" * 70)

    print(
        "EUR/USD ORACLE "
        "CONTEXT FACTORY MACRO WIRING TEST"
    )

    print("=" * 70)


    # ==================================
    # DEFAULT PRODUCTION REPOSITORIES
    # ==================================

    default_factory = (
        AgentContextFactory(
            require_ticks=False
        )
    )


    assert (
        default_factory
        .ecb_rate_repository
        .source
        == ECB_RATE_SOURCE
    )

    assert (
        default_factory
        .ecb_rate_repository
        .central_bank
        == "ECB"
    )


    print(
        "\nDefault ECB repository wiring: PASS"
    )


    assert (
        default_factory
        .economic_calendar_repository
        .source
        == ECB_CALENDAR_SOURCE
    )

    assert (
        default_factory
        .economic_calendar_repository
        .currency
        == ECB_POLICY_EVENT_CURRENCY
    )

    assert (
        default_factory
        .economic_calendar_repository
        .jurisdiction
        == ECB_POLICY_EVENT_JURISDICTION
    )


    print(
        "Default calendar repository wiring: PASS"
    )


    # ==================================
    # DETERMINISTIC REPOSITORY INJECTION
    # ==================================

    rate_repository = (
        FakeRateRepository()
    )

    calendar_repository = (
        FakeCalendarRepository()
    )


    factory = (
        AgentContextFactory(

            require_ticks=False,

            ecb_history_limit=17,

            macro_calendar_lookback_days=30,

            macro_calendar_lookahead_days=45,

            ecb_rate_repository=
                rate_repository,

            economic_calendar_repository=
                calendar_repository
        )
    )


    (
        ecb_data,
        economic_calendar
    ) = factory._load_macro_context(
        AS_OF
    )


    assert (
        rate_repository.calls
        == [
            {
                "as_of":
                    AS_OF,

                "limit":
                    17
            }
        ]
    )


    print(
        "Causal ECB query propagation: PASS"
    )


    assert (
        calendar_repository.calls
        == [
            {
                "as_of":
                    AS_OF,

                "scheduled_start": (
                    AS_OF
                    - timedelta(
                        days=30
                    )
                ),

                "scheduled_end": (
                    AS_OF
                    + timedelta(
                        days=45
                    )
                )
            }
        ]
    )


    print(
        "Calendar window propagation: PASS"
    )


    assert (
        ecb_data
        == (
            "rate-a",
            "rate-b"
        )
    )

    assert (
        economic_calendar
        == (
            "event-a",
            "event-b"
        )
    )


    print(
        "Repository result tuple normalization: PASS"
    )


    # ==================================
    # CONFIGURATION GUARDS
    # ==================================

    assert_value_error(

        lambda:
            AgentContextFactory(
                ecb_history_limit=0
            )
    )


    assert_value_error(

        lambda:
            AgentContextFactory(
                macro_calendar_lookback_days=0
            )
    )


    assert_value_error(

        lambda:
            AgentContextFactory(
                macro_calendar_lookahead_days=0
            )
    )


    print(
        "Macro configuration guards: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "CONTEXT FACTORY MACRO WIRING TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()