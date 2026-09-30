from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MarketTick:

    symbol: str
    bid: float
    ask: float
    timestamp: datetime
    source_time_msc: int

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def spread_pips(self) -> float:
        return self.spread * 10000
        