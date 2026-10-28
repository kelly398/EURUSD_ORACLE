
"""Milestones 53-56: point-in-time input readiness for synthetic Council replay.

M53 inventory every required synthetic Council input family.
M54 verify declared publication, ingestion, coverage and state timestamps.
M55 block omitted, late, stale, forged or mismatched synthetic inputs.
M56 reuse the existing source-backed synthetic replay and real 31-seat Council.

All source identities are TEST DECLARATIONS. Timestamp consistency is not
independent provider attestation. No live source admission, training or trading.
The replay is in-memory SQLite only and never returns an executable action.
"""

import argparse
from datetime import timedelta
from hashlib import sha256
import json

from backend.agents.contracts import AgentContext, AgentStage
from backend.agents.council_inputs import ExecutionMode
from backend.agents.council_manifest import COUNCIL_MANIFEST
from backend.agents.synthetic_forward_replay_audit import audit_variable_replay
from backend.agents.synthetic_forward_variable_replay import make_variable_demo
from backend.data_engine.forward_store import encoded

VERSION = "EURUSD_SYNTHETIC_COUNCIL_INPUT_READINESS_V1"
FAMILIES = (
    "market_state", "candles", "ticks", "cot_data", "ecb_data", "fed_data",
    "economic_calendar", "cross_asset_data", "calendar_coverage", "risk_state",
    "risk_limits", "reliability", "synthesis_policy",
)


class InputReadinessError(RuntimeError):
    """The inspection cannot safely establish a synthetic-only contract."""


def _digest(data):
    return sha256(encoded(data).encode("utf-8")).hexdigest()


def _utc(value):
    try:
        return value.utcoffset() == timedelta(0)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return False


def _code(blockers, name, reason):
    blockers.add(name.upper() + ":" + reason)


def _source_time_checks(items, field, cutoff, blockers):
    """Check available/received versions, not publication schedules as receipts."""
    for item in items:
        available = getattr(item, "available_at", None)
        ingested = getattr(item, "ingested_at", None)
        if not _utc(available) or not _utc(ingested):
            _code(blockers, field, "CLOCK_NOT_UTC")
            continue
        if available > ingested:
            _code(blockers, field, "RECEIPT_BEFORE_AVAILABILITY")
        if available > cutoff:
            _code(blockers, field, "AVAILABLE_AFTER_AS_OF")
        if ingested > cutoff:
            _code(blockers, field, "INGESTED_AFTER_AS_OF")
        if not isinstance(getattr(item, "source", None), str) or not item.source.strip():
            _code(blockers, field, "SOURCE_IDENTITY_MISSING")
        source_hash = getattr(item, "content_hash", None)
        if (not isinstance(source_hash, str) or len(source_hash) != 64
                or any(c not in "0123456789abcdef" for c in source_hash)):
            _code(blockers, field, "SOURCE_HASH_FORMAT_INVALID")


def inspect_council_input_readiness(context, *, synthetic_only=False):
    """Assess 13 typed input families without invoking the Council.

    This proves only internal, test-declared availability consistency. It cannot
    derive first receipt or source version history from these typed values.
    """
    if synthetic_only is not True:
        raise InputReadinessError("Explicit synthetic-only opt-in required.")
    if not isinstance(context, AgentContext):
        raise InputReadinessError("Typed AgentContext required.")
    try:
        ctx = AgentContext.model_validate(context.model_dump())
    except (ValueError, TypeError, AttributeError) as exc:
        raise InputReadinessError("Council input schema revalidation failed.") from exc
    if (ctx.execution_mode is not ExecutionMode.RESEARCH or ctx.symbol != "EURUSD"
            or ctx.timeframe != "M5" or not _utc(ctx.as_of)
            or ctx.primary_agent_opinions or ctx.governance_opinions
            or ctx.execution_authorized is not False):
        raise InputReadinessError("Research-only EURUSD M5 empty-stage context required.")

    as_of = ctx.as_of
    blockers = set()
    counts = {}
    for name in FAMILIES:
        data = getattr(ctx, name)
        count = 0 if data is None else len(data) if isinstance(data, tuple) else 1
        counts[name] = count
        if count == 0:
            _code(blockers, name, "ABSENT")

    if not _utc(ctx.market_state.as_of) or ctx.market_state.as_of != as_of:
        _code(blockers, "market_state", "DECISION_CLOCK_MISMATCH")
    if ctx.candles:
        tail = ctx.candles[-1]
        if not _utc(tail.timestamp) or tail.timestamp != as_of - timedelta(minutes=5):
            _code(blockers, "candles", "LAST_CLOSED_M5_NOT_CURRENT")
        if any(not _utc(x.timestamp) or x.timestamp + timedelta(minutes=5) > as_of
               for x in ctx.candles):
            _code(blockers, "candles", "FUTURE_OR_UNCLOSED_CANDLE")
    if ctx.ticks:
        if any(not _utc(x.timestamp) or x.timestamp >= as_of for x in ctx.ticks):
            _code(blockers, "ticks", "FUTURE_OR_NONUTC_TICK")
        if (not _utc(ctx.ticks[-1].timestamp) or
                not timedelta(0) < as_of - ctx.ticks[-1].timestamp <= timedelta(seconds=120)):
            _code(blockers, "ticks", "LATEST_TICK_STALE")

    for name in ("cot_data", "ecb_data", "fed_data", "economic_calendar", "cross_asset_data"):
        _source_time_checks(getattr(ctx, name), name, as_of, blockers)

    for item in ctx.economic_calendar:
        if not _utc(item.scheduled_at):
            _code(blockers, "economic_calendar", "SCHEDULE_CLOCK_NOT_UTC")
        elif item.actual is not None and item.scheduled_at > as_of:
            _code(blockers, "economic_calendar", "UNRELEASED_ACTUAL")
    for item in ctx.cross_asset_data:
        if (not _utc(item.observation_at) or not _utc(item.previous_observation_at)
                or as_of - item.observation_at > timedelta(days=ctx.max_cross_asset_age_days)):
            _code(blockers, "cross_asset_data", "OBSERVATION_CLOCK_OR_AGE_INVALID")

    coverage = ctx.calendar_coverage
    if coverage is not None:
        if (not all(_utc(x) for x in (coverage.start, coverage.end, coverage.available_at))
                or not coverage.start <= as_of < coverage.end
                or coverage.available_at > as_of):
            _code(blockers, "calendar_coverage", "TIME_WINDOW_INVALID")
        if not {"EUR", "USD"}.issubset(set(coverage.currencies)):
            _code(blockers, "calendar_coverage", "CURRENCY_COVERAGE_INCOMPLETE")
        if any(item.source not in coverage.sources for item in ctx.economic_calendar):
            _code(blockers, "calendar_coverage", "EVENT_SOURCE_NOT_COVERED")

    risk = ctx.risk_state
    if risk is not None:
        if not _utc(risk.as_of) or risk.as_of != as_of:
            _code(blockers, "risk_state", "STATE_NOT_CURRENT")
        if not risk.reconciliation_ok or risk.kill_switch:
            _code(blockers, "risk_state", "RISK_GOVERNANCE_VETO")
        # broker_connected may be True in a synthetic test fixture. It is NOT
        # proof of a broker connection and is not used as an admission flag.

    primary = {spec.agent_id for spec in COUNCIL_MANIFEST if spec.stage is AgentStage.PRIMARY}
    provided = {item.agent_id for item in ctx.reliability}
    if provided != primary:
        _code(blockers, "reliability", "PRIMARY_SEAT_COVERAGE_INCOMPLETE")
    for item in ctx.reliability:
        if not _utc(item.available_at) or item.available_at > as_of:
            _code(blockers, "reliability", "AVAILABLE_AFTER_AS_OF")
    # RiskLimits and SynthesisPolicy carry values, not original acquisition
    # receipts. Their presence is inspectable, but source attestation is not.

    codes = sorted(blockers)
    report = {
        "assessment_version": VERSION,
        "as_of": as_of.isoformat(),
        "input_family_counts": counts,
        "input_family_count": len(FAMILIES),
        "status": "SYNTHETIC_TIMING_CHECKS_PASS_UNATTESTED" if not codes else "BLOCKED_INPUT_READINESS",
        "blockers": codes,
        "synthetic_context_source_independently_verified": False,
        "macro_version_receipts_independently_verified": False,
        "calendar_completeness_independently_verified": False,
        "portfolio_and_risk_limits_independently_attested": False,
        "market_state_derivation_proven_from_forward_store": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    report["input_evidence_sha256"] = _digest(report)
    return report


def integrated_synthetic_readiness(store, policy, context, *, capture_count=5,
                                   synthetic_only=False):
    """Never dispatch Council unless readiness and existing replay audits pass."""
    inputs = inspect_council_input_readiness(context, synthetic_only=synthetic_only)
    blockers = list(inputs["blockers"])
    if not blockers:
        from backend.agents.test_full_research_council import synthetic_full_context
        if context.model_dump(mode="json") != synthetic_full_context().model_dump(mode="json"):
            blockers.append("SYNTHETIC_FIXTURE_IDENTITY_MISMATCH")
    replay = None
    if not blockers:
        replay = audit_variable_replay(
            store, policy, context, capture_count=capture_count, synthetic_only=True)
        if replay["status"] != "AUDITED_SYNTHETIC_COUNCIL_HOLD":
            blockers.extend(replay["reason_codes"])
        elif (replay["council_seats"] != 31 or replay["council_invoked"] is not True
              or replay["final_action"] != "HOLD" or replay["execution_authorized"] is not False):
            raise InputReadinessError("Canonical Council failed the shadow HOLD contract.")
    result = {
        "assessment_version": VERSION,
        "status": "SYNTHETIC_INPUT_READINESS_SHADOW_HOLD" if not blockers else "BLOCKED",
        "input_report_sha256": inputs["input_evidence_sha256"],
        "replay_report_sha256": replay["report_sha256"] if replay else None,
        "blockers": sorted(set(blockers)),
        "council_invoked": bool(replay and replay["council_invoked"]),
        "council_seats": replay["council_seats"] if replay else 0,
        "synthetic_only": True,
        "all_external_source_attestations_complete": False,
        "real_forward_admission": False,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", required=True, action="store_true")
    parser.add_argument("--captures", type=int, default=5)
    parser.add_argument("--scenario", choices=("complete", "missing_fed", "late_calendar",
                                             "missing_coverage", "stale_risk", "all"), default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    from backend.agents.synthetic_forward_capture_replay import CaptureReplayError
    from backend.data_engine.clock_policy import ClockPolicyError
    scenarios = ("complete", "missing_fed", "late_calendar", "missing_coverage", "stale_risk") if args.scenario == "all" else (args.scenario,)
    outputs = []
    for scenario in scenarios:
        engine = None
        try:
            store, policy, ctx, engine = make_variable_demo(capture_count=args.captures)
            if scenario == "missing_fed":
                ctx = ctx.model_copy(update={"fed_data": ()})
            elif scenario == "late_calendar":
                event = ctx.economic_calendar[0]
                late = event.model_copy(update={"ingested_at": ctx.as_of + timedelta(seconds=1)})
                ctx = ctx.model_copy(update={"economic_calendar": (late,)})
            elif scenario == "missing_coverage":
                ctx = ctx.model_copy(update={"calendar_coverage": None})
            elif scenario == "stale_risk":
                risk = ctx.risk_state.model_copy(update={"as_of": ctx.as_of - timedelta(minutes=10)})
                ctx = ctx.model_copy(update={"risk_state": risk})
            result = integrated_synthetic_readiness(
                store, policy, ctx, capture_count=args.captures, synthetic_only=True)
            outputs.append({"scenario": scenario, "result": result})
        except (InputReadinessError, CaptureReplayError, ClockPolicyError, ValueError, TypeError) as exc:
            print("INPUT READINESS BLOCKED: " + type(exc).__name__)
            return 1
        finally:
            if engine is not None:
                engine.dispose()
    if args.json:
        print(json.dumps(outputs, indent=2, sort_keys=True, allow_nan=False))
    else:
        print("EURUSD ORACLE | Council causal input readiness M53-56")
        for item in outputs:
            r = item["result"]
            print(f"SCENARIO {item['scenario']} | {r['status']} | seats={r['council_seats']} "
                  f"| FINAL=HOLD | digest={r['report_sha256']}")
            for blocker in r["blockers"]:
                print("  " + blocker)
        print("SYNTHETIC ONLY | NO SOURCE ATTESTATION | NO BROKER | NO FILES WRITTEN "
              "| REAL ADMISSION/TRAINING/EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
