from sqlalchemy import func

from sqlalchemy.dialects.postgresql import (
    insert
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import Tick


class BatchTickStorage:

    def get_latest_time_msc(
        self,
        symbol
    ):

        session = SessionLocal()

        try:

            return (
                session.query(
                    func.max(
                        Tick.source_time_msc
                    )
                )
                .filter(
                    Tick.symbol == symbol
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
                insert(Tick)
                .values(rows)
                .on_conflict_do_nothing(
                    index_elements=[
                        "fingerprint"
                    ]
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
            