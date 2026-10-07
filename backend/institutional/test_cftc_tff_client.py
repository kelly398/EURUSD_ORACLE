from datetime import (
    datetime,
    timezone
)

from urllib.parse import (
    parse_qs,
    urlparse
)

from backend.institutional.cftc_tff_client import (
    CFTCTFFFuturesOnlyClient,
    CFTC_TFF_FUTURES_ONLY_DATASET_ID
)


SAMPLE_ROW = {

    ":created_at":
        "2026-10-02T19:30:08.574Z",

    ":updated_at":
        "2026-10-02T19:30:08.574Z",

    ":id":
        "row-gfhe-9uya_szuq",

    ":version":
        "rv-eq4v.trxe.5pvd",

    "asset_mgr_positions_long":
        "500353",

    "asset_mgr_positions_short":
        "279750",

    "asset_mgr_positions_spread":
        "43567",

    "cftc_contract_market_code":
        "099741",

    "commodity_name":
        "EUROPEAN CURRENCY UNIT",

    "contract_market_name":
        "EURO FX",

    "dealer_positions_long_all":
        "47546",

    "dealer_positions_short_all":
        "259712",

    "dealer_positions_spread_all":
        "7214",

    "futonly_or_combined":
        "FutOnly",

    "id":
        "260929099741F",

    "lev_money_positions_long":
        "124857",

    "lev_money_positions_short":
        "164122",

    "lev_money_positions_spread":
        "19175",

    "market_and_exchange_names":
        (
            "EURO FX - "
            "CHICAGO MERCANTILE EXCHANGE"
        ),

    "nonrept_positions_long_all":
        "80065",

    "nonrept_positions_short_all":
        "57386",

    "open_interest_all":
        "853959",

    "other_rept_positions_long":
        "27547",

    "other_rept_positions_short":
        "19398",

    "other_rept_positions_spread":
        "3635",

    "report_date_as_yyyy_mm_dd":
        "2026-09-29T00:00:00.000"
}


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "CFTC TFF CLIENT TEST"
    )

    print(
        "=" * 70
    )


    client = (
        CFTCTFFFuturesOnlyClient()
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
        client.normalize_row(

            row=SAMPLE_ROW,

            ingested_at=
                ingested_at
        )
    )


    assert (
        snapshot
        .cftc_contract_market_code
        == "099741"
    )

    assert (
        snapshot.source_record_id
        == "260929099741F"
    )

    assert (
        snapshot.commodity_name
        == "EUROPEAN CURRENCY UNIT"
    )

    assert (
        snapshot.open_interest
        == 853_959
    )

    assert (
        snapshot.dealer_long
        == 47_546
    )

    assert (
        snapshot.dealer_short
        == 259_712
    )

    assert (
        snapshot.asset_manager_long
        == 500_353
    )

    assert (
        snapshot.asset_manager_short
        == 279_750
    )

    assert (
        snapshot.leveraged_money_long
        == 124_857
    )

    assert (
        snapshot.leveraged_money_short
        == 164_122
    )


    print(
        "\nOfficial field mapping: PASS"
    )

    print(
        "Leading-zero contract code: PASS"
    )


    # PRE row became visible a few seconds
    # after the scheduled publication time.
    assert (
        snapshot.available_at
        == datetime(
            2026,
            10,
            2,
            19,
            30,
            8,
            574000,
            tzinfo=timezone.utc
        )
    )


    print(
        "PRE availability provenance: PASS"
    )


    assert (
        len(
            snapshot.content_hash
        )
        == 64
    )


    reversed_row = dict(
        reversed(
            list(
                SAMPLE_ROW.items()
            )
        )
    )


    reversed_snapshot = (
        client.normalize_row(

            row=reversed_row,

            ingested_at=
                ingested_at
        )
    )


    assert (
        reversed_snapshot.content_hash
        == snapshot.content_hash
    )


    print(
        "Deterministic content hash: PASS"
    )


    # ==================================
    # METADATA-ONLY UPDATE
    # ==================================
    #
    # Simulates a Socrata dataset-wide
    # replacement where the COT observation
    # itself did not change.
    # ==================================

    metadata_only = dict(
        SAMPLE_ROW
    )

    metadata_only[
        ":updated_at"
    ] = (
        "2026-10-05T12:00:00.000Z"
    )

    metadata_snapshot = (
        client.normalize_row(

            row=metadata_only,

            ingested_at=
                ingested_at
        )
    )


    assert (
        metadata_snapshot.content_hash
        == snapshot.content_hash
    )

    assert (
        metadata_snapshot.available_at
        == datetime(
            2026,
            10,
            5,
            12,
            0,
            tzinfo=timezone.utc
        )
    )


    print(
        "Metadata excluded from content hash: PASS"
    )


    # ==================================
    # TRUE CONTENT REVISION
    # ==================================

    corrected = dict(
        SAMPLE_ROW
    )

    corrected[
        "asset_mgr_positions_long"
    ] = "500999"

    corrected[
        ":updated_at"
    ] = (
        "2026-10-05T12:00:00.000Z"
    )


    corrected_snapshot = (
        client.normalize_row(

            row=corrected,

            ingested_at=
                ingested_at
        )
    )


    assert (
        corrected_snapshot.content_hash
        != snapshot.content_hash
    )

    assert (
        corrected_snapshot.available_at
        == datetime(
            2026,
            10,
            5,
            12,
            0,
            tzinfo=timezone.utc
        )
    )


    print(
        "True revision detection: PASS"
    )


    # ==================================
    # 2025 SHUTDOWN
    # ==================================

    shutdown_row = dict(
        SAMPLE_ROW
    )

    shutdown_row[
        "id"
    ] = "250930099741F"

    shutdown_row[
        "report_date_as_yyyy_mm_dd"
    ] = (
        "2025-09-30T00:00:00.000"
    )

    shutdown_row[
        ":created_at"
    ] = (
        "2025-11-19T20:30:44.459Z"
    )

    shutdown_row[
        ":updated_at"
    ] = (
        "2025-11-19T20:30:44.459Z"
    )


    shutdown_snapshot = (
        client.normalize_row(

            row=shutdown_row,

            ingested_at=
                ingested_at
        )
    )


    assert (
        shutdown_snapshot.available_at
        == datetime(
            2025,
            11,
            19,
            20,
            30,
            44,
            459000,
            tzinfo=timezone.utc
        )
    )


    print(
        "2025 shutdown anti-lookahead: PASS"
    )


    # ==================================
    # OLDER REPORT WITHOUT CALENDAR
    # ==================================

    older_row = dict(
        SAMPLE_ROW
    )

    older_row[
        "id"
    ] = "240604099741F"

    older_row[
        "report_date_as_yyyy_mm_dd"
    ] = (
        "2024-06-04T00:00:00.000"
    )

    older_row[
        ":created_at"
    ] = (
        "2024-06-07T19:30:10.000Z"
    )

    older_row[
        ":updated_at"
    ] = (
        "2024-06-07T19:30:10.000Z"
    )


    older_snapshot = (
        client.normalize_row(

            row=older_row,

            ingested_at=
                ingested_at
        )
    )


    assert (
        older_snapshot.available_at
        == datetime(
            2024,
            6,
            7,
            19,
            30,
            10,
            tzinfo=timezone.utc
        )
    )


    print(
        "PRE historical fallback: PASS"
    )

    # ==================================
    # 2023 ION PUBLICATION DELAY
    # ==================================
    #
    # PRE provenance observed for the
    # 2023-01-31 report:
    #
    # created_at:
    # 2023-02-24T20:30:04.153Z
    #
    # This report was delayed during the
    # ION cyber-related reporting incident.
    # ==================================

    ion_row = dict(
        SAMPLE_ROW
    )

    ion_row[
        "id"
    ] = "230131099741F"

    ion_row[
        "report_date_as_yyyy_mm_dd"
    ] = (
        "2023-01-31T00:00:00.000"
    )

    ion_row[
        ":created_at"
    ] = (
        "2023-02-24T20:30:04.153Z"
    )

    ion_row[
        ":updated_at"
    ] = (
        "2023-02-24T20:30:04.153Z"
    )


    ion_snapshot = (
        client.normalize_row(

            row=ion_row,

            ingested_at=
                ingested_at
        )
    )


    assert (
        ion_snapshot.available_at
        == datetime(
            2023,
            2,
            24,
            20,
            30,
            4,
            153000,
            tzinfo=timezone.utc
        )
    )


    print(
        "2023 ION delayed-release provenance: PASS"
    )

    # ==================================
    # QUERY CONTRACT
    # ==================================

    url = client.build_url(

        start_report_date=
            snapshot.report_date,

        end_report_date=
            snapshot.report_date,

        limit=25
    )


    parsed = urlparse(
        url
    )

    query = parse_qs(
        parsed.query
    )


    assert (
        CFTC_TFF_FUTURES_ONLY_DATASET_ID
        in parsed.path
    )

    assert (
        query["$select"]
        == [
            ":*,*"
        ]
    )

    assert (
        query["$limit"]
        == [
            "25"
        ]
    )

    assert (
        query["$order"]
        == [
            (
                "report_date_as_yyyy_mm_dd "
                "ASC"
            )
        ]
    )

    assert (
        "099741"
        in query["$where"][0]
    )

    assert (
        "FutOnly"
        in query["$where"][0]
    )


    print(
        "PRE system-field query contract: PASS"
    )


    # ==================================
    # WRONG CONTRACT
    # ==================================

    wrong_contract = dict(
        SAMPLE_ROW
    )

    wrong_contract[
        "cftc_contract_market_code"
    ] = "099742"


    try:

        client.normalize_row(
            wrong_contract,
            ingested_at
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Wrong CFTC contract "
            "was accepted."
        )


    print(
        "Contract identity guard: PASS"
    )


    # ==================================
    # WRONG REPORT FAMILY
    # ==================================

    wrong_family = dict(
        SAMPLE_ROW
    )

    wrong_family[
        "futonly_or_combined"
    ] = "Combined"


    try:

        client.normalize_row(
            wrong_family,
            ingested_at
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Non-Futures-Only row "
            "was accepted."
        )


    print(
        "Report-family guard: PASS"
    )


    # ==================================
    # INVALID SYSTEM TEMPORAL ORDER
    # ==================================

    invalid_system_time = dict(
        SAMPLE_ROW
    )

    invalid_system_time[
        ":updated_at"
    ] = (
        "2026-10-01T12:00:00.000Z"
    )


    try:

        client.normalize_row(
            invalid_system_time,
            ingested_at
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "updated_at before created_at "
            "was accepted."
        )


    print(
        "System timestamp ordering guard: PASS"
    )


    # ==================================
    # NAIVE INGESTION TIMESTAMP
    # ==================================

    try:

        client.normalize_row(

            SAMPLE_ROW,

            datetime(
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
        "Ingestion timestamp guard: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "CFTC TFF CLIENT TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()