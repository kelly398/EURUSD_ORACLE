
"""P3-05..08 read-only recovery tests with SQLite and temporary Git/log files."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import ModuleType
import json
import subprocess
import sys

import pytest
from sqlalchemy import create_engine, event

from backend.data_engine.clock_policy import ClockPolicy, implementation_sha256
from backend.data_engine.forward_store import ForwardFeedStore, epochs
from backend.runtime.forward_recovery_evidence import (
    RecoveryEvidenceError, _digest, _limit, _utc, read_stored_epoch,
    search_local_policy, compare_clock_code, inspect_startup_contract,
    inspect_logs, build_recovery_plan, inspect_recovery_evidence, run,
)

NOW = datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def data():
    engine = create_engine("sqlite://")
    store = ForwardFeedStore(engine, clock=lambda: NOW)
    store.initialize()
    policy = ClockPolicy(
        company_sha256="a" * 64, server_sha256="b" * 64,
        evidence_sha256="c" * 64,
        implementation_sha256=implementation_sha256(),
        valid_from=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(days=10),
    )
    store.append(policy, tick_rows=[], bar_rows=[], received_at=NOW,
                 tick_start=NOW-timedelta(minutes=2), tick_end=NOW,
                 bar_start=NOW-timedelta(minutes=10), bar_end=NOW)
    try:
        yield engine, policy
    finally:
        engine.dispose()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    path = root / "backend/data_engine/clock_policy.py"
    path.parent.mkdir(parents=True)
    contents = Path(__file__).parents[1].joinpath("data_engine/clock_policy.py").read_bytes()
    path.write_bytes(contents)

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, check=True
        ).stdout.decode().strip()

    git("init", "-q")
    git("config", "core.autocrlf", "false")
    git("add", "-A")
    git("-c", "user.name=Test", "-c", "user.email=test@example.org",
        "commit", "-qm", "first")
    revision = git("rev-parse", "HEAD")
    path.write_bytes(contents + b"\ndef new_test_function():\n    return True\n")

    supervisor = root / "backend/runtime/market_data_supervisor.py"
    supervisor.parent.mkdir(parents=True)
    supervisor.write_text(
        "def x(parser):\n    parser.add_argument('--clock-policy')\n",
        encoding="utf-8"
    )
    recorder = root / "backend/runtime/capture_forward_feed.py"
    recorder.write_text(
        "def x(parser):\n"
        "    parser.add_argument('--clock-policy', required=True)\n"
        "    parser.add_argument('--initialize', action='store_true')\n"
    )
    return root, revision, contents


def policy_report(data):
    engine, policy = data
    return read_stored_epoch(engine, epoch_id=policy.epoch_id, as_of=NOW)


def test_stored_policy_db_is_canonical_and_not_original_file(data):
    saved, evidence = policy_report(data)
    assert saved.epoch_id == evidence["epoch_id"]
    assert evidence["stored_policy_is_recoverable_as_data"] is True
    assert evidence["original_policy_file_location_proven"] is False
    assert evidence["last_capture_ingested_at"] == NOW.isoformat()


def test_missing_epoch_blocks(data):
    with pytest.raises(RecoveryEvidenceError, match="Epoch not recorded"):
        read_stored_epoch(data[0], epoch_id="f"*64, as_of=NOW)


def test_future_or_non_utc_cutoff_blocks(data):
    with pytest.raises(RecoveryEvidenceError, match="explicit UTC"):
        read_stored_epoch(
            data[0], epoch_id=data[1].epoch_id,
            as_of=NOW.astimezone(timezone(timedelta(hours=3)))
        )
    with pytest.raises(RecoveryEvidenceError, match="future"):
        read_stored_epoch(
            data[0], epoch_id=data[1].epoch_id,
            as_of=datetime.now(timezone.utc)+timedelta(days=1)
        )


def test_db_policy_tamper_detected(data):
    engine, policy = data
    with engine.begin() as c:
        c.execute(epochs.update().values(policy_json="{}"))
    with pytest.raises(RecoveryEvidenceError, match="validated"):
        policy_report(data)


def test_db_metadata_tamper_detected(data):
    engine, policy = data
    with engine.begin() as c:
        c.execute(epochs.update().values(valid_until=NOW+timedelta(days=2)))
    with pytest.raises(RecoveryEvidenceError, match="disagree"):
        policy_report(data)


def test_db_reader_only_selects(data):
    statements = []

    def observe(_c, _cur, sql, _params, _context, _many):
        statements.append(sql.lstrip().upper())

    event.listen(data[0], "before_cursor_execute", observe)
    try:
        policy_report(data)
    finally:
        event.remove(data[0], "before_cursor_execute", observe)
    assert statements and all(s.startswith("SELECT") for s in statements)


def test_json_search_finds_policy_irrespective_of_filename(data, tmp_path):
    policy = data[1]
    root = tmp_path / "manual-config"
    root.mkdir()
    artifact = root / "arbitrary.json"
    artifact.write_text(policy.model_dump_json(), encoding="utf-8")
    report = search_local_policy(policy, roots=[root])
    assert report["matching_file_count"] == 1
    assert report["matching_policy_files"][0]["same_policy_as_stored_epoch"] is True
    assert report["original_file_location_attested"] is False


def test_search_never_creates_missing_policy(data, tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    r = search_local_policy(data[1], roots=[root])
    assert r["matching_file_count"] == 0
    assert list(root.iterdir()) == []


def test_search_excludes_venvs_symlinks_and_other_epochs(data, tmp_path):
    root = tmp_path / "config"
    root.mkdir()
    other = data[1].model_copy(update={"server_sha256": "d"*64})
    (root / "other.json").write_text(other.model_dump_json())
    venv = root / ".venv"
    venv.mkdir()
    (venv / "secret.json").write_text(data[1].model_dump_json())
    r = search_local_policy(data[1], roots=[root])
    assert r["matching_file_count"] == 0
    assert r["json_files_examined"] == 1


def test_bounded_scan_reports_incomplete(data, tmp_path):
    for idx in range(4):
        (tmp_path / f"{idx}.json").write_text("{}")
    r = search_local_policy(data[1], roots=[tmp_path], max_json_files=1)
    assert r["search_truncated"] is True
    assert r["json_files_examined"] == 1


def test_bounded_directory_scan_cannot_walk_every_folder(data, tmp_path):
    for n in range(5):
        (tmp_path / f"dir_{n}").mkdir()
    scan = search_local_policy(data[1], roots=[tmp_path], max_directories=1)
    assert scan["search_truncated"] is True
    assert scan["directories_visited"] == 1


def test_clock_exact_original_blob_identifies_changes(repo):
    root, revision, old = repo
    r = compare_clock_code(
        repo=root, bound_hash=sha256(old).hexdigest(),
        candidate_revision=revision
    )
    assert r["status"] == "EXACT_LOCAL_GIT_BLOB_LOCATED_UNATTESTED"
    assert "new_test_function" in r["changed_functions"]
    assert r["broker_utc_certified"] is False
    assert r["source_bytes_match"] is False


def test_clock_wrong_hash_never_claims_exact_match(repo):
    root, revision, _ = repo
    r = compare_clock_code(
        repo=root, bound_hash="f"*64, candidate_revision=revision
    )
    assert r["status"] == "CANDIDATE_NOT_BYTE_EXACT"
    assert r["history_certified"] is False


def test_clock_invalid_revision_rejected(repo):
    with pytest.raises(RecoveryEvidenceError, match="Invalid Git"):
        compare_clock_code(
            repo=repo[0], bound_hash="a"*64,
            candidate_revision="HEAD; rm something"
        )


def test_startup_flags_static_and_no_process(repo):
    r = inspect_startup_contract(repo[0])
    assert r["status"] == "STATIC_LAUNCH_CONTRACT_RECOGNIZED"
    assert r["recorder_has_initialize_write_flag"] is True
    assert r["recorder_or_supervisor_started"] is False
    assert "utc_forward_feed" not in r["default_legacy_workers"]


def test_startup_missing_flags_blocks(repo):
    (repo[0] / "backend/runtime/capture_forward_feed.py").write_text("pass\n")
    assert inspect_startup_contract(repo[0])["status"] == "LAUNCH_CONTRACT_UNVERIFIED"


def test_logs_without_files_are_not_reconstructed():
    r = inspect_logs()
    assert r["status"] == "NO_LOG_FILES_SUPPLIED"
    assert r["historic_stoppage_cause_proven"] is False


def test_log_patterns_counted_but_line_text_not_repeated(tmp_path):
    path = tmp_path / "supervisor.log"
    path.write_text(
        "SUPERVISOR EXIT account_secret=abc\n"
        "SUPERVISOR START foo\n"
        "MT5 connection unavailable\n"
    )
    r = inspect_logs([path])
    assert r["event_pattern_counts"]["supervisor_exit"] == 1
    assert r["event_pattern_counts"]["mt5_unavailable"] == 1
    assert "abc" not in json.dumps(r)
    assert r["log_authenticity_verified"] is False


def test_log_truncation_reported(tmp_path):
    path = tmp_path / "big.log"
    path.write_bytes(b"x" * 1300)
    r = inspect_logs([path], max_bytes=1024)
    assert r["truncated_log_files"] == 1


def test_plan_stays_hold_even_when_local_evidence_looks_good(data, repo, tmp_path):
    saved, db = policy_report(data)
    root, revision, old = repo
    policy_file = tmp_path / "current.json"
    policy_file.write_text(saved.model_dump_json())
    scan = search_local_policy(saved, roots=[tmp_path])
    clock = compare_clock_code(
        repo=root, bound_hash=sha256(old).hexdigest(),
        candidate_revision=revision
    )
    log_path = tmp_path / "run.log"
    log_path.write_text("SUPERVISOR EXIT\n")
    r = build_recovery_plan(
        db, scan, clock, inspect_startup_contract(root),
        inspect_logs([log_path])
    )
    assert r["final_action"] == "HOLD"
    assert r["recovery_authorized"] is False
    assert r["database_modified"] is False
    assert r["collector_started"] is False
    assert r["broker_identity_attested"] is False


def test_missing_logs_and_policy_yield_clear_blockers(data, repo):
    saved, db = policy_report(data)
    clock = compare_clock_code(
        repo=repo[0], bound_hash="e"*64,
        candidate_revision=repo[1]
    )
    r = build_recovery_plan(
        db, search_local_policy(saved), clock,
        inspect_startup_contract(repo[0]), inspect_logs()
    )
    assert "NO_VERIFIED_LOCAL_POLICY_COPY" in r["operator_blockers"]
    assert "COLLECTOR_EXIT_LOG_EVIDENCE_NOT_REVIEWED" in r["operator_blockers"]
    assert "BOUND_CLOCK_GIT_SOURCE_NOT_BYTE_EXACT" in r["operator_blockers"]


def test_report_digest_stable(data, repo):
    _, db = policy_report(data)
    args = (
        db, search_local_policy(data[1]),
        compare_clock_code(
            repo=repo[0], bound_hash="e"*64,
            candidate_revision=repo[1]
        ),
        inspect_startup_contract(repo[0]),
        inspect_logs()
    )
    a = build_recovery_plan(*args)
    b = build_recovery_plan(*args)
    assert a == b
    assert a["report_sha256"] == _digest({
        k: v for k, v in a.items() if k != "report_sha256"
    })


def test_integration_inspection_uses_prior_git_audit(data, repo, monkeypatch, tmp_path):
    module = ModuleType("backend.data_engine.forward_cycle_readiness")
    module.audit_bound_clock_git_history = lambda *args, **kwargs: {
        "matches": [{"revision": repo[1], "match_type": "EXACT_GIT_BLOB_BYTES"}]
    }
    monkeypatch.setitem(sys.modules, module.__name__, module)
    r = inspect_recovery_evidence(
        data[0], epoch_id=data[1].epoch_id,
        as_of=NOW, repo=repo[0], search_roots=[tmp_path]
    )
    assert r["clock_history"]["status"] in (
        "EXACT_LOCAL_GIT_BLOB_LOCATED_UNATTESTED",
        "CANDIDATE_NOT_BYTE_EXACT"
    )
    assert r["final_action"] == "HOLD"


@pytest.mark.parametrize("value", [0, True, 5001, "100", -1])
def test_invalid_scan_limits(value, data):
    with pytest.raises(RecoveryEvidenceError, match="max_json_files"):
        search_local_policy(data[1], max_json_files=value)


def test_cli_requires_read_only_flags():
    with pytest.raises(SystemExit) as ex:
        run(["--epoch-id", "f"*64, "--as-of", NOW.isoformat()])
    assert ex.value.code == 2


def test_bad_epoch_cli_fails_without_db_connection(capsys):
    assert run([
        "--read-only-db", "--git-read-only",
        "--epoch-id", "BAD", "--as-of", NOW.isoformat()
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out
