"""Pure calculations shared by remaining Council seats; no data acquisition."""
from math import fsum

from backend.agents.contracts import AgentContext, AgentSignalType, AgentStage
from backend.agents.council_manifest import COUNCIL_MANIFEST


def validated_analysis(context):
    if not isinstance(context, AgentContext):
        raise TypeError("This seat requires AgentContext with typed supplemental inputs.")
    return type(context).model_validate(context.model_dump())


def missing_primary(context):
    supplied = {opinion.agent_id for opinion in context.primary_agent_opinions}
    return tuple(spec.agent_id for spec in COUNCIL_MANIFEST
                 if spec.stage == AgentStage.PRIMARY and spec.agent_id not in supplied)


def directional_summary(opinions):
    """Descriptive support only; contextual/governance opinions never vote."""
    items = tuple(o for o in opinions if o.signal_type == AgentSignalType.DIRECTIONAL)
    active = tuple(o for o in items if o.score != 0 and o.direction.value != "neutral" and o.confidence > 0)
    long_support = fsum(o.confidence * abs(o.score) for o in active if o.score > 0)
    short_support = fsum(o.confidence * abs(o.score) for o in active if o.score < 0)
    total = long_support + short_support
    return active, long_support, short_support, total


def calendar_window(context, before_seconds, after_seconds):
    """All supplied EUR/USD events are treated conservatively as relevant.

    Half-open blackout: [scheduled-before, scheduled+after). No impact ranking
    is invented because the existing event schema has no impact field.
    """
    from datetime import timedelta
    start = context.as_of - timedelta(seconds=after_seconds)
    end = context.as_of + timedelta(seconds=before_seconds)
    coverage = context.calendar_coverage
    covered = bool(coverage and coverage.start <= start and coverage.end > end
                   and set(coverage.currencies) == {"EUR", "USD"})
    events = tuple(e for e in context.economic_calendar if e.currency in {"EUR", "USD"})
    if coverage:
        covered = covered and all(e.source in coverage.sources for e in events
                                  if start <= e.scheduled_at <= end)
    blocked = tuple(e for e in events
                    if -after_seconds < (e.scheduled_at - context.as_of).total_seconds() <= before_seconds)
    return covered, blocked, events
