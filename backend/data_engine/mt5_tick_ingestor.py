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
        overlap_seconds=2,
        catchup_window_minutes=60,
        recovery_log_threshold_seconds=30
    ):

        if (
            catchup_window_minutes
            <= 0
        ):

            raise ValueError(
                "catchup_window_minutes must "
                "be greater than zero."
            )


        self.symbol = (
            symbol
        )

        self.recovery_log_threshold_seconds = (
            recovery_log_threshold_seconds
        )

        self.bootstrap_minutes = (
            bootstrap_minutes
        )

        self.overlap_seconds = (
            overlap_seconds
        )

        self.catchup_window_minutes = (
            catchup_window_minutes
        )


        self.provider = (
            MT5Provider()
        )


        self.storage = (
            BatchTickStorage()
        )


    def _convert_tick(
        self,
        tick
    ):

        time_msc = int(
            tick[
                "time_msc"
            ]
        )


        bid = float(
            tick[
                "bid"
            ]
        )


        ask = float(
            tick[
                "ask"
            ]
        )


        last = float(
            tick[
                "last"
            ]
        )


        volume = float(
            tick[
                "volume"
            ]
        )


        flags = int(
            tick[
                "flags"
            ]
        )


        volume_real = float(
            tick[
                "volume_real"
            ]
        )


        timestamp = (
            datetime.fromtimestamp(

                time_msc
                / 1000.0,

                tz=timezone.utc
            )
        )


        spread = (
            ask - bid
        )


        spread_pips = round(
            spread * 10000.0,
            6
        )


        fingerprint = (
            create_tick_fingerprint(

                symbol=
                    self.symbol,

                time_msc=
                    time_msc,

                bid=
                    bid,

                ask=
                    ask,

                last=
                    last,

                volume=
                    volume,

                flags=
                    flags,

                volume_real=
                    volume_real
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


    def ingest_once(
        self
    ):

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
            latest_price[
                "time_msc"
            ]
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

                    broker_now_msc
                    / 1000.0,

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

                    start_msc
                    / 1000.0,

                    tz=timezone.utc
                )
            )


        date_to = (
            datetime.fromtimestamp(

                broker_now_msc
                / 1000.0,

                tz=timezone.utc
            )
        )

        gap_seconds = max(
            0.0,
            (
                date_to
                - date_from
            ).total_seconds()
        )


        verbose_recovery = (
            gap_seconds
            >= self.recovery_log_threshold_seconds
        )

        if verbose_recovery:
            print(
                "TICK RECOVERY | "
                f"from={date_from.isoformat()} | "
                f"to={date_to.isoformat()}",
                flush=True
            )

        if (
            date_from
            >= date_to
        ):

            return 0


        total_fetched = 0

        total_inserted = 0


        window_start = (
            date_from
        )


        while (
            window_start
            < date_to
        ):

            window_end = min(

                window_start

                + timedelta(
                    minutes=
                        self.catchup_window_minutes
                ),

                date_to
            )


            if verbose_recovery:

                print(
                    "TICK WINDOW | "
                    f"from={window_start.isoformat()} | "
                    f"to={window_end.isoformat()} | "
                    "fetching...",
                    flush=True
                )


            ticks = (
                self.provider
                .get_ticks_range(

                    self.symbol,

                    window_start,

                    window_end
                )
            )


            fetched = (
                len(ticks)
            )


            rows = [

                self._convert_tick(
                    tick
                )

                for tick
                in ticks
            ]


            inserted = (
                self.storage.save_batch(
                    rows
                )
            )


            total_fetched += (
                fetched
            )


            total_inserted += (
                inserted
            )


            if verbose_recovery:

                print(
                    "TICK WINDOW DONE | "
                    f"fetched={fetched} | "
                    f"inserted={inserted} | "
                    f"through="
                    f"{window_end.isoformat()}",
                    flush=True
                )


            del rows

            del ticks


            window_start = (
                window_end
            )


        if verbose_recovery:

            print(
                "TICK INGEST COMPLETE | "
                f"fetched={total_fetched} | "
                f"inserted={total_inserted} | "
                f"through={date_to.isoformat()}",
                flush=True
            )


        return (
            total_inserted
        )


    def connect(
        self
    ):

        return (
            self.provider.connect()
        )


    def disconnect(
        self
    ):

        self.provider.disconnect()