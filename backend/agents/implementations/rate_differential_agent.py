"""Seat 26: announced ECB deposit rate minus Fed target-range midpoint.

This compares policy instruments, not matched-tenor tradeable yields or a
validated FX forecast. Announced and effective dates remain distinct.
"""
from backend.agents.base_agent import BaseAgent
from backend.agents.contracts import AgentCategory, AgentSignalType
from backend.agents.analysis_helpers import validated_analysis
from backend.agents.implementations.fed_agent import FedAgent


class RateDifferentialAgent(BaseAgent):
    agent_id = "rate_differential"
    agent_name = "Rate Differential"
    category = AgentCategory.MACRO
    signal_type = AgentSignalType.CONTEXTUAL
    version = "1.0.0"

    def analyze(self, context):
        context = validated_analysis(context)
        rates = tuple(r for r in context.ecb_data if r.rate_name == "deposit_facility_rate"
                      and r.source == "ECB_SDMX_FM")
        if not rates or not context.fed_data:
            raise RuntimeError("Rate differential requires both causal ECB deposit and paired Fed target data.")
        latest_time = max(r.available_at for r in rates)
        latest = tuple(r for r in rates if r.available_at == latest_time)
        if len(latest) != 1:
            raise RuntimeError("Ambiguous latest announced ECB deposit rate.")
        ecb = latest[0]
        if not -10 <= ecb.value_percent <= 30:
            raise RuntimeError("ECB deposit rate is outside the input domain.")
        fed = FedAgent().run(context).evidence
        midpoint = fed["latest_target_midpoint_percent"]
        spread = round(ecb.value_percent - midpoint, 8)
        return self.build_opinion(context, 0.0, 0.0,
            "Compared latest publicly announced policy instruments; no unilateral EUR/USD forecast or effective-now claim.",
            {"input_ready": True, "comparison": "announced_ecb_dfr_minus_fed_target_midpoint",
             "ecb_rate_percent": ecb.value_percent, "fed_midpoint_percent": midpoint,
             "differential_percentage_points": spread, "differential_basis_points": round(spread * 100, 6),
             "ecb_reference_date": ecb.reference_date.isoformat(), "fed_reference_date": fed["latest_effective_date"],
             "ecb_available_at": ecb.available_at.isoformat(), "fed_available_at": fed["latest_release_at"],
             "historical_source_version_certified": False, "directional_mapping_authorized": False,
             "execution_authorized": False})
