from datetime import (
    datetime,
    timedelta,
    timezone
)

from backend.data_engine.mt5_provider import (
    MT5Provider
)

from backend.data_engine.batch_tick_storage import (
    BatchTickStorage
)

from backend.data_engine.tick_identity import (
    create_tick_fingerprint
)


class MT5TickIngestor:

    def __init__(
        self,
        symbol="EURUSD",
        bootstrap_minutes=30,
        overlap_seconds=2
    ):

        self.symbol = symbol

        self.bootstrap_minutes = (
            bootstrap_minutes
        )

        self.overlap_seconds = (
            overlap_seconds
        )

        self.provider = MT5Provider()

        self.storage = BatchTickStorage()


    def _convert_tick(
        self,
        tick
    ):

        time_msc = int(
            tick["time_msc"]
        )

        bid = float(
            tick["bid"]
        )

        ask = float(
            tick["ask"]
        )

        last = float(
            tick["last"]
        )

        volume = float(
            tick["volume"]
        )

        flags = int(
            tick["flags"]
        )

        volume_real = float(
            tick["volume_real"]
        )

        timestamp = datetime.fromtimestamp(
            time_msc / 1000,
            tz=timezone.utc
        )

        spread = ask - bid

        spread_pips = round(
            spread * 10000,
            6
        )

        fingerprint = (
            create_tick_fingerprint(
                symbol=self.symbol,
                time_msc=time_msc,
                bid=bid,
                ask=ask,
                last=last,
                volume=volume,
                flags=flags,
                volume_real=volume_real
            )
        )

        return {

            "symbol":
                self.symbol,

            "timestamp":
                timestamp,

            "source_time_msc":
                time_msc,

            "bid":
                bid,

            "ask":
                ask,

            "last":
                last,

            "volume":
                volume,

            "flags":
                flags,

            "volume_real":
                volume_real,

            "spread":
                spread,

            "spread_pips":
                spread_pips,

            "fingerprint":
                fingerprint
        }


    def ingest_once(self):

        latest_price = (
            self.provider.get_price(
                self.symbol
            )
        )

        if latest_price is None:

            print(
                "No current MT5 price."
            )

            return 0


        broker_now_msc = int(
            latest_price["time_msc"]
        )


        latest_stored_msc = (
            self.storage
            .get_latest_time_msc(
                self.symbol
            )
        )


        if latest_stored_msc is None:

            date_from = (
                datetime.fromtimestamp(
                    broker_now_msc / 1000,
                    tz=timezone.utc
                )
                - timedelta(
                    minutes=
                    self.bootstrap_minutes
                )
            )

        else:

            start_msc = (
                latest_stored_msc
                - (
                    self.overlap_seconds
                    * 1000
                )
            )

            date_from = (
                datetime.fromtimestamp(
                    start_msc / 1000,
                    tz=timezone.utc
                )
            )


        date_to = datetime.fromtimestamp(
            broker_now_msc / 1000,
            tz=timezone.utc
        )


        ticks = (
            self.provider.get_ticks_range(
                self.symbol,
                date_from,
                date_to
            )
        )


        if len(ticks) == 0:

            return 0


        rows = [

            self._convert_tick(
                tick
            )

            for tick in ticks
        ]


        inserted = (
            self.storage.save_batch(
                rows
            )
        )


        print(
            f"TICK INGEST | "
            f"fetched={len(ticks)} | "
            f"inserted={inserted} | "
            f"through={date_to.isoformat()}"
        )


        return inserted


    def connect(self):

        return self.provider.connect()


    def disconnect(self):

        self.provider.disconnect()
        