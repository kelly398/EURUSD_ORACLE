from datetime import datetime

from sqlalchemy import select

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    BrokerCandle
)

from backend.market_state.schemas import (
    CandleBar
)


class CandleRepository:

    def __init__(
        self,
        source="MT5"
    ):

        self.source = source


    def _to_schema(
        self,
        row
    ):

        return CandleBar(

            symbol=row.symbol,

            timeframe=row.timeframe,

            timestamp=row.timestamp,

            open=row.open,

            high=row.high,

            low=row.low,

            close=row.close,

            tick_volume=row.tick_volume,

            spread_points=row.spread_points,

            real_volume=row.real_volume,

            source=row.source
        )


    def get_latest(
        self,
        symbol="EURUSD",
        timeframe="M5",
        limit=300
    ):

        if limit <= 0:

            raise ValueError(
                "limit must be greater "
                "than zero."
            )


        session = SessionLocal()


        try:

            statement = (

                select(
                    BrokerCandle
                )

                .where(

                    BrokerCandle.symbol
                    == symbol,

                    BrokerCandle.timeframe
                    == timeframe,

                    BrokerCandle.source
                    == self.source,

                    BrokerCandle.is_complete
                    .is_(True)
                )

                .order_by(
                    BrokerCandle.timestamp.desc()
                )

                .limit(limit)
            )


            rows = (

                session.execute(
                    statement
                )

                .scalars()

                .all()
            )


            # SQL returned newest first.
            # Intelligence engines require
            # chronological order.
            rows.reverse()


            return [

                self._to_schema(
                    row
                )

                for row in rows
            ]


        finally:

            session.close()


    def get_latest_as_of(
        self,
        latest_timestamp: datetime,
        symbol="EURUSD",
        timeframe="M5",
        limit=300
    ):

        if limit <= 0:

            raise ValueError(
                "limit must be greater "
                "than zero."
            )


        if (
            latest_timestamp.tzinfo
            is None
        ):

            raise ValueError(
                "latest_timestamp must "
                "be timezone-aware."
            )


        session = SessionLocal()


        try:

            statement = (

                select(
                    BrokerCandle
                )

                .where(

                    BrokerCandle.symbol
                    == symbol,

                    BrokerCandle.timeframe
                    == timeframe,

                    BrokerCandle.source
                    == self.source,

                    BrokerCandle.is_complete
                    .is_(True),

                    BrokerCandle.timestamp
                    <= latest_timestamp
                )

                .order_by(
                    BrokerCandle.timestamp.desc()
                )

                .limit(limit)
            )


            rows = (

                session.execute(
                    statement
                )

                .scalars()

                .all()
            )


            rows.reverse()


            return [

                self._to_schema(
                    row
                )

                for row in rows
            ]


        finally:

            session.close()


    def get_between(
        self,
        start: datetime,
        end: datetime,
        symbol="EURUSD",
        timeframe="M5"
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
                    BrokerCandle
                )

                .where(

                    BrokerCandle.symbol
                    == symbol,

                    BrokerCandle.timeframe
                    == timeframe,

                    BrokerCandle.source
                    == self.source,

                    BrokerCandle.is_complete
                    .is_(True),

                    BrokerCandle.timestamp
                    >= start,

                    BrokerCandle.timestamp
                    < end
                )

                .order_by(
                    BrokerCandle.timestamp.asc()
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
            