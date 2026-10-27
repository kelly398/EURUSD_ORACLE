"""Seat 28: descriptive agreement of comparable SP500 and VIX close changes."""
from datetime import timedelta

from backend.agents.base_agent import BaseAgent
from backend.agents.contracts import AgentCategory, AgentSignalType
from backend.agents.analysis_helpers import validated_analysis


class RiskSentimentAgent(BaseAgent):
    agent_id = "risk_sentiment"
    agent_name = "Risk Sentiment"
    category = AgentCategory.MACRO
    signal_type = AgentSignalType.CONTEXTUAL
    version = "1.0.0"

    def analyze(self, context):
        context = validated_analysis(context)
        items = {item.series_id: item for item in context.cross_asset_data}
        if set(items) != {"SP500", "VIXCLS"}:
            raise RuntimeError("Risk sentiment requires both causal SP500 and VIXCLS observations.")
        equity, vol = items["SP500"], items["VIXCLS"]
        if (equity.observation_at != vol.observation_at
                or equity.previous_observation_at != vol.previous_observation_at):
            raise RuntimeError("Cross-asset changes must cover identical observation intervals.")
        if context.as_of-equity.observation_at > timedelta(days=context.max_cross_asset_age_days):
            raise RuntimeError("Cross-asset observations are stale.")
        equity_move = equity.value/equity.previous_value-1
        vol_move = vol.value/vol.previous_value-1
        if equity_move > 0 and vol_move < 0:
            regime = "risk_on_proxy"
        elif equity_move < 0 and vol_move > 0:
            regime = "risk_off_proxy"
        else:
            regime = "mixed_or_unchanged"
        return self.build_opinion(context, 0.0, 1.0,
            "Described aligned equity/volatility close changes. This two-series proxy is not a calibrated risk regime or EUR/USD direction model.",
            {"input_ready": True, "risk_regime_proxy": regime,
             "sp500_change_fraction": equity_move, "vix_change_fraction": vol_move,
             "observation_at": equity.observation_at.isoformat(),
             "previous_observation_at": equity.previous_observation_at.isoformat(),
             "max_age_days": context.max_cross_asset_age_days,
             "confidence_meaning": "input_measurement_completeness_only",
             "directional_mapping_authorized": False, "execution_authorized": False})
