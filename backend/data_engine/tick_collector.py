import time
from datetime import datetime


from backend.data_engine.mt5_provider import MT5Provider

from backend.data_engine.tick_model import MarketTick

from backend.data_engine.validator import TickValidator



class TickCollector:


    def __init__(self):

        self.provider = MT5Provider()

        self.validator = TickValidator()



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
                    "EURUSD"
                )


                tick = MarketTick(

                    symbol=price["symbol"],

                    bid=price["bid"],

                    ask=price["ask"],

                    timestamp=datetime.now()

                )


                if self.validator.validate(tick):

                    print(
                    tick
                    )


                TICK_INTERVAL = 1



        except KeyboardInterrupt:


            print(
            "Collector stopped"
            )


            self.provider.disconnect()
