
"""Seat 25 F4.4.1: offline, contextual Fed target-range interpretation.

The Fed policy calendar, independent source-version attestations, production
admission, persistence, and trade-direction mapping are NOT integrated.
Valid F1 economic hashes establish only internal consistency, not authenticity.
"""

from backend.agents.base_agent import BaseAgent
from backend.agents.contracts import AgentCategory, AgentSignalType
from backend.macro.fed_replay_lock import (
    FedReplayLockError,
    lock_fed_data_as_of,
)


class FedAgent(BaseAgent):
    agent_id = "fed"
    agent_name = "Federal Reserve"
    category = AgentCategory.MACRO
    signal_type = AgentSignalType.CONTEXTUAL
    version = "0.1.0"

    def analyze(self, context):
        if not context.fed_data:
            raise RuntimeError("Fed agent requires causal Fed target-rate data.")

        supplied = tuple(context.fed_data)

        try:
            locked = lock_fed_data_as_of(supplied, context.as_of)
        except (FedReplayLockError, TypeError, ValueError) as exc:
            raise RuntimeError("Fed agent rejected inconsistent rate evidence.") from exc

        if not locked or locked != supplied:
            raise RuntimeError(
                "Fed agent requires a canonical, fully snapshot-locked context."
            )

        by_statement = {}
        for item in locked:
            statement_id = item.source_record_id.split(":")[1]
            bound = item.source_record_id.rsplit(":", 1)[1]
            by_statement.setdefault(statement_id, {})[bound] = item

        decisions = []
        for statement_id, pair in by_statement.items():
            if set(pair) != {"lower", "upper"}:
                raise RuntimeError("Fed agent requires complete target-range pairs.")
            lower = pair["lower"]
            upper = pair["upper"]
            decisions.append((lower.available_at, statement_id, lower, upper))

        decisions.sort(key=lambda entry: (entry[0], entry[1]))

        # Two different official decisions with exactly the same release instant
        # cannot be put into causal order by statement name alone.
        for older, newer in zip(decisions, decisions[1:]):
            if older[0] == newer[0]:
                raise RuntimeError("Ambiguous simultaneous Fed decisions.")

        _, statement_id, lower, upper = decisions[-1]
        prior = decisions[-2] if len(decisions) > 1 else None

        if prior is None:
            move = "insufficient_history"
            midpoint_change_bps = None
        else:
            old_lower, old_upper = prior[2], prior[3]
            lower_diff = lower.value_percent - old_lower.value_percent
            upper_diff = upper.value_percent - old_upper.value_percent
            if lower_diff == 0 and upper_diff == 0:
                move = "unchanged"
            elif lower_diff > 0 and upper_diff > 0:
                move = "higher"
            elif lower_diff < 0 and upper_diff < 0:
                move = "lower"
            else:
                move = "mixed_or_width_changed"
            midpoint_change_bps = round(
                (
                    (lower.value_percent + upper.value_percent)
                    - (old_lower.value_percent + old_upper.value_percent)
                ) * 50.0,
                6,
            )

        # Source fields establish a DATE, not the precise UTC instant a new
        # rate becomes effective. Do not assert intraday 'active rate' status.
        if lower.reference_date > context.as_of.date():
            effective_date_status = "future_effective_date"
        elif lower.reference_date == context.as_of.date():
            effective_date_status = "effective_today_time_unverified"
        else:
            effective_date_status = "effective_date_passed"

        evidence = {
            "fed_decision_count": len(decisions),
            "latest_statement_id": statement_id,
            "latest_release_at": lower.available_at.isoformat(),
            "latest_effective_date": lower.reference_date.isoformat(),
            "latest_lower_target_percent": float(lower.value_percent),
            "latest_upper_target_percent": float(upper.value_percent),
            "latest_range_width_bps": round(
                (upper.value_percent - lower.value_percent) * 100.0, 6
            ),
            "latest_target_midpoint_percent": round(
                (lower.value_percent + upper.value_percent) / 2.0, 6
            ),
            "effective_date_status": effective_date_status,
            "target_range_change_vs_prior": move,
            "midpoint_change_vs_prior_bps": midpoint_change_bps,
            "economic_hash_consistency_checked": True,
            "historical_source_version_certified": False,
            "historical_replay_admission_authorized": False,
            "fed_calendar_interpretation_available": False,
            "directional_mapping_authorized": False,
            "relative_rate_differential_available": False,
        }

        return self.build_opinion(
            context=context,
            score=0.0,
            confidence=0.0,
            rationale=(
                "Fed target-range announcements were summarized from internally "
                "consistent, snapshot-locked evidence. Historical source-version "
                "authenticity, exact effective time, Fed calendar interpretation "
                "and EUR/USD directional mapping remain unverified or disabled."
            ),
            evidence=evidence,
        )
