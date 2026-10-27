"""Central dispatch eligibility. Registration never grants source admission."""
from dataclasses import dataclass
from datetime import timedelta

from backend.agents.contracts import AgentContext, AgentStage
from backend.agents.council_inputs import ExecutionMode
from backend.market_state.schemas import DataQualityStatus


@dataclass(frozen=True)
class AgentEligibility:
    agent_id: str
    mode: ExecutionMode
    blockers: tuple[str, ...] = ()

    @property
    def allowed(self):
        return not self.blockers


def assess_eligibility(spec, context, *, mode=ExecutionMode.PRODUCTION):
    if not isinstance(context, AgentContext):
        raise TypeError("Eligibility requires AgentContext.")
    context = type(context).model_validate(context.model_dump())
    mode = ExecutionMode(mode)
    reasons = []
    if context.execution_mode != mode:
        reasons.append("EXECUTION_MODE_MISMATCH")
    if spec.stage == AgentStage.PRIMARY and context.market_state.data_quality != DataQualityStatus.PASS:
        reasons.append("MARKET_DATA_QUALITY_NOT_PASS")
    # Other manifest labels (feature_vector, model_artifact, tick_features,
    # market_structure) are derived by the established agent implementations.
    direct = {"candles", "ecb_data", "fed_data", "cot_data", "risk_state",
              "cross_asset_data", "primary_agent_opinions", "governance_opinions"}
    for name in spec.required_inputs:
        if name in direct and not getattr(context, name):
            reasons.append(f"INPUT_MISSING:{name}")
    if spec.agent_id in {"ecb", "fed"} and not context.economic_calendar:
        # Fed research retains the existing rate-only interpretation, without
        # claiming an admitted calendar. Production never permits that shortcut.
        if spec.agent_id == "ecb" or mode == ExecutionMode.PRODUCTION:
            reasons.append("INPUT_MISSING:economic_calendar")
    if spec.agent_id == "rate_differential":
        if not context.ecb_data:
            reasons.append("INPUT_MISSING:ecb_data")
        if not context.fed_data:
            reasons.append("INPUT_MISSING:fed_data")
    if spec.agent_id == "economic_calendar":
        if context.calendar_coverage is None:
            reasons.append("CALENDAR_COVERAGE_UNKNOWN")
        if context.risk_limits is None:
            reasons.append("INPUT_MISSING:risk_limits")
    if spec.agent_id == "risk_sentiment":
        if {item.series_id for item in context.cross_asset_data} != {"SP500", "VIXCLS"}:
            reasons.append("CROSS_ASSET_PAIR_INCOMPLETE")
        else:
            first, second = context.cross_asset_data
            if (first.observation_at != second.observation_at
                    or first.previous_observation_at != second.previous_observation_at):
                reasons.append("CROSS_ASSET_INTERVAL_MISMATCH")
            if context.as_of-first.observation_at > timedelta(days=context.max_cross_asset_age_days):
                reasons.append("CROSS_ASSET_DATA_STALE")
    if spec.agent_id == "risk_officer" and context.risk_limits is None:
        reasons.append("INPUT_MISSING:risk_limits")
    if spec.agent_id == "chief_strategist":
        if context.synthesis_policy is None:
            reasons.append("INPUT_MISSING:synthesis_policy")
        if not context.reliability:
            reasons.append("INPUT_MISSING:reliability")
    if mode == ExecutionMode.PRODUCTION:
        if spec.agent_id in {"fed", "rate_differential"}:
            from backend.agents.fed_input_readiness import inspect_fed_input_readiness
            reasons.extend(inspect_fed_input_readiness(context).blockers)
        if spec.agent_id == "economic_calendar":
            reasons.append("CALENDAR_PRODUCTION_ADMISSION_MISSING")
    return AgentEligibility(spec.agent_id, mode, tuple(dict.fromkeys(reasons)))
