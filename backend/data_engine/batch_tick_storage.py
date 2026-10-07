from sqlalchemy import func

from sqlalchemy.dialects.postgresql import (
    insert
)

from backend.database.connection import (
    SessionLocal
)

from backend.database.models import (
    Tick
)


class BatchTickStorage:

    INSERT_BATCH_SIZE = 5000


    @staticmethod
    def _chunked(
        rows,
        size
    ):

        for start in range(
            0,
            len(rows),
            size
        ):

            yield rows[
                start:
                start + size
            ]


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


        inserted = 0

        session = SessionLocal()


        try:

            for batch in self._chunked(
                rows,
                self.INSERT_BATCH_SIZE
            ):

                statement = (

                    insert(
                        Tick
                    )

                    .values(
                        batch
                    )

                    .on_conflict_do_nothing(
                        index_elements=[
                            "fingerprint"
                        ]
                    )
                )


                result = (
                    session.execute(
                        statement
                    )
                )


                session.commit()


                if (
                    result.rowcount
                    is not None
                    and
                    result.rowcount > 0
                ):

                    inserted += (
                        result.rowcount
                    )


            return inserted


        except Exception:

            session.rollback()

            raise


        finally:

            session.close()