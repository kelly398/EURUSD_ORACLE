"""Seat 31: reliability/regime-weighted, category-balanced research synthesis.

Contextual agents never cast directional votes. Governance has an independent
veto. Unavailable calibration/evaluation weights cause HOLD, not equal-weight
substitution. This does not implement or authorize broker execution.
"""
from datetime import timedelta
from math import fsum

from backend.agents.base_agent import BaseAgent
from backend.agents.contracts import AgentCategory, AgentSignalType
from backend.agents.analysis_helpers import validated_analysis, missing_primary, directional_summary


class ChiefStrategistAgent(BaseAgent):
    agent_id = "chief_strategist"
    agent_name = "Chief Strategist"
    category = AgentCategory.GOVERNANCE
    signal_type = AgentSignalType.GOVERNANCE
    version = "1.0.0"

    def analyze(self, context):
        context = validated_analysis(context)
        reasons = []
        if missing_primary(context):
            reasons.append("incomplete_primary_council")
        governance = {o.agent_id: o for o in context.governance_opinions}
        if set(governance) != {"devils_advocate", "risk_officer"}:
            reasons.append("incomplete_governance")
        else:
            if governance["risk_officer"].evidence.get("risk_checks_passed") is not True:
                reasons.append("risk_officer_veto")
            if governance["devils_advocate"].evidence.get("review_passed") is not True:
                reasons.append("devils_advocate_veto")
        fed = next((o for o in context.primary_agent_opinions if o.agent_id == "fed"), None)
        if fed is None or fed.evidence.get("historical_source_version_certified") is not True:
            reasons.append("fed_source_admission_missing")
        policy = context.synthesis_policy
        if policy is None:
            reasons.append("synthesis_policy_missing")
        active, _, _, _ = directional_summary(context.primary_agent_opinions)
        reliability = {entry.agent_id: entry for entry in context.reliability}
        groups = {}
        for opinion in active:
            weight = reliability.get(opinion.agent_id)
            if weight is None:
                reasons.append(f"reliability_missing:{opinion.agent_id}")
                continue
            if policy and context.as_of-weight.available_at > timedelta(days=policy.max_reliability_age_days):
                reasons.append(f"reliability_stale:{opinion.agent_id}")
                continue
            magnitude = weight.reliability * weight.regime_compatibility * opinion.confidence
            if magnitude > 0:
                groups.setdefault(opinion.category, []).append((opinion, magnitude))
        category_scores = []
        category_agreements = []
        category_confidences = []
        for category in sorted(groups, key=lambda item: item.value):
            entries = groups[category]
            total = fsum(weight for _, weight in entries)
            category_scores.append(fsum(o.score*weight for o, weight in entries)/total)
            category_confidences.append(fsum(o.confidence*weight for o, weight in entries)/total)
        strength = fsum(category_scores)/len(category_scores) if category_scores else 0.0
        confidence = fsum(category_confidences)/len(category_confidences) if category_confidences else 0.0
        for category in sorted(groups, key=lambda item: item.value):
            entries = groups[category]
            total = fsum(weight for _, weight in entries)
            category_agreements.append(fsum(weight for o, weight in entries if o.score*strength > 0)/total)
        agreement = fsum(category_agreements)/len(category_agreements) if category_agreements else 0.0
        if policy:
            contributors = sum(len(entries) for entries in groups.values())
            if contributors < policy.min_directional_agents:
                reasons.append("insufficient_weighted_agents")
            if len(groups) < policy.min_categories:
                reasons.append("insufficient_categories")
            if agreement < policy.min_agreement:
                reasons.append("insufficient_agreement")
            if abs(strength) < max(policy.min_strength, self.neutral_threshold):
                reasons.append("insufficient_strength")
            if confidence < policy.min_confidence:
                reasons.append("insufficient_confidence")
        accepted = not reasons
        return self.build_opinion(context, strength if accepted else 0.0, confidence if accepted else 0.0,
            "Synthesized directional evidence with explicit reliability and regime weights, equal category influence, and separate governance vetoes. Research output cannot execute trades.",
            {"decision": ("research_long_candidate" if strength > 0 else "research_short_candidate") if accepted else "hold",
             "blockers": ",".join(dict.fromkeys(reasons)), "weighted_strength": strength,
             "weighted_agreement": agreement, "weighted_confidence": confidence,
             "weighted_categories": len(groups), "directional_agents": len(active),
             "execution_authorized": False, "analysis_mode": context.execution_mode.value})
