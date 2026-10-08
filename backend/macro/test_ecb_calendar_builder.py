from datetime import (
    date,
    datetime,
    timedelta,
    timezone
)

from backend.macro.ecb_calendar_builder import (
    ECBPolicyCalendarBuilder
)

from backend.macro.ecb_calendar_protocol import (
    ECB_2026_CALENDAR_AVAILABLE_AT,
    ECB_CALENDAR_SOURCE,
    ECB_POLICY_EVENT_CURRENCY,
    ECB_POLICY_EVENT_JURISDICTION,
    ECB_POLICY_EVENT_NAME,
    ECBPolicyMeetingSpec
)


INGESTED_AT = datetime(
    2026,
    10,
    8,
    0,
    0,
    tzinfo=timezone.utc
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
        "ECB CALENDAR BUILDER TEST"
    )

    print("=" * 70)


    builder = (
        ECBPolicyCalendarBuilder()
    )


    snapshots = (
        builder.build_2026_snapshots(
            ingested_at=
                INGESTED_AT
        )
    )


    # ========================================================
    # COMPLETE 2026 CALENDAR
    # ========================================================

    assert (
        len(
            snapshots
        )
        == 8
    )


    assert tuple(

        item.source_event_id

        for item
        in snapshots

    ) == (

        "ECB|MONETARY_POLICY_DECISION|2026|01",
        "ECB|MONETARY_POLICY_DECISION|2026|02",
        "ECB|MONETARY_POLICY_DECISION|2026|03",
        "ECB|MONETARY_POLICY_DECISION|2026|04",
        "ECB|MONETARY_POLICY_DECISION|2026|05",
        "ECB|MONETARY_POLICY_DECISION|2026|06",
        "ECB|MONETARY_POLICY_DECISION|2026|07",
        "ECB|MONETARY_POLICY_DECISION|2026|08"
    )


    print(
        "\nEight-event 2026 calendar: PASS"
    )

    print(
        "Stable chronological identity: PASS"
    )


    # ========================================================
    # COMMON EVENT SEMANTICS
    # ========================================================

    assert all(

        item.event_name
        == ECB_POLICY_EVENT_NAME

        for item
        in snapshots
    )


    assert all(

        item.jurisdiction
        == ECB_POLICY_EVENT_JURISDICTION

        for item
        in snapshots
    )


    assert all(

        item.currency
        == ECB_POLICY_EVENT_CURRENCY

        for item
        in snapshots
    )


    assert all(

        item.source
        == ECB_CALENDAR_SOURCE

        for item
        in snapshots
    )


    assert all(

        item.actual
        is None

        and

        item.forecast
        is None

        and

        item.previous
        is None

        and

        item.unit
        is None

        for item
        in snapshots
    )


    print(
        "Calendar event semantics: PASS"
    )


    # ========================================================
    # CONSERVATIVE CAUSAL AVAILABILITY
    # ========================================================

    assert all(

        item.available_at
        == ECB_2026_CALENDAR_AVAILABLE_AT

        for item
        in snapshots
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


    assert all(

        item.available_at
        < item.scheduled_at

        for item
        in snapshots
    )


    print(
        "Conservative availability propagation: PASS"
    )

    print(
        "Pre-scheduled-event visibility: PASS"
    )


    # ========================================================
    # DST-AWARE SCHEDULE
    # ========================================================

    february = snapshots[0]

    september = snapshots[5]

    december = snapshots[7]


    assert (
        february.scheduled_at
        == datetime(
            2026,
            2,
            5,
            13,
            15,
            tzinfo=timezone.utc
        )
    )


    assert (
        september.scheduled_at
        == datetime(
            2026,
            9,
            10,
            12,
            15,
            tzinfo=timezone.utc
        )
    )


    assert (
        december.scheduled_at
        == datetime(
            2026,
            12,
            17,
            13,
            15,
            tzinfo=timezone.utc
        )
    )


    print(
        "DST-aware event scheduling: PASS"
    )


    # ========================================================
    # CONTENT HASH CONTRACT
    # ========================================================

    assert all(

        len(
            item.content_hash
        )
        == 64

        for item
        in snapshots
    )


    assert (
        len(
            {
                item.content_hash

                for item
                in snapshots
            }
        )
        == 8
    )


    print(
        "Deterministic event hashing: PASS"
    )


    # ========================================================
    # PROVENANCE EXCLUDED FROM CONTENT IDENTITY
    # ========================================================

    spec = ECBPolicyMeetingSpec(

        year=2026,

        ordinal=6,

        meeting_date=date(
            2026,
            9,
            10
        )
    )


    original = (
        builder.build_snapshot(

            spec=
                spec,

            available_at=datetime(
                2025,
                4,
                24,
                22,
                0,
                tzinfo=timezone.utc
            ),

            ingested_at=
                INGESTED_AT
        )
    )


    rediscovered = (
        builder.build_snapshot(

            spec=
                spec,

            available_at=datetime(
                2025,
                4,
                25,
                12,
                0,
                tzinfo=timezone.utc
            ),

            ingested_at=datetime(
                2026,
                10,
                9,
                0,
                0,
                tzinfo=timezone.utc
            )
        )
    )


    assert (
        original.source_event_id
        == rediscovered.source_event_id
    )


    assert (
        original.content_hash
        == rediscovered.content_hash
    )


    print(
        "Provenance excluded from hash: PASS"
    )


    # ========================================================
    # RESCHEDULE CREATES A TRUE NEW VERSION
    # ========================================================

    rescheduled_spec = (
        ECBPolicyMeetingSpec(

            year=2026,

            ordinal=6,

            meeting_date=date(
                2026,
                9,
                17
            )
        )
    )


    rescheduled = (
        builder.build_snapshot(

            spec=
                rescheduled_spec,

            available_at=(
                ECB_2026_CALENDAR_AVAILABLE_AT
            ),

            ingested_at=
                INGESTED_AT
        )
    )


    assert (
        rescheduled.source_event_id
        == original.source_event_id
    )


    assert (
        rescheduled.scheduled_at
        != original.scheduled_at
    )


    assert (
        rescheduled.content_hash
        != original.content_hash
    )


    print(
        "Reschedule version identity: PASS"
    )


    # ========================================================
    # INGESTED_AT UTC NORMALIZATION
    # ========================================================

    offset_ingestion = datetime(

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


    normalized = (
        builder.build_snapshot(

            spec=
                spec,

            ingested_at=
                offset_ingestion
        )
    )


    assert (
        normalized.ingested_at
        == INGESTED_AT
    )


    print(
        "UTC ingestion normalization: PASS"
    )


    # ========================================================
    # TYPE / TIMEZONE GUARDS
    # ========================================================

    assert_type_error(

        lambda:
            builder.build_snapshot(

                spec="not-a-spec",

                ingested_at=
                    INGESTED_AT
            )
    )


    assert_type_error(

        lambda:
            builder.build_snapshot(

                spec=
                    spec,

                ingested_at=(
                    "2026-10-08"
                )
            )
    )


    assert_value_error(

        lambda:
            builder.build_snapshot(

                spec=
                    spec,

                ingested_at=datetime(
                    2026,
                    10,
                    8
                )
            )
    )


    assert_value_error(

        lambda:
            builder.build_snapshot(

                spec=
                    spec,

                available_at=datetime(
                    2025,
                    4,
                    24
                ),

                ingested_at=
                    INGESTED_AT
            )
    )


    print(
        "Calendar builder guards: PASS"
    )


    # ========================================================
    # DETERMINISTIC ORDER
    # ========================================================

    reversed_snapshots = (
        builder.build_snapshots(

            specs=(

                ECBPolicyMeetingSpec(
                    year=2026,
                    ordinal=8,
                    meeting_date=date(
                        2026,
                        12,
                        17
                    )
                ),

                ECBPolicyMeetingSpec(
                    year=2026,
                    ordinal=1,
                    meeting_date=date(
                        2026,
                        2,
                        5
                    )
                )
            ),

            ingested_at=
                INGESTED_AT
        )
    )


    assert (
        reversed_snapshots[0]
        .source_event_id
        == (
            "ECB|MONETARY_POLICY_DECISION|"
            "2026|01"
        )
    )


    assert (
        reversed_snapshots[1]
        .source_event_id
        == (
            "ECB|MONETARY_POLICY_DECISION|"
            "2026|08"
        )
    )


    print(
        "Deterministic snapshot ordering: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "ECB CALENDAR BUILDER TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()