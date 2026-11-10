from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    DataQualityEvent
)
from backend.data_engine.feed_identity import require_feed_id


class DataQualityEventStorage:

    def save(
        self,
        symbol,
        timeframe,
        check_name,
        severity,
        message,
        feed_id=None,
    ):

        if feed_id is not None:
            require_feed_id(feed_id)

        session = SessionLocal()

        try:

            event = DataQualityEvent(
                symbol=symbol,
                timeframe=timeframe,
                check_name=check_name,
                severity=severity,
                message=message,
                feed_id=feed_id,
            )

            session.add(event)
            session.commit()

        except Exception as error:

            session.rollback()

            print(
                "DATA QUALITY EVENT "
                "STORAGE ERROR:",
                error
            )

        finally:

            session.close()

