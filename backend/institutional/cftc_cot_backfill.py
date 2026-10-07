import argparse

from dataclasses import dataclass

from datetime import (
    date,
    datetime,
    timezone
)

from backend.institutional.cftc_tff_client import (
    CFTCTFFFuturesOnlyClient
)

from backend.institutional.cot_storage import (
    COTPositioningStorage
)

CFTC_CAUSAL_BACKFILL_START_REPORT_DATE = date(
    2022,
    10,
    18
)

@dataclass(
    frozen=True
)
class COTBackfillResult:

    start_report_date: date

    end_report_date: date

    ingested_at: datetime

    fetched: int

    inserted: int

    skipped: int


class CFTCCOTBackfill:

    def __init__(
        self,
        client=None,
        storage=None
    ):

        self.client = (
            client
            if client is not None
            else CFTCTFFFuturesOnlyClient()
        )

        self.storage = (
            storage
            if storage is not None
            else COTPositioningStorage()
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
        start_report_date,
        end_report_date,
        ingested_at=None,
        limit=50000
    ):

        self._validate_date(
            start_report_date,
            "start_report_date"
        )

        self._validate_date(
            end_report_date,
            "end_report_date"
        )


        # ==================================
        # DATE-RANGE VALIDATION
        # ==================================

        if (
            end_report_date
            < start_report_date
        ):

            raise ValueError(
                "end_report_date cannot "
                "precede start_report_date."
            )


        # ==================================
        # CAUSAL HISTORICAL FLOOR
        # ==================================
        #
        # 2022-10-18 is the first report date
        # admitted to the ordinary PRE-native
        # production backfill path.
        #
        # Earlier TFF history requires separate
        # provenance reconstruction.
        # ==================================

        if (
            start_report_date
            < (
                CFTC_CAUSAL_BACKFILL_START_REPORT_DATE
            )
        ):

            raise ValueError(
                "Production CFTC COT backfill "
                "cannot precede "
                "2022-10-18. Earlier TFF "
                "history requires separate "
                "historical provenance "
                "reconstruction."
            )


        # ==================================
        # LIMIT VALIDATION
        # ==================================

        if limit <= 0:

            raise ValueError(
                "limit must be positive."
            )


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
            or ingested_at.tzinfo
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


        snapshots = (
            self.client.fetch_snapshots(

                start_report_date=
                    start_report_date,

                end_report_date=
                    end_report_date,

                ingested_at=
                    ingested_at,

                limit=
                    limit
            )
        )


        storage_result = (
            self.storage.store_many(
                snapshots
            )
        )


        if (
            storage_result.received
            != len(
                snapshots
            )
        ):

            raise RuntimeError(
                "COT storage result count "
                "does not match fetched "
                "snapshot count."
            )


        return COTBackfillResult(

            start_report_date=
                start_report_date,

            end_report_date=
                end_report_date,

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
            "Backfill causal CFTC TFF "
            "Euro FX positioning data."
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

    parser.add_argument(
        "--limit",
        type=int,
        default=50000
    )


    args = (
        parser.parse_args()
    )


    backfill = (
        CFTCCOTBackfill()
    )


    result = backfill.run(

        start_report_date=
            args.start,

        end_report_date=
            args.end,

        limit=
            args.limit
    )


    print(
        "=" * 70
    )

    print(
        "EUR/USD ORACLE "
        "CFTC COT BACKFILL"
    )

    print(
        "=" * 70
    )

    print(
        f"Start report date: "
        f"{result.start_report_date}"
    )

    print(
        f"End report date:   "
        f"{result.end_report_date}"
    )

    print(
        f"Ingested at:       "
        f"{result.ingested_at}"
    )

    print(
        f"Fetched:           "
        f"{result.fetched}"
    )

    print(
        f"Inserted:          "
        f"{result.inserted}"
    )

    print(
        f"Skipped:           "
        f"{result.skipped}"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":

    main()