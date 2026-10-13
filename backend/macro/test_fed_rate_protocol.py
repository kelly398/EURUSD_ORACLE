
"""F1 deterministic Federal Reserve rate protocol tests.

Run:
    python -m backend.macro.test_fed_rate_protocol

No network, database, broker, or reserved HMM evaluation.
"""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import re

from backend.macro.fed_rate_protocol import (
    FED_CENTRAL_BANK,
    FED_RATE_HASH_EXCLUDED_FIELDS,
    FED_RATE_HASH_FIELDS,
    FED_RATE_NAMES,
    FED_RATE_POLICY_VERSION,
    FED_RATE_SOURCE,
    FED_RATE_TLS_VERIFICATION_REQUIRED,
    FED_RATE_UNVERIFIED_TLS_ALLOWED,
    FedOriginalRateEvidence,
    FedRateProtocolError,
    FedMeetingIndexStamp,
    FedReleaseStamp,
    build_fed_content_hash,
    build_fed_original_rate_snapshots,
    build_fed_record_id,
    build_fed_release_at_utc,
    build_fed_statement_id,
    canonical_fed_percent,
    conservative_fed_date_only_available_at,
    parse_fed_rate_percent,
    validate_fed_original_evidence,
)


UTC = timezone.utc

SEPT_RELEASE = datetime(
    2026, 9, 16, 18, 0, tzinfo=UTC
)

INGESTED = datetime(
    2026, 10, 13, 19, 0, tzinfo=UTC
)


def stamp(year, month, day, hour, minute, zone):
    return FedReleaseStamp(
        date(year, month, day),
        hour,
        minute,
        zone,
    )


def index_stamp(year, month, day, hour, minute):
    return FedMeetingIndexStamp(
        date(year, month, day),
        hour,
        minute,
    )


def fixture(ymd, hh, zone, lower, upper, effective):
    path = "https://www.federalreserve.gov/"

    original = stamp(
        ymd.year, ymd.month, ymd.day, hh, 0, zone
    )

    index = index_stamp(
        ymd.year, ymd.month, ymd.day, hh, 0
    )

    datepart = ymd.strftime("%Y%m%d")

    return FedOriginalRateEvidence(
        statement_url=(
            path
            + "newsevents/pressreleases/monetary"
            + datepart
            + "a.htm"
        ),
        implementation_url=(
            path
            + "newsevents/pressreleases/monetary"
            + datepart
            + "a1.htm"
        ),
        meeting_url=(
            path
            + "monetarypolicy/fomcpresconf"
            + datepart
            + ".htm"
        ),
        statement_header_release=original,
        meeting_statement_release=index,
        meeting_implementation_release=index,
        statement_lower=lower,
        statement_upper=upper,
        implementation_lower=lower,
        implementation_upper=upper,
        effective_date=effective,
    )


def refuses(action, message_fragment=""):
    try:
        action()
    except FedRateProtocolError as exc:
        if message_fragment:
            assert (
                message_fragment.lower()
                in str(exc).lower()
            ), str(exc)
        return

    raise AssertionError(
        "Expected FedRateProtocolError (fail closed)."
    )


def main():
    print("=" * 68)
    print("EUR/USD ORACLE - FED SEAT 25 F1 PROTOCOL TEST")
    print("=" * 68)

    # ---------------------------------------------------------
    # 1. Official source and TLS contract
    # ---------------------------------------------------------

    assert FED_CENTRAL_BANK == "FED"
    assert FED_RATE_SOURCE == "FED_FOMC_RELEASE"
    assert FED_RATE_POLICY_VERSION == "fomc_target_range_v1"

    assert FED_RATE_NAMES == (
        "federal_funds_target_lower",
        "federal_funds_target_upper",
    )

    assert FED_RATE_TLS_VERIFICATION_REQUIRED is True
    assert FED_RATE_UNVERIFIED_TLS_ALLOWED is False

    print("Official source and TLS contract: PASS")

    # ---------------------------------------------------------
    # 2. Release times and daylight saving
    # ---------------------------------------------------------

    assert build_fed_release_at_utc(
        date(2026, 9, 16), 14, 0, "EDT"
    ) == SEPT_RELEASE

    assert build_fed_release_at_utc(
        date(2026, 1, 28), 14, 0, "EST"
    ) == datetime(2026, 1, 28, 19, 0, tzinfo=UTC)

    assert build_fed_release_at_utc(
        date(2020, 3, 3), 10, 0, "EST"
    ) == datetime(2020, 3, 3, 15, 0, tzinfo=UTC)

    assert build_fed_release_at_utc(
        date(2020, 3, 15), 17, 0, "EDT"
    ) == datetime(2020, 3, 15, 21, 0, tzinfo=UTC)

    refuses(
        lambda: build_fed_release_at_utc(
            date(2026, 1, 28), 14, 0, "EDT"
        )
    )

    refuses(
        lambda: build_fed_release_at_utc(
            date(2026, 9, 16), 14, 0, "EST"
        )
    )

    refuses(
        lambda: build_fed_release_at_utc(
            date(2020, 3, 8), 2, 30, "EST"
        )
    )

    refuses(
        lambda: build_fed_release_at_utc(
            date(2026, 9, 16), 14, 0, "ET"
        )
    )

    print("Release timestamps and DST validity: PASS")

    # ---------------------------------------------------------
    # 3. Canonical September 2026 FOMC decision
    # ---------------------------------------------------------

    september = fixture(
        date(2026, 9, 16),
        14,
        "EDT",
        "3-3/4",
        "4",
        date(2026, 9, 17),
    )

    statement_id = build_fed_statement_id(
        september.statement_url
    )

    assert statement_id == "monetary20260916a"

    assert build_fed_record_id(
        statement_id, "lower"
    ) == "FOMC:monetary20260916a:lower"

    assert build_fed_record_id(
        statement_id, "upper"
    ) == "FOMC:monetary20260916a:upper"

    assert validate_fed_original_evidence(
        september
    )[:2] == (statement_id, SEPT_RELEASE)

    lower, upper = build_fed_original_rate_snapshots(
        september, INGESTED
    )

    assert (
        lower.value_percent,
        upper.value_percent,
    ) == (3.75, 4.0)

    assert lower.reference_date == date(2026, 9, 17)
    assert upper.reference_date == date(2026, 9, 17)

    assert lower.available_at == SEPT_RELEASE
    assert upper.available_at == SEPT_RELEASE

    assert (lower.rate_name, upper.rate_name) == FED_RATE_NAMES

    assert all(
        re.fullmatch(r"[a-f0-9]{64}", x.content_hash)
        for x in (lower, upper)
    )

    assert lower.content_hash != upper.content_hash

    assert lower.content_hash == (
        "0712290bf7b25aea321549c5924c79c6"
        "f8a464ba50f0da57179b664198be54a0"
    )

    assert upper.content_hash == (
        "9c816df7af186b25b83f04b7862d012"
        "c387a8d99e41091aa43b483d8871fd3cf"
    )

    print("Canonical policy decision and SHA-256: PASS")

    # ---------------------------------------------------------
    # 4. Anti-lookahead boundary
    # ---------------------------------------------------------

    before = SEPT_RELEASE - timedelta(microseconds=1)

    assert not (lower.available_at <= before)
    assert lower.available_at <= SEPT_RELEASE

    assert lower.reference_date > SEPT_RELEASE.date()

    print("Exact causal release boundary: PASS")

    # ---------------------------------------------------------
    # 5. Unchanged rates still create new decision identities
    # ---------------------------------------------------------

    july = fixture(
        date(2026, 7, 29),
        14,
        "EDT",
        "3-1/2",
        "3-3/4",
        date(2026, 7, 30),
    )

    january = fixture(
        date(2026, 1, 28),
        14,
        "EST",
        "3.50",
        "3.75",
        date(2026, 1, 29),
    )

    july_low, july_high = build_fed_original_rate_snapshots(
        july, INGESTED
    )

    jan_low, jan_high = build_fed_original_rate_snapshots(
        january, INGESTED
    )

    assert july_low.value_percent == jan_low.value_percent
    assert july_high.value_percent == jan_high.value_percent

    assert july_low.source_record_id != jan_low.source_record_id
    assert july_low.content_hash != jan_low.content_hash

    print("Independent hold-decision identity: PASS")

    # ---------------------------------------------------------
    # 6. Emergency Sunday announcement
    # ---------------------------------------------------------

    march15 = fixture(
        date(2020, 3, 15),
        17,
        "EDT",
        "0",
        "1/4",
        date(2020, 3, 16),
    )

    emergency_low, emergency_high = (
        build_fed_original_rate_snapshots(
            march15, INGESTED
        )
    )

    assert emergency_low.available_at == datetime(
        2020, 3, 15, 21, tzinfo=UTC
    )

    assert emergency_high.value_percent == 0.25

    print("Emergency monetary-policy announcement: PASS")

    # ---------------------------------------------------------
    # 7. Contradictory official-source evidence
    # ---------------------------------------------------------

    march3 = fixture(
        date(2020, 3, 3),
        10,
        "EST",
        "1",
        "1-1/4",
        date(2020, 3, 4),
    )

    conflict = replace(
        march3,
        meeting_implementation_release=index_stamp(
            2020, 3, 2, 10, 0
        ),
    )

    refuses(
        lambda: build_fed_original_rate_snapshots(
            conflict, INGESTED
        ),
        "conflict",
    )

    refuses(
        lambda: build_fed_original_rate_snapshots(
            replace(
                march3,
                meeting_implementation_release=index_stamp(
                    2020, 3, 3, 14, 0
                ),
            ),
            INGESTED,
        ),
        "disagree",
    )

    print("Conflicting provenance rejection: PASS")

    # ---------------------------------------------------------
    # 8. Strict official document identities
    # ---------------------------------------------------------

    refuses(
        lambda: build_fed_original_rate_snapshots(
            replace(
                september,
                meeting_statement_release=None,
            ),
            INGESTED,
        )
    )

    refuses(
        lambda: build_fed_statement_id(
            "http://www.federalreserve.gov/"
            "newsevents/pressreleases/"
            "monetary20260916a.htm"
        )
    )

    refuses(
        lambda: build_fed_statement_id(
            "https://www.federalreserve.gov/"
            "newsevents/pressreleases/"
            "monetary20260916a.htm?modified=1"
        )
    )

    refuses(
        lambda: build_fed_statement_id(
            "https://www.federalreserve.gov/"
            "newsevents/pressreleases/"
            "monetary20081216b.htm"
        )
    )

    print("Official document identity restrictions: PASS")

    # ---------------------------------------------------------
    # 9. Rate normalization
    # ---------------------------------------------------------

    assert canonical_fed_percent(
        "3-3/4"
    ) == canonical_fed_percent("3.75")

    assert canonical_fed_percent("1/4") == "0.250000"
    assert canonical_fed_percent("0") == "0.000000"

    invalid_rates = (
        "nan",
        "inf",
        "-1",
        "1e2",
        "1/3",
        "3-4/4",
        "31",
        "3 to 4",
        "3.5000001",
        "",
    )

    for token in invalid_rates:
        refuses(
            lambda token=token: parse_fed_rate_percent(token)
        )

    refuses(lambda: parse_fed_rate_percent(3.75))

    print("Strict numeric normalization: PASS")

    # ---------------------------------------------------------
    # 10. Invalid economic evidence
    # ---------------------------------------------------------

    refuses(
        lambda: build_fed_original_rate_snapshots(
            replace(september, statement_upper="3.50"),
            INGESTED,
        )
    )

    refuses(
        lambda: build_fed_original_rate_snapshots(
            replace(september, implementation_upper="3.75"),
            INGESTED,
        )
    )

    refuses(
        lambda: build_fed_original_rate_snapshots(
            replace(
                september,
                effective_date=date(2026, 9, 15),
            ),
            INGESTED,
        )
    )

    refuses(
        lambda: build_fed_original_rate_snapshots(
            september,
            SEPT_RELEASE - timedelta(microseconds=1),
        )
    )

    refuses(
        lambda: build_fed_original_rate_snapshots(
            september,
            datetime(2026, 10, 13),
        )
    )

    print("Invalid economic evidence rejection: PASS")

    # ---------------------------------------------------------
    # 11. Ingestion time must not change economic history
    # ---------------------------------------------------------

    newer = build_fed_original_rate_snapshots(
        september,
        INGESTED + timedelta(hours=3),
    )

    assert (
        lower.content_hash,
        upper.content_hash,
    ) == tuple(x.content_hash for x in newer)

    assert newer[0].ingested_at != lower.ingested_at

    assert FED_RATE_HASH_FIELDS == (
        "central_bank",
        "source",
        "policy_version",
        "statement_id",
        "rate_name",
        "reference_date",
        "target_lower_percent",
        "target_upper_percent",
    )

    assert "available_at" in FED_RATE_HASH_EXCLUDED_FIELDS
    assert "ingested_at" in FED_RATE_HASH_EXCLUDED_FIELDS

    print("Deterministic economic hashes: PASS")

    # ---------------------------------------------------------
    # 12. Economic changes modify both paired hashes
    # ---------------------------------------------------------

    original_hashes = (
        lower.content_hash,
        upper.content_hash,
    )

    revision_cases = (
        ("3.50", "4.00", date(2026, 9, 17)),
        ("3.75", "4.25", date(2026, 9, 17)),
        ("3.75", "4.00", date(2026, 9, 18)),
    )

    for revised_lower, revised_upper, effective in revision_cases:
        revised_hashes = tuple(
            build_fed_content_hash(
                statement_id,
                rate_name,
                effective,
                revised_lower,
                revised_upper,
            )
            for rate_name in FED_RATE_NAMES
        )

        assert all(
            new != old
            for new, old in zip(
                revised_hashes,
                original_hashes,
                strict=True,
            )
        )

    print("Paired economic-content hashing: PASS")

    # ---------------------------------------------------------
    # 13. Conservative date-only publication boundary
    # ---------------------------------------------------------

    assert conservative_fed_date_only_available_at(
        date(2024, 7, 10)
    ) == datetime(2024, 7, 11, 4, tzinfo=UTC)

    assert conservative_fed_date_only_available_at(
        date(2024, 1, 10)
    ) == datetime(2024, 1, 11, 5, tzinfo=UTC)

    refuses(
        lambda: conservative_fed_date_only_available_at(
            "2024-07-10"
        )
    )

    print("Conservative date-only boundary: PASS")

    print("=" * 68)
    print("F1 FED RATE PROTOCOL: ALL TESTS PASSED")
    print("=" * 68)
    print(
        "No networking, database writes, broker actions, "
        "or reserved HMM evaluation."
    )


if __name__ == "__main__":
    main()
