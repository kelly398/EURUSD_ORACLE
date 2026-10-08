from dataclasses import dataclass

from datetime import (
    date,
    datetime,
    time,
    timedelta,
    timezone
)

from zoneinfo import (
    ZoneInfo
)


# ============================================================
# OFFICIAL ECB CALENDAR SOURCE
# ============================================================

ECB_CALENDAR_SOURCE = (
    "ECB_GOVERNING_COUNCIL_CALENDAR"
)


ECB_2026_CALENDAR_RELEASE_URL = (
    "https://www.ecb.europa.eu/"
    "press/pr/date/2025/html/"
    "ecb.pr250424~2643533670.en.html"
)


ECB_POLICY_TIME_RULE_URL = (
    "https://www.ecb.europa.eu/"
    "press/pr/date/2022/html/"
    "ecb.pr220627~73acedf868.en.html"
)


# ============================================================
# PUBLICATION PROVENANCE
# ============================================================
#
# The official 2026 calendar release exposes a
# publication DATE:
#
#     24 April 2025
#
# It does NOT expose a trustworthy intra-day
# publication timestamp.
#
# Therefore v1 deliberately fails conservative:
#
#     available_at =
#         next local midnight in Europe/Berlin
#
# This ensures historical replay never assumes
# the calendar was public earlier during the
# publication date than can actually be proven.
# ============================================================

ECB_2026_CALENDAR_PUBLICATION_DATE = (
    date(
        2025,
        4,
        24
    )
)


ECB_CALENDAR_TIMEZONE = (
    "Europe/Berlin"
)


def conservative_date_only_available_at(
    publication_date
):

    if (
        type(
            publication_date
        )
        is not date
    ):

        raise TypeError(
            "publication_date must be date."
        )


    timezone_local = (
        ZoneInfo(
            ECB_CALENDAR_TIMEZONE
        )
    )


    next_local_midnight = datetime.combine(

        publication_date
        + timedelta(
            days=1
        ),

        time(
            0,
            0
        ),

        tzinfo=
            timezone_local
    )


    return (
        next_local_midnight
        .astimezone(
            timezone.utc
        )
    )


ECB_2026_CALENDAR_AVAILABLE_AT = (
    conservative_date_only_available_at(
        ECB_2026_CALENDAR_PUBLICATION_DATE
    )
)


# ============================================================
# ECB MONETARY-POLICY DECISION TIME RULE
# ============================================================
#
# ECB announced on 27 June 2022 that,
# starting 21 July 2022, monetary-policy
# decisions are published at 14:15 local ECB
# time.
#
# Europe/Berlin is used instead of a fixed UTC
# offset so daylight-saving transitions are
# handled correctly.
# ============================================================

ECB_POLICY_DECISION_TIME_RULE_START = (
    date(
        2022,
        7,
        21
    )
)


ECB_POLICY_DECISION_LOCAL_TIME = (
    time(
        14,
        15
    )
)


def build_ecb_decision_scheduled_at(
    meeting_date
):

    if (
        type(
            meeting_date
        )
        is not date
    ):

        raise TypeError(
            "meeting_date must be date."
        )


    if (
        meeting_date
        < ECB_POLICY_DECISION_TIME_RULE_START
    ):

        raise ValueError(
            "ECB calendar v1 only supports "
            "meeting dates on or after "
            "2022-07-21."
        )


    timezone_local = (
        ZoneInfo(
            ECB_CALENDAR_TIMEZONE
        )
    )


    local_scheduled = (
        datetime.combine(

            meeting_date,

            ECB_POLICY_DECISION_LOCAL_TIME,

            tzinfo=
                timezone_local
        )
    )


    return (
        local_scheduled
        .astimezone(
            timezone.utc
        )
    )


# ============================================================
# 2026 OFFICIAL GOVERNING COUNCIL DATES
# ============================================================

@dataclass(
    frozen=True
)
class ECBPolicyMeetingSpec:

    year: int

    ordinal: int

    meeting_date: date


ECB_2026_POLICY_MEETINGS = (

    ECBPolicyMeetingSpec(
        year=2026,
        ordinal=1,
        meeting_date=date(
            2026,
            2,
            5
        )
    ),

    ECBPolicyMeetingSpec(
        year=2026,
        ordinal=2,
        meeting_date=date(
            2026,
            3,
            19
        )
    ),

    ECBPolicyMeetingSpec(
        year=2026,
        ordinal=3,
        meeting_date=date(
            2026,
            4,
            30
        )
    ),

    ECBPolicyMeetingSpec(
        year=2026,
        ordinal=4,
        meeting_date=date(
            2026,
            6,
            11
        )
    ),

    ECBPolicyMeetingSpec(
        year=2026,
        ordinal=5,
        meeting_date=date(
            2026,
            7,
            23
        )
    ),

    ECBPolicyMeetingSpec(
        year=2026,
        ordinal=6,
        meeting_date=date(
            2026,
            9,
            10
        )
    ),

    ECBPolicyMeetingSpec(
        year=2026,
        ordinal=7,
        meeting_date=date(
            2026,
            10,
            29
        )
    ),

    ECBPolicyMeetingSpec(
        year=2026,
        ordinal=8,
        meeting_date=date(
            2026,
            12,
            17
        )
    ),
)


# ============================================================
# EVENT CONTRACT
# ============================================================

ECB_POLICY_EVENT_NAME = (
    "ECB Monetary Policy Decision"
)

ECB_POLICY_EVENT_JURISDICTION = (
    "EURO_AREA"
)

ECB_POLICY_EVENT_CURRENCY = (
    "EUR"
)


# ============================================================
# STABLE LOGICAL EVENT IDENTITY
# ============================================================
#
# Do NOT use the scheduled date as the logical
# identity.
#
# If ECB changes a meeting date, the same
# logical meeting must be able to receive a new
# content_hash/version.
#
# Identity therefore uses:
#
#     ECB
#     + monetary-policy-decision
#     + year
#     + chronological meeting ordinal
# ============================================================

ECB_POLICY_EVENT_ID_RULE = (
    "ECB|MONETARY_POLICY_DECISION|YEAR|ORDINAL"
)


def build_ecb_policy_event_id(
    year,
    ordinal
):

    if (
        not isinstance(
            year,
            int
        )

        or

        isinstance(
            year,
            bool
        )

        or

        year <= 0
    ):

        raise ValueError(
            "year must be a positive integer."
        )


    if (
        not isinstance(
            ordinal,
            int
        )

        or

        isinstance(
            ordinal,
            bool
        )

        or

        ordinal <= 0
    ):

        raise ValueError(
            "ordinal must be a "
            "positive integer."
        )


    return (
        "ECB|MONETARY_POLICY_DECISION|"
        + f"{year:04d}"
        + "|"
        + f"{ordinal:02d}"
    )


# ============================================================
# CONTENT-HASH CONTRACT
# ============================================================
#
# available_at is provenance, not economic
# content, and therefore must NOT affect the
# event version hash.
#
# If ECB reschedules the meeting,
# scheduled_at changes and therefore the
# content hash changes while source_event_id
# remains stable.
# ============================================================

ECB_POLICY_EVENT_CONTENT_HASH_FIELDS = (

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


ECB_POLICY_EVENT_CONTENT_HASH_EXCLUDED_FIELDS = (

    "available_at",

    "ingested_at",

    "publication_date",

    "http_last_modified",

    "http_date",

    "http_etag"
)