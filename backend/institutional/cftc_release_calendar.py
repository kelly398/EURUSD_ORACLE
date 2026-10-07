from datetime import (
    date,
    datetime,
    time,
    timezone
)

from zoneinfo import ZoneInfo


NEW_YORK = ZoneInfo(
    "America/New_York"
)

UTC = timezone.utc

CFTC_RELEASE_TIME = time(
    15,
    30
)


# ============================================================
# 2025 APPROPRIATIONS-LAPSE OVERRIDES
# ============================================================
#
# Final CFTC catch-up schedule published
# December 9, 2025.
#
# These MUST override ordinary Friday logic.
# ============================================================

SPECIAL_RELEASE_DATES = {

    date(2025, 9, 23):
        date(2025, 9, 26),

    date(2025, 9, 30):
        date(2025, 11, 19),

    date(2025, 10, 7):
        date(2025, 11, 21),

    date(2025, 10, 14):
        date(2025, 11, 25),

    date(2025, 10, 21):
        date(2025, 12, 2),

    date(2025, 10, 28):
        date(2025, 12, 5),

    date(2025, 11, 4):
        date(2025, 12, 9),

    date(2025, 11, 10):
        date(2025, 12, 10),

    date(2025, 11, 18):
        date(2025, 12, 12),

    date(2025, 11, 25):
        date(2025, 12, 15),

    date(2025, 12, 2):
        date(2025, 12, 17),

    date(2025, 12, 9):
        date(2025, 12, 19),

    date(2025, 12, 16):
        date(2025, 12, 23),

    date(2025, 12, 23):
        date(2025, 12, 29),
}


# ============================================================
# OFFICIAL 2026 CFTC RELEASE DATES
# ============================================================
#
# Dates come from the published CFTC COT
# release schedule, including holiday delays.
#
# For post-backlog reports, a report is paired
# with the first scheduled publication date
# after its report date.
# ============================================================

OFFICIAL_2026_RELEASE_DATES = (

    date(2026, 1, 5),
    date(2026, 1, 9),
    date(2026, 1, 16),
    date(2026, 1, 23),
    date(2026, 1, 30),

    date(2026, 2, 6),
    date(2026, 2, 13),
    date(2026, 2, 20),
    date(2026, 2, 27),

    date(2026, 3, 6),
    date(2026, 3, 13),
    date(2026, 3, 20),
    date(2026, 3, 27),

    date(2026, 4, 3),
    date(2026, 4, 10),
    date(2026, 4, 17),
    date(2026, 4, 24),

    date(2026, 5, 1),
    date(2026, 5, 8),
    date(2026, 5, 15),
    date(2026, 5, 22),
    date(2026, 5, 29),

    date(2026, 6, 5),
    date(2026, 6, 12),
    date(2026, 6, 22),
    date(2026, 6, 26),

    date(2026, 7, 6),
    date(2026, 7, 10),
    date(2026, 7, 17),
    date(2026, 7, 24),
    date(2026, 7, 31),

    date(2026, 8, 7),
    date(2026, 8, 14),
    date(2026, 8, 21),
    date(2026, 8, 28),

    date(2026, 9, 4),
    date(2026, 9, 11),
    date(2026, 9, 18),
    date(2026, 9, 25),

    date(2026, 10, 2),
    date(2026, 10, 9),
    date(2026, 10, 16),
    date(2026, 10, 23),
    date(2026, 10, 30),

    date(2026, 11, 6),
    date(2026, 11, 16),
    date(2026, 11, 20),
    date(2026, 11, 30),

    date(2026, 12, 4),
    date(2026, 12, 11),
    date(2026, 12, 18),
    date(2026, 12, 28),
)


class CFTCReleaseCalendar:

    def _publication_datetime(
        self,
        publication_date
    ):

        local_timestamp = datetime.combine(
            publication_date,
            CFTC_RELEASE_TIME,
            tzinfo=NEW_YORK
        )

        return local_timestamp.astimezone(
            UTC
        )


    def publication_date(
        self,
        report_date
    ):

        if (
            type(report_date)
            is not date
        ):

            raise TypeError(
                "report_date must be date."
            )


        special = (
            SPECIAL_RELEASE_DATES
            .get(
                report_date
            )
        )


        if special is not None:

            return special


        # ==================================
        # POST-BACKLOG OFFICIAL SCHEDULE
        # ==================================
        #
        # 2025-12-30 report is released on
        # 2026-01-05.
        #
        # We intentionally do not invent dates
        # outside the authoritative range.
        # ==================================

        if (
            report_date
            >= date(
                2025,
                12,
                30
            )
        ):

            for release_date in (
                OFFICIAL_2026_RELEASE_DATES
            ):

                if (
                    release_date
                    > report_date
                ):

                    return release_date


        raise ValueError(
            "No authoritative CFTC release "
            "mapping is available for "
            f"report_date={report_date}."
        )


    def available_at(
        self,
        report_date
    ):

        publication_date = (
            self.publication_date(
                report_date
            )
        )


        return (
            self._publication_datetime(
                publication_date
            )
        )