from datetime import (
    date,
    datetime,
    timezone
)

from backend.macro.ecb_calendar_protocol import (
    ECB_2026_CALENDAR_AVAILABLE_AT,
    ECB_2026_CALENDAR_PUBLICATION_DATE,
    ECB_2026_POLICY_MEETINGS,
    ECB_CALENDAR_SOURCE,
    ECB_CALENDAR_TIMEZONE,
    ECB_POLICY_DECISION_TIME_RULE_START,
    ECB_POLICY_EVENT_CONTENT_HASH_EXCLUDED_FIELDS,
    ECB_POLICY_EVENT_CONTENT_HASH_FIELDS,
    ECB_POLICY_EVENT_CURRENCY,
    ECB_POLICY_EVENT_ID_RULE,
    ECB_POLICY_EVENT_JURISDICTION,
    ECB_POLICY_EVENT_NAME,
    build_ecb_decision_scheduled_at,
    build_ecb_policy_event_id,
    conservative_date_only_available_at
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


def main():

    print("=" * 70)

    print(
        "EUR/USD ORACLE "
        "ECB CALENDAR PROTOCOL TEST"
    )

    print("=" * 70)


    # ========================================================
    # OFFICIAL SOURCE CONTRACT
    # ========================================================

    assert (
        ECB_CALENDAR_SOURCE
        == (
            "ECB_GOVERNING_COUNCIL_CALENDAR"
        )
    )

    assert (
        ECB_CALENDAR_TIMEZONE
        == "Europe/Berlin"
    )


    print(
        "\nOfficial ECB calendar source: PASS"
    )


    # ========================================================
    # DATE-ONLY PUBLICATION PROVENANCE
    # ========================================================

    assert (
        ECB_2026_CALENDAR_PUBLICATION_DATE
        == date(
            2025,
            4,
            24
        )
    )


    assert (
        ECB_2026_CALENDAR_AVAILABLE_AT
        == datetime(
            2025,
            4,
            24,
            22,
            0,
            tzinfo=timezone.utc
        )
    )


    assert (
        conservative_date_only_available_at(

            date(
                2025,
                4,
                24
            )

        )
        == ECB_2026_CALENDAR_AVAILABLE_AT
    )


    assert_type_error(

        lambda:
            conservative_date_only_available_at(
                "2025-04-24"
            )
    )


    print(
        "Conservative publication boundary: PASS"
    )


    # ========================================================
    # POLICY-TIME RULE
    # ========================================================

    assert (
        ECB_POLICY_DECISION_TIME_RULE_START
        == date(
            2022,
            7,
            21
        )
    )


    # February is CET / UTC+1.
    assert (
        build_ecb_decision_scheduled_at(

            date(
                2026,
                2,
                5
            )

        )
        == datetime(
            2026,
            2,
            5,
            13,
            15,
            tzinfo=timezone.utc
        )
    )


    # September is CEST / UTC+2.
    assert (
        build_ecb_decision_scheduled_at(

            date(
                2026,
                9,
                10
            )

        )
        == datetime(
            2026,
            9,
            10,
            12,
            15,
            tzinfo=timezone.utc
        )
    )


    # December is back to CET / UTC+1.
    assert (
        build_ecb_decision_scheduled_at(

            date(
                2026,
                12,
                17
            )

        )
        == datetime(
            2026,
            12,
            17,
            13,
            15,
            tzinfo=timezone.utc
        )
    )


    assert_value_error(

        lambda:
            build_ecb_decision_scheduled_at(

                date(
                    2022,
                    7,
                    20
                )
            )
    )


    assert_type_error(

        lambda:
            build_ecb_decision_scheduled_at(
                "2026-09-10"
            )
    )


    print(
        "14:15 Europe/Berlin scheduling: PASS"
    )

    print(
        "DST-aware UTC conversion: PASS"
    )


    # ========================================================
    # OFFICIAL 2026 MEETING DATES
    # ========================================================

    assert tuple(

        item.meeting_date

        for item
        in ECB_2026_POLICY_MEETINGS

    ) == (

        date(
            2026,
            2,
            5
        ),

        date(
            2026,
            3,
            19
        ),

        date(
            2026,
            4,
            30
        ),

        date(
            2026,
            6,
            11
        ),

        date(
            2026,
            7,
            23
        ),

        date(
            2026,
            9,
            10
        ),

        date(
            2026,
            10,
            29
        ),

        date(
            2026,
            12,
            17
        )
    )


    assert tuple(

        item.ordinal

        for item
        in ECB_2026_POLICY_MEETINGS

    ) == (
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8
    )


    print(
        "Official 2026 meeting dates: PASS"
    )


    # ========================================================
    # STABLE EVENT IDENTITY
    # ========================================================

    assert (
        build_ecb_policy_event_id(
            2026,
            1
        )
        == (
            "ECB|MONETARY_POLICY_DECISION|"
            "2026|01"
        )
    )


    assert (
        build_ecb_policy_event_id(
            2026,
            8
        )
        == (
            "ECB|MONETARY_POLICY_DECISION|"
            "2026|08"
        )
    )


    assert (
        ECB_POLICY_EVENT_ID_RULE
        == (
            "ECB|MONETARY_POLICY_DECISION|"
            "YEAR|ORDINAL"
        )
    )


    assert_value_error(

        lambda:
            build_ecb_policy_event_id(
                0,
                1
            )
    )


    assert_value_error(

        lambda:
            build_ecb_policy_event_id(
                2026,
                0
            )
    )


    print(
        "Stable meeting identity: PASS"
    )


    # ========================================================
    # EVENT SEMANTICS
    # ========================================================

    assert (
        ECB_POLICY_EVENT_NAME
        == (
            "ECB Monetary Policy Decision"
        )
    )

    assert (
        ECB_POLICY_EVENT_JURISDICTION
        == "EURO_AREA"
    )

    assert (
        ECB_POLICY_EVENT_CURRENCY
        == "EUR"
    )


    print(
        "ECB calendar event semantics: PASS"
    )


    # ========================================================
    # VERSION HASH BOUNDARY
    # ========================================================

    assert (
        ECB_POLICY_EVENT_CONTENT_HASH_FIELDS
        == (
            "source_event_id",
            "event_name",
            "jurisdiction",
            "currency",
            "scheduled_at",
            "actual",
            "forecast",
            "previous",
            "unit",
            "source"
        )
    )


    for forbidden in (

        "available_at",
        "ingested_at",
        "publication_date",
        "http_last_modified",
        "http_date",
        "http_etag"
    ):

        assert (
            forbidden
            in (
                ECB_POLICY_EVENT_CONTENT_HASH_EXCLUDED_FIELDS
            )
        )


    print(
        "Calendar content-hash boundary: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "ECB CALENDAR PROTOCOL TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()