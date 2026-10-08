from datetime import datetime

from sqlalchemy import (
    select
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


class CentralBankRateRepository:

    def __init__(
        self,
        source="ECB",
        central_bank="ECB"
    ):

        self.source = source

        self.central_bank = (
            central_bank
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

        return CentralBankRateSnapshot(

            central_bank=
                row.central_bank,

            rate_name=
                row.rate_name,

            reference_date=
                row.reference_date,

            available_at=
                row.available_at,

            value_percent=
                float(
                    row.value_percent
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
                    CentralBankRate
                )

                .where(

                    CentralBankRate.source
                    == self.source,

                    CentralBankRate.central_bank
                    == self.central_bank,

                    CentralBankRate.available_at
                    <= as_of
                )

                .order_by(

                    CentralBankRate
                    .available_at
                    .asc(),

                    CentralBankRate
                    .id
                    .asc()
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


    def _latest_versions(
        self,
        rows
    ):

        latest_by_record = {}


        for row in rows:

            existing = (
                latest_by_record.get(
                    row.source_record_id
                )
            )


            if existing is None:

                latest_by_record[
                    row.source_record_id
                ] = row

                continue


            if (
                row.available_at
                > existing.available_at
            ):

                latest_by_record[
                    row.source_record_id
                ] = row

                continue


            if (
                row.available_at
                == existing.available_at
            ):

                if (
                    row.content_hash
                    != existing.content_hash
                ):

                    raise ValueError(
                        "Ambiguous central-bank "
                        "rate revisions share the "
                        "same source_record_id "
                        "and available_at."
                    )


                if (
                    row.id
                    > existing.id
                ):

                    latest_by_record[
                        row.source_record_id
                    ] = row


        return tuple(
            latest_by_record.values()
        )


    def get_history_as_of(
        self,
        as_of,
        limit=256
    ):

        """
        Return causal central-bank rate history.

        Exactly one legally available version
        is returned per source_record_id.

        Result order:
            reference_date
            rate_name
            source_record_id
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


        selected_rows = list(
            self._latest_versions(
                rows
            )
        )


        selected_rows.sort(
            key=lambda row: (
                row.reference_date,
                row.rate_name,
                row.source_record_id
            )
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


    def get_latest_by_rate_as_of(
        self,
        as_of
    ):

        """
        Return the latest causal observation
        for each rate_name.

        This is the policy-rate state that a
        central-bank agent may safely consume
        at `as_of`.
        """

        rows = (
            self._get_available_rows(
                as_of
            )
        )


        causal_versions = (
            self._latest_versions(
                rows
            )
        )


        latest_by_rate = {}


        for row in causal_versions:

            existing = (
                latest_by_rate.get(
                    row.rate_name
                )
            )


            if existing is None:

                latest_by_rate[
                    row.rate_name
                ] = row

                continue


            candidate_key = (

                row.reference_date,

                row.available_at,

                row.source_record_id
            )


            existing_key = (

                existing.reference_date,

                existing.available_at,

                existing.source_record_id
            )


            if (
                candidate_key
                > existing_key
            ):

                latest_by_rate[
                    row.rate_name
                ] = row


        selected_rows = sorted(

            latest_by_rate.values(),

            key=lambda row:
                row.rate_name
        )


        return tuple(

            self._to_schema(
                row
            )

            for row
            in selected_rows
        )


class EconomicCalendarRepository:

    def __init__(
        self,
        source="ECB",
        currency=None,
        jurisdiction=None
    ):

        self.source = source

        self.currency = currency

        self.jurisdiction = jurisdiction


    def _validate_datetime(
        self,
        value,
        name
    ):

        if not isinstance(
            value,
            datetime
        ):

            raise TypeError(
                f"{name} must be datetime."
            )


        if (
            value.tzinfo
            is None
        ):

            raise ValueError(
                f"{name} must be "
                "timezone-aware."
            )


    def _to_schema(
        self,
        row
    ):

        return EconomicCalendarEventSnapshot(

            source_event_id=
                row.source_event_id,

            event_name=
                row.event_name,

            jurisdiction=
                row.jurisdiction,

            currency=
                row.currency,

            scheduled_at=
                row.scheduled_at,

            available_at=
                row.available_at,

            actual=
                (
                    None
                    if row.actual is None
                    else float(
                        row.actual
                    )
                ),

            forecast=
                (
                    None
                    if row.forecast is None
                    else float(
                        row.forecast
                    )
                ),

            previous=
                (
                    None
                    if row.previous is None
                    else float(
                        row.previous
                    )
                ),

            unit=
                row.unit,

            content_hash=
                row.content_hash,

            source=
                row.source,

            ingested_at=
                row.ingested_at
        )


    def _get_available_rows(
        self,
        as_of,
        scheduled_start,
        scheduled_end
    ):

        self._validate_datetime(
            as_of,
            "as_of"
        )

        self._validate_datetime(
            scheduled_start,
            "scheduled_start"
        )

        self._validate_datetime(
            scheduled_end,
            "scheduled_end"
        )


        if (
            scheduled_end
            <= scheduled_start
        ):

            raise ValueError(
                "scheduled_end must be "
                "after scheduled_start."
            )


        filters = [

            EconomicCalendarEvent.source
            == self.source,

            EconomicCalendarEvent.available_at
            <= as_of,

            EconomicCalendarEvent.scheduled_at
            >= scheduled_start,

            EconomicCalendarEvent.scheduled_at
            < scheduled_end
        ]


        if self.currency is not None:

            filters.append(
                EconomicCalendarEvent.currency
                == self.currency
            )


        if self.jurisdiction is not None:

            filters.append(
                EconomicCalendarEvent.jurisdiction
                == self.jurisdiction
            )


        session = SessionLocal()


        try:

            statement = (

                select(
                    EconomicCalendarEvent
                )

                .where(
                    *filters
                )

                .order_by(

                    EconomicCalendarEvent
                    .scheduled_at
                    .asc(),

                    EconomicCalendarEvent
                    .source_event_id
                    .asc(),

                    EconomicCalendarEvent
                    .available_at
                    .asc(),

                    EconomicCalendarEvent
                    .id
                    .asc()
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


    def _latest_versions(
        self,
        rows
    ):

        latest_by_event = {}


        for row in rows:

            existing = (
                latest_by_event.get(
                    row.source_event_id
                )
            )


            if existing is None:

                latest_by_event[
                    row.source_event_id
                ] = row

                continue


            if (
                row.available_at
                > existing.available_at
            ):

                latest_by_event[
                    row.source_event_id
                ] = row

                continue


            if (
                row.available_at
                == existing.available_at
            ):

                if (
                    row.content_hash
                    != existing.content_hash
                ):

                    raise ValueError(
                        "Ambiguous economic-calendar "
                        "revisions share the same "
                        "source_event_id and "
                        "available_at."
                    )


                if (
                    row.id
                    > existing.id
                ):

                    latest_by_event[
                        row.source_event_id
                    ] = row


        return tuple(
            latest_by_event.values()
        )


    def get_window_as_of(
        self,
        as_of,
        scheduled_start,
        scheduled_end
    ):

        """
        Return the causal calendar information
        set for the half-open scheduled window:

            [scheduled_start, scheduled_end)

        A future scheduled event remains visible
        when its exact record version was public
        by `as_of`.

        Exactly one causal version is returned
        per source_event_id.
        """

        rows = (
            self._get_available_rows(

                as_of=
                    as_of,

                scheduled_start=
                    scheduled_start,

                scheduled_end=
                    scheduled_end
            )
        )


        selected_rows = list(
            self._latest_versions(
                rows
            )
        )


        selected_rows.sort(
            key=lambda row: (
                row.scheduled_at,
                row.source_event_id
            )
        )


        return tuple(

            self._to_schema(
                row
            )

            for row
            in selected_rows
        )