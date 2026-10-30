
"""P6-21..24: final read-only Stage 6 engineering and evidence handover.

Inspects five *previously committed* Stage 6 batches at the operator-reported
Git branch, static Council contracts, original policy bytes, and optional
already-saved local reports. Does NOT test or self-certify this uncommitted
final batch. Local Git, local SHA-256 and local timestamps are NOT third-party
attestation. Never connects to MT5, SQL, APIs or executes Council/trading code.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess

from backend.runtime.stage6_council_context_readiness import (
    CouncilContextError, inspect_static_council,
)

VERSION = "EURUSD_STAGE6_FINAL_RESEARCH_HANDOVER_P6_21_24_V1"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
POLICY_SHA256 = "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088"
STAGE5_ANCHOR = "fb1c643"
BRANCH = "feature/utc-research-m01-m08"

# Batch 6 does not appear here: it has not been tested and committed yet.
BATCHES = (
    ("P6-01..04", "9fc5e1d", "stage6_research_governance"),
    ("P6-05..08", "3ad7e04", "stage6_source_evidence_envelopes"),
    ("P6-09..12", "e403da8", "stage6_council_context_readiness"),
    ("P6-13..16", "ee5a43d", "stage6_prospective_ledger_readiness"),
    ("P6-17..20", "7a4bcfb", "stage6_research_evaluation_readiness"),
)

POLICY_PATH = "docs/clock_profiles/hfm_forward_20261027.json"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX_GIT = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
MAX_SOURCE = 1048576
MAX_REPORT = 1048576
UTC = timezone.utc

# Names checked in previously committed evidence and safety contracts.
DENIED_TRUST_FLAGS = (
    "research_admission_authorized", "paper_execution_authorized",
    "live_execution_authorized", "paper_orders_authorized",
    "live_orders_authorized", "training_authorized",
    "model_training_authorized", "prospective_profitability_established",
    "statistical_superiority_or_net_profit_established",
    "independent_holdout_certified", "independent_outcomes_certified",
    "actual_broker_costs_certified", "research_ready",
    "collector_restart_allowed", "council_dispatch_allowed",
    "historical_epoch_rebinding_allowed", "external_origins_authenticated",
    "independent_origin_or_receipt_attested", "independent_broker_utc_attested",
    "receipt_times_independently_authenticated",
)


class FinalHandoverError(ValueError):
    """Unexpected repository ancestry, source, report or trust claim."""


def _utc(value):
    if not isinstance(value, str):
        raise FinalHandoverError("Explicit UTC timestamp required.")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise FinalHandoverError("Invalid UTC timestamp.") from None
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise FinalHandoverError("Explicit zero-offset UTC required.")
    return parsed.astimezone(UTC)


def _digest(value):
    return sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise FinalHandoverError("Duplicate report JSON key refused.")
        result[key] = value
    return result


def _decode(raw):
    try:
        result = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                FinalHandoverError("Nonfinite JSON refused.")
            ),
        )
    except FinalHandoverError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise FinalHandoverError("Invalid report JSON.") from None
    if not isinstance(result, dict):
        raise FinalHandoverError("JSON report object required.")
    return result


def _read(path, limit):
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise FinalHandoverError("Existing regular file required.")
    try:
        with file.open("rb") as handle:
            raw = handle.read(limit + 1)
    except OSError:
        raise FinalHandoverError("Source/report file could not be read.") from None
    if len(raw) > limit:
        raise FinalHandoverError("Source/report file exceeds bounded size.")
    return raw


def _git(repo, *args, accepted=(0,)):
    """Allow only fixed, read-only Git queries; no shell or network calls."""
    environment = dict(
        os.environ,
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
    )
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=12,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise FinalHandoverError(
            "Local read-only Git query unavailable."
        ) from None
    if completed.returncode not in accepted or len(completed.stdout) > MAX_SOURCE:
        raise FinalHandoverError("Local read-only Git query failed.")
    return completed.returncode, completed.stdout


def _line(repo, *args):
    try:
        return _git(repo, *args)[1].decode("utf-8").strip()
    except UnicodeError:
        raise FinalHandoverError("Invalid Git metadata encoding.") from None


def _sources_for(stem):
    return (
        "backend/runtime/" + stem + ".py",
        "backend/runtime/test_" + stem + ".py",
    )


def _static_gates(governance_source):
    """Obtain all 11 committed proof requirements without imports."""
    try:
        tree = ast.parse(governance_source.decode("utf-8"))
    except (SyntaxError, UnicodeError, RecursionError):
        raise FinalHandoverError(
            "Governing gate source is not valid Python."
        ) from None

    assignments = [
        n.value for n in tree.body
        if isinstance(n, ast.Assign)
        and any(
            isinstance(t, ast.Name) and t.id == "GATES"
            for t in n.targets
        )
    ]
    if len(assignments) != 1:
        raise FinalHandoverError(
            "Exactly one static GATES declaration required."
        )
    try:
        gates = ast.literal_eval(assignments[0])
    except (ValueError, TypeError, RecursionError):
        raise FinalHandoverError(
            "GATES must be literal, not executable code."
        ) from None

    if (
        not isinstance(gates, tuple)
        or len(gates) != 11
        or any(
            not isinstance(g, tuple)
            or len(g) != 4
            or any(not isinstance(v, str) or not v for v in g)
            for g in gates
        )
        or [g[0] for g in gates] != [
            "G%02d" % i for i in range(1, 12)
        ]
        or len({g[1] for g in gates}) != 11
    ):
        raise FinalHandoverError(
            "Expected eleven unique external evidence gates."
        )
    return gates


def inspect_previous_stage6_git(
    repo, *, batches=BATCHES,
    stage5_anchor=STAGE5_ANCHOR, branch=BRANCH,
):
    """Check five-commit first-parent lineage, two-file adds and blobs."""
    root = Path(repo).absolute()
    if root.is_symlink() or not root.is_dir():
        raise FinalHandoverError(
            "Existing nonsymlink repository root required."
        )

    reported_root = _line(root, "rev-parse", "--show-toplevel")
    if Path(reported_root).resolve() != root.resolve():
        raise FinalHandoverError(
            "Run inspector at repository top-level only."
        )

    if _line(root, "symbolic-ref", "--quiet", "--short", "HEAD") != branch:
        raise FinalHandoverError("Unexpected current branch.")

    head = _line(root, "rev-parse", "HEAD")
    tracking = _line(
        root, "rev-parse", "refs/remotes/origin/" + branch,
    )

    if head != tracking or not HEX_GIT.fullmatch(head):
        raise FinalHandoverError(
            "Local HEAD and local origin-tracking ref differ."
        )

    # Ignore untracked Batch 6 files; tracked changes must remain clean.
    if _git(
        root, "diff", "--quiet", "HEAD", "--", accepted=(0, 1),
    )[0] != 0:
        raise FinalHandoverError(
            "Tracked source or staging differs from HEAD."
        )

    lineage = _line(
        root, "rev-list", "--first-parent", "--max-count=6", "HEAD",
    ).splitlines()

    if len(lineage) != len(batches) + 1:
        raise FinalHandoverError(
            "Stage 6 first-parent ancestry length mismatch."
        )

    expected = [
        (label, prefix, stem)
        for label, prefix, stem in reversed(batches)
    ]
    for actual, (_, prefix, _) in zip(lineage[:len(batches)], expected):
        if not actual.startswith(prefix):
            raise FinalHandoverError(
                "Unexpected Stage 6 Git commit order."
            )

    if not lineage[-1].startswith(stage5_anchor):
        raise FinalHandoverError(
            "Stage 5 final handover ancestor mismatch."
        )

    details = []
    gate_source = None

    for label, prefix, stem in batches:
        match = next(x for x in lineage if x.startswith(prefix))
        if not HEX_GIT.fullmatch(match):
            raise FinalHandoverError("Invalid Git commit hash format.")

        parents = _line(
            root, "rev-list", "--parents", "-n", "1", match,
        ).split()
        if len(parents) != 2:
            raise FinalHandoverError(
                "Merge/root commit forbidden inside Stage 6 sequence."
            )

        expected_paths = _sources_for(stem)
        statuses = _line(
            root, "diff-tree", "--no-commit-id", "-r", "--name-status",
            "--no-renames", match,
        ).splitlines()

        actual_changes = set()
        for changed in statuses:
            pair = changed.split("\t")
            if len(pair) != 2 or pair[0] != "A":
                raise FinalHandoverError(
                    "Stage 6 batch changed existing/unapproved paths."
                )
            actual_changes.add(pair[1])

        if actual_changes != set(expected_paths) or len(statuses) != 2:
            raise FinalHandoverError(
                "Each prior batch must add exactly its source/test pair."
            )

        file_details = []
        for path in expected_paths:
            committed_blob = _line(
                root, "rev-parse", match + ":" + path,
            )
            head_blob = _line(root, "rev-parse", "HEAD:" + path)

            if committed_blob != head_blob:
                raise FinalHandoverError(
                    "Previously committed source blob changed at HEAD."
                )

            body = _git(root, "show", "HEAD:" + path)[1]
            local = _read(root / Path(path), MAX_SOURCE)

            # Git's cleaned blob can differ in line endings from a
            # Windows checkout; git diff HEAD checks tracked equivalence.
            try:
                ast.parse(body.decode("utf-8"))
                ast.parse(local.decode("utf-8-sig"))
            except (UnicodeError, SyntaxError, RecursionError):
                raise FinalHandoverError(
                    "Stage 6 source/test AST invalid."
                ) from None

            if len(body) > MAX_SOURCE:
                raise FinalHandoverError(
                    "Committed source file exceeds limit."
                )

            file_details.append({
                "path": path,
                "committed_git_blob_id": committed_blob,
                "committed_content_sha256": sha256(body).hexdigest(),
                "tracked_worktree_matches_git_head": True,
                "python_ast_parsed": True,
            })

            if (
                stem == "stage6_research_governance"
                and path == expected_paths[0]
            ):
                gate_source = body

        details.append({
            "checkpoint_range": label,
            "commit": match,
            "commit_prefix": prefix,
            "change_scope": "TWO_ADDED_FILES",
            "files": file_details,
            "source_unchanged_at_head": True,
        })

    gates = _static_gates(gate_source)

    return {
        "status": "FIVE_PREVIOUS_STAGE6_GIT_ANCHORS_VERIFIED_LOCALLY",
        "repository_branch": branch,
        "local_head": head,
        "local_origin_tracking_head": tracking,
        "local_origin_tracking_matches_head": True,
        "remote_server_independently_queried": False,
        "stage5_parent_commit": lineage[-1],
        "prior_batches_verified": len(details),
        "prior_checkpoints_git_anchor_verified": 4 * len(details),
        "tracked_worktree_clean": True,
        "untracked_files_not_examined": True,
        "batches": details,
        "gate_requirements_from_static_source": [
            {
                "gate_id": g[0],
                "gate": g[1],
                "review_role": g[2],
                "independent_proof_required": g[3],
            }
            for g in gates
        ],
    }


def inspect_original_policy(repo):
    """Check October 27 policy byte pin; never rebind it."""
    root = Path(repo)
    path = root / POLICY_PATH

    for part in (
        root, root / "docs",
        root / "docs/clock_profiles", path,
    ):
        if part.is_symlink():
            raise FinalHandoverError(
                "Original policy is inside a symlinked path."
            )

    digest = sha256(_read(path, 16384)).hexdigest()
    if digest != POLICY_SHA256:
        raise FinalHandoverError(
            "Historical policy byte pin mismatch; never repair/rebind."
        )

    return {
        "status": "ORIGINAL_HISTORICAL_POLICY_BYTES_MATCH_PIN",
        "path": POLICY_PATH,
        "raw_sha256": digest,
        "historical_epoch_id": EPOCH,
        "historical_epoch_rebound": False,
        "independent_broker_utc_attested": False,
    }


def _reject_self_promotion(report):
    """Reject nested operational approvals and external verification."""
    stack = [report]
    checked = 0

    while stack:
        obj = stack.pop()
        checked += 1

        if checked > 10000:
            raise FinalHandoverError(
                "Saved report nesting/size limit exceeded."
            )

        if isinstance(obj, dict):
            for key, value in obj.items():
                if not isinstance(key, str):
                    raise FinalHandoverError(
                        "Invalid report key type."
                    )

                claimed_external = (
                    key in (
                        "external_gates_verified",
                        "external_provenance_gates_verified",
                        "external_prospective_gates_verified",
                        "external_evaluation_gates_verified",
                        "independent_source_families_verified",
                        "source_families_independently_verified",
                        "externally_verified_source_families",
                    )
                    or key.endswith("_independently_verified")
                    or key.startswith((
                        "independently_authenticated_",
                        "externally_verified_",
                    ))
                    or (
                        key.startswith((
                            "independent_", "independently_",
                            "external_", "externally_",
                        ))
                        and key.endswith((
                            "_verified", "_certified", "_attested",
                            "_authenticated", "_authorized", "_approved",
                        ))
                    )
                )

                readiness_promoted = (
                    key in (
                        "operational_readiness",
                        "research_operational_readiness",
                    )
                    and value in (
                        "READY", "ADMITTED", "APPROVED", "OPERATIONAL",
                    )
                )

                if (
                    key in DENIED_TRUST_FLAGS and value is True
                    or key in (
                        "execution_authorized", "approved",
                    ) and value is True
                    or claimed_external and value not in (0, False, None)
                    or readiness_promoted
                ):
                    raise FinalHandoverError(
                        "Saved report self-promotes unverified trust."
                    )

                if isinstance(value, (list, dict)):
                    stack.append(value)

        elif isinstance(obj, list):
            stack.extend(obj)


def inspect_optional_saved_reports(saved_reports, *, as_of):
    """Check saved local report hashes, never independent proof."""
    cutoff = _utc(as_of)
    reports = {} if saved_reports is None else dict(saved_reports)

    if (
        len(reports) > len(BATCHES)
        or not set(reports).issubset({x[0] for x in BATCHES})
    ):
        raise FinalHandoverError(
            "Unexpected saved-report batch labels."
        )

    result = []
    for label, _, _ in BATCHES:
        if label not in reports:
            result.append({
                "checkpoint_range": label,
                "status": "NOT_SUPPLIED",
                "report_sha256": None,
                "independently_attested": False,
            })
            continue

        data = _decode(_read(reports[label], MAX_REPORT))
        claimed_digest = data.get("report_sha256")

        if (
            not isinstance(claimed_digest, str)
            or not HEX64.fullmatch(claimed_digest)
            or _digest({
                k: v for k, v in data.items()
                if k != "report_sha256"
            }) != claimed_digest
            or data.get("epoch_id") != EPOCH
            or data.get("final_action") != "HOLD"
            or data.get("read_only") is not True
            or _utc(data.get("as_of")) > cutoff
            or any(
                data.get(name) is True
                for name in DENIED_TRUST_FLAGS
            )
        ):
            raise FinalHandoverError(
                "Saved candidate report is inconsistent or unsafe."
            )

        _reject_self_promotion(data)

        result.append({
            "checkpoint_range": label,
            "status": "LOCAL_REPORT_DIGEST_MATCHES_UNATTESTED",
            "report_sha256": claimed_digest,
            "independently_attested": False,
        })

    return {
        "optional_reports_supplied": sum(
            x["status"] != "NOT_SUPPLIED" for x in result
        ),
        "saved_reports_independently_verified": 0,
        "reports": result,
    }


def assess_stage6_final_handover(
    *, repo, epoch_id, as_of, saved_reports=None,
    batches=BATCHES, stage5_anchor=STAGE5_ANCHOR, branch=BRANCH,
):
    if epoch_id != EPOCH:
        raise FinalHandoverError(
            "Historical epoch mismatch; no rebind permitted."
        )

    cutoff = _utc(as_of)
    source = inspect_previous_stage6_git(
        repo, batches=batches,
        stage5_anchor=stage5_anchor, branch=branch,
    )
    original = inspect_original_policy(repo)

    try:
        council = inspect_static_council(
            contracts_file=Path(repo) / "backend/agents/contracts.py",
            manifest_file=Path(repo) / "backend/agents/council_manifest.py",
        )
    except (CouncilContextError, OSError, ValueError, TypeError):
        raise FinalHandoverError(
            "Canonical Council static inventory invalid."
        ) from None

    reports = inspect_optional_saved_reports(
        saved_reports, as_of=cutoff.isoformat(),
    )

    gates = [
        {
            **entry,
            "status": "NOT_INDEPENDENTLY_VERIFIED",
            "approved": False,
        }
        for entry in source["gate_requirements_from_static_source"]
    ]

    blockers = [
        "EXTERNAL_GATE_NOT_INDEPENDENTLY_VERIFIED:" + g["gate"]
        for g in gates
    ]

    blockers.extend((
        "NO_INDEPENDENT_ATTESTATION_FOR_ALL_13_COUNCIL_INPUT_FAMILIES",
        "NO_AUTHENTICATED_PREDECISION_PREDICTION_LEDGER",
        "NO_INDEPENDENT_POST_HORIZON_OUTCOMES_OR_REAL_EXECUTION_COST_EVIDENCE",
        "NO_INDEPENDENT_PRE_EVALUATION_HOLDOUT_FREEZE",
        "NO_VERIFIED_COST_AWARE_COUNCIL_SUPERIORITY_OR_PROFITABILITY",
        "NO_SEPARATE_OPERATOR_APPROVAL_FOR_COLLECTOR_COUNCIL_OR_EXECUTION",
        "FINAL_BATCH_WINDOWS_TESTS_AND_GIT_COMMIT_PENDING",
    ))

    if reports["optional_reports_supplied"] < len(batches):
        blockers.append(
            "SOME_OPTIONAL_SAVED_STAGE6_REPORTS_NOT_SUPPLIED"
        )

    result = {
        "assessment_version": VERSION,
        "epoch_id": EPOCH,
        "as_of": cutoff.isoformat(),
        "scope": "P6_21_TO_P6_24_FINAL_OFFLINE_RESEARCH_HANDOVER",
        "previous_stage6_git_custody": source,
        "original_historical_clock_policy": original,
        "static_council_inventory": {
            "status": council["status"],
            "static_seats": council["static_seats"],
            "static_source_families": council["static_source_families"],
            "manifest_sha256": council["manifest_sha256"],
            "contracts_sha256": council["contracts_sha256"],
            "council_executed": False,
        },
        "previous_stage6_saved_reports": reports,
        "engineering_checkpoints_prior_batches_verified_locally": (
            4 * len(batches)
        ),
        "engineering_checkpoints_total_stage6": 24,
        "final_batch_self_certified": False,
        "final_batch_windows_tests_verified_by_this_report": False,
        "final_batch_git_commit_verified_by_this_report": False,
        "operator_reported_previous_windows_regression": {
            "status": "OPERATOR_TERMINAL_LOG_NOT_RERUN_BY_INSPECTOR",
            "passed": 1155,
            "skipped": 14,
            "subtests_passed": 61,
            "last_confirmed_batch": "P6-17..20",
        },
        "external_acceptance_matrix": gates,
        "external_evidence_gates_total": 11,
        "external_evidence_gates_independently_verified": 0,
        "source_families_independently_verified": 0,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "next_research_priority": (
            "INDEPENDENT_SOURCE_CUSTODY_CLOCK_AND_PROSPECTIVE_EVALUATION_"
            "WITH_SEPARATE_OPERATOR_APPROVAL"
        ),
        "next_phase_automatically_authorized": False,
        "blockers": sorted(blockers),
        "read_only": True,
        "files_written": False,
        "git_mutated": False,
        "remote_network_accessed": False,
        "database_accessed": False,
        "broker_connected": False,
        "collector_started": False,
        "historical_epoch_rebound": False,
        "council_invoked": False,
        "outcome_settlement_invoked": False,
        "model_training_authorized": False,
        "paper_execution_authorized": False,
        "live_execution_authorized": False,
        "final_action": "HOLD",
    }

    result["report_sha256"] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only-git", action="store_true", required=True,
    )
    parser.add_argument(
        "--read-only-evidence", action="store_true", required=True,
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument(
        "--saved-report", action="append", default=[],
        metavar="P6-01..04=PATH",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise FinalHandoverError(
                "Future assessment cutoff refused."
            )

        optional = {}
        for item in args.saved_report:
            if "=" not in item:
                raise FinalHandoverError(
                    "Saved reports must be LABEL=PATH."
                )

            label, path = item.split("=", 1)
            if not path or label in optional:
                raise FinalHandoverError(
                    "Duplicate/empty saved-report argument."
                )
            optional[label] = path

        report = assess_stage6_final_handover(
            repo=args.repo,
            epoch_id=args.epoch_id,
            as_of=args.as_of,
            saved_reports=optional,
        )

    except (
        FinalHandoverError, OSError, ValueError, TypeError,
    ) as exc:
        print(
            "STAGE 6 FINAL HANDOVER BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(json.dumps(
            report, sort_keys=True, indent=2, allow_nan=False,
        ))
    else:
        print(
            "EURUSD ORACLE | Stage 6 P6-21..24 "
            "final research handover"
        )
        git = report["previous_stage6_git_custody"]

        print(
            "Prior Stage 6 Git anchors: %s/5"
            % git["prior_batches_verified"]
        )
        print(
            "Prior engineering checkpoints: %s/20"
            % git["prior_checkpoints_git_anchor_verified"]
        )
        print(
            "Local HEAD/tracking match: %s"
            % git["local_origin_tracking_matches_head"]
        )
        print(
            "Historical policy: "
            + report["original_historical_clock_policy"]["status"]
        )
        print("Council seats/source families: 31/13 (AST only)")
        print(
            "Optional saved Stage 6 reports: %s/5"
            % report["previous_stage6_saved_reports"]["optional_reports_supplied"]
        )
        print(
            "Independent external gates: 0/11 | "
            "Council families: 0/13"
        )
        print("Batch 6: AWAITING WINDOWS TESTS AND COMMIT")
        print(
            "Research operational readiness: "
            + report["research_operational_readiness"]
        )
        for item in report["blockers"]:
            print("  BLOCKER " + item)

        print("SHA-256: " + report["report_sha256"])
        print(
            "READ-ONLY | NO SOURCE CHANGES | "
            "NO COUNCIL | NO ORDERS | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
