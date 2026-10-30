
"""P7-01..04: synthetic-Git, strict custody, capability and HOLD tests.

No actual ORACLE branch is changed; all Git histories live in tmp_path.
No broker, Council, collector, settlement, network or trading services run.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from backend.runtime.stage7_postcommit_evidence_baseline import (
    BATCHES, BRANCH, EPOCH, Stage7BaselineError,
    _digest, _json, _utc, assess_stage7_baseline, inspect_capability_inventory,
    inspect_evidence_custody, inspect_six_stage6_commits, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY_FILE = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
CONTRACTS = ROOT / "backend/agents/contracts.py"
MANIFEST = ROOT / "backend/agents/council_manifest.py"
ASOF = "2026-10-30T12:00:00Z"
CUSTODY_ASOF = "2026-10-28T12:00:00Z"


def git(repo, *arguments):
    completed = subprocess.run(
        ["git", *arguments], cwd=repo,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
        check=False, shell=False,
    )
    if completed.returncode != 0:
        raise AssertionError("Synthetic Git fixture failed: " + repr(arguments))
    return completed.stdout.decode("utf-8", errors="replace").strip()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def gate_contract():
    names = (
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
    return tuple(
        ("G%02d" % (i + 1), name, "independent_reviewer", "External proof")
        for i, name in enumerate(names)
    )


def fixture_git(tmp_path, *, extra_in_batch=False, wrong_gates=False):
    repo = tmp_path / "SYNTHETIC_ORACLE_NOT_LIVE"
    repo.mkdir()
    git(repo, "init", "-b", BRANCH)
    git(repo, "config", "user.name", "SYNTHETIC_TEST_ONLY")
    git(repo, "config", "user.email", "synthetic@invalid.example")
    (repo / "docs/clock_profiles").mkdir(parents=True)
    (repo / "backend/agents").mkdir(parents=True)
    (repo / "backend/runtime").mkdir(parents=True)
    (repo / "backend/data_engine").mkdir(parents=True)
    (repo / "backend/learning").mkdir(parents=True)
    (repo / "frontend").mkdir()
    (repo / "docs/clock_profiles/hfm_forward_20261027.json").write_bytes(POLICY_FILE.read_bytes())
    (repo / "backend/agents/contracts.py").write_bytes(CONTRACTS.read_bytes())
    (repo / "backend/agents/council_manifest.py").write_bytes(MANIFEST.read_bytes())
    (repo / "backend/data_engine/store.py").write_text("class Stored: pass\n", encoding="utf-8")
    (repo / "backend/learning/memory.py").write_text("class Memory: pass\n", encoding="utf-8")
    (repo / "frontend/README.md").write_text("Synthetic frontend stub\n", encoding="utf-8")
    git(repo, "add", "--", "docs", "backend/agents", "backend/data_engine", "backend/learning", "frontend")
    git(repo, "commit", "-m", "synthetic Stage 5 final boundary")
    parent = git(repo, "rev-parse", "HEAD")[:7]
    batches = []
    for idx, (label, _, stem) in enumerate(BATCHES):
        a = "backend/runtime/" + stem + ".py"
        b = "backend/runtime/test_" + stem + ".py"
        text = "VERSION = 'SYNTHETIC_TEST'\n"
        if idx == 0:
            gates = gate_contract()
            if wrong_gates:
                gates = gates[:-1]
            text += "GATES = " + repr(gates) + "\n"
        (repo / a).write_text(text + "def foo(): return 'HOLD'\n", encoding="utf-8")
        (repo / b).write_text("def test_synthetic_only(): assert True\n", encoding="utf-8")
        git(repo, "add", "--", a, b)
        if extra_in_batch and idx == 2:
            (repo / "unapproved.dat").write_text("extra", encoding="utf-8")
            git(repo, "add", "--", "unapproved.dat")
        git(repo, "commit", "-m", "synthetic " + label)
        batches.append((label, git(repo, "rev-parse", "HEAD")[:8], stem))
    git(repo, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD")
    return repo, tuple(batches), parent


@pytest.fixture
def repo_fixture(tmp_path):
    return fixture_git(tmp_path)


def inspect(fixture):
    repo, batches, parent = fixture
    return inspect_six_stage6_commits(
        repo, batches=batches, stage5_prefix=parent,
        final_prefix=batches[-1][1], branch=BRANCH,
    )


def assess(fixture, *, custody=None, as_of=ASOF):
    repo, batches, parent = fixture
    return assess_stage7_baseline(
        repo=repo, epoch_id=EPOCH, as_of=as_of,
        batches=batches, stage5_prefix=parent,
        final_prefix=batches[-1][1], branch=BRANCH,
        **(custody or {}),
    )


def record(rid, receipt, *, supersedes=None, data_hash=None):
    return {
        "record_id": rid, "artifact_type": "MARKET_TICK",
        "council_family": "ticks", "observer_id": "UNVERIFIED_OBSERVER_A",
        "issuer_claim": "UNVERIFIED_FEED_A", "claimed_receipt_id": "RECEIPT_" + rid,
        "first_available_at": receipt.replace("02Z", "00Z") if receipt.endswith("02Z") else receipt,
        "observed_at": receipt.replace("02Z", "01Z") if receipt.endswith("02Z") else receipt,
        "claimed_receipt_at": receipt,
        "relative_path": rid + ".dat", "file_sha256": data_hash,
        "review_permission": "REVIEW_ONLY", "supersedes": supersedes,
    }


def custody_fixture(tmp_path, *, revision=False):
    root = tmp_path / "UNTRUSTED_LOCAL_CUSTODY"
    root.mkdir()
    entries = []
    candidates = [("tick_v1", "2026-10-28T09:00:02Z", None, b"LOCAL TICK 1")]
    if revision:
        candidates.append(("tick_v2", "2026-10-28T09:05:02Z", "tick_v1", b"LOCAL TICK 2"))
    for rid, receipt, parent, contents in candidates:
        file = root / (rid + ".dat")
        file.write_bytes(contents)
        entries.append(record(rid, receipt, supersedes=parent, data_hash=sha256(contents).hexdigest()))
    index = {"schema_version": 1, "epoch_id": EPOCH, "as_of": CUSTODY_ASOF, "entries": entries}
    path = tmp_path / "custody_index.json"
    save(path, index)
    return root, path, index


def custody(root=None, index=None, *, old_index=None, old_root=None):
    return inspect_evidence_custody(
        index_file=index, evidence_root=root, epoch_id=EPOCH,
        as_of=ASOF, previous_index=old_index, previous_root=old_root,
    )


# P7-01: six previous commits and no changes to frozen Stage 6 source.

def test_p701_six_commits_and_stage_boundary(repo_fixture):
    result = inspect(repo_fixture)
    assert result["batch_count"] == 6
    assert result["engineering_checkpoints"] == 24
    assert len(result["stage6_batches"]) == 6
    assert result["stage5_boundary_commit"].startswith(repo_fixture[2])
    assert result["github_contacted"] is False


def test_p701_six_exact_two_file_scopes(repo_fixture):
    result = inspect(repo_fixture)
    for entry in result["stage6_batches"]:
        assert entry["change_scope"] == "EXACT_SOURCE_TEST_ADDS"
        assert len(entry["files"]) == 2
        assert all(len(x["git_content_sha256"]) == 64 for x in entry["files"])
        assert entry["unchanged_at_head"] is True


def test_p701_tracking_only_local_not_remote(repo_fixture):
    result = inspect(repo_fixture)
    assert result["local_tracking_agrees"] is True
    assert result["head"] == result["local_origin_tracking_head"]
    assert not result["github_contacted"]


def test_p701_final_anchor_not_head_after_later_stage(repo_fixture):
    repo, batches, parent = repo_fixture
    (repo / "docs/stage7_note.txt").write_text("new follow-on stage", encoding="utf-8")
    git(repo, "add", "--", "docs/stage7_note.txt")
    git(repo, "commit", "-m", "synthetic Stage 7 successor")
    git(repo, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD")
    result = inspect(repo_fixture)
    assert result["stage6_final_commit"].startswith(batches[-1][1])
    assert result["head"] != result["stage6_final_commit"]


def test_p701_wrong_stage5_boundary_refused(repo_fixture):
    repo, batches, parent = repo_fixture
    with pytest.raises(Stage7BaselineError, match="Stage 5 boundary"):
        inspect_six_stage6_commits(repo, batches=batches,
                                   stage5_prefix="1234bad", final_prefix=batches[-1][1])


def test_p701_wrong_stage6_order_refused(repo_fixture):
    repo, batches, parent = repo_fixture
    changed = list(batches)
    a, b, c = changed[1]
    changed[1] = (a, "baadbad", c)
    with pytest.raises(Stage7BaselineError, match="commit order"):
        inspect_six_stage6_commits(repo, batches=tuple(changed), stage5_prefix=parent,
                                   final_prefix=batches[-1][1])


def test_p701_modified_tracked_source_blocks(repo_fixture):
    repo, batches, parent = repo_fixture
    (repo / "backend/agents/contracts.py").write_text("# CHANGED", encoding="utf-8")
    with pytest.raises(Stage7BaselineError, match="Tracked or staged"):
        inspect(repo_fixture)


def test_p701_staged_change_blocks(repo_fixture):
    repo, batches, parent = repo_fixture
    (repo / "backend/learning/memory.py").write_text("# CHANGED", encoding="utf-8")
    git(repo, "add", "--", "backend/learning/memory.py")
    with pytest.raises(Stage7BaselineError, match="Tracked or staged"):
        inspect(repo_fixture)


def test_p701_tracking_mismatch_blocks(repo_fixture):
    repo, batches, parent = repo_fixture
    git(repo, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD~1")
    with pytest.raises(Stage7BaselineError, match="tracking ref differ"):
        inspect(repo_fixture)


def test_p701_wrong_branch_blocks(repo_fixture):
    repo, batches, parent = repo_fixture
    git(repo, "checkout", "-b", "UNVERIFIED_BRANCH")
    with pytest.raises(Stage7BaselineError, match="research branch"):
        inspect(repo_fixture)


def test_p701_extra_file_in_batch_blocks(tmp_path):
    fixture = fixture_git(tmp_path, extra_in_batch=True)
    with pytest.raises(Stage7BaselineError, match="two-file scope"):
        inspect(fixture)


def test_p701_gate_contract_mutation_refused(tmp_path):
    fixture = fixture_git(tmp_path, wrong_gates=True)
    with pytest.raises(Stage7BaselineError, match="11-gate"):
        inspect(fixture)


def test_p701_git_from_nested_directory_refused(repo_fixture):
    repo, batches, parent = repo_fixture
    with pytest.raises(Stage7BaselineError, match="repository root"):
        inspect_six_stage6_commits(repo / "backend", batches=batches,
                                   stage5_prefix=parent, final_prefix=batches[-1][1])


def test_p701_untracked_stage7_files_allowed(repo_fixture):
    repo, batches, parent = repo_fixture
    (repo / "backend/runtime/stage7_postcommit_evidence_baseline.py").write_text(
        "# newly created code not yet committed\n", encoding="utf-8")
    result = inspect(repo_fixture)
    assert result["untracked_stage7_files_out_of_scope"] is True


# P7-02: tracked capability inventory, no service execution, no false claims.

def test_p702_inventory_reports_paths_not_connectivity(repo_fixture):
    repo = repo_fixture[0]
    inv = inspect_capability_inventory(repo)
    assert inv["status"] == "TRACKED_FILE_INVENTORY_ONLY"
    assert inv["tracked_files_total"] >= 18
    assert not inv["modules_imported_or_executed"]
    assert not inv["deployment_or_connectivity_verified"]


def test_p702_inventory_counts_council_learning_data(repo_fixture):
    inv = inspect_capability_inventory(repo_fixture[0])["inventory_categories"]
    assert inv["council_agents"]["tracked_file_count"] >= 2
    assert inv["learning"]["tracked_python_count"] == 1
    assert inv["market_data"]["tracked_python_count"] == 1
    assert inv["tests"]["tracked_python_count"] == 6
    assert inv["runtime"]["tracked_python_count"] == 6
    assert inv["dashboard_ui"]["tracked_file_count"] == 1


def test_p702_inventory_refuses_claiming_implementation(repo_fixture):
    inv = inspect_capability_inventory(repo_fixture[0])["inventory_categories"]
    assert all(not x["independently_proven"] and not x["operator_approved"] for x in inv.values())
    assert all(x["active_service_connected"] == "NOT_INSPECTED" for x in inv.values())
    assert inv["dashboard_ui"]["implementation_status"] == "TRACKED_FILES_ONLY_UNREVIEWED"


def test_p702_inventory_excludes_untracked_new_file(repo_fixture):
    repo = repo_fixture[0]
    initial = inspect_capability_inventory(repo)["tracked_files_total"]
    (repo / "frontend/UNTRACKED.py").write_text("# NOT TRACKED", encoding="utf-8")
    assert inspect_capability_inventory(repo)["tracked_files_total"] == initial


def test_p702_inventory_path_digest_repeatable(repo_fixture):
    repo = repo_fixture[0]
    assert inspect_capability_inventory(repo) == inspect_capability_inventory(repo)
    assert len(inspect_capability_inventory(repo)["tracked_paths_sha256"]) == 64


# P7-03: local original-evidence custody envelopes, NOT real authentication.

def test_p703_no_index_does_not_certify_absence():
    result = custody()
    assert result["status"] == "NOT_SUPPLIED"
    assert result["independently_authenticated_receipts"] == 0
    assert result["independently_verified_provider_families"] == 0


def test_p703_one_local_hash_claim_is_unattested(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    result = custody(root, path)
    assert result["locally_hash_matched_artifacts"] == 1
    assert result["artifact_types_seen"] == ["MARKET_TICK"]
    assert result["council_context_labels_seen"] == ["ticks"]
    assert result["provider_origin_independently_verified"] is False
    assert result["operator_review_approval"] is False


def test_p703_revision_chain_valid_locally_only(tmp_path):
    root, path, doc = custody_fixture(tmp_path, revision=True)
    result = custody(root, path)
    assert result["revision_claims_checked"] == 1
    assert result["locally_hash_matched_artifacts"] == 2
    assert result["independently_authenticated_receipts"] == 0


def test_p703_previous_snapshot_prefix_checked(tmp_path):
    root, path, doc = custody_fixture(tmp_path, revision=True)
    old = tmp_path / "OLD_INDEX.json"
    save(old, {**doc, "as_of": "2026-10-28T09:00:02Z", "entries": doc["entries"][:1]})
    result = custody(root, path, old_index=old, old_root=root)
    assert result["append_only_prefix_checked"] is True
    assert result["previous_snapshot"] == "LOCAL_PREFIX_CONSISTENT_UNATTESTED"


def test_p703_previous_prefix_edit_rejected(tmp_path):
    root, path, doc = custody_fixture(tmp_path, revision=True)
    old = tmp_path / "OLD_INDEX.json"
    save(old, doc)
    doc["entries"] = doc["entries"][:1]
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="append-only prefix"):
        custody(root, path, old_index=old, old_root=root)


def test_p703_changed_evidence_without_rehash_rejected(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    (root / "tick_v1.dat").write_bytes(b"ALTERED FILE")
    with pytest.raises(Stage7BaselineError, match="SHA-256 mismatch"):
        custody(root, path)


def test_p703_changed_evidence_rehashed_breaks_previous_snapshot(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    old = tmp_path / "OLD_INDEX.json"
    save(old, doc)
    (root / "tick_v1.dat").write_bytes(b"ALTERED FILE")
    doc["entries"][0]["file_sha256"] = sha256(b"ALTERED FILE").hexdigest()
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="SHA-256 mismatch|append-only prefix"):
        custody(root, path, old_index=old, old_root=root)


def test_p703_approval_field_even_rehashed_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    doc["entries"][0]["externally_verified"] = True
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="strict schema"):
        custody(root, path)


def test_p703_incorrect_review_permission_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    doc["entries"][0]["review_permission"] = "APPROVED"
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="Review-only"):
        custody(root, path)


def test_p703_receipt_before_observation_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    doc["entries"][0]["claimed_receipt_at"] = "2026-10-28T08:59:59Z"
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="chronology"):
        custody(root, path)


def test_p703_first_availability_after_observation_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    doc["entries"][0]["first_available_at"] = "2026-10-28T09:01:00Z"
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="chronology"):
        custody(root, path)


def test_p703_index_after_review_cutoff_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    doc["as_of"] = "2026-11-03T12:00:00Z"
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="future of assessment"):
        custody(root, path)


def test_p703_custody_index_wrong_epoch_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    doc["epoch_id"] = "f" * 64
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="schema/epoch"):
        custody(root, path)


def test_p703_duplicate_receipt_id_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path, revision=True)
    doc["entries"][1]["claimed_receipt_id"] = doc["entries"][0]["claimed_receipt_id"]
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="identifier reused"):
        custody(root, path)


def test_p703_future_parent_revision_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path, revision=True)
    doc["entries"][0]["supersedes"] = "tick_v2"
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="Unknown, future"):
        custody(root, path)


def test_p703_cross_type_revision_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path, revision=True)
    doc["entries"][1]["artifact_type"] = "ECB_RELEASE"
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="incompatible"):
        custody(root, path)


def test_p703_duplicated_record_id_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path, revision=True)
    doc["entries"][1]["record_id"] = "tick_v1"
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="Duplicate custody"):
        custody(root, path)


def test_p703_duplicate_artifact_path_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path, revision=True)
    doc["entries"][1]["relative_path"] = "tick_v1.dat"
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="Duplicate custody artifact path"):
        custody(root, path)


@pytest.mark.parametrize("bad", ["../secret", "/etc/passwd", "C:/secret", "a//b", "./file", "a/../b", "nested\\bad"])
def test_p703_relative_path_traversal_refused(tmp_path, bad):
    root, path, doc = custody_fixture(tmp_path)
    doc["entries"][0]["relative_path"] = bad
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="Unsafe relative"):
        custody(root, path)


def test_p703_symlink_artifact_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    link = root / "link.dat"
    try:
        link.symlink_to(root / "tick_v1.dat")
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlink creation unavailable")
    doc["entries"][0]["relative_path"] = link.name
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="Symlink"):
        custody(root, path)


def test_p703_missing_root_with_index_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    with pytest.raises(Stage7BaselineError, match="root required"):
        custody(index=path)


def test_p703_root_without_index_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    with pytest.raises(Stage7BaselineError, match="without current index"):
        custody(root=root)


def test_p703_duplicate_json_field_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
    with pytest.raises(Stage7BaselineError, match="Duplicate JSON"):
        custody(root, path)


def test_p703_nonfinite_json_refused():
    with pytest.raises(Stage7BaselineError, match="Nonfinite"):
        _json(b'{"field":NaN}')


def test_p703_family_unknown_refused(tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    doc["entries"][0]["council_family"] = "INVENTED_FAMILY"
    save(path, doc)
    with pytest.raises(Stage7BaselineError, match="unknown type/family"):
        custody(root, path)


# P7-04: deterministic handover, fixed historical pins and HOLD decisions.

def test_p704_read_only_report_complete_synthetic(repo_fixture):
    result = assess(repo_fixture)
    assert result["scope"] == "P7_01_TO_P7_04_OFFLINE_POSTCOMMIT_BASELINE"
    assert result["stage6_postcommit_git_audit"]["batch_count"] == 6
    assert result["historical_clock_policy"]["raw_sha256"] == (
        "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088")
    assert result["static_council"]["seats"] == 31
    assert result["static_council"]["context_family_labels"] == 13


def test_p704_gate_matrix_retains_all_eleven(repo_fixture):
    result = assess(repo_fixture)
    assert len(result["external_gate_matrix"]) == 11
    assert [x["id"] for x in result["external_gate_matrix"]] == ["G%02d" % i for i in range(1, 12)]
    assert all(x["status"] == "NOT_INDEPENDENTLY_VERIFIED" for x in result["external_gate_matrix"])
    assert all(x["approved"] is False for x in result["external_gate_matrix"])


def test_p704_local_custody_does_not_upgrade_gate(repo_fixture, tmp_path):
    root, path, doc = custody_fixture(tmp_path)
    result = assess(repo_fixture, custody={"index_file": path, "evidence_root": root})
    assert result["candidate_custody_register"]["locally_hash_matched_artifacts"] == 1
    assert result["external_gates_independently_verified"] == 0
    assert result["context_families_independently_verified"] == 0
    assert result["final_action"] == "HOLD"


def test_p704_policy_mutation_is_blocked(repo_fixture):
    repo, batches, parent = repo_fixture
    (repo / "docs/clock_profiles/hfm_forward_20261027.json").write_bytes(b"FORGED")
    with pytest.raises(Stage7BaselineError, match="Tracked or staged"):
        assess(repo_fixture)


def test_p704_epoch_rebind_denied(repo_fixture):
    repo, batches, parent = repo_fixture
    with pytest.raises(Stage7BaselineError, match="Historical epoch mismatch"):
        assess_stage7_baseline(repo=repo, epoch_id="f" * 64,
                               as_of=ASOF, batches=batches, stage5_prefix=parent,
                               final_prefix=batches[-1][1])


def test_p704_no_trading_training_or_database(repo_fixture):
    result = assess(repo_fixture)
    for field in ("files_written_by_inspector", "git_mutated", "remote_server_queried",
                  "network_accessed", "broker_connected", "database_accessed",
                  "collector_started", "council_invoked", "training_authorized",
                  "settlement_invoked", "paper_orders_authorized", "live_orders_authorized",
                  "historical_epoch_rebound", "forward_epoch_created", "operator_approval_granted"):
        assert result[field] is False
    assert result["read_only"] is True


def test_p704_report_deterministic_for_same_files_and_cutoff(repo_fixture):
    first, second = assess(repo_fixture), assess(repo_fixture)
    assert first == second
    assert first["report_sha256"] == _digest({k: v for k, v in first.items() if k != "report_sha256"})


def test_p704_previous_regression_operator_report_only(repo_fixture):
    value = assess(repo_fixture)["previous_operator_windows_regression"]
    assert (value["passed"], value["skipped"], value["subtests_passed"]) == (1200, 14, 61)
    assert value["status"] == "USER_REPORTED_LOG_NOT_RERUN_BY_INSPECTOR"


def test_p704_future_cutoff_cli_denied(capsys, repo_fixture):
    future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    assert run(["--read-only-git", "--read-only-evidence", "--epoch-id", EPOCH,
                "--as-of", future, "--repo", str(repo_fixture[0])]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p704_cli_fails_without_required_flags():
    with pytest.raises(SystemExit) as result:
        run(["--epoch-id", EPOCH, "--as-of", ASOF])
    assert result.value.code == 2


def test_p704_cli_real_pins_reject_synthetic_git(capsys, repo_fixture):
    assert run(["--read-only-git", "--read-only-evidence", "--repo", str(repo_fixture[0]),
                "--epoch-id", EPOCH, "--as-of", ASOF]) == 1
    assert "BLOCKED" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["not-a-time", "2026-10-30T12:00:00", "2026-10-30T15:00:00+03:00"])
def test_p704_utc_requires_explicit_zero_offset(bad):
    with pytest.raises(Stage7BaselineError):
        _utc(bad)
