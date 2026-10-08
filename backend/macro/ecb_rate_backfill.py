import argparse

from dataclasses import dataclass

from datetime import (
    date,
    datetime,
    timezone
)

from backend.macro.ecb_rate_client import (
    ECBRateClient
)

from backend.macro.macro_storage import (
    CentralBankRateStorage
)


@dataclass(
    frozen=True
)
class ECBRateBackfillResult:

    start_date: date

    end_date: date

    ingested_at: datetime

    fetched: int

    inserted: int

    skipped: int


class ECBRateBackfill:

    def __init__(
        self,
        client=None,
        storage=None
    ):

        self.client = (
            client
            if client is not None
            else ECBRateClient()
        )

        self.storage = (
            storage
            if storage is not None
            else CentralBankRateStorage()
        )


    def _validate_date(
        self,
        value,
        name
    ):

        if (
            type(value)
            is not date
        ):

            raise TypeError(
                f"{name} must be date."
            )


    def run(
        self,
        start_date,
        end_date,
        ingested_at=None
    ):

        self._validate_date(
            start_date,
            "start_date"
        )

        self._validate_date(
            end_date,
            "end_date"
        )


        # ==================================
        # DATE-RANGE VALIDATION
        # ==================================

        if (
            end_date
            < start_date
        ):

            raise ValueError(
                "end_date cannot precede "
                "start_date."
            )


        # ==================================
        # BATCH INGESTION TIMESTAMP
        # ==================================
        #
        # The client never invents this value.
        #
        # The orchestration layer may use the
        # current UTC time for an ordinary live
        # backfill invocation.
        #
        # Tests and historical workflows may
        # supply it explicitly.
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
        # OFFICIAL ECB ACQUISITION
        # ==================================

        snapshots = (
            self.client.fetch_snapshots(

                start_date=
                    start_date,

                end_date=
                    end_date,

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
                "ECB rate storage result count "
                "does not match fetched "
                "snapshot count."
            )


        if (
            storage_result.inserted
            + storage_result.skipped
            != storage_result.received
        ):

            raise RuntimeError(
                "ECB rate storage accounting "
                "is inconsistent."
            )


        return ECBRateBackfillResult(

            start_date=
                start_date,

            end_date=
                end_date,

            ingested_at=
                ingested_at,

            fetched=
                len(
                    snapshots
                ),

            inserted=
                storage_result.inserted,

            skipped=
                storage_result.skipped
        )


def _parse_date(
    value
):

    try:

        return date.fromisoformat(
            value
        )

    except ValueError as exc:

        raise argparse.ArgumentTypeError(
            "Date must use YYYY-MM-DD."
        ) from exc


def main():

    parser = argparse.ArgumentParser(

        description=(
            "Backfill causal ECB key "
            "policy-rate data from the "
            "official ECB SDMX API."
        )
    )


    parser.add_argument(

        "--start",

        required=True,

        type=_parse_date
    )


    parser.add_argument(

        "--end",

        required=True,

        type=_parse_date
    )


    args = (
        parser.parse_args()
    )


    backfill = (
        ECBRateBackfill()
    )


    result = backfill.run(

        start_date=
            args.start,

        end_date=
            args.end
    )


    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "ECB RATE BACKFILL"
    )

    print(
        "=" * 70
    )

    print(
        f"Start date:   "
        f"{result.start_date}"
    )

    print(
        f"End date:     "
        f"{result.end_date}"
    )

    print(
        f"Ingested at:  "
        f"{result.ingested_at}"
    )

    print(
        f"Fetched:      "
        f"{result.fetched}"
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