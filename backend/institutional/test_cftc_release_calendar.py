from datetime import (
    date,
    datetime,
    timezone
)

from backend.institutional.cftc_release_calendar import (
    CFTCReleaseCalendar
)


def main():

    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "CFTC RELEASE CALENDAR TEST"
    )

    print(
        "=" * 70
    )


    calendar = (
        CFTCReleaseCalendar()
    )


    # Normal pre-shutdown publication.
    assert (
        calendar.available_at(
            date(
                2025,
                9,
                23
            )
        )
        == datetime(
            2025,
            9,
            26,
            19,
            30,
            tzinfo=timezone.utc
        )
    )

    print(
        "\nPre-shutdown normal release: PASS"
    )


    # First withheld shutdown report.
    assert (
        calendar.available_at(
            date(
                2025,
                9,
                30
            )
        )
        == datetime(
            2025,
            11,
            19,
            20,
            30,
            tzinfo=timezone.utc
        )
    )

    print(
        "2025 shutdown delay: PASS"
    )


    # Final accelerated CFTC schedule.
    assert (
        calendar.available_at(
            date(
                2025,
                11,
                10
            )
        )
        == datetime(
            2025,
            12,
            10,
            20,
            30,
            tzinfo=timezone.utc
        )
    )

    assert (
        calendar.available_at(
            date(
                2025,
                12,
                23
            )
        )
        == datetime(
            2025,
            12,
            29,
            20,
            30,
            tzinfo=timezone.utc
        )
    )

    print(
        "Final catch-up schedule: PASS"
    )


    # 2025-12-30 -> holiday-delayed
    # January 5, 2026 publication.
    assert (
        calendar.available_at(
            date(
                2025,
                12,
                30
            )
        )
        == datetime(
            2026,
            1,
            5,
            20,
            30,
            tzinfo=timezone.utc
        )
    )

    print(
        "2026 holiday release mapping: PASS"
    )


    # Juneteenth-delay example.
    assert (
        calendar.available_at(
            date(
                2026,
                6,
                16
            )
        )
        == datetime(
            2026,
            6,
            22,
            19,
            30,
            tzinfo=timezone.utc
        )
    )

    print(
        "2026 delayed Monday release: PASS"
    )


    # Latest row observed in the live probe:
    #
    # report 2026-09-29
    # released 2026-10-02 15:30 EDT
    # = 19:30 UTC
    assert (
        calendar.available_at(
            date(
                2026,
                9,
                29
            )
        )
        == datetime(
            2026,
            10,
            2,
            19,
            30,
            tzinfo=timezone.utc
        )
    )

    print(
        "Current Euro FX report mapping: PASS"
    )


    # We refuse to silently guess historical
    # publication dates outside the frozen
    # authoritative period.
    try:

        calendar.available_at(
            date(
                2025,
                9,
                16
            )
        )

    except ValueError:

        pass

    else:

        raise AssertionError(
            "Unsupported historical date "
            "was incorrectly inferred."
        )


    print(
        "Unsupported-date fail-closed guard: PASS"
    )


    print(
        "\n"
        + "=" * 70
    )

    print(
        "CFTC RELEASE CALENDAR TEST: PASS"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()