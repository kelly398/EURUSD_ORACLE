
"""S5-21..24: static, read-only Stage 5 engineering and research handover.

This inspector validates local Git ancestry and source contracts. It may inspect
existing locally saved report snapshots but NEVER treats them as third-party
attestation. No fetch, broker, database, Council, collector, training or trades.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

VERSION = "EURUSD_STAGE5_FINAL_RESEARCH_HANDOVER_S5_21_24_V1"
BRANCH = "feature/utc-research-m01-m08"
STAGE4_ANCHOR = "761f60f"
UTC = timezone.utc
SHA = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_SOURCE_BYTES = 1048576
MAX_INDEX_BYTES = 131072
MAX_REPORT_BYTES = 1048576

# Commit, corresponding inspector/test files, inspector version and public entrypoint.
BATCHES = (
    (
        "S5-01_TO_S5-04", "1031a5b",
        "backend/runtime/stage5_evidence_intake.py",
        "backend/runtime/test_stage5_evidence_intake.py",
        "EURUSD_STAGE5_EVIDENCE_INTAKE_S5_01_04_V1", "assess_stage5_intake",
    ),
    (
        "S5-05_TO_S5-08", "6612b09",
        "backend/runtime/stage5_broker_clock_attestation.py",
        "backend/runtime/test_stage5_broker_clock_attestation.py",
        "EURUSD_STAGE5_BROKER_CLOCK_S5_05_08_V1", "assess_stage5_broker_clock",
    ),
    (
        "S5-09_TO_S5-12", "10b9de6",
        "backend/runtime/stage5_capture_recovery_readiness.py",
        "backend/runtime/test_stage5_capture_recovery_readiness.py",
        "EURUSD_STAGE5_CAPTURE_RECOVERY_S5_09_12_V1", "assess_stage5_capture_recovery",
    ),
    (
        "S5-13_TO_S5-16", "925eced",
        "backend/runtime/stage5_council_source_causality.py",
        "backend/runtime/test_stage5_council_source_causality.py",
        "EURUSD_STAGE5_COUNCIL_SOURCE_CAUSALITY_S5_13_16_V1", "assess_stage5_council_sources",
    ),
    (
        "S5-17_TO_S5-20", "dcccf0b",
        "backend/runtime/stage5_prospective_evidence_readiness.py",
        "backend/runtime/test_stage5_prospective_evidence_readiness.py",
        "EURUSD_STAGE5_PROSPECTIVE_EVIDENCE_S5_17_20_V1", "assess_stage5_prospective",
    ),
)

EXTERNAL_GATES = (
    "INDEPENDENT_BROKER_SERVER_IDENTITY",
    "INDEPENDENT_BROKER_CLOCK_REFERENCE_AND_OFFSET",
    "INDEPENDENT_ORIGINAL_CAPTURE_RECEIPTS_AND_SOURCE_PAYLOADS",
    "CURRENT_FORWARD_SESSION_WITH_APPROVED_NEW_EPOCH_PLAN",
    "INDEPENDENT_MARKET_AND_MACRO_SOURCE_PROVENANCE_FOR_13_FAMILIES",
    "INDEPENDENT_COUNCIL_DERIVED_INPUT_AND_MODEL_PROVENANCE",
    "OPERATOR_APPROVED_RISK_AND_RECOVERY_CONTROLS",
    "EXTERNALLY_TIMESTAMPED_PREDECISION_PREDICTIONS",
    "INDEPENDENT_POST_HORIZON_OUTCOME_RECEIPTS",
    "BROKER_CONFIRMED_EXECUTION_COSTS_IF_TRADES_EXIST",
    "INDEPENDENT_PRE_EVALUATION_HOLDOUT_FREEZE",
)
REPORT_INDEX_KEYS = {"schema_version", "epoch_id", "as_of", "entries"}
REPORT_ENTRY_KEYS = {"batch", "relative_path", "file_sha256"}


class Stage5HandoverError(ValueError):
    """Invalid local engineering state or supplied snapshot evidence."""


def _utc(value):
    if not isinstance(value, str):
        raise Stage5HandoverError("Explicit UTC timestamp text required.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise Stage5HandoverError("Invalid UTC timestamp.") from None
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise Stage5HandoverError("Explicit zero-offset UTC required.")
    return result.astimezone(UTC)


def _digest(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise Stage5HandoverError("Duplicate JSON key.")
        obj[key] = value
    return obj


def _read(path, limit):
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise Stage5HandoverError("Existing regular evidence file required.")
    try:
        with file.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise Stage5HandoverError("Evidence file unreadable.") from None
    if len(raw) > limit:
        raise Stage5HandoverError("Evidence file exceeds size limit.")
    return raw


def _json(raw):
    try:
        obj = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                Stage5HandoverError("Nonfinite JSON refused.")
            ),
        )
    except Stage5HandoverError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise Stage5HandoverError("Invalid JSON evidence.") from None
    if not isinstance(obj, dict):
        raise Stage5HandoverError("Evidence must be a JSON object.")
    return obj


def _relative_existing(root, relative):
    if (
        not isinstance(relative, str) or not 1 <= len(relative) <= 240
        or "\\" in relative or ":" in relative or "\x00" in relative
        or PurePosixPath(relative).is_absolute()
        or any(part in ("", ".", "..") for part in relative.split("/"))
    ):
        raise Stage5HandoverError("Unsafe relative report path.")
    base = Path(root)
    if not base.is_dir() or base.is_symlink():
        raise Stage5HandoverError("Existing non-symlink report root required.")
    for component in relative.split("/"):
        base = base / component
        if base.is_symlink():
            raise Stage5HandoverError("Symlinked report path refused.")
    return base


def _git(repo, *arguments):
    """Local, bounded, no-lock Git queries. Never contacts origin."""
    env = os.environ.copy()
    env["GIT_OPTIONAL_LOCKS"] = "0"
    # Avoid caller-supplied alternate Git directories/worktrees overriding cwd.
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        env.pop(key, None)
    try:
        result = subprocess.run(
            ["git", *arguments], cwd=repo, env=env, stdin=subprocess.DEVNULL,
            capture_output=True, check=False, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Stage5HandoverError("Local Git inspection unavailable.") from None
    if len(result.stdout) > 131072 or len(result.stderr) > 131072:
        raise Stage5HandoverError("Git response too large.")
    return result.returncode, result.stdout.decode("utf-8", errors="replace").strip()


def _full_commit(repo, short):
    code, value = _git(repo, "rev-parse", "--verify", short + "^{commit}")
    if code or not GIT_SHA.fullmatch(value) or not value.startswith(short):
        raise Stage5HandoverError("Required Git commit anchor missing: " + short)
    return value


def inspect_stage5_git_history(repo):
    """Verify five anchors, their exact two-file commits, unchanged blobs."""
    root = Path(repo)
    if not root.is_dir() or root.is_symlink():
        raise Stage5HandoverError("Repository directory unavailable.")
    code, toplevel = _git(root, "rev-parse", "--show-toplevel")
    if code or Path(toplevel).resolve() != root.resolve():
        raise Stage5HandoverError("Expected Git repository root required.")
    code, branch = _git(root, "branch", "--show-current")
    if code or branch != BRANCH:
        raise Stage5HandoverError("Unexpected research branch.")
    code, head = _git(root, "rev-parse", "HEAD")
    if code or not GIT_SHA.fullmatch(head):
        raise Stage5HandoverError("Git HEAD unavailable.")
    code, remote = _git(root, "rev-parse", "--verify", "refs/remotes/origin/" + BRANCH)
    tracking_matches = code == 0 and remote == head
    stage4 = _full_commit(root, STAGE4_ANCHOR)
    anchors = [stage4]
    result = []
    for batch, short, inspector, test, version, function in BATCHES:
        anchor = _full_commit(root, short)
        prior = anchors[-1]
        ancestry = (
            _git(root, "merge-base", "--is-ancestor", prior, anchor)[0] == 0
            and _git(root, "merge-base", "--is-ancestor", anchor, head)[0] == 0
        )
        code, changed = _git(
            root, "diff-tree", "--no-commit-id", "--name-only", "-r", anchor,
        )
        exact_files = code == 0 and sorted(changed.splitlines()) == sorted((inspector, test))
        blobs_match = True
        for name in (inspector, test):
            a_code, a_hash = _git(root, "rev-parse", anchor + ":" + name)
            h_code, h_hash = _git(root, "rev-parse", "HEAD:" + name)
            blobs_match &= bool(
                a_code == 0 and h_code == 0 and GIT_SHA.fullmatch(a_hash)
                and a_hash == h_hash
            )
        code, dirty = _git(
            root, "status", "--porcelain", "--untracked-files=no", "--", inspector, test,
        )
        if code:
            raise Stage5HandoverError("Git source status unavailable.")
        clean = not dirty
        file = root / inspector
        raw = _read(file, MAX_SOURCE_BYTES)
        try:
            source = ast.parse(raw.decode("utf-8"))
        except (UnicodeError, SyntaxError, RecursionError, ValueError):
            raise Stage5HandoverError("Committed Stage 5 inspector cannot be parsed.") from None
        found_versions = [
            node.value.value for node in source.body
            if isinstance(node, ast.Assign)
            and any(isinstance(n, ast.Name) and n.id == "VERSION" for n in node.targets)
            and isinstance(node.value, ast.Constant)
        ]
        method_present = any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function for node in source.body
        )
        source_contract_matches = found_versions == [version] and method_present
        verified = bool(ancestry and exact_files and blobs_match and clean and source_contract_matches)
        result.append({
            "batch": batch, "commit": anchor,
            "anchor_follows_previous_and_precedes_head": ancestry,
            "commit_changes_exact_expected_two_files": exact_files,
            "files_unchanged_since_commit": blobs_match,
            "previous_files_clean": clean,
            "inspector_version_and_entrypoint_present": source_contract_matches,
            "locally_verified": verified,
        })
        anchors.append(anchor)
    return {
        "branch": branch, "head": head, "stage4_completion_commit": stage4,
        "local_remote_tracking_matches_head": tracking_matches,
        "origin_contacted": False, "git_mutated": False,
        "batches": result,
        "verified_prior_batches": sum(x["locally_verified"] for x in result),
        "verified_prior_checkpoints": sum(4 for x in result if x["locally_verified"]),
        "all_five_prior_batches_verified": bool(
            tracking_matches and all(x["locally_verified"] for x in result)
        ),
    }


def inspect_saved_reports(index_file, *, root, epoch_id, as_of):
    """Optional original saved reports: local hashes, not independent evidence."""
    empty = {
        "status": "NOT_SUPPLIED", "index_sha256": None,
        "locally_reconciled_report_count": 0, "reports_by_batch": [],
        "independent_external_evidence_verified": False,
        "windows_regression_attested_by_this_inspector": False,
    }
    if index_file is None:
        if root is not None:
            raise Stage5HandoverError("Report root without index refused.")
        return empty
    if root is None:
        raise Stage5HandoverError("Report root required with index.")
    if not isinstance(epoch_id, str) or not SHA.fullmatch(epoch_id):
        raise Stage5HandoverError("Invalid epoch identifier.")
    cutoff = _utc(as_of)
    raw = _read(index_file, MAX_INDEX_BYTES)
    data = _json(raw)
    if (
        set(data) != REPORT_INDEX_KEYS
        or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1
        or data["epoch_id"] != epoch_id
        or _utc(data["as_of"]) != cutoff
    ):
        raise Stage5HandoverError("Saved-report index contract/scope mismatch.")
    entries = data["entries"]
    if not isinstance(entries, list) or len(entries) > len(BATCHES):
        raise Stage5HandoverError("Saved-report index entry count invalid.")
    versions = {batch: version for batch, _, _, _, version, _ in BATCHES}
    seen = set()
    summaries = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != REPORT_ENTRY_KEYS:
            raise Stage5HandoverError("Saved-report entry contract invalid.")
        batch, expected_hash = entry["batch"], entry["file_sha256"]
        if batch not in versions or batch in seen:
            raise Stage5HandoverError("Unknown or duplicate saved-report batch.")
        if not isinstance(expected_hash, str) or not SHA.fullmatch(expected_hash):
            raise Stage5HandoverError("Invalid saved-report file digest.")
        content = _read(_relative_existing(root, entry["relative_path"]), MAX_REPORT_BYTES)
        if sha256(content).hexdigest() != expected_hash:
            raise Stage5HandoverError("Saved-report byte SHA-256 mismatch.")
        report = _json(content)
        claimed_digest = report.get("report_sha256")
        if (
            report.get("assessment_version") != versions[batch]
            or report.get("epoch_id") != epoch_id
            or report.get("final_action") != "HOLD"
            or report.get("read_only") is not True
            or not isinstance(claimed_digest, str)
            or not SHA.fullmatch(claimed_digest)
            or _digest({k: v for k, v in report.items() if k != "report_sha256"})
                != claimed_digest
            or _utc(report.get("as_of")) > cutoff
        ):
            raise Stage5HandoverError("Saved-report scope, digest or HOLD contract invalid.")
        summaries.append({
            "batch": batch, "saved_report_sha256": claimed_digest,
            "original_file_sha256": expected_hash,
            "local_digest_consistent": True,
            "independently_attested": False,
        })
        seen.add(batch)
    return {
        "status": "LOCAL_REPORT_SNAPSHOTS_UNATTESTED",
        "index_sha256": sha256(raw).hexdigest(),
        "locally_reconciled_report_count": len(summaries),
        "reports_by_batch": sorted(summaries, key=lambda x: x["batch"]),
        "independent_external_evidence_verified": False,
        "windows_regression_attested_by_this_inspector": False,
    }


def assess_stage5_handover(*, repo, epoch_id, as_of, report_index=None, report_root=None):
    if not isinstance(epoch_id, str) or not SHA.fullmatch(epoch_id):
        raise Stage5HandoverError("Invalid historical epoch ID.")
    cutoff = _utc(as_of)
    git = inspect_stage5_git_history(repo)
    snapshots = inspect_saved_reports(
        report_index, root=report_root, epoch_id=epoch_id, as_of=cutoff.isoformat(),
    )
    verified = git["all_five_prior_batches_verified"]
    blockers = ["EXTERNAL_GATE_NOT_INDEPENDENTLY_VERIFIED:" + x for x in EXTERNAL_GATES]
    if not verified:
        blockers.append("FIVE_BATCH_GIT_CHAIN_NOT_LOCALLY_CONSISTENT")
    if snapshots["status"] == "NOT_SUPPLIED":
        blockers.append("PREVIOUS_JSON_REPORT_SNAPSHOTS_NOT_SUPPLIED")
    blockers.extend((
        "EXTERNAL_MARKET_AND_COUNCIL_INPUT_FAMILIES_UNATTESTED_0_OF_13",
        "PROSPECTIVE_OUTCOMES_COSTS_AND_HOLDOUT_NOT_EXTERNALLY_VERIFIED",
        "CURRENT_FORWARD_RESEARCH_SESSION_NOT_APPROVED",
        "OCTOBER_27_HISTORICAL_EPOCH_MUST_NOT_BE_REBOUND",
    ))
    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id, "as_of": cutoff.isoformat(),
        "checkpoint_scope": "S5-21_TO_S5-24",
        "local_git_custody": git,
        "optional_prior_report_snapshots": snapshots,
        "engineering_result": (
            "PRIOR_20_GIT_CHECKPOINTS_VERIFIED_PENDING_FINAL_WINDOWS_TESTS"
            if verified else "PRIOR_STAGE5_GIT_CHAIN_BLOCKED"
        ),
        "stage5_prior_checkpoints_locally_verified": git["verified_prior_checkpoints"],
        "stage5_final_four_checkpoints_pending_windows_verification_and_commit": True,
        "external_gates": [
            {"gate": x, "status": "NOT_INDEPENDENTLY_VERIFIED", "approved": False}
            for x in EXTERNAL_GATES
        ],
        "external_gates_independently_verified": 0,
        "council_source_families_independently_verified": 0,
        "prospective_evidence_gates_independently_verified": 0,
        "net_of_costs_performance_verified": False,
        "research_ready": False,
        "operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "next_research_first_workstreams": [
            "ACQUIRE_ORIGIN_AUTHENTICATED_BROKER_IDENTITY_AND_INDEPENDENT_UTC_TIME",
            "PRESERVE_HISTORICAL_EPOCH_AND_APPROVE_ANY_NEW_SESSION_SEPARATELY",
            "ACQUIRE_VERSIONED_POINT_IN_TIME_COUNCIL_SOURCE_CUSTODY_FOR_13_FAMILIES",
            "ESTABLISH_EXTERNAL_PREDICTION_REGISTRATION_AND_OUTCOME_RECEIPTS",
            "FREEZE_HELDOUT_EVALUATION_BEFORE_MEASURING_OUTCOMES",
            "EVALUATE_RISK_VETOES_AND_NET_OF_COSTS_PERFORMANCE_WITH_BASELINES",
            "REQUIRE_INDEPENDENT_RISK_APPROVAL_BEFORE_ANY_PAPER_EXECUTION",
            "KEEP_LIVE_BROKER_EXECUTION_DISABLED_PENDING_SEPARATE_APPROVAL",
        ],
        "blockers": sorted(set(blockers)),
        "files_written": False, "database_accessed": False,
        "network_accessed": False, "broker_connected": False,
        "collector_started": False, "historical_epoch_rebound": False,
        "council_invoked": False, "settlement_executed": False,
        "training_authorized": False, "execution_authorized": False,
        "paper_execution_authorized": False, "live_execution_authorized": False,
        "read_only": True, "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-git", action="store_true", required=True)
    parser.add_argument("--read-only-source", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--report-index")
    parser.add_argument("--report-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise Stage5HandoverError("Future review cutoff refused.")
        result = assess_stage5_handover(
            repo=args.repo, epoch_id=args.epoch_id, as_of=args.as_of,
            report_index=args.report_index, report_root=args.report_root,
        )
    except (Stage5HandoverError, ValueError, OSError) as exc:
        print("STAGE 5 FINAL HANDOVER BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 5 S5-21..24 final research handover")
        print("Local prior Stage 5 Git anchors verified: %s/5" % (
            result["local_git_custody"]["verified_prior_batches"],
        ))
        print("Prior checkpoints locally verified: %s/20" % (
            result["stage5_prior_checkpoints_locally_verified"],
        ))
        print("Local origin-tracking HEAD matches: " + str(
            result["local_git_custody"]["local_remote_tracking_matches_head"]
        ))
        print("Saved optional report snapshots: %s" % (
            result["optional_prior_report_snapshots"]["locally_reconciled_report_count"],
        ))
        print("External gates independently verified: 0/11")
        print("Council families independently verified: 0/13")
        print("Research operational readiness: " + result["operational_readiness"])
        print("Engineering: " + result["engineering_result"])
        for blocker in result["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + result["report_sha256"])
        print("READ-ONLY | NO BROKER | NO COUNCIL | NO TRADE | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
