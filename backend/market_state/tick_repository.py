from datetime import datetime

from sqlalchemy import select

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    Tick
)

from backend.market_state.schemas import (
    TickSample
)


class TickRepository:

    def _to_schema(
        self,
        row
    ):

        return TickSample(

            symbol=row.symbol,

            timestamp=row.timestamp,

            source_time_msc=
                row.source_time_msc,

            bid=row.bid,

            ask=row.ask,

            spread_pips=
                row.spread_pips
        )


    def get_between(
        self,
        start: datetime,
        end: datetime,
        symbol="EURUSD"
    ):

        if end <= start:

            raise ValueError(
                "end must be later "
                "than start."
            )


        session = SessionLocal()


        try:

            statement = (

                select(
                    Tick
                )

                .where(

                    Tick.symbol
                    == symbol,

                    Tick.timestamp
                    >= start,

                    Tick.timestamp
                    < end
                )

                .order_by(
                    Tick.timestamp.asc(),
                    Tick.source_time_msc.asc(),
                    Tick.id.asc()
                )
            )


            rows = (

                session.execute(
                    statement
                )

                .scalars()

                .all()
            )


            return [

                self._to_schema(
                    row
                )

                for row in rows
            ]


        finally:

            session.close()
            