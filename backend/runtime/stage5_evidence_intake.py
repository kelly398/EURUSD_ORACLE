
"""S5-01..04: read-only evidence intake and Stage 4 baseline verification.

Only verifies existing local bytes, claimed metadata order and committed Git
anchors. A digest or document label is never independent provider attestation.
No collector, broker, Council, database, training or trading action is possible.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

VERSION = "EURUSD_STAGE5_EVIDENCE_INTAKE_S5_01_04_V1"
BRANCH = "feature/utc-research-m01-m08"
LAST_STAGE4_COMMIT = "761f60f"
LAST_STAGE4_FILES = (
    "backend/runtime/stage4_final_handover.py",
    "backend/runtime/test_stage4_final_handover.py",
)
GATES = (
    ("INDEPENDENT_BROKER_SERVER_IDENTITY", "BROKER_OR_TERMINAL_PROVIDER_CONFIRMED_SERVER_ID"),
    ("INDEPENDENT_BROKER_CLOCK_REFERENCE_AND_OFFSET", "BROKER_CLOCK_VERSUS_INDEPENDENT_UTC_WITH_OBSERVATION_WINDOW"),
    ("INDEPENDENT_ORIGINAL_CAPTURE_RECEIPTS_AND_SOURCE_PAYLOADS", "ORIGIN_ATTESTED_CAPTURE_RECEIPT_AND_ORIGINAL_PAYLOAD"),
    ("CURRENT_FORWARD_SESSION_WITH_APPROVED_NEW_EPOCH_PLAN", "SEPARATE_OPERATOR_APPROVAL_AND_NEW_EPOCH_PRECONDITIONS"),
    ("INDEPENDENT_MARKET_AND_MACRO_SOURCE_PROVENANCE_FOR_13_FAMILIES", "ORIGINAL_RELEASE_VERSION_ASOF_TIME_AND_SOURCE_CUSTODY"),
    ("INDEPENDENT_COUNCIL_DERIVED_INPUT_AND_MODEL_PROVENANCE", "FROZEN_FEATURE_MODEL_AND_AGENT_INPUT_LINEAGE"),
    ("OPERATOR_APPROVED_RISK_AND_RECOVERY_CONTROLS", "DOCUMENTED_OPERATOR_APPROVAL_AND_FAIL_CLOSED_LIMITS"),
    ("EXTERNALLY_TIMESTAMPED_PREDECISION_PREDICTIONS", "TRUSTED_PREDECISION_APPEND_ONLY_REGISTRATION"),
    ("INDEPENDENT_POST_HORIZON_OUTCOME_RECEIPTS", "POST_HORIZON_ORIGIN_ATTESTED_REFERENCE_QUOTE"),
    ("BROKER_CONFIRMED_EXECUTION_COSTS_IF_TRADES_EXIST", "BROKER_CONFIRMED_FILLS_AND_COSTS_OR_VERIFIED_NO_TRADE_SCOPE"),
    ("INDEPENDENT_PRE_EVALUATION_HOLDOUT_FREEZE", "EXTERNALLY_TIMESTAMPED_PRE_EVALUATION_DATA_SPLIT_FREEZE"),
)
CATEGORIES = frozenset({
    "broker_export", "provider_document", "third_party_statement",
    "operator_statement", "local_generated", "other",
})
INDEX_KEYS = frozenset({"schema_version", "epoch_id", "as_of", "records"})
RECORD_KEYS = frozenset({
    "gate", "candidate_id", "relative_path", "file_sha256",
    "source_category", "claimed_issuer", "claimed_issued_at",
    "claimed_received_at",
})
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
MAX_INDEX_BYTES = 131072
MAX_ARTIFACT_BYTES = 4194304
MAX_RECORDS = 64
UTC = timezone.utc


class Stage5IntakeError(ValueError):
    """Untrusted evidence cannot be accepted even as a local candidate."""


def _utc(value):
    if not isinstance(value, str):
        raise Stage5IntakeError("UTC timestamp text required.")
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise Stage5IntakeError("Invalid timestamp.") from None
    if at.tzinfo is None or at.utcoffset() != timedelta(0):
        raise Stage5IntakeError("Explicit zero-offset UTC timestamp required.")
    return at.astimezone(UTC)


def _hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()


def _unique(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise Stage5IntakeError("Duplicate JSON key.")
        data[key] = value
    return data


def _safe_file(path, limit):
    item = Path(path)
    if item.is_symlink() or not item.is_file():
        raise Stage5IntakeError("Existing regular evidence file required.")
    try:
        with item.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        raise Stage5IntakeError("Evidence file cannot be read.") from None
    if len(raw) > limit:
        raise Stage5IntakeError("Evidence file size limit exceeded.")
    return raw


def _inside_root(root, relative):
    if not isinstance(relative, str) or not 1 <= len(relative) <= 240:
        raise Stage5IntakeError("Invalid relative evidence path.")
    if "\\" in relative or ":" in relative or "\x00" in relative:
        raise Stage5IntakeError("Unsafe evidence path.")
    parts = relative.split("/")
    if (
        PurePosixPath(relative).is_absolute()
        or any(p in ("", ".", "..") for p in parts)
    ):
        raise Stage5IntakeError("Evidence path traversal refused.")
    base = Path(root)
    if not base.is_dir() or base.is_symlink():
        raise Stage5IntakeError("Existing non-symlink evidence root required.")
    result = base
    for part in parts:
        result = result / part
        if result.is_symlink():
            raise Stage5IntakeError("Evidence symlink refused.")
    return result


def _git(repo, *args):
    try:
        process = subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, check=False,
            timeout=15, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Stage5IntakeError("Read-only Git inspection unavailable.") from None
    if len(process.stdout) > 131072 or len(process.stderr) > 131072:
        raise Stage5IntakeError("Git response exceeds size limit.")
    return process.returncode, process.stdout.decode("utf-8", errors="replace").strip()


def inspect_stage4_baseline(repo, *, expected_anchor=LAST_STAGE4_COMMIT):
    """Read Git history; never fetch, checkout, commit or write."""
    root = Path(repo)
    if not root.is_dir() or root.is_symlink():
        raise Stage5IntakeError("Repository directory unavailable.")
    if not re.fullmatch(r"[0-9a-f]{7,40}", expected_anchor):
        raise Stage5IntakeError("Invalid expected Stage 4 anchor.")
    code, branch = _git(root, "branch", "--show-current")
    if code or branch != BRANCH:
        raise Stage5IntakeError("Unexpected Git branch.")
    code, head = _git(root, "rev-parse", "HEAD")
    if code or not GIT_SHA.fullmatch(head):
        raise Stage5IntakeError("HEAD could not be verified.")
    code, anchor = _git(root, "rev-parse", "--verify", expected_anchor + "^{commit}")
    if code or not GIT_SHA.fullmatch(anchor) or not anchor.startswith(expected_anchor):
        raise Stage5IntakeError("Stage 4 completion anchor unavailable.")
    ancestor = _git(root, "merge-base", "--is-ancestor", anchor, head)[0] == 0
    code, changed = _git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", anchor)
    exact_commit = code == 0 and sorted(changed.splitlines()) == sorted(LAST_STAGE4_FILES)
    unchanged = []
    for path in LAST_STAGE4_FILES:
        a, previous = _git(root, "rev-parse", anchor + ":" + path)
        b, newest = _git(root, "rev-parse", "HEAD:" + path)
        unchanged.append(a == 0 and b == 0 and GIT_SHA.fullmatch(previous) and previous == newest)
    code, status = _git(root, "status", "--porcelain", "--untracked-files=no", "--", *LAST_STAGE4_FILES)
    if code:
        raise Stage5IntakeError("Could not inspect Stage 4 source state.")
    code, remote = _git(root, "rev-parse", "--verify", "refs/remotes/origin/" + BRANCH)
    remote = remote if code == 0 and GIT_SHA.fullmatch(remote) else None
    return {
        "stage4_anchor": anchor, "head": head, "branch": branch,
        "anchor_is_head_ancestor": ancestor, "anchor_commit_has_exact_files": exact_commit,
        "anchored_files_unchanged_at_head": bool(all(unchanged)),
        "anchored_files_clean": not bool(status),
        "local_remote_tracking_matches_head": remote == head,
        "baseline_locally_consistent": bool(
            ancestor and exact_commit and all(unchanged) and not status and remote == head
        ),
        "origin_contacted": False, "git_mutated": False,
    }


def inspect_candidate_index(index, *, root, epoch_id, as_of):
    """Existing optional index only; never creates records or independently attests."""
    counts = {name: 0 for name, _ in GATES}
    if index is None:
        if root is not None:
            raise Stage5IntakeError("Evidence root without index refused.")
        return {
            "status": "NOT_SUPPLIED", "index_sha256": None,
            "candidate_count": 0, "file_bytes_locally_checked": 0,
            "counts": counts, "independently_verified": 0,
        }
    if root is None:
        raise Stage5IntakeError("Evidence root required with index.")
    raw = _safe_file(index, MAX_INDEX_BYTES)
    try:
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                Stage5IntakeError("Nonfinite JSON evidence value.")
            ),
        )
    except Stage5IntakeError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise Stage5IntakeError("Evidence index JSON invalid.") from None
    if (
        not isinstance(data, dict) or set(data) != INDEX_KEYS
        or type(data["schema_version"]) is not int or data["schema_version"] != 1
        or data["epoch_id"] != epoch_id or _utc(data["as_of"]) != as_of
    ):
        raise Stage5IntakeError("Evidence index version or review scope mismatch.")
    records = data["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise Stage5IntakeError("Evidence record limit exceeded.")
    seen = set()
    total = 0
    for record in records:
        if not isinstance(record, dict) or set(record) != RECORD_KEYS:
            raise Stage5IntakeError("Evidence record contract mismatch.")
        gate, identity = record["gate"], record["candidate_id"]
        if not isinstance(gate, str) or gate not in counts:
            raise Stage5IntakeError("Unknown evidence gate.")
        if not isinstance(identity, str) or not LABEL.fullmatch(identity) or identity in seen:
            raise Stage5IntakeError("Invalid or duplicate candidate ID.")
        if (
            not isinstance(record["source_category"], str)
            or record["source_category"] not in CATEGORIES
            or not isinstance(record["claimed_issuer"], str)
            or not LABEL.fullmatch(record["claimed_issuer"])
        ):
            raise Stage5IntakeError("Invalid claimed evidence source.")
        if not isinstance(record["file_sha256"], str) or not HEX64.fullmatch(record["file_sha256"]):
            raise Stage5IntakeError("Invalid candidate SHA-256.")
        if not _utc(record["claimed_issued_at"]) <= _utc(record["claimed_received_at"]) <= as_of:
            raise Stage5IntakeError("Claimed source chronology invalid.")
        file = _inside_root(root, record["relative_path"])
        content = _safe_file(file, MAX_ARTIFACT_BYTES)
        if sha256(content).hexdigest() != record["file_sha256"]:
            raise Stage5IntakeError("Candidate file SHA-256 mismatch.")
        total += len(content)
        seen.add(identity)
        counts[gate] += 1
    return {
        "status": "LOCAL_CANDIDATES_HASH_LINKED_UNATTESTED",
        "index_sha256": sha256(raw).hexdigest(),
        "candidate_count": len(records),
        "file_bytes_locally_checked": total,
        "counts": counts, "independently_verified": 0,
    }


def assess_stage5_intake(*, repo, epoch_id, as_of, candidate_index=None,
                         evidence_root=None, expected_anchor=LAST_STAGE4_COMMIT):
    at = _utc(as_of)
    if not isinstance(epoch_id, str) or not HEX64.fullmatch(epoch_id):
        raise Stage5IntakeError("Invalid epoch ID.")
    baseline = inspect_stage4_baseline(repo, expected_anchor=expected_anchor)
    candidate = inspect_candidate_index(
        candidate_index, root=evidence_root, epoch_id=epoch_id, as_of=at
    )
    blockers = []
    if not baseline["baseline_locally_consistent"]:
        blockers.append("STAGE4_LOCAL_GIT_BASELINE_NOT_CONSISTENT")
    if candidate["status"] == "NOT_SUPPLIED":
        blockers.append("NO_INTAKE_CANDIDATE_INDEX_SUPPLIED")
    evidence_gates = []
    for name, criterion in GATES:
        count = candidate["counts"][name]
        evidence_gates.append({
            "gate": name, "acceptance_standard": criterion,
            "local_candidate_count": count,
            "status": "LOCAL_CANDIDATE_UNATTESTED" if count else "NOT_SUBMITTED",
            "independently_verified": False,
            "approved_for_research_admission": False,
        })
        blockers.append("EXTERNAL_GATE_UNVERIFIED:" + name)
    result = {
        "assessment_version": VERSION, "epoch_id": epoch_id, "as_of": at.isoformat(),
        "stage4_baseline": baseline, "candidate_index": candidate,
        "checkpoint_scope": "S5-01_TO_S5-04",
        "engineering_result": (
            "LOCAL_INTAKE_PREFLIGHT_VALIDATED_EXTERNAL_EVIDENCE_PENDING"
            if baseline["baseline_locally_consistent"]
            else "LOCAL_BASELINE_BLOCKED"
        ),
        "gates": evidence_gates, "external_gates_independently_verified": 0,
        "council_input_families_independently_verified_by_this_review": 0,
        "operator_next_actions": [
            "PRESERVE_EXISTING_STAGE4_EPOCH_POLICY_AND_CAPTURE_RECORDS",
            "LOCATE_GENUINE_ORIGINAL_BROKER_IDENTITY_AND_TIME_SOURCE_DOCUMENTS",
            "CONTACT_BROKER_OR_PROVIDER_FOR_INDEPENDENT_SOURCE_CONFIRMATION",
            "REGISTER_ONLY_EXISTING_CANDIDATE_FILES_WITH_TRUE_ACQUISITION_METADATA",
            "DO_NOT_BACKDATE_PREDICTIONS_OR_RESTART_COLLECTORS",
        ],
        "blockers": sorted(set(blockers)),
        "database_accessed": False, "network_accessed": False,
        "broker_connected": False, "files_written": False, "collector_started": False,
        "council_invoked": False, "predictions_registered": False,
        "training_authorized": False, "execution_authorized": False,
        "real_forward_admission": False, "read_only": True, "final_action": "HOLD",
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
    parser.add_argument("--candidate-index")
    parser.add_argument("--evidence-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise Stage5IntakeError("Future intake cutoff refused.")
        result = assess_stage5_intake(
            repo=args.repo, epoch_id=args.epoch_id, as_of=args.as_of,
            candidate_index=args.candidate_index, evidence_root=args.evidence_root,
        )
    except (Stage5IntakeError, OSError, ValueError) as exc:
        print("STAGE 5 EVIDENCE INTAKE BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    else:
        base = result["stage4_baseline"]
        print("EURUSD ORACLE | Stage 5 S5-01..04 evidence intake")
        print("Stage 4 final anchor verified: " + str(base["baseline_locally_consistent"]))
        print("Local remote-tracking HEAD matches: " + str(base["local_remote_tracking_matches_head"]))
        print("Existing candidate files hash-matched: " + str(result["candidate_index"]["candidate_count"]))
        print("External evidence independently verified: 0/11")
        for gate in result["gates"]:
            print("  GATE " + gate["gate"] + " | " + gate["status"])
        print("Engineering: " + result["engineering_result"])
        print("SHA-256: " + result["report_sha256"])
        print("READ-ONLY | NO BROKER | NO RESTART | NO COUNCIL | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
