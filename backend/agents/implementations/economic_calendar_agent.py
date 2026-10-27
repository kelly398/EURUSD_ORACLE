"""Seat 27: covered event windows and numeric release context, without FX votes."""
from backend.agents.base_agent import BaseAgent
from backend.agents.contracts import AgentCategory, AgentSignalType
from backend.agents.analysis_helpers import validated_analysis, calendar_window


class EconomicCalendarAgent(BaseAgent):
    agent_id = "economic_calendar"
    agent_name = "Economic Calendar"
    category = AgentCategory.MACRO
    signal_type = AgentSignalType.CONTEXTUAL
    version = "1.0.0"

    def analyze(self, context):
        context = validated_analysis(context)
        limits = context.risk_limits
        if limits is None:
            before, after = 0, 0
            covered = False
            _, blocked, events = calendar_window(context, before, after)
        else:
            before = limits.event_blackout_before_seconds
            after = limits.event_blackout_after_seconds
            covered, blocked, events = calendar_window(context, before, after)
        upcoming = tuple(e for e in events if e.scheduled_at >= context.as_of)
        next_event = min(upcoming, key=lambda e: (e.scheduled_at, e.source_event_id), default=None)
        releases = tuple(e for e in events if e.scheduled_at <= context.as_of and e.actual is not None)
        latest = max(releases, key=lambda e: (e.scheduled_at, e.source_event_id), default=None)
        # The schema lacks a forecast provenance/consensus contract. Do not turn
        # actual minus forecast into a validated surprise or directional signal.
        return self.build_opinion(context, 0.0, 1.0 if covered else 0.0,
            "Reported event proximity within supplied feed coverage. Missing coverage means unknown event risk; release surprise is not calibrated.",
            {"input_ready": covered, "coverage_complete_for_window": covered,
             "coverage_scope": "supplied_sources_only", "event_risk_blocked": bool(blocked) or not covered,
             "blackout_event_count": len(blocked), "known_event_count": len(events),
             "blackout_before_seconds": before, "blackout_after_seconds": after,
             "next_event_id": next_event.source_event_id if next_event else None,
             "next_event_at": next_event.scheduled_at.isoformat() if next_event else None,
             "seconds_to_next_event": (next_event.scheduled_at-context.as_of).total_seconds() if next_event else None,
             "latest_released_event_id": latest.source_event_id if latest else None,
             "latest_actual": latest.actual if latest else None,
             "surprise_model_available": False, "directional_mapping_authorized": False,
             "execution_authorized": False})
