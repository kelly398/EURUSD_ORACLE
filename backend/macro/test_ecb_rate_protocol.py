from backend.macro.ecb_rate_protocol import (
    ECB_CENTRAL_BANK,
    ECB_RATE_ACTION_FIELD,
    ECB_RATE_ALLOWED_ACTIONS,
    ECB_RATE_AVAILABLE_AT_FIELD,
    ECB_RATE_CONTENT_HASH_EXCLUDED_FIELDS,
    ECB_RATE_CONTENT_HASH_FIELDS,
    ECB_RATE_REFERENCE_DATE_FIELD,
    ECB_RATE_REQUIRED_COLUMNS,
    ECB_RATE_SERIES,
    ECB_RATE_SOURCE,
    ECB_RATE_SOURCE_RECORD_ID_RULE,
    ECB_RATE_TLS_CA_PROVIDER,
    ECB_RATE_TLS_VERIFICATION_REQUIRED,
    ECB_RATE_UNVERIFIED_TLS_ALLOWED,
    ECB_RATE_VALID_TO_FIELD,
    ECB_RATE_VALUE_FIELD,
    ECB_REQUEST_FORMAT,
    ECB_REQUEST_INCLUDE_HISTORY,
    ECB_SDMX_DATAFLOW_ID,
    ECB_SDMX_RATE_BASE_URL,
    build_ecb_rate_response_key,
    build_ecb_rate_source_record_id
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
        "ECB RATE ACQUISITION PROTOCOL TEST"
    )

    print("=" * 70)


    # ========================================================
    # OFFICIAL SOURCE
    # ========================================================

    assert (
        ECB_RATE_SOURCE
        == "ECB_SDMX_FM"
    )

    assert (
        ECB_CENTRAL_BANK
        == "ECB"
    )

    assert (
        ECB_SDMX_RATE_BASE_URL
        == (
            "https://data-api.ecb.europa.eu/"
            "service/data/FM/"
        )
    )

    assert (
        ECB_REQUEST_INCLUDE_HISTORY
        is True
    )

    assert (
        ECB_REQUEST_FORMAT
        == "csvdata"
    )


    print(
        "\nOfficial ECB SDMX source: PASS"
    )


    # ========================================================
    # THREE OFFICIAL POLICY-RATE SERIES
    # ========================================================

    expected_series = (

        (
            "deposit_facility_rate",
            "DFR",
            "B.U2.EUR.4F.KR.DFR.LEV"
        ),

        (
            "main_refinancing_operations_rate",
            "MRR_FR",
            "B.U2.EUR.4F.KR.MRR_FR.LEV"
        ),

        (
            "marginal_lending_facility_rate",
            "MLFR",
            "B.U2.EUR.4F.KR.MLFR.LEV"
        )
    )


    assert tuple(

        (
            item.rate_name,
            item.provider_series_id,
            item.series_key
        )

        for item
        in ECB_RATE_SERIES

    ) == expected_series


    print(
        "Three official key-rate series: PASS"
    )


    # ========================================================
    # CAUSAL FIELD MAPPING
    # ========================================================

    assert (
        ECB_RATE_REFERENCE_DATE_FIELD
        == "TIME_PERIOD"
    )

    assert (
        ECB_RATE_AVAILABLE_AT_FIELD
        == "VALID_FROM"
    )

    assert (
        ECB_RATE_VALUE_FIELD
        == "OBS_VALUE"
    )

    assert (
        ECB_RATE_ACTION_FIELD
        == "ACTION"
    )

    assert (
        ECB_RATE_VALID_TO_FIELD
        == "VALID_TO"
    )


    print(
        "Causal field mapping: PASS"
    )


    # ========================================================
    # FAIL-CLOSED SOURCE SEMANTICS
    # ========================================================

    assert (
        ECB_RATE_ALLOWED_ACTIONS
        == (
            "Replace",
        )
    )


    assert (
        ECB_RATE_REQUIRED_COLUMNS
        == (
            "KEY",
            "TIME_PERIOD",
            "OBS_VALUE",
            "ACTION",
            "VALID_FROM",
            "VALID_TO"
        )
    )


    print(
        "Fail-closed source semantics: PASS"
    )


    # ========================================================
    # REQUEST KEY VS OFFICIAL RESPONSE KEY
    # ========================================================

    assert (
        ECB_SDMX_DATAFLOW_ID
        == "FM"
    )


    response_key = (
        build_ecb_rate_response_key(

            "B.U2.EUR.4F.KR.DFR.LEV"
        )
    )


    assert (
        response_key
        == (
            "FM.B.U2.EUR.4F.KR.DFR.LEV"
        )
    )


    assert_value_error(

        lambda:
            build_ecb_rate_response_key(
                ""
            )
    )


    print(
        "Official SDMX response key: PASS"
    )


    # ========================================================
    # LOGICAL RECORD IDENTITY
    # ========================================================

    source_record_id = (
        build_ecb_rate_source_record_id(

            series_key=
                response_key,

            time_period=(
                "2026-09-16"
            )
        )
    )


    assert (
        source_record_id
        == (
            "FM.B.U2.EUR.4F.KR.DFR.LEV"
            "|2026-09-16"
        )
    )


    assert (
        ECB_RATE_SOURCE_RECORD_ID_RULE
        == "KEY|TIME_PERIOD"
    )


    assert_value_error(

        lambda:
            build_ecb_rate_source_record_id(
                "",
                "2026-09-16"
            )
    )


    assert_value_error(

        lambda:
            build_ecb_rate_source_record_id(
                (
                    "FM.B.U2.EUR.4F.KR.DFR.LEV"
                ),
                ""
            )
    )


    print(
        "Stable logical record identity: PASS"
    )


    # ========================================================
    # ECONOMIC CONTENT HASH CONTRACT
    # ========================================================

    assert (
        ECB_RATE_CONTENT_HASH_FIELDS
        == (
            "central_bank",
            "rate_name",
            "series_key",
            "reference_date",
            "value_percent",
            "source"
        )
    )


    for forbidden in (
        "VALID_FROM",
        "VALID_TO",
        "ingested_at",
        "http_last_modified",
        "http_date",
        "http_etag"
    ):

        assert (
            forbidden
            in ECB_RATE_CONTENT_HASH_EXCLUDED_FIELDS
        )


    print(
        "Economic content-hash boundary: PASS"
    )


    # ========================================================
    # TLS CONTRACT
    # ========================================================

    assert (
        ECB_RATE_TLS_CA_PROVIDER
        == "certifi"
    )

    assert (
        ECB_RATE_TLS_VERIFICATION_REQUIRED
        is True
    )

    assert (
        ECB_RATE_UNVERIFIED_TLS_ALLOWED
        is False
    )


    print(
        "Verified TLS contract: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "ECB RATE ACQUISITION "
        "PROTOCOL TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()