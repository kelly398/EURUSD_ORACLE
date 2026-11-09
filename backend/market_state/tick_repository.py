from datetime import datetime

from sqlalchemy import select

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    Tick
)

from backend.data_engine.feed_identity import require_feed_id

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
                row.spread_pips,

            feed_id=row.feed_id,
        )


    def __init__(self, feed_id=None):

        if feed_id is not None:
            require_feed_id(feed_id)

        self.feed_id = feed_id


    def _feed_filter(self):

        if self.feed_id is None:
            return Tick.feed_id.is_(None)

        return Tick.feed_id == self.feed_id


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
                    < end,

                    self._feed_filter()
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
