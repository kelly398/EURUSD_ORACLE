class CandleBuilder:

    def build_candle(
        self,
        ticks,
        symbol,
        timeframe,
        candle_start
    ):

        if not ticks:
            return None

        mid_prices = [
            (tick.bid + tick.ask) / 2
            for tick in ticks
        ]

        spread_pips = [
            tick.spread_pips
            for tick in ticks
        ]

        return {

            "symbol":
                symbol,

            "timeframe":
                timeframe,

            "open":
                mid_prices[0],

            "high":
                max(mid_prices),

            "low":
                min(mid_prices),

            "close":
                mid_prices[-1],

            "tick_volume":
                len(ticks),

            "average_spread_pips":
                sum(spread_pips)
                / len(spread_pips),

            "max_spread_pips":
                max(spread_pips),

            "is_complete":
                True,

            "timestamp":
                candle_start
        }
        