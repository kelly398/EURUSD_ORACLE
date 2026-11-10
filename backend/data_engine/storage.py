from sqlalchemy.exc import IntegrityError

from backend.database.connection import SessionLocal
from backend.database.models import Tick
from backend.data_engine.tick_identity import create_tick_fingerprint


class TickStorage:

    def save(self, tick) -> bool:

        session = SessionLocal()

        try:

            # MarketTick is the minimal legacy collector shape. Keep its
            # fingerprint tuple aligned with the model's zero defaults for
            # fields the collector does not receive from its provider.
            last = 0.0
            volume = 0.0
            flags = 0
            volume_real = 0.0
            fingerprint = create_tick_fingerprint(
                symbol=tick.symbol,
                time_msc=tick.source_time_msc,
                bid=tick.bid,
                ask=tick.ask,
                last=last,
                volume=volume,
                flags=flags,
                volume_real=volume_real,
            )

            record = Tick(
                symbol=tick.symbol,
                bid=tick.bid,
                ask=tick.ask,
                last=last,
                volume=volume,
                flags=flags,
                volume_real=volume_real,
                spread=tick.spread,
                spread_pips=tick.spread_pips,
                source_time_msc=tick.source_time_msc,
                timestamp=tick.timestamp,
                fingerprint=fingerprint,
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
