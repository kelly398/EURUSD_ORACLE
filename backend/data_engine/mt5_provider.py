from datetime import datetime

import MetaTrader5 as mt5

from backend.data_engine.base_provider import MarketDataProvider


class MT5Provider(MarketDataProvider):

    TIMEFRAMES = {
        "M1": mt5.TIMEFRAME_M1,
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }


    def __init__(self):

        self.connected = False


    def is_healthy(
        self
    ):

        if not self.connected:

            return False


        terminal = (
            mt5.terminal_info()
        )


        if terminal is None:

            return False


        return bool(
            terminal.connected
        )


    def connect(
        self
    ):

        if self.is_healthy():

            return True


        # Clean any stale local MT5
        # Python API state before
        # attempting a reconnect.
        mt5.shutdown()

        self.connected = False


        if mt5.initialize(
            timeout=10000
        ):

            terminal = (
                mt5.terminal_info()
            )


            if (
                terminal is not None
                and
                terminal.connected
            ):

                self.connected = True

                return True


            mt5.shutdown()


        print(
            "MT5 INITIALIZATION ERROR:",
            mt5.last_error(),
            flush=True
        )


        self.connected = False

        return False


    def disconnect(
        self
    ):

        try:

            mt5.shutdown()

        finally:

            self.connected = False


    def get_price(
        self,
        symbol
    ):

        if not self.connected:

            raise RuntimeError(
                "MT5 not connected"
            )

        tick = mt5.symbol_info_tick(
            symbol
        )

        if tick is None:

            print(
                "MT5 PRICE ERROR:",
                mt5.last_error(),
                flush=True
            )

            return None

        return {
            "symbol": symbol,
            "bid": float(tick.bid),
            "ask": float(tick.ask),
            "time": int(tick.time),
            "time_msc": int(
                getattr(
                    tick,
                    "time_msc",
                    tick.time * 1000
                )
            ),
        }


    def get_ticks_range(
        self,
        symbol,
        date_from: datetime,
        date_to: datetime
    ):

        if not self.connected:

            raise RuntimeError(
                "MT5 not connected"
            )

        ticks = mt5.copy_ticks_range(
            symbol,
            date_from,
            date_to,
            mt5.COPY_TICKS_ALL
        )

        if ticks is None:

            print(
                "MT5 TICK HISTORY ERROR:",
                mt5.last_error()
            )

            return []

        return ticks


    def get_candles(
        self,
        symbol,
        timeframe,
        date_from=None,
        date_to=None
    ):

        if not self.connected:

            raise RuntimeError(
                "MT5 not connected"
            )

        mt5_timeframe = (
            self.TIMEFRAMES.get(
                timeframe
            )
        )

        if mt5_timeframe is None:

            raise ValueError(
                f"Unsupported timeframe: "
                f"{timeframe}"
            )

        if (
            date_from is None
            or date_to is None
        ):

            raise ValueError(
                "date_from and date_to "
                "are required"
            )

        rates = mt5.copy_rates_range(
            symbol,
            mt5_timeframe,
            date_from,
            date_to
        )

        if rates is None:

            print(
                "MT5 CANDLE HISTORY ERROR:",
                mt5.last_error()
            )

            return []

        return rates
        