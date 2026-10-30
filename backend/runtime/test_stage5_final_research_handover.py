
"""S5-21..24 synthetic local Git and saved-report handover tests only."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

import backend.runtime.stage5_final_research_handover as mod
from backend.runtime.stage5_final_research_handover import (
    Stage5HandoverError, _digest, _utc, assess_stage5_handover,
    inspect_saved_reports, inspect_stage5_git_history, run,
)

UTC = timezone.utc
ASOF = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
EPOCH = "7" * 64


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, stderr=subprocess.DEVNULL).decode().strip()


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """Six real local commits; never counterfeit production Git anchors."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "user.name", "Synthetic Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "checkout", "-q", "-b", mod.BRANCH)
    (root / "README.md").write_text("synthetic history\n", encoding="utf-8")
    git(root, "add", "--", "README.md")
    git(root, "commit", "-qm", "synthetic Stage 4 anchor")
    stage4 = git(root, "rev-parse", "HEAD")
    monkeypatch.setattr(mod, "STAGE4_ANCHOR", stage4[:7])
    specs = []
    for batch, _, inspector, test, version, function in mod.BATCHES:
        source = root / inspector
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(
            f'VERSION = "{version}"\n\ndef {function}():\n    return None\n',
            encoding="utf-8",
        )
        tests = root / test
        tests.write_text("def test_fixture_only():\n    assert True\n", encoding="utf-8")
        git(root, "add", "--", inspector, test)
        git(root, "commit", "-qm", "synthetic " + batch)
        anchor = git(root, "rev-parse", "HEAD")
        specs.append((batch, anchor[:7], inspector, test, version, function))
    monkeypatch.setattr(mod, "BATCHES", tuple(specs))
    git(root, "update-ref", "refs/remotes/origin/" + mod.BRANCH, "HEAD")
    return root


def assess(repo, **kwargs):
    return assess_stage5_handover(
        repo=repo, epoch_id=EPOCH, as_of=ASOF.isoformat(), **kwargs,
    )


def save(root, filename, content):
    path = root / filename
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def snapshots(tmp_path, batches=None):
    root = tmp_path / "reports"
    root.mkdir()
    entries = []
    for batch, _, _, _, version, _ in (batches or mod.BATCHES):
        report = {
            "assessment_version": version,
            "epoch_id": EPOCH,
            "as_of": (ASOF - timedelta(hours=1)).isoformat(),
            "read_only": True,
            "final_action": "HOLD",
            "test_fixture_synthetic_only": True,
        }
        report["report_sha256"] = _digest(report)
        path = save(root, batch + ".json", report)
        entries.append({
            "batch": batch, "relative_path": path.name,
            "file_sha256": sha256(path.read_bytes()).hexdigest(),
        })
    idx = save(tmp_path, "index.json", {
        "schema_version": 1, "epoch_id": EPOCH,
        "as_of": ASOF.isoformat(), "entries": entries,
    })
    return root, idx


def report_review(root, idx):
    return inspect_saved_reports(idx, root=root, epoch_id=EPOCH, as_of=ASOF.isoformat())


def rewrite_index(idx, update):
    data = json.loads(idx.read_text())
    update(data)
    save(idx.parent, idx.name, data)


def edit_first_report(root, idx, update, *, update_file_hash=True):
    index = json.loads(idx.read_text())
    entry = index["entries"][0]
    file = root / entry["relative_path"]
    record = json.loads(file.read_text())
    update(record)
    save(root, file.name, record)
    if update_file_hash:
        entry["file_sha256"] = sha256(file.read_bytes()).hexdigest()
        save(idx.parent, idx.name, index)


def test_s521_five_anchors_and_twenty_previous_checkpoints(repo):
    result = inspect_stage5_git_history(repo)
    assert result["all_five_prior_batches_verified"] is True
    assert result["verified_prior_batches"] == 5
    assert result["verified_prior_checkpoints"] == 20
    assert result["origin_contacted"] is False
    assert result["git_mutated"] is False


def test_s521_each_batch_only_modifies_two_files(repo):
    result = inspect_stage5_git_history(repo)
    assert len(result["batches"]) == 5
    assert all(x["commit_changes_exact_expected_two_files"] for x in result["batches"])
    assert all(x["files_unchanged_since_commit"] for x in result["batches"])
    assert all(x["inspector_version_and_entrypoint_present"] for x in result["batches"])


def test_s521_no_git_worktree_mutation(repo):
    before = git(repo, "status", "--porcelain")
    inspect_stage5_git_history(repo)
    assert git(repo, "status", "--porcelain") == before


def test_s521_local_remote_tracking_mismatch_blocks(repo):
    git(repo, "update-ref", "refs/remotes/origin/" + mod.BRANCH, "HEAD~1")
    r = inspect_stage5_git_history(repo)
    assert r["verified_prior_checkpoints"] == 20
    assert r["local_remote_tracking_matches_head"] is False
    assert r["all_five_prior_batches_verified"] is False


def test_s521_wrong_branch_refused(repo):
    git(repo, "checkout", "-qb", "wrong")
    with pytest.raises(Stage5HandoverError, match="branch"):
        inspect_stage5_git_history(repo)


def test_s521_git_root_required(repo):
    with pytest.raises(Stage5HandoverError, match="root"):
        inspect_stage5_git_history(repo / "backend")


def test_s521_dirty_previous_file_blocks(repo):
    file = repo / mod.BATCHES[2][2]
    file.write_text("# dirty synthetic\n", encoding="utf-8")
    r = inspect_stage5_git_history(repo)
    assert r["verified_prior_batches"] == 4
    assert r["all_five_prior_batches_verified"] is False


def test_s521_later_committed_file_change_blocks(repo):
    file = repo / mod.BATCHES[1][2]
    file.write_text("VERSION = 'CHANGED'\n", encoding="utf-8")
    git(repo, "add", "--", mod.BATCHES[1][2])
    git(repo, "commit", "-qm", "synthetic tamper")
    git(repo, "update-ref", "refs/remotes/origin/" + mod.BRANCH, "HEAD")
    r = inspect_stage5_git_history(repo)
    assert r["batches"][1]["files_unchanged_since_commit"] is False
    assert r["all_five_prior_batches_verified"] is False


def test_s521_extra_later_unrelated_commit_is_allowed(repo):
    (repo / "README.md").write_text("later unrelated\n", encoding="utf-8")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "later unrelated")
    git(repo, "update-ref", "refs/remotes/origin/" + mod.BRANCH, "HEAD")
    assert inspect_stage5_git_history(repo)["all_five_prior_batches_verified"]


def test_s521_missing_previous_source_refused(repo):
    (repo / mod.BATCHES[0][2]).unlink()
    with pytest.raises(Stage5HandoverError, match="regular"):
        inspect_stage5_git_history(repo)


def test_s521_wrong_previous_inspector_version_blocks(repo):
    path = repo / mod.BATCHES[0][2]
    path.write_text('VERSION = "WRONG"\n\ndef assess_stage5_intake(): pass\n')
    r = inspect_stage5_git_history(repo)
    assert r["batches"][0]["inspector_version_and_entrypoint_present"] is False


def test_s521_backward_batch_anchors_block(repo, monkeypatch):
    specs = list(mod.BATCHES)
    specs[1], specs[2] = specs[2], specs[1]
    monkeypatch.setattr(mod, "BATCHES", tuple(specs))
    assert inspect_stage5_git_history(repo)["all_five_prior_batches_verified"] is False


def test_s522_no_snapshot_index_is_not_supplied():
    r = inspect_saved_reports(None, root=None, epoch_id=EPOCH, as_of=ASOF.isoformat())
    assert r["status"] == "NOT_SUPPLIED"
    assert r["locally_reconciled_report_count"] == 0
    assert r["independent_external_evidence_verified"] is False


def test_s522_index_without_root_refused(tmp_path):
    _, idx = snapshots(tmp_path)
    with pytest.raises(Stage5HandoverError, match="root required"):
        inspect_saved_reports(idx, root=None, epoch_id=EPOCH, as_of=ASOF.isoformat())


def test_s522_root_without_index_refused(tmp_path):
    with pytest.raises(Stage5HandoverError, match="root without index"):
        inspect_saved_reports(None, root=tmp_path, epoch_id=EPOCH, as_of=ASOF.isoformat())


def test_s522_five_local_snapshots_never_attest_external_claims(tmp_path):
    root, idx = snapshots(tmp_path)
    r = report_review(root, idx)
    assert r["locally_reconciled_report_count"] == 5
    assert all(x["local_digest_consistent"] for x in r["reports_by_batch"])
    assert all(x["independently_attested"] is False for x in r["reports_by_batch"])
    assert r["windows_regression_attested_by_this_inspector"] is False


def test_s522_partial_saved_reports_are_accepted_but_not_certified(tmp_path):
    root, idx = snapshots(tmp_path, batches=mod.BATCHES[:2])
    r = report_review(root, idx)
    assert r["locally_reconciled_report_count"] == 2
    assert r["independent_external_evidence_verified"] is False


def test_s522_file_tamper_fails_sha(tmp_path):
    root, idx = snapshots(tmp_path)
    edit_first_report(root, idx, lambda x: x.update(extra="tampered"), update_file_hash=False)
    with pytest.raises(Stage5HandoverError, match="byte SHA-256"):
        report_review(root, idx)


def test_s522_internal_report_hash_tamper_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    edit_first_report(root, idx, lambda x: x.update(test_fixture_synthetic_only=False))
    with pytest.raises(Stage5HandoverError, match="digest"):
        report_review(root, idx)


@pytest.mark.parametrize("change", [
    {"final_action": "BUY"},
    {"read_only": False},
    {"assessment_version": "WRONG"},
    {"epoch_id": "f" * 64},
    {"as_of": (ASOF + timedelta(seconds=1)).isoformat()},
])
def test_s522_bad_report_claims_refused(tmp_path, change):
    root, idx = snapshots(tmp_path)
    def update(record):
        record.update(change)
        record["report_sha256"] = _digest({k: v for k, v in record.items() if k != "report_sha256"})
    edit_first_report(root, idx, update)
    with pytest.raises(Stage5HandoverError, match="contract"):
        report_review(root, idx)


def test_s522_index_epoch_mismatch_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    rewrite_index(idx, lambda x: x.update(epoch_id="a" * 64))
    with pytest.raises(Stage5HandoverError, match="scope"):
        report_review(root, idx)


def test_s522_index_asof_mismatch_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    rewrite_index(idx, lambda x: x.update(as_of=(ASOF - timedelta(seconds=1)).isoformat()))
    with pytest.raises(Stage5HandoverError, match="scope"):
        report_review(root, idx)


def test_s522_boolean_index_version_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    rewrite_index(idx, lambda x: x.update(schema_version=True))
    with pytest.raises(Stage5HandoverError, match="contract"):
        report_review(root, idx)


def test_s522_duplicate_report_batch_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    rewrite_index(idx, lambda x: x["entries"].__setitem__(1, x["entries"][0].copy()))
    with pytest.raises(Stage5HandoverError, match="duplicate"):
        report_review(root, idx)


def test_s522_unknown_report_batch_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    rewrite_index(idx, lambda x: x["entries"][0].update(batch="S5-UNKNOWN"))
    with pytest.raises(Stage5HandoverError, match="Unknown"):
        report_review(root, idx)


def test_s522_invalid_file_digest_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    rewrite_index(idx, lambda x: x["entries"][0].update(file_sha256="bad"))
    with pytest.raises(Stage5HandoverError, match="digest"):
        report_review(root, idx)


def test_s522_more_than_five_reports_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    rewrite_index(idx, lambda x: x["entries"].append(x["entries"][0].copy()))
    with pytest.raises(Stage5HandoverError, match="count"):
        report_review(root, idx)


def test_s522_duplicate_json_key_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    idx.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(Stage5HandoverError, match="Duplicate"):
        report_review(root, idx)


@pytest.mark.parametrize("relative", [
    "../secret", "/absolute", "C:/system", "dir/../secret",
    "file\\shortcut", "a//b", "./file",
])
def test_s522_unsafe_path_refused(tmp_path, relative):
    root, idx = snapshots(tmp_path)
    rewrite_index(idx, lambda x: x["entries"][0].update(relative_path=relative))
    with pytest.raises(Stage5HandoverError, match="path"):
        report_review(root, idx)


def test_s522_symlink_refused(tmp_path):
    root, idx = snapshots(tmp_path)
    target = root / mod.BATCHES[0][0]
    link = root / "link.json"
    try:
        link.symlink_to(root / (mod.BATCHES[0][0] + ".json"))
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    rewrite_index(idx, lambda x: x["entries"][0].update(relative_path=link.name))
    with pytest.raises(Stage5HandoverError, match="Symlinked"):
        report_review(root, idx)


def test_s523_all_eleven_external_gates_remain_unverified(repo):
    r = assess(repo)
    assert len(r["external_gates"]) == 11
    assert r["external_gates_independently_verified"] == 0
    assert all(x["approved"] is False for x in r["external_gates"])
    assert r["research_ready"] is False
    assert r["operational_readiness"] == "BLOCKED_EXTERNAL_EVIDENCE"


def test_s523_source_families_and_prospective_evidence_not_certified(repo):
    r = assess(repo)
    assert r["council_source_families_independently_verified"] == 0
    assert r["prospective_evidence_gates_independently_verified"] == 0
    assert r["net_of_costs_performance_verified"] is False
    assert r["final_action"] == "HOLD"


def test_s523_complete_local_saved_reports_still_not_admission(repo, tmp_path):
    root, idx = snapshots(tmp_path)
    r = assess(repo, report_index=idx, report_root=root)
    assert r["optional_prior_report_snapshots"]["locally_reconciled_report_count"] == 5
    assert r["research_ready"] is False
    assert r["external_gates_independently_verified"] == 0
    assert r["final_action"] == "HOLD"


def test_s523_git_blocker_propagates_to_handover(repo):
    (repo / mod.BATCHES[3][2]).write_text("dirty")
    r = assess(repo)
    assert r["engineering_result"] == "PRIOR_STAGE5_GIT_CHAIN_BLOCKED"
    assert "FIVE_BATCH_GIT_CHAIN_NOT_LOCALLY_CONSISTENT" in r["blockers"]


def test_s523_research_first_workstreams_keep_paper_and_live_blocked(repo):
    r = assess(repo)
    assert any("PROSPECTIVE" not in x for x in r["next_research_first_workstreams"])
    assert "KEEP_LIVE_BROKER_EXECUTION_DISABLED_PENDING_SEPARATE_APPROVAL" in r["next_research_first_workstreams"]
    assert r["paper_execution_authorized"] is False
    assert r["live_execution_authorized"] is False


def test_s524_final_report_hash_deterministic(repo):
    a, b = assess(repo), assess(repo)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_s524_20_prior_and_four_pending(repo):
    r = assess(repo)
    assert r["stage5_prior_checkpoints_locally_verified"] == 20
    assert r["stage5_final_four_checkpoints_pending_windows_verification_and_commit"] is True
    assert r["engineering_result"] == "PRIOR_20_GIT_CHECKPOINTS_VERIFIED_PENDING_FINAL_WINDOWS_TESTS"


def test_s524_no_operational_actions(repo):
    r = assess(repo)
    for key in (
        "files_written", "database_accessed", "network_accessed",
        "broker_connected", "collector_started", "historical_epoch_rebound",
        "council_invoked", "settlement_executed", "training_authorized",
        "execution_authorized", "paper_execution_authorized", "live_execution_authorized",
    ):
        assert r[key] is False
    assert r["read_only"] is True and r["final_action"] == "HOLD"


def test_s524_cli_requires_three_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", ASOF.isoformat()])
    assert exc.value.code == 2


def test_s524_cli_bad_epoch_fails_before_git(capsys):
    result = run([
        "--read-only-git", "--read-only-source", "--read-only-evidence",
        "--repo", "missing", "--epoch-id", "BAD", "--as-of", ASOF.isoformat(),
    ])
    assert result == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_s524_cli_json_report_final_hold(repo, capsys):
    code = run([
        "--read-only-git", "--read-only-source", "--read-only-evidence",
        "--repo", str(repo), "--epoch-id", EPOCH, "--as-of", ASOF.isoformat(), "--json",
    ])
    assert code == 0
    r = json.loads(capsys.readouterr().out)
    assert r["stage5_prior_checkpoints_locally_verified"] == 20
    assert r["final_action"] == "HOLD"


@pytest.mark.parametrize("stamp", [
    "2026-10-30T09:00:00", "bad", "2026-10-30T12:00:00+03:00",
])
def test_s524_strict_utc(stamp):
    with pytest.raises(Stage5HandoverError):
        _utc(stamp)
