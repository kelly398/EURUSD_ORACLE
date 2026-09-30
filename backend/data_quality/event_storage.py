from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    DataQualityEvent
)


class DataQualityEventStorage:

    def save(
        self,
        symbol,
        timeframe,
        check_name,
        severity,
        message
    ):

        session = SessionLocal()

        try:

            event = DataQualityEvent(
                symbol=symbol,
                timeframe=timeframe,
                check_name=check_name,
                severity=severity,
                message=message
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
            