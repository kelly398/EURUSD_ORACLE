
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

        # The feed-aware database migration and readers are not active yet.
        # Reject feed-labelled or mixed rows before opening a DB session.
        if any("feed_id" in row for row in rows):
            raise RuntimeError(
                "Feed-labelled candle writes are blocked pending approved "
                "database migration and feed-isolated readers."
            )

        session = SessionLocal()

        try:

            statement = (
                insert(BrokerCandle)
                .values(rows)
                # Compatible with the existing UNIQUE constraint and the
                # future partial uniqueness indexes after coordinated cutover.
                # DO NOT remove the old constraint in this milestone.
                .on_conflict_do_nothing()
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
