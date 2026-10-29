
"""Synthetic S4-21..24 Git/evidence handover tests. No broker or network."""

from datetime import datetime, timedelta, timezone
import json
import subprocess

import pytest

from backend.runtime.stage4_final_handover import (
    BATCHES, BRANCH, EXTERNAL_GATES, REPORT_VERSIONS,
    Stage4HandoverError, _hash, _utc,
    assess_stage4_handover, inspect_git_anchors,
    inspect_optional_reports, run,
)

EPOCH = "7" * 64
ASOF = datetime(2026, 10, 29, 21, 30, tzinfo=timezone.utc)


def _cmd(repo, *args):
    return subprocess.check_output(
        ["git", *args], cwd=repo, stderr=subprocess.PIPE,
    ).decode("utf-8").strip()


def _fixture_repo(base):
    base.mkdir()
    _cmd(base, "init", "-q")
    _cmd(base, "config", "core.autocrlf", "false")
    _cmd(base, "config", "user.name", "Stage4 Fixture")
    _cmd(base, "config", "user.email", "stage4@example.invalid")
    _cmd(base, "checkout", "-q", "-b", BRANCH)
    (base / "README.md").write_text("Synthetic root commit\n", encoding="utf-8")
    _cmd(base, "add", "--", "README.md")
    _cmd(base, "commit", "-qm", "root fixture")
    for name, _, stem in BATCHES:
        paths = (
            f"backend/runtime/{stem}.py",
            f"backend/runtime/test_{stem}.py",
        )
        for path in paths:
            target = base / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "# SYNTHETIC FIXTURE, NOT PROJECT SOURCE\n"
                "def fixture_only():\n    return " + repr(name) + "\n",
                encoding="utf-8",
            )
        _cmd(base, "add", "--", *paths)
        _cmd(base, "commit", "-qm", "fixture " + name)
    _cmd(base, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD")
    return base


@pytest.fixture
def repo(tmp_path):
    return _fixture_repo(tmp_path / "repo")


def _patched_anchors(monkeypatch, repo):
    """Use fixture commits, not the real project's five immutable hashes."""
    from backend.runtime import stage4_final_handover as module
    rows = []
    history = _cmd(repo, "rev-list", "--reverse", "HEAD").splitlines()
    for (name, _original, stem), revision in zip(BATCHES, history[1:]):
        rows.append((name, revision[:7], stem))
    monkeypatch.setattr(module, "BATCHES", tuple(rows))
    return rows


def _assess(repo):
    return assess_stage4_handover(
        repo=repo, epoch_id=EPOCH, as_of=ASOF.isoformat(),
    )


def _sample_report(tmp_path, batch, **changes):
    data = {
        "assessment_version": REPORT_VERSIONS.get(batch, "FORensics_V1"),
        "epoch_id": EPOCH,
        "as_of": ASOF.isoformat(),
        "final_action": "HOLD",
        "read_only": True,
        "collector_started": False,
        "council_invoked": False,
        "training_authorized": False,
        "execution_authorized": False,
        "independently_verified_family_count": 0,
        "external_gates_independently_verified": 0,
    }
    if batch == "s4_13_16":
        data.pop("epoch_id")
    data.update(changes)
    data["report_sha256"] = _hash(data)
    file = tmp_path / (batch + ".json")
    file.write_text(json.dumps(data), encoding="utf-8")
    return file


def test_s421_explicit_11_external_gates():
    assert len(EXTERNAL_GATES) == 11
    assert len(set(EXTERNAL_GATES)) == len(EXTERNAL_GATES)
    assert "EXTERNALLY_TIMESTAMPED_PREDECISION_PREDICTIONS" in EXTERNAL_GATES


def test_s421_no_reports_supplied_does_not_invent_evidence():
    x = inspect_optional_reports({}, epoch_id=EPOCH, as_of=ASOF)
    assert len(x) == 5
    assert all(row["status"] == "NOT_SUPPLIED" for row in x)
    assert all(row["independently_attested"] is False for row in x)


def test_s421_local_report_hash_valid_not_independent(tmp_path):
    f = _sample_report(tmp_path, "s4_17_20")
    result = inspect_optional_reports({"s4_17_20": f}, epoch_id=EPOCH, as_of=ASOF)
    assert result[-1]["status"] == "LOCAL_REPORT_HASH_LINKED_UNATTESTED"
    assert result[-1]["independently_attested"] is False


def test_s421_all_five_optional_reports_preserve_hold(tmp_path):
    files = {name: _sample_report(tmp_path, name) for name, _, _ in BATCHES}
    result = inspect_optional_reports(files, epoch_id=EPOCH, as_of=ASOF)
    assert len([x for x in result if x["file_sha256"] is not None]) == 5
    assert all(x["independently_attested"] is False for x in result)


def test_s421_tampered_report_digest_rejected(tmp_path):
    f = _sample_report(tmp_path, "s4_09_12")
    data = json.loads(f.read_text())
    data["read_only"] = False
    f.write_text(json.dumps(data))
    with pytest.raises(Stage4HandoverError, match="SHA-256"):
        inspect_optional_reports({"s4_09_12": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_rehashed_nonhold_report_rejected(tmp_path):
    f = _sample_report(tmp_path, "s4_05_08", final_action="TRADE")
    with pytest.raises(Stage4HandoverError, match="HOLD"):
        inspect_optional_reports({"s4_05_08": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_rehashed_execution_flag_rejected(tmp_path):
    f = _sample_report(tmp_path, "s4_17_20", execution_authorized=True)
    with pytest.raises(Stage4HandoverError, match="contradicts"):
        inspect_optional_reports({"s4_17_20": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_wrong_report_epoch_rejected(tmp_path):
    f = _sample_report(tmp_path, "s4_09_12", epoch_id="a" * 64)
    with pytest.raises(Stage4HandoverError, match="epoch"):
        inspect_optional_reports({"s4_09_12": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_wrong_version_rejected(tmp_path):
    f = _sample_report(tmp_path, "s4_17_20", assessment_version="WRONG")
    with pytest.raises(Stage4HandoverError, match="version"):
        inspect_optional_reports({"s4_17_20": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_future_asof_rejected(tmp_path):
    f = _sample_report(tmp_path, "s4_13_16", as_of=(ASOF + timedelta(seconds=1)).isoformat())
    with pytest.raises(Stage4HandoverError, match="cutoff"):
        inspect_optional_reports({"s4_13_16": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_council_independent_claim_rejected(tmp_path):
    f = _sample_report(tmp_path, "s4_13_16", independently_verified_family_count=1)
    with pytest.raises(Stage4HandoverError, match="Council independent"):
        inspect_optional_reports({"s4_13_16": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_prospective_external_claim_rejected(tmp_path):
    f = _sample_report(tmp_path, "s4_17_20", external_gates_independently_verified=1)
    with pytest.raises(Stage4HandoverError, match="Prospective external"):
        inspect_optional_reports({"s4_17_20": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_unknown_report_scope_rejected(tmp_path):
    with pytest.raises(Stage4HandoverError, match="Unknown"):
        inspect_optional_reports({"s4_25_28": tmp_path / "never"}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_duplicate_json_key_rejected(tmp_path):
    f = tmp_path / "dup.json"
    f.write_text('{"read_only":true,"read_only":false}')
    with pytest.raises(Stage4HandoverError, match="Duplicate"):
        inspect_optional_reports({"s4_01_04": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s421_oversize_report_rejected(tmp_path):
    f = tmp_path / "large.json"
    f.write_bytes(b"x" * 262145)
    with pytest.raises(Stage4HandoverError, match="size"):
        inspect_optional_reports({"s4_01_04": f}, epoch_id=EPOCH, as_of=ASOF)


def test_s422_five_git_anchors_verified_without_writes(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    before = _cmd(repo, "status", "--porcelain")
    g = inspect_git_anchors(repo)
    assert g["verified_prior_batches"] == 5
    assert g["head_equals_local_remote_tracking"] is True
    assert g["previous_checkpoint_files_clean"] is True
    assert all(x["exact_two_files_in_commit"] for x in g["anchors"])
    assert _cmd(repo, "status", "--porcelain") == before


def test_s422_local_remote_tracking_mismatch_detected(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    previous = _cmd(repo, "rev-parse", "HEAD~1")
    _cmd(repo, "update-ref", "refs/remotes/origin/" + BRANCH, previous)
    g = inspect_git_anchors(repo)
    assert g["head_equals_local_remote_tracking"] is False


def test_s422_dirty_previous_source_detected(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    stem = BATCHES[0][2]
    (repo / f"backend/runtime/{stem}.py").write_text("# modified locally\n")
    g = inspect_git_anchors(repo)
    assert g["previous_checkpoint_files_clean"] is False
    assert g["verified_prior_batches"] == 5


def test_s422_previous_committed_source_modified_at_head(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    stem = BATCHES[0][2]
    path = f"backend/runtime/{stem}.py"
    (repo / path).write_text("# modified in later commit\n")
    _cmd(repo, "add", "--", path)
    _cmd(repo, "commit", "-qm", "illicit later modification")
    _cmd(repo, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD")
    g = inspect_git_anchors(repo)
    assert g["verified_prior_batches"] == 4
    assert g["anchors"][0]["files_unchanged_at_head"] is False


def test_s422_wrong_branch_fails_closed(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    _cmd(repo, "checkout", "-q", "-b", "different")
    with pytest.raises(Stage4HandoverError, match="branch"):
        inspect_git_anchors(repo)


def test_s422_missing_git_repo_fails_closed(tmp_path):
    p = tmp_path / "empty"
    p.mkdir()
    with pytest.raises(Stage4HandoverError):
        inspect_git_anchors(p)


def test_s423_local_engineering_ready_but_operational_hold(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    r = _assess(repo)
    assert r["prior_committed_checkpoints_checked"] == 20
    assert r["engineering_status"].startswith("PRIOR_20_GIT_CHECKPOINTS_VERIFIED")
    assert r["operational_readiness"] == "BLOCKED_EXTERNAL_EVIDENCE"
    assert r["final_action"] == "HOLD"


def test_s423_missing_optional_reports_is_a_blocker(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    r = _assess(repo)
    assert r["locally_linked_reports"] == 0
    assert "NOT_ALL_PRIOR_STAGE4_JSON_REPORTS_SUPPLIED" in r["blockers"]


def test_s423_unchanged_fixed_inputs_produce_same_sha(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    a, b = _assess(repo), _assess(repo)
    assert a == b
    assert a["report_sha256"] == _hash({k: v for k, v in a.items() if k != "report_sha256"})


def test_s423_remote_tracking_mismatch_blocks_local_acceptance(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    _cmd(repo, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD~1")
    r = _assess(repo)
    assert r["engineering_status"] == "LOCAL_CHECKPOINT_RECONCILIATION_BLOCKED"
    assert "HEAD_NOT_EQUAL_TO_LOCAL_REMOTE_TRACKING_REF" in r["blockers"]


def test_s423_all_external_requirements_still_block(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    r = _assess(repo)
    assert r["independently_verified_external_gates"] == 0
    for gate in EXTERNAL_GATES:
        assert "EXTERNAL_EVIDENCE_REQUIRED:" + gate in r["blockers"]


def test_s424_readonly_actions_never_authorized(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    r = _assess(repo)
    for field in (
        "database_accessed", "broker_connected", "network_accessed", "files_modified",
        "git_mutated", "collector_started", "council_invoked", "predictions_registered",
        "training_authorized", "execution_authorized", "real_forward_council_admission",
    ):
        assert r[field] is False
    assert r["read_only"] is True
    assert r["final_action"] == "HOLD"


def test_s424_no_regression_log_or_operational_approval_fabricated(repo, monkeypatch):
    _patched_anchors(monkeypatch, repo)
    r = _assess(repo)
    assert "WINDOWS_TESTS" in r["engineering_status"]
    assert "COMPLETE_FINAL_STAGE4_WINDOWS_REGRESSION_AND_REAL_READONLY_INSPECTION" in r["handover_actions"]


def test_s424_cli_requires_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", ASOF.isoformat()])
    assert exc.value.code == 2


def test_s424_cli_invalid_epoch_blocks_before_git(capsys):
    assert run([
        "--read-only-git", "--read-only-evidence", "--epoch-id", "wrong",
        "--as-of", ASOF.isoformat(), "--repo", "not-an-existing-repo",
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_s424_cli_duplicate_report_arg_fails_closed(capsys):
    assert run([
        "--read-only-git", "--read-only-evidence", "--epoch-id", EPOCH,
        "--as-of", ASOF.isoformat(), "--report", "s4_01_04=x",
        "--report", "s4_01_04=y",
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_s424_cli_outputs_structured_verified_report(repo, monkeypatch, capsys):
    _patched_anchors(monkeypatch, repo)
    assert run([
        "--read-only-git", "--read-only-evidence", "--epoch-id", EPOCH,
        "--as-of", ASOF.isoformat(), "--repo", str(repo), "--json",
    ]) == 0
    x = json.loads(capsys.readouterr().out)
    assert x["report_sha256"] == _hash({k: v for k, v in x.items() if k != "report_sha256"})
    assert x["git_verification"]["verified_prior_batches"] == 5
    assert x["final_action"] == "HOLD"


@pytest.mark.parametrize("timestamp", [
    "2026-10-29T21:30:00", "INVALID", "2026-10-30T00:30:00+03:00",
])
def test_s424_non_utc_timestamps_rejected(timestamp):
    with pytest.raises(Stage4HandoverError):
        _utc(timestamp)
