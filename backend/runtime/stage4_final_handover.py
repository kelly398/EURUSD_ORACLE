
"""Stage 4 S4-21..24: read-only engineering handover and evidence gap audit.

Validates local Git checkpoint objects and *optional existing* Stage 4 JSON
reports. Never contacts brokers, executes Council agents, writes, or admits
research evidence as externally attested. All operational decisions remain HOLD.
"""

import argparse
import os
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

VERSION = "EURUSD_STAGE4_FINAL_HANDOVER_S4_21_24_V1"
BRANCH = "feature/utc-research-m01-m08"
SHA = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
MAX_REPORT = 262144
UTC = timezone.utc

# Already-pushed checkpoints only. The final batch is deliberately excluded
# until the user verifies its Windows tests and commits it separately.
BATCHES = (
    ("s4_01_04", "00f2618", "stage4_collector_forensics"),
    ("s4_05_08", "9550bc7", "stage4_broker_clock_evidence"),
    ("s4_09_12", "7ef1d0e", "stage4_forward_receipt_recovery"),
    ("s4_13_16", "375a2fd", "stage4_council_input_provenance"),
    ("s4_17_20", "1e27212", "stage4_prospective_evidence_custody"),
)

REPORT_VERSIONS = {
    "s4_05_08": "EURUSD_STAGE4_BROKER_CLOCK_EVIDENCE_S4_05_08_V1",
    "s4_09_12": "EURUSD_STAGE4_FORWARD_RECEIPT_RECOVERY_S4_09_12_V1",
    "s4_13_16": "EURUSD_STAGE4_COUNCIL_INPUT_PROVENANCE_S4_13_16_V1",
    "s4_17_20": "EURUSD_STAGE4_PROSPECTIVE_EVIDENCE_CUSTODY_S4_17_20_V1",
}

# These are evidence requirements, not asserted facts of absence everywhere.
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


class Stage4HandoverError(ValueError):
    """Invalid supplied cutoff, evidence, or repository contract."""


def _hash(obj):
    return sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _utc(value):
    if not isinstance(value, str):
        raise Stage4HandoverError("UTC timestamp text required.")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise Stage4HandoverError("Invalid UTC timestamp.") from None
    if stamp.tzinfo is None or stamp.utcoffset() != timezone.utc.utcoffset(None):
        raise Stage4HandoverError("Explicit UTC offset zero required.")
    return stamp.astimezone(UTC)


def _unique(pairs):
    obj = {}
    for key, val in pairs:
        if key in obj:
            raise Stage4HandoverError("Duplicate report JSON key.")
        obj[key] = val
    return obj


def _report_file(path):
    f = Path(path)
    if f.is_symlink() or not f.is_file():
        raise Stage4HandoverError("Existing regular report file required.")
    try:
        with f.open("rb") as stream:
            raw = stream.read(MAX_REPORT + 1)
    except OSError:
        raise Stage4HandoverError("Report file unavailable.") from None
    if len(raw) > MAX_REPORT:
        raise Stage4HandoverError("Report exceeds bounded size.")
    try:
        obj = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                Stage4HandoverError("Nonfinite JSON refused.")
            ),
        )
    except Stage4HandoverError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise Stage4HandoverError("Report JSON invalid.") from None
    if not isinstance(obj, dict):
        raise Stage4HandoverError("Report must be an object.")
    return obj, sha256(raw).hexdigest()


def inspect_optional_reports(paths, *, epoch_id, as_of):
    """Hash and scope-check existing self-generated reports only."""
    if not isinstance(paths, dict) or set(paths) - {x[0] for x in BATCHES}:
        raise Stage4HandoverError("Unknown Stage 4 report identifier.")
    out = []
    for name, _, _ in BATCHES:
        path = paths.get(name)
        if path is None:
            out.append({"batch": name, "status": "NOT_SUPPLIED",
                        "file_sha256": None, "report_sha256": None,
                        "independently_attested": False})
            continue
        data, file_sha = _report_file(path)
        digest = data.get("report_sha256")
        if not isinstance(digest, str) or not SHA.fullmatch(digest) or digest != _hash({
            key: val for key, val in data.items() if key != "report_sha256"
        }):
            raise Stage4HandoverError("Stage 4 report SHA-256 mismatch.")
        if data.get("final_action") != "HOLD" or data.get("read_only") is not True:
            raise Stage4HandoverError("Stage 4 report is not read-only HOLD.")
        if name in REPORT_VERSIONS and data.get("assessment_version") != REPORT_VERSIONS[name]:
            raise Stage4HandoverError("Stage 4 report version mismatch.")
        if name != "s4_13_16" and data.get("epoch_id") != epoch_id:
            raise Stage4HandoverError("Stage 4 report epoch mismatch.")
        if "as_of" in data and _utc(data["as_of"]) > as_of:
            raise Stage4HandoverError("Stage 4 report cutoff later than handover.")
        for field in (
            "collector_started", "collector_restart_authorized", "broker_connected",
            "council_invoked", "execution_authorized", "training_authorized",
            "real_forward_council_admission", "database_modified", "files_modified",
        ):
            if field in data and data[field] is not False:
                raise Stage4HandoverError("Stage 4 report contradicts HOLD action flags.")
        # Key output contracts, without elevating self-claims to external proof.
        if name == "s4_13_16" and data.get("independently_verified_family_count") != 0:
            raise Stage4HandoverError("Council independent provenance claim refused.")
        if name == "s4_17_20" and data.get("external_gates_independently_verified") != 0:
            raise Stage4HandoverError("Prospective external attestation claim refused.")
        out.append({"batch": name, "status": "LOCAL_REPORT_HASH_LINKED_UNATTESTED",
                    "file_sha256": file_sha, "report_sha256": digest,
                    "independently_attested": False})
    return out


def _git(repo, *args):
    try:
        cp = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True,
            check=False, timeout=15, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Stage4HandoverError("Local Git command unavailable.") from None
    if len(cp.stdout) > 131072 or len(cp.stderr) > 131072:
        raise Stage4HandoverError("Git output exceeds inspection bound.")
    return cp.returncode, cp.stdout.decode("utf-8", errors="replace").strip()


def inspect_git_anchors(repo):
    """No fetch, checkout or Git writes; verifies historical commits in place."""
    root = Path(repo)
    if not root.is_dir() or root.is_symlink():
        raise Stage4HandoverError("Existing repository directory required.")
    rc, branch = _git(root, "branch", "--show-current")
    if rc or branch != BRANCH:
        raise Stage4HandoverError("Unexpected local branch.")
    rc, head = _git(root, "rev-parse", "HEAD")
    if rc or not GIT_SHA.fullmatch(head):
        raise Stage4HandoverError("HEAD revision unavailable.")
    rc, remote = _git(root, "rev-parse", "--verify", "refs/remotes/origin/" + BRANCH)
    if rc or not GIT_SHA.fullmatch(remote):
        remote = None

    results = []
    previous = None
    all_paths = []
    for name, short, stem in BATCHES:
        files = [
            f"backend/runtime/{stem}.py",
            f"backend/runtime/test_{stem}.py",
        ]
        all_paths.extend(files)
        rc, commit = _git(root, "rev-parse", "--verify", short + "^{commit}")
        present = rc == 0 and bool(GIT_SHA.fullmatch(commit)) and commit.startswith(short)
        ancestor = False
        predecessor = False
        files_match = False
        blobs_match = False
        if present:
            ancestor = _git(root, "merge-base", "--is-ancestor", commit, head)[0] == 0
            predecessor = previous is None or _git(
                root, "merge-base", "--is-ancestor", previous, commit
            )[0] == 0
            rc, changed = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", commit)
            files_match = rc == 0 and sorted(changed.splitlines()) == sorted(files)
            hashes = []
            for path in files:
                rc1, old = _git(root, "rev-parse", commit + ":" + path)
                rc2, newest = _git(root, "rev-parse", "HEAD:" + path)
                hashes.append(rc1 == 0 and rc2 == 0 and bool(GIT_SHA.fullmatch(old)) and old == newest)
            blobs_match = all(hashes)
            previous = commit
        results.append({
            "batch": name, "commit": short, "commit_present": present,
            "ancestor_of_head": ancestor, "in_expected_order": predecessor,
            "exact_two_files_in_commit": files_match,
            "files_unchanged_at_head": blobs_match,
            "local_git_anchor_verified": all((present, ancestor, predecessor, files_match, blobs_match)),
        })
    rc, changed = _git(root, "status", "--porcelain", "--", *all_paths)
    if rc:
        raise Stage4HandoverError("Cannot inspect existing Stage 4 working files.")
    return {
        "branch": branch, "head": head, "local_remote_tracking_head": remote,
        "head_equals_local_remote_tracking": remote is not None and head == remote,
        "previous_checkpoint_files_clean": not bool(changed),
        "verified_prior_batches": sum(x["local_git_anchor_verified"] for x in results),
        "prior_batches_total": len(BATCHES),
        "anchors": results,
        "git_network_accessed": False, "history_mutated": False,
    }


def assess_stage4_handover(*, repo, epoch_id, as_of, report_paths=None):
    at = _utc(as_of)
    if not isinstance(epoch_id, str) or not SHA.fullmatch(epoch_id):
        raise Stage4HandoverError("Invalid epoch SHA-256.")
    git = inspect_git_anchors(repo)
    reports = inspect_optional_reports(report_paths or {}, epoch_id=epoch_id, as_of=at)
    blockers = []
    if git["verified_prior_batches"] != git["prior_batches_total"]:
        blockers.append("PRIOR_STAGE4_GIT_ANCHOR_NOT_VERIFIED")
    if not git["previous_checkpoint_files_clean"]:
        blockers.append("PRIOR_STAGE4_SOURCE_HAS_LOCAL_CHANGES")
    if not git["head_equals_local_remote_tracking"]:
        blockers.append("HEAD_NOT_EQUAL_TO_LOCAL_REMOTE_TRACKING_REF")
    if any(x["status"] == "NOT_SUPPLIED" for x in reports):
        blockers.append("NOT_ALL_PRIOR_STAGE4_JSON_REPORTS_SUPPLIED")
    blockers += ["EXTERNAL_EVIDENCE_REQUIRED:" + gate for gate in EXTERNAL_GATES]
    source_ready = (
        git["verified_prior_batches"] == 5
        and git["previous_checkpoint_files_clean"]
        and git["head_equals_local_remote_tracking"]
    )
    result = {
        "assessment_version": VERSION, "epoch_id": epoch_id, "as_of": at.isoformat(),
        "checkpoint_scope": "S4-21_THROUGH_S4-24_ENGINEERING_HANDOVER",
        "prior_committed_checkpoints_checked": 20 if source_ready else None,
        "git_verification": git, "optional_prior_stage4_reports": reports,
        "locally_linked_reports": sum(x["status"] != "NOT_SUPPLIED" for x in reports),
        "engineering_status": (
            "PRIOR_20_GIT_CHECKPOINTS_VERIFIED_PENDING_FINAL_WINDOWS_TESTS"
            if source_ready else "LOCAL_CHECKPOINT_RECONCILIATION_BLOCKED"
        ),
        "operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "independently_verified_external_gates": 0,
        "external_evidence_requirements": list(EXTERNAL_GATES),
        "blockers": sorted(set(blockers)),
        "handover_actions": [
            "COMPLETE_FINAL_STAGE4_WINDOWS_REGRESSION_AND_REAL_READONLY_INSPECTION",
            "REVIEW_AND_COMMIT_FINAL_TWO_FILES_ONLY_AFTER_TEST_VERIFICATION",
            "PRESERVE_HISTORICAL_EPOCH_AND_ORIGINAL_CLOCK_POLICY_UNCHANGED",
            "OBTAIN_INDEPENDENT_BROKER_CLOCK_RECEIPTS_AND_SOURCE_EVIDENCE",
            "ATTEST_COUNCIL_INPUTS_AND_PROSPECTIVE_REGISTRATIONS_BEFORE_ADMISSION",
            "REQUIRE_SEPARATE_OPERATOR_APPROVAL_FOR_ANY_NEW_FORWARD_EPOCH",
        ],
        "read_only": True, "database_accessed": False, "broker_connected": False,
        "network_accessed": False, "files_modified": False, "git_mutated": False,
        "collector_started": False, "council_invoked": False,
        "predictions_registered": False, "training_authorized": False,
        "execution_authorized": False, "real_forward_council_admission": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _hash(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-git", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--report", action="append", default=[], metavar="BATCH=EXISTING_JSON")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        at = _utc(args.as_of)
        if at > datetime.now(UTC):
            raise Stage4HandoverError("Future inspection cutoff refused.")
        paths = {}
        for value in args.report:
            if "=" not in value:
                raise Stage4HandoverError("Report argument must be BATCH=EXISTING_JSON.")
            key, path = value.split("=", 1)
            if key in paths or key not in {x[0] for x in BATCHES} or not path:
                raise Stage4HandoverError("Invalid/duplicate report assignment.")
            paths[key] = path
        report = assess_stage4_handover(
            repo=args.repo, epoch_id=args.epoch_id, as_of=at.isoformat(),
            report_paths=paths,
        )
    except (Stage4HandoverError, OSError, ValueError) as exc:
        print("STAGE 4 FINAL HANDOVER BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        git = report["git_verification"]
        print("EURUSD ORACLE | Stage 4 S4-21..24 final engineering handover")
        print("Prior Git batches verified: %s/5" % git["verified_prior_batches"])
        print("Prior code clean: " + str(git["previous_checkpoint_files_clean"]))
        print("Local remote-tracking HEAD matches: " + str(git["head_equals_local_remote_tracking"]))
        print("Prior saved reports linked: %s/5" % report["locally_linked_reports"])
        print("Engineering status: " + report["engineering_status"])
        print("External evidence independently verified: 0/%s" % len(EXTERNAL_GATES))
        print("Operational readiness: BLOCKED_EXTERNAL_EVIDENCE")
        for reason in report["blockers"]:
            print("  BLOCKER " + reason)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO RESTART | NO COUNCIL | NO TRAINING | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
