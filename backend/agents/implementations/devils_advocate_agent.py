"""Seat 29: challenge incomplete, concentrated or contradictory Council views."""
from backend.agents.base_agent import BaseAgent
from backend.agents.contracts import AgentCategory, AgentSignalType
from backend.agents.analysis_helpers import validated_analysis, missing_primary, directional_summary


class DevilsAdvocateAgent(BaseAgent):
    agent_id = "devils_advocate"
    agent_name = "Devil's Advocate"
    category = AgentCategory.GOVERNANCE
    signal_type = AgentSignalType.GOVERNANCE
    version = "1.0.0"

    def analyze(self, context):
        context = validated_analysis(context)
        missing = missing_primary(context)
        active, longs, shorts, total = directional_summary(context.primary_agent_opinions)
        disagreement = min(longs, shorts)/total if total else 1.0
        categories = {o.category for o in active}
        reasons = []
        policy = context.synthesis_policy
        if missing:
            reasons.append("incomplete_primary_council")
        if not total:
            reasons.append("no_directional_support")
        if policy is None:
            reasons.append("synthesis_policy_missing")
        else:
            if len(active) < policy.min_directional_agents:
                reasons.append("insufficient_directional_agents")
            if len(categories) < policy.min_categories:
                reasons.append("concentrated_category_support")
            if disagreement > 1-policy.min_agreement:
                reasons.append("contradictory_directional_evidence")
        unavailable = tuple(o.agent_id for o in context.primary_agent_opinions
                            if o.evidence.get("input_ready") is False)
        if unavailable:
            reasons.append("primary_inputs_unavailable")
        return self.build_opinion(context, 0.0, 1.0,
            "Independently challenged completeness, opposing evidence and category concentration. Review cannot authorize execution.",
            {"review_passed": not reasons, "veto": bool(reasons),
             "blockers": ",".join(reasons), "missing_primary": ",".join(missing),
             "unavailable_primary": ",".join(unavailable), "directional_agents": len(active),
             "directional_categories": len(categories), "opposing_support_fraction": disagreement,
             "execution_authorized": False})
