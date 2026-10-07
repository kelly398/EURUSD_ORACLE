from datetime import (
    date,
    datetime,
    timezone
)

from sqlalchemy import (
    delete
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
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)


TEST_SOURCE = (
    "ORACLE_COT_REPOSITORY_TEST"
)


def make_record(
    *,
    report_date,
    available_at,
    content_hash,
    asset_manager_long,
    source_record_id
):

    return COTPositioning(

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
            "EURO FX"
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
            TEST_SOURCE
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


def seed():

    session = SessionLocal()

    try:

        rows = [

            # ==================================
            # REPORT 1
            # ==================================

            make_record(

                report_date=date(
                    2026,
                    9,
                    15
                ),

                available_at=datetime(
                    2026,
                    9,
                    18,
                    19,
                    30,
                    tzinfo=timezone.utc
                ),

                content_hash=(
                    "1" * 64
                ),

                asset_manager_long=
                    480_000,

                source_record_id=(
                    "report-1-original"
                )
            ),


            # ==================================
            # REPORT 2 - ORIGINAL
            # ==================================

            make_record(

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
                    "2" * 64
                ),

                asset_manager_long=
                    490_000,

                source_record_id=(
                    "report-2-original"
                )
            ),


            # ==================================
            # REPORT 2 - LATER CORRECTION
            # ==================================

            make_record(

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
                    "3" * 64
                ),

                asset_manager_long=
                    495_000,

                source_record_id=(
                    "report-2-corrected"
                )
            ),


            # ==================================
            # REPORT 3
            # ==================================

            make_record(

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
                    "4" * 64
                ),

                asset_manager_long=
                    500_000,

                source_record_id=(
                    "report-3-original"
                )
            )
        ]


        session.add_all(
            rows
        )

        session.commit()


    finally:

        session.close()


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "COT REPOSITORY TEST"
    )

    print(
        "=" * 70
    )


    cleanup()


    try:

        seed()


        repository = (
            COTPositioningRepository(
                source=TEST_SOURCE
            )
        )


        # ==================================
        # BEFORE FIRST PUBLICATION
        # ==================================

        before_first = (
            repository.get_latest_as_of(

                datetime(
                    2026,
                    9,
                    18,
                    19,
                    29,
                    59,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            before_first
            is None
        )


        print(
            "\nPre-publication exclusion: PASS"
        )


        # ==================================
        # EXACT PUBLICATION BOUNDARY
        # ==================================

        first_available = (
            repository.get_latest_as_of(

                datetime(
                    2026,
                    9,
                    18,
                    19,
                    30,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            first_available
            is not None
        )

        assert (
            first_available.report_date
            == date(
                2026,
                9,
                15
            )
        )


        print(
            "Exact available_at boundary: PASS"
        )


        # ==================================
        # ORIGINAL VERSION BEFORE REVISION
        # ==================================

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
            before_revision.report_date
            == date(
                2026,
                9,
                22
            )
        )

        assert (
            before_revision
            .source_record_id
            == "report-2-original"
        )

        assert (
            before_revision
            .asset_manager_long
            == 490_000
        )


        print(
            "Future revision isolation: PASS"
        )


        # ==================================
        # CORRECTED VERSION AFTER REVISION
        # ==================================

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
            .source_record_id
            == "report-2-corrected"
        )

        assert (
            after_revision
            .asset_manager_long
            == 495_000
        )


        print(
            "Revision activation: PASS"
        )


        # ==================================
        # FUTURE REPORT EXCLUSION
        # ==================================

        still_report_two = (
            repository.get_latest_as_of(

                datetime(
                    2026,
                    10,
                    2,
                    19,
                    29,
                    59,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            still_report_two.report_date
            == date(
                2026,
                9,
                22
            )
        )


        print(
            "Future report isolation: PASS"
        )


        # ==================================
        # NEXT REPORT ACTIVATION
        # ==================================

        report_three = (
            repository.get_latest_as_of(

                datetime(
                    2026,
                    10,
                    2,
                    19,
                    30,
                    tzinfo=timezone.utc
                )
            )
        )


        assert (
            report_three.report_date
            == date(
                2026,
                9,
                29
            )
        )


        print(
            "New report activation: PASS"
        )


        # ==================================
        # CAUSAL HISTORY
        # ==================================

        history_before_revision = (
            repository.get_history_as_of(

                as_of=datetime(
                    2026,
                    9,
                    27,
                    12,
                    0,
                    tzinfo=timezone.utc
                ),

                limit=10
            )
        )


        assert (
            len(
                history_before_revision
            )
            == 2
        )

        assert (
            history_before_revision[0]
            .report_date
            == date(
                2026,
                9,
                15
            )
        )

        assert (
            history_before_revision[1]
            .source_record_id
            == "report-2-original"
        )


        history_after_revision = (
            repository.get_history_as_of(

                as_of=datetime(
                    2026,
                    9,
                    28,
                    14,
                    0,
                    tzinfo=timezone.utc
                ),

                limit=10
            )
        )


        assert (
            len(
                history_after_revision
            )
            == 2
        )

        assert (
            history_after_revision[1]
            .source_record_id
            == "report-2-corrected"
        )


        assert (

            tuple(
                item.report_date
                for item
                in history_after_revision
            )

            == tuple(
                sorted(
                    item.report_date
                    for item
                    in history_after_revision
                )
            )
        )


        print(
            "Revision-aware history: PASS"
        )

        print(
            "Chronological ordering: PASS"
        )


        # ==================================
        # LIMIT
        # ==================================

        limited = (
            repository.get_history_as_of(

                as_of=datetime(
                    2026,
                    10,
                    3,
                    tzinfo=timezone.utc
                ),

                limit=2
            )
        )


        assert (
            len(
                limited
            )
            == 2
        )

        assert (
            limited[0].report_date
            == date(
                2026,
                9,
                22
            )
        )

        assert (
            limited[1].report_date
            == date(
                2026,
                9,
                29
            )
        )


        print(
            "History limit: PASS"
        )


        # ==================================
        # INVALID AS-OF
        # ==================================

        try:

            repository.get_latest_as_of(

                datetime(
                    2026,
                    10,
                    3
                )
            )

        except ValueError:

            pass

        else:

            raise AssertionError(
                "Naive as_of was "
                "incorrectly accepted."
            )


        print(
            "Timezone guard: PASS"
        )


        print(
            "\n"
            + "=" * 70
        )

        print(
            "COT REPOSITORY TEST: PASS"
        )

        print(
            "=" * 70
        )


    finally:

        cleanup()


if __name__ == "__main__":

    main()