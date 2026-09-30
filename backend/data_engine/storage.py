from backend.database.connection import SessionLocal
from backend.database.models import Tick


class TickStorage:


    def save(self, tick):

        session = SessionLocal()

        try:

            record = Tick(

                symbol=tick.symbol,

                bid=tick.bid,

                ask=tick.ask,

                spread=tick.spread,

                timestamp=tick.timestamp

            )

            session.add(record)

            session.commit()


        except Exception as error:

            session.rollback()

            print(
                "DATABASE ERROR:",
                error
            )


        finally:

            session.close()
            