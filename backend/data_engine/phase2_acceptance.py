
"""Phase 2 E4 acceptance: read-only Git ancestry and evidence-report review.

Engineering acceptance is separate from external data/trading readiness. This
module never invokes the Council, initializes DB tables, trains, or trades.
Its only DB access is the existing forward research inspector's read-only path.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

from backend.data_engine.forward_cycle_readiness import ForwardCycleError, _hash_receipt
from backend.data_engine.forward_evidence_health import _parse_as_of, _require_epoch_id
from backend.data_engine.forward_research_evaluation import (
    VERSION as RESEARCH_VERSION, inspect_forward_research,
)
from backend.data_engine.forward_store import encoded

VERSION = "EURUSD_PHASE2_ENGINEERING_ACCEPTANCE_E4_V1"
EXPECTED_BRANCH = "feature/utc-research-m01-m08"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CHECKPOINTS = (
    ("PHASE1_M57_M60", "fe5780a", (
        "backend/agents/synthetic_research_mvp_acceptance.py",
        "backend/agents/test_synthetic_research_mvp_acceptance.py",
    )),
    ("A4_B1_B3", "5b021fb", (
        "backend/data_engine/forward_evidence_health.py",
        "backend/data_engine/test_forward_evidence_health.py",
    )),
    ("B4_C1_C3", "f922273", (
        "backend/data_engine/forward_source_lineage.py",
        "backend/data_engine/test_forward_source_lineage.py",
    )),
    ("C4_D1_D3", "8abaa1b", (
        "backend/data_engine/forward_cycle_readiness.py",
        "backend/data_engine/test_forward_cycle_readiness.py",
    )),
    ("D4_E1_E3", "04eebb4", (
        "backend/data_engine/forward_research_evaluation.py",
        "backend/data_engine/test_forward_research_evaluation.py",
    )),
)

# These are evidence requirements, not claims that the underlying evidence
# does not exist anywhere. This E4 review does not independently certify them.
EXTERNAL_GATES = (
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


class Phase2AcceptanceError(RuntimeError):
    """Acceptance inputs did not satisfy a required safety invariant."""


def _digest(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def _git(repo, *args):
    """Read-only Git commands; no shell, fetch, checkout, reset or index edits."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 128, b""
    return result.returncode, result.stdout


def audit_phase2_git(repo=".", *, checkpoints=CHECKPOINTS,
                     expected_branch=EXPECTED_BRANCH):
    """Verify ancestry and tracked files at HEAD, not a remote-push claim."""
    root = Path(repo).resolve()
    rc_head, head_bytes = _git(root, "rev-parse", "--verify", "HEAD")
    rc_branch, branch_bytes = _git(root, "branch", "--show-current")
    head = head_bytes.decode("ascii", "replace").strip() if rc_head == 0 else None
    branch = branch_bytes.decode("utf-8", "replace").strip() if rc_branch == 0 else None
    rows = []
    for label, commit, paths in checkpoints:
        if not (isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{7,40}", commit)):
            raise Phase2AcceptanceError("Unsafe expected Git revision format.")
        if not paths or any(not p.startswith("backend/") or ".." in p for p in paths):
            raise Phase2AcceptanceError("Unsafe expected tracked path.")
        rc_commit, full = _git(root, "rev-parse", "--verify", commit + "^{commit}")
        resolved = full.decode("ascii", "replace").strip() if rc_commit == 0 else None
        rc_ancestor, _ = _git(root, "merge-base", "--is-ancestor", commit, "HEAD")
        rc_tree, listed = _git(root, "ls-tree", "-r", "--name-only", "HEAD", "--", *paths)
        listed_paths = set(listed.decode("utf-8", "replace").splitlines()) if rc_tree == 0 else set()
        files_present = sorted(p for p in paths if p in listed_paths)
        passed = (head is not None and resolved is not None and rc_ancestor == 0
                  and rc_tree == 0 and len(files_present) == len(paths))
        rows.append({
            "checkpoint": label, "expected_commit": commit,
            "resolved_commit": resolved, "ancestor_of_head": rc_ancestor == 0,
            "tracked_files_present": files_present, "tracked_files_required": len(paths),
            "status": "VERIFIED_IN_LOCAL_HEAD" if passed else "NOT_VERIFIED_IN_LOCAL_HEAD",
        })
    verified = sum(row["status"] == "VERIFIED_IN_LOCAL_HEAD" for row in rows)
    result = {
        "head": head, "branch": branch, "expected_branch": expected_branch,
        "branch_matches": branch == expected_branch,
        "checkpoint_count": len(rows), "checkpoints_verified": verified,
        "checkpoints": rows, "read_only_git_queries": True,
        "status": "VERIFIED_LOCAL_CHECKPOINT_ANCESTRY" if (
            verified == len(rows) and branch == expected_branch and bool(head)
        ) else "INCOMPLETE_LOCAL_CHECKPOINT_EVIDENCE",
        "remote_push_independently_verified": False,
    }
    result["report_sha256"] = _digest(result)
    return result


def evaluate_phase2_acceptance(research, git_audit):
    """Pure acceptance verdict; never promotes evidence to real-data admission."""
    try:
        _hash_receipt(research)
        _hash_receipt(git_audit)
    except (ForwardCycleError, TypeError, ValueError) as exc:
        raise Phase2AcceptanceError("Input report integrity invalid.") from exc
    if research.get("assessment_version") != RESEARCH_VERSION:
        raise Phase2AcceptanceError("Unexpected research report version.")
    if research.get("final_action") != "HOLD" or any(
        research.get(key) is not False for key in (
            "historical_utc_certified", "real_forward_admission", "training_authorized",
            "execution_authorized", "council_invoked",
        )
    ) or research.get("council_seats_run") != 0 or research.get("read_only") is not True:
        raise Phase2AcceptanceError("Research HOLD/read-only boundary violated.")
    if (research.get("measured_predictive_performance") is not None
            or research.get("measured_transaction_costs") is not None
            or research.get("measured_out_of_sample_trades") is not None
            or research.get("performance_conclusion") != "NOT_MEASURABLE_FROM_THIS_EVIDENCE_SCOPE"):
        raise Phase2AcceptanceError("Unsupported performance measurement claimed.")
    if (research.get("unverified_council_input_count") != 13
            or len(set(research.get("unverified_council_families", ()))) != 13
            or not research.get("readiness_blockers")
            or research.get("research_readiness") != "BLOCKED_EXTERNAL_EVIDENCE_AND_EVALUATION"):
        raise Phase2AcceptanceError("External readiness constraints missing.")
    if (git_audit.get("read_only_git_queries") is not True
            or git_audit.get("remote_push_independently_verified") is not False
            or git_audit.get("checkpoint_count") != len(CHECKPOINTS)):
        raise Phase2AcceptanceError("Git inspection scope invalid.")
    if git_audit.get("checkpoints_verified") != sum(
        row.get("status") == "VERIFIED_IN_LOCAL_HEAD"
        for row in git_audit.get("checkpoints", ())
    ):
        raise Phase2AcceptanceError("Git checkpoint counts inconsistent.")
    epoch = _require_epoch_id(research["epoch_id"])
    cutoff = _parse_as_of(research["as_of"])
    git_ok = (git_audit["status"] == "VERIFIED_LOCAL_CHECKPOINT_ANCESTRY"
              and git_audit["checkpoints_verified"] == git_audit["checkpoint_count"]
              and git_audit["branch_matches"] is True)
    technical_ok = (research.get("technical_observation_status")
                    == "LOCAL_TIMING_CHECKS_PASS_UNATTESTED")
    result = {
        "assessment_version": VERSION, "epoch_id": epoch, "as_of": cutoff.isoformat(),
        "research_evidence_sha256": research["report_sha256"],
        "git_audit_sha256": git_audit["report_sha256"],
        "local_git_head": git_audit["head"],
        "verified_prior_checkpoint_commits": git_audit["checkpoints_verified"],
        "required_prior_checkpoint_commits": git_audit["checkpoint_count"],
        "phase1_roadmap_milestones": 60,
        "phase2_planned_development_checkpoints": 20,
        "phase2_committed_before_e4": 19 if git_ok else None,
        "e4_technical_review_performed": True,
        "engineering_acceptance": (
            "ACCEPTED_WITH_EXTERNAL_BLOCKERS" if git_ok
            else "BLOCKED_INCOMPLETE_LOCAL_GIT_EVIDENCE"
        ),
        "operational_readiness": "BLOCKED_UNVERIFIED_EXTERNAL_EVIDENCE",
        "forward_feed_local_timing": (
            "LOCAL_TIMING_CHECKS_PASS_UNATTESTED" if technical_ok else "BLOCKED"
        ),
        "clock_candidate_status": research["clock_candidate_status"],
        "clock_local_worktree_matches_epoch": research["clock_local_worktree_matches_epoch"],
        "council_source_families_unverified": 13,
        "external_evidence_gates": [
            {"gate": name, "verification": "NOT_ESTABLISHED_BY_PHASE2_REVIEW"}
            for name in EXTERNAL_GATES
        ],
        "external_gate_count": len(EXTERNAL_GATES),
        "research_blockers": sorted(set(research["readiness_blockers"])),
        "performance_measured": False,
        "transaction_costs_measured": False,
        "git_remote_push_proven_by_this_report": False,
        "read_only": True, "council_invoked": False, "council_seats_run": 0,
        "historical_utc_certified": False, "real_forward_admission": False,
        "training_authorized": False, "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def inspect_phase2_acceptance(engine, *, epoch_id, as_of, repo=".",
                              max_captures=32, max_source_rows=50000,
                              max_git_versions=100, max_capture_age_seconds=900,
                              checkpoints=CHECKPOINTS, expected_branch=EXPECTED_BRANCH):
    research = inspect_forward_research(
        engine, epoch_id=epoch_id, as_of=as_of, repo=repo,
        max_captures=max_captures, max_source_rows=max_source_rows,
        max_git_versions=max_git_versions,
        max_capture_age_seconds=max_capture_age_seconds,
    )
    history = audit_phase2_git(repo, checkpoints=checkpoints,
                               expected_branch=expected_branch)
    return evaluate_phase2_acceptance(research, history)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-db", action="store_true", required=True)
    parser.add_argument("--git-read-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--captures", type=int, default=32)
    parser.add_argument("--source-limit", type=int, default=50000)
    parser.add_argument("--max-git-versions", type=int, default=100)
    parser.add_argument("--capture-age-seconds", type=int, default=900)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        epoch = _require_epoch_id(args.epoch_id)
        cutoff = _parse_as_of(args.as_of)
        from backend.database.connection import engine
        try:
            result = inspect_phase2_acceptance(
                engine, epoch_id=epoch, as_of=cutoff, repo=Path.cwd(),
                max_captures=args.captures, max_source_rows=args.source_limit,
                max_git_versions=args.max_git_versions,
                max_capture_age_seconds=args.capture_age_seconds,
            )
        finally:
            engine.dispose()
    except Exception as exc:
        print("PHASE 2 ACCEPTANCE BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Phase 2 E4 engineering acceptance")
        print("Epoch: " + result["epoch_id"] + " | cutoff=" + result["as_of"])
        print("Git checkpoint ancestry: " + str(result["verified_prior_checkpoint_commits"])
              + "/" + str(result["required_prior_checkpoint_commits"]))
        print("Engineering: " + result["engineering_acceptance"])
        print("Real-data readiness: " + result["operational_readiness"])
        print("External gate attestations established by this review: 0/9")
        print("Council input families unverified: 13/13")
        print("SHA-256: " + result["report_sha256"])
        print("NO COUNCIL | NO TRAINING | NO EXECUTION | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
