
"""M37-40 synthetic mapping, provenance, causal rejection and real Council tests."""

from datetime import timedelta
import json

import pytest
from sqlalchemy import create_engine, event

from backend.agents.synthetic_forward_causal_bridge import (
    CausalBridgeError, make_synthetic_demo, run_causal_synthetic_bridge,
    source_rows, run,
)
from backend.agents.forward_evidence_shadow import inspect_forward_evidence
from backend.data_engine.forward_store import (
    ForwardFeedStore, bars, captures, ticks,
)


@pytest.fixture
def demo():
    store, policy, context, engine = make_synthetic_demo()
    try:
        yield store, policy, context, engine
    finally:
        engine.dispose()


def test_m37_normalized_records_map_to_council_types(demo):
    store, policy, context, _ = demo
    evidence = inspect_forward_evidence(store, policy, context.as_of)
    result = source_rows(store, policy, context.as_of, evidence)
    assert result["bar"] == context.candles[-1]
    assert result["tick"].timestamp == context.ticks[-1].timestamp
    assert result["tick"].bid == context.ticks[-1].bid
    assert result["tick"].ask == context.ticks[-1].ask
    assert result["tick"].source_time_msc != context.ticks[-1].source_time_msc
    assert result["tick_capture"] == result["bar_capture"]


def test_m38_proof_retains_source_capture_and_no_attestation(demo):
    store, policy, context, _ = demo
    result = run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)
    assert len(result["first_capture_ids"]) == 2
    assert result["mapped_tick_id"]
    assert result["mapped_candle_id"]
    assert result["independent_broker_attestation"] is False
    assert result["production_admission"] is False
    assert result["historical_admission"] is False
    assert result["training_authorized"] is False
    assert result["execution_authorized"] is False


def test_m40_canonical_council_gets_mapped_synthetic_inputs(demo):
    store, policy, context, _ = demo
    outcome = run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)
    assert outcome["status"] == "SYNTHETIC_MATCHED_SHADOW_ONLY"
    assert outcome["mapped_synthetic_records_in_council"] is True
    assert outcome["canonical_council_invoked"] is True
    assert outcome["council_seats"] == 31
    assert outcome["final_action"] == "HOLD"


def test_m40_repeated_reports_have_equal_hash(demo):
    store, policy, context, _ = demo
    first = run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)
    second = run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)
    assert first == second
    assert len(first["report_sha256"]) == 64


def test_m39_requires_opt_in_before_source_read(demo):
    store, policy, context, _ = demo
    with pytest.raises(CausalBridgeError, match="opt-in"):
        run_causal_synthetic_bridge(store, policy, context)


def test_m39_rejects_unpinned_epoch(demo):
    store, policy, context, _ = demo
    altered = policy.model_copy(update={"evidence_sha256": "0" * 64})
    with pytest.raises(CausalBridgeError, match="identity"):
        run_causal_synthetic_bridge(store, altered, context, synthetic_only=True)


def test_m39_rejects_real_or_disk_database(demo, tmp_path):
    _, policy, context, _ = demo
    engine = create_engine("sqlite:///" + str(tmp_path / "not_allowed.sqlite"))
    try:
        store = ForwardFeedStore(engine)
        with pytest.raises(CausalBridgeError, match="in-memory"):
            run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)
    finally:
        engine.dispose()


def test_m39_rejects_modified_council_context(demo):
    store, policy, context, _ = demo
    corrupted = context.model_copy(update={"as_of": context.as_of + timedelta(seconds=1)})
    with pytest.raises(CausalBridgeError, match="synthetic fixture"):
        run_causal_synthetic_bridge(store, policy, corrupted, synthetic_only=True)


def test_m39_rejects_altered_macro_inputs(demo):
    store, policy, context, _ = demo
    corrupted = context.model_copy(update={"fed_data": ()})
    with pytest.raises(CausalBridgeError, match="synthetic fixture"):
        run_causal_synthetic_bridge(store, policy, corrupted, synthetic_only=True)


def test_m39_tampered_tick_price_fails_closed(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(ticks.update().values(bid=1.5))
    with pytest.raises(Exception):
        run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)


def test_m39_tampered_bar_price_fails_closed(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(bars.update().values(close=2.0))
    with pytest.raises(Exception):
        run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)


def test_m39_tampered_capture_manifest_fails_closed(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(captures.update().values(manifest_json='{"changed":true}'))
    with pytest.raises(Exception):
        run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)


def test_m39_original_source_time_tampering_fails_closed(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(ticks.update().values(raw_time_msc=100))
    with pytest.raises(Exception):
        run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)


def test_m39_missing_bar_yields_hold_without_council(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(bars.delete())
    result = run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)
    assert result["status"] == "BLOCKED"
    assert result["council_seats"] == 0
    assert result["final_action"] == "HOLD"
    assert "FORWARD_BAR_MISSING" in result["blockers"]


def test_m39_missing_tick_yields_hold_without_council(demo):
    store, policy, context, engine = demo
    with engine.begin() as conn:
        conn.execute(ticks.delete())
    result = run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)
    assert result["status"] == "BLOCKED"
    assert result["council_seats"] == 0
    assert "FORWARD_TICK_MISSING" in result["blockers"]


def test_m39_mismatched_quote_fails_before_council(demo, monkeypatch):
    from backend.agents import synthetic_forward_causal_bridge as bridge
    store, policy, context, _ = demo
    original = bridge.source_rows

    def forged(*args):
        result = original(*args)
        result["tick"] = result["tick"].model_copy(update={"bid": 1.0})
        return result

    monkeypatch.setattr(bridge, "source_rows", forged)
    with pytest.raises(CausalBridgeError, match="quote"):
        run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)


def test_m39_mismatched_candle_fails_before_council(demo, monkeypatch):
    from backend.agents import synthetic_forward_causal_bridge as bridge
    store, policy, context, _ = demo
    original = bridge.source_rows

    def forged(*args):
        result = original(*args)
        result["bar"] = result["bar"].model_copy(update={"close": 2.0})
        return result

    monkeypatch.setattr(bridge, "source_rows", forged)
    with pytest.raises(CausalBridgeError, match="market-state anchor"):
        run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)


def test_m39_no_writes_when_mapping_and_running(demo):
    store, policy, context, engine = demo
    statements = []

    def spy(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement.lstrip().upper())

    event.listen(engine, "before_cursor_execute", spy)
    try:
        run_causal_synthetic_bridge(store, policy, context, synthetic_only=True)
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    assert statements
    assert all(statement.startswith("SELECT") for statement in statements)


def test_m40_cli_mapped_and_stale(capsys):
    assert run(["--synthetic-only", "--scenario", "both"]) == 0
    output = capsys.readouterr().out
    assert "SCENARIO matched | SYNTHETIC_MATCHED_SHADOW_ONLY" in output
    assert "SCENARIO stale | BLOCKED" in output
    assert "mapped=True | seats=31 | FINAL=HOLD" in output
    assert "mapped=False | seats=0 | FINAL=HOLD" in output
    assert "NO BROKER" in output


def test_m40_cli_json_preserves_boundaries(capsys):
    assert run(["--synthetic-only", "--scenario", "both", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result) == 2
    for item in result:
        r = item["result"]
        assert r["final_action"] == "HOLD"
        assert r["execution_authorized"] is False
        assert r["independent_broker_attestation"] is False
    assert result[1]["result"]["canonical_council_invoked"] is False


def test_m40_cli_requires_synthetic_only():
    with pytest.raises(SystemExit) as error:
        run(["--scenario", "matched"])
    assert error.value.code == 2
