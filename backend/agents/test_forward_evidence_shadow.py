
"""M33-36: forward source, freshness, fail-closed and inert Council contracts."""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest
from sqlalchemy import create_engine, event, select

from backend.agents.forward_evidence_shadow import (
    ForwardShadowError, inspect_forward_evidence, shadow_forward_gate, run,
)
from backend.data_engine.clock_policy import (
    ClockPolicy, implementation_sha256, utc_msc, hfm_utc_to_wall,
)
from backend.data_engine.forward_store import (
    ForwardFeedStore, epochs, ticks, bars, captures,
)
from backend.agents.test_full_research_council import synthetic_full_context

NOW = datetime(2026, 10, 27, 14, tzinfo=timezone.utc)


@pytest.fixture
def sample():
    engine = create_engine("sqlite://")
    clock = [NOW]
    store = ForwardFeedStore(engine, clock=lambda: clock[0])
    store.initialize()
    lease = ClockPolicy(
        company_sha256=sha256(b"SYNTHETIC_COMPANY").hexdigest(),
        server_sha256=sha256(b"SYNTHETIC_SERVER").hexdigest(),
        evidence_sha256="a" * 64,
        implementation_sha256=implementation_sha256(),
        valid_from=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(days=1),
    )
    ms = utc_msc(hfm_utc_to_wall(NOW - timedelta(seconds=2)))
    bar_start = utc_msc(hfm_utc_to_wall(NOW - timedelta(minutes=5))) // 1000
    tick = dict(time=ms // 1000, time_msc=ms, bid=1.1, ask=1.1001,
                last=0.0, volume=0, flags=6, volume_real=0.0)
    bar = dict(time=bar_start, open=1.1, high=1.101, low=1.099,
               close=1.1005, tick_volume=100, spread=10, real_volume=0)
    store.append(lease, tick_rows=[lease.tick(tick)], bar_rows=[lease.rate(bar)],
                 received_at=NOW, tick_start=NOW - timedelta(minutes=1),
                 tick_end=NOW, bar_start=NOW - timedelta(minutes=10),
                 bar_end=NOW)
    try:
        yield engine, clock, store, lease
    finally:
        engine.dispose()


def test_m33_revalidates_epoch_raw_identity_and_first_capture(sample):
    _, _, store, lease = sample
    result = inspect_forward_evidence(store, lease, NOW)
    assert result["status"] == "LOCAL_EVIDENCE_CHECKS_PASS_RESEARCH_ONLY"
    assert result["epoch_id"] == lease.epoch_id
    assert len(result["evidence_sha256"]) == 64
    assert not result["blockers"]
    assert result["latest_records"]["tick"]["age_seconds"] == 2
    assert result["latest_records"]["bar"]["age_seconds"] == 0
    assert all(result["latest_records"][key]["raw_normalization_and_local_capture_verified"]
               for key in ("tick", "bar"))
    assert result["independent_external_receipt_attestation"] is False
    assert result["source_admitted_to_council"] is False
    assert result["execution_authorized"] is False


def test_m34_revalidates_age_policy_and_staleness(sample):
    _, clock, store, lease = sample
    clock[0] = NOW + timedelta(minutes=3)
    report = inspect_forward_evidence(store, lease, clock[0])
    assert report["status"] == "BLOCKED"
    assert "UTC_TICKS_STALE" in report["blockers"]
    assert "FORWARD_TICK_STALE_BY_POLICY" in report["blockers"]


def test_m34_custom_freshness_thresholds_are_honored(sample):
    _, clock, store, lease = sample
    clock[0] = NOW + timedelta(seconds=10)
    assert "FORWARD_TICK_STALE_BY_POLICY" in inspect_forward_evidence(
        store, lease, clock[0], max_tick_age_seconds=10)["blockers"]
    assert not inspect_forward_evidence(
        store, lease, clock[0], max_tick_age_seconds=20)["blockers"]


@pytest.mark.parametrize("name,value", [
    ("max_tick_age_seconds", 0), ("max_tick_age_seconds", 3601),
    ("max_tick_age_seconds", True), ("max_tick_age_seconds", "120"),
    ("max_bar_age_seconds", 0), ("max_bar_age_seconds", -1),
    ("max_bar_age_seconds", True), ("max_bar_age_seconds", 3601),
])
def test_m35_invalid_limits_rejected_before_io(name, value):
    with pytest.raises(ForwardShadowError):
        inspect_forward_evidence(None, None, None, **{name: value})


def test_m35_wrong_epoch_fails_closed(sample):
    _, _, store, lease = sample
    alt = lease.model_copy(update={"evidence_sha256": "b" * 64})
    with pytest.raises(ForwardShadowError, match="epoch"):
        inspect_forward_evidence(store, alt, NOW)


def test_m35_tampered_raw_tick_fails_closed(sample):
    engine, _, store, lease = sample
    with engine.begin() as connection:
        connection.execute(ticks.update().values(raw_json='{"bid":1}'))
    with pytest.raises(ForwardShadowError):
        inspect_forward_evidence(store, lease, NOW)


def test_m35_tampered_capture_manifest_fails_closed(sample):
    engine, _, store, lease = sample
    with engine.begin() as connection:
        connection.execute(captures.update().values(manifest_json='{}'))
    with pytest.raises(ForwardShadowError, match="capture|receipt"):
        inspect_forward_evidence(store, lease, NOW)


def test_m35_duplicated_json_field_fails_closed(sample):
    engine, _, store, lease = sample
    with engine.begin() as connection:
        connection.execute(ticks.update().values(raw_json='{"bid":1,"bid":2}'))
    with pytest.raises(ForwardShadowError):
        inspect_forward_evidence(store, lease, NOW)


def test_m35_tampered_epoch_policy_fails_closed(sample):
    engine, _, store, lease = sample
    with engine.begin() as connection:
        connection.execute(epochs.update().values(policy_json='{}'))
    with pytest.raises(ForwardShadowError, match="epoch"):
        inspect_forward_evidence(store, lease, NOW)


def test_m35_tampered_bar_fails_closed(sample):
    engine, _, store, lease = sample
    with engine.begin() as connection:
        connection.execute(bars.update().values(close=2.0))
    with pytest.raises(ForwardShadowError):
        inspect_forward_evidence(store, lease, NOW)


def test_m35_missing_bar_blocks_no_imputation(sample):
    engine, _, store, lease = sample
    with engine.begin() as connection:
        connection.execute(bars.delete())
    result = inspect_forward_evidence(store, lease, NOW)
    assert result["status"] == "BLOCKED"
    assert result["latest_records"]["bar"] is None
    assert "FORWARD_BAR_MISSING" in result["blockers"]


def test_m35_source_inspection_is_select_only(sample):
    engine, _, store, lease = sample
    queries = []

    def spy(_conn, _cursor, statement, _params, _context, _executemany):
        queries.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", spy)
    try:
        inspect_forward_evidence(store, lease, NOW)
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert queries
    assert all(statement.startswith("SELECT") for statement in queries)


def test_m35_clock_with_non_utc_offset_rejected(sample):
    _, _, store, lease = sample
    with pytest.raises(ForwardShadowError):
        inspect_forward_evidence(store, lease, NOW.astimezone(
            timezone(timedelta(hours=1))))


def test_m35_no_implicit_synthetic_council_context(sample):
    _, _, store, lease = sample
    with pytest.raises(ForwardShadowError, match="opt-in"):
        shadow_forward_gate(store, lease, NOW,
                            synthetic_council_context=synthetic_full_context())


def test_m36_rejected_evidence_never_invokes_council(sample, monkeypatch):
    from backend.agents import synthetic_council_shadow as shadow
    _, clock, store, lease = sample
    clock[0] = NOW + timedelta(minutes=3)

    def deny(*args, **kwargs):
        raise AssertionError("Council must not run on stale feed")

    monkeypatch.setattr(shadow, "run_shadow_council", deny)
    report = shadow_forward_gate(store, lease, clock[0],
                                  synthetic_only=True,
                                  synthetic_council_context=synthetic_full_context())
    assert report["synthetic_council_test"] is None
    assert report["final_action"] == "HOLD"
    assert report["source_admitted_to_council"] is False


def test_m36_fresh_synthetic_gate_runs_separate_real_council(sample):
    _, _, store, lease = sample
    report = shadow_forward_gate(store, lease, NOW,
                                  synthetic_only=True,
                                  synthetic_council_context=synthetic_full_context())
    council = report["synthetic_council_test"]
    assert council["council_seat_count"] == 31
    assert council["final_action"] == "HOLD"
    assert council["execution_authorized"] is False
    assert report["forward_rows_supplied_to_council"] is False
    assert report["source_admitted_to_council"] is False
    assert report["training_authorized"] is False
    assert report["execution_authorized"] is False


def test_m36_same_evidence_produces_deterministic_report(sample):
    _, _, store, lease = sample
    first = shadow_forward_gate(store, lease, NOW)
    second = shadow_forward_gate(store, lease, NOW)
    assert first == second
    assert len(first["report_sha256"]) == 64


def test_cli_synthetic_both_scenarios(capsys):
    assert run(["--synthetic-only", "--scenario", "both"]) == 0
    output = capsys.readouterr().out
    assert "SCENARIO complete | LOCAL_EVIDENCE_CHECKS_PASS_RESEARCH_ONLY" in output
    assert "SCENARIO stale | BLOCKED" in output
    assert "synthetic_seats=31" in output
    assert "synthetic_seats=0" in output
    assert output.count("FINAL=HOLD") == 2
    assert "NO BROKER" in output


def test_cli_json_without_admission(capsys):
    assert run(["--synthetic-only", "--scenario", "stale", "--json"]) == 0
    value, = json.loads(capsys.readouterr().out)
    assert value["result"]["final_action"] == "HOLD"
    assert value["result"]["synthetic_council_test"] is None
    assert value["result"]["source_admitted_to_council"] is False


def test_cli_missing_explicit_synthetic_only():
    with pytest.raises(SystemExit) as exc:
        run(["--scenario", "complete"])
    assert exc.value.code == 2
