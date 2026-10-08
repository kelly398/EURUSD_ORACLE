from datetime import (
    date,
    datetime,
    timedelta,
    timezone
)

from sqlalchemy import (
    delete,
    func,
    select
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    CentralBankRate,
    EconomicCalendarEvent
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
    EconomicCalendarEventSnapshot
)

from backend.macro.macro_storage import (
    CentralBankRateStorage,
    EconomicCalendarStorage
)


TEST_RATE_SOURCE = (
    "ORACLE_TEST_MACRO_RATE_STORAGE"
)

TEST_CALENDAR_SOURCE = (
    "ORACLE_TEST_MACRO_CALENDAR_STORAGE"
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
HASH_D = "d" * 64
HASH_E = "e" * 64


def make_rate(
    *,
    source_record_id,
    reference_date,
    available_at,
    content_hash,
    value_percent,
    ingested_at=None
):

    if ingested_at is None:

        ingested_at = (
            available_at
            + timedelta(
                seconds=1
            )
        )


    return CentralBankRateSnapshot(

        central_bank="ECB",

        rate_name=(
            "deposit_facility_rate"
        ),

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
            TEST_RATE_SOURCE,

        ingested_at=
            ingested_at
    )


def make_event(
    *,
    source_event_id,
    scheduled_at,
    available_at,
    content_hash,
    actual=None,
    forecast=None,
    previous=None,
    unit=None,
    ingested_at=None
):

    if ingested_at is None:

        ingested_at = (
            available_at
            + timedelta(
                seconds=1
            )
        )


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

        forecast=
            forecast,

        previous=
            previous,

        unit=
            unit,

        content_hash=
            content_hash,

        source=
            TEST_CALENDAR_SOURCE,

        ingested_at=
            ingested_at
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
                == TEST_RATE_SOURCE
            )
        )


        session.execute(

            delete(
                EconomicCalendarEvent
            )

            .where(
                EconomicCalendarEvent.source
                == TEST_CALENDAR_SOURCE
            )
        )


        session.commit()


    except Exception:

        session.rollback()

        raise


    finally:

        session.close()


def count_rate_rows():

    session = SessionLocal()


    try:

        return session.scalar(

            select(
                func.count()
            )

            .select_from(
                CentralBankRate
            )

            .where(
                CentralBankRate.source
                == TEST_RATE_SOURCE
            )
        )


    finally:

        session.close()


def count_calendar_rows():

    session = SessionLocal()


    try:

        return session.scalar(

            select(
                func.count()
            )

            .select_from(
                EconomicCalendarEvent
            )

            .where(
                EconomicCalendarEvent.source
                == TEST_CALENDAR_SOURCE
            )
        )


    finally:

        session.close()


def get_rate_rows(
    source_record_id
):

    session = SessionLocal()


    try:

        return (

            session.execute(

                select(
                    CentralBankRate
                )

                .where(

                    CentralBankRate.source
                    == TEST_RATE_SOURCE,

                    CentralBankRate
                    .source_record_id
                    == source_record_id
                )

                .order_by(

                    CentralBankRate
                    .available_at
                    .asc(),

                    CentralBankRate
                    .id
                    .asc()
                )
            )

            .scalars()

            .all()
        )


    finally:

        session.close()


def get_event_rows(
    source_event_id
):

    session = SessionLocal()


    try:

        return (

            session.execute(

                select(
                    EconomicCalendarEvent
                )

                .where(

                    EconomicCalendarEvent.source
                    == TEST_CALENDAR_SOURCE,

                    EconomicCalendarEvent
                    .source_event_id
                    == source_event_id
                )

                .order_by(

                    EconomicCalendarEvent
                    .available_at
                    .asc(),

                    EconomicCalendarEvent
                    .id
                    .asc()
                )
            )

            .scalars()

            .all()
        )


    finally:

        session.close()


class FailingRateStorage(
    CentralBankRateStorage
):

    def _insert_snapshot(
        self,
        session,
        snapshot
    ):

        inserted = (
            super()
            ._insert_snapshot(
                session=session,
                snapshot=snapshot
            )
        )


        if (
            snapshot.source_record_id
            == "rate-rollback-second"
        ):

            raise RuntimeError(
                "Intentional rate batch failure."
            )


        return inserted


class FailingCalendarStorage(
    EconomicCalendarStorage
):

    def _insert_snapshot(
        self,
        session,
        snapshot
    ):

        inserted = (
            super()
            ._insert_snapshot(
                session=session,
                snapshot=snapshot
            )
        )


        if (
            snapshot.source_event_id
            == "calendar-rollback-second"
        ):

            raise RuntimeError(
                "Intentional calendar batch failure."
            )


        return inserted


def main():

    print("=" * 70)

    print(
        "EUR/USD ORACLE "
        "MACRO TRANSACTIONAL STORAGE TEST"
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


        # ====================================================
        # EMPTY BATCH CONTRACT
        # ====================================================

        empty_rate = (
            rate_storage.store_many(
                []
            )
        )

        empty_calendar = (
            calendar_storage.store_many(
                []
            )
        )


        assert (
            empty_rate.received,
            empty_rate.inserted,
            empty_rate.skipped
        ) == (
            0,
            0,
            0
        )

        assert (
            empty_calendar.received,
            empty_calendar.inserted,
            empty_calendar.skipped
        ) == (
            0,
            0,
            0
        )


        print(
            "\nEmpty batch contract: PASS"
        )


        # ====================================================
        # CENTRAL BANK RATE FIRST INSERT
        # ====================================================

        original_rate = make_rate(

            source_record_id=(
                "ecb-deposit-2026-01-22"
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

            content_hash=
                HASH_A,

            value_percent=
                2.00
        )


        result = (
            rate_storage.store_many(
                [
                    original_rate
                ]
            )
        )


        assert (
            result.received,
            result.inserted,
            result.skipped
        ) == (
            1,
            1,
            0
        )

        assert (
            count_rate_rows()
            == 1
        )


        print(
            "Central-bank first insertion: PASS"
        )


        # ====================================================
        # IDENTICAL RATE RE-INGESTION
        # ====================================================

        same_rate_later = make_rate(

            source_record_id=(
                original_rate
                .source_record_id
            ),

            reference_date=
                original_rate.reference_date,

            available_at=datetime(
                2026,
                1,
                22,
                15,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=
                HASH_A,

            value_percent=
                2.00,

            ingested_at=datetime(
                2026,
                1,
                22,
                15,
                0,
                5,
                tzinfo=timezone.utc
            )
        )


        result = (
            rate_storage.store_many(
                [
                    same_rate_later
                ]
            )
        )


        assert (
            result.inserted,
            result.skipped
        ) == (
            0,
            1
        )


        rate_rows = get_rate_rows(
            original_rate.source_record_id
        )


        assert (
            len(
                rate_rows
            )
            == 1
        )

        assert (
            rate_rows[0].available_at
            == original_rate.available_at
        )


        print(
            "Central-bank identical "
            "re-ingestion skip: PASS"
        )

        print(
            "Central-bank causal timestamp "
            "preserved: PASS"
        )


        # ====================================================
        # TRUE RATE REVISION
        # ====================================================

        revised_rate = make_rate(

            source_record_id=(
                original_rate
                .source_record_id
            ),

            reference_date=
                original_rate.reference_date,

            available_at=datetime(
                2026,
                1,
                22,
                16,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=
                HASH_B,

            value_percent=
                1.95
        )


        result = (
            rate_storage.store_many(
                [
                    revised_rate
                ]
            )
        )


        assert (
            result.inserted,
            result.skipped
        ) == (
            1,
            0
        )


        rate_rows = get_rate_rows(
            original_rate.source_record_id
        )


        assert (
            len(
                rate_rows
            )
            == 2
        )

        assert (
            rate_rows[0].content_hash
            == HASH_A
        )

        assert (
            rate_rows[1].content_hash
            == HASH_B
        )

        assert (
            rate_rows[0].value_percent
            == 2.00
        )

        assert (
            rate_rows[1].value_percent
            == 1.95
        )


        print(
            "Central-bank changed-content "
            "revision: PASS"
        )

        print(
            "Central-bank historical version "
            "preservation: PASS"
        )


        # ====================================================
        # SAME-BATCH RATE DUPLICATE
        # ====================================================

        duplicate_rate = make_rate(

            source_record_id=(
                "rate-batch-duplicate"
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

            content_hash=
                HASH_C,

            value_percent=
                1.90
        )


        result = (
            rate_storage.store_many(
                [
                    duplicate_rate,
                    duplicate_rate
                ]
            )
        )


        assert (
            result.received,
            result.inserted,
            result.skipped
        ) == (
            2,
            1,
            1
        )


        print(
            "Central-bank same-batch "
            "duplicate suppression: PASS"
        )


        # ====================================================
        # RATE TRANSACTION ROLLBACK
        # ====================================================

        rate_rows_before = (
            count_rate_rows()
        )


        rollback_rate_one = make_rate(

            source_record_id=(
                "rate-rollback-first"
            ),

            reference_date=date(
                2026,
                2,
                10
            ),

            available_at=datetime(
                2026,
                2,
                10,
                12,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=
                HASH_D,

            value_percent=
                1.85
        )


        rollback_rate_two = make_rate(

            source_record_id=(
                "rate-rollback-second"
            ),

            reference_date=date(
                2026,
                2,
                20
            ),

            available_at=datetime(
                2026,
                2,
                20,
                12,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=
                HASH_E,

            value_percent=
                1.80
        )


        try:

            FailingRateStorage().store_many(
                [
                    rollback_rate_one,
                    rollback_rate_two
                ]
            )


        except RuntimeError as exc:

            assert (
                str(
                    exc
                )
                == (
                    "Intentional rate "
                    "batch failure."
                )
            )


        else:

            raise AssertionError(
                "Intentional rate batch "
                "failure did not propagate."
            )


        assert (
            count_rate_rows()
            == rate_rows_before
        )

        assert (
            get_rate_rows(
                "rate-rollback-first"
            )
            == []
        )

        assert (
            get_rate_rows(
                "rate-rollback-second"
            )
            == []
        )


        print(
            "Central-bank mid-batch "
            "rollback: PASS"
        )

        print(
            "Central-bank no partial "
            "persistence: PASS"
        )


        # ====================================================
        # ECONOMIC CALENDAR FIRST INSERT
        # ====================================================

        original_event = make_event(

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

            content_hash=
                HASH_A
        )


        result = (
            calendar_storage.store_many(
                [
                    original_event
                ]
            )
        )


        assert (
            result.received,
            result.inserted,
            result.skipped
        ) == (
            1,
            1,
            0
        )

        assert (
            count_calendar_rows()
            == 1
        )


        print(
            "Calendar first insertion: PASS"
        )


        # ====================================================
        # IDENTICAL EVENT RE-INGESTION
        # ====================================================

        same_event_later = make_event(

            source_event_id=(
                original_event
                .source_event_id
            ),

            scheduled_at=
                original_event.scheduled_at,

            available_at=datetime(
                2026,
                1,
                10,
                9,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=
                HASH_A,

            ingested_at=datetime(
                2026,
                1,
                10,
                9,
                0,
                5,
                tzinfo=timezone.utc
            )
        )


        result = (
            calendar_storage.store_many(
                [
                    same_event_later
                ]
            )
        )


        assert (
            result.inserted,
            result.skipped
        ) == (
            0,
            1
        )


        event_rows = get_event_rows(
            original_event.source_event_id
        )


        assert (
            len(
                event_rows
            )
            == 1
        )

        assert (
            event_rows[0].available_at
            == original_event.available_at
        )


        print(
            "Calendar identical "
            "re-ingestion skip: PASS"
        )

        print(
            "Calendar causal timestamp "
            "preserved: PASS"
        )


        # ====================================================
        # TRUE EVENT REVISION / RELEASE
        # ====================================================

        released_event = make_event(

            source_event_id=(
                original_event
                .source_event_id
            ),

            scheduled_at=
                original_event.scheduled_at,

            available_at=datetime(
                2026,
                3,
                5,
                13,
                15,
                5,
                tzinfo=timezone.utc
            ),

            content_hash=
                HASH_B,

            actual=
                1.90,

            previous=
                2.00,

            unit=
                "percent"
        )


        result = (
            calendar_storage.store_many(
                [
                    released_event
                ]
            )
        )


        assert (
            result.inserted,
            result.skipped
        ) == (
            1,
            0
        )


        event_rows = get_event_rows(
            original_event.source_event_id
        )


        assert (
            len(
                event_rows
            )
            == 2
        )

        assert (
            event_rows[0].content_hash
            == HASH_A
        )

        assert (
            event_rows[0].actual
            is None
        )

        assert (
            event_rows[1].content_hash
            == HASH_B
        )

        assert (
            event_rows[1].actual
            == 1.90
        )


        print(
            "Calendar changed-content "
            "revision: PASS"
        )

        print(
            "Calendar historical version "
            "preservation: PASS"
        )


        # ====================================================
        # SAME-BATCH EVENT DUPLICATE
        # ====================================================

        duplicate_event = make_event(

            source_event_id=(
                "calendar-batch-duplicate"
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
                2,
                1,
                9,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=
                HASH_C
        )


        result = (
            calendar_storage.store_many(
                [
                    duplicate_event,
                    duplicate_event
                ]
            )
        )


        assert (
            result.received,
            result.inserted,
            result.skipped
        ) == (
            2,
            1,
            1
        )


        print(
            "Calendar same-batch duplicate "
            "suppression: PASS"
        )


        # ====================================================
        # CALENDAR TRANSACTION ROLLBACK
        # ====================================================

        calendar_rows_before = (
            count_calendar_rows()
        )


        rollback_event_one = make_event(

            source_event_id=(
                "calendar-rollback-first"
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

            content_hash=
                HASH_D
        )


        rollback_event_two = make_event(

            source_event_id=(
                "calendar-rollback-second"
            ),

            scheduled_at=datetime(
                2026,
                5,
                2,
                10,
                0,
                tzinfo=timezone.utc
            ),

            available_at=datetime(
                2026,
                4,
                2,
                10,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=
                HASH_E
        )


        try:

            FailingCalendarStorage().store_many(
                [
                    rollback_event_one,
                    rollback_event_two
                ]
            )


        except RuntimeError as exc:

            assert (
                str(
                    exc
                )
                == (
                    "Intentional calendar "
                    "batch failure."
                )
            )


        else:

            raise AssertionError(
                "Intentional calendar batch "
                "failure did not propagate."
            )


        assert (
            count_calendar_rows()
            == calendar_rows_before
        )

        assert (
            get_event_rows(
                "calendar-rollback-first"
            )
            == []
        )

        assert (
            get_event_rows(
                "calendar-rollback-second"
            )
            == []
        )


        print(
            "Calendar mid-batch rollback: PASS"
        )

        print(
            "Calendar no partial "
            "persistence: PASS"
        )


        # ====================================================
        # TYPE GUARDS
        # ====================================================

        try:

            rate_storage.store_many(
                [
                    original_event
                ]
            )

        except TypeError:

            pass

        else:

            raise AssertionError(
                "Rate storage type guard failed."
            )


        try:

            calendar_storage.store_many(
                [
                    original_rate
                ]
            )

        except TypeError:

            pass

        else:

            raise AssertionError(
                "Calendar storage type guard failed."
            )


        print(
            "Macro storage type guards: PASS"
        )


        print(
            "\n"
            + "=" * 70
        )

        print(
            "MACRO TRANSACTIONAL STORAGE TEST: PASS"
        )

        print(
            "=" * 70
        )


    finally:

        cleanup()


if __name__ == "__main__":

    main()