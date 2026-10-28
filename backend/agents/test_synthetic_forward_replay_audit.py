
"""M49-52 deterministic source digests, PIT eligibility and Council HOLD gates."""

from datetime import timedelta
import json

import pytest
from sqlalchemy import create_engine, event

import backend.agents.synthetic_forward_replay_audit as audit
from backend.agents.synthetic_forward_replay_audit import (
    audit_variable_replay, run,
)
from backend.agents.synthetic_forward_variable_replay import (
    CaptureReplayError, make_variable_demo,
)
from backend.data_engine.forward_store import (
    ForwardFeedStore, bars, captures, epochs, ticks,
)


@pytest.fixture
def demo():
    store, policy, context, engine = make_variable_demo(capture_count=5)
    try:
        yield store, policy, context, engine
    finally:
        engine.dispose()


def checked(fault, size=5):
    store, policy, context, engine = make_variable_demo(
        capture_count=size, fault=fault)
    try:
        return audit_variable_replay(store, policy, context,
                                     capture_count=size, synthetic_only=True)
    finally:
        engine.dispose()


@pytest.mark.parametrize("size", [2, 5, 8])
def test_m49_m52_variable_length_valid_council_and_hold(size):
    report = checked("none", size)
    assert report["status"] == "AUDITED_SYNTHETIC_COUNCIL_HOLD"
    assert report["reason_codes"] == []
    assert len(report["snapshots"]) == size
    assert report["council_invoked"] is True
    assert report["council_seats"] == 31
    assert report["final_action"] == "HOLD"
    assert report["independent_broker_attestation"] is False
    assert report["real_forward_admission"] is False
    assert report["historical_admission"] is False
    assert report["execution_authorized"] is False
    assert report["training_authorized"] is False


def test_m50_each_prefix_has_expected_membership_and_unique_hash(demo):
    store, policy, context, _ = demo
    report = audit_variable_replay(store, policy, context,
                                   capture_count=5, synthetic_only=True)
    stamps = [s["cutoff"] for s in report["snapshots"]]
    assert stamps == [(context.as_of - timedelta(minutes=5 * i)).isoformat()
                      for i in (4, 3, 2, 1, 0)]
    assert [s["visible_captures"] for s in report["snapshots"]] == [1, 2, 3, 4, 5]
    assert all(s["reason_codes"] == [] for s in report["snapshots"])
    assert all(s["status"] == "POINT_IN_TIME_VERIFIED_SYNTHETIC"
               for s in report["snapshots"])
    assert len({s["observed_state_sha256"] for s in report["snapshots"]}) == 5
    assert len({s["receipt_sha256"] for s in report["snapshots"]}) == 5


def test_m49_report_hashes_are_stable(demo):
    store, policy, context, _ = demo
    a = audit_variable_replay(store, policy, context,
                              capture_count=5, synthetic_only=True)
    b = audit_variable_replay(store, policy, context,
                              capture_count=5, synthetic_only=True)
    assert a == b
    assert len(a["evidence_fingerprint_sha256"]) == 64
    assert len(a["report_sha256"]) == 64


@pytest.mark.parametrize("fault,expected", [
    ("delayed", "VISIBLE_CAPTURE_COUNT_MISMATCH"),
    ("missing", "FIRST_CAPTURE_BAR_MEMBERSHIP_INVALID"),
    ("overlap", "TICK_INTERVAL_OVERLAP"),
    ("conflict", "TICK_SOURCE_INTEGRITY_INVALID"),
    ("reordered", "TICK_INTERVAL_GAP"),
])
def test_m51_each_adversarial_scenario_has_specific_code(fault, expected):
    report = checked(fault)
    assert report["status"] == "BLOCKED"
    assert report["council_invoked"] is False
    assert report["council_seats"] == 0
    assert report["final_action"] == "HOLD"
    assert any(reason.endswith(expected) for reason in report["reason_codes"])


def test_m49_missing_and_overlap_are_not_same_evidence():
    missing = checked("missing")
    overlap = checked("overlap")
    assert missing["evidence_fingerprint_sha256"] != overlap["evidence_fingerprint_sha256"]
    assert missing["report_sha256"] != overlap["report_sha256"]
    assert missing["reason_codes"] != overlap["reason_codes"]


def test_m49_all_six_scenarios_have_distinct_hashes():
    records = [checked(name) for name in
               ("none", "delayed", "missing", "overlap", "conflict", "reordered")]
    assert len({item["evidence_fingerprint_sha256"] for item in records}) == 6


def test_m50_delayed_data_is_not_visible_at_decision_cutoff():
    store, policy, context, engine = make_variable_demo(fault="delayed")
    try:
        observed = audit._observed_at(store, policy, context.as_of)
        assert len(observed["captures"]) == 4
        assert len(observed["ticks"]) == 4
        assert len(observed["bars"]) == 4
        report = audit_variable_replay(store, policy, context,
                                       capture_count=5, synthetic_only=True)
        assert report["snapshots"][-1]["visible_captures"] == 4
    finally:
        engine.dispose()


@pytest.mark.parametrize("fault", ["delayed", "missing", "overlap", "conflict", "reordered"])
def test_m52_blocked_source_never_dispatches_the_council(monkeypatch, fault):
    def forbidden(*args, **kwargs):
        raise AssertionError("Council dispatch must be unreachable")
    monkeypatch.setattr(audit, "replay_variable_captures", forbidden)
    report = checked(fault)
    assert report["council_invoked"] is False


def test_m52_success_calls_existing_replay_once(monkeypatch, demo):
    store, policy, context, _ = demo
    calls = []
    real = audit.replay_variable_captures

    def wrapped(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(audit, "replay_variable_captures", wrapped)
    report = audit_variable_replay(store, policy, context,
                                   capture_count=5, synthetic_only=True)
    assert len(calls) == 1
    assert report["council_invoked"] is True


def test_m51_tampered_manifest_is_blocked_before_council(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(captures.update().values(manifest_json="{}"))
    report = audit_variable_replay(store, policy, context,
                                   capture_count=5, synthetic_only=True)
    assert "SNAPSHOT_1:CAPTURE_MANIFEST_INTEGRITY_INVALID" in report["reason_codes"]
    assert report["council_invoked"] is False


def test_m51_tampered_bar_is_blocked_before_council(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(bars.update().values(close=2.0))
    report = audit_variable_replay(store, policy, context,
                                   capture_count=5, synthetic_only=True)
    assert "SNAPSHOT_1:BAR_SOURCE_INTEGRITY_INVALID" in report["reason_codes"]
    assert report["council_invoked"] is False


def test_m51_epoch_tamper_is_blocked_before_council(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(epochs.update().values(policy_json="{}"))
    report = audit_variable_replay(store, policy, context,
                                   capture_count=5, synthetic_only=True)
    assert "SNAPSHOT_1:EPOCH_PROVENANCE_INVALID" in report["reason_codes"]
    assert report["council_invoked"] is False


def test_m49_same_failure_code_different_source_changes_digest():
    hashes = []
    for bid in (1.5, 2.0):
        store, policy, ctx, engine = make_variable_demo()
        try:
            with engine.begin() as conn:
                conn.execute(ticks.update().values(bid=bid))
            report = audit_variable_replay(store, policy, ctx,
                                           capture_count=5, synthetic_only=True)
            assert "SNAPSHOT_1:TICK_SOURCE_INTEGRITY_INVALID" in report["reason_codes"]
            hashes.append(report["evidence_fingerprint_sha256"])
        finally:
            engine.dispose()
    assert len(set(hashes)) == 2


def test_m52_assessment_makes_no_writes(demo):
    store, policy, context, engine = demo
    statements = []

    def spy(_conn, _cursor, statement, _params, _context, _executemany):
        statements.append(statement.strip().upper())

    event.listen(engine, "before_cursor_execute", spy)
    try:
        audit_variable_replay(store, policy, context,
                              capture_count=5, synthetic_only=True)
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert statements and all(s.startswith("SELECT") for s in statements)


def test_m52_disk_sqlite_refused(demo, tmp_path):
    _, policy, ctx, _ = demo
    engine = create_engine("sqlite:///" + str(tmp_path / "blocked.db"))
    try:
        with pytest.raises(CaptureReplayError, match="in-memory"):
            audit_variable_replay(ForwardFeedStore(engine), policy, ctx,
                                  capture_count=5, synthetic_only=True)
    finally:
        engine.dispose()


def test_m52_explicit_opt_in_required(demo):
    store, policy, ctx, _ = demo
    with pytest.raises(CaptureReplayError, match="opt-in"):
        audit_variable_replay(store, policy, ctx, capture_count=5)


def test_m52_cli_all_reports_reason_codes(capsys):
    assert run(["--synthetic-only", "--captures", "5", "--scenario", "all"]) == 0
    output = capsys.readouterr().out
    assert "SCENARIO complete | AUDITED_SYNTHETIC_COUNCIL_HOLD" in output
    assert "SNAPSHOT_3:FIRST_CAPTURE_BAR_MEMBERSHIP_INVALID" in output
    assert "SNAPSHOT_3:TICK_INTERVAL_OVERLAP" in output
    assert "SCENARIO delayed | BLOCKED" in output
    assert output.count("seats=31 | FINAL=HOLD") == 1
    assert output.count("seats=0 | FINAL=HOLD") == 5
    assert "NO BROKER" in output


def test_m52_cli_json_is_research_only(capsys):
    assert run(["--synthetic-only", "--captures", "8",
                "--scenario", "complete", "--json"]) == 0
    item, = json.loads(capsys.readouterr().out)
    report = item["result"]
    assert len(report["snapshots"]) == 8
    assert report["council_seats"] == 31
    assert report["execution_authorized"] is False
    assert report["final_action"] == "HOLD"


def test_m52_cli_flag_required():
    with pytest.raises(SystemExit) as exc:
        run(["--scenario", "complete"])
    assert exc.value.code == 2


def test_m52_invalid_capture_count_fails_closed(capsys):
    assert run(["--synthetic-only", "--captures", "9"]) == 1
    assert "AUDIT BLOCKED" in capsys.readouterr().out
