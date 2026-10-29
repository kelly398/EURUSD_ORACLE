
"""P3-21..24: final read-only engineering handover, not trading admission.

Rechecks provenance, point-in-time lineage, evaluation scope and local Git ancestry.
Local hashes and Git commits are NOT independent broker or timestamp attestations.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess

from backend.data_engine.forward_store import hashed
from backend.runtime.forward_source_provenance import (
    VERSION as SOURCE_VERSION, inspect_forward_source_provenance,
)
from backend.runtime.forward_market_input_lineage import (
    VERSION as MARKET_VERSION, inspect_market_input_lineage,
)
from backend.runtime.forward_prospective_evaluation import (
    VERSION as EVAL_VERSION, _safe_json, _timestamp, inspect_evaluation_contract,
)

VERSION = "EURUSD_PHASE3_ENGINEERING_HANDOVER_P3_24_V1"
BRANCH = "feature/utc-research-m01-m08"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GIT_OID = re.compile(r"^[0-9a-f]{40,64}$")
PRIOR_COMMITS = (
    ("PHASE2_ACCEPTANCE", "bbf0772", (
        "backend/data_engine/phase2_acceptance.py",
        "backend/data_engine/test_phase2_acceptance.py",
    )),
    ("P3_01_04", "4788e52", (
        "backend/runtime/forward_collector_preflight.py",
        "backend/runtime/test_forward_collector_preflight.py",
    )),
    ("P3_05_08", "6a4f9ee", (
        "backend/runtime/forward_recovery_evidence.py",
        "backend/runtime/test_forward_recovery_evidence.py",
    )),
    ("P3_09_12", "faaba1c", (
        "backend/runtime/forward_source_provenance.py",
        "backend/runtime/test_forward_source_provenance.py",
    )),
    ("P3_13_16", "e4f6c68", (
        "backend/runtime/forward_market_input_lineage.py",
        "backend/runtime/test_forward_market_input_lineage.py",
    )),
    ("P3_17_20", "eb712a9", (
        "backend/runtime/forward_prospective_evaluation.py",
        "backend/runtime/test_forward_prospective_evaluation.py",
    )),
)

EXTERNAL_GATES = (
    "BROKER_SERVER_IDENTITY_AND_CLOCK_OFFSET_ATTESTATION",
    "ORIGINAL_HISTORICAL_UTC_AND_SOURCE_FIRST_AVAILABILITY",
    "INDEPENDENT_FORWARD_RECEIPT_AND_ORDERED_API_BATCH_EVIDENCE",
    "RECORDER_PROCESS_LOGS_AND_RESTART_CAUSE_DIAGNOSIS",
    "CAUSAL_PROVENANCE_FOR_ALL_13_COUNCIL_INPUT_FAMILIES",
    "MACRO_AND_CALENDAR_PUBLICATION_VERSION_PROVENANCE",
    "INDEPENDENT_RISK_POLICY_AND_CROSS_ASSET_PROVENANCE",
    "EXTERNALLY_ANCHORED_PROSPECTIVE_PREDICTIONS_AND_HELDOUT_OUTCOMES",
    "BROKER_VERIFIED_ALL_IN_COSTS_FILLS_AND_OUT_OF_SAMPLE_VALIDATION",
)

# Expected diagnostics do not themselves prove source integrity defects.
ALLOWED_SOURCE = {
    "BOUND_CLOCK_IMPLEMENTATION_MISMATCH",
    "CAPTURE_NOT_CURRENT_AT_CUTOFF",
    "EPOCH_NOT_ACTIVE_AT_CUTOFF",
}
ALLOWED_MARKET = {
    "CURRENT_CLOCK_CODE_DIFFERS_FROM_BOUND_EPOCH",
    "CAPTURE_NOT_CURRENT_AT_DECISION",
    "TICK_NOT_CURRENT_AT_DECISION",
    "M5_NOT_LAST_CLOSED_AT_DECISION",
    "EPOCH_NOT_ACTIVE_AT_DECISION",
}
ALLOWED_EVAL = ALLOWED_MARKET | {
    "NO_REGISTRATIONS_IN_SUPPLIED_SCOPE",
    "NO_POST_HORIZON_OUTCOMES_IN_SUPPLIED_SCOPE",
    "NO_MATCHED_ALL_IN_COST_RECORDS_IN_SUPPLIED_SCOPE",
    "NO_HELDOUT_SPLIT_IN_SUPPLIED_SCOPE",
}


class Phase3AcceptanceError(RuntimeError):
    """Reject malformed evidence or unauthorized claimed readiness."""


def _git(repo, *args):
    try:
        response = subprocess.run(
            ["git", *args], cwd=Path(repo), capture_output=True,
            check=False, timeout=12,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 128, ""
    return response.returncode, response.stdout.decode(
        "utf-8", "replace"
    ).strip()


def audit_phase3_git(
    repo=".", *, checkpoints=PRIOR_COMMITS, expected_branch=BRANCH
):
    """Inspect current local history; never fetch, reset or checkout."""
    root = Path(repo).resolve()
    rc, head = _git(root, "rev-parse", "--verify", "HEAD")
    head = head if rc == 0 and GIT_OID.fullmatch(head) else None

    rc, branch = _git(root, "branch", "--show-current")
    branch = branch if rc == 0 else None

    checked = []
    for label, commit, paths in checkpoints:
        if (
            not re.fullmatch(r"[0-9a-f]{7,40}", commit)
            or not paths
            or any(
                not isinstance(path, str)
                or not path.startswith("backend/")
                or ".." in path
                for path in paths
            )
        ):
            raise Phase3AcceptanceError(
                "Invalid static checkpoint specification."
            )

        rc_rev, revision = _git(
            root, "rev-parse", "--verify", commit + "^{commit}"
        )
        rc_ancestor, _ = _git(
            root, "merge-base", "--is-ancestor", commit, "HEAD"
        )
        rc_paths, tree = _git(
            root, "ls-tree", "-r", "--name-only", "HEAD", "--", *paths
        )

        tracked = (
            sorted(set(paths) & set(tree.splitlines()))
            if rc_paths == 0 else []
        )
        verified = bool(
            head and rc_rev == 0 and GIT_OID.fullmatch(revision)
            and rc_ancestor == 0 and len(tracked) == len(paths)
        )

        checked.append({
            "checkpoint": label,
            "expected_commit": commit,
            "commit_at_head_ancestor": verified,
            "tracked_files_found": len(tracked),
            "tracked_files_expected": len(paths),
        })

    count = sum(row["commit_at_head_ancestor"] for row in checked)

    result = {
        "local_head": head,
        "branch": branch,
        "expected_branch": expected_branch,
        "branch_matches": branch == expected_branch,
        "prior_commits_required": len(checkpoints),
        "prior_commits_verified": count,
        "checkpoints": checked,
        "status": (
            "LOCAL_GIT_ANCESTRY_VERIFIED"
            if head and branch == expected_branch
            and count == len(checkpoints)
            else "LOCAL_GIT_ANCESTRY_INCOMPLETE"
        ),
        "git_commands_read_only": True,
        "remote_push_independently_verified": False,
        "regression_suite_run_by_inspector": False,
    }
    result["report_sha256"] = hashed(result)
    return result


def _check_receipt(report, version, disabled):
    if (
        not isinstance(report, dict)
        or report.get("assessment_version") != version
    ):
        raise Phase3AcceptanceError(
            "Unexpected source report version."
        )

    digest = report.get("report_sha256")
    if (
        not isinstance(digest, str)
        or not HEX64.fullmatch(digest)
        or digest != hashed({
            key: val
            for key, val in report.items()
            if key != "report_sha256"
        })
    ):
        raise Phase3AcceptanceError(
            "Source report digest mismatch."
        )

    if (
        report.get("read_only") is not True
        or report.get("final_action") != "HOLD"
    ):
        raise Phase3AcceptanceError(
            "Source report HOLD/read-only boundary violated."
        )

    for key in disabled:
        if report.get(key) is not False:
            raise Phase3AcceptanceError(
                "Source report authorization boundary violated."
            )


def evaluate_phase3_acceptance(source, market, evaluation, git):
    """Engineering review never grants real-data or trading admission."""
    _check_receipt(source, SOURCE_VERSION, (
        "database_modified",
        "policy_modified",
        "collector_started",
        "real_data_admission",
        "training_authorized",
        "execution_authorized",
    ))

    _check_receipt(market, MARKET_VERSION, (
        "database_modified",
        "collector_started",
        "historical_utc_certified",
        "real_forward_council_admission",
        "council_invoked",
        "prospective_prediction_registered",
        "training_authorized",
        "execution_authorized",
    ))

    _check_receipt(evaluation, EVAL_VERSION, (
        "database_modified",
        "files_created",
        "prospective_prediction_registered_by_inspector",
        "real_forward_council_admission",
        "council_invoked",
        "training_authorized",
        "execution_authorized",
        "performance_measured",
        "transaction_costs_measured",
        "independent_prediction_registration_attested",
        "independent_outcome_source_attested",
        "broker_fill_and_costs_attested",
        "heldout_split_independently_frozen",
        "external_timestamp_anchor_verified",
    ))

    if (
        not isinstance(git, dict)
        or git.get("report_sha256") != hashed({
            k: v for k, v in git.items()
            if k != "report_sha256"
        })
        or git.get("git_commands_read_only") is not True
        or git.get("remote_push_independently_verified") is not False
        or git.get("prior_commits_required") != len(PRIOR_COMMITS)
    ):
        raise Phase3AcceptanceError(
            "Local Git report contract invalid."
        )

    for row, expected in zip(
        git.get("checkpoints", ()), PRIOR_COMMITS
    ):
        if (
            row.get("checkpoint") != expected[0]
            or row.get("expected_commit") != expected[1]
            or row.get("tracked_files_expected") != len(expected[2])
        ):
            raise Phase3AcceptanceError(
                "Git checkpoints do not match required anchors."
            )

    if (
        git.get("prior_commits_verified") != sum(
            row.get("commit_at_head_ancestor") is True
            for row in git.get("checkpoints", ())
        )
        or len(git.get("checkpoints", ())) != len(PRIOR_COMMITS)
    ):
        raise Phase3AcceptanceError(
            "Local Git audit counts inconsistent."
        )

    if (
        source.get("epoch_id") != market.get("epoch_id")
        or source.get("epoch_id") != evaluation.get("epoch_id")
    ):
        raise Phase3AcceptanceError(
            "Epoch identity changed across source reports."
        )

    if (
        source.get("as_of") != market.get("as_of")
        or source.get("as_of") != evaluation.get("as_of")
    ):
        raise Phase3AcceptanceError(
            "Inspection cutoffs disagree."
        )

    if (
        market.get("decision_at") != evaluation.get("decision_at")
        or evaluation.get("market_lineage_sha256")
        != market["report_sha256"]
    ):
        raise Phase3AcceptanceError(
            "Evaluation is not bound to this market lineage."
        )

    decision = _timestamp(market["decision_at"])
    cutoff = _timestamp(source["as_of"])

    if (
        decision > cutoff
        or not HEX64.fullmatch(source["epoch_id"])
    ):
        raise Phase3AcceptanceError(
            "Invalid decision time or epoch identifier."
        )

    if (
        market.get("council_seats_run") != 0
        or evaluation.get("council_seats_run") != 0
        or market.get(
            "council_input_families_independently_attested"
        ) != 0
        or market.get("independent_broker_source_attested") is not False
        or market.get("independent_first_availability_attested") is not False
    ):
        raise Phase3AcceptanceError(
            "Unverified market data claimed independent readiness."
        )

    if (
        source.get("broker_identity_fields", {}).get(
            "independent_broker_identity_attested"
        ) is not False
        or source.get("broker_identity_fields", {}).get(
            "independent_clock_offset_attested"
        ) is not False
        or source.get("capture_sample", {}).get(
            "independent_first_receipt_attested"
        ) is not False
    ):
        raise Phase3AcceptanceError(
            "Unverified broker source claimed attestation."
        )

    if (
        evaluation.get("out_of_sample_trades_measured") is not None
        or evaluation.get("evidence_scope") not in (
            "NONE_SUPPLIED",
            "LOCAL_SELF_HASHED_BUNDLE_UNATTESTED",
        )
    ):
        raise Phase3AcceptanceError(
            "Unattested evaluation evidence claimed performance."
        )

    if (
        evaluation.get("evidence_scope") == "NONE_SUPPLIED"
        and any(
            evaluation.get(name) != 0
            for name in (
                "registration_count_in_scope",
                "post_horizon_outcomes_in_scope",
                "cost_records_in_scope",
            )
        )
    ):
        raise Phase3AcceptanceError(
            "Missing bundle cannot contain evidence records."
        )

    src_policy = source.get("policy_file", {})
    mkt_policy = market.get("policy_copy", {})

    if src_policy != mkt_policy:
        raise Phase3AcceptanceError(
            "Policy-file evidence disagrees between inspections."
        )

    src_blockers = set(
        source.get("local_structural_blockers", ())
    )
    mkt_blockers = set(market.get("local_blockers", ()))
    eval_blockers = set(
        evaluation.get("local_evidence_blockers", ())
    )

    if not all(
        isinstance(x, str)
        for x in src_blockers | mkt_blockers | eval_blockers
    ):
        raise Phase3AcceptanceError(
            "Non-string local blocker supplied."
        )

    if not mkt_blockers.issubset(eval_blockers):
        raise Phase3AcceptanceError(
            "Evaluation omitted market blockers."
        )

    rows = source.get("capture_sample", {})
    if (
        not isinstance(rows, dict)
        or not isinstance(rows.get("sample_truncated"), dict)
    ):
        raise Phase3AcceptanceError(
            "Source coverage fields missing."
        )

    local_evidence_ok = (
        src_policy.get("canonical_epoch_match") is True
        and src_policy.get(
            "observed_digest_matches_expected"
        ) is True
        and rows.get("captures_examined", 0) > 0
        and rows.get("ticks_examined", 0) > 0
        and rows.get("bars_examined", 0) > 0
        and not any(rows["sample_truncated"].values())
        and not any(
            market.get("first_owned_receipts", {})
            .get("sample_truncated", {}).values()
        )
        and market.get("locally_derived_market_values") is not None
        and src_blockers.issubset(ALLOWED_SOURCE)
        and mkt_blockers.issubset(ALLOWED_MARKET)
        and eval_blockers.issubset(ALLOWED_EVAL)
    )

    git_ok = (
        git.get("status") == "LOCAL_GIT_ANCESTRY_VERIFIED"
        and git.get("branch_matches") is True
        and git.get("prior_commits_verified") == len(PRIOR_COMMITS)
    )

    accepted = bool(local_evidence_ok and git_ok)

    blockers = sorted(
        ["SOURCE:" + item for item in src_blockers]
        + ["MARKET:" + item for item in mkt_blockers]
        + ["EVALUATION:" + item for item in eval_blockers]
        + (
            []
            if local_evidence_ok
            else ["LOCAL_EVIDENCE_REVIEW_INCOMPLETE_OR_TAMPERED"]
        )
        + (
            []
            if git_ok
            else ["LOCAL_GIT_ANCESTRY_INCOMPLETE"]
        )
    )

    result = {
        "assessment_version": VERSION,
        "epoch_id": source["epoch_id"],
        "as_of": cutoff.isoformat(),
        "decision_at": decision.isoformat(),
        "source_provenance_sha256": source["report_sha256"],
        "market_lineage_sha256": market["report_sha256"],
        "prospective_evaluation_sha256":
            evaluation["report_sha256"],
        "git_audit_sha256": git["report_sha256"],
        "git_head": git["local_head"],
        "local_prior_commits_verified":
            git["prior_commits_verified"],
        "local_prior_commits_required": len(PRIOR_COMMITS),
        "phase3_previously_committed_checkpoints": (
            20 if git_ok else None
        ),
        "phase3_planned_checkpoints": 24,
        "p3_21_24_review_performed": True,
        "engineering_acceptance": (
            "ACCEPTED_WITH_EXTERNAL_BLOCKERS"
            if accepted
            else "BLOCKED_INCOMPLETE_LOCAL_ENGINEERING_EVIDENCE"
        ),
        "operational_readiness":
            "BLOCKED_UNVERIFIED_EXTERNAL_EVIDENCE",
        "source_counts_in_inspected_scope": {
            "captures": rows["captures_examined"],
            "ticks": rows["ticks_examined"],
            "bars": rows["bars_examined"],
        },
        "prospective_registration_count_in_supplied_scope":
            evaluation["registration_count_in_scope"],
        "independent_external_gates": [
            {
                "gate": gate,
                "verification":
                    "NOT_ESTABLISHED_BY_THIS_REVIEW",
            }
            for gate in EXTERNAL_GATES
        ],
        "external_gates_verified_by_this_review": 0,
        "council_input_families_independently_verified": 0,
        "council_input_families_total": 13,
        "local_review_blockers": blockers,
        "next_evidence_actions": [
            "INVESTIGATE_RECORDER_PROCESS_AND_ORIGINAL_LOGS_READ_ONLY",
            "INDEPENDENTLY_ATTEST_BROKER_IDENTITY_CLOCK_AND_SOURCE_RECEIPTS",
            "RECONCILE_CLOCK_IMPLEMENTATION_WITHOUT_MUTATING_STORED_EPOCH",
            "OBTAIN_CAUSAL_MACRO_CALENDAR_RISK_AND_COUNCIL_INPUT_PROVENANCE",
            "ESTABLISH_EXTERNALLY_TIMESTAMPED_PROSPECTIVE_RECORDS_AND_HELDOUT_PLAN",
            "OBTAIN_BROKER_VERIFIED_COSTS_OUTCOMES_AND_EXECUTION_EVIDENCE",
        ],
        "remote_push_independently_verified": False,
        "full_windows_regression_run_by_inspector": False,
        "engineering_review_is_trading_authorization": False,
        "read_only": True,
        "database_modified": False,
        "policy_modified": False,
        "collector_started": False,
        "prospective_prediction_registered": False,
        "real_forward_council_admission": False,
        "historical_utc_certified": False,
        "council_invoked": False,
        "council_seats_run": 0,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = hashed(result)
    return result


def inspect_phase3_acceptance(
    engine,
    *,
    epoch_id,
    as_of,
    decision_at,
    policy_file,
    expected_policy_sha256,
    repo=".",
    evidence_file=None,
    max_captures=32,
    max_source_rows=5000,
    checkpoints=PRIOR_COMMITS,
    expected_branch=BRANCH,
):
    """Read-only orchestration of inspectors and local Git audit."""
    source = inspect_forward_source_provenance(
        engine,
        epoch_id=epoch_id,
        as_of=as_of,
        policy_file=policy_file,
        expected_policy_sha256=expected_policy_sha256,
        max_captures=max_captures,
        max_rows=max_source_rows,
    )
    market = inspect_market_input_lineage(
        engine,
        epoch_id=epoch_id,
        as_of=as_of,
        decision_at=decision_at,
        policy_file=policy_file,
        expected_policy_sha256=expected_policy_sha256,
        max_captures=max_captures,
        max_rows=max_source_rows,
    )
    bundle, file_hash = _safe_json(evidence_file)
    evaluation = inspect_evaluation_contract(
        market,
        bundle=bundle,
        file_sha256=file_hash,
    )
    history = audit_phase3_git(
        repo,
        checkpoints=checkpoints,
        expected_branch=expected_branch,
    )
    return evaluate_phase3_acceptance(
        source, market, evaluation, history
    )


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only-db", action="store_true", required=True
    )
    parser.add_argument(
        "--git-read-only", action="store_true", required=True
    )
    parser.add_argument(
        "--read-only-evidence", action="store_true", required=True
    )
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--policy-file", required=True)
    parser.add_argument(
        "--expected-policy-sha256", required=True
    )
    parser.add_argument("--evidence-file")
    parser.add_argument("--captures", type=int, default=32)
    parser.add_argument("--source-rows", type=int, default=5000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if (
            not isinstance(args.epoch_id, str)
            or not HEX64.fullmatch(args.epoch_id)
        ):
            raise Phase3AcceptanceError("Invalid epoch ID.")

        if not HEX64.fullmatch(
            args.expected_policy_sha256
        ):
            raise Phase3AcceptanceError(
                "Invalid expected policy SHA-256."
            )

        cutoff = _timestamp(args.as_of)
        decision = _timestamp(args.decision_at)

        if (
            decision > cutoff
            or cutoff > datetime.now(timezone.utc)
        ):
            raise Phase3AcceptanceError(
                "Future or invalid decision cutoff."
            )

        if (
            type(args.captures) is not int
            or not 1 <= args.captures <= 100
        ):
            raise Phase3AcceptanceError(
                "Invalid capture limit."
            )

        if (
            type(args.source_rows) is not int
            or not 1 <= args.source_rows <= 5000
        ):
            raise Phase3AcceptanceError(
                "Invalid source row limit."
            )

        from backend.database.connection import engine

        try:
            report = inspect_phase3_acceptance(
                engine,
                epoch_id=args.epoch_id,
                as_of=cutoff,
                decision_at=decision,
                policy_file=args.policy_file,
                expected_policy_sha256=args.expected_policy_sha256,
                repo=Path.cwd(),
                evidence_file=args.evidence_file,
                max_captures=args.captures,
                max_source_rows=args.source_rows,
            )
        finally:
            engine.dispose()

    except Exception as exc:
        print(
            "PHASE 3 ENGINEERING REVIEW BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(json.dumps(
            report,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ))
    else:
        print(
            "EURUSD ORACLE | Phase 3 engineering acceptance P3-21..24"
        )
        print(
            "Prior local Git anchors: %s/%s" % (
                report["local_prior_commits_verified"],
                report["local_prior_commits_required"],
            )
        )
        print(
            "Engineering: " + report["engineering_acceptance"]
        )
        print(
            "Operational readiness: "
            + report["operational_readiness"]
        )
        print(
            "External gates independently verified by review: 0/9"
        )
        print(
            "Council families independently verified by review: 0/13"
        )
        print(
            "Source scope: "
            + str(report["source_counts_in_inspected_scope"])
        )
        print(
            "Supplied registrations: "
            + str(
                report[
                    "prospective_registration_count_in_supplied_scope"
                ]
            )
        )
        for blocker in report["local_review_blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print(
            "READ-ONLY | NO NEW EPOCH | NO COUNCIL | "
            "NO TRAINING | NO EXECUTION | FINAL=HOLD"
        )
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
