from dataclasses import dataclass
from datetime import datetime


@dataclass
class MarketTick:

    symbol: str

    bid: float

    ask: float

    timestamp: datetime


    @property
    def spread(self):

        return self.ask - self.bid


    @property
    def spread_pips(self):

        return self.spread * 10000
