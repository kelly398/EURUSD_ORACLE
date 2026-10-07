from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import (
    insert as postgresql_insert
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    COTPositioning
)

from backend.institutional.cot_schemas import (
    COTPositioningSnapshot
)


@dataclass(
    frozen=True
)
class COTStorageResult:

    received: int

    inserted: int

    skipped: int


class COTPositioningStorage:

    def _insert_snapshot(
        self,
        session,
        snapshot
    ):

        statement = (

            postgresql_insert(
                COTPositioning
            )

            .values(

                report_type=
                    snapshot.report_type,

                cftc_contract_market_code=
                    (
                        snapshot
                        .cftc_contract_market_code
                    ),

                market_and_exchange_names=
                    (
                        snapshot
                        .market_and_exchange_names
                    ),

                contract_market_name=
                    snapshot.contract_market_name,

                commodity_name=
                    snapshot.commodity_name,

                report_date=
                    snapshot.report_date,

                available_at=
                    snapshot.available_at,

                open_interest=
                    snapshot.open_interest,

                dealer_long=
                    snapshot.dealer_long,

                dealer_short=
                    snapshot.dealer_short,

                dealer_spread=
                    snapshot.dealer_spread,

                asset_manager_long=
                    snapshot.asset_manager_long,

                asset_manager_short=
                    snapshot.asset_manager_short,

                asset_manager_spread=
                    snapshot.asset_manager_spread,

                leveraged_money_long=
                    snapshot.leveraged_money_long,

                leveraged_money_short=
                    snapshot.leveraged_money_short,

                leveraged_money_spread=
                    snapshot.leveraged_money_spread,

                other_reportables_long=
                    snapshot.other_reportables_long,

                other_reportables_short=
                    snapshot.other_reportables_short,

                other_reportables_spread=
                    snapshot.other_reportables_spread,

                nonreportable_long=
                    snapshot.nonreportable_long,

                nonreportable_short=
                    snapshot.nonreportable_short,

                source_record_id=
                    snapshot.source_record_id,

                content_hash=
                    snapshot.content_hash,

                source=
                    snapshot.source,

                ingested_at=
                    snapshot.ingested_at
            )

            .on_conflict_do_nothing(
                constraint=(
                    "uq_cot_positioning_version"
                )
            )

            .returning(
                COTPositioning.id
            )
        )


        inserted_id = (

            session.execute(
                statement
            )

            .scalar_one_or_none()
        )


        return (
            inserted_id
            is not None
        )


    def store_many(
        self,
        snapshots
    ):

        snapshots = tuple(
            snapshots
        )


        for snapshot in snapshots:

            if not isinstance(
                snapshot,
                COTPositioningSnapshot
            ):

                raise TypeError(
                    "COT storage accepts only "
                    "COTPositioningSnapshot."
                )


        if not snapshots:

            return COTStorageResult(
                received=0,
                inserted=0,
                skipped=0
            )


        session = SessionLocal()


        inserted = 0

        skipped = 0


        try:

            for snapshot in snapshots:

                was_inserted = (
                    self._insert_snapshot(
                        session=session,
                        snapshot=snapshot
                    )
                )


                if was_inserted:

                    inserted += 1

                else:

                    skipped += 1


            session.commit()


            return COTStorageResult(

                received=
                    len(
                        snapshots
                    ),

                inserted=
                    inserted,

                skipped=
                    skipped
            )


        except Exception:

            session.rollback()

            raise


        finally:

            session.close()