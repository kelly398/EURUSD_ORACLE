"""Shared typed Council inputs, independent of registry and execution mode."""
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.macro.macro_schemas import CentralBankRateSnapshot

class FrozenInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware.")


class CrossAssetObservation(FrozenInput):
    """A source-versioned close and a strictly earlier comparable close.

    Current FRED downloads must use their observation/receipt time as available_at
    unless a separately validated historical publication record is supplied.
    """
    series_id: Literal["SP500", "VIXCLS"]
    observation_at: datetime
    previous_observation_at: datetime
    available_at: datetime
    ingested_at: datetime
    value: float = Field(gt=0)
    previous_value: float = Field(gt=0)
    source: Literal["FRED"] = "FRED"
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_times(self):
        for name in ("observation_at", "previous_observation_at", "available_at", "ingested_at"):
            require_aware(getattr(self, name), name)
        if not self.previous_observation_at < self.observation_at <= self.available_at <= self.ingested_at:
            raise ValueError("Cross-asset observation/publication/receipt chronology is invalid.")
        return self


class CalendarCoverage(FrozenInput):
    """Evidence of acquisition coverage, never inferred from an empty event list.

    Sources are scoped to the specified feeds; this is not proof that every
    possible macro event is covered or that surprise values are comparable.
    """
    start: datetime
    end: datetime
    available_at: datetime
    currencies: tuple[Literal["EUR", "USD"], ...]
    sources: tuple[str, ...]

    @model_validator(mode="after")
    def validate_window(self):
        for name in ("start", "end", "available_at"):
            require_aware(getattr(self, name), name)
        if self.start >= self.end or not self.sources or not self.currencies:
            raise ValueError("Calendar coverage requires a nonempty window, currencies and sources.")
        if len(set(self.sources)) != len(self.sources) or any(not s.strip() for s in self.sources):
            raise ValueError("Calendar coverage sources must be unique and nonempty.")
        if len(set(self.currencies)) != len(self.currencies):
            raise ValueError("Calendar coverage currencies must be unique.")
        return self


class PortfolioRiskSnapshot(FrozenInput):
    as_of: datetime
    account_currency: str = Field(pattern=r"^[A-Z]{3}$")
    equity: float = Field(gt=0)
    day_start_equity: float = Field(gt=0)
    gross_exposure: float = Field(ge=0)
    open_risk: float = Field(ge=0)
    proposed_risk: float = Field(gt=0)
    proposed_gross_exposure: float = Field(ge=0)
    broker_connected: bool
    reconciliation_ok: bool
    kill_switch: bool
    # All risk/exposure amounts use account currency, including proposed values.

    @model_validator(mode="after")
    def aware(self):
        require_aware(self.as_of, "risk as_of")
        return self


class RiskLimits(FrozenInput):
    """Explicit operator-supplied research limits; no capital defaults."""
    max_daily_drawdown_fraction: float = Field(gt=0, lt=1)
    max_gross_exposure_multiple: float = Field(gt=0)
    max_total_risk_fraction: float = Field(gt=0, lt=1)
    max_trade_risk_fraction: float = Field(gt=0, lt=1)
    max_spread_pips: float = Field(gt=0)
    max_atr_pips: float = Field(gt=0)
    max_snapshot_age_seconds: int = Field(gt=0, le=3600)
    max_market_age_seconds: int = Field(gt=0, le=3600)
    event_blackout_before_seconds: int = Field(ge=0, le=86400)
    event_blackout_after_seconds: int = Field(ge=0, le=86400)

    @model_validator(mode="after")
    def coherent(self):
        if self.max_trade_risk_fraction > self.max_total_risk_fraction:
            raise ValueError("Per-trade risk cannot exceed total risk limit.")
        return self


class AgentReliability(FrozenInput):
    agent_id: str
    reliability: float = Field(ge=0, le=1)
    regime_compatibility: float = Field(ge=0, le=1)
    available_at: datetime
    evaluation_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def aware(self):
        require_aware(self.available_at, "reliability available_at")
        return self


class SynthesisPolicy(FrozenInput):
    min_directional_agents: int = Field(ge=2, le=28)
    min_categories: int = Field(ge=2, le=6)
    min_agreement: float = Field(gt=0.5, le=1)
    min_strength: float = Field(gt=0, le=1)
    min_confidence: float = Field(gt=0, le=1)
    max_reliability_age_days: int = Field(gt=0, le=366)


class ExecutionMode(str, Enum):
    PRODUCTION = "production"
    RESEARCH = "research"


class CouncilSupplementalInputs(FrozenInput):
    """Optional source inputs; excludes market identity, mode and stage opinions.

    No values here grant source admission. The factory rejects Fed injection
    in production until an admitted delivery mechanism is installed.
    """
    fed_data: tuple[CentralBankRateSnapshot, ...] = ()
    cross_asset_data: tuple[CrossAssetObservation, ...] = ()
    calendar_coverage: CalendarCoverage | None = None
    risk_state: PortfolioRiskSnapshot | None = None
    risk_limits: RiskLimits | None = None
    reliability: tuple[AgentReliability, ...] = ()
    synthesis_policy: SynthesisPolicy | None = None
    max_cross_asset_age_days: int = Field(default=4, ge=1, le=10)
