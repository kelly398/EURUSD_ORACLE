from datetime import (
    date,
    datetime,
    timedelta,
    timezone
)

from sqlalchemy import (
    delete
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    CentralBankRate,
    EconomicCalendarEvent
)

from backend.macro.macro_repository import (
    CentralBankRateRepository,
    EconomicCalendarRepository
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
    EconomicCalendarEventSnapshot
)

from backend.macro.macro_storage import (
    CentralBankRateStorage,
    EconomicCalendarStorage
)


RATE_SOURCE = (
    "ORACLE_TEST_MACRO_RATE_REPOSITORY"
)

CALENDAR_SOURCE = (
    "ORACLE_TEST_MACRO_CALENDAR_REPOSITORY"
)


def make_rate(
    *,
    source_record_id,
    rate_name,
    reference_date,
    available_at,
    content_hash,
    value_percent
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

        source_record_id=
            source_record_id,

        content_hash=
            content_hash,

        source=
            RATE_SOURCE,

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

        source=
            CALENDAR_SOURCE,

        ingested_at=(
            available_at
            + timedelta(
                seconds=1
            )
        )
    )


def cleanup():

    session = SessionLocal()


    try:

        session.execute(

            delete(
                CentralBankRate
            )

            .where(
                CentralBankRate.source
                == RATE_SOURCE
            )
        )


        session.execute(

            delete(
                EconomicCalendarEvent
            )

            .where(
                EconomicCalendarEvent.source
                == CALENDAR_SOURCE
            )
        )


        session.commit()


    except Exception:

        session.rollback()

        raise


    finally:

        session.close()


def expect_error(
    error_type,
    callable_
):

    try:

        callable_()

    except error_type:

        return


    raise AssertionError(
        f"Expected {error_type.__name__}."
    )


def main():

    print("=" * 70)

    print(
        "EUR/USD ORACLE "
        "MACRO CAUSAL REPOSITORY TEST"
    )

    print("=" * 70)


    cleanup()


    try:

        rate_storage = (
            CentralBankRateStorage()
        )

        calendar_storage = (
            EconomicCalendarStorage()
        )


        rate_repository = (
            CentralBankRateRepository(
                source=RATE_SOURCE,
                central_bank="ECB"
            )
        )


        calendar_repository = (
            EconomicCalendarRepository(
                source=CALENDAR_SOURCE,
                currency="EUR",
                jurisdiction="EURO_AREA"
            )
        )


        # ====================================================
        # CENTRAL-BANK RATE VERSIONS
        # ====================================================

        original_deposit = make_rate(

            source_record_id=(
                "ecb-deposit-2026-01-22"
            ),

            rate_name=(
                "deposit_facility_rate"
            ),

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
                15,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "a" * 64
            ),

            value_percent=2.00
        )


        revised_deposit = make_rate(

            source_record_id=(
                "ecb-deposit-2026-01-22"
            ),

            rate_name=(
                "deposit_facility_rate"
            ),

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

            content_hash=(
                "b" * 64
            ),

            value_percent=1.95
        )


        mro_rate = make_rate(

            source_record_id=(
                "ecb-mro-2026-01-22"
            ),

            rate_name=(
                "main_refinancing_operations_rate"
            ),

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
                15,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "c" * 64
            ),

            value_percent=2.15
        )


        rate_storage.store_many(
            (
                original_deposit,
                revised_deposit,
                mro_rate
            )
        )


        before_release = (
            rate_repository
            .get_history_as_of(

                as_of=datetime(
                    2026,
                    1,
                    22,
                    13,
                    14,
                    59,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            before_release
            == tuple()
        )


        print(
            "\nRate pre-publication exclusion: PASS"
        )


        exact_release = (
            rate_repository
            .get_history_as_of(

                as_of=datetime(
                    2026,
                    1,
                    22,
                    13,
                    15,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            len(
                exact_release
            )
            == 2
        )

        assert (
            original_deposit
            in exact_release
        )

        assert (
            mro_rate
            in exact_release
        )


        print(
            "Rate exact available_at boundary: PASS"
        )


        before_revision = (
            rate_repository
            .get_history_as_of(

                as_of=datetime(
                    2026,
                    1,
                    22,
                    14,
                    0,
                    tzinfo=timezone.utc
                )
            )
        )


        deposit_before = next(

            item

            for item
            in before_revision

            if (
                item.rate_name
                == "deposit_facility_rate"
            )
        )


        assert (
            deposit_before.content_hash
            == original_deposit.content_hash
        )


        print(
            "Rate future revision exclusion: PASS"
        )


        after_revision = (
            rate_repository
            .get_history_as_of(

                as_of=datetime(
                    2026,
                    1,
                    22,
                    15,
                    0,
                    tzinfo=timezone.utc
                )
            )
        )


        deposit_after = next(

            item

            for item
            in after_revision

            if (
                item.rate_name
                == "deposit_facility_rate"
            )
        )


        assert (
            deposit_after.content_hash
            == revised_deposit.content_hash
        )

        assert (
            deposit_after.value_percent
            == 1.95
        )


        print(
            "Rate revision activation: PASS"
        )


        latest_rates = (
            rate_repository
            .get_latest_by_rate_as_of(

                as_of=datetime(
                    2026,
                    1,
                    22,
                    16,
                    0,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            tuple(
                item.rate_name

                for item
                in latest_rates
            )
            == (
                "deposit_facility_rate",
                "main_refinancing_operations_rate"
            )
        )


        assert (
            latest_rates[0]
            == revised_deposit
        )


        print(
            "Latest causal state by rate: PASS"
        )


        # ====================================================
        # RATE AMBIGUITY FAIL-CLOSED
        # ====================================================

        ambiguous_one = make_rate(

            source_record_id=(
                "ambiguous-rate"
            ),

            rate_name=(
                "test_rate"
            ),

            reference_date=date(
                2026,
                2,
                1
            ),

            available_at=datetime(
                2026,
                2,
                1,
                12,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "d" * 64
            ),

            value_percent=1.0
        )


        ambiguous_two = make_rate(

            source_record_id=(
                "ambiguous-rate"
            ),

            rate_name=(
                "test_rate"
            ),

            reference_date=date(
                2026,
                2,
                1
            ),

            available_at=
                ambiguous_one.available_at,

            content_hash=(
                "e" * 64
            ),

            value_percent=1.1
        )


        rate_storage.store_many(
            (
                ambiguous_one,
                ambiguous_two
            )
        )


        expect_error(

            ValueError,

            lambda:
                rate_repository
                .get_history_as_of(

                    as_of=datetime(
                        2026,
                        2,
                        1,
                        13,
                        0,
                        tzinfo=timezone.utc
                    )
                )
        )


        print(
            "Rate ambiguous revision guard: PASS"
        )


        # ====================================================
        # ECONOMIC CALENDAR
        # ====================================================

        scheduled_version = make_event(

            source_event_id=(
                "ecb-policy-2026-03-05"
            ),

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

            content_hash=(
                "a" * 64
            )
        )


        released_version = make_event(

            source_event_id=(
                "ecb-policy-2026-03-05"
            ),

            scheduled_at=
                scheduled_version.scheduled_at,

            available_at=datetime(
                2026,
                3,
                5,
                13,
                15,
                5,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "b" * 64
            ),

            actual=1.90
        )


        later_future_event = make_event(

            source_event_id=(
                "ecb-policy-2026-04-30"
            ),

            scheduled_at=datetime(
                2026,
                4,
                30,
                13,
                15,
                tzinfo=timezone.utc
            ),

            available_at=datetime(
                2026,
                1,
                7,
                9,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "c" * 64
            )
        )


        calendar_storage.store_many(
            (
                scheduled_version,
                released_version,
                later_future_event
            )
        )


        before_calendar_publication = (
            calendar_repository
            .get_window_as_of(

                as_of=datetime(
                    2026,
                    1,
                    5,
                    8,
                    59,
                    59,
                    tzinfo=timezone.utc
                ),

                scheduled_start=datetime(
                    2026,
                    3,
                    1,
                    tzinfo=timezone.utc
                ),

                scheduled_end=datetime(
                    2026,
                    3,
                    10,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            before_calendar_publication
            == tuple()
        )


        print(
            "Calendar pre-publication exclusion: PASS"
        )


        future_visible = (
            calendar_repository
            .get_window_as_of(

                as_of=datetime(
                    2026,
                    1,
                    6,
                    12,
                    0,
                    tzinfo=timezone.utc
                ),

                scheduled_start=datetime(
                    2026,
                    3,
                    1,
                    tzinfo=timezone.utc
                ),

                scheduled_end=datetime(
                    2026,
                    3,
                    10,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            future_visible
            == (
                scheduled_version,
            )
        )

        assert (
            future_visible[0].scheduled_at
            > datetime(
                2026,
                1,
                6,
                12,
                0,
                tzinfo=timezone.utc
            )
        )


        print(
            "Future scheduled-event visibility: PASS"
        )


        immediately_before_release = (
            calendar_repository
            .get_window_as_of(

                as_of=datetime(
                    2026,
                    3,
                    5,
                    13,
                    15,
                    4,
                    tzinfo=timezone.utc
                ),

                scheduled_start=datetime(
                    2026,
                    3,
                    1,
                    tzinfo=timezone.utc
                ),

                scheduled_end=datetime(
                    2026,
                    3,
                    10,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            immediately_before_release[0]
            == scheduled_version
        )

        assert (
            immediately_before_release[0].actual
            is None
        )


        print(
            "Calendar future revision exclusion: PASS"
        )


        exact_release_event = (
            calendar_repository
            .get_window_as_of(

                as_of=
                    released_version.available_at,

                scheduled_start=datetime(
                    2026,
                    3,
                    1,
                    tzinfo=timezone.utc
                ),

                scheduled_end=datetime(
                    2026,
                    3,
                    10,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            exact_release_event[0]
            == released_version
        )

        assert (
            exact_release_event[0].actual
            == 1.90
        )


        print(
            "Calendar exact revision activation: PASS"
        )


        half_open_window = (
            calendar_repository
            .get_window_as_of(

                as_of=datetime(
                    2026,
                    2,
                    1,
                    tzinfo=timezone.utc
                ),

                scheduled_start=datetime(
                    2026,
                    3,
                    1,
                    tzinfo=timezone.utc
                ),

                scheduled_end=
                    later_future_event.scheduled_at
            )
        )


        assert (
            later_future_event
            not in half_open_window
        )


        print(
            "Calendar half-open window: PASS"
        )


        # ====================================================
        # CALENDAR AMBIGUITY FAIL-CLOSED
        # ====================================================

        ambiguous_event_one = make_event(

            source_event_id=(
                "ambiguous-calendar-event"
            ),

            scheduled_at=datetime(
                2026,
                5,
                1,
                10,
                0,
                tzinfo=timezone.utc
            ),

            available_at=datetime(
                2026,
                4,
                1,
                10,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "d" * 64
            )
        )


        ambiguous_event_two = make_event(

            source_event_id=(
                "ambiguous-calendar-event"
            ),

            scheduled_at=
                ambiguous_event_one.scheduled_at,

            available_at=
                ambiguous_event_one.available_at,

            content_hash=(
                "e" * 64
            ),

            actual=1.8
        )


        calendar_storage.store_many(
            (
                ambiguous_event_one,
                ambiguous_event_two
            )
        )


        expect_error(

            ValueError,

            lambda:
                calendar_repository
                .get_window_as_of(

                    as_of=datetime(
                        2026,
                        4,
                        2,
                        tzinfo=timezone.utc
                    ),

                    scheduled_start=datetime(
                        2026,
                        5,
                        1,
                        tzinfo=timezone.utc
                    ),

                    scheduled_end=datetime(
                        2026,
                        5,
                        2,
                        tzinfo=timezone.utc
                    )
                )
        )


        print(
            "Calendar ambiguous revision guard: PASS"
        )


        # ====================================================
        # INPUT GUARDS
        # ====================================================

        expect_error(

            TypeError,

            lambda:
                rate_repository
                .get_history_as_of(
                    as_of="2026-01-01"
                )
        )


        expect_error(

            ValueError,

            lambda:
                rate_repository
                .get_history_as_of(
                    as_of=datetime(
                        2026,
                        1,
                        1
                    )
                )
        )


        expect_error(

            ValueError,

            lambda:
                rate_repository
                .get_history_as_of(
                    as_of=datetime(
                        2026,
                        1,
                        1,
                        tzinfo=timezone.utc
                    ),
                    limit=0
                )
        )


        expect_error(

            ValueError,

            lambda:
                calendar_repository
                .get_window_as_of(

                    as_of=datetime(
                        2026,
                        1,
                        1,
                        tzinfo=timezone.utc
                    ),

                    scheduled_start=datetime(
                        2026,
                        2,
                        1,
                        tzinfo=timezone.utc
                    ),

                    scheduled_end=datetime(
                        2026,
                        2,
                        1,
                        tzinfo=timezone.utc
                    )
                )
        )


        print(
            "Macro repository input guards: PASS"
        )


        print(
            "\n"
            + "=" * 70
        )

        print(
            "MACRO CAUSAL REPOSITORY TEST: PASS"
        )

        print(
            "=" * 70
        )


    finally:

        cleanup()


if __name__ == "__main__":

    main()