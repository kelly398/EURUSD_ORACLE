
"""P7-01..04: Stage 6 postcommit audit and research evidence baseline.

This is an OFFLINE, READ-ONLY inspection of local Git, tracked file names,
original clock-policy bytes and optional already-existing evidence files.
It never imports an agent or broker, starts a collector, records new evidence,
invokes the Council or promotes internally claimed provenance to external proof.
The local origin-tracking ref is NOT a live verification of GitHub custody.
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

from backend.runtime.stage6_council_context_readiness import (
    CouncilContextError, FAMILIES, inspect_static_council,
)
from backend.runtime.stage6_final_research_handover import (
    FinalHandoverError, _static_gates, inspect_original_policy,
)

VERSION = "EURUSD_STAGE7_POSTCOMMIT_EVIDENCE_BASELINE_P7_01_04_V1"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
BRANCH = "feature/utc-research-m01-m08"
STAGE5_PREFIX = "fb1c643"
STAGE6_FINAL_PREFIX = "1408d5e"
BATCHES = (
    ("P6-01..04", "9fc5e1d", "stage6_research_governance"),
    ("P6-05..08", "3ad7e04", "stage6_source_evidence_envelopes"),
    ("P6-09..12", "e403da8", "stage6_council_context_readiness"),
    ("P6-13..16", "ee5a43d", "stage6_prospective_ledger_readiness"),
    ("P6-17..20", "7a4bcfb", "stage6_research_evaluation_readiness"),
    ("P6-21..24", "1408d5e", "stage6_final_research_handover"),
)
ARTIFACT_TYPES = (
    "BROKER_METADATA", "CLOCK_REFERENCE", "CAPTURE_RECEIPT",
    "MARKET_TICK", "M5_CANDLE", "COT_RELEASE", "ECB_RELEASE",
    "FED_RELEASE", "ECONOMIC_EVENT", "CROSS_ASSET",
    "MODEL_LINEAGE", "OPERATOR_POLICY", "OTHER_PROVIDER_ARTIFACT",
)
CATEGORIES = (
    "council_agents", "market_data", "model_assets", "learning",
    "api", "runtime", "tests", "dashboard_ui", "other",
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
GIT_HEX = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
INDEX_KEYS = {"schema_version", "epoch_id", "as_of", "entries"}
ENTRY_KEYS = {
    "record_id", "artifact_type", "council_family", "observer_id",
    "issuer_claim", "claimed_receipt_id", "first_available_at", "observed_at",
    "claimed_receipt_at", "relative_path", "file_sha256",
    "review_permission", "supersedes",
}
MAX_GIT_BYTES = 2097152
MAX_FILES = 20000
MAX_INDEX_BYTES = 131072
MAX_ARTIFACT_BYTES = 1048576
MAX_TOTAL_BYTES = 8388608
MAX_RECORDS = 96
UTC = timezone.utc


class Stage7BaselineError(ValueError):
    """Local source or evidence is structurally invalid or ambiguous."""


def _utc(value):
    if not isinstance(value, str):
        raise Stage7BaselineError("Explicit UTC timestamp required.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise Stage7BaselineError("Invalid UTC timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise Stage7BaselineError("Explicit zero-offset UTC required.")
    return parsed.astimezone(UTC)


def _digest(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _no_duplicate_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Stage7BaselineError("Duplicate JSON field refused.")
        result[key] = value
    return result


def _json(raw):
    try:
        result = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicate_fields,
            parse_constant=lambda _: (_ for _ in ()).throw(
                Stage7BaselineError("Nonfinite JSON refused.")
            ),
        )
    except Stage7BaselineError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise Stage7BaselineError("Invalid UTF-8 JSON object.") from None
    if not isinstance(result, dict):
        raise Stage7BaselineError("JSON object required.")
    return result


def _no_symlink_ancestors(path):
    candidate = Path(path).absolute()
    for part in (candidate, *candidate.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise Stage7BaselineError("Symlink or junction in inspected path.")
    return candidate


def _read(path, limit):
    file = _no_symlink_ancestors(path)
    if not file.is_file():
        raise Stage7BaselineError("Existing regular evidence file required.")
    try:
        with file.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise Stage7BaselineError("Evidence file cannot be read.") from None
    if len(raw) > limit:
        raise Stage7BaselineError("Evidence file exceeds bounded size.")
    return raw


def _id(value, field):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise Stage7BaselineError("Invalid identifier: " + field)
    return value


def _sha(value, field):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise Stage7BaselineError("Invalid SHA-256: " + field)
    return value


def _safe_child(root, relative):
    if (not isinstance(relative, str) or not 1 <= len(relative) <= 240
            or "\\" in relative or ":" in relative or "\x00" in relative
            or PurePosixPath(relative).is_absolute()
            or any(segment in ("", ".", "..") for segment in relative.split("/"))):
        raise Stage7BaselineError("Unsafe relative evidence path.")
    base = _no_symlink_ancestors(root)
    if not base.is_dir():
        raise Stage7BaselineError("Existing evidence directory required.")
    target = _no_symlink_ancestors(base.joinpath(*relative.split("/")))
    if not target.resolve().is_relative_to(base.resolve()):
        raise Stage7BaselineError("Evidence path escapes root.")
    return target


def _git(root, *args, accepted=(0,), limit=MAX_GIT_BYTES):
    """Run only local fixed read-only Git subcommands; no shell or fetch."""
    environment = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, check=False, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Stage7BaselineError("Local read-only Git query unavailable.") from None
    if result.returncode not in accepted or len(result.stdout) > limit:
        raise Stage7BaselineError("Local read-only Git query failed.")
    return result.returncode, result.stdout


def _line(root, *args):
    try:
        return _git(root, *args)[1].decode("utf-8").strip()
    except UnicodeError:
        raise Stage7BaselineError("Invalid Git metadata encoding.") from None


def _source_paths(stem):
    return (
        "backend/runtime/" + stem + ".py",
        "backend/runtime/test_" + stem + ".py",
    )


def inspect_six_stage6_commits(repo, *, batches=BATCHES, stage5_prefix=STAGE5_PREFIX,
                               branch=BRANCH, final_prefix=STAGE6_FINAL_PREFIX):
    """Review immutable Stage 6 prefix even after future Stage 7 commits."""
    root = _no_symlink_ancestors(repo)
    if not root.is_dir():
        raise Stage7BaselineError("Repository root does not exist.")
    reported = _line(root, "rev-parse", "--show-toplevel")
    if Path(reported).resolve() != root.resolve():
        raise Stage7BaselineError("Run at the Git repository root.")
    if _line(root, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
        raise Stage7BaselineError("Unexpected research branch.")
    head = _line(root, "rev-parse", "HEAD")
    tracking = _line(root, "rev-parse", "refs/remotes/origin/" + branch)
    if head != tracking or not GIT_HEX.fullmatch(head):
        raise Stage7BaselineError("Local HEAD and origin-tracking ref differ.")
    if _git(root, "diff", "--quiet", "HEAD", "--", accepted=(0, 1))[0]:
        raise Stage7BaselineError("Tracked or staged changes detected.")

    # The final Stage 6 commit is a historical anchor, not necessarily HEAD.
    anchor = _line(root, "rev-parse", "--verify", final_prefix + "^{commit}")
    if not anchor.startswith(final_prefix) or not GIT_HEX.fullmatch(anchor):
        raise Stage7BaselineError("Final Stage 6 anchor cannot be resolved.")
    head_history = _line(
        root, "rev-list", "--first-parent", "--max-count=256", "HEAD",
    ).splitlines()
    if anchor not in head_history:
        raise Stage7BaselineError("Stage 6 final commit not on HEAD first-parent history.")
    chain = _line(root, "rev-list", "--first-parent", "--max-count=7", anchor).splitlines()
    if len(chain) != 7:
        raise Stage7BaselineError("Six-commit Stage 6 first-parent chain incomplete.")
    for actual, (_, expected, _) in zip(chain[:6], reversed(batches)):
        if not actual.startswith(expected):
            raise Stage7BaselineError("Stage 6 first-parent commit order mismatch.")
    if not chain[6].startswith(stage5_prefix):
        raise Stage7BaselineError("Stage 5 boundary commit mismatch.")

    summary = []
    governance = None
    for label, prefix, stem in batches:
        commit = next(c for c in chain[:6] if c.startswith(prefix))
        if len(_line(root, "rev-list", "--parents", "-n", "1", commit).split()) != 2:
            raise Stage7BaselineError("Stage 6 commit must not be a merge.")
        changed = _line(root, "diff-tree", "--no-commit-id", "-r", "--name-status",
                        "--no-renames", commit).splitlines()
        expected_paths = _source_paths(stem)
        entries = [line.split("\t") for line in changed]
        if (len(entries) != 2 or any(len(x) != 2 or x[0] != "A" for x in entries)
                or {x[1] for x in entries} != set(expected_paths)):
            raise Stage7BaselineError("Stage 6 commit deviated from exact two-file scope.")
        files = []
        for relative in expected_paths:
            committed = _line(root, "rev-parse", commit + ":" + relative)
            current = _line(root, "rev-parse", "HEAD:" + relative)
            if committed != current:
                raise Stage7BaselineError("Committed Stage 6 source altered at HEAD.")
            content = _git(root, "show", commit + ":" + relative, limit=MAX_GIT_BYTES)[1]
            local = _read(root / relative, MAX_GIT_BYTES)
            if not content or not local:
                raise Stage7BaselineError("Empty Stage 6 source/test file.")
            try:
                ast.parse(content.decode("utf-8-sig"))
                ast.parse(local.decode("utf-8-sig"))
            except (UnicodeError, SyntaxError, RecursionError):
                raise Stage7BaselineError("Committed Stage 6 Python AST invalid.") from None
            # Git diff HEAD checks worktree equivalence including Windows CRLF.
            files.append({"path": relative, "git_blob_id": committed,
                          "git_content_sha256": sha256(content).hexdigest(),
                          "python_ast_parsed": True})
            if stem == "stage6_research_governance" and relative == expected_paths[0]:
                governance = content
        summary.append({"checkpoints": label, "commit": commit,
                        "change_scope": "EXACT_SOURCE_TEST_ADDS",
                        "files": files, "unchanged_at_head": True})
    try:
        gates = _static_gates(governance)
    except (FinalHandoverError, ValueError, TypeError):
        raise Stage7BaselineError("Historical 11-gate contract invalid.") from None
    return {
        "status": "SIX_STAGE6_ANCHORS_LOCALLY_VERIFIED",
        "research_branch": branch, "head": head, "local_origin_tracking_head": tracking,
        "local_tracking_agrees": True, "github_contacted": False,
        "stage6_final_commit": anchor, "stage5_boundary_commit": chain[6],
        "stage6_batches": summary, "batch_count": 6, "engineering_checkpoints": 24,
        "tracked_clean": True, "untracked_stage7_files_out_of_scope": True,
        "gate_contract": [{"id": g[0], "name": g[1], "review_role": g[2],
                           "independent_proof_required": g[3]} for g in gates],
    }


def _category(path):
    lowered = path.lower()
    if (lowered.startswith("tests/") or lowered.endswith("_test.py")
            or Path(lowered).name.startswith("test_") or "/tests/" in lowered):
        return "tests"
    if lowered.startswith("backend/agents/"):
        return "council_agents"
    if lowered.startswith(("backend/data_engine/", "backend/data/", "data/")):
        return "market_data"
    if lowered.startswith(("backend/models/", "backend/model/", "models/", "model_artifacts/")):
        return "model_assets"
    if lowered.startswith("backend/learning/"):
        return "learning"
    if lowered.startswith(("backend/api/", "api/", "backend/routes/")):
        return "api"
    if lowered.startswith("backend/runtime/"):
        return "runtime"
    if lowered.startswith(("dashboard/", "frontend/", "ui/", "web/", "client/", "backend/dashboard/")):
        return "dashboard_ui"
    return "other"


def inspect_capability_inventory(repo):
    """Git-tracked filenames only; presence is never execution or approval."""
    root = _no_symlink_ancestors(repo)
    paths_bytes = _git(root, "ls-files", "--cached", "-z", limit=MAX_GIT_BYTES)[1]
    try:
        paths = [p.decode("utf-8") for p in paths_bytes.split(b"\x00") if p]
    except UnicodeError:
        raise Stage7BaselineError("Unrecognized tracked filename encoding.") from None
    if len(paths) > MAX_FILES or len(set(paths)) != len(paths):
        raise Stage7BaselineError("Tracked file inventory exceeds limit or contains duplicate.")
    grouped = {key: [] for key in CATEGORIES}
    for path in paths:
        if (not path or path.startswith("/") or "\\" in path or "\x00" in path
                or any(x in ("", ".", "..") for x in path.split("/"))):
            raise Stage7BaselineError("Unsafe tracked filename.")
        grouped[_category(path)].append(path)
    results = {}
    for key, matches in grouped.items():
        matches.sort()
        results[key] = {
            "tracked_file_count": len(matches),
            "tracked_python_count": sum(p.endswith(".py") for p in matches),
            "tracked_paths_sha256": _digest(matches),
            "sample_paths": matches[:8],
            "source_present": bool(matches),
            "implementation_status": "TRACKED_FILES_ONLY_UNREVIEWED" if matches else "NOT_FOUND_IN_TRACKED_INDEX",
            "test_status": "TEST_PATHS_PRESENT_NOT_EXECUTED" if key == "tests" and matches else "NOT_EVALUATED",
            "active_service_connected": "NOT_INSPECTED",
            "operator_approved": False,
            "independently_proven": False,
        }
    return {
        "status": "TRACKED_FILE_INVENTORY_ONLY",
        "tracked_files_total": len(paths),
        "tracked_paths_sha256": _digest(sorted(paths)),
        "inventory_categories": results,
        "untracked_files_excluded": True,
        "modules_imported_or_executed": False,
        "deployment_or_connectivity_verified": False,
    }


def _read_custody_index(index_file, root, *, epoch_id, assessment_cutoff):
    if root is None:
        raise Stage7BaselineError("Custody evidence root required with index.")
    raw = _read(index_file, MAX_INDEX_BYTES)
    index = _json(raw)
    if (set(index) != INDEX_KEYS or type(index.get("schema_version")) is not int
            or index["schema_version"] != 1 or index["epoch_id"] != epoch_id):
        raise Stage7BaselineError("Custody index schema/epoch invalid.")
    index_cutoff = _utc(index["as_of"])
    if index_cutoff > assessment_cutoff:
        raise Stage7BaselineError("Custody index from future of assessment.")
    entries = index["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_RECORDS:
        raise Stage7BaselineError("Custody index record count invalid.")
    seen_ids = {}
    seen_paths = set()
    seen_receipts = set()
    used_supersessions = set()
    previous_receipt = None
    byte_count = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise Stage7BaselineError("Custody record strict schema invalid.")
        rid = _id(entry["record_id"], "record_id")
        kind = entry["artifact_type"]
        family = entry["council_family"]
        if rid in seen_ids or kind not in ARTIFACT_TYPES or (family is not None and family not in FAMILIES):
            raise Stage7BaselineError("Duplicate custody ID or unknown type/family.")
        observer = _id(entry["observer_id"], "observer_id")
        issuer = _id(entry["issuer_claim"], "issuer_claim")
        claimed_receipt_id = _id(entry["claimed_receipt_id"], "claimed_receipt_id")
        receipt_key = (issuer, claimed_receipt_id)
        if receipt_key in seen_receipts:
            raise Stage7BaselineError("Claimed receipt identifier reused.")
        seen_receipts.add(receipt_key)
        if entry["review_permission"] != "REVIEW_ONLY":
            raise Stage7BaselineError("Review-only permission required; no approval flags.")
        availability = _utc(entry["first_available_at"])
        observed = _utc(entry["observed_at"])
        receipt = _utc(entry["claimed_receipt_at"])
        if not availability <= observed <= receipt <= index_cutoff:
            raise Stage7BaselineError("Custody availability/receipt chronology invalid.")
        if previous_receipt is not None and receipt < previous_receipt:
            raise Stage7BaselineError("Custody entries out of receipt order.")
        previous_receipt = receipt
        path = entry["relative_path"]
        if path in seen_paths:
            raise Stage7BaselineError("Duplicate custody artifact path.")
        seen_paths.add(path)
        digest = _sha(entry["file_sha256"], "file_sha256")
        artifact = _read(_safe_child(root, path), MAX_ARTIFACT_BYTES)
        byte_count += len(artifact)
        if byte_count > MAX_TOTAL_BYTES:
            raise Stage7BaselineError("Cumulative custody artifact limit exceeded.")
        if sha256(artifact).hexdigest() != digest:
            raise Stage7BaselineError("Custody artifact SHA-256 mismatch.")
        parent = entry["supersedes"]
        if parent is not None:
            _id(parent, "supersedes")
            if parent not in seen_ids or parent in used_supersessions:
                raise Stage7BaselineError("Unknown, future or forked custody revision.")
            prior = seen_ids[parent]
            if (prior["artifact_type"] != kind or prior["issuer_claim"] != issuer
                    or prior["council_family"] != family or prior["file_sha256"] == digest):
                raise Stage7BaselineError("Ambiguous or incompatible custody revision.")
            used_supersessions.add(parent)
        seen_ids[rid] = entry
    return {
        "raw_index_sha256": sha256(raw).hexdigest(), "index_as_of": index_cutoff.isoformat(),
        "entries": entries, "locally_hash_matched_artifacts": len(entries),
        "artifact_types_seen": sorted({e["artifact_type"] for e in entries}),
        "council_context_labels_seen": sorted({e["council_family"] for e in entries if e["council_family"]}),
        "revision_claims_checked": sum(e["supersedes"] is not None for e in entries),
        "claimed_receipt_count": len(seen_receipts),
    }


def inspect_evidence_custody(*, index_file=None, evidence_root=None,
                              previous_index=None, previous_root=None,
                              epoch_id, as_of):
    """Bounded local file-integrity check, not independent provider attestation."""
    cutoff = _utc(as_of)
    if index_file is None:
        if any(x is not None for x in (evidence_root, previous_index, previous_root)):
            raise Stage7BaselineError("Evidence roots/snapshots without current index.")
        return {
            "status": "NOT_SUPPLIED", "index_sha256": None,
            "locally_hash_matched_artifacts": 0, "artifact_types_seen": [],
            "council_context_labels_seen": [], "revision_claims_checked": 0,
            "previous_snapshot": "NOT_SUPPLIED", "append_only_prefix_checked": False,
            "independently_authenticated_receipts": 0,
            "independently_verified_provider_families": 0,
            "provider_origin_independently_verified": False,
            "operator_review_approval": False,
        }
    current = _read_custody_index(index_file, evidence_root, epoch_id=epoch_id,
                                  assessment_cutoff=cutoff)
    old_status = "NOT_SUPPLIED"
    prefix_consistent = False
    if previous_index is None:
        if previous_root is not None:
            raise Stage7BaselineError("Previous custody root without previous index.")
    else:
        if previous_root is None:
            raise Stage7BaselineError("Previous custody index requires previous root.")
        previous = _read_custody_index(previous_index, previous_root, epoch_id=epoch_id,
                                       assessment_cutoff=cutoff)
        if (previous["index_as_of"] > current["index_as_of"]
                or previous["entries"] != current["entries"][:len(previous["entries"])]):
            raise Stage7BaselineError("Custody snapshot append-only prefix was changed or deleted.")
        old_status = "LOCAL_PREFIX_CONSISTENT_UNATTESTED"
        prefix_consistent = True
    return {
        "status": "LOCAL_CUSTODY_CANDIDATES_UNATTESTED",
        "index_sha256": current["raw_index_sha256"],
        "index_as_of": current["index_as_of"],
        "locally_hash_matched_artifacts": current["locally_hash_matched_artifacts"],
        "artifact_types_seen": current["artifact_types_seen"],
        "council_context_labels_seen": current["council_context_labels_seen"],
        "revision_claims_checked": current["revision_claims_checked"],
        "previous_snapshot": old_status, "append_only_prefix_checked": prefix_consistent,
        "independently_authenticated_receipts": 0,
        "independently_verified_provider_families": 0,
        "provider_origin_independently_verified": False,
        "operator_review_approval": False,
    }


def assess_stage7_baseline(*, repo, epoch_id, as_of, index_file=None, evidence_root=None,
                           previous_index=None, previous_root=None,
                           batches=BATCHES, stage5_prefix=STAGE5_PREFIX,
                           branch=BRANCH, final_prefix=STAGE6_FINAL_PREFIX):
    """Compose a deterministic engineering baseline without upgrading any gate."""
    if epoch_id != EPOCH:
        raise Stage7BaselineError("Historical epoch mismatch; no rebind.")
    cutoff = _utc(as_of)
    git = inspect_six_stage6_commits(repo, batches=batches, stage5_prefix=stage5_prefix,
                                     branch=branch, final_prefix=final_prefix)
    inventory = inspect_capability_inventory(repo)
    try:
        historical = inspect_original_policy(repo)
        council = inspect_static_council(
            contracts_file=Path(repo) / "backend/agents/contracts.py",
            manifest_file=Path(repo) / "backend/agents/council_manifest.py",
        )
    except (FinalHandoverError, CouncilContextError, OSError, ValueError, TypeError):
        raise Stage7BaselineError("Historical policy or Council static inventory invalid.") from None
    if council["static_seats"] != 31 or council["static_source_families"] != 13:
        raise Stage7BaselineError("31-seat/13-family static Council contract mismatch.")
    custody = inspect_evidence_custody(
        index_file=index_file, evidence_root=evidence_root,
        previous_index=previous_index, previous_root=previous_root,
        epoch_id=epoch_id, as_of=as_of,
    )
    gates = [
        {"id": item["id"], "requirement": item["name"],
         "independent_reviewer_required": True,
         "status": "NOT_INDEPENDENTLY_VERIFIED", "approved": False}
        for item in git["gate_contract"]
    ]
    blockers = ["EXTERNAL_GATE_UNVERIFIED:" + g["id"] for g in gates]
    blockers += [
        "NO_INDEPENDENT_BROKER_CLOCK_OR_ORIGINAL_CAPTURE_ATTESTATION",
        "NO_INDEPENDENT_PROVIDER_SOURCE_ADMISSION_FOR_13_COUNCIL_LABELS",
        "NO_AUTHORIZED_PROSPECTIVE_COUNCIL_INPUT_OR_FORWARD_EPOCH",
        "NO_INDEPENDENT_PREDECISION_OUTCOME_COST_OR_HOLDOUT_PROOF",
        "NO_OPERATOR_RISK_AND_EXECUTION_APPROVAL",
        "NO_VERIFIED_NET_OF_COSTS_COUNCIL_ADVANTAGE",
        "P7_01_04_WINDOWS_TESTS_AND_GIT_COMMIT_PENDING",
    ]
    if custody["status"] == "NOT_SUPPLIED":
        blockers.append("NO_OPTIONAL_CUSTODY_EVIDENCE_INDEX_SUPPLIED")
    if custody["previous_snapshot"] == "NOT_SUPPLIED":
        blockers.append("NO_OPTIONAL_PREVIOUS_CUSTODY_SNAPSHOT_SUPPLIED")
    report = {
        "assessment_version": VERSION,
        "scope": "P7_01_TO_P7_04_OFFLINE_POSTCOMMIT_BASELINE",
        "epoch_id": EPOCH, "as_of": cutoff.isoformat(),
        "stage6_postcommit_git_audit": git,
        "capability_inventory": inventory,
        "historical_clock_policy": historical,
        "static_council": {
            "status": council["status"], "seats": council["static_seats"],
            "context_family_labels": council["static_source_families"],
            "contract_sha256": council["contracts_sha256"],
            "manifest_sha256": council["manifest_sha256"],
            "agents_executed": False,
        },
        "candidate_custody_register": custody,
        "previous_operator_windows_regression": {
            "status": "USER_REPORTED_LOG_NOT_RERUN_BY_INSPECTOR",
            "passed": 1200, "skipped": 14, "subtests_passed": 61,
            "last_committed_stage6_prefix": STAGE6_FINAL_PREFIX,
        },
        "stage7_checkpoints_planned_in_batch": 4,
        "stage7_checkpoints_automatically_verified": 0,
        "external_gate_matrix": gates,
        "external_gates_independently_verified": 0,
        "context_families_independently_verified": 0,
        "read_only": True, "files_written_by_inspector": False,
        "git_mutated": False, "remote_server_queried": False,
        "network_accessed": False, "broker_connected": False,
        "database_accessed": False, "collector_started": False,
        "council_invoked": False, "training_authorized": False,
        "settlement_invoked": False, "paper_orders_authorized": False,
        "live_orders_authorized": False, "historical_epoch_rebound": False,
        "forward_epoch_created": False, "operator_approval_granted": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-git", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--custody-index")
    parser.add_argument("--custody-root")
    parser.add_argument("--previous-index")
    parser.add_argument("--previous-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise Stage7BaselineError("Future assessment cutoff refused.")
        report = assess_stage7_baseline(
            repo=args.repo, epoch_id=args.epoch_id, as_of=args.as_of,
            index_file=args.custody_index, evidence_root=args.custody_root,
            previous_index=args.previous_index, previous_root=args.previous_root,
        )
    except (Stage7BaselineError, OSError, ValueError, TypeError) as exc:
        print("STAGE 7 BASELINE BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 7 P7-01..04 postcommit evidence baseline")
        print("Stage 6 commits locally verified: %d/6" % report["stage6_postcommit_git_audit"]["batch_count"])
        print("Stage 6 engineering checkpoints: 24/24 (Git source anchors)")
        print("Local HEAD/tracking: MATCH | Remote independently checked: NO")
        print("Tracked files inventoried: %d" % report["capability_inventory"]["tracked_files_total"])
        print("Historical policy: " + report["historical_clock_policy"]["status"])
        print("Council seats/context labels: 31/13 (static AST only)")
        print("Optional custody index: " + report["candidate_custody_register"]["status"])
        print("Independent gate/source acceptance: 0/11 | 0/13")
        print("Stage 7 Batch 1: AWAITING WINDOWS VERIFICATION AND COMMIT")
        for issue in report["blockers"]:
            print("  BLOCKER " + issue)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO COLLECTOR | NO COUNCIL | NO ORDERS | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
