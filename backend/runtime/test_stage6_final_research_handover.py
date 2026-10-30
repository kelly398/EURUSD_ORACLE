
"""P6-21..24: isolated synthetic Git ancestry and read-only HOLD regression."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from backend.runtime.stage6_final_research_handover import (
    BATCHES, BRANCH, EPOCH, POLICY_SHA256, FinalHandoverError,
    _digest, _static_gates, _utc, assess_stage6_final_handover,
    inspect_optional_saved_reports, inspect_original_policy,
    inspect_previous_stage6_git, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
CONTRACTS = ROOT / "backend/agents/contracts.py"
MANIFEST = ROOT / "backend/agents/council_manifest.py"
AS_OF = "2026-10-30T08:00:00Z"

# Synthetic local Git refs are not real GitHub remotes or external evidence.


def git(repo, *args):
    command = subprocess.run(
        ["git", *args],
        cwd=repo,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=20,
        check=False,
    )
    if command.returncode:
        raise AssertionError(
            "Synthetic Git fixture setup failed: " + repr(args)
        )
    return command.stdout.decode(
        "utf-8", errors="replace",
    ).strip()


def official_gate_fixture():
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
        (
            "G%02d" % (i + 1),
            name,
            "independent_reviewer",
            "External source and custody proof required",
        )
        for i, name in enumerate(names)
    )


def fixture_git(
    tmp_path, *, wrong_gate=False, extra_change=False,
):
    """Create disposable local Git history, not external evidence."""
    repo = tmp_path / "oracle_fixture"
    repo.mkdir()

    git(repo, "init", "-b", BRANCH)
    git(repo, "config", "user.name", "ORACLE-SYNTHETIC-TEST")
    git(repo, "config", "user.email", "fixture@invalid.example")

    (repo / "docs/clock_profiles").mkdir(parents=True)
    (repo / "backend/agents").mkdir(parents=True)
    (repo / "backend/runtime").mkdir(parents=True)

    (
        repo / "docs/clock_profiles/hfm_forward_20261027.json"
    ).write_bytes(POLICY.read_bytes())
    (
        repo / "backend/agents/contracts.py"
    ).write_bytes(CONTRACTS.read_bytes())
    (
        repo / "backend/agents/council_manifest.py"
    ).write_bytes(MANIFEST.read_bytes())

    git(repo, "add", "--", "docs", "backend/agents")
    git(repo, "commit", "-m", "synthetic Stage 5 parent")
    parent = git(repo, "rev-parse", "HEAD")[:7]

    batches = []
    for index, (label, _, stem) in enumerate(BATCHES):
        source = repo / ("backend/runtime/" + stem + ".py")
        test = repo / ("backend/runtime/test_" + stem + ".py")

        content = 'VERSION = "SYNTHETIC_FIXTURE_ONLY"\n'
        if index == 0:
            gates = official_gate_fixture()
            if wrong_gate:
                gates = gates[:-1]
            content += "GATES = " + repr(gates) + "\n"

        source.write_text(
            content + "\ndef description():\n    return 'HOLD'\n",
            encoding="utf-8",
        )
        test.write_text(
            "def test_synthetic():\n    assert True\n",
            encoding="utf-8",
        )

        git(
            repo, "add", "--",
            "backend/runtime/" + stem + ".py",
            "backend/runtime/test_" + stem + ".py",
        )

        if extra_change and index == 2:
            extra = repo / "unexpected.txt"
            extra.write_text(
                "THIS SHOULD BE REJECTED",
                encoding="utf-8",
            )
            git(repo, "add", "--", "unexpected.txt")

        git(repo, "commit", "-m", "synthetic " + label)

        batches.append((
            label,
            git(repo, "rev-parse", "HEAD")[:7],
            stem,
        ))

    # Local tracking ref only; no network authentication.
    git(
        repo, "update-ref",
        "refs/remotes/origin/" + BRANCH, "HEAD",
    )
    return repo, tuple(batches), parent


@pytest.fixture
def synthetic_repo(tmp_path):
    return fixture_git(tmp_path)


def call(
    repo, batches, parent, *,
    as_of=AS_OF, saved_reports=None,
):
    return assess_stage6_final_handover(
        repo=repo,
        epoch_id=EPOCH,
        as_of=as_of,
        batches=batches,
        stage5_anchor=parent,
        branch=BRANCH,
        saved_reports=saved_reports,
    )


def inspect(repo, batches, parent):
    return inspect_previous_stage6_git(
        repo,
        batches=batches,
        stage5_anchor=parent,
        branch=BRANCH,
    )


def saved_fixture(tmp_path, *, extra=None):
    data = {
        "epoch_id": EPOCH,
        "as_of": AS_OF,
        "read_only": True,
        "final_action": "HOLD",
        "assessment_version": "SYNTHETIC_REPORT_ONLY",
        "external_gates_verified": 0,
    }
    if extra:
        data.update(extra)

    data["report_sha256"] = _digest(data)

    file = tmp_path / "saved_report.json"
    file.write_text(
        json.dumps(data, sort_keys=True),
        encoding="utf-8",
    )
    return file, data


# P6-21: previous Git batch custody

def test_p621_five_commits_and_stage5_ancestor(synthetic_repo):
    repo, batches, parent = synthetic_repo
    r = inspect(repo, batches, parent)
    assert r["prior_batches_verified"] == 5
    assert r["prior_checkpoints_git_anchor_verified"] == 20
    assert r["stage5_parent_commit"].startswith(parent)
    assert r["local_head"] == r["local_origin_tracking_head"]
    assert r["remote_server_independently_queried"] is False


def test_p621_each_batch_exactly_two_new_files(synthetic_repo):
    r = inspect(*synthetic_repo)
    assert len(r["batches"]) == 5
    for batch in r["batches"]:
        assert batch["change_scope"] == "TWO_ADDED_FILES"
        assert len(batch["files"]) == 2
        assert all(
            f["python_ast_parsed"] for f in batch["files"]
        )
        assert all(
            len(f["committed_content_sha256"]) == 64
            for f in batch["files"]
        )


def test_p621_all_five_stems_in_inventory(synthetic_repo):
    r = inspect(*synthetic_repo)
    assert [
        x["checkpoint_range"] for x in r["batches"]
    ] == [x[0] for x in BATCHES]
    assert all(
        x["source_unchanged_at_head"] for x in r["batches"]
    )


def test_p621_wrong_prior_stage6_anchor_refused(synthetic_repo):
    repo, batches, parent = synthetic_repo
    bad = list(batches)
    bad[1] = (bad[1][0], "0123456", bad[1][2])

    with pytest.raises(
        FinalHandoverError, match="commit order",
    ):
        inspect(repo, tuple(bad), parent)


def test_p621_wrong_stage5_parent_refused(synthetic_repo):
    repo, batches, _ = synthetic_repo

    with pytest.raises(FinalHandoverError, match="Stage 5"):
        inspect(repo, batches, "0123456")


def test_p621_local_remote_tracking_mismatch_fails(synthetic_repo):
    repo, batches, parent = synthetic_repo

    git(
        repo, "update-ref",
        "refs/remotes/origin/" + BRANCH, "HEAD~1",
    )
    with pytest.raises(
        FinalHandoverError, match="tracking ref differ",
    ):
        inspect(repo, batches, parent)


def test_p621_uncommitted_tracked_change_blocks(synthetic_repo):
    repo, batches, parent = synthetic_repo

    path = repo / "backend/runtime" / (
        batches[-1][2] + ".py"
    )
    path.write_text(
        "SYNTHETIC MODIFIED", encoding="utf-8",
    )

    with pytest.raises(
        FinalHandoverError, match="Tracked source",
    ):
        inspect(repo, batches, parent)


def test_p621_staged_change_blocks(synthetic_repo):
    repo, batches, parent = synthetic_repo

    path = repo / "backend/runtime" / (
        batches[-1][2] + ".py"
    )
    path.write_text(
        "SYNTHETIC STAGED CHANGE", encoding="utf-8",
    )
    git(repo, "add", "--", str(path.relative_to(repo)))

    with pytest.raises(
        FinalHandoverError, match="Tracked source",
    ):
        inspect(repo, batches, parent)


def test_p621_one_extra_modified_path_in_commit_refused(tmp_path):
    repo, batches, parent = fixture_git(
        tmp_path, extra_change=True,
    )
    with pytest.raises(
        FinalHandoverError,
        match="exactly its source/test pair",
    ):
        inspect(repo, batches, parent)


def test_p621_unexpected_branch_refused(synthetic_repo):
    repo, batches, parent = synthetic_repo

    git(repo, "checkout", "-b", "wrong-branch")
    with pytest.raises(
        FinalHandoverError, match="Unexpected current branch",
    ):
        inspect(repo, batches, parent)


def test_p621_only_repo_root_accepted(synthetic_repo):
    repo, batches, parent = synthetic_repo

    with pytest.raises(
        FinalHandoverError, match="top-level",
    ):
        inspect(repo / "backend", batches, parent)


def test_p621_untracked_batch6_does_not_fake_a_commit(synthetic_repo):
    repo, batches, parent = synthetic_repo

    (
        repo / "backend/runtime/stage6_final_research_handover.py"
    ).write_text(
        "# UNTRACKED PROPOSED FINAL MODULE ONLY\n",
        encoding="utf-8",
    )
    r = inspect(repo, batches, parent)
    assert r["untracked_files_not_examined"] is True
    assert r["prior_checkpoints_git_anchor_verified"] == 20


def test_p621_gate_inventory_must_be_exactly_eleven(tmp_path):
    repo, batches, parent = fixture_git(
        tmp_path, wrong_gate=True,
    )
    with pytest.raises(FinalHandoverError, match="eleven"):
        inspect(repo, batches, parent)


def test_p621_historical_policy_matches_byte_anchor():
    assert sha256(
        POLICY.read_bytes()
    ).hexdigest() == POLICY_SHA256


def test_p621_original_policy_tamper_fails(tmp_path):
    path = tmp_path / "docs/clock_profiles"
    path.mkdir(parents=True)

    (
        path / "hfm_forward_20261027.json"
    ).write_bytes(POLICY.read_bytes() + b" ")

    with pytest.raises(
        FinalHandoverError, match="byte pin mismatch",
    ):
        inspect_original_policy(tmp_path)


# P6-22: independently verified external gates must stay zero

def test_p622_final_matrix_exact_eleven(synthetic_repo):
    r = call(*synthetic_repo)
    assert len(r["external_acceptance_matrix"]) == 11
    assert [
        g["gate_id"] for g in r["external_acceptance_matrix"]
    ] == ["G%02d" % x for x in range(1, 12)]
    assert all(
        not gate["approved"]
        for gate in r["external_acceptance_matrix"]
    )


def test_p622_static_gates_never_invokes_council(synthetic_repo):
    repo, batches, parent = synthetic_repo
    gates = inspect(
        repo, batches, parent,
    )["gate_requirements_from_static_source"]

    assert len(gates) == 11
    assert all(
        gate["review_role"] == "independent_reviewer"
        for gate in gates
    )
    assert (
        "EXTERNAL_GATE_NOT_INDEPENDENTLY_VERIFIED:"
        "INDEPENDENT_BROKER_SERVER_IDENTITY"
    ) in call(repo, batches, parent)["blockers"]


def test_p622_external_evidence_not_self_certified(synthetic_repo):
    r = call(*synthetic_repo)
    assert r["external_evidence_gates_independently_verified"] == 0
    assert r["source_families_independently_verified"] == 0
    assert r["research_operational_readiness"] == (
        "BLOCKED_EXTERNAL_EVIDENCE"
    )


def test_p622_no_reports_supplied_is_distinct_from_verification(
    synthetic_repo,
):
    r = call(
        *synthetic_repo
    )["previous_stage6_saved_reports"]

    assert r["optional_reports_supplied"] == 0
    assert len(r["reports"]) == 5
    assert r["saved_reports_independently_verified"] == 0


def test_p622_saved_local_hash_does_not_establish_external_trust(
    tmp_path, synthetic_repo,
):
    repo, batches, parent = synthetic_repo
    path, _ = saved_fixture(tmp_path)

    r = call(
        repo, batches, parent,
        saved_reports={"P6-17..20": path},
    )

    assert (
        r["previous_stage6_saved_reports"]["optional_reports_supplied"]
        == 1
    )
    assert r["external_evidence_gates_independently_verified"] == 0
    assert not next(
        x
        for x in r["previous_stage6_saved_reports"]["reports"]
        if x["checkpoint_range"] == "P6-17..20"
    )["independently_attested"]


def test_p622_local_report_tamper_refused(tmp_path):
    path, data = saved_fixture(tmp_path)
    data["final_action"] = "LONG"
    path.write_text(
        json.dumps(data), encoding="utf-8",
    )

    with pytest.raises(
        FinalHandoverError, match="inconsistent or unsafe",
    ):
        inspect_optional_saved_reports(
            {"P6-17..20": path}, as_of=AS_OF,
        )


def test_p622_rehashed_self_approval_refused(tmp_path):
    path, data = saved_fixture(
        tmp_path, extra={"paper_execution_authorized": True},
    )

    with pytest.raises(
        FinalHandoverError,
        match="inconsistent or unsafe|self-promotes",
    ):
        inspect_optional_saved_reports(
            {"P6-01..04": path}, as_of=AS_OF,
        )


def test_p622_rehashed_nested_gate_approval_refused(tmp_path):
    path, data = saved_fixture(
        tmp_path,
        extra={"matrix": [{"gate_id": "G01", "approved": True}]},
    )

    with pytest.raises(
        FinalHandoverError, match="self-promotes",
    ):
        inspect_optional_saved_reports(
            {"P6-09..12": path}, as_of=AS_OF,
        )


def test_p622_rehashed_independent_claim_refused(tmp_path):
    path, data = saved_fixture(
        tmp_path,
        extra={"source_families_independently_verified": 13},
    )

    with pytest.raises(
        FinalHandoverError, match="self-promotes",
    ):
        inspect_optional_saved_reports(
            {"P6-05..08": path}, as_of=AS_OF,
        )


def test_p622_rehashed_external_authentication_claim_refused(tmp_path):
    path, data = saved_fixture(
        tmp_path,
        extra={"independently_authenticated_outcomes": 2},
    )
    with pytest.raises(
        FinalHandoverError, match="self-promotes",
    ):
        inspect_optional_saved_reports(
            {"P6-13..16": path}, as_of=AS_OF,
        )


def test_p622_rehashed_external_origin_bool_refused(tmp_path):
    path, data = saved_fixture(
        tmp_path,
        extra={"external_origins_authenticated": True},
    )
    with pytest.raises(
        FinalHandoverError,
        match="inconsistent or unsafe|self-promotes",
    ):
        inspect_optional_saved_reports(
            {"P6-05..08": path}, as_of=AS_OF,
        )


def test_p622_rehashed_research_readiness_claim_refused(tmp_path):
    path, data = saved_fixture(
        tmp_path,
        extra={"research_operational_readiness": "APPROVED"},
    )

    with pytest.raises(
        FinalHandoverError, match="self-promotes",
    ):
        inspect_optional_saved_reports(
            {"P6-01..04": path}, as_of=AS_OF,
        )


def test_p622_report_from_future_cutoff_refused(tmp_path):
    path, data = saved_fixture(
        tmp_path,
        extra={"as_of": "2026-12-01T08:00:00Z"},
    )

    with pytest.raises(
        FinalHandoverError, match="inconsistent or unsafe",
    ):
        inspect_optional_saved_reports(
            {"P6-13..16": path}, as_of=AS_OF,
        )


def test_p622_unexpected_report_label_refused(tmp_path):
    path, data = saved_fixture(tmp_path)

    with pytest.raises(
        FinalHandoverError, match="Unexpected saved-report",
    ):
        inspect_optional_saved_reports(
            {"P6-99..99": path}, as_of=AS_OF,
        )


# P6-23: final readiness decision, historical preservation and risk gates

def test_p623_council_static_31_and_13(synthetic_repo):
    r = call(*synthetic_repo)["static_council_inventory"]
    assert r["static_seats"] == 31
    assert r["static_source_families"] == 13
    assert not r["council_executed"]


def test_p623_original_epoch_policy_immutable(synthetic_repo):
    r = call(
        *synthetic_repo
    )["original_historical_clock_policy"]

    assert r["raw_sha256"] == POLICY_SHA256
    assert not r["historical_epoch_rebound"]
    assert not r["independent_broker_utc_attested"]


def test_p623_no_trading_learning_collection_permissions(
    synthetic_repo,
):
    r = call(*synthetic_repo)

    for key in (
        "files_written",
        "git_mutated",
        "remote_network_accessed",
        "database_accessed",
        "broker_connected",
        "collector_started",
        "historical_epoch_rebound",
        "council_invoked",
        "outcome_settlement_invoked",
        "model_training_authorized",
        "paper_execution_authorized",
        "live_execution_authorized",
        "next_phase_automatically_authorized",
    ):
        assert r[key] is False

    assert r["final_action"] == "HOLD"
    assert r["read_only"] is True


def test_p623_no_false_profitability_or_source_claims(
    synthetic_repo,
):
    r = call(*synthetic_repo)

    assert (
        "NO_VERIFIED_COST_AWARE_COUNCIL_SUPERIORITY_OR_PROFITABILITY"
    ) in r["blockers"]
    assert (
        "NO_INDEPENDENT_ATTESTATION_FOR_ALL_13_COUNCIL_INPUT_FAMILIES"
    ) in r["blockers"]
    assert r["research_operational_readiness"] == (
        "BLOCKED_EXTERNAL_EVIDENCE"
    )


def test_p623_reported_windows_regression_not_rerun(
    synthetic_repo,
):
    r = call(
        *synthetic_repo
    )["operator_reported_previous_windows_regression"]

    assert r["status"] == (
        "OPERATOR_TERMINAL_LOG_NOT_RERUN_BY_INSPECTOR"
    )
    assert (
        r["passed"], r["skipped"], r["subtests_passed"],
    ) == (1155, 14, 61)


def test_p623_no_auto_promotion_after_all_saved_reports(
    tmp_path, synthetic_repo,
):
    repo, batches, parent = synthetic_repo
    path, data = saved_fixture(tmp_path)
    reports = {
        label: path for label, _, _ in BATCHES
    }

    r = call(
        repo, batches, parent, saved_reports=reports,
    )
    assert (
        r["previous_stage6_saved_reports"]["optional_reports_supplied"]
        == 5
    )
    assert r["external_evidence_gates_independently_verified"] == 0
    assert not r["next_phase_automatically_authorized"]


# P6-24: bounded deterministic final handover

def test_p624_prior_20_not_automatically_24(synthetic_repo):
    r = call(*synthetic_repo)

    assert r["engineering_checkpoints_prior_batches_verified_locally"] == 20
    assert r["engineering_checkpoints_total_stage6"] == 24
    assert not r["final_batch_self_certified"]
    assert not r["final_batch_windows_tests_verified_by_this_report"]
    assert not r["final_batch_git_commit_verified_by_this_report"]
    assert (
        "FINAL_BATCH_WINDOWS_TESTS_AND_GIT_COMMIT_PENDING"
    ) in r["blockers"]


def test_p624_report_hash_deterministic(synthetic_repo):
    a = call(*synthetic_repo)
    b = call(*synthetic_repo)

    assert a == b
    assert a["report_sha256"] == _digest({
        k: v for k, v in a.items()
        if k != "report_sha256"
    })


def test_p624_read_only_git_inspection_does_not_edit_source(
    synthetic_repo,
):
    repo, batches, parent = synthetic_repo
    policy = repo / "docs/clock_profiles/hfm_forward_20261027.json"

    original = policy.read_bytes()
    state_before = git(repo, "status", "--porcelain")

    call(repo, batches, parent)

    assert state_before == git(
        repo, "status", "--porcelain",
    )
    assert policy.read_bytes() == original


def test_p624_cli_requires_explicit_read_only_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", AS_OF])

    assert exc.value.code == 2


def test_p624_cli_wrong_epoch_fails_closed(
    capsys, synthetic_repo,
):
    repo, batches, parent = synthetic_repo
    assert run([
        "--read-only-git",
        "--read-only-evidence",
        "--repo", str(repo),
        "--epoch-id", "WRONG",
        "--as-of", AS_OF,
    ]) == 1

    assert "BLOCKED" in capsys.readouterr().out


def test_p624_cli_future_asof_fails_closed(
    capsys, synthetic_repo,
):
    repo, batches, parent = synthetic_repo
    future = (
        datetime.now(timezone.utc) + timedelta(days=1)
    ).isoformat()

    assert run([
        "--read-only-git",
        "--read-only-evidence",
        "--repo", str(repo),
        "--epoch-id", EPOCH,
        "--as-of", future,
    ]) == 1

    assert "BLOCKED" in capsys.readouterr().out


def test_p624_cli_saved_report_duplicate_label_blocks(
    capsys, synthetic_repo,
):
    repo, batches, parent = synthetic_repo

    args = [
        "--read-only-git",
        "--read-only-evidence",
        "--repo", str(repo),
        "--epoch-id", EPOCH,
        "--as-of", AS_OF,
        "--saved-report", "P6-01..04=X",
        "--saved-report", "P6-01..04=X",
    ]

    assert run(args) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p624_cli_reports_genuine_anchors_required(
    capsys, synthetic_repo,
):
    repo, batches, parent = synthetic_repo

    # Synthetic commits are not the actual pinned Windows Git hashes.
    assert run([
        "--read-only-git",
        "--read-only-evidence",
        "--repo", str(repo),
        "--epoch-id", EPOCH,
        "--as-of", AS_OF,
        "--json",
    ]) == 1

    assert "BLOCKED" in capsys.readouterr().out


def test_p624_invalid_utc_cases():
    for bad in (
        "bad",
        "2026-10-30T08:00:00",
        "2026-10-30T11:00:00+03:00",
    ):
        with pytest.raises(FinalHandoverError):
            _utc(bad)


def test_p624_invalid_json_gates_refused():
    bad = b"GATES = [('G01', 'X', 'Y', 'Z')]\n"

    with pytest.raises(FinalHandoverError, match="eleven"):
        _static_gates(bad)
