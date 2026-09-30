from sqlalchemy import func

from sqlalchemy.dialects.postgresql import (
    insert
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    BrokerCandle
)


class BrokerCandleStorage:

    def get_latest_timestamp(
        self,
        symbol,
        timeframe
    ):

        session = SessionLocal()

        try:

            return (
                session.query(
                    func.max(
                        BrokerCandle.timestamp
                    )
                )
                .filter(
                    BrokerCandle.symbol
                    == symbol,

                    BrokerCandle.timeframe
                    == timeframe,

                    BrokerCandle.source
                    == "MT5"
                )
                .scalar()
            )

        finally:

            session.close()


    def save_batch(
        self,
        rows
    ):

        if not rows:
            return 0

        session = SessionLocal()

        try:

            statement = (
                insert(BrokerCandle)
                .values(rows)
                .on_conflict_do_nothing(
                    constraint=
                    "uq_broker_candle"
                )
            )

            result = session.execute(
                statement
            )

            session.commit()

            return result.rowcount or 0

        except Exception:

            session.rollback()
            raise

        finally:

            session.close()
            