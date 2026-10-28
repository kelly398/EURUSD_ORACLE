
"""M45-48: bounded variable replay, as-of chronology and real Council HOLD."""

from datetime import timedelta
import json

import pytest
from sqlalchemy import create_engine, event, select

from backend.agents.synthetic_forward_variable_replay import (
    CaptureReplayError, make_variable_demo, replay_variable_captures, run,
)
from backend.data_engine.forward_store import (
    ForwardFeedStore, bars, captures, epochs, ticks,
)


@pytest.fixture
def demo():
    store, policy, ctx, engine = make_variable_demo(capture_count=5)
    try:
        yield store, policy, ctx, engine
    finally:
        engine.dispose()


@pytest.mark.parametrize("size", [2, 3, 5, 8])
def test_m45_variable_capture_counts_complete_and_hold(size):
    store, policy, ctx, engine = make_variable_demo(capture_count=size)
    try:
        result = replay_variable_captures(
            store, policy, ctx, capture_count=size, synthetic_only=True)
        assert result["status"] == "SYNTHETIC_VARIABLE_REPLAY_HOLD"
        assert len(result["snapshot_receipts"]) == size
        assert [x["visible_captures"] for x in result["snapshot_receipts"]] == list(
            range(1, size + 1))
        assert [x["expected_visible_captures"] for x in result["snapshot_receipts"]] == list(
            range(1, size + 1))
        assert result["council_runs"] == 1
        assert result["early_cutoff_council_runs"] == 0
        assert result["council_seats"] == 31
        assert result["final_action"] == "HOLD"
        assert result["historical_admission"] is False
        assert result["training_authorized"] is False
        assert result["execution_authorized"] is False
    finally:
        engine.dispose()


def test_m46_every_prefix_has_unique_digest_and_no_future_receipt(demo):
    store, policy, ctx, _ = demo
    result = replay_variable_captures(store, policy, ctx, capture_count=5,
                                      synthetic_only=True)
    prefix = result["snapshot_receipts"]
    assert len({x["first_capture_id"] for x in prefix}) == 5
    assert len({x["verified_prefix_sha256"] for x in prefix}) == 5
    assert [x["as_of"] for x in prefix] == [
        (ctx.as_of - timedelta(minutes=5 * i)).isoformat()
        for i in (4, 3, 2, 1, 0)
    ]
    assert all(x["status"] == "LOCAL_SYNTHETIC_SNAPSHOT_ONLY" for x in prefix)
    assert result["future_receipts_used"] is False
    assert result["independent_broker_attestation"] is False
    assert result["source_admitted_to_production"] is False


def test_m48_identical_reports_are_deterministic(demo):
    store, policy, ctx, _ = demo
    a = replay_variable_captures(store, policy, ctx, capture_count=5,
                                 synthetic_only=True)
    b = replay_variable_captures(store, policy, ctx, capture_count=5,
                                 synthetic_only=True)
    assert a == b
    assert len(a["report_sha256"]) == 64


def test_m47_delayed_last_receipt_cannot_backfill():
    store, policy, ctx, engine = make_variable_demo(capture_count=5, fault="delayed")
    try:
        report = replay_variable_captures(store, policy, ctx, capture_count=5,
                                          synthetic_only=True)
        assert report["status"] == "BLOCKED"
        assert report["council_runs"] == 0
        assert report["council_seats"] == 0
        assert report["snapshot_receipts"][-1]["visible_captures"] == 4
        assert "SNAPSHOT_5:EXPECTED_5_VISIBLE_CAPTURES" in report["blockers"]
    finally:
        engine.dispose()


@pytest.mark.parametrize("fault,reason", [
    ("missing", "membership"),
    ("overlap", "membership|gap or overlap"),
    ("conflict", "Tick raw"),
    ("reordered", "manifest|interval"),
])
def test_m47_integrity_faults_fail_closed(fault, reason):
    store, policy, ctx, engine = make_variable_demo(capture_count=5, fault=fault)
    try:
        with pytest.raises(CaptureReplayError, match=reason):
            replay_variable_captures(store, policy, ctx, capture_count=5,
                                     synthetic_only=True)
    finally:
        engine.dispose()


def test_m47_capture_manifest_tamper_fails_closed(demo):
    store, policy, ctx, engine = demo
    with engine.begin() as conn:
        conn.execute(captures.update().values(manifest_json="{}"))
    with pytest.raises(CaptureReplayError, match="manifest"):
        replay_variable_captures(store, policy, ctx, capture_count=5,
                                 synthetic_only=True)


def test_m47_epoch_policy_tamper_fails_closed(demo):
    store, policy, ctx, engine = demo
    with engine.begin() as conn:
        conn.execute(epochs.update().values(policy_json="{}"))
    with pytest.raises(CaptureReplayError, match="Epoch provenance"):
        replay_variable_captures(store, policy, ctx, capture_count=5,
                                 synthetic_only=True)


def test_m47_wrong_fixture_market_state_rejected(demo):
    store, policy, ctx, _ = demo
    forged = ctx.model_copy(update={"fed_data": ()})
    with pytest.raises(CaptureReplayError, match="fixture"):
        replay_variable_captures(store, policy, forged, capture_count=5,
                                 synthetic_only=True)


def test_m47_wrong_capture_count_does_not_silently_reuse_rows(demo):
    store, policy, ctx, _ = demo
    report = replay_variable_captures(store, policy, ctx, capture_count=4,
                                      synthetic_only=True)
    assert report["status"] == "BLOCKED"
    assert report["council_runs"] == 0
    assert any("EXPECTED_1_VISIBLE_CAPTURES" in x for x in report["blockers"])


@pytest.mark.parametrize("count", [0, 1, 9, True, "5", 5.5])
def test_m45_unbounded_or_invalid_count_rejected(count, demo):
    store, policy, ctx, _ = demo
    with pytest.raises(CaptureReplayError, match="Capture count"):
        replay_variable_captures(store, policy, ctx, capture_count=count,
                                 synthetic_only=True)


def test_m47_synthetic_opt_in_mandatory(demo):
    store, policy, ctx, _ = demo
    with pytest.raises(CaptureReplayError, match="opt-in"):
        replay_variable_captures(store, policy, ctx, capture_count=5)


def test_m47_disk_store_blocked_before_access(demo, tmp_path):
    _, policy, ctx, _ = demo
    db = create_engine("sqlite:///" + str(tmp_path / "disallowed.sqlite"))
    try:
        with pytest.raises(CaptureReplayError, match="in-memory"):
            replay_variable_captures(ForwardFeedStore(db), policy, ctx,
                                     capture_count=5, synthetic_only=True)
    finally:
        db.dispose()


def test_m48_assessment_is_select_only(demo):
    store, policy, ctx, engine = demo
    statements = []

    def observe(_conn, _cursor, statement, _params, _ctx, _many):
        statements.append(statement.strip().upper())

    event.listen(engine, "before_cursor_execute", observe)
    try:
        replay_variable_captures(store, policy, ctx, capture_count=5,
                                 synthetic_only=True)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert statements
    assert all(s.startswith("SELECT") for s in statements)


def test_m48_cli_completes_multiple_cases_and_blocks_faults(capsys):
    assert run(["--synthetic-only", "--captures", "5", "--scenario", "all"]) == 0
    output = capsys.readouterr().out
    assert "SCENARIO complete | SYNTHETIC_VARIABLE_REPLAY_HOLD" in output
    assert "SCENARIO delayed | BLOCKED" in output
    for case in ("missing", "overlap", "conflict", "reordered"):
        assert f"SCENARIO {case} | BLOCKED_INTEGRITY" in output
    assert output.count("council_runs=1 | seats=31 | FINAL=HOLD") == 1
    assert output.count("council_runs=0 | seats=0 | FINAL=HOLD") == 5
    assert "NO BROKER" in output


def test_m48_cli_json_real_council_multiple_lengths(capsys):
    for size in (2, 8):
        assert run(["--synthetic-only", "--captures", str(size),
                    "--scenario", "complete", "--json"]) == 0
        item, = json.loads(capsys.readouterr().out)
        result = item["result"]
        assert len(result["snapshot_receipts"]) == size
        assert result["council_runs"] == 1
        assert result["execution_authorized"] is False
        assert result["final_action"] == "HOLD"


def test_m48_cli_requires_synthetic_only():
    with pytest.raises(SystemExit) as exc:
        run(["--captures", "5"])
    assert exc.value.code == 2


def test_m48_cli_invalid_bound_fails_closed(capsys):
    assert run(["--synthetic-only", "--captures", "9"]) == 1
    assert "BLOCKED" in capsys.readouterr().out
