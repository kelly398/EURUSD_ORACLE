
"""S4-01..04 forensics regressions; no actual Windows process or broker used."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from backend.data_engine.forward_store import hashed
from backend.runtime.stage4_collector_forensics import (
    Stage4ForensicsError,
    _parse_stamp,
    inspect_existing_logs,
    inspect_policy_copy,
    inspect_stage4_forensics,
    normalize_windows_survey,
    survey_windows_runtime,
    evaluate_stage4_forensics,
    run,
)
from backend.runtime.forward_recovery_evidence import (
    read_stored_epoch,
    inspect_startup_contract,
)
from backend.runtime.test_forward_source_provenance import _fixture

UTC = timezone.utc
ASOF = datetime(
    2026, 10, 27, 14, 0, 10,
    tzinfo=UTC,
)


@pytest.fixture
def demo(tmp_path):
    engine, policy, _ = _fixture(count=1)
    path = tmp_path / "hfm-policy.json"
    path.write_text(
        policy.model_dump_json(),
        encoding="utf-8",
    )
    try:
        yield (
            engine,
            policy,
            path,
            sha256(path.read_bytes()).hexdigest(),
        )
    finally:
        engine.dispose()


def empty_survey():
    return {
        "process_status": "OBSERVED",
        "processes": [],
        "tasks_status": "OBSERVED",
        "tasks": [],
        "services_status": "OBSERVED",
        "services": [],
    }


def test_s401_normalize_empty_observation_not_historic_failure():
    report = normalize_windows_survey(
        empty_survey()
    )
    assert report["matching_process_count"] == 0
    assert report["process_visibility"] == "OBSERVED"
    assert report["historical_process_state_proven"] is False


def test_s401_report_process_without_exposing_command_line():
    raw = empty_survey()
    raw["processes"] = [{
        "pid": 123,
        "role": "forward_recorder",
        "clock_policy_flag": True,
        "continuous_flag": True,
        "initialize_flag": False,
        "started_utc": ASOF.isoformat(),
    }]
    report = normalize_windows_survey(raw)
    assert report["processes"][0]["pid"] == 123
    assert report["processes"][0]["clock_policy_flag"] is True
    assert "command_line" not in report["processes"][0]
    assert report["command_lines_exposed"] is False


def test_s401_wrong_process_types_rejected():
    raw = empty_survey()
    raw["processes"] = [{
        "pid": True,
        "role": "supervisor",
        "clock_policy_flag": False,
        "continuous_flag": False,
        "initialize_flag": False,
        "started_utc": None,
    }]
    with pytest.raises(
        Stage4ForensicsError,
        match="Invalid process",
    ):
        normalize_windows_survey(raw)


def test_s401_unexpected_sensitive_field_rejected():
    raw = empty_survey()
    raw["processes"] = [{
        "pid": 20,
        "role": "supervisor",
        "clock_policy_flag": True,
        "continuous_flag": False,
        "initialize_flag": False,
        "started_utc": None,
        "command_line": "SECRET",
    }]
    with pytest.raises(
        Stage4ForensicsError,
        match="unexpected fields",
    ):
        normalize_windows_survey(raw)


def test_s402_launchers_recognized_without_execution_args():
    raw = empty_survey()
    raw["tasks"] = [{
        "launcher": "scheduled_task",
        "name": "OracleCapture",
        "state": "Ready",
        "clock_policy_flag": False,
    }]
    raw["services"] = [{
        "launcher": "windows_service",
        "name": "OracleFeed",
        "state": "Stopped",
        "clock_policy_flag": True,
    }]

    report = normalize_windows_survey(raw)

    assert report["matching_launcher_count"] == 2
    assert (
        report["matching_launchers"][0]["name"]
        == "OracleCapture"
    )


def test_s402_bad_launcher_role_rejected():
    raw = empty_survey()
    raw["tasks"] = [{
        "launcher": "windows_service",
        "name": "bad",
        "state": "Ready",
        "clock_policy_flag": False,
    }]
    with pytest.raises(Stage4ForensicsError):
        normalize_windows_survey(raw)


def test_s401_optional_survey_disabled_no_guessing():
    report = survey_windows_runtime()
    assert report["process_visibility"] == "NOT_INSPECTED"
    assert report["matching_process_count"] is None


def test_s401_survey_runner_receives_fixed_script():
    def runner(cmd, **kwargs):
        assert cmd[:3] == [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
        ]
        assert "Get-CimInstance" in cmd[4]
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                empty_survey()
            ).encode(),
        )

    result = survey_windows_runtime(
        enabled=True,
        runner=runner,
    )

    assert result["task_visibility"] == "OBSERVED"
    assert result["matching_process_count"] == 0


def test_s402_windows_probe_failure_not_misreported_as_no_process():
    def runner(_cmd, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=b"",
        )

    report = survey_windows_runtime(
        enabled=True,
        runner=runner,
    )

    assert report["process_visibility"] == "UNAVAILABLE"
    assert report["matching_process_count"] is None


def test_s403_no_log_files_no_cause_claim():
    result = inspect_existing_logs([])
    assert result["readable_files"] == 0
    assert result["historic_stoppage_cause_proven"] is False


def test_s403_event_counters_timestamp_and_secret_redacted(tmp_path):
    path = tmp_path / "recorder.log"
    path.write_text(
        "2026-10-27T15:02:00Z SUPERVISOR EXIT "
        "pid=10 password=secret\n"
        "2026-10-27T15:02:01+00:00 "
        "SUPERVISOR RESTART SCHEDULED\n"
        "2026-10-27T15:02:02Z "
        "Clock implementation differs from the bound epoch\n",
        encoding="utf-8",
    )

    report = inspect_existing_logs([path])

    assert (
        report["event_counts"]["supervisor_exit"]
        == 1
    )
    assert (
        report["event_counts"]["clock_code_mismatch"]
        == 1
    )
    assert (
        report["last_explicit_utc_per_event"][
            "supervisor_exit"
        ]
        == "2026-10-27T15:02:00+00:00"
    )
    assert "secret" not in json.dumps(report)
    assert report["raw_log_text_exposed"] is False


def test_s403_log_no_timezone_never_claims_utc(tmp_path):
    path = tmp_path / "recorder.log"
    path.write_text(
        "2026-10-27 15:03:00 MT5 connection unavailable\n"
    )

    report = inspect_existing_logs([path])

    assert (
        report["event_counts"]["mt5_connection_unavailable"]
        == 1
    )
    assert (
        "mt5_connection_unavailable"
        not in report["last_explicit_utc_per_event"]
    )


def test_s403_unavailable_placeholder_not_considered_evidence(tmp_path):
    result = inspect_existing_logs([
        tmp_path / "missing.log"
    ])
    assert result["readable_files"] == 0
    assert (
        result["files"][0]["status"]
        == "UNAVAILABLE_OR_TOO_LARGE"
    )


def test_s403_oversized_log_never_partially_counted(tmp_path):
    path = tmp_path / "big.log"
    path.write_bytes(
        b"SUPERVISOR START\n"
        + b"x" * 1024
    )

    result = inspect_existing_logs(
        [path],
        max_bytes=1024,
    )

    assert result["readable_files"] == 0
    assert result["event_counts"]["supervisor_start"] == 0


def test_s403_symlinked_log_blocked(tmp_path):
    original = tmp_path / "original.log"
    original.write_text("SUPERVISOR START")
    link = tmp_path / "link.log"

    try:
        link.symlink_to(original)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks unavailable")

    assert inspect_existing_logs([link])[
        "readable_files"
    ] == 0


@pytest.mark.parametrize(
    "cap",
    [0, 1, 1048577, True, "1024"],
)
def test_s403_unsafe_log_bounds_rejected(cap):
    with pytest.raises(
        Stage4ForensicsError,
        match="Log inspection bound",
    ):
        inspect_existing_logs(
            max_bytes=cap
        )


def test_s404_policy_copy_checked_without_rewriting(demo):
    _, policy, path, digest = demo
    before = path.read_bytes()

    result = inspect_policy_copy(
        path,
        policy,
        expected_file_sha256=digest,
    )

    assert result["status"] == "LOCAL_COPY_MATCH_UNATTESTED"
    assert result["policy_clock_matches_current_code"] is True
    assert path.read_bytes() == before


def test_s404_modified_policy_detected(demo):
    _, policy, path, digest = demo
    path.write_text(path.read_text() + "\n")

    result = inspect_policy_copy(
        path,
        policy,
        expected_file_sha256=digest,
    )

    assert (
        result["hash_matches_previous_observation"]
        is False
    )
    assert result["status"] == "COPY_MISMATCH"


def test_s404_missing_policy_no_creation(demo, tmp_path):
    _, policy, _, digest = demo
    path = tmp_path / "missing.json"

    result = inspect_policy_copy(
        path,
        policy,
        expected_file_sha256=digest,
    )

    assert result["status"] == "UNAVAILABLE"
    assert not path.exists()


def test_s404_db_read_select_only(demo):
    queries = []

    def watch(
        _c, _cursor, sql,
        _params, _context, _many,
    ):
        queries.append(sql.strip().upper())

    event.listen(
        demo[0],
        "before_cursor_execute",
        watch,
    )

    try:
        stored, epoch = read_stored_epoch(
            demo[0],
            epoch_id=demo[1].epoch_id,
            as_of=ASOF,
        )
    finally:
        event.remove(
            demo[0],
            "before_cursor_execute",
            watch,
        )

    assert queries
    assert all(
        q.startswith("SELECT")
        for q in queries
    )
    assert stored.epoch_id == epoch["epoch_id"]


def test_s404_full_inspection_synthetic_is_hold(demo):
    r = inspect_stage4_forensics(
        demo[0],
        epoch_id=demo[1].epoch_id,
        as_of=ASOF,
        policy_file=demo[2],
        expected_policy_sha256=demo[3],
        repo=Path(__file__).parents[2],
        inspect_windows=False,
    )

    assert r["final_action"] == "HOLD"
    assert r["historic_stoppage_cause"] == "NOT_ESTABLISHED"
    assert r["collector_started"] is False
    assert r["execution_authorized"] is False
    assert r["report_sha256"] == hashed({
        k: v
        for k, v in r.items()
        if k != "report_sha256"
    })


def test_s404_report_reconciles_observed_no_process(demo):
    _, epoch = read_stored_epoch(
        demo[0],
        epoch_id=demo[1].epoch_id,
        as_of=ASOF,
    )

    host = normalize_windows_survey(
        empty_survey()
    )
    logs = inspect_existing_logs([])
    policy = inspect_policy_copy(
        demo[2],
        demo[1],
        expected_file_sha256=demo[3],
    )
    startup = inspect_startup_contract(
        Path(__file__).parents[2]
    )

    r = evaluate_stage4_forensics(
        epoch,
        host,
        logs,
        policy,
        startup,
        observed_at=ASOF,
    )

    assert (
        "NO_MATCHING_RUNNING_COLLECTOR_OR_SUPERVISOR"
        in r["evidence_blockers"]
    )
    assert (
        "NO_READABLE_SUPERVISOR_OR_RECORDER_LOGS"
        in r["evidence_blockers"]
    )
    assert r["final_action"] == "HOLD"


def test_s404_output_deterministic_fixed_inputs(demo):
    opts = dict(
        epoch_id=demo[1].epoch_id,
        as_of=ASOF,
        policy_file=demo[2],
        expected_policy_sha256=demo[3],
        repo=Path(__file__).parents[2],
    )

    assert (
        inspect_stage4_forensics(demo[0], **opts)
        == inspect_stage4_forensics(demo[0], **opts)
    )


def test_s404_cutoff_mismatch_rejected(demo):
    _, epoch = read_stored_epoch(
        demo[0],
        epoch_id=demo[1].epoch_id,
        as_of=ASOF,
    )

    with pytest.raises(
        Stage4ForensicsError,
        match="inconsistent",
    ):
        evaluate_stage4_forensics(
            epoch,
            survey_windows_runtime(),
            inspect_existing_logs([]),
            inspect_policy_copy(
                demo[2],
                demo[1],
                expected_file_sha256=demo[3],
            ),
            inspect_startup_contract(
                Path(__file__).parents[2]
            ),
            observed_at=ASOF + timedelta(seconds=1),
        )


def test_s404_cli_requires_both_readonly_flags():
    with pytest.raises(SystemExit) as exc:
        run([
            "--epoch-id",
            "a" * 64,
        ])
    assert exc.value.code == 2


def test_s404_invalid_epoch_rejected_before_db_import(capsys):
    status = run([
        "--read-only-db",
        "--read-only-host",
        "--epoch-id",
        "bad",
        "--as-of",
        ASOF.isoformat(),
        "--policy-file",
        "x",
        "--expected-policy-sha256",
        "a" * 64,
    ])

    assert status == 1
    assert (
        "BLOCKED"
        in capsys.readouterr().out
    )


def test_s404_utc_timestamp_required():
    with pytest.raises(Stage4ForensicsError):
        _parse_stamp(
            "2026-10-27T14:00:00"
        )
