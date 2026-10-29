
"""Phase 2 D4+E1-E3: reproducible, non-executing evaluation evidence audit.

Reads the existing forward-feed tables through one delegated read-only inspection,
then queries local Git history without altering it. This is an EVIDENCE-INVENTORY
report, not a claim that research predictions, outcomes or execution costs were
measured. Its output is always HOLD, with no real Council invocation.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re

from backend.data_engine.forward_cycle_readiness import (
    ForwardCycleError, _hash_receipt, audit_bound_clock_git_history,
    evaluate_forward_cycle_gate,
)
from backend.data_engine.forward_evidence_health import _parse_as_of, _require_epoch_id
from backend.data_engine.forward_source_lineage import inspect_forward_source_lineage
from backend.data_engine.forward_store import encoded

VERSION = "EURUSD_FORWARD_RESEARCH_EVALUATION_V1"
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
MATCH_TYPES = {"EXACT_GIT_BLOB_BYTES", "CRLF_CHECKOUT_EQUIVALENT"}


class ForwardEvaluationError(RuntimeError):
    """Evidence identity, provenance, or readiness invariant failed."""


def _digest(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def _require(condition, message):
    if not condition:
        raise ForwardEvaluationError(message)


def evaluate_forward_research(lineage, git_clock, gate):
    """Combine already generated, content-hashed local reports without IO.

    These hashes detect incidental changes between reports. They are not
    signatures, broker attestations, or evidence that their content is true.
    """
    for name, receipt in (("lineage", lineage), ("clock", git_clock), ("gate", gate)):
        try:
            _hash_receipt(receipt)
        except (ForwardCycleError, TypeError, ValueError) as exc:
            raise ForwardEvaluationError(f"Invalid {name} report digest.") from exc

    _require(gate.get("source_lineage_sha256") == lineage["report_sha256"],
             "Gate does not reference the supplied lineage report.")
    _require(gate.get("git_clock_audit_sha256") == git_clock["report_sha256"],
             "Gate does not reference the supplied Git audit.")
    _require(gate.get("epoch_id") == lineage.get("epoch_id"),
             "Epoch identities disagree.")
    _require(gate.get("as_of") == lineage.get("as_of"),
             "Point-in-time cutoffs disagree.")
    epoch = _require_epoch_id(gate["epoch_id"])
    cutoff = _parse_as_of(gate["as_of"])
    clock = lineage.get("clock_lineage", {})
    _require(clock.get("bound_implementation_sha256") == git_clock.get("bound_implementation_sha256")
             and clock.get("local_implementation_sha256") == git_clock.get("local_implementation_sha256"),
             "Clock implementation identities disagree.")

    _require(all(report.get("final_action") == "HOLD" for report in (lineage, git_clock, gate)),
             "Upstream HOLD restriction was not preserved.")
    _require(all(report.get("execution_authorized") is False for report in (lineage, git_clock, gate)),
             "Upstream execution restriction was not preserved.")
    _require(gate.get("council_invoked") is False and gate.get("council_seats_run") == 0,
             "Cannot evaluate a report claiming a dispatched Council.")
    _require(gate.get("real_forward_admission") is False
             and gate.get("training_authorized") is False,
             "Upstream admission restriction was not preserved.")
    _require(lineage.get("read_only") is True, "Source inspection is not read-only.")

    members = lineage.get("council_input_families")
    _require(isinstance(members, list) and len(members) == 13
             and lineage.get("council_input_family_count") == 13
             and lineage.get("missing_external_attestations") == 13,
             "Expected 13 unverified Council input contracts.")
    unverified = sorted(item["family"] for item in members)
    _require(unverified == gate.get("unverified_input_families")
             and gate.get("unverified_input_count") == 13,
             "Council source inventory differs from the readiness gate.")
    _require(all(item.get("real_point_in_time_lineage_proven") is False
                 and item.get("real_first_receipt_attested") is False for item in members),
             "Unexpected Council source certification claim.")

    history_state = git_clock.get("local_history_status")
    candidates = git_clock.get("matches", [])
    _require(isinstance(candidates, list) and len(candidates) <= 1,
             "Clock candidate list is malformed.")
    if history_state == "LOCAL_GIT_BLOB_CANDIDATE_FOUND":
        _require(len(candidates) == 1, "Clock candidate is missing.")
        match = candidates[0]
        _require(isinstance(match, dict)
                 and isinstance(match.get("revision"), str)
                 and COMMIT.fullmatch(match["revision"]) is not None
                 and match.get("match_type") in MATCH_TYPES,
                 "Clock candidate metadata is invalid.")
        candidate = {"revision": match["revision"], "match_type": match["match_type"]}
        candidate_status = (
            "EXACT_LOCAL_GIT_BYTES_UNATTESTED" if match["match_type"] == "EXACT_GIT_BLOB_BYTES"
            else "LINE_ENDING_EQUIVALENCE_ONLY_UNATTESTED"
        )
    else:
        _require(not candidates, "Clock audit status contradicts matched candidates.")
        candidate = None
        candidate_status = "NO_REPRODUCIBLE_LOCAL_CLOCK_BLOB_MATCH"

    technical_blockers = list(gate.get("technical_blockers", []))
    _require(gate.get("technical_observation_status") in
             ("BLOCKED", "LOCAL_TIMING_CHECKS_PASS_UNATTESTED"),
             "Invalid technical observation status.")
    _require(gate.get("technical_observation_status") ==
             ("BLOCKED" if technical_blockers else "LOCAL_TIMING_CHECKS_PASS_UNATTESTED"),
             "Technical observation and blockers disagree.")

    # The source tables contain market data, not independently registered
    # prospective predictions, later outcomes or broker execution receipts.
    # Do NOT claim they are absent from the whole repository: not inspected.
    evidence = [
        {"dimension": "forward_captures", "status": (
            "LOCAL_SAMPLE_PRESENT_UNATTESTED" if lineage["capture_count_in_bounded_sample"]
            else "NO_CAPTURE_VISIBLE_AT_CUTOFF"),
         "evaluated_count": lineage["capture_count_in_bounded_sample"]},
        {"dimension": "council_input_first_availability", "status": "NOT_VERIFIED",
         "evaluated_count": 0},
        {"dimension": "prospectively_registered_predictions", "status": "NOT_INSPECTED",
         "evaluated_count": None},
        {"dimension": "independent_post_horizon_outcomes", "status": "NOT_INSPECTED",
         "evaluated_count": None},
        {"dimension": "held_out_evaluation_period", "status": "NOT_ESTABLISHED",
         "evaluated_count": None},
        {"dimension": "matched_bid_ask_commission_and_slippage", "status": "NOT_MEASURED",
         "evaluated_count": None},
        {"dimension": "broker_fill_and_realized_execution_cost", "status": "NOT_INSPECTED",
         "evaluated_count": None},
    ]
    blockers = sorted(set(technical_blockers) | {
        "INDEPENDENT_COUNCIL_INPUT_AVAILABILITY_UNVERIFIED",
        "NO_VERIFIED_PROSPECTIVE_PREDICTION_OUTCOME_PAIRS_IN_SCOPE",
        "NO_HELD_OUT_PREDICTIVE_PERFORMANCE_EVIDENCE",
        "NO_VERIFIED_ALL_IN_TRANSACTION_COST_MODEL",
        "NO_BROKER_FILL_OR_LIVE_RISK_VALIDATION",
        "REAL_FORWARD_COUNCIL_NOT_ADMITTED",
    })
    if candidate_status != "EXACT_LOCAL_GIT_BYTES_UNATTESTED":
        blockers.append("EPOCH_CLOCK_SOURCE_BYTES_NOT_EXACTLY_LOCATED")
    if not clock.get("same_local_code_hash", False):
        blockers.append("EPOCH_CLOCK_IMPLEMENTATION_DRIFT_UNRESOLVED")

    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch,
        "as_of": cutoff.isoformat(),
        "inputs": {
            "lineage_sha256": lineage["report_sha256"],
            "git_clock_sha256": git_clock["report_sha256"],
            "cycle_gate_sha256": gate["report_sha256"],
        },
        "clock_original_candidate": candidate,
        "clock_candidate_status": candidate_status,
        "clock_local_worktree_matches_epoch": git_clock["current_worktree_matches_bound"],
        "clock_history_search_status": history_state,
        "coverage": {
            "bounded_forward_capture_count": lineage["capture_count_in_bounded_sample"],
            "sample_truncated": lineage["capture_sample_truncated"],
            "overlapping_query_windows": lineage["overlap_query_count"],
            "unqueried_intervals_in_sample": lineage["unqueried_gap_count"],
            "last_visible_capture_ingested_at": lineage["last_visible_capture_ingested_at"],
            "seconds_since_last_capture": lineage["seconds_since_capture_ingestion"],
            "latest_tick_at": lineage["last_tick"],
            "latest_m5_at": lineage["last_m5_bar"],
            "full_epoch_coverage_certified": False,
        },
        "prospective_evaluation_evidence": evidence,
        "measured_out_of_sample_trades": None,
        "measured_predictive_performance": None,
        "measured_transaction_costs": None,
        "performance_conclusion": "NOT_MEASURABLE_FROM_THIS_EVIDENCE_SCOPE",
        "technical_observation_status": gate["technical_observation_status"],
        "readiness_blockers": sorted(set(blockers)),
        "unverified_council_families": unverified,
        "unverified_council_input_count": 13,
        "research_readiness": "BLOCKED_EXTERNAL_EVIDENCE_AND_EVALUATION",
        "scope": "BOUNDED_LOCAL_FORWARD_RECEIPTS_AND_TYPED_CONTRACTS_ONLY",
        "read_only": True,
        "council_invoked": False,
        "council_seats_run": 0,
        "historical_utc_certified": False,
        "real_forward_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def inspect_forward_research(engine, *, epoch_id, as_of, repo=".", max_captures=32,
                             max_source_rows=50000, max_capture_age_seconds=900,
                             max_git_versions=100):
    """One delegated read-only DB inspection and bounded read-only Git lookups."""
    lineage = inspect_forward_source_lineage(
        engine, epoch_id=epoch_id, as_of=as_of, max_captures=max_captures,
        max_source_rows=max_source_rows, max_capture_age_seconds=max_capture_age_seconds,
    )
    clock = lineage["clock_lineage"]
    git_clock = audit_bound_clock_git_history(
        clock["bound_implementation_sha256"], clock["local_implementation_sha256"],
        repo=repo, max_versions=max_git_versions,
    )
    gate = evaluate_forward_cycle_gate(lineage, git_clock,
                                       max_capture_age_seconds=max_capture_age_seconds)
    return evaluate_forward_research(lineage, git_clock, gate)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-db", action="store_true", required=True)
    parser.add_argument("--git-read-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--captures", type=int, default=32)
    parser.add_argument("--source-limit", type=int, default=50000)
    parser.add_argument("--capture-age-seconds", type=int, default=900)
    parser.add_argument("--max-git-versions", type=int, default=100)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        epoch = _require_epoch_id(args.epoch_id)
        cutoff = _parse_as_of(args.as_of)
        from backend.database.connection import engine
        try:
            report = inspect_forward_research(
                engine, epoch_id=epoch, as_of=cutoff, repo=Path.cwd(),
                max_captures=args.captures, max_source_rows=args.source_limit,
                max_capture_age_seconds=args.capture_age_seconds,
                max_git_versions=args.max_git_versions,
            )
        finally:
            engine.dispose()
    except Exception as exc:
        print("FORWARD RESEARCH EVALUATION BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Research evaluation evidence D4+E1-E3")
        print("Epoch: " + report["epoch_id"] + " | cutoff=" + report["as_of"])
        print("Bounded captures: " + str(report["coverage"]["bounded_forward_capture_count"]))
        print("Clock candidate: " + report["clock_candidate_status"])
        if report["clock_original_candidate"]:
            candidate = report["clock_original_candidate"]
            print("Git revision: " + candidate["revision"] + " | " + candidate["match_type"])
        print("Predictive performance: NOT MEASURED")
        print("Transaction costs: NOT MEASURED")
        print("Council source provenance: 0/13 independently verified")
        for code in report["readiness_blockers"]:
            print("  BLOCKER " + code)
        print("REPORT SHA-256: " + report["report_sha256"])
        print("READ-ONLY | COUNCIL 0 SEATS | NO TRAINING | NO EXECUTION | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
