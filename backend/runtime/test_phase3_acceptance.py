
"""P3-21..24: synthetic read-only acceptance and temporary local Git history."""

from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import subprocess

import pytest
from sqlalchemy import event

from backend.data_engine.forward_store import hashed
import backend.runtime.phase3_acceptance as acceptance
from backend.runtime.forward_source_provenance import (
    inspect_forward_source_provenance,
)
from backend.runtime.forward_market_input_lineage import (
    inspect_market_input_lineage,
)
from backend.runtime.forward_prospective_evaluation import (
    inspect_evaluation_contract,
)
from backend.runtime.test_forward_source_provenance import _fixture


@pytest.fixture
def git_history(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout.decode("utf-8").strip()

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("checkout", "-qb", "feature/e4-tests")

    rows = []
    for index in range(6):
        paths = (
            f"backend/runtime/test_fake_{index}_a.py",
            f"backend/runtime/test_fake_{index}_b.py",
        )
        for file in paths:
            dest = root / file
            dest.parent.mkdir(
                parents=True, exist_ok=True
            )
            dest.write_text(
                "# isolated Git fixture\n"
            )

        git("add", "--", *paths)
        git(
            "-c", "user.name=Unit Test",
            "-c", "user.email=unit@example.org",
            "commit", "-qm", f"checkpoint-{index}",
        )

        rows.append((
            f"P3_CHECK_{index}",
            git("rev-parse", "HEAD")[:7],
            paths,
        ))

    monkeypatch.setattr(
        acceptance, "PRIOR_COMMITS", tuple(rows)
    )
    return root, tuple(rows)


@pytest.fixture
def proof(tmp_path, git_history):
    engine, policy, latest = _fixture(count=2)

    path = tmp_path / "policy.json"
    path.write_text(
        policy.model_dump_json(),
        encoding="utf-8",
    )
    digest = sha256(path.read_bytes()).hexdigest()
    decision = latest + timedelta(seconds=3)
    cutoff = latest + timedelta(seconds=130)

    try:
        source = inspect_forward_source_provenance(
            engine,
            epoch_id=policy.epoch_id,
            as_of=cutoff,
            policy_file=path,
            expected_policy_sha256=digest,
        )

        market = inspect_market_input_lineage(
            engine,
            epoch_id=policy.epoch_id,
            decision_at=decision,
            as_of=cutoff,
            policy_file=path,
            expected_policy_sha256=digest,
        )

        evaluation = inspect_evaluation_contract(market)

        git = acceptance.audit_phase3_git(
            git_history[0],
            checkpoints=git_history[1],
            expected_branch="feature/e4-tests",
        )

        yield (
            source, market, evaluation, git,
            engine, policy, path, digest, cutoff, decision,
        )
    finally:
        engine.dispose()


def accept(proof):
    return acceptance.evaluate_phase3_acceptance(
        *proof[:4]
    )


def resign(report):
    report["report_sha256"] = hashed({
        k: v
        for k, v in report.items()
        if k != "report_sha256"
    })


def test_p321_nine_independent_gates_never_attested(proof):
    report = accept(proof)

    assert len(acceptance.EXTERNAL_GATES) == 9
    assert report[
        "external_gates_verified_by_this_review"
    ] == 0
    assert all(
        item["verification"]
        == "NOT_ESTABLISHED_BY_THIS_REVIEW"
        for item in report["independent_external_gates"]
    )
    assert report[
        "council_input_families_independently_verified"
    ] == 0


def test_p322_local_source_prospective_blockers_are_reconciled(proof):
    report = accept(proof)

    assert report["source_counts_in_inspected_scope"] == {
        "captures": 2,
        "ticks": 2,
        "bars": 2,
    }
    assert (
        "EVALUATION:NO_REGISTRATIONS_IN_SUPPLIED_SCOPE"
        in report["local_review_blockers"]
    )
    assert (
        "EVALUATION:NO_POST_HORIZON_OUTCOMES_IN_SUPPLIED_SCOPE"
        in report["local_review_blockers"]
    )
    assert (
        report["operational_readiness"]
        == "BLOCKED_UNVERIFIED_EXTERNAL_EVIDENCE"
    )


def test_p323_all_six_local_git_anchors_and_files_verified(proof):
    report = accept(proof)

    assert report["local_prior_commits_verified"] == 6
    assert report["local_prior_commits_required"] == 6
    assert report[
        "phase3_previously_committed_checkpoints"
    ] == 20
    assert (
        report["remote_push_independently_verified"]
        is False
    )
    assert (
        report["full_windows_regression_run_by_inspector"]
        is False
    )


def test_p324_engineering_accepted_but_always_hold(proof):
    report = accept(proof)

    assert (
        report["engineering_acceptance"]
        == "ACCEPTED_WITH_EXTERNAL_BLOCKERS"
    )
    assert report["phase3_planned_checkpoints"] == 24
    assert report["p3_21_24_review_performed"] is True
    assert report["collector_started"] is False
    assert report["training_authorized"] is False
    assert report["execution_authorized"] is False
    assert report["council_seats_run"] == 0
    assert report["final_action"] == "HOLD"


def test_p324_repeat_evaluation_stable(proof):
    assert accept(proof) == accept(proof)

    report = accept(proof)
    assert report["report_sha256"] == hashed({
        k: v
        for k, v in report.items()
        if k != "report_sha256"
    })


def test_p323_read_only_git_does_not_change_head_or_index(
    proof, git_history
):
    root, spec = git_history

    def status(*args):
        return subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=True,
        ).stdout

    before = (
        status("rev-parse", "HEAD"),
        status("status", "--porcelain"),
    )

    acceptance.audit_phase3_git(
        root,
        checkpoints=spec,
        expected_branch="feature/e4-tests",
    )

    assert before == (
        status("rev-parse", "HEAD"),
        status("status", "--porcelain"),
    )


def test_p323_missing_revision_blocks_approval(
    proof, git_history
):
    root, spec = git_history
    changed = list(spec)
    label, _, files = changed[-1]
    changed[-1] = (label, "f" * 40, files)

    bad = acceptance.audit_phase3_git(
        root,
        checkpoints=changed,
        expected_branch="feature/e4-tests",
    )

    assert bad["status"] == "LOCAL_GIT_ANCESTRY_INCOMPLETE"
    assert bad["prior_commits_verified"] == 5


def test_p323_wrong_branch_blocks_approval(
    proof, git_history
):
    root, spec = git_history

    audit = acceptance.audit_phase3_git(
        root,
        checkpoints=spec,
        expected_branch="other",
    )

    result = acceptance.evaluate_phase3_acceptance(
        *proof[:3], audit
    )
    assert result[
        "engineering_acceptance"
    ].startswith("BLOCKED")
    assert result[
        "phase3_previously_committed_checkpoints"
    ] is None
    assert result["final_action"] == "HOLD"


def test_p323_missing_tracked_file_blocks_git_gate(
    proof, git_history
):
    root, spec = git_history
    changed = list(spec)
    name, commit, files = changed[0]

    changed[0] = (
        name, commit,
        (files[0], "backend/runtime/missing.py"),
    )

    audit = acceptance.audit_phase3_git(
        root,
        checkpoints=changed,
        expected_branch="feature/e4-tests",
    )
    assert audit["prior_commits_verified"] == 5


def test_p323_wrong_checkpoint_identity_rejected(proof):
    audit = dict(proof[3])
    audit["checkpoints"] = [
        dict(v) for v in audit["checkpoints"]
    ]
    audit["checkpoints"][0]["checkpoint"] = "FORGED"
    resign(audit)

    with pytest.raises(
        acceptance.Phase3AcceptanceError,
        match="anchors",
    ):
        acceptance.evaluate_phase3_acceptance(
            *proof[:3], audit
        )


def test_p323_git_digest_tampering_rejected(proof):
    audit = dict(proof[3])
    audit["branch"] = "forged"

    with pytest.raises(
        acceptance.Phase3AcceptanceError,
        match="Git",
    ):
        acceptance.evaluate_phase3_acceptance(
            *proof[:3], audit
        )


def test_p322_epoch_swap_rejected(proof):
    bad = dict(proof[0])
    bad["epoch_id"] = "f" * 64
    resign(bad)

    with pytest.raises(
        acceptance.Phase3AcceptanceError,
        match="Epoch",
    ):
        acceptance.evaluate_phase3_acceptance(
            bad, *proof[1:4]
        )


def test_p322_market_lineage_swap_rejected(proof):
    bad = dict(proof[2])
    bad["market_lineage_sha256"] = "b" * 64
    resign(bad)

    with pytest.raises(
        acceptance.Phase3AcceptanceError,
        match="not bound",
    ):
        acceptance.evaluate_phase3_acceptance(
            proof[0], proof[1], bad, proof[3]
        )


def test_p322_policy_hash_mismatch_rejected(proof):
    bad = dict(proof[1])
    bad["policy_copy"] = dict(bad["policy_copy"])
    bad["policy_copy"]["file_sha256"] = "e" * 64
    resign(bad)

    evaluation = dict(proof[2])
    evaluation["market_lineage_sha256"] = (
        bad["report_sha256"]
    )
    resign(evaluation)

    with pytest.raises(
        acceptance.Phase3AcceptanceError,
        match="Policy-file",
    ):
        acceptance.evaluate_phase3_acceptance(
            proof[0], bad, evaluation, proof[3]
        )


def test_p322_policy_verification_failure_blocks_engineering(proof):
    bad = dict(proof[0])
    bad["policy_file"] = dict(bad["policy_file"])
    bad["policy_file"]["canonical_epoch_match"] = False
    resign(bad)

    market = dict(proof[1])
    market["policy_copy"] = dict(bad["policy_file"])
    resign(market)

    evaluation = dict(proof[2])
    evaluation["market_lineage_sha256"] = (
        market["report_sha256"]
    )
    resign(evaluation)

    result = acceptance.evaluate_phase3_acceptance(
        bad, market, evaluation, proof[3]
    )
    assert result[
        "engineering_acceptance"
    ].startswith("BLOCKED")
    assert result["final_action"] == "HOLD"


def test_p324_training_flag_rejected_even_if_rehashed(proof):
    bad = dict(proof[2])
    bad["training_authorized"] = True
    resign(bad)

    with pytest.raises(
        acceptance.Phase3AcceptanceError,
        match="authorization",
    ):
        acceptance.evaluate_phase3_acceptance(
            proof[0], proof[1], bad, proof[3]
        )


def test_p324_fake_independent_broker_attestation_rejected(proof):
    bad = dict(proof[0])
    bad["broker_identity_fields"] = dict(
        bad["broker_identity_fields"]
    )
    bad["broker_identity_fields"][
        "independent_clock_offset_attested"
    ] = True
    resign(bad)

    with pytest.raises(
        acceptance.Phase3AcceptanceError,
        match="broker",
    ):
        acceptance.evaluate_phase3_acceptance(
            bad, *proof[1:4]
        )


def test_p324_fake_performance_rejected(proof):
    bad = dict(proof[2])
    bad["performance_measured"] = True
    resign(bad)

    with pytest.raises(
        acceptance.Phase3AcceptanceError,
        match="authorization",
    ):
        acceptance.evaluate_phase3_acceptance(
            proof[0], proof[1], bad, proof[3]
        )


def test_p322_unknown_local_source_error_blocks_engineering(proof):
    bad = dict(proof[0])
    bad["local_structural_blockers"] = [
        "BAD_SOURCE_ROW"
    ]
    resign(bad)

    result = acceptance.evaluate_phase3_acceptance(
        bad, *proof[1:4]
    )
    assert result[
        "engineering_acceptance"
    ].startswith("BLOCKED")


def test_p322_sample_truncation_blocks_engineering(proof):
    bad = dict(proof[0])
    bad["capture_sample"] = dict(
        bad["capture_sample"]
    )
    bad["capture_sample"]["sample_truncated"] = {
        "captures": True,
        "ticks": False,
        "bars": False,
    }
    resign(bad)

    result = acceptance.evaluate_phase3_acceptance(
        bad, *proof[1:4]
    )
    assert result[
        "engineering_acceptance"
    ].startswith("BLOCKED")


def test_p324_cli_requires_all_readonly_optins():
    for args in (
        [],
        ["--read-only-db"],
        ["--git-read-only"],
        ["--read-only-evidence"],
    ):
        with pytest.raises(SystemExit) as error:
            acceptance.run(args)
        assert error.value.code == 2


def test_p324_bad_epoch_fails_before_db_import(capsys):
    result = acceptance.run([
        "--read-only-db",
        "--git-read-only",
        "--read-only-evidence",
        "--epoch-id", "bad",
        "--as-of", "2026-10-27T15:05:00Z",
        "--decision-at", "2026-10-27T15:05:00Z",
        "--policy-file", "missing.json",
        "--expected-policy-sha256", "a" * 64,
    ])

    assert result == 1
    assert (
        "REVIEW BLOCKED"
        in capsys.readouterr().out
    )


def test_p324_integrated_inspection_sql_is_select_only(
    proof, git_history
):
    (
        source, market, evaluation, audit,
        engine, policy, path, digest, cutoff, decision,
    ) = proof
    queries = []

    def watch(
        _connection, _cursor, statement,
        _params, _ctx, _many
    ):
        queries.append(statement.strip().upper())

    event.listen(
        engine, "before_cursor_execute", watch
    )
    try:
        report = acceptance.inspect_phase3_acceptance(
            engine,
            epoch_id=policy.epoch_id,
            as_of=cutoff,
            decision_at=decision,
            policy_file=path,
            expected_policy_sha256=digest,
            repo=git_history[0],
            checkpoints=git_history[1],
            expected_branch="feature/e4-tests",
        )
    finally:
        event.remove(
            engine, "before_cursor_execute", watch
        )

    assert queries
    assert all(
        q.startswith("SELECT")
        for q in queries
    )
    assert report["final_action"] == "HOLD"
    assert (
        report["engineering_acceptance"]
        == "ACCEPTED_WITH_EXTERNAL_BLOCKERS"
    )


def test_p324_optional_evidence_not_created(
    proof, git_history, tmp_path
):
    missing = tmp_path / "missing.json"

    with pytest.raises(Exception):
        acceptance.inspect_phase3_acceptance(
            proof[4],
            epoch_id=proof[5].epoch_id,
            as_of=proof[8],
            decision_at=proof[9],
            policy_file=proof[6],
            expected_policy_sha256=proof[7],
            repo=git_history[0],
            evidence_file=missing,
            checkpoints=git_history[1],
            expected_branch="feature/e4-tests",
        )

    assert not missing.exists()
