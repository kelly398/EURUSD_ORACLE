"""Compatibility research view of the canonical AgentContext.

All supplemental contracts and validation live in council_inputs/contracts.
New callers can use AgentContext(execution_mode=ExecutionMode.RESEARCH, ...).
"""
from typing import Literal
from pydantic import ConfigDict
from backend.agents.contracts import AgentContext
from backend.agents.council_inputs import (
    FrozenInput, ExecutionMode, CrossAssetObservation, CalendarCoverage,
    PortfolioRiskSnapshot, RiskLimits, AgentReliability, SynthesisPolicy, require_aware,
)


class CouncilAnalysisContext(AgentContext):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    execution_mode: Literal[ExecutionMode.RESEARCH] = ExecutionMode.RESEARCH
    analysis_mode: Literal["research"] = "research"

    @classmethod
    def from_context(cls, context, **inputs):
        if inputs.get("execution_mode", ExecutionMode.RESEARCH) != ExecutionMode.RESEARCH:
            raise ValueError("CouncilAnalysisContext is restricted to research.")
        inputs["execution_mode"] = ExecutionMode.RESEARCH
        return super().from_context(context, **inputs)
