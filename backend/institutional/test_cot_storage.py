from datetime import (
    date,
    datetime,
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
    COTPositioning
)

from backend.institutional.cot_repository import (
    COTPositioningRepository
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot,
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)

from backend.institutional.cot_storage import (
    COTPositioningStorage
)


TEST_SOURCE = (
    "ORACLE_COT_STORAGE_TEST"
)


def make_snapshot(
    *,
    report_date,
    available_at,
    content_hash,
    source_record_id,
    asset_manager_long,
    ingested_at=None
):

    if ingested_at is None:

        ingested_at = (
            available_at
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

        open_interest=
            800_000,

        dealer_long=
            50_000,

        dealer_short=
            250_000,

        dealer_spread=
            8_000,

        asset_manager_long=
            asset_manager_long,

        asset_manager_short=
            280_000,

        asset_manager_spread=
            40_000,

        leveraged_money_long=
            125_000,

        leveraged_money_short=
            165_000,

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

        source_record_id=
            source_record_id,

        content_hash=
            content_hash,

        source=
            TEST_SOURCE,

        ingested_at=
            ingested_at
    )


def cleanup():

    session = SessionLocal()

    try:

        session.execute(

            delete(
                COTPositioning
            )

            .where(
                COTPositioning.source
                == TEST_SOURCE
            )
        )

        session.commit()

    finally:

        session.close()


def count_test_rows():

    session = SessionLocal()

    try:

        return session.scalar(

            select(
                func.count()
            )

            .select_from(
                COTPositioning
            )

            .where(
                COTPositioning.source
                == TEST_SOURCE
            )
        )

    finally:

        session.close()


def get_rows_for_report(
    report_date
):

    session = SessionLocal()

    try:

        return (

            session.execute(

                select(
                    COTPositioning
                )

                .where(

                    COTPositioning.source
                    == TEST_SOURCE,

                    COTPositioning.report_date
                    == report_date
                )

                .order_by(
                    COTPositioning
                    .available_at
                    .asc()
                )
            )

            .scalars()

            .all()
        )

    finally:

        session.close()


class FailingStorage(
    COTPositioningStorage
):

    def _insert_snapshot(
        self,
        session,
        snapshot
    ):

        if (
            snapshot.source_record_id
            == "rollback-second"
        ):

            raise RuntimeError(
                "Intentional batch failure."
            )


        return super()._insert_snapshot(
            session=session,
            snapshot=snapshot
        )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "COT STORAGE TEST"
    )

    print(
        "=" * 70
    )


    cleanup()


    try:

        storage = (
            COTPositioningStorage()
        )


        original = make_snapshot(

            report_date=date(
                2026,
                9,
                22
            ),

            available_at=datetime(
                2026,
                9,
                25,
                19,
                30,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "a" * 64
            ),

            source_record_id=(
                "storage-original"
            ),

            asset_manager_long=
                490_000
        )


        # ==================================
        # FIRST INSERT
        # ==================================

        result = (
            storage.store_many(
                [
                    original
                ]
            )
        )


        assert (
            result.received
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
            count_test_rows()
            == 1
        )


        print(
            "\nFirst insertion: PASS"
        )


        # ==================================
        # IDENTICAL RE-INGESTION
        # ==================================
        #
        # Same economic observation and same
        # hash, but PRE metadata is observed
        # later.
        #
        # The original causal row MUST NOT
        # be mutated.
        # ==================================

        same_content_later = make_snapshot(

            report_date=date(
                2026,
                9,
                22
            ),

            available_at=datetime(
                2026,
                9,
                28,
                12,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "a" * 64
            ),

            source_record_id=(
                "storage-original"
            ),

            asset_manager_long=
                490_000,

            ingested_at=datetime(
                2026,
                9,
                28,
                12,
                5,
                tzinfo=timezone.utc
            )
        )


        result = (
            storage.store_many(
                [
                    same_content_later
                ]
            )
        )


        assert (
            result.inserted
            == 0
        )

        assert (
            result.skipped
            == 1
        )

        assert (
            count_test_rows()
            == 1
        )


        rows = (
            get_rows_for_report(
                date(
                    2026,
                    9,
                    22
                )
            )
        )


        assert (
            len(
                rows
            )
            == 1
        )

        assert (
            rows[0].available_at
            == datetime(
                2026,
                9,
                25,
                19,
                30,
                tzinfo=timezone.utc
            )
        )


        print(
            "Identical re-ingestion skip: PASS"
        )

        print(
            "Original causal timestamp preserved: PASS"
        )


        # ==================================
        # TRUE CONTENT REVISION
        # ==================================

        revision = make_snapshot(

            report_date=date(
                2026,
                9,
                22
            ),

            available_at=datetime(
                2026,
                9,
                28,
                14,
                0,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "b" * 64
            ),

            source_record_id=(
                "storage-original"
            ),

            asset_manager_long=
                495_000
        )


        result = (
            storage.store_many(
                [
                    revision
                ]
            )
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
            count_test_rows()
            == 2
        )


        rows = (
            get_rows_for_report(
                date(
                    2026,
                    9,
                    22
                )
            )
        )


        assert (
            len(
                rows
            )
            == 2
        )

        assert (
            rows[0].content_hash
            == (
                "a" * 64
            )
        )

        assert (
            rows[1].content_hash
            == (
                "b" * 64
            )
        )


        print(
            "Changed-content revision insertion: PASS"
        )

        print(
            "Historical version preservation: PASS"
        )


        # ==================================
        # REPOSITORY CAUSALITY
        # ==================================

        repository = (
            COTPositioningRepository(
                source=TEST_SOURCE
            )
        )


        before_revision = (
            repository.get_latest_as_of(

                datetime(
                    2026,
                    9,
                    27,
                    12,
                    0,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            before_revision
            .content_hash
            == (
                "a" * 64
            )
        )

        assert (
            before_revision
            .asset_manager_long
            == 490_000
        )


        after_revision = (
            repository.get_latest_as_of(

                datetime(
                    2026,
                    9,
                    28,
                    14,
                    0,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            after_revision
            .content_hash
            == (
                "b" * 64
            )
        )

        assert (
            after_revision
            .asset_manager_long
            == 495_000
        )


        print(
            "Stored revision causality: PASS"
        )


        # ==================================
        # SAME-BATCH DUPLICATE
        # ==================================

        batch_duplicate = make_snapshot(

            report_date=date(
                2026,
                9,
                29
            ),

            available_at=datetime(
                2026,
                10,
                2,
                19,
                30,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "c" * 64
            ),

            source_record_id=(
                "batch-duplicate"
            ),

            asset_manager_long=
                500_000
        )


        result = (
            storage.store_many(
                [
                    batch_duplicate,
                    batch_duplicate
                ]
            )
        )


        assert (
            result.received
            == 2
        )

        assert (
            result.inserted
            == 1
        )

        assert (
            result.skipped
            == 1
        )


        print(
            "Same-batch duplicate suppression: PASS"
        )


        # ==================================
        # TRANSACTIONAL ROLLBACK
        # ==================================

        rollback_first = make_snapshot(

            report_date=date(
                2026,
                10,
                6
            ),

            available_at=datetime(
                2026,
                10,
                9,
                19,
                30,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "d" * 64
            ),

            source_record_id=(
                "rollback-first"
            ),

            asset_manager_long=
                501_000
        )


        rollback_second = make_snapshot(

            report_date=date(
                2026,
                10,
                13
            ),

            available_at=datetime(
                2026,
                10,
                16,
                19,
                30,
                tzinfo=timezone.utc
            ),

            content_hash=(
                "e" * 64
            ),

            source_record_id=(
                "rollback-second"
            ),

            asset_manager_long=
                502_000
        )


        rows_before_failure = (
            count_test_rows()
        )


        failing_storage = (
            FailingStorage()
        )


        try:

            failing_storage.store_many(
                [
                    rollback_first,
                    rollback_second
                ]
            )

        except RuntimeError as exc:

            assert (
                str(
                    exc
                )
                == (
                    "Intentional batch failure."
                )
            )

        else:

            raise AssertionError(
                "Intentional batch failure "
                "did not propagate."
            )


        rows_after_failure = (
            count_test_rows()
        )


        assert (
            rows_after_failure
            == rows_before_failure
        )


        session = SessionLocal()

        try:

            rollback_count = (
                session.scalar(

                    select(
                        func.count()
                    )

                    .select_from(
                        COTPositioning
                    )

                    .where(

                        COTPositioning.source
                        == TEST_SOURCE,

                        COTPositioning.content_hash
                        .in_(
                            [
                                "d" * 64,
                                "e" * 64
                            ]
                        )
                    )
                )
            )

        finally:

            session.close()


        assert (
            rollback_count
            == 0
        )


        print(
            "Mid-batch rollback: PASS"
        )

        print(
            "No partial persistence: PASS"
        )


        # ==================================
        # TYPE GUARD
        # ==================================

        try:

            storage.store_many(
                [
                    "not-a-snapshot"
                ]
            )

        except TypeError:

            pass

        else:

            raise AssertionError(
                "Non-snapshot storage input "
                "was accepted."
            )


        print(
            "Snapshot type guard: PASS"
        )


        print(
            "\n"
            + "=" * 70
        )

        print(
            "COT STORAGE TEST: PASS"
        )

        print(
            "=" * 70
        )


    finally:

        cleanup()


if __name__ == "__main__":

    main()