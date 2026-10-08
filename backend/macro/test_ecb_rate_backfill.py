from datetime import (
    date,
    datetime,
    timedelta,
    timezone
)

from backend.macro.ecb_rate_backfill import (
    ECBRateBackfill
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot
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

    return CentralBankRateSnapshot(

        central_bank="ECB",

        rate_name=(
            "deposit_facility_rate"
        ),

        reference_date=date(
            2026,
            9,
            16
        ),

        available_at=datetime(
            2026,
            9,
            10,
            14,
            22,
            33,
            tzinfo=timezone.utc
        ),

        value_percent=2.5,

        source_record_id=(
            "FM.B.U2.EUR.4F.KR.DFR.LEV"
            "|2026-09-16"
        ),

        content_hash=(
            "a" * 64
        ),

        source="ECB_SDMX_FM",

        ingested_at=
            ingested_at
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


def assert_type_error(
    callable_
):

    try:

        callable_()

    except TypeError:

        return


    raise AssertionError(
        "Expected TypeError."
    )


class FakeClient:

    def __init__(
        self,
        snapshots
    ):

        self.snapshots = tuple(
            snapshots
        )

        self.calls = []


    def fetch_snapshots(
        self,
        start_date,
        end_date,
        ingested_at
    ):

        self.calls.append({

            "start_date":
                start_date,

            "end_date":
                end_date,

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
        "ECB RATE BACKFILL TEST"
    )

    print("=" * 70)


    snapshot = (
        make_snapshot(
            INGESTED_AT
        )
    )


    client = (
        FakeClient(
            (
                snapshot,
            )
        )
    )


    storage = (
        FakeStorage()
    )


    backfill = (
        ECBRateBackfill(
            client=client,
            storage=storage
        )
    )


    # ========================================================
    # CLIENT / STORAGE COMPOSITION
    # ========================================================

    result = backfill.run(

        start_date=date(
            2026,
            9,
            1
        ),

        end_date=date(
            2026,
            9,
            30
        ),

        ingested_at=
            INGESTED_AT
    )


    assert (
        result.start_date
        == date(
            2026,
            9,
            1
        )
    )

    assert (
        result.end_date
        == date(
            2026,
            9,
            30
        )
    )

    assert (
        result.ingested_at
        == INGESTED_AT
    )

    assert (
        result.fetched
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
        len(
            client.calls
        )
        == 1
    )


    assert (
        client.calls[0]
        == {

            "start_date":
                date(
                    2026,
                    9,
                    1
                ),

            "end_date":
                date(
                    2026,
                    9,
                    30
                ),

            "ingested_at":
                INGESTED_AT
        }
    )


    assert (
        storage.received
        == (
            snapshot,
        )
    )


    print(
        "\nClient/storage composition: PASS"
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

    non_utc_ingested_at = (
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


    utc_client = (
        FakeClient(
            tuple()
        )
    )


    utc_backfill = (
        ECBRateBackfill(

            client=
                utc_client,

            storage=
                FakeStorage()
        )
    )


    utc_result = (
        utc_backfill.run(

            start_date=date(
                2026,
                9,
                1
            ),

            end_date=date(
                2026,
                9,
                30
            ),

            ingested_at=
                non_utc_ingested_at
        )
    )


    assert (
        utc_result.ingested_at
        == INGESTED_AT
    )


    assert (
        utc_client.calls[0][
            "ingested_at"
        ]
        == INGESTED_AT
    )


    print(
        "UTC ingestion normalization: PASS"
    )


    # ========================================================
    # EMPTY FETCH CONTRACT
    # ========================================================

    empty_client = (
        FakeClient(
            tuple()
        )
    )


    empty_storage = (
        FakeStorage()
    )


    empty_result = (
        ECBRateBackfill(

            client=
                empty_client,

            storage=
                empty_storage

        ).run(

            start_date=date(
                2026,
                1,
                1
            ),

            end_date=date(
                2026,
                1,
                31
            ),

            ingested_at=
                INGESTED_AT
        )
    )


    assert (
        empty_result.fetched,
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
        "Empty fetch contract: PASS"
    )


    # ========================================================
    # IDEMPOTENT RESULT ACCOUNTING
    # ========================================================

    skip_client = (
        FakeClient(
            (
                snapshot,
            )
        )
    )


    skip_storage = (
        FakeStorage(
            inserted=0,
            skipped=1
        )
    )


    skip_result = (
        ECBRateBackfill(

            client=
                skip_client,

            storage=
                skip_storage

        ).run(

            start_date=date(
                2026,
                9,
                1
            ),

            end_date=date(
                2026,
                9,
                30
            ),

            ingested_at=
                INGESTED_AT
        )
    )


    assert (
        skip_result.fetched,
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
    # DATE TYPE GUARDS
    # ========================================================

    assert_type_error(

        lambda:
            backfill.run(

                start_date=(
                    "2026-09-01"
                ),

                end_date=date(
                    2026,
                    9,
                    30
                ),

                ingested_at=
                    INGESTED_AT
            )
    )


    assert_type_error(

        lambda:
            backfill.run(

                start_date=date(
                    2026,
                    9,
                    1
                ),

                end_date=(
                    "2026-09-30"
                ),

                ingested_at=
                    INGESTED_AT
            )
    )


    print(
        "Date type guards: PASS"
    )


    # ========================================================
    # BAD DATE RANGE
    # ========================================================

    calls_before_bad_range = (
        len(
            client.calls
        )
    )


    assert_value_error(

        lambda:
            backfill.run(

                start_date=date(
                    2026,
                    10,
                    1
                ),

                end_date=date(
                    2026,
                    9,
                    30
                ),

                ingested_at=
                    INGESTED_AT
            )
    )


    assert (
        len(
            client.calls
        )
        == calls_before_bad_range
    )


    print(
        "Date-range guard: PASS"
    )


    # ========================================================
    # INGESTION TIMESTAMP GUARDS
    # ========================================================

    assert_value_error(

        lambda:
            backfill.run(

                start_date=date(
                    2026,
                    9,
                    1
                ),

                end_date=date(
                    2026,
                    9,
                    30
                ),

                ingested_at=datetime(
                    2026,
                    10,
                    8
                )
            )
    )


    assert_value_error(

        lambda:
            backfill.run(

                start_date=date(
                    2026,
                    9,
                    1
                ),

                end_date=date(
                    2026,
                    9,
                    30
                ),

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

    bad_received_backfill = (
        ECBRateBackfill(

            client=
                FakeClient(
                    (
                        snapshot,
                    )
                ),

            storage=
                BadReceivedStorage()
        )
    )


    try:

        bad_received_backfill.run(

            start_date=date(
                2026,
                9,
                1
            ),

            end_date=date(
                2026,
                9,
                30
            ),

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

    bad_accounting_backfill = (
        ECBRateBackfill(

            client=
                FakeClient(
                    (
                        snapshot,
                    )
                ),

            storage=
                BadAccountingStorage()
        )
    )


    try:

        bad_accounting_backfill.run(

            start_date=date(
                2026,
                9,
                1
            ),

            end_date=date(
                2026,
                9,
                30
            ),

            ingested_at=
                INGESTED_AT
        )

    except RuntimeError:

        pass

    else:

        raise AssertionError(
            "Storage accounting "
            "mismatch was accepted."
        )


    print(
        "Storage accounting guard: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "ECB RATE BACKFILL TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()