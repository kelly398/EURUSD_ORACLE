from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import (
    insert as postgresql_insert
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    CentralBankRate,
    EconomicCalendarEvent
)

from backend.macro.macro_schemas import (
    CentralBankRateSnapshot,
    EconomicCalendarEventSnapshot
)


@dataclass(
    frozen=True
)
class MacroStorageResult:

    received: int

    inserted: int

    skipped: int


class FedRatePersistenceBlocked(PermissionError):
    """An attested Federal Reserve persistence path is not yet installed."""


class CentralBankRateStorage:

    @staticmethod
    def _validate_snapshot(snapshot):
        if not isinstance(snapshot, CentralBankRateSnapshot):
            raise TypeError(
                "Central-bank rate storage accepts only "
                "CentralBankRateSnapshot."
            )

        # Applies to both the public batch writer and the row helper.
        # No source-version attestation is yet available for Fed records.
        if (
            snapshot.central_bank.strip().casefold() == "fed"
            or snapshot.source.strip().casefold() == "fed_fomc_release"
        ):
            raise FedRatePersistenceBlocked(
                "FED policy-rate persistence is disabled pending "
                "historical source-version attestation."
            )

    def _insert_snapshot(
        self,
        session,
        snapshot
    ):

        self._validate_snapshot(snapshot)

        statement = (

            postgresql_insert(
                CentralBankRate
            )

            .values(

                central_bank=
                    snapshot.central_bank,

                rate_name=
                    snapshot.rate_name,

                reference_date=
                    snapshot.reference_date,

                available_at=
                    snapshot.available_at,

                value_percent=
                    snapshot.value_percent,

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
                    "uq_central_bank_rate_version"
                )
            )

            .returning(
                CentralBankRate.id
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
            self._validate_snapshot(snapshot)


        if not snapshots:

            return MacroStorageResult(
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


            return MacroStorageResult(

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


class EconomicCalendarStorage:

    def _insert_snapshot(
        self,
        session,
        snapshot
    ):

        statement = (

            postgresql_insert(
                EconomicCalendarEvent
            )

            .values(

                source_event_id=
                    snapshot.source_event_id,

                event_name=
                    snapshot.event_name,

                jurisdiction=
                    snapshot.jurisdiction,

                currency=
                    snapshot.currency,

                scheduled_at=
                    snapshot.scheduled_at,

                available_at=
                    snapshot.available_at,

                actual=
                    snapshot.actual,

                forecast=
                    snapshot.forecast,

                previous=
                    snapshot.previous,

                unit=
                    snapshot.unit,

                content_hash=
                    snapshot.content_hash,

                source=
                    snapshot.source,

                ingested_at=
                    snapshot.ingested_at
            )

            .on_conflict_do_nothing(
                constraint=(
                    "uq_economic_calendar_event_version"
                )
            )

            .returning(
                EconomicCalendarEvent.id
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
                EconomicCalendarEventSnapshot
            ):

                raise TypeError(
                    "Economic-calendar storage "
                    "accepts only "
                    "EconomicCalendarEventSnapshot."
                )


        if not snapshots:

            return MacroStorageResult(
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


            return MacroStorageResult(

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