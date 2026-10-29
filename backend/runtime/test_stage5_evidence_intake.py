
"""S5-01..04 synthetic intake tests. No broker, database or network."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from backend.runtime.stage5_evidence_intake import (
    BRANCH, GATES, LAST_STAGE4_FILES, Stage5IntakeError,
    _hash, _utc, assess_stage5_intake, inspect_candidate_index,
    inspect_stage4_baseline, run,
)

EPOCH = "7" * 64
ASOF = datetime(2026, 10, 29, 21, 30, tzinfo=timezone.utc)


def git(repo, *args):
    return subprocess.check_output(["git", *args], cwd=repo).decode().strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "core.autocrlf", "false")
    git(root, "config", "user.name", "Fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "checkout", "-q", "-b", BRANCH)
    (root / "README.md").write_text("synthetic root\n")
    git(root, "add", "--", "README.md")
    git(root, "commit", "-qm", "root")
    for p in LAST_STAGE4_FILES:
        file = root / p
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("# synthetic Stage 4 test fixture\n")
    git(root, "add", "--", *LAST_STAGE4_FILES)
    git(root, "commit", "-qm", "stage4 final synthetic")
    anchor = git(root, "rev-parse", "HEAD")
    git(root, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD")
    return root, anchor


def item(root, *, gate=None, identity="fixture-1", path="source.txt"):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(b"SYNTHETIC SOURCE ONLY")
    return {
        "gate": gate or GATES[0][0],
        "candidate_id": identity,
        "relative_path": path,
        "file_sha256": sha256(file.read_bytes()).hexdigest(),
        "source_category": "broker_export",
        "claimed_issuer": "SYNTHETIC-NOT-BROKER",
        "claimed_issued_at": (ASOF - timedelta(hours=2)).isoformat(),
        "claimed_received_at": (ASOF - timedelta(hours=1)).isoformat(),
    }


def index(tmp_path, records, *, epoch=EPOCH, cutoff=ASOF):
    p = tmp_path / "intake-index.json"
    p.write_text(json.dumps({
        "schema_version": 1, "epoch_id": epoch,
        "as_of": cutoff.isoformat(), "records": records,
    }))
    return p


def assess(repo, **options):
    path, anchor = repo
    return assess_stage5_intake(
        repo=path, expected_anchor=anchor,
        epoch_id=EPOCH, as_of=ASOF.isoformat(), **options,
    )


def test_s501_stage4_anchor_validated_without_git_changes(repo):
    root, anchor = repo
    before = git(root, "status", "--porcelain")
    r = inspect_stage4_baseline(root, expected_anchor=anchor)
    assert r["baseline_locally_consistent"] is True
    assert r["anchor_is_head_ancestor"] is True
    assert r["anchor_commit_has_exact_files"] is True
    assert r["origin_contacted"] is False
    assert git(root, "status", "--porcelain") == before


def test_s501_different_branch_fails(repo):
    root, anchor = repo
    git(root, "checkout", "-q", "-b", "different")
    with pytest.raises(Stage5IntakeError, match="branch"):
        inspect_stage4_baseline(root, expected_anchor=anchor)


def test_s501_missing_repository_fails(tmp_path):
    p = tmp_path / "empty"
    p.mkdir()
    with pytest.raises(Stage5IntakeError):
        inspect_stage4_baseline(p)


def test_s501_bad_anchor_rejected(repo):
    with pytest.raises(Stage5IntakeError, match="anchor"):
        inspect_stage4_baseline(repo[0], expected_anchor="not-a-hash")


def test_s501_dirty_prior_file_blocks(repo):
    root, anchor = repo
    (root / LAST_STAGE4_FILES[0]).write_text("# dirty\n")
    result = inspect_stage4_baseline(root, expected_anchor=anchor)
    assert result["anchored_files_clean"] is False
    assert result["baseline_locally_consistent"] is False


def test_s501_tracking_ref_mismatch_blocks(repo):
    root, anchor = repo
    git(root, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD~1")
    r = inspect_stage4_baseline(root, expected_anchor=anchor)
    assert r["local_remote_tracking_matches_head"] is False
    assert r["baseline_locally_consistent"] is False


def test_s501_later_commit_keeps_anchor_valid(repo):
    root, anchor = repo
    (root / "README.md").write_text("later README update\n")
    git(root, "add", "--", "README.md")
    git(root, "commit", "-qm", "later")
    git(root, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD")
    r = inspect_stage4_baseline(root, expected_anchor=anchor)
    assert r["anchor_is_head_ancestor"] is True
    assert r["baseline_locally_consistent"] is True


def test_s501_later_edit_of_anchor_file_is_blocked(repo):
    root, anchor = repo
    (root / LAST_STAGE4_FILES[0]).write_text("later tampered bytes\n")
    git(root, "add", "--", LAST_STAGE4_FILES[0])
    git(root, "commit", "-qm", "later modification")
    git(root, "update-ref", "refs/remotes/origin/" + BRANCH, "HEAD")
    r = inspect_stage4_baseline(root, expected_anchor=anchor)
    assert r["anchored_files_unchanged_at_head"] is False
    assert r["baseline_locally_consistent"] is False


def test_s502_no_index_means_no_supplied_candidates():
    r = inspect_candidate_index(None, root=None, epoch_id=EPOCH, as_of=ASOF)
    assert r["status"] == "NOT_SUPPLIED"
    assert r["candidate_count"] == 0
    assert r["independently_verified"] == 0


def test_s502_hash_matched_candidate_remains_unattested(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    record = item(root)
    p = index(tmp_path, [record])
    r = inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)
    assert r["candidate_count"] == 1
    assert r["counts"][GATES[0][0]] == 1
    assert r["status"] == "LOCAL_CANDIDATES_HASH_LINKED_UNATTESTED"
    assert r["independently_verified"] == 0


def test_s502_tampered_file_is_rejected(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    record = item(root)
    p = index(tmp_path, [record])
    (root / "source.txt").write_bytes(b"tampered")
    with pytest.raises(Stage5IntakeError, match="SHA-256 mismatch"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s502_bad_index_epoch_rejected(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    p = index(tmp_path, [item(root)], epoch="a" * 64)
    with pytest.raises(Stage5IntakeError, match="scope"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s502_index_asof_mismatch_rejected(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    p = index(tmp_path, [], cutoff=ASOF - timedelta(seconds=1))
    with pytest.raises(Stage5IntakeError, match="scope"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s502_wrong_evidence_root_rejected(tmp_path):
    with pytest.raises(Stage5IntakeError, match="root without index"):
        inspect_candidate_index(None, root=tmp_path, epoch_id=EPOCH, as_of=ASOF)


def test_s502_no_root_with_index_rejected(tmp_path):
    p = index(tmp_path, [])
    with pytest.raises(Stage5IntakeError, match="root required"):
        inspect_candidate_index(p, root=None, epoch_id=EPOCH, as_of=ASOF)


def test_s502_missing_file_not_created(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    record = item(root)
    (root / "source.txt").unlink()
    p = index(tmp_path, [record])
    with pytest.raises(Stage5IntakeError, match="regular"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)
    assert not (root / "source.txt").exists()


@pytest.mark.parametrize("bad", ["../escape", "folder/../escape", "C:/bad", "/absolute", "a\\b", "a//b", "./file"])
def test_s502_path_traversal_rejected(tmp_path, bad):
    root = tmp_path / "files"
    root.mkdir()
    record = item(root)
    record["relative_path"] = bad
    p = index(tmp_path, [record])
    with pytest.raises(Stage5IntakeError, match="path|traversal"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s503_claimed_future_receipt_rejected(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    record = item(root)
    record["claimed_received_at"] = (ASOF + timedelta(seconds=1)).isoformat()
    p = index(tmp_path, [record])
    with pytest.raises(Stage5IntakeError, match="chronology"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s503_claimed_issued_after_received_rejected(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    record = item(root)
    record["claimed_issued_at"] = ASOF.isoformat()
    p = index(tmp_path, [record])
    with pytest.raises(Stage5IntakeError, match="chronology"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s503_unknown_gate_rejected(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    p = index(tmp_path, [item(root, gate="FAKE")])
    with pytest.raises(Stage5IntakeError, match="Unknown"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s503_duplicate_id_rejected(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    a = item(root)
    b = item(root, gate=GATES[1][0], path="second.txt")
    p = index(tmp_path, [a, b])
    with pytest.raises(Stage5IntakeError, match="duplicate"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s503_untrusted_category_refused(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    r = item(root)
    r["source_category"] = "verified_broker"
    p = index(tmp_path, [r])
    with pytest.raises(Stage5IntakeError, match="source"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s503_duplicate_json_keys_rejected(tmp_path):
    p = tmp_path / "dup.json"
    p.write_text('{"schema_version":1,"schema_version":1}')
    root = tmp_path / "files"
    root.mkdir()
    with pytest.raises(Stage5IntakeError, match="Duplicate"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s503_boolean_schema_version_rejected(tmp_path):
    p = tmp_path / "invalid.json"
    p.write_text(json.dumps({
        "schema_version": True, "epoch_id": EPOCH,
        "as_of": ASOF.isoformat(), "records": [],
    }))
    root = tmp_path / "files"
    root.mkdir()
    with pytest.raises(Stage5IntakeError, match="scope"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s503_oversized_artifact_rejected(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    record = item(root)
    (root / "source.txt").write_bytes(b"x" * 4194305)
    record["file_sha256"] = sha256((root / "source.txt").read_bytes()).hexdigest()
    p = index(tmp_path, [record])
    with pytest.raises(Stage5IntakeError, match="size limit"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s503_symlinked_file_rejected(tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    record = item(root)
    link = root / "shortcut.txt"
    try:
        link.symlink_to(root / "source.txt")
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")
    record["relative_path"] = link.name
    p = index(tmp_path, [record])
    with pytest.raises(Stage5IntakeError, match="symlink"):
        inspect_candidate_index(p, root=root, epoch_id=EPOCH, as_of=ASOF)


def test_s504_eleven_external_gates_stay_hold(repo):
    result = assess(repo)
    assert len(result["gates"]) == 11
    assert result["external_gates_independently_verified"] == 0
    assert result["council_input_families_independently_verified_by_this_review"] == 0
    assert result["final_action"] == "HOLD"
    assert result["engineering_result"].startswith("LOCAL_INTAKE_PREFLIGHT_VALIDATED")


def test_s504_candidates_never_promoted(repo, tmp_path):
    root = tmp_path / "files"
    root.mkdir()
    p = index(tmp_path, [item(root)])
    result = assess(repo, candidate_index=p, evidence_root=root)
    assert result["candidate_index"]["candidate_count"] == 1
    assert result["gates"][0]["status"] == "LOCAL_CANDIDATE_UNATTESTED"
    assert result["gates"][0]["independently_verified"] is False
    assert result["real_forward_admission"] is False


def test_s504_deterministic_hash_for_same_local_scope(repo):
    first, second = assess(repo), assess(repo)
    assert first == second
    assert first["report_sha256"] == _hash({
        k: v for k, v in first.items() if k != "report_sha256"
    })


def test_s504_no_operational_actions(repo):
    result = assess(repo)
    for key in (
        "database_accessed", "network_accessed", "broker_connected", "files_written",
        "collector_started", "council_invoked", "predictions_registered",
        "training_authorized", "execution_authorized", "real_forward_admission",
    ):
        assert result[key] is False


def test_s504_tracked_changes_block_acceptance(repo):
    root, _ = repo
    (root / LAST_STAGE4_FILES[0]).write_text("dirty\n")
    result = assess(repo)
    assert result["engineering_result"] == "LOCAL_BASELINE_BLOCKED"
    assert "STAGE4_LOCAL_GIT_BASELINE_NOT_CONSISTENT" in result["blockers"]


def test_s504_cli_requires_readonly_flags():
    with pytest.raises(SystemExit) as error:
        run(["--epoch-id", EPOCH, "--as-of", ASOF.isoformat()])
    assert error.value.code == 2


def test_s504_cli_bad_id_fails_before_git(capsys):
    rc = run([
        "--read-only-git", "--read-only-evidence", "--repo", "missing",
        "--epoch-id", "wrong", "--as-of", ASOF.isoformat(),
    ])
    assert rc == 1
    assert "BLOCKED" in capsys.readouterr().out


@pytest.mark.parametrize("stamp", ["2026-10-29T21:30:00", "invalid", "2026-10-30T00:30:00+03:00"])
def test_s504_utc_offset_required(stamp):
    with pytest.raises(Stage5IntakeError):
        _utc(stamp)
