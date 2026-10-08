from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.macro.ecb_calendar_backfill import (
    ECBPolicyCalendarBackfill
)

from backend.macro.macro_schemas import (
    EconomicCalendarEventSnapshot
)

from backend.macro.macro_storage import (
    MacroStorageResult
)


INGESTED_AT = datetime(
    2026,
    10,
    8,
    0,
    0,
    tzinfo=timezone.utc
)


def make_snapshot(
    ingested_at
):

    return (
        EconomicCalendarEventSnapshot(

            source_event_id=(
                "ECB|MONETARY_POLICY_DECISION|"
                "2026|06"
            ),

            event_name=(
                "ECB Monetary Policy Decision"
            ),

            jurisdiction="EURO_AREA",

            currency="EUR",

            scheduled_at=datetime(
                2026,
                9,
                10,
                12,
                15,
                tzinfo=timezone.utc
            ),

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
                "a" * 64
            ),

            source=(
                "ECB_GOVERNING_COUNCIL_CALENDAR"
            ),

            ingested_at=
                ingested_at
        )
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


class FakeBuilder:

    def __init__(
        self,
        snapshots
    ):

        self.snapshots = tuple(
            snapshots
        )

        self.calls = []


    def build_2026_snapshots(
        self,
        ingested_at
    ):

        self.calls.append({

            "ingested_at":
                ingested_at
        })


        return self.snapshots


class FakeStorage:

    def __init__(
        self,
        *,
        inserted=None,
        skipped=None
    ):

        self.received = None

        self.inserted_override = (
            inserted
        )

        self.skipped_override = (
            skipped
        )


    def store_many(
        self,
        snapshots
    ):

        self.received = tuple(
            snapshots
        )


        received_count = len(
            self.received
        )


        inserted = (
            received_count
            if self.inserted_override is None
            else self.inserted_override
        )


        skipped = (
            0
            if self.skipped_override is None
            else self.skipped_override
        )


        return MacroStorageResult(

            received=
                received_count,

            inserted=
                inserted,

            skipped=
                skipped
        )


class BadReceivedStorage:

    def store_many(
        self,
        snapshots
    ):

        snapshots = tuple(
            snapshots
        )


        return MacroStorageResult(

            received=(
                len(
                    snapshots
                )
                + 1
            ),

            inserted=0,

            skipped=0
        )


class BadAccountingStorage:

    def store_many(
        self,
        snapshots
    ):

        snapshots = tuple(
            snapshots
        )


        return MacroStorageResult(

            received=
                len(
                    snapshots
                ),

            inserted=0,

            skipped=0
        )


def main():

    print("=" * 70)

    print(
        "EUR/USD ORACLE "
        "ECB CALENDAR BACKFILL TEST"
    )

    print("=" * 70)


    snapshot = (
        make_snapshot(
            INGESTED_AT
        )
    )


    builder = (
        FakeBuilder(
            (
                snapshot,
            )
        )
    )


    storage = (
        FakeStorage()
    )


    backfill = (
        ECBPolicyCalendarBackfill(

            builder=
                builder,

            storage=
                storage
        )
    )


    # ========================================================
    # BUILDER / STORAGE COMPOSITION
    # ========================================================

    result = (
        backfill.run_2026(

            ingested_at=
                INGESTED_AT
        )
    )


    assert (
        result.calendar_year
        == 2026
    )

    assert (
        result.ingested_at
        == INGESTED_AT
    )

    assert (
        result.built
        == 1
    )

    assert (
        result.inserted
        == 1
    )

    assert (
        result.skipped
        == 0
    )


    assert (
        builder.calls
        == [
            {
                "ingested_at":
                    INGESTED_AT
            }
        ]
    )


    assert (
        storage.received
        == (
            snapshot,
        )
    )


    print(
        "\nBuilder/storage composition: PASS"
    )

    print(
        "Batch timestamp propagation: PASS"
    )

    print(
        "Result accounting: PASS"
    )


    # ========================================================
    # UTC NORMALIZATION
    # ========================================================

    offset_ingested_at = (
        datetime(
            2026,
            10,
            8,
            3,
            0,
            tzinfo=timezone(
                timedelta(
                    hours=3
                )
            )
        )
    )


    utc_builder = (
        FakeBuilder(
            tuple()
        )
    )


    utc_result = (

        ECBPolicyCalendarBackfill(

            builder=
                utc_builder,

            storage=
                FakeStorage()

        ).run_2026(

            ingested_at=
                offset_ingested_at
        )
    )


    assert (
        utc_result.ingested_at
        == INGESTED_AT
    )


    assert (
        utc_builder.calls[0][
            "ingested_at"
        ]
        == INGESTED_AT
    )


    print(
        "UTC ingestion normalization: PASS"
    )


    # ========================================================
    # EMPTY BUILD CONTRACT
    # ========================================================

    empty_builder = (
        FakeBuilder(
            tuple()
        )
    )


    empty_storage = (
        FakeStorage()
    )


    empty_result = (

        ECBPolicyCalendarBackfill(

            builder=
                empty_builder,

            storage=
                empty_storage

        ).run_2026(

            ingested_at=
                INGESTED_AT
        )
    )


    assert (
        empty_result.built,
        empty_result.inserted,
        empty_result.skipped
    ) == (
        0,
        0,
        0
    )


    assert (
        empty_storage.received
        == tuple()
    )


    print(
        "Empty build contract: PASS"
    )


    # ========================================================
    # IDEMPOTENT SKIP ACCOUNTING
    # ========================================================

    skip_result = (

        ECBPolicyCalendarBackfill(

            builder=
                FakeBuilder(
                    (
                        snapshot,
                    )
                ),

            storage=
                FakeStorage(
                    inserted=0,
                    skipped=1
                )

        ).run_2026(

            ingested_at=
                INGESTED_AT
        )
    )


    assert (
        skip_result.built,
        skip_result.inserted,
        skip_result.skipped
    ) == (
        1,
        0,
        1
    )


    print(
        "Idempotent skip accounting: PASS"
    )


    # ========================================================
    # TIMESTAMP GUARDS
    # ========================================================

    assert_value_error(

        lambda:
            backfill.run_2026(

                ingested_at=datetime(
                    2026,
                    10,
                    8
                )
            )
    )


    assert_value_error(

        lambda:
            backfill.run_2026(

                ingested_at=(
                    "2026-10-08"
                )
            )
    )


    print(
        "Timestamp guard: PASS"
    )


    # ========================================================
    # STORAGE RECEIVED-COUNT GUARD
    # ========================================================

    try:

        ECBPolicyCalendarBackfill(

            builder=
                FakeBuilder(
                    (
                        snapshot,
                    )
                ),

            storage=
                BadReceivedStorage()

        ).run_2026(

            ingested_at=
                INGESTED_AT
        )

    except RuntimeError:

        pass

    else:

        raise AssertionError(
            "Storage received-count "
            "mismatch was accepted."
        )


    print(
        "Storage received-count guard: PASS"
    )


    # ========================================================
    # STORAGE ACCOUNTING GUARD
    # ========================================================

    try:

        ECBPolicyCalendarBackfill(

            builder=
                FakeBuilder(
                    (
                        snapshot,
                    )
                ),

            storage=
                BadAccountingStorage()

        ).run_2026(

            ingested_at=
                INGESTED_AT
        )

    except RuntimeError:

        pass

    else:

        raise AssertionError(
            "Storage accounting mismatch "
            "was accepted."
        )


    print(
        "Storage accounting guard: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "ECB CALENDAR BACKFILL TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()