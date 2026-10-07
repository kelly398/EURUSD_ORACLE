from datetime import (
    date,
    datetime,
    timezone
)

from backend.institutional.cftc_cot_backfill import (
    CFTCCOTBackfill
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot,
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)

from backend.institutional.cot_storage import (
    COTStorageResult
)


def make_snapshot(
    ingested_at
):

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

        report_date=date(
            2025,
            9,
            30
        ),

        available_at=datetime(
            2025,
            11,
            19,
            20,
            30,
            44,
            459000,
            tzinfo=timezone.utc
        ),

        open_interest=
            800_000,

        dealer_long=
            50_000,

        dealer_short=
            250_000,

        dealer_spread=
            8_000,

        asset_manager_long=
            490_000,

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

        source_record_id=(
            "250930099741F"
        ),

        content_hash=(
            "a" * 64
        ),

        source="CFTC",

        ingested_at=
            ingested_at
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
        start_report_date,
        end_report_date,
        ingested_at,
        limit
    ):

        self.calls.append({

            "start_report_date":
                start_report_date,

            "end_report_date":
                end_report_date,

            "ingested_at":
                ingested_at,

            "limit":
                limit
        })


        return self.snapshots


class FakeStorage:

    def __init__(
        self
    ):

        self.received = None


    def store_many(
        self,
        snapshots
    ):

        self.received = tuple(
            snapshots
        )


        return COTStorageResult(

            received=
                len(
                    self.received
                ),

            inserted=
                len(
                    self.received
                ),

            skipped=0
        )


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "CFTC COT BACKFILL TEST"
    )

    print(
        "=" * 70
    )


    ingested_at = datetime(
        2026,
        10,
        7,
        12,
        0,
        tzinfo=timezone.utc
    )


    snapshot = (
        make_snapshot(
            ingested_at
        )
    )


    client = (
        FakeClient(
            [
                snapshot
            ]
        )
    )

    storage = (
        FakeStorage()
    )


    backfill = (
        CFTCCOTBackfill(
            client=client,
            storage=storage
        )
    )


    result = backfill.run(

        start_report_date=date(
            2025,
            9,
            30
        ),

        end_report_date=date(
            2025,
            9,
            30
        ),

        ingested_at=
            ingested_at,

        limit=100
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
        result.ingested_at
        == ingested_at
    )


    assert (
        len(
            client.calls
        )
        == 1
    )

    assert (
        client.calls[0][
            "limit"
        ]
        == 100
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


    # ==================================
    # BAD DATE RANGE
    # ==================================

    try:

        backfill.run(

            start_report_date=date(
                2025,
                10,
                7
            ),

            end_report_date=date(
                2025,
                9,
                30
            ),

            ingested_at=
                ingested_at
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Invalid date range "
            "was accepted."
        )


    print(
        "Date-range guard: PASS"
    )


    # ==================================
    # NAIVE INGESTION TIMESTAMP
    # ==================================

    try:

        backfill.run(

            start_report_date=date(
                2025,
                9,
                30
            ),

            end_report_date=date(
                2025,
                9,
                30
            ),

            ingested_at=datetime(
                2026,
                10,
                7,
                12,
                0
            )
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Naive ingested_at "
            "was accepted."
        )


    print(
        "Timestamp guard: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "CFTC COT BACKFILL TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()