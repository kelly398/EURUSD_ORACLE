"""Explicit targets and provenance for append-only Council memory."""
from datetime import date, datetime, timezone
from enum import Enum
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Memory timestamps must be timezone-aware.")
    return value.astimezone(timezone.utc)


def canonical(value):
    """Stable, finite JSON; UTC offsets and dict order do not change identity."""
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if isinstance(value, datetime):
        return utc(value).isoformat(timespec="microseconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [canonical(v) for v in value]
    return value


def encoded(value):
    return json.dumps(canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


class MemoryInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class RunProvenance(MemoryInput):
    # Namespaced experiments cannot silently enter prospective live performance.
    scope: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    origin: Literal["synthetic", "historical_research", "live"]
    policy_version: str = Field(min_length=1, max_length=128)
    code_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: dict[str, str]
    environment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def check_artifacts(self):
        import re
        if any(not name or not re.fullmatch(r"[0-9a-f]{64}", value)
               for name, value in self.artifact_sha256.items()):
            raise ValueError("Artifact references require names and SHA-256 hashes.")
        return self


class ForecastSpecification(MemoryInput):
    """Mid-quote target; hypothetical P&L includes spread and stated commission.

    This is not a calibrated probability or an executable order. Financing,
    slippage and latency are not modeled by this first outcome contract.
    """
    target_version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    horizon_seconds: int = Field(strict=True, gt=0, le=86400)
    neutral_band_pips: float = Field(ge=0)
    max_quote_age_seconds: int = Field(strict=True, ge=0, le=300)
    round_trip_commission_pips: float = Field(ge=0)
    pip_size: Literal[0.0001] = 0.0001
    price_basis: Literal["mid_quote"] = "mid_quote"
    cost_model: Literal["spread_and_commission_only"] = "spread_and_commission_only"


class OutcomeQuote(MemoryInput):
    symbol: Literal["EURUSD"] = "EURUSD"
    origin: Literal["synthetic", "historical_research", "live"]
    timestamp: datetime
    available_at: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)
    source: str = Field(min_length=1, max_length=128)
    source_record_id: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def check_quote(self):
        if utc(self.available_at) < utc(self.timestamp):
            raise ValueError("Quote receipt cannot precede its timestamp.")
        if self.ask < self.bid:
            raise ValueError("Crossed outcome quote.")
        return self
