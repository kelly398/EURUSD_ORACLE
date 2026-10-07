from datetime import datetime

from sqlalchemy import (
    select
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    COTPositioning
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot,
    COT_REPORT_TYPE_TFF_FUTURES_ONLY,
    EURO_FX_CFTC_CONTRACT_MARKET_CODE
)


class COTPositioningRepository:

    def __init__(
        self,
        source="CFTC",
        report_type=(
            COT_REPORT_TYPE_TFF_FUTURES_ONLY
        ),
        cftc_contract_market_code=(
            EURO_FX_CFTC_CONTRACT_MARKET_CODE
        )
    ):

        self.source = source

        self.report_type = (
            report_type
        )

        self.cftc_contract_market_code = (
            cftc_contract_market_code
        )


    def _validate_as_of(
        self,
        as_of
    ):

        if not isinstance(
            as_of,
            datetime
        ):

            raise TypeError(
                "as_of must be datetime."
            )


        if (
            as_of.tzinfo
            is None
        ):

            raise ValueError(
                "as_of must be "
                "timezone-aware."
            )


    def _to_schema(
        self,
        row
    ):

        return COTPositioningSnapshot(

            report_type=
                row.report_type,

            cftc_contract_market_code=
                row.cftc_contract_market_code,

            market_and_exchange_names=
                row.market_and_exchange_names,

            contract_market_name=
                row.contract_market_name,

            commodity_name=
                row.commodity_name,

            report_date=
                row.report_date,

            available_at=
                row.available_at,

            open_interest=
                int(
                    row.open_interest
                ),

            dealer_long=
                int(
                    row.dealer_long
                ),

            dealer_short=
                int(
                    row.dealer_short
                ),

            dealer_spread=
                int(
                    row.dealer_spread
                ),

            asset_manager_long=
                int(
                    row.asset_manager_long
                ),

            asset_manager_short=
                int(
                    row.asset_manager_short
                ),

            asset_manager_spread=
                int(
                    row.asset_manager_spread
                ),

            leveraged_money_long=
                int(
                    row.leveraged_money_long
                ),

            leveraged_money_short=
                int(
                    row.leveraged_money_short
                ),

            leveraged_money_spread=
                int(
                    row.leveraged_money_spread
                ),

            other_reportables_long=
                int(
                    row.other_reportables_long
                ),

            other_reportables_short=
                int(
                    row.other_reportables_short
                ),

            other_reportables_spread=
                int(
                    row.other_reportables_spread
                ),

            nonreportable_long=
                int(
                    row.nonreportable_long
                ),

            nonreportable_short=
                int(
                    row.nonreportable_short
                ),

            source_record_id=
                row.source_record_id,

            content_hash=
                row.content_hash,

            source=
                row.source,

            ingested_at=
                row.ingested_at
        )


    def _get_available_rows(
        self,
        as_of
    ):

        self._validate_as_of(
            as_of
        )


        session = SessionLocal()


        try:

            statement = (

                select(
                    COTPositioning
                )

                .where(

                    COTPositioning.source
                    == self.source,

                    COTPositioning.report_type
                    == self.report_type,

                    COTPositioning
                    .cftc_contract_market_code
                    == (
                        self
                        .cftc_contract_market_code
                    ),

                    COTPositioning.available_at
                    <= as_of
                )

                .order_by(

                    COTPositioning
                    .report_date
                    .asc(),

                    COTPositioning
                    .available_at
                    .desc(),

                    COTPositioning
                    .id
                    .desc()
                )
            )


            return (

                session.execute(
                    statement
                )

                .scalars()

                .all()
            )


        finally:

            session.close()


    def get_history_as_of(
        self,
        as_of,
        limit=156
    ):

        """
        Return causal positioning history.

        Exactly one record version is returned
        per report_date.

        If a report was subsequently corrected,
        the newest version whose available_at is
        <= as_of is selected.

        Result order is chronological by
        report_date.
        """

        if limit <= 0:

            raise ValueError(
                "limit must be greater "
                "than zero."
            )


        rows = (
            self._get_available_rows(
                as_of
            )
        )


        # Rows are ordered:
        #
        # report_date ASC
        # available_at DESC
        # id DESC
        #
        # Therefore the first row seen for each
        # report_date is the newest version that
        # was legally available at `as_of`.
        latest_version_by_report = {}


        for row in rows:

            if (
                row.report_date
                not in latest_version_by_report
            ):

                latest_version_by_report[
                    row.report_date
                ] = row


        selected_rows = list(
            latest_version_by_report.values()
        )


        selected_rows.sort(
            key=lambda row:
                row.report_date
        )


        selected_rows = (
            selected_rows[
                -limit:
            ]
        )


        return tuple(

            self._to_schema(
                row
            )

            for row
            in selected_rows
        )


    def get_latest_as_of(
        self,
        as_of
    ):

        """
        Return the most recent causal COT
        report version available at `as_of`.

        Returns None when no eligible report
        exists.
        """

        history = (
            self.get_history_as_of(
                as_of=as_of,
                limit=1
            )
        )


        if not history:

            return None


        return history[-1]