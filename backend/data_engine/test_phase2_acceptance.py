
"""E4 acceptance regressions; synthetic SQLite feed and temporary Git only."""

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import event

from backend.data_engine.phase2_acceptance import (
    CHECKPOINTS, EXTERNAL_GATES, Phase2AcceptanceError, _digest,
    audit_phase2_git, evaluate_phase2_acceptance, inspect_phase2_acceptance,
    run,
)
from backend.data_engine.forward_research_evaluation import inspect_forward_research
from backend.data_engine.test_forward_cycle_readiness import gitrepo, _git
from backend.data_engine.test_forward_evidence_health import make_fixture


@pytest.fixture
def demo():
    engine, policy, cutoff = make_fixture()
    try:
        yield engine, policy, cutoff
    finally:
        engine.dispose()


@pytest.fixture
def repo_e4(gitrepo):
    """Commit five fake work batches inside a strictly isolated local repo."""
    repo = gitrepo[0]
    expected = []
    for index in range(5):
        files = (
            f"backend/data_engine/test_e4_demo_{index}_a.py",
            f"backend/data_engine/test_e4_demo_{index}_b.py",
        )
        for name in files:
            path = repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# test-only {index}\n", encoding="utf-8")
        _git(repo, "add", "--", *files)
        _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.org",
             "commit", "-qm", f"checkpoint {index}")
        expected.append((f"FAKE_BATCH_{index}", _git(repo, "rev-parse", "HEAD"), files))
    branch = _git(repo, "branch", "--show-current")
    return repo, tuple(expected), branch


def _research(demo, repo_e4):
    engine, policy, cutoff = demo
    return inspect_forward_research(engine, epoch_id=policy.epoch_id,
                                    as_of=cutoff, repo=repo_e4[0])


def _git_audit(repo_e4):
    repo, expected, branch = repo_e4
    return audit_phase2_git(repo, checkpoints=expected, expected_branch=branch)


def _review(demo, repo_e4):
    return evaluate_phase2_acceptance(_research(demo, repo_e4), _git_audit(repo_e4))


def test_e4_git_ancestry_includes_all_five_local_commits(repo_e4):
    report = _git_audit(repo_e4)
    assert report["status"] == "VERIFIED_LOCAL_CHECKPOINT_ANCESTRY"
    assert report["checkpoints_verified"] == report["checkpoint_count"] == 5
    assert all(len(row["tracked_files_present"]) == 2 for row in report["checkpoints"])
    assert report["remote_push_independently_verified"] is False


def test_e4_reports_absent_git_checkpoint_without_claiming_success(repo_e4):
    repo, expected, branch = repo_e4
    bad = list(expected)
    name, _, paths = bad[-1]
    bad[-1] = (name, "0" * 40, paths)
    report = audit_phase2_git(repo, checkpoints=bad, expected_branch=branch)
    assert report["checkpoints_verified"] == 4
    assert report["status"] == "INCOMPLETE_LOCAL_CHECKPOINT_EVIDENCE"


def test_e4_reports_wrong_branch(repo_e4):
    repo, expected, _ = repo_e4
    report = audit_phase2_git(repo, checkpoints=expected, expected_branch="missing/branch")
    assert report["checkpoints_verified"] == 5
    assert report["branch_matches"] is False
    assert report["status"] == "INCOMPLETE_LOCAL_CHECKPOINT_EVIDENCE"


def test_e4_missing_tracked_file_blocks_checkpoint(repo_e4):
    repo, expected, branch = repo_e4
    broken = list(expected)
    label, commit, paths = broken[0]
    broken[0] = (label, commit, (paths[0], "backend/missing.py"))
    result = audit_phase2_git(repo, checkpoints=broken, expected_branch=branch)
    assert result["checkpoints_verified"] == 4


def test_e4_git_queries_leave_worktree_and_head_unchanged(repo_e4):
    repo, _, _ = repo_e4
    before = (_git(repo, "rev-parse", "HEAD"), _git(repo, "status", "--porcelain"))
    _git_audit(repo_e4)
    after = (_git(repo, "rev-parse", "HEAD"), _git(repo, "status", "--porcelain"))
    assert before == after


def test_e4_final_engineering_acceptance_does_not_admit_real_feed(demo, repo_e4):
    result = _review(demo, repo_e4)
    assert result["engineering_acceptance"] == "ACCEPTED_WITH_EXTERNAL_BLOCKERS"
    assert result["operational_readiness"] == "BLOCKED_UNVERIFIED_EXTERNAL_EVIDENCE"
    assert result["phase2_committed_before_e4"] == 19
    assert result["e4_technical_review_performed"] is True
    assert result["council_seats_run"] == 0
    assert result["training_authorized"] is False
    assert result["execution_authorized"] is False
    assert result["final_action"] == "HOLD"


def test_e4_all_external_requirements_remain_unattested(demo, repo_e4):
    result = _review(demo, repo_e4)
    assert len(EXTERNAL_GATES) == 9
    assert result["external_gate_count"] == 9
    assert all(row["verification"] == "NOT_ESTABLISHED_BY_PHASE2_REVIEW"
               for row in result["external_evidence_gates"])
    assert result["council_source_families_unverified"] == 13
    assert result["performance_measured"] is False
    assert result["transaction_costs_measured"] is False


def test_e4_incomplete_git_evidence_prevents_engineering_acceptance(demo, repo_e4):
    research = _research(demo, repo_e4)
    repo, expected, branch = repo_e4
    bad = list(expected)
    label, _, paths = bad[1]
    bad[1] = (label, "0" * 40, paths)
    audit = audit_phase2_git(repo, checkpoints=bad, expected_branch=branch)
    result = evaluate_phase2_acceptance(research, audit)
    assert result["engineering_acceptance"] == "BLOCKED_INCOMPLETE_LOCAL_GIT_EVIDENCE"
    assert result["phase2_committed_before_e4"] is None
    assert result["final_action"] == "HOLD"


def test_e4_research_report_tampering_rejected(demo, repo_e4):
    research = _research(demo, repo_e4)
    research["epoch_id"] = "f" * 64
    with pytest.raises(Phase2AcceptanceError, match="integrity"):
        evaluate_phase2_acceptance(research, _git_audit(repo_e4))


@pytest.mark.parametrize("field,value", [
    ("final_action", "BUY"),
    ("training_authorized", True),
    ("real_forward_admission", True),
    ("council_seats_run", 31),
    ("measured_predictive_performance", 0.88),
    ("measured_transaction_costs", 1.0),
    ("unverified_council_input_count", 0),
])
def test_e4_refuses_forged_operational_claims(demo, repo_e4, field, value):
    research = _research(demo, repo_e4)
    research[field] = value
    research["report_sha256"] = _digest({
        k: v for k, v in research.items() if k != "report_sha256"
    })
    with pytest.raises(Phase2AcceptanceError):
        evaluate_phase2_acceptance(research, _git_audit(repo_e4))


def test_e4_report_deterministic_with_identical_cutoff(demo, repo_e4):
    first = _review(demo, repo_e4)
    second = _review(demo, repo_e4)
    assert first == second
    assert first["report_sha256"] == _digest({
        k: v for k, v in first.items() if k != "report_sha256"
    })


def test_e4_older_capture_cutoff_remains_operationally_blocked(demo, repo_e4):
    engine, policy, cutoff = demo
    result = inspect_phase2_acceptance(
        engine, epoch_id=policy.epoch_id, as_of=cutoff + timedelta(minutes=20),
        repo=repo_e4[0], checkpoints=repo_e4[1], expected_branch=repo_e4[2],
    )
    assert result["forward_feed_local_timing"] == "BLOCKED"
    assert result["operational_readiness"] == "BLOCKED_UNVERIFIED_EXTERNAL_EVIDENCE"
    assert "LATEST_TICK_NOT_CURRENT" in result["research_blockers"]


def test_e4_complete_inspection_select_only_in_database(demo, repo_e4):
    engine, policy, cutoff = demo
    statements = []

    def track(_conn, _cursor, sql, _params, _ctx, _many):
        statements.append(sql.strip().upper())

    event.listen(engine, "before_cursor_execute", track)
    try:
        inspect_phase2_acceptance(
            engine, epoch_id=policy.epoch_id, as_of=cutoff,
            repo=repo_e4[0], checkpoints=repo_e4[1], expected_branch=repo_e4[2],
        )
    finally:
        event.remove(engine, "before_cursor_execute", track)
    assert statements and all(statement.startswith("SELECT") for statement in statements)


def test_e4_cli_requires_both_explicit_read_only_flags():
    for args in ([], ["--read-only-db"], ["--git-read-only"]):
        with pytest.raises(SystemExit) as exc:
            run(args)
        assert exc.value.code == 2


def test_e4_bad_epoch_rejected_before_database_connect(capsys):
    code = run(["--read-only-db", "--git-read-only", "--epoch-id", "bad",
                "--as-of", "2026-10-27T14:00:00Z"])
    assert code == 1
    assert "PHASE 2 ACCEPTANCE BLOCKED" in capsys.readouterr().out


def test_e4_expected_prior_commits_and_file_inventory():
    assert len(CHECKPOINTS) == 5
    assert CHECKPOINTS[-1][1] == "04eebb4"
    assert len({commit for _, commit, _ in CHECKPOINTS}) == 5
    assert all(len(files) == 2 for _, _, files in CHECKPOINTS)
