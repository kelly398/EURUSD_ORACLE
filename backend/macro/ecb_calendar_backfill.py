from dataclasses import dataclass

from datetime import (
    datetime,
    timezone
)

from backend.macro.ecb_calendar_builder import (
    ECBPolicyCalendarBuilder
)

from backend.macro.macro_storage import (
    EconomicCalendarStorage
)


@dataclass(
    frozen=True
)
class ECBPolicyCalendarBackfillResult:

    calendar_year: int

    ingested_at: datetime

    built: int

    inserted: int

    skipped: int


class ECBPolicyCalendarBackfill:

    def __init__(
        self,
        builder=None,
        storage=None
    ):

        self.builder = (
            builder
            if builder is not None
            else ECBPolicyCalendarBuilder()
        )

        self.storage = (
            storage
            if storage is not None
            else EconomicCalendarStorage()
        )


    def run_2026(
        self,
        ingested_at=None
    ):

        # ==================================
        # BATCH INGESTION TIMESTAMP
        # ==================================

        if ingested_at is None:

            ingested_at = (
                datetime.now(
                    timezone.utc
                )
            )


        if (
            not isinstance(
                ingested_at,
                datetime
            )

            or

            ingested_at.tzinfo
            is None
        ):

            raise ValueError(
                "ingested_at must be "
                "timezone-aware datetime."
            )


        ingested_at = (
            ingested_at.astimezone(
                timezone.utc
            )
        )


        # ==================================
        # DETERMINISTIC CALENDAR BUILD
        # ==================================

        snapshots = (
            self.builder
            .build_2026_snapshots(

                ingested_at=
                    ingested_at
            )
        )


        # ==================================
        # TRANSACTIONAL STORAGE
        # ==================================

        storage_result = (
            self.storage.store_many(
                snapshots
            )
        )


        # ==================================
        # ACCOUNTING CONSISTENCY
        # ==================================

        if (
            storage_result.received
            != len(
                snapshots
            )
        ):

            raise RuntimeError(
                "ECB calendar storage result "
                "count does not match built "
                "snapshot count."
            )


        if (
            storage_result.inserted
            + storage_result.skipped
            != storage_result.received
        ):

            raise RuntimeError(
                "ECB calendar storage "
                "accounting is inconsistent."
            )


        return (
            ECBPolicyCalendarBackfillResult(

                calendar_year=2026,

                ingested_at=
                    ingested_at,

                built=
                    len(
                        snapshots
                    ),

                inserted=
                    storage_result.inserted,

                skipped=
                    storage_result.skipped
            )
        )


def main():

    result = (
        ECBPolicyCalendarBackfill()
        .run_2026()
    )


    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "ECB POLICY CALENDAR BACKFILL"
    )

    print(
        "=" * 70
    )

    print(
        f"Calendar year: "
        f"{result.calendar_year}"
    )

    print(
        f"Ingested at:  "
        f"{result.ingested_at}"
    )

    print(
        f"Built:        "
        f"{result.built}"
    )

    print(
        f"Inserted:     "
        f"{result.inserted}"
    )

    print(
        f"Skipped:      "
        f"{result.skipped}"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()