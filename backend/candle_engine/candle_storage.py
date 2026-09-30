from sqlalchemy.exc import IntegrityError

from backend.database.connection import SessionLocal
from backend.database.models import Candle


class CandleStorage:

    def exists(
        self,
        symbol,
        timeframe,
        timestamp
    ):

        session = SessionLocal()

        try:

            return (
                session.query(Candle)
                .filter(
                    Candle.symbol == symbol,
                    Candle.timeframe == timeframe,
                    Candle.timestamp == timestamp
                )
                .first()
                is not None
            )

        finally:

            session.close()


    def save(
        self,
        candle
    ):

        session = SessionLocal()

        try:

            record = Candle(

                symbol=
                    candle["symbol"],

                timeframe=
                    candle["timeframe"],

                open=
                    candle["open"],

                high=
                    candle["high"],

                low=
                    candle["low"],

                close=
                    candle["close"],

                tick_volume=
                    candle["tick_volume"],

                average_spread_pips=
                    candle[
                        "average_spread_pips"
                    ],

                max_spread_pips=
                    candle[
                        "max_spread_pips"
                    ],

                is_complete=
                    candle["is_complete"],

                timestamp=
                    candle["timestamp"]
            )

            session.add(record)
            session.commit()

            return True

        except IntegrityError:

            session.rollback()

            return False

        except Exception as error:

            session.rollback()

            print(
                "CANDLE STORAGE ERROR:",
                error
            )

            return False

        finally:

            session.close()
            