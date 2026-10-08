from datetime import (
    date,
    datetime,
    timedelta,
    timezone
)

from types import (
    SimpleNamespace
)

from backend.agents.contracts import (
    AgentContext
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
    EconomicCalendarEventSnapshot
)

from backend.market_state.schemas import (
    DataQualityStatus
)

from backend.models.historical_replay import (
    HistoricalReplayBuilder
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64


AS_OF = datetime(
    2026,
    1,
    22,
    14,
    0,
    tzinfo=timezone.utc
)


def make_rate(
    *,
    source_record_id,
    reference_date,
    available_at,
    content_hash,
    value_percent,
    rate_name="deposit_facility_rate",
    central_bank="ECB"
):

    return CentralBankRateSnapshot(

        central_bank=
            central_bank,

        rate_name=
            rate_name,

        reference_date=
            reference_date,

        available_at=
            available_at,

        value_percent=
            value_percent,

        source_record_id=
            source_record_id,

        content_hash=
            content_hash,

        source="TEST",

        ingested_at=(
            available_at
            + timedelta(
                seconds=1
            )
        )
    )


def make_event(
    *,
    source_event_id,
    scheduled_at,
    available_at,
    content_hash,
    actual=None
):

    return EconomicCalendarEventSnapshot(

        source_event_id=
            source_event_id,

        event_name=(
            "ECB Monetary Policy Decision"
        ),

        jurisdiction="EURO_AREA",

        currency="EUR",

        scheduled_at=
            scheduled_at,

        available_at=
            available_at,

        actual=
            actual,

        forecast=None,

        previous=None,

        unit=(
            "percent"
            if actual is not None
            else None
        ),

        content_hash=
            content_hash,

        source="TEST",

        ingested_at=(
            available_at
            + timedelta(
                seconds=1
            )
        )
    )


def validate_context(
    *,
    ecb_data=tuple(),
    economic_calendar=tuple()
):

    market_state = SimpleNamespace(

        symbol="EURUSD",

        timeframe="M5",

        as_of=AS_OF,

        data_quality=(
            DataQualityStatus.PASS
        )
    )


    candle = SimpleNamespace(

        symbol="EURUSD",

        timeframe="M5",

        timestamp=(
            AS_OF
            - timedelta(
                minutes=5
            )
        )
    )


    context = (
        AgentContext.model_construct(

            symbol="EURUSD",

            timeframe="M5",

            as_of=AS_OF,

            market_state=
                market_state,

            candles=(
                candle,
            ),

            ticks=tuple(),

            cot_data=tuple(),

            ecb_data=tuple(
                ecb_data
            ),

            economic_calendar=tuple(
                economic_calendar
            )
        )
    )


    return (
        context.validate_context()
    )


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
        "MACRO CONTEXT / REPLAY TEST"
    )

    print("=" * 70)


    builder = (
        HistoricalReplayBuilder()
    )


    # ========================================================
    # ECB REVISION-AWARE AS-OF LOCK
    # ========================================================

    old_rate = make_rate(

        source_record_id=
            "ecb-deposit-2026-01-22",

        reference_date=date(
            2026,
            1,
            22
        ),

        available_at=datetime(
            2026,
            1,
            22,
            12,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=HASH_A,

        value_percent=2.25
    )


    revised_rate = make_rate(

        source_record_id=
            "ecb-deposit-2026-01-22",

        reference_date=date(
            2026,
            1,
            22
        ),

        available_at=datetime(
            2026,
            1,
            22,
            13,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=HASH_B,

        value_percent=2.00
    )


    future_rate_revision = make_rate(

        source_record_id=
            "ecb-deposit-2026-01-22",

        reference_date=date(
            2026,
            1,
            22
        ),

        available_at=datetime(
            2026,
            1,
            22,
            15,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=HASH_C,

        value_percent=1.90
    )


    earlier_mro = make_rate(

        source_record_id=
            "ecb-mro-2026-01-15",

        reference_date=date(
            2026,
            1,
            15
        ),

        available_at=datetime(
            2026,
            1,
            15,
            13,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=HASH_D,

        value_percent=2.15,

        rate_name=(
            "main_refinancing_operations_rate"
        )
    )


    locked_ecb = (
        builder._lock_ecb_data_as_of(

            ecb_data=(

                future_rate_revision,
                revised_rate,
                earlier_mro,
                old_rate
            ),

            as_of=
                AS_OF
        )
    )


    assert (
        len(
            locked_ecb
        )
        == 2
    )

    assert (
        locked_ecb[0]
        == earlier_mro
    )

    assert (
        locked_ecb[1]
        == revised_rate
    )

    assert (
        future_rate_revision
        not in locked_ecb
    )


    print(
        "\nECB future revision exclusion: PASS"
    )

    print(
        "ECB newest causal revision: PASS"
    )

    print(
        "ECB deterministic ordering: PASS"
    )


    # ========================================================
    # ECB AMBIGUOUS REVISION
    # ========================================================

    ambiguous_rate = make_rate(

        source_record_id=
            revised_rate.source_record_id,

        reference_date=
            revised_rate.reference_date,

        available_at=
            revised_rate.available_at,

        content_hash=HASH_C,

        value_percent=1.95
    )


    assert_value_error(

        lambda:
            builder
            ._lock_ecb_data_as_of(

                ecb_data=(
                    revised_rate,
                    ambiguous_rate
                ),

                as_of=
                    AS_OF
            )
    )


    print(
        "ECB ambiguous revision guard: PASS"
    )


    # ========================================================
    # WRONG CENTRAL BANK
    # ========================================================

    fed_rate = make_rate(

        source_record_id=
            "fed-test",

        reference_date=date(
            2026,
            1,
            22
        ),

        available_at=datetime(
            2026,
            1,
            22,
            13,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=HASH_A,

        value_percent=4.0,

        central_bank="FED"
    )


    assert_value_error(

        lambda:
            builder
            ._lock_ecb_data_as_of(

                ecb_data=(
                    fed_rate,
                ),

                as_of=
                    AS_OF
            )
    )


    print(
        "ECB identity guard: PASS"
    )


    # ========================================================
    # ECONOMIC CALENDAR REVISION LOCK
    # ========================================================

    initial_event = make_event(

        source_event_id=
            "ecb-policy-2026-01-22",

        scheduled_at=datetime(
            2026,
            1,
            22,
            13,
            15,
            tzinfo=timezone.utc
        ),

        available_at=datetime(
            2026,
            1,
            10,
            12,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=HASH_A
    )


    released_event = make_event(

        source_event_id=
            "ecb-policy-2026-01-22",

        scheduled_at=
            initial_event.scheduled_at,

        available_at=datetime(
            2026,
            1,
            22,
            13,
            15,
            5,
            tzinfo=timezone.utc
        ),

        content_hash=HASH_B,

        actual=2.0
    )


    future_event_revision = make_event(

        source_event_id=
            "ecb-policy-2026-01-22",

        scheduled_at=
            initial_event.scheduled_at,

        available_at=datetime(
            2026,
            1,
            23,
            9,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=HASH_C,

        actual=1.95
    )


    future_scheduled_event = make_event(

        source_event_id=
            "ecb-policy-2026-03-05",

        scheduled_at=datetime(
            2026,
            3,
            5,
            13,
            15,
            tzinfo=timezone.utc
        ),

        available_at=datetime(
            2026,
            1,
            5,
            9,
            0,
            tzinfo=timezone.utc
        ),

        content_hash=HASH_D
    )


    locked_calendar = (
        builder
        ._lock_economic_calendar_as_of(

            economic_calendar=(

                future_scheduled_event,
                future_event_revision,
                initial_event,
                released_event
            ),

            as_of=
                AS_OF
        )
    )


    assert (
        len(
            locked_calendar
        )
        == 2
    )

    assert (
        locked_calendar[0]
        == released_event
    )

    assert (
        locked_calendar[1]
        == future_scheduled_event
    )

    assert (
        future_event_revision
        not in locked_calendar
    )


    print(
        "Calendar future revision exclusion: PASS"
    )

    print(
        "Calendar newest causal revision: PASS"
    )

    print(
        "Future scheduled-event visibility: PASS"
    )

    print(
        "Calendar deterministic ordering: PASS"
    )


    # ========================================================
    # CALENDAR AMBIGUOUS REVISION
    # ========================================================

    ambiguous_event = make_event(

        source_event_id=
            released_event.source_event_id,

        scheduled_at=
            released_event.scheduled_at,

        available_at=
            released_event.available_at,

        content_hash=HASH_C,

        actual=1.90
    )


    assert_value_error(

        lambda:
            builder
            ._lock_economic_calendar_as_of(

                economic_calendar=(
                    released_event,
                    ambiguous_event
                ),

                as_of=
                    AS_OF
            )
    )


    print(
        "Calendar ambiguous revision guard: PASS"
    )


    # ========================================================
    # AGENT CONTEXT CONTRACT
    # ========================================================

    validated = (
        validate_context(

            ecb_data=
                locked_ecb,

            economic_calendar=
                locked_calendar
        )
    )


    assert (
        validated.ecb_data
        == locked_ecb
    )

    assert (
        validated.economic_calendar
        == locked_calendar
    )


    print(
        "Macro AgentContext contract: PASS"
    )


    assert_value_error(

        lambda:
            validate_context(

                ecb_data=(
                    future_rate_revision,
                )
            )
    )


    print(
        "Context ECB anti-lookahead: PASS"
    )


    assert_value_error(

        lambda:
            validate_context(

                economic_calendar=(
                    future_event_revision,
                )
            )
    )


    print(
        "Context calendar anti-lookahead: PASS"
    )


    # A future scheduled event remains valid
    # because this exact calendar version was
    # already public before AS_OF.

    future_event_context = (
        validate_context(

            economic_calendar=(
                future_scheduled_event,
            )
        )
    )


    assert (
        future_event_context
        .economic_calendar[0]
        == future_scheduled_event
    )


    print(
        "Context future-event visibility: PASS"
    )


    # Multiple versions of one logical object
    # must never coexist in a final context.

    assert_value_error(

        lambda:
            validate_context(

                ecb_data=(
                    old_rate,
                    revised_rate
                )
            )
    )


    assert_value_error(

        lambda:
            validate_context(

                economic_calendar=(
                    initial_event,
                    released_event
                )
            )
    )


    print(
        "Context single-version contract: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "MACRO CONTEXT / REPLAY TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()