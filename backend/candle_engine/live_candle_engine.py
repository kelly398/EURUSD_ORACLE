from backend.database.connection import SessionLocal
from backend.database.models import Tick

from backend.data_engine.tick_model import MarketTick

from backend.candle_engine.candle_builder import CandleBuilder
from backend.candle_engine.candle_storage import CandleStorage
from backend.candle_engine.timeframe import previous_window


class LiveCandleEngine:

    def __init__(
        self,
        symbol="EURUSD",
        timeframe_minutes=5
    ):

        self.symbol = symbol
        self.timeframe_minutes = timeframe_minutes
        self.timeframe = f"M{timeframe_minutes}"

        self.builder = CandleBuilder()
        self.storage = CandleStorage()


    def get_latest_tick_timestamp(self):

        session = SessionLocal()

        try:

            latest_tick = (
                session.query(Tick)
                .filter(
                    Tick.symbol == self.symbol
                )
                .order_by(
                    Tick.timestamp.desc()
                )
                .first()
            )

            if latest_tick is None:
                return None

            return latest_tick.timestamp

        finally:

            session.close()


    def get_ticks_for_window(
        self,
        start,
        end
    ):

        session = SessionLocal()

        try:

            records = (
                session.query(Tick)
                .filter(
                    Tick.symbol == self.symbol,
                    Tick.timestamp >= start,
                    Tick.timestamp < end
                )
                .order_by(
                    Tick.timestamp.asc()
                )
                .all()
            )

            return [
                MarketTick(
                    symbol=row.symbol,
                    bid=row.bid,
                    ask=row.ask,
                    timestamp=row.timestamp,
                    source_time_msc=row.source_time_msc
                )
                for row in records
            ]

        finally:

            session.close()


    def generate_latest_completed_candle(self):

        latest_tick_time = (
            self.get_latest_tick_timestamp()
        )

        if latest_tick_time is None:

            print(
                "No EURUSD ticks available."
            )

            return None


        start, end = previous_window(
            latest_tick_time,
            self.timeframe_minutes
        )


        if self.storage.exists(
            self.symbol,
            self.timeframe,
            start
        ):

            return None


        ticks = self.get_ticks_for_window(
            start,
            end
        )


        if not ticks:

            print(
                f"No ticks in completed window "
                f"{start.isoformat()} -> "
                f"{end.isoformat()}"
            )

            return None


        candle = self.builder.build_candle(

            ticks=ticks,

            symbol=self.symbol,

            timeframe=self.timeframe,

            candle_start=start
        )


        if self.storage.save(candle):

            print(
                "M5 CANDLE CREATED:",
                candle
            )

            return candle


        return None
        