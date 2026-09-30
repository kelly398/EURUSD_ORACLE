from dataclasses import dataclass
from datetime import datetime


@dataclass
class Candle:

    symbol: str

    timeframe: str

    open: float

    high: float

    low: float

    close: float

    volume: int

    average_spread: float

    timestamp: datetime
      