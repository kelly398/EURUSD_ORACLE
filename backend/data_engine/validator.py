from backend.data_engine.tick_model import MarketTick


class TickValidator:

    def validate(
        self,
        tick: MarketTick
    ) -> bool:

        if tick.bid <= 0:
            return False

        if tick.ask <= 0:
            return False

        if tick.ask < tick.bid:
            return False

        if tick.spread_pips < 0:
            return False

        # Do not reject wide spreads here.
        # Wide spreads are genuine market information.
        # Risk management will decide whether trading is allowed.

        return True
        