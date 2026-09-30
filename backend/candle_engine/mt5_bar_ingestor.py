from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.data_engine.mt5_provider import (
    MT5Provider
)

from backend.candle_engine.broker_candle_storage import (
    BrokerCandleStorage
)

from backend.candle_engine.timeframe import (
    floor_time
)


class MT5BarIngestor:

    def __init__(
        self,
        symbol="EURUSD",
        timeframe="M5",
        timeframe_minutes=5,
        bootstrap_hours=24,
        repair_lookback_minutes=60
    ):

        self.symbol = symbol

        self.timeframe = timeframe

        self.timeframe_minutes = (
            timeframe_minutes
        )

        self.bootstrap_hours = (
            bootstrap_hours
        )

        self.repair_lookback_minutes = (
            repair_lookback_minutes
        )

        self.provider = MT5Provider()

        self.storage = (
            BrokerCandleStorage()
        )


    def connect(self):

        return self.provider.connect()


    def disconnect(self):

        self.provider.disconnect()


    def ingest_once(self):

        price = self.provider.get_price(
            self.symbol
        )

        if price is None:

            print(
                "No current MT5 price."
            )

            return 0


        broker_now = datetime.fromtimestamp(
            price["time_msc"] / 1000,
            tz=timezone.utc
        )


        current_boundary = floor_time(
            broker_now,
            self.timeframe_minutes
        )


        latest_stored = (
            self.storage
            .get_latest_timestamp(
                self.symbol,
                self.timeframe
            )
        )


        if latest_stored is None:

            date_from = (
                current_boundary
                - timedelta(
                    hours=
                    self.bootstrap_hours
                )
            )

        else:

            date_from = (
                latest_stored
                - timedelta(
                    minutes=
                    self.repair_lookback_minutes
                )
            )


        rates = self.provider.get_candles(

            symbol=self.symbol,

            timeframe=self.timeframe,

            date_from=date_from,

            date_to=current_boundary
        )


        if len(rates) == 0:

            print(
                "No MT5 bars returned."
            )

            return 0


        rows = []


        for rate in rates:

            bar_time = (
                datetime.fromtimestamp(
                    int(rate["time"]),
                    tz=timezone.utc
                )
            )


            # Critical rule:
            # never persist the currently forming bar.
            if bar_time >= current_boundary:

                continue


            rows.append(
                {
                    "symbol":
                        self.symbol,

                    "timeframe":
                        self.timeframe,

                    "timestamp":
                        bar_time,

                    "open":
                        float(
                            rate["open"]
                        ),

                    "high":
                        float(
                            rate["high"]
                        ),

                    "low":
                        float(
                            rate["low"]
                        ),

                    "close":
                        float(
                            rate["close"]
                        ),

                    "tick_volume":
                        int(
                            rate[
                                "tick_volume"
                            ]
                        ),

                    "spread_points":
                        int(
                            rate["spread"]
                        ),

                    "real_volume":
                        float(
                            rate[
                                "real_volume"
                            ]
                        ),

                    "is_complete":
                        True,

                    "source":
                        "MT5"
                }
            )


        inserted = (
            self.storage.save_batch(
                rows
            )
        )


        print(
            f"M5 INGEST | "
            f"fetched={len(rates)} | "
            f"complete={len(rows)} | "
            f"inserted={inserted} | "
            f"boundary="
            f"{current_boundary.isoformat()}"
        )


        return inserted
        