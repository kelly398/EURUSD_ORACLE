from datetime import datetime
from enum import Enum
from typing import Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    model_validator
)

from backend.market_state.schemas import (
    CandleBar,
    DataQualityStatus,
    MarketStateSnapshot
)


EvidenceValue = Union[
    str,
    int,
    float,
    bool,
    None
]


# ============================================================
# AGENT CATEGORY
# ============================================================

class AgentCategory(str, Enum):

    TECHNICAL = "technical"

    STATISTICAL = "statistical"

    MACHINE_LEARNING = "machine_learning"

    MICROSTRUCTURE = "microstructure"

    SESSION = "session"

    INSTITUTIONAL = "institutional"

    MACRO = "macro"

    GOVERNANCE = "governance"


# ============================================================
# AGENT DIRECTION
# ============================================================

class AgentDirection(str, Enum):

    LONG = "long"

    SHORT = "short"

    NEUTRAL = "neutral"


# ============================================================
# AGENT CONTEXT
# ============================================================

class AgentContext(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    symbol: str

    timeframe: str

    as_of: datetime

    market_state: MarketStateSnapshot

    candles: tuple[
        CandleBar,
        ...
    ]


    @model_validator(
        mode="after"
    )
    def validate_context(self):

        if (
            self.market_state.symbol
            != self.symbol
        ):

            raise ValueError(
                "AgentContext symbol "
                "does not match "
                "MarketStateSnapshot."
            )


        if (
            self.market_state.timeframe
            != self.timeframe
        ):

            raise ValueError(
                "AgentContext timeframe "
                "does not match "
                "MarketStateSnapshot."
            )


        if (
            self.market_state.as_of
            != self.as_of
        ):

            raise ValueError(
                "AgentContext as_of "
                "does not match "
                "MarketStateSnapshot."
            )


        if (
            self.market_state.data_quality
            != DataQualityStatus.PASS
        ):

            raise ValueError(
                "Agents may only receive "
                "PASS market-state data."
            )


        if not self.candles:

            raise ValueError(
                "AgentContext requires "
                "canonical candle history."
            )


        for candle in self.candles:

            if candle.symbol != self.symbol:

                raise ValueError(
                    "AgentContext contains "
                    "mixed candle symbols."
                )


            if (
                candle.timeframe
                != self.timeframe
            ):

                raise ValueError(
                    "AgentContext contains "
                    "mixed candle timeframes."
                )


        for previous, current in zip(
            self.candles,
            self.candles[1:]
        ):

            if (
                current.timestamp
                <= previous.timestamp
            ):

                raise ValueError(
                    "AgentContext candles "
                    "must be in strict "
                    "chronological order."
                )


        return self


# ============================================================
# STANDARDIZED AGENT OPINION
# ============================================================

class AgentOpinion(BaseModel):

    model_config = ConfigDict(
        frozen=True
    )

    agent_id: str

    agent_name: str

    category: AgentCategory

    version: str

    as_of: datetime

    direction: AgentDirection

    score: float = Field(
        ge=-1.0,
        le=1.0
    )

    confidence: float = Field(
        ge=0.0,
        le=1.0
    )

    rationale: str = Field(
        min_length=1
    )

    evidence: dict[
        str,
        EvidenceValue
    ] = Field(
        default_factory=dict
    )
    