
"""Phase 2 C4+D1-D3: Git clock lineage and non-executing forward-cycle gate.

C4 looks up the epoch-bound clock implementation among locally available Git
blobs. D1 checks point-in-time forward health; D2 inventories the 13 causal
Council input gaps; D3 enforces HOLD on every unverified or stale input.

DB reads are delegated to the existing read-only inspector. Git is queried by
rev-list/cat-file only: no checkout, fetch, reset, writes, broker, training or
trade execution. Local Git objects are not independent broker attestation.
"""

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

from backend.data_engine.forward_evidence_health import _parse_as_of, _require_epoch_id
from backend.data_engine.forward_source_lineage import (
    VERSION as LINEAGE_VERSION, inspect_forward_source_lineage,
)
from backend.data_engine.forward_store import encoded

VERSION = "EURUSD_FORWARD_CYCLE_READINESS_V1"
CLOCK_FILE = "backend/data_engine/clock_policy.py"
HEX = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")


class ForwardCycleError(RuntimeError):
    """The provided report or local-history evidence is not trustworthy."""


def _digest(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def _hash_receipt(receipt, field="report_sha256"):
    if not isinstance(receipt, dict) or not HEX.fullmatch(str(receipt.get(field, ""))):
        raise ForwardCycleError("Receipt fingerprint missing or malformed.")
    if receipt[field] != _digest({k: v for k, v in receipt.items() if k != field}):
        raise ForwardCycleError("Receipt fingerprint does not match content.")


def _git(repo, *arguments, timeout=8):
    """Read-only Git plumbing; never invokes shell or changes the worktree."""
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=repo, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ForwardCycleError("Local Git history is unavailable.") from exc
    if result.returncode != 0:
        raise ForwardCycleError("Local Git object query failed.")
    return result.stdout


def audit_bound_clock_git_history(bound_hash, local_hash, *, repo=".", max_versions=100):
    """Search at most max_versions reachable clock-file versions, read-only.

    Hashing Git blob bytes mirrors clock_policy.implementation_sha256() only
    when the worktree uses identical bytes. CRLF conversion is reported as an
    equivalence candidate, NOT an exact reproduction of bound source bytes.
    """
    if not all(isinstance(x, str) and HEX.fullmatch(x) for x in (bound_hash, local_hash)):
        raise ForwardCycleError("Valid clock implementation hashes required.")
    if type(max_versions) is not int or not 1 <= max_versions <= 200:
        raise ForwardCycleError("max_versions must be an integer in [1,200].")
    root = Path(repo).resolve()
    matches = []
    checked = 0
    status = "NOT_FOUND_IN_BOUNDED_LOCAL_GIT_HISTORY"
    notes = []
    try:
        revisions = _git(root, "rev-list", "--all", f"--max-count={max_versions}",
                         "--", CLOCK_FILE).decode("ascii").splitlines()
        if any(COMMIT.fullmatch(revision) is None for revision in revisions):
            raise ForwardCycleError("Local Git revision listing is malformed.")
        for revision in revisions:
            blob = _git(root, "cat-file", "blob", f"{revision}:{CLOCK_FILE}")
            if len(blob) > 131072:
                raise ForwardCycleError("Clock code version exceeds read-only size bound.")
            checked += 1
            exact = sha256(blob).hexdigest() == bound_hash
            # For Windows, an otherwise identical checkout may have CRLF endings.
            converted = blob.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            crlf = not exact and sha256(converted).hexdigest() == bound_hash
            if exact or crlf:
                matches.append({
                    "revision": revision,
                    "match_type": "EXACT_GIT_BLOB_BYTES" if exact else "CRLF_CHECKOUT_EQUIVALENT",
                })
                # One version is enough to locate a local candidate; do not
                # represent later unsearched versions as examined.
                break
        if matches:
            status = "LOCAL_GIT_BLOB_CANDIDATE_FOUND"
        elif not revisions:
            notes.append("NO_REACHABLE_CLOCK_FILE_COMMITS")
        if len(revisions) == max_versions and not matches:
            notes.append("GIT_HISTORY_SEARCH_CAPPED")
    except (ForwardCycleError, UnicodeDecodeError):
        status = "LOCAL_GIT_HISTORY_UNAVAILABLE"
        notes.append("GIT_HISTORY_QUERY_FAILED_OR_UNAVAILABLE")
    result = {
        "bound_implementation_sha256": bound_hash,
        "local_implementation_sha256": local_hash,
        "current_worktree_matches_bound": local_hash == bound_hash,
        "local_history_status": status,
        "revisions_examined": checked,
        "max_versions": max_versions,
        "matches": matches,
        "limitations": notes + [
            "LOCAL_GIT_HISTORY_NOT_INDEPENDENT_BROKER_ATTESTATION",
            "MATCH_DOES_NOT_AUTHORIZE_REPLAY_UNDER_DIFFERENT_CODE",
        ],
        "epoch_policy_modified": False,
        "historical_utc_certified": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def evaluate_forward_cycle_gate(lineage, clock_git, *, max_capture_age_seconds=900):
    """Pure point-in-time decision gate. Never materializes a CouncilContext.

    Locally current data may pass some technical checks, but all 13 real source
    families lack independent first-receipt and lineage proofs. No real Council
    invocation can occur through this function, regardless of input contents.
    """
    _hash_receipt(lineage)
    _hash_receipt(clock_git)
    if lineage.get("assessment_version") != LINEAGE_VERSION:
        raise ForwardCycleError("Source-lineage version mismatch.")
    if lineage.get("final_action") != "HOLD" or lineage.get("execution_authorized") is not False:
        raise ForwardCycleError("Source-lineage HOLD contract violated.")
    if clock_git.get("final_action") != "HOLD" or clock_git.get("execution_authorized") is not False:
        raise ForwardCycleError("Git clock audit HOLD contract violated.")
    if type(max_capture_age_seconds) is not int or not 60 <= max_capture_age_seconds <= 86400:
        raise ForwardCycleError("Capture age threshold must be an integer in [60,86400].")
    cutoff = _parse_as_of(lineage["as_of"])
    _require_epoch_id(lineage["epoch_id"])
    if (lineage["clock_lineage"]["bound_implementation_sha256"] !=
            clock_git["bound_implementation_sha256"] or
            lineage["clock_lineage"]["local_implementation_sha256"] !=
            clock_git["local_implementation_sha256"]):
        raise ForwardCycleError("Git and database clock identities disagree.")

    technical = set()
    if lineage["health_local_status"] != "LOCAL_STRUCTURAL_CHECKS_PASS_UNATTESTED":
        technical.add("SOURCE_HEALTH_BLOCKED")
    if lineage["health_blockers"]:
        technical.update("SOURCE:" + code for code in lineage["health_blockers"])
    if lineage["capture_sample_truncated"]:
        technical.add("CAPTURE_SAMPLE_INCOMPLETE")
    lag = lineage["seconds_since_capture_ingestion"]
    if lag is None or not isinstance(lag, (int, float)) or lag < 0 or lag > max_capture_age_seconds:
        technical.add("CAPTURE_INGESTION_NOT_CURRENT")
    tick_age = lineage["seconds_since_tick"]
    if tick_age is None or not isinstance(tick_age, (int, float)) or tick_age < 0 or tick_age > 120:
        technical.add("LATEST_TICK_NOT_CURRENT")
    last_bar = lineage.get("last_m5_bar")
    expected = (cutoff.replace(minute=(cutoff.minute // 5) * 5,
                               second=0, microsecond=0) - timedelta(minutes=5)).isoformat()
    if last_bar != expected:
        technical.add("LAST_CLOSED_M5_NOT_CURRENT")
    if not lineage["clock_lineage"]["same_local_code_hash"] or not clock_git["current_worktree_matches_bound"]:
        technical.add("BOUND_CLOCK_IMPLEMENTATION_MISMATCH")
    if lineage["unqueried_gap_count"]:
        technical.add("UNCOVERED_FORWARD_QUERY_INTERVALS")

    families = lineage["council_input_families"]
    if (len(families) != 13 or lineage["council_input_family_count"] != 13 or
            len({row["family"] for row in families}) != 13):
        raise ForwardCycleError("Council input-family contract incomplete.")
    # No external attestation is obtained by this inspector; a record claiming
    # otherwise is outside our typed-contract-only evidence contract.
    if any(row["status"] != "TYPED_CONTRACT_ONLY_UNVERIFIED" or
           row["real_point_in_time_lineage_proven"] is not False or
           row["real_first_receipt_attested"] is not False or
           row["real_source_identity_verified"] is not False for row in families):
        raise ForwardCycleError("Unsupported real source admission claim.")
    gap_families = sorted(row["family"] for row in families)
    gates = sorted(technical | {
        "EXTERNAL_COUNCIL_SOURCE_PROVENANCE_UNVERIFIED",
        "NO_REAL_COUNCIL_CONTEXT_ADMITTED",
        "NO_BROKER_EXECUTION_VALIDATION",
    })
    result = {
        "assessment_version": VERSION,
        "epoch_id": lineage["epoch_id"],
        "as_of": cutoff.isoformat(),
        "source_lineage_sha256": lineage["report_sha256"],
        "git_clock_audit_sha256": clock_git["report_sha256"],
        "clock_git_history_status": clock_git["local_history_status"],
        "technical_observation_status": "BLOCKED" if technical else "LOCAL_TIMING_CHECKS_PASS_UNATTESTED",
        "technical_blockers": sorted(technical),
        "unverified_input_families": gap_families,
        "unverified_input_count": len(gap_families),
        "real_council_dispatch_blockers": gates,
        "real_forward_council_readiness": "BLOCKED_UNVERIFIED_EVIDENCE",
        "council_invoked": False,
        "council_seats_run": 0,
        "collector_process_state": "NOT_INSPECTED",
        "independent_source_attestation": False,
        "historical_utc_certified": False,
        "historical_admission": False,
        "real_forward_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def inspect_forward_cycle(engine, *, epoch_id, as_of, repo=".", max_captures=32,
                          max_source_rows=50000, max_capture_age_seconds=900,
                          max_git_versions=100):
    """Only one delegated DB inspection plus bounded read-only Git lookups."""
    lineage = inspect_forward_source_lineage(
        engine, epoch_id=epoch_id, as_of=as_of, max_captures=max_captures,
        max_source_rows=max_source_rows, max_capture_age_seconds=max_capture_age_seconds,
    )
    clock = lineage["clock_lineage"]
    history = audit_bound_clock_git_history(
        clock["bound_implementation_sha256"], clock["local_implementation_sha256"],
        repo=repo, max_versions=max_git_versions,
    )
    return evaluate_forward_cycle_gate(lineage, history,
                                       max_capture_age_seconds=max_capture_age_seconds)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-db", required=True, action="store_true")
    parser.add_argument("--git-read-only", required=True, action="store_true")
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
            report = inspect_forward_cycle(
                engine, epoch_id=epoch, as_of=cutoff, repo=Path.cwd(),
                max_captures=args.captures, max_source_rows=args.source_limit,
                max_capture_age_seconds=args.capture_age_seconds,
                max_git_versions=args.max_git_versions,
            )
        finally:
            engine.dispose()
    except Exception as exc:
        print("FORWARD CYCLE INSPECTION BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Forward non-executing cycle gate C4+D1-D3")
        print("Epoch: " + report["epoch_id"] + " | cutoff=" + report["as_of"])
        print("Technical checks: " + report["technical_observation_status"])
        print("Git clock candidate: " + report["clock_git_history_status"])
        print("Unverified real Council inputs: " + str(report["unverified_input_count"]) + "/13")
        for reason in report["technical_blockers"]:
            print("  TECHNICAL BLOCKER " + reason)
        print("REAL COUNCIL: BLOCKED | seats=0 | FINAL=HOLD")
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY DB/GIT | NO BROKER | NO TRAINING | NO EXECUTION")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
