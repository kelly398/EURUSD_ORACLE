
"""Milestones 57-60: bounded synthetic EURUSD ORACLE research-MVP acceptance.

M57: exercise real typed input-readiness, capture audit and canonical Council.
M58: re-run each independent fixture to detect nondeterministic results.
M59: verify all adverse cases block, and explicitly inventory external blockers.
M60: issue one deterministic, fail-closed research acceptance verdict.

IMPORTANT: Synthetic integration acceptance is NOT source admission, out-of-sample
validation, training authorization, or a licence to trade. No broker/HTTP/DB
connections, persistent outputs, or historical research exports are used.
Only isolated in-memory fixtures are constructed by the existing test helper.
"""

import argparse
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json

from backend.agents.synthetic_council_input_readiness import (
    integrated_synthetic_readiness,
)
from backend.agents.synthetic_forward_variable_replay import (
    make_variable_demo,
)
from backend.data_engine.forward_store import encoded

VERSION = "EURUSD_SYNTHETIC_RESEARCH_MVP_ACCEPTANCE_V1"


class ResearchAcceptanceError(RuntimeError):
    """Any violated acceptance invariant fails closed."""


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    capture_count: int
    source_fault: str = "none"
    input_fault: str = "none"
    expected_reason: str = ""


SCENARIOS = (
    Scenario("complete_5", 5),
    Scenario("complete_8", 8),
    Scenario("missing_fed", 5, input_fault="missing_fed",
             expected_reason="FED_DATA:ABSENT"),
    Scenario("late_calendar", 5, input_fault="late_calendar",
             expected_reason="ECONOMIC_CALENDAR:INGESTED_AFTER_AS_OF"),
    Scenario("missing_coverage", 5, input_fault="missing_coverage",
             expected_reason="CALENDAR_COVERAGE:ABSENT"),
    Scenario("stale_risk", 5, input_fault="stale_risk",
             expected_reason="RISK_STATE:STATE_NOT_CURRENT"),
    Scenario("risk_kill_switch", 5, input_fault="risk_kill_switch",
             expected_reason="RISK_STATE:RISK_GOVERNANCE_VETO"),
    Scenario("altered_fixture", 5, input_fault="altered_fixture",
             expected_reason="SYNTHETIC_FIXTURE_IDENTITY_MISMATCH"),
    Scenario("delayed_capture", 5, source_fault="delayed",
             expected_reason="SNAPSHOT_5:VISIBLE_CAPTURE_COUNT_MISMATCH"),
    Scenario("missing_candle", 5, source_fault="missing",
             expected_reason="SNAPSHOT_3:FIRST_CAPTURE_BAR_MEMBERSHIP_INVALID"),
    Scenario("overlapping_capture", 5, source_fault="overlap",
             expected_reason="SNAPSHOT_3:TICK_INTERVAL_OVERLAP"),
    Scenario("conflicting_tick", 5, source_fault="conflict",
             expected_reason="SNAPSHOT_1:TICK_SOURCE_INTEGRITY_INVALID"),
    Scenario("reordered_capture", 5, source_fault="reordered",
             expected_reason="SNAPSHOT_5:TICK_INTERVAL_GAP"),
)

# These are evidence requirements, not post-hoc guesses based on test successes.
UNRESOLVED_GATES = (
    "INDEPENDENT_BROKER_IDENTITY_AND_CLOCK_ATTESTATION",
    "HISTORICAL_EVENT_UTC_CERTIFICATION",
    "HISTORICAL_ORIGINAL_FIRST_AVAILABILITY_EVIDENCE",
    "INDEPENDENT_MACRO_PUBLICATION_AND_RECEIPT_PROVENANCE",
    "ECONOMIC_CALENDAR_VERSION_AND_COVERAGE_ATTESTATION",
    "EXTERNAL_RISK_STATE_RECONCILIATION_AND_POLICY_PROVENANCE",
    "COMPLETE_REAL_FORWARD_COUNCIL_INPUT_CAUSAL_DERIVATION",
    "OUT_OF_SAMPLE_PREDICTIVE_AND_COST_VALIDATION",
    "BROKER_EXECUTION_AND_LIVE_RISK_VALIDATION",
)


def _hash(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def _check(condition, reason):
    if not condition:
        raise ResearchAcceptanceError(reason)


def _mutate_input(context, fault):
    if fault == "none":
        return context
    if fault == "missing_fed":
        return context.model_copy(update={"fed_data": ()})
    if fault == "late_calendar":
        first = context.economic_calendar[0]
        bad = first.model_copy(update={
            "ingested_at": context.as_of + timedelta(seconds=1)
        })
        return context.model_copy(update={
            "economic_calendar": (bad, *context.economic_calendar[1:])
        })
    if fault == "missing_coverage":
        return context.model_copy(update={"calendar_coverage": None})
    if fault in ("stale_risk", "risk_kill_switch"):
        changes = ({"as_of": context.as_of - timedelta(minutes=10)}
                   if fault == "stale_risk" else {"kill_switch": True})
        return context.model_copy(update={
            "risk_state": context.risk_state.model_copy(update=changes)
        })
    if fault == "altered_fixture":
        changed = context.risk_limits.model_copy(update={
            "max_spread_pips": 99.0
        })
        return context.model_copy(update={"risk_limits": changed})
    raise ResearchAcceptanceError("Unknown synthetic input fault.")


def _verify_result(spec, result):
    """Do not turn failure of an expected failure into a passing acceptance."""
    _check(isinstance(result, dict), "Unrecognized integrated result.")
    required = (
        "status", "blockers", "council_invoked", "council_seats",
        "final_action", "synthetic_only", "real_forward_admission",
        "historical_admission", "training_authorized", "execution_authorized",
        "all_external_source_attestations_complete", "report_sha256",
        "input_report_sha256", "replay_report_sha256",
    )
    _check(all(key in result for key in required),
           "Integrated result is missing required fields.")
    _check(result["synthetic_only"] is True and
           result["final_action"] == "HOLD" and
           all(result[key] is False for key in (
               "real_forward_admission", "historical_admission",
               "training_authorized", "execution_authorized",
               "all_external_source_attestations_complete",
           )), "Safety or source-attestation boundary was violated.")
    _check(isinstance(result["blockers"], list) and
           all(isinstance(x, str) for x in result["blockers"]),
           "Invalid source blocker evidence.")
    _check(isinstance(result["report_sha256"], str) and
           len(result["report_sha256"]) == 64 and
           result["report_sha256"] == _hash({
               k: v for k, v in result.items() if k != "report_sha256"
           }), "Integrated report hash invalid.")

    if spec.expected_reason:
        _check(result["status"] == "BLOCKED" and
               result["council_invoked"] is False and
               type(result["council_seats"]) is int and
               result["council_seats"] == 0 and
               spec.expected_reason in result["blockers"],
               "Negative scenario did not block as required: " + spec.name)
    else:
        _check(result["status"] == "SYNTHETIC_INPUT_READINESS_SHADOW_HOLD" and
               result["council_invoked"] is True and
               type(result["council_seats"]) is int and
               result["council_seats"] == 31 and
               result["blockers"] == [] and
               isinstance(result["replay_report_sha256"], str) and
               len(result["replay_report_sha256"]) == 64,
               "Positive scenario did not exercise the whole Council: " + spec.name)


def _one_pass(spec):
    """Construct and dispose one isolated source; only in-memory fixture writes."""
    engine = None
    try:
        store, policy, context, engine = make_variable_demo(
            capture_count=spec.capture_count, fault=spec.source_fault
        )
        changed = _mutate_input(context, spec.input_fault)
        result = integrated_synthetic_readiness(
            store, policy, changed,
            capture_count=spec.capture_count, synthetic_only=True,
        )
        _verify_result(spec, result)
        # Expose only diagnostic hashes and bounded reason codes.
        return result
    finally:
        if engine is not None:
            engine.dispose()


def assess_research_mvp(*, synthetic_only=False, scenarios=SCENARIOS):
    """Issue an acceptance verdict ONLY if the entire prescribed matrix passes.

    The scenarios argument exists to allow injection of failures in automated
    tests, not to redefine the acceptance plan. A subset cannot be accepted.
    """
    _check(synthetic_only is True, "Explicit synthetic-only opt-in is required.")
    cases = tuple(scenarios)
    _check(cases == SCENARIOS, "Full immutable acceptance matrix required.")
    _check(len({s.name for s in cases}) == len(cases),
           "Acceptance scenario identity duplicated.")

    receipts = []
    for spec in cases:
        first = _one_pass(spec)
        second = _one_pass(spec)
        _check(first == second, "Non-deterministic report: " + spec.name)
        receipts.append({
            "scenario": spec.name,
            "capture_count": spec.capture_count,
            "expected": "BLOCKED" if spec.expected_reason else "COUNCIL_HOLD",
            "observed_status": first["status"],
            "council_invoked": first["council_invoked"],
            "council_seats": first["council_seats"],
            "blockers": first["blockers"],
            "integrated_report_sha256": first["report_sha256"],
            "repeated_identically": True,
            "final_action": "HOLD",
        })

    _check(len({r["integrated_report_sha256"] for r in receipts}) == len(receipts),
           "Different acceptance scenarios have indistinguishable receipts.")
    positive = sum(not s.expected_reason for s in cases)
    negative = len(cases) - positive
    _check(positive == 2 and negative == 11,
           "Acceptance matrix size or classification drifted.")
    result = {
        "assessment_version": VERSION,
        "acceptance_scope": "SYNTHETIC_RESEARCH_INTEGRATION_ONLY",
        "synthetic_research_mvp_status": "ACCEPTED_WITH_EXTERNAL_BLOCKERS",
        "scenario_count": len(receipts),
        "positive_scenarios": positive,
        "negative_scenarios": negative,
        "determinism_repeated_runs": len(receipts) * 2,
        "scenarios": receipts,
        "operational_readiness": "BLOCKED_UNVERIFIED_EXTERNAL_EVIDENCE",
        "unresolved_external_gates": list(UNRESOLVED_GATES),
        "all_external_gates_met": False,
        "historical_utc_certified": False,
        "real_forward_admission": False,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "broker_connected": False,
        "final_action": "HOLD",
    }
    result["acceptance_sha256"] = _hash(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Full synthetic EURUSD ORACLE research-MVP acceptance M57-60"
    )
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_research_mvp(synthetic_only=args.synthetic_only)
    except Exception as exc:
        # A failed acceptance should return a failure code, not a partial PASS.
        print("RESEARCH MVP ACCEPTANCE BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print("EURUSD ORACLE | Synthetic research MVP acceptance M57-60")
        for item in report["scenarios"]:
            print(
                f"SCENARIO {item['scenario']} | {item['observed_status']} | "
                f"seats={item['council_seats']} | repeated=True | "
                f"FINAL=HOLD | digest={item['integrated_report_sha256']}"
            )
            for reason in item["blockers"]:
                print("  " + reason)
        print("SYNTHETIC RESEARCH MVP: " + report["synthetic_research_mvp_status"])
        print("OPERATIONAL READINESS: " + report["operational_readiness"])
        print("UNRESOLVED EXTERNAL GATES: " + str(len(UNRESOLVED_GATES)))
        print("ACCEPTANCE SHA-256: " + report["acceptance_sha256"])
        print("IN-MEMORY SYNTHETIC ONLY | NO BROKER | NO FILES WRITTEN | "
              "NO REAL ADMISSION | TRAINING/EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
