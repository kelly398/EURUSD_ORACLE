"""Seat 30: independent deterministic portfolio, market and event constraints.

No default capital limits, fractional-Kelly estimates or broker orders. The
caller must supply reconciled risk amounts and an explicit limits policy.
"""
from math import isfinite

from backend.agents.base_agent import BaseAgent
from backend.agents.contracts import AgentCategory, AgentSignalType
from backend.agents.analysis_helpers import validated_analysis, missing_primary, calendar_window
from backend.market_state.schemas import DataQualityStatus


class RiskOfficerAgent(BaseAgent):
    agent_id = "risk_officer"
    agent_name = "Risk Officer"
    category = AgentCategory.GOVERNANCE
    signal_type = AgentSignalType.GOVERNANCE
    version = "1.0.0"

    def analyze(self, context):
        context = validated_analysis(context)
        reasons = []
        missing = missing_primary(context)
        if missing:
            reasons.append("incomplete_primary_council")
        for opinion in context.primary_agent_opinions:
            if opinion.evidence.get("input_ready") is False:
                reasons.append(f"missing_inputs:{opinion.agent_id}")
        state, limits, market = context.risk_state, context.risk_limits, context.market_state
        if market.data_quality != DataQualityStatus.PASS:
            reasons.append("market_data_quality_failed")
        if state is None:
            reasons.append("portfolio_state_missing")
        if limits is None:
            reasons.append("risk_limits_missing")
        drawdown = max(0.0, 1-state.equity/state.day_start_equity) if state else None
        if state and limits:
            if (context.as_of-state.as_of).total_seconds() > limits.max_snapshot_age_seconds:
                reasons.append("stale_portfolio_state")
            if state.kill_switch:
                reasons.append("kill_switch")
            if not state.broker_connected:
                reasons.append("broker_disconnected")
            if not state.reconciliation_ok:
                reasons.append("portfolio_not_reconciled")
            if drawdown >= limits.max_daily_drawdown_fraction:
                reasons.append("daily_drawdown_limit")
            if (state.gross_exposure+state.proposed_gross_exposure)/state.equity > limits.max_gross_exposure_multiple:
                reasons.append("gross_exposure_limit")
            if (state.open_risk+state.proposed_risk)/state.equity > limits.max_total_risk_fraction:
                reasons.append("total_risk_limit")
            if state.proposed_risk/state.equity > limits.max_trade_risk_fraction:
                reasons.append("per_trade_risk_limit")
        if limits:
            if not context.ticks:
                reasons.append("current_quote_missing")
            elif (context.as_of-context.ticks[-1].timestamp).total_seconds() > limits.max_market_age_seconds:
                reasons.append("stale_current_quote")
            spread = market.liquidity.average_spread_pips if market.liquidity else None
            # Use the worse of the snapshot average and most recent quote.
            if context.ticks and spread is not None:
                spread = max(spread, context.ticks[-1].spread_pips)
            atr = market.volatility.atr_pips if market.volatility else None
            for name, value, maximum in (("spread", spread, limits.max_spread_pips),
                                          ("atr", atr, limits.max_atr_pips)):
                if value is None or not isfinite(value) or value < 0:
                    reasons.append(f"{name}_measurement_missing")
                elif value > maximum:
                    reasons.append(f"{name}_limit")
            covered, events, _ = calendar_window(context, limits.event_blackout_before_seconds,
                                                  limits.event_blackout_after_seconds)
            if not covered:
                reasons.append("calendar_coverage_unknown")
            if events:
                reasons.append("economic_event_blackout")
        return self.build_opinion(context, 0.0, 1.0,
            "Applied explicit independent risk limits. Passing these research checks is not trade authorization or a production admission certificate.",
            {"risk_checks_passed": not reasons, "veto": bool(reasons),
             "blockers": ",".join(reasons), "daily_drawdown_fraction": drawdown,
             "missing_primary": ",".join(missing), "execution_authorized": False})
