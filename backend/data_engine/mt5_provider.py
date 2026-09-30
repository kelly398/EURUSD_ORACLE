import MetaTrader5 as mt5

from backend.data_engine.base_provider import MarketDataProvider



class MT5Provider(MarketDataProvider):


    def __init__(self):

        self.connected = False



    def connect(self):

        result = mt5.initialize()


        if result:

            self.connected = True

            return True


        else:

            print(
                mt5.last_error()
            )

            return False



    def disconnect(self):

        mt5.shutdown()

        self.connected = False



    def get_price(self, symbol):


        if not self.connected:

            raise Exception(
                "MT5 not connected"
            )


        tick = mt5.symbol_info_tick(
            symbol
        )


        if tick is None:

            return None


        return {

            "symbol": symbol,

            "bid": tick.bid,

            "ask": tick.ask,

            "time": tick.time

        }

    def get_candles(self, symbol, timeframe, count):

        return []

