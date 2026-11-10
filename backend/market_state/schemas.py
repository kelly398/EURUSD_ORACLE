from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator
)
from backend.data_engine.feed_identity import require_feed_id


# ============================================================
# ENUMS
# ============================================================

class MarketDirection(str, Enum):

    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class VolatilityRegime(str, Enum):

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class MomentumState(str, Enum):

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SpreadState(str, Enum):

    TIGHT = "tight"
    NORMAL = "normal"
    WIDE = "wide"
    EXTREME = "extreme"


class DataQualityStatus(str, Enum):

    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


# ============================================================
# CANONICAL CANDLE
# ============================================================

class CandleBar(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    symbol: str
    timeframe: str
    timestamp: datetime

    open: float = Field(
        gt=0
    )

    high: float = Field(
        gt=0
    )

    low: float = Field(
        gt=0
    )

    close: float = Field(
        gt=0
    )

    tick_volume: int = Field(
        ge=0
    )

    spread_points: int = Field(
        ge=0
    )

    real_volume: float = Field(
        ge=0
    )

    source: str

    feed_id: Optional[str] = None


    @model_validator(
        mode="after"
    )
    def validate_ohlc(self):

        if self.high < max(
            self.open,
            self.close,
            self.low
        ):

            raise ValueError(
                "Candle high is invalid."
            )


        if self.low > min(
            self.open,
            self.close,
            self.high
        ):

            raise ValueError(
                "Candle low is invalid."
            )


        return self


class TickSample(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    symbol: str

    feed_id: Optional[str] = None

    timestamp: datetime

    source_time_msc: int = Field(
        ge=0
    )

    bid: float = Field(
        gt=0
    )

    ask: float = Field(
        gt=0
    )

    spread_pips: float = Field(
        ge=0
    )


    @model_validator(
        mode="after"
    )
    def validate_prices(self):

        if self.ask < self.bid:

            raise ValueError(
                "Tick ask cannot be "
                "below tick bid."
            )

        return self


# ============================================================
# TREND STATE
# ============================================================

class TrendState(BaseModel):

    direction: MarketDirection

    strength: float = Field(
        ge=0,
        le=1
    )

    slope_pips_per_bar: Optional[
        float
    ] = None


# ============================================================
# VOLATILITY STATE
# ============================================================

class VolatilityState(BaseModel):

    regime: VolatilityRegime

    atr_pips: Optional[
        float
    ] = None

    realized_volatility: Optional[
        float
    ] = None


# ============================================================
# MOMENTUM STATE
# ============================================================

class MomentumSnapshot(BaseModel):

    state: MomentumState

    strength: float = Field(
        ge=0,
        le=1
    )


# ============================================================
# MARKET STRUCTURE
# ============================================================

class StructureState(BaseModel):

    label: str

    confidence: float = Field(
        ge=0,
        le=1
    )


# ============================================================
# LIQUIDITY STATE
# ============================================================

class LiquidityState(BaseModel):

    spread_state: SpreadState

    average_spread_pips: Optional[
        float
    ] = None

    tick_activity: Optional[
        str
    ] = None


# ============================================================
# COMPLETE MARKET STATE
# ============================================================

class MarketStateSnapshot(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    symbol: str

    feed_id: Optional[str] = None

    timeframe: str

    as_of: datetime

    data_quality: DataQualityStatus

    candle_count: int = Field(
        ge=0
    )

    trend: Optional[
        TrendState
    ] = None

    volatility: Optional[
        VolatilityState
    ] = None

    momentum: Optional[
        MomentumSnapshot
    ] = None

    structure: Optional[
        StructureState
    ] = None

    liquidity: Optional[
        LiquidityState
    ] = None

    session: Optional[
        str
    ] = None

    @model_validator(mode="after")
    def validate_feed_identity(self):
        if self.feed_id is not None:
            require_feed_id(self.feed_id)
        return self
