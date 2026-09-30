import time

from datetime import datetime, timezone

from backend.data_engine.mt5_provider import MT5Provider
from backend.data_engine.tick_model import MarketTick
from backend.data_engine.validator import TickValidator
from backend.data_engine.storage import TickStorage


class TickCollector:

    def __init__(
        self,
        symbol="EURUSD",
        poll_interval=0.10
    ):

        self.symbol = symbol
        self.poll_interval = poll_interval

        self.provider = MT5Provider()
        self.validator = TickValidator()
        self.storage = TickStorage()

        self.last_source_time_msc = None


    def start(self):

        if not self.provider.connect():

            print(
                "Unable to connect to MT5"
            )

            return

        print(
            "Tick collector started"
        )

        try:

            while True:

                price = self.provider.get_price(
                    self.symbol
                )

                if price is None:

                    time.sleep(
                        self.poll_interval
                    )

                    continue

                source_time_msc = (
                    price["time_msc"]
                )

                # MT5 returns the last known tick repeatedly
                # when no new tick has arrived.
                if (
                    source_time_msc
                    == self.last_source_time_msc
                ):

                    time.sleep(
                        self.poll_interval
                    )

                    continue

                timestamp = datetime.fromtimestamp(
                    source_time_msc / 1000,
                    tz=timezone.utc
                )

                tick = MarketTick(
                    symbol=price["symbol"],
                    bid=price["bid"],
                    ask=price["ask"],
                    timestamp=timestamp,
                    source_time_msc=source_time_msc
                )

                if self.validator.validate(tick):

                    stored = self.storage.save(
                        tick
                    )

                    if stored:

                        print(
                            "STORED:",
                            tick
                        )

                self.last_source_time_msc = (
                    source_time_msc
                )

                time.sleep(
                    self.poll_interval
                )

        except KeyboardInterrupt:

            print(
                "\nTick collector stopping..."
            )

        finally:

            self.provider.disconnect()

            print(
                "MT5 disconnected cleanly."
            )
            