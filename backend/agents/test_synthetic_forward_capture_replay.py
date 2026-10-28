
"""M41-44: three capture windows, as-of snapshots, tampering and Council HOLD."""

from datetime import timedelta
import json

import pytest
from sqlalchemy import create_engine, event, select

from backend.agents.synthetic_forward_capture_replay import (
    CaptureReplayError, make_replay_demo, replay_synthetic_captures, run,
)
from backend.data_engine.clock_policy import ClockPolicyError
from backend.data_engine.forward_store import (
    ForwardFeedStore, epochs, captures, ticks, bars,
)


@pytest.fixture
def demo():
    store, policy, context, engine = make_replay_demo()
    try:
        yield store, policy, context, engine
    finally:
        engine.dispose()


def test_m41_three_contiguous_provenance_captures(demo):
    store, policy, context, engine = demo
    with engine.connect() as conn:
        caps = conn.execute(select(captures).order_by(captures.c.tick_end)).mappings().all()
        assert len(caps) == 3
        assert all(caps[i]["tick_end"] == caps[i+1]["tick_start"] for i in (0, 1))
        assert len({c["capture_id"] for c in caps}) == 3
    report = replay_synthetic_captures(store, policy, context, synthetic_only=True)
    assert report["status"] == "SYNTHETIC_THREE_CAPTURE_SHADOW_ONLY"
    assert [s["visible_captures"] for s in report["snapshot_receipts"]] == [1, 2, 3]
    assert all(s["status"] == "LOCAL_SYNTHETIC_SNAPSHOT_ONLY"
               for s in report["snapshot_receipts"])
    assert len({s["first_capture_id"] for s in report["snapshot_receipts"]}) == 3


def test_m42_no_future_snapshot_rows_and_correct_cutoffs(demo):
    store, policy, context, _ = demo
    report = replay_synthetic_captures(store, policy, context, synthetic_only=True)
    assert [r["as_of"] for r in report["snapshot_receipts"]] == [
        (context.as_of-timedelta(minutes=10)).isoformat(),
        (context.as_of-timedelta(minutes=5)).isoformat(),
        context.as_of.isoformat(),
    ]
    assert report["source_provenance_independently_attested"] is False
    assert report["real_forward_admission"] is False


def test_m44_real_council_shadow_gets_mapped_synthetic_final_tick_and_bar(demo):
    store, policy, context, _ = demo
    report = replay_synthetic_captures(store, policy, context, synthetic_only=True)
    assert report["market_inputs_from_verified_synthetic_captures"] is True
    assert report["council_invoked"] is True
    assert report["council_seats"] == 31
    assert report["council_report_sha256"]
    assert report["final_action"] == "HOLD"
    assert report["execution_authorized"] is False
    assert report["training_authorized"] is False
    assert report["historical_admission"] is False


def test_m44_deterministic_replay_receipt(demo):
    store, policy, context, _ = demo
    a = replay_synthetic_captures(store, policy, context, synthetic_only=True)
    b = replay_synthetic_captures(store, policy, context, synthetic_only=True)
    assert a == b
    assert len(a["report_sha256"]) == 64


def test_m43_delayed_third_capture_cannot_be_backfilled():
    store, policy, context, engine = make_replay_demo(fault="delayed")
    try:
        report = replay_synthetic_captures(store, policy, context, synthetic_only=True)
        assert report["status"] == "BLOCKED"
        assert report["council_invoked"] is False
        assert report["council_seats"] == 0
        assert [s["visible_captures"] for s in report["snapshot_receipts"]] == [1, 2, 2]
        assert "EXPECTED_THREE_FIRST_CAPTURE_BATCHES" in report["blockers"]
        assert "SNAPSHOT_3:M5_NOT_CURRENT" in report["blockers"]
    finally:
        engine.dispose()


def test_m43_duplicate_or_unprovable_capture_membership_fails():
    store, policy, context, engine = make_replay_demo(fault="duplicate")
    try:
        with pytest.raises(CaptureReplayError, match="gap or overlap|Duplicate|unprovable"):
            replay_synthetic_captures(store, policy, context, synthetic_only=True)
    finally:
        engine.dispose()


def test_m43_altered_capture_manifest_fails(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(captures.update().values(manifest_json="{}"))
    with pytest.raises(CaptureReplayError, match="manifest"):
        replay_synthetic_captures(store, policy, context, synthetic_only=True)


def test_m43_source_tick_price_tampering_fails(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(ticks.update().values(bid=2.0))
    with pytest.raises(CaptureReplayError, match="Tick raw"):
        replay_synthetic_captures(store, policy, context, synthetic_only=True)


def test_m43_source_candle_price_tampering_fails(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(bars.update().values(close=2.0))
    with pytest.raises(CaptureReplayError, match="Candle raw"):
        replay_synthetic_captures(store, policy, context, synthetic_only=True)


def test_m43_source_raw_time_tampering_fails(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(ticks.update().values(raw_time_msc=100))
    with pytest.raises(CaptureReplayError, match="Tick raw"):
        replay_synthetic_captures(store, policy, context, synthetic_only=True)


def test_m43_clock_discontinuity_is_rejected(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        latest = conn.execute(select(captures.c.capture_id).order_by(
            captures.c.tick_end.desc()).limit(1)).scalar_one()
        conn.execute(captures.update().where(captures.c.capture_id == latest).values(
            tick_start=context.as_of-timedelta(minutes=4)))
    with pytest.raises(CaptureReplayError, match="manifest|interval"):
        replay_synthetic_captures(store, policy, context, synthetic_only=True)


def test_m43_missing_middle_tick_is_rejected(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(ticks.delete().where(ticks.c.timestamp ==
                    context.as_of-timedelta(minutes=5, seconds=10)))
    with pytest.raises(CaptureReplayError, match="membership"):
        replay_synthetic_captures(store, policy, context, synthetic_only=True)


def test_m43_missing_middle_bar_is_rejected(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(bars.delete().where(bars.c.timestamp ==
                    context.as_of-timedelta(minutes=10)))
    with pytest.raises(CaptureReplayError, match="membership"):
        replay_synthetic_captures(store, policy, context, synthetic_only=True)


def test_m43_epoch_policy_tampering_is_rejected(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(epochs.update().values(policy_json="{}"))
    with pytest.raises(CaptureReplayError, match="Epoch provenance"):
        replay_synthetic_captures(store, policy, context, synthetic_only=True)


def test_m43_reject_disk_store_without_reading(tmp_path, demo):
    _, policy, context, _ = demo
    disk = create_engine("sqlite:///" + str(tmp_path/"disallowed.db"))
    try:
        with pytest.raises(CaptureReplayError, match="in-memory"):
            replay_synthetic_captures(ForwardFeedStore(disk), policy, context,
                                       synthetic_only=True)
    finally:
        disk.dispose()


def test_m43_requires_synthetic_opt_in(demo):
    store, policy, context, _ = demo
    with pytest.raises(CaptureReplayError, match="opt-in"):
        replay_synthetic_captures(store, policy, context)


def test_m43_rejects_changed_context(demo):
    store, policy, context, _ = demo
    bad = context.model_copy(update={"fed_data": ()})
    with pytest.raises(CaptureReplayError, match="fixture"):
        replay_synthetic_captures(store, policy, bad, synthetic_only=True)


def test_m44_assessment_select_only(demo):
    store, policy, context, engine = demo
    observed = []

    def watch(_connection, _cursor, statement, _parameters, _context, _executemany):
        observed.append(statement.strip().upper())

    event.listen(engine, "before_cursor_execute", watch)
    try:
        replay_synthetic_captures(store, policy, context, synthetic_only=True)
    finally:
        event.remove(engine, "before_cursor_execute", watch)
    assert observed
    assert all(s.startswith("SELECT") for s in observed)


def test_m44_cli_all_scenarios(capsys):
    assert run(["--synthetic-only", "--scenario", "all"]) == 0
    output = capsys.readouterr().out
    assert "SCENARIO complete | SYNTHETIC_THREE_CAPTURE_SHADOW_ONLY" in output
    assert "SCENARIO delayed | BLOCKED" in output
    assert "SCENARIO duplicate | BLOCKED_INTEGRITY" in output
    assert "seats=31 | FINAL=HOLD" in output
    assert output.count("seats=0 | FINAL=HOLD") == 2
    assert "NO BROKER" in output


def test_m44_cli_json_enforces_no_admission(capsys):
    assert run(["--synthetic-only", "--scenario", "complete", "--json"]) == 0
    data, = json.loads(capsys.readouterr().out)
    report = data["result"]
    assert report["council_invoked"] is True
    assert report["execution_authorized"] is False
    assert report["final_action"] == "HOLD"


def test_m44_cli_explicit_synthetic_flag_required():
    with pytest.raises(SystemExit) as exc:
        run(["--scenario", "complete"])
    assert exc.value.code == 2
