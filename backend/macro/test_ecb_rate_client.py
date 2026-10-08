from datetime import (
    date,
    datetime,
    timezone
)

from urllib.parse import (
    parse_qs,
    urlparse
)

from backend.macro.ecb_rate_client import (
    ECBRateClient
)

from backend.macro.ecb_rate_protocol import (
    ECB_RATE_SERIES,
    ECB_RATE_SOURCE
)


# ============================================================
# REQUEST KEYS
# ============================================================
#
# These are used in:
#
#   /service/data/FM/<REQUEST KEY>
#
# They DO NOT contain the "FM." dataflow prefix.
# ============================================================

DFR_KEY = (
    "B.U2.EUR.4F.KR.DFR.LEV"
)

MRR_KEY = (
    "B.U2.EUR.4F.KR.MRR_FR.LEV"
)

MLFR_KEY = (
    "B.U2.EUR.4F.KR.MLFR.LEV"
)


# ============================================================
# OFFICIAL RESPONSE KEYS
# ============================================================
#
# ECB CSV rows return KEY values with the FM dataflow prefix.
# ============================================================

DFR_RESPONSE_KEY = (
    "FM.B.U2.EUR.4F.KR.DFR.LEV"
)

MRR_RESPONSE_KEY = (
    "FM.B.U2.EUR.4F.KR.MRR_FR.LEV"
)

MLFR_RESPONSE_KEY = (
    "FM.B.U2.EUR.4F.KR.MLFR.LEV"
)


INGESTED_AT = datetime(
    2026,
    10,
    8,
    0,
    0,
    tzinfo=timezone.utc
)


def make_row(
    **overrides
):

    row = {

        "KEY":
            DFR_RESPONSE_KEY,

        "TIME_PERIOD":
            "2026-09-16",

        "OBS_VALUE":
            "2.5",

        "ACTION":
            "Replace",

        "VALID_FROM":
            (
                "2026-09-10T"
                "16:22:33.000+02:00"
            ),

        "VALID_TO":
            ""
    }


    row.update(
        overrides
    )


    return row


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


class FakeResponse:

    def __init__(
        self,
        payload
    ):

        self._payload = payload


    def __enter__(
        self
    ):

        return self


    def __exit__(
        self,
        exc_type,
        exc,
        traceback
    ):

        return False


    def read(
        self
    ):

        return self._payload


def main():

    print("=" * 70)

    print(
        "EUR/USD ORACLE "
        "ECB RATE CLIENT TEST"
    )

    print("=" * 70)


    client = ECBRateClient()


    # ========================================================
    # VERIFIED TLS
    # ========================================================

    assert (
        client.ssl_context.check_hostname
        is True
    )

    assert (
        client.ssl_context.verify_mode
        != 0
    )


    print(
        "\nVerified TLS context: PASS"
    )


    # ========================================================
    # URL CONTRACT
    # ========================================================
    #
    # build_url receives the REQUEST key,
    # without the FM. response prefix.
    # ========================================================

    url = client.build_url(

        series_key=
            DFR_KEY,

        start_date=date(
            2026,
            6,
            1
        ),

        end_date=date(
            2026,
            9,
            30
        )
    )


    parsed = urlparse(
        url
    )

    query = parse_qs(
        parsed.query
    )


    assert (
        parsed.path.endswith(
            "/service/data/FM/"
            + DFR_KEY
        )
    )

    assert (
        query["startPeriod"]
        == [
            "2026-06-01"
        ]
    )

    assert (
        query["endPeriod"]
        == [
            "2026-09-30"
        ]
    )

    assert (
        query["includeHistory"]
        == [
            "true"
        ]
    )

    assert (
        query["format"]
        == [
            "csvdata"
        ]
    )


    print(
        "Frozen SDMX URL contract: PASS"
    )


    # ========================================================
    # OBSERVED ECB DFR ROW
    # ========================================================
    #
    # expected_series_key is the REQUEST key.
    #
    # row["KEY"] is the official RESPONSE key.
    # ========================================================

    snapshot = client.normalize_row(

        row=
            make_row(),

        rate_name=(
            "deposit_facility_rate"
        ),

        expected_series_key=
            DFR_KEY,

        ingested_at=
            INGESTED_AT
    )


    assert (
        snapshot.central_bank
        == "ECB"
    )

    assert (
        snapshot.rate_name
        == "deposit_facility_rate"
    )

    assert (
        snapshot.reference_date
        == date(
            2026,
            9,
            16
        )
    )

    assert (
        snapshot.available_at
        == datetime(
            2026,
            9,
            10,
            14,
            22,
            33,
            tzinfo=timezone.utc
        )
    )

    assert (
        snapshot.value_percent
        == 2.5
    )

    assert (
        snapshot.source_record_id
        == (
            DFR_RESPONSE_KEY
            + "|2026-09-16"
        )
    )

    assert (
        snapshot.source
        == ECB_RATE_SOURCE
    )

    assert (
        len(
            snapshot.content_hash
        )
        == 64
    )


    print(
        "Observed DFR normalization: PASS"
    )

    print(
        "VALID_FROM UTC normalization: PASS"
    )


    # ========================================================
    # CONTENT HASH EXCLUDES PROVENANCE
    # ========================================================

    rediscovered = client.normalize_row(

        row=
            make_row(

                VALID_FROM=(
                    "2026-09-10T"
                    "18:00:00+02:00"
                )
            ),

        rate_name=(
            "deposit_facility_rate"
        ),

        expected_series_key=
            DFR_KEY,

        ingested_at=datetime(
            2026,
            10,
            9,
            tzinfo=timezone.utc
        )
    )


    assert (
        rediscovered.content_hash
        == snapshot.content_hash
    )


    print(
        "Provenance excluded from hash: PASS"
    )


    # ========================================================
    # CANONICAL NUMERIC CONTENT
    # ========================================================

    alternate_format = (
        client.normalize_row(

            row=
                make_row(
                    OBS_VALUE="2.5000"
                ),

            rate_name=(
                "deposit_facility_rate"
            ),

            expected_series_key=
                DFR_KEY,

            ingested_at=
                INGESTED_AT
        )
    )


    assert (
        alternate_format.content_hash
        == snapshot.content_hash
    )


    print(
        "Canonical numeric hashing: PASS"
    )


    # ========================================================
    # TRUE ECONOMIC REVISION
    # ========================================================

    changed_value = (
        client.normalize_row(

            row=
                make_row(
                    OBS_VALUE="2.25"
                ),

            rate_name=(
                "deposit_facility_rate"
            ),

            expected_series_key=
                DFR_KEY,

            ingested_at=
                INGESTED_AT
        )
    )


    assert (
        changed_value.content_hash
        != snapshot.content_hash
    )


    print(
        "True economic revision hash: PASS"
    )


    # ========================================================
    # FAIL-CLOSED SOURCE SEMANTICS
    # ========================================================

    assert_value_error(

        lambda:
            client.normalize_row(

                row=
                    make_row(
                        ACTION="Delete"
                    ),

                rate_name=(
                    "deposit_facility_rate"
                ),

                expected_series_key=
                    DFR_KEY,

                ingested_at=
                    INGESTED_AT
            )
    )


    assert_value_error(

        lambda:
            client.normalize_row(

                row=
                    make_row(
                        VALID_TO=(
                            "2026-09-11T"
                            "00:00:00+02:00"
                        )
                    ),

                rate_name=(
                    "deposit_facility_rate"
                ),

                expected_series_key=
                    DFR_KEY,

                ingested_at=
                    INGESTED_AT
            )
    )


    assert_value_error(

        lambda:
            client.normalize_row(

                row=
                    make_row(
                        VALID_FROM=""
                    ),

                rate_name=(
                    "deposit_facility_rate"
                ),

                expected_series_key=
                    DFR_KEY,

                ingested_at=
                    INGESTED_AT
            )
    )


    assert_value_error(

        lambda:
            client.normalize_row(

                row=
                    make_row(
                        OBS_VALUE=""
                    ),

                rate_name=(
                    "deposit_facility_rate"
                ),

                expected_series_key=
                    DFR_KEY,

                ingested_at=
                    INGESTED_AT
            )
    )


    # Wrong official response KEY for a DFR request.
    assert_value_error(

        lambda:
            client.normalize_row(

                row=
                    make_row(
                        KEY=
                            MRR_RESPONSE_KEY
                    ),

                rate_name=(
                    "deposit_facility_rate"
                ),

                expected_series_key=
                    DFR_KEY,

                ingested_at=
                    INGESTED_AT
            )
    )


    print(
        "Unknown action fail-closed: PASS"
    )

    print(
        "Closed-version fail-closed: PASS"
    )

    print(
        "Required-value guards: PASS"
    )

    print(
        "Series identity guard: PASS"
    )


    # ========================================================
    # INGESTION TIMESTAMP CONTRACT
    # ========================================================

    assert_value_error(

        lambda:
            client.normalize_row(

                row=
                    make_row(),

                rate_name=(
                    "deposit_facility_rate"
                ),

                expected_series_key=
                    DFR_KEY,

                ingested_at=datetime(
                    2026,
                    10,
                    8
                )
            )
    )


    assert_type_error(

        lambda:
            client.normalize_row(

                row=
                    make_row(),

                rate_name=(
                    "deposit_facility_rate"
                ),

                expected_series_key=
                    DFR_KEY,

                ingested_at=(
                    "2026-10-08"
                )
            )
    )


    print(
        "Explicit causal ingestion time: PASS"
    )


    # ========================================================
    # CSV HEADER CONTRACT
    # ========================================================

    bad_csv = (

        "KEY,TIME_PERIOD,OBS_VALUE\n"
        f"{DFR_RESPONSE_KEY},"
        "2026-09-16,"
        "2.5\n"

    ).encode(
        "utf-8"
    )


    assert_value_error(

        lambda:
            client._parse_csv(
                bad_csv
            )
    )


    print(
        "Required CSV columns: PASS"
    )


    # ========================================================
    # DETERMINISTIC TRANSPORT TEST
    # ========================================================

    csv_payload = (

        "KEY,TIME_PERIOD,OBS_VALUE,"
        "ACTION,VALID_FROM,VALID_TO\n"

        f"{DFR_RESPONSE_KEY},"
        "2026-06-17,"
        "2.25,"
        "Replace,"
        "2026-06-11T14:38:45.000+02:00,"
        "\n"

        f"{DFR_RESPONSE_KEY},"
        "2026-09-16,"
        "2.5,"
        "Replace,"
        "2026-09-10T16:22:33.000+02:00,"
        "\n"

    ).encode(
        "utf-8"
    )


    calls = []


    def fake_opener(
        request,
        timeout,
        context
    ):

        calls.append({

            "url":
                request.full_url,

            "timeout":
                timeout,

            "context":
                context
        })


        return FakeResponse(
            csv_payload
        )


    deterministic_client = (
        ECBRateClient(
            timeout_seconds=17,
            opener=fake_opener
        )
    )


    snapshots = (
        deterministic_client
        .fetch_series_snapshots(

            series_key=
                DFR_KEY,

            start_date=date(
                2026,
                6,
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
            calls
        )
        == 1
    )

    assert (
        calls[0]["timeout"]
        == 17.0
    )

    assert (
        calls[0]["context"]
        is deterministic_client.ssl_context
    )

    assert (
        len(
            snapshots
        )
        == 2
    )

    assert (
        snapshots[0].reference_date
        == date(
            2026,
            6,
            17
        )
    )

    assert (
        snapshots[0].value_percent
        == 2.25
    )

    assert (
        snapshots[1].reference_date
        == date(
            2026,
            9,
            16
        )
    )

    assert (
        snapshots[1].value_percent
        == 2.5
    )

    assert (
        snapshots[0].source_record_id
        == (
            DFR_RESPONSE_KEY
            + "|2026-06-17"
        )
    )

    assert (
        snapshots[1].source_record_id
        == (
            DFR_RESPONSE_KEY
            + "|2026-09-16"
        )
    )


    print(
        "Deterministic transport injection: PASS"
    )

    print(
        "CSV-to-snapshot pipeline: PASS"
    )


    # ========================================================
    # FROZEN THREE-SERIES REQUEST MAP
    # ========================================================

    assert tuple(

        item.series_key

        for item
        in ECB_RATE_SERIES

    ) == (
        DFR_KEY,
        MRR_KEY,
        MLFR_KEY
    )


    print(
        "Three-series client coverage: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "ECB RATE CLIENT TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()