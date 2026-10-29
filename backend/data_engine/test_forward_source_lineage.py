
"""B4+C1-C3: forward recency, clock lineage, Council fields; no broker."""

from datetime import timedelta
import json

import pytest
from sqlalchemy import event

import backend.data_engine.forward_source_lineage as lineage
from backend.data_engine.forward_source_lineage import (
    FAMILY_MODELS, ForwardLineageError, _digest, evaluate_source_lineage,
    inspect_forward_source_lineage, inventory_council_sources, run,
)
from backend.data_engine.forward_evidence_health import inspect_forward_evidence
from backend.data_engine.test_forward_evidence_health import make_fixture
from backend.data_engine.forward_store import captures, ticks


@pytest.fixture
def demo():
    engine, policy, now = make_fixture()
    try:
        yield engine, policy, now
    finally:
        engine.dispose()


def health(demo):
    engine, policy, now = demo
    return inspect_forward_evidence(engine, epoch_id=policy.epoch_id, as_of=now)


def inspect(demo, **kwargs):
    engine, policy, now = demo
    return inspect_forward_source_lineage(
        engine, epoch_id=policy.epoch_id, as_of=now, **kwargs,
    )


def test_b4_last_capture_end_and_ingested_are_reported(demo):
    result = inspect(demo)
    _, _, now = demo
    assert result["capture_count_in_bounded_sample"] == 2
    assert result["last_visible_capture_query_end"] == now.isoformat()
    assert result["last_visible_capture_received_at"] == now.isoformat()
    assert result["last_visible_capture_ingested_at"] == now.isoformat()
    assert result["seconds_since_capture_ingestion"] == 0
    assert result["seconds_since_tick"] == 2
    assert result["seconds_since_m5_bar_open"] == 300
    assert result["collector_process_was_running"] == "NOT_INSPECTED"
    assert "UNKNOWN" in result["collection_stoppage_cause"]


def test_b4_old_capture_flags_no_recent_ingestion(demo):
    engine, policy, now = demo
    report = inspect_forward_source_lineage(
        engine, epoch_id=policy.epoch_id, as_of=now + timedelta(minutes=40),
    )
    assert "NO_RECENT_CAPTURE_INGESTION_AT_CUTOFF" in report["collection_observations"]
    assert report["seconds_since_capture_ingestion"] == 2400
    assert report["health_local_status"] == "BLOCKED"
    assert "LATEST_TICK_STALE_OR_FUTURE" in report["health_blockers"]
    assert report["final_action"] == "HOLD"


def test_b4_capture_age_is_configurable_without_mutation(demo):
    engine, policy, now = demo
    cutoff = now + timedelta(minutes=16)
    x = inspect_forward_source_lineage(
        engine, epoch_id=policy.epoch_id, as_of=cutoff, max_capture_age_seconds=900,
    )
    y = inspect_forward_source_lineage(
        engine, epoch_id=policy.epoch_id, as_of=cutoff, max_capture_age_seconds=1200,
    )
    assert "NO_RECENT_CAPTURE_INGESTION_AT_CUTOFF" in x["collection_observations"]
    assert "NO_RECENT_CAPTURE_INGESTION_AT_CUTOFF" not in y["collection_observations"]
    assert x["report_sha256"] != y["report_sha256"]


@pytest.mark.parametrize("value", [0, 59, 86401, True, "900", 5.0])
def test_b4_invalid_age_threshold_rejected(value, demo):
    with pytest.raises(ForwardLineageError, match="threshold"):
        evaluate_source_lineage(health(demo), max_capture_age_seconds=value)


def test_b4_real_query_overlap_not_assumed_feed_failure(demo):
    engine, policy, now = demo
    with engine.begin() as conn:
        conn.execute(captures.update().where(captures.c.tick_end == now)
                     .values(tick_start=now - timedelta(minutes=6)))
    report = inspect(demo)
    assert "OVERLAPPING_QUERY_WINDOWS_NOT_A_SOURCE_FAILURE" in report["collection_observations"]
    assert report["overlap_query_count"] >= 1
    assert report["real_forward_admission"] is False


def test_c1_current_clock_matches_synthetic_epoch(demo):
    r = inspect(demo)
    assert r["clock_lineage"]["same_local_code_hash"] is True
    assert r["clock_lineage"]["status"] == "LOCAL_CODE_HASH_MATCH_UNATTESTED"
    assert r["clock_lineage"]["historical_broker_utc_certified"] is False
    assert r["clock_lineage"]["stored_source_bytes_verified_against_external_anchor"] is False


def test_c1_changed_clock_hash_reports_blocker(demo, monkeypatch):
    monkeypatch.setattr(
        "backend.data_engine.forward_evidence_health.implementation_sha256",
        lambda: "e" * 64,
    )
    r = inspect(demo)
    assert r["clock_lineage"]["same_local_code_hash"] is False
    assert r["clock_lineage"]["status"] == "BLOCKED_BOUND_IMPLEMENTATION_DIFFERS"
    assert "LOCAL_CLOCK_IMPLEMENTATION_UNMATCHED" in r["health_blockers"]
    assert r["clock_lineage"]["change_cause_determined"] is False
    assert r["final_action"] == "HOLD"


def test_c2_all_thirteen_declared_council_fields_exist():
    rows = inventory_council_sources()
    assert len(rows) == len(FAMILY_MODELS) == 13
    assert len({item["family"] for item in rows}) == 13
    assert all(not item["real_first_receipt_attested"] for item in rows)
    assert all(not item["real_point_in_time_lineage_proven"] for item in rows)


def test_c2_actual_rate_and_calendar_models_expose_ingestion_fields():
    by_name = {r["family"]: r for r in inventory_council_sources()}
    for field in ("cot_data", "ecb_data", "fed_data", "economic_calendar", "cross_asset_data"):
        assert by_name[field]["has_ingestion_field"] is True
        assert by_name[field]["has_public_availability_field"] is True
    assert "scheduled_at" in by_name["economic_calendar"]["timestamp_fields"]
    assert by_name["risk_limits"]["has_ingestion_field"] is False
    assert by_name["synthesis_policy"]["has_ingestion_field"] is False


def test_c2_forward_candle_and_tick_context_omits_receipt_times():
    by_name = {r["family"]: r for r in inventory_council_sources()}
    assert "timestamp" in by_name["candles"]["timestamp_fields"]
    assert "timestamp" in by_name["ticks"]["timestamp_fields"]
    assert by_name["candles"]["has_ingestion_field"] is False
    assert by_name["ticks"]["has_ingestion_field"] is False
    assert by_name["market_state"]["has_ingestion_field"] is False


def test_c2_detects_drift_in_agent_context(monkeypatch):
    from backend.agents.contracts import AgentContext
    fake = dict(AgentContext.model_fields)
    fake.pop("cot_data")
    monkeypatch.setattr(AgentContext, "model_fields", fake)
    with pytest.raises(ForwardLineageError, match="contract changed"):
        inventory_council_sources()


def test_c3_all_family_provenance_is_explicitly_unverified(demo):
    r = inspect(demo)
    assert r["council_input_family_count"] == 13
    assert r["missing_external_attestations"] == 13
    assert r["real_council_source_lineage_verified"] is False
    assert all(x["status"] == "TYPED_CONTRACT_ONLY_UNVERIFIED"
               for x in r["council_input_families"])
    assert r["training_authorized"] is False
    assert r["execution_authorized"] is False
    assert r["final_action"] == "HOLD"


def test_c3_report_repeatable_and_hashed(demo):
    a = inspect(demo)
    b = inspect(demo)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})
    assert len(a["source_health_sha256"]) == 64


def test_c3_v1_or_forged_health_receipt_rejected(demo):
    report = health(demo)
    old = dict(report, assessment_version="EURUSD_FORWARD_EVIDENCE_HEALTH_V1")
    with pytest.raises(ForwardLineageError, match="V2"):
        evaluate_source_lineage(old)
    forged = dict(report)
    forged["captures_examined"] = 999
    with pytest.raises(ForwardLineageError, match="integrity invalid"):
        evaluate_source_lineage(forged)


def test_c3_acceptance_cannot_turn_hold_to_trade(demo):
    report = health(demo)
    forged = dict(report, execution_authorized=True)
    forged["report_sha256"] = _digest({k: v for k, v in forged.items() if k != "report_sha256"})
    with pytest.raises(ForwardLineageError, match="HOLD"):
        evaluate_source_lineage(forged)


def test_c3_all_sql_select_only_via_existing_inspector(demo):
    engine, policy, now = demo
    statements = []

    def track(_conn, _cursor, sql, _params, _ctx, _many):
        statements.append(sql.strip().upper())

    event.listen(engine, "before_cursor_execute", track)
    try:
        inspect(demo)
    finally:
        event.remove(engine, "before_cursor_execute", track)

    assert statements
    assert all(s.startswith("SELECT") for s in statements)


def test_c3_refuses_partial_source_limit(demo):
    result = inspect(demo, max_captures=1)
    assert result["capture_sample_truncated"] is True
    assert "INSPECTION_INCOMPLETE" in result["health_blockers"]
    assert result["real_council_source_lineage_verified"] is False


def test_c3_cli_requires_explicit_read_only_opt_in():
    with pytest.raises(SystemExit) as error:
        run(["--epoch-id", "f" * 64, "--as-of", "2026-10-27T14:00:00Z"])
    assert error.value.code == 2


def test_c3_cli_rejects_bad_epoch_before_connect(capsys):
    assert run(["--read-only-db", "--epoch-id", "bad", "--as-of",
                "2026-10-27T14:00:00Z"]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_c3_offline_report_json_has_no_certification(demo):
    report = inspect(demo)
    wire = json.loads(json.dumps(report))
    assert wire["council_input_family_count"] == 13
    assert wire["real_forward_admission"] is False
    assert wire["final_action"] == "HOLD"
