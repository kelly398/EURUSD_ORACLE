from backend.data_engine.tick_model import MarketTick


class TickValidator:


    def validate(self, tick: MarketTick):


        if tick.bid <= 0:

            return False


        if tick.ask <= 0:

            return False


        if tick.ask < tick.bid:

            return False


        return True