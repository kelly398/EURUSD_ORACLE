from sqlalchemy.exc import IntegrityError

from backend.database.connection import SessionLocal
from backend.database.models import Tick


class TickStorage:

    def save(self, tick) -> bool:

        session = SessionLocal()

        try:

            record = Tick(
                symbol=tick.symbol,
                bid=tick.bid,
                ask=tick.ask,
                spread=tick.spread,
                spread_pips=tick.spread_pips,
                source_time_msc=tick.source_time_msc,
                timestamp=tick.timestamp
            )

            session.add(record)
            session.commit()

            return True

        except IntegrityError:

            session.rollback()

            # Same broker tick already stored.
            return False

        except Exception as error:

            session.rollback()

            print(
                "TICK STORAGE ERROR:",
                error
            )

            return False

        finally:

            session.close()
            