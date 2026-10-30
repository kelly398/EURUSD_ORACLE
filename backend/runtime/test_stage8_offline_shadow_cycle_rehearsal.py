
"""P8-17..20: frozen Stage 8 end-to-end mock-clock replay, never shadow GO.

The test fixture builder is the only writer. The production module may only
read pre-existing Stage 8 artifacts; even its shadow gate is never permission.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage8_point_in_time_market_state import (
    assess_stage8_market_state,
)
from backend.runtime.stage8_offline_council_sandbox import assess_stage8_council
from backend.runtime.stage8_explainable_research_memory import assess_stage8_explainable_memory
from backend.runtime.test_stage8_explainable_research_memory import (
    setup as earlier_setup, proposal as earlier_proposal,
    save, POLICY, CONTRACTS, MANIFEST, ASOF,
)
from backend.runtime.test_stage8_point_in_time_market_state import (
    change_payload, append, row as source_row,
)
from backend.runtime.stage8_offline_shadow_cycle_rehearsal import (
    EPOCH, POLICY_SHA256, VERSION, GATE_VERSION, WITNESS_VERSION,
    ShadowRehearsalError, _hash, _clock,
    assess_stage8_shadow_rehearsal, run,
)

ROOT = Path(__file__).resolve().parents[2]
BAR = "2026-10-30T10:00:00Z"
DECISION = "2026-10-30T10:00:20Z"
CASE_KEYS = {
    "case_id", "bar_close_at", "decision_at", "heartbeat_index", "heartbeat_root",
    "context_index", "context_root", "context_report", "seat_schedule",
    "council_report", "forecast_proposal", "prior_journal", "memory_report",
}


def _rel(root, path):
    if path is None:
        return None
    return Path(path).relative_to(root).as_posix()


def recalc_context(root):
    return assess_stage8_market_state(
        epoch_id=EPOCH, as_of=ASOF, decision_at=DECISION, bar_close_at=BAR,
        context_index=root / "context_index.json",
        evidence_root=root / "UNVERIFIED_MARKET_CONTEXT",
        heartbeat_index=root / "heartbeat_index.json",
        heartbeat_root=root / "SYNTHETIC_HEARTBEAT",
        policy_file=POLICY, contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )


def recalc_council(root):
    return assess_stage8_council(
        epoch_id=EPOCH, as_of=ASOF, policy_file=POLICY,
        context_report=root / "unverified_point_in_time_report.json",
        seat_schedule=root / "synthetic_only_seat_results.json",
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )


def recalc_memory(root, *, forecast=None):
    return assess_stage8_explainable_memory(
        epoch_id=EPOCH, as_of=ASOF, policy_file=POLICY,
        context_report=root / "unverified_point_in_time_report.json",
        seat_schedule=root / "synthetic_only_seat_results.json",
        council_report=root / "synthetic_council_report.json",
        forecast_proposal=forecast,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )


def rebuild(root, forecast=None):
    ctx = recalc_context(root)
    save(root / "unverified_point_in_time_report.json", ctx)
    # A changed causal context must bind a fresh local synthetic seat schedule;
    # it cannot reuse the previous digest or imply real Council execution.
    seat_path = root / "synthetic_only_seat_results.json"
    seat_doc = json.loads(seat_path.read_text("utf-8"))
    seat_doc["context_sha256"] = ctx["context"]["context_sha256"]
    seat_doc["decision_key"] = ctx["context"]["decision_key"]
    save(seat_path, seat_doc)
    council = recalc_council(root)
    save(root / "synthetic_council_report.json", council)
    memory = recalc_memory(root, forecast=forecast)
    save(root / "synthetic_memory_report.json", memory)
    return ctx, council, memory


def fixtures(tmp_path, *, forecast=False, full=True):
    ctx, context_doc, schedule, schedule_doc, council = earlier_setup(tmp_path, full=full)
    candidate = None
    if forecast:
        candidate, _ = earlier_proposal(tmp_path, council)
    memory = recalc_memory(tmp_path, forecast=candidate)
    mem_path = tmp_path / "synthetic_memory_report.json"
    save(mem_path, memory)
    case = {
        "case_id": "CASE_ONE", "bar_close_at": BAR, "decision_at": DECISION,
        "heartbeat_index": "heartbeat_index.json",
        "heartbeat_root": "SYNTHETIC_HEARTBEAT",
        "context_index": "context_index.json",
        "context_root": "UNVERIFIED_MARKET_CONTEXT",
        "context_report": _rel(tmp_path, ctx),
        "seat_schedule": _rel(tmp_path, schedule),
        "council_report": _rel(tmp_path, council),
        "forecast_proposal": _rel(tmp_path, candidate),
        "prior_journal": None, "memory_report": _rel(tmp_path, mem_path),
    }
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_M5_SHADOW_BATCH5", "mode": "SYNTHETIC_OFFLINE",
        "mock_clock_at": ASOF, "requested_scope": "OFFLINE_REPLAY",
        "shadow_gate": None, "cycles": [case],
    }
    path = tmp_path / "batch5_manifest.json"
    save(path, doc)
    return path, doc


def assess(path=None, root=None, *, policy=POLICY, asof=ASOF,
           contracts=CONTRACTS, manifest=MANIFEST):
    return assess_stage8_shadow_rehearsal(
        epoch_id=EPOCH, as_of=asof, policy_file=policy,
        fixture_manifest=path, evidence_root=root,
        contracts_file=contracts, manifest_file=manifest,
    )


def checked(tmp_path, *, forecast=False, full=True):
    path, doc = fixtures(tmp_path, forecast=forecast, full=full)
    return assess(path, tmp_path)


def change(path, doc, **kwargs):
    doc.update(kwargs)
    save(path, doc)


def case(result, index=0):
    return result["cycles"][index]


def gate():
    return {
        "version": GATE_VERSION,
        "operator_scope_claim": "LOCAL_UNATTESTED_CLAIM",
        "source_admission_claim": "LOCAL_UNATTESTED_CLAIM",
        "witness_service_claim": "LOCAL_UNATTESTED_CLAIM",
        "stop_switch_claim": "LOCAL_UNATTESTED_CLAIM",
    }


def _refresh_after_schedule(root, doc):
    save(root / "synthetic_only_seat_results.json", doc)
    c = recalc_council(root)
    save(root / "synthetic_council_report.json", c)
    m = recalc_memory(root)
    save(root / "synthetic_memory_report.json", m)


# P8-17 -- deterministic mock-clock historical research replay.


def test_p817_empty_no_fixture_is_no_dispatch_hold():
    r = assess()
    assert r["cycles"] == []
    assert r["final_action"] == "HOLD"
    assert r["full_frozen_offline_chains_reconstructed"] == 0
    assert r["candidate_shadow_cycles_started"] == 0


def test_p817_full_four_module_chain_reconstructs_without_dispatch(tmp_path):
    r = checked(tmp_path)
    c = case(r)
    assert c["state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD"
    assert c["completed_frozen_stages"] == 3
    assert r["full_frozen_offline_chains_reconstructed"] == 1
    assert c["submitted_seats"] == 31
    assert c["missing_seat_count"] == 0
    assert c["heartbeat_candidate"]
    assert r["registered_predictions"] == 0
    assert c["synthetic_proposed_direction"] == "BUY"
    assert r["final_action"] == "HOLD"


def test_p817_immutable_mock_clock_before_real_clock(tmp_path):
    r = checked(tmp_path)
    assert r["mock_clock_at"] == "2026-10-30T11:00:00+00:00"
    assert r["replay_mode"] == "OFFLINE_REPLAY_SYNTHETIC_INPUTS"
    assert r["as_of"] == "2026-10-30T11:00:00+00:00"


def test_p817_same_frozen_fixture_replay_byte_identical(tmp_path):
    path, doc = fixtures(tmp_path)
    r1, r2 = assess(path, tmp_path), assess(path, tmp_path)
    assert r1 == r2
    assert r1["report_sha256"] == _hash({k: v for k, v in r1.items() if k != "report_sha256"})


def test_p817_events_prove_local_hash_chain_not_external_receipt(tmp_path):
    c = case(checked(tmp_path))
    prev = "0" * 64
    assert [e["stage"] for e in c["evidence_events"]] == [
        "M5_HEARTBEAT", "POINT_IN_TIME_CONTEXT", "COUNCIL_REHEARSAL",
        "EXPLAINABLE_MEMORY", "WITNESS_HANDOFF", "FINAL_RESEARCH_CYCLE"]
    for n, ev in enumerate(c["evidence_events"], 1):
        assert ev["ordinal"] == n
        assert ev["previous_sha256"] == prev
        assert ev["event_sha256"] == _hash({k: v for k, v in ev.items() if k != "event_sha256"})
        assert ev["terminal_permission"] == "HOLD"
        prev = ev["event_sha256"]


def test_p817_complete_simulated_cycle_no_forecast_registration(tmp_path):
    c = case(checked(tmp_path))
    assert c["synthetic_prediction_id"] is None
    assert c["forecast_registered"] is False
    assert c["witness_handoff"]["status"] == "NOT_PREPARED"


def test_p817_prior_frozen_snapshot_bytes_untouched(tmp_path):
    path, doc = fixtures(tmp_path)
    files_ = [tmp_path / key for key in (
        "heartbeat_index.json", "context_index.json", "unverified_point_in_time_report.json",
        "synthetic_only_seat_results.json", "synthetic_council_report.json",
        "synthetic_memory_report.json", "batch5_manifest.json",
    )]
    previous = [sha256(p.read_bytes()).hexdigest() for p in files_]
    _ = assess(path, tmp_path)
    assert previous == [sha256(p.read_bytes()).hexdigest() for p in files_]


def test_p817_simulation_uses_only_historical_epoch(tmp_path):
    r = checked(tmp_path)
    assert r["historical_epoch_id"] == EPOCH
    assert not r["historical_epoch_rebound"]
    assert not r["future_epoch_created"]
    assert r["historical_policy_sha256"] == POLICY_SHA256


def test_p817_full_fixture_later_mock_clock_breaks_report_replay(tmp_path):
    path, doc = fixtures(tmp_path)
    change(path, doc, mock_clock_at="2026-10-30T11:00:30Z")
    r = assess(path, tmp_path, asof="2026-10-30T11:01:00Z")
    assert case(r)["state"].startswith("HELD_")
    assert "POINT_IN_TIME_CONTEXT_REPLAY_MISMATCH" in case(r)["reason_codes"]


def test_p817_manifest_reordering_cannot_reorder_case_evidence(tmp_path):
    path, doc = fixtures(tmp_path)
    second = deepcopy(doc["cycles"][0])
    second["case_id"] = "SECOND_CASE"
    doc["cycles"].append(second)
    save(path, doc)
    first = assess(path, tmp_path)
    doc["cycles"].reverse()
    save(path, doc)
    later = assess(path, tmp_path)
    assert first["cycles"] == later["cycles"]


# P8-18 -- witness interface, no fabricated third-party receipts.


def test_p818_prepared_witness_request_is_unsent_and_not_signature(tmp_path):
    r = checked(tmp_path, forecast=True)
    c = case(r)
    assert c["state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD"
    assert c["witness_handoff"]["status"] == "UNSENT_REQUEST_SHAPE_ONLY"
    assert len(c["witness_handoff"]["request_sha256"]) == 64
    assert c["witness_handoff"]["receipt_id"] is None
    assert c["witness_handoff"]["witness_signature"] is None
    assert c["witness_handoff"]["independent_receipt_verified"] is False
    assert c["forecast_registered"] is False


def test_p818_witness_metadata_never_claims_receipt_service_connected(tmp_path):
    r = checked(tmp_path, forecast=True)
    assert r["witness_interface"]["request_only"] is True
    assert r["witness_interface"]["third_party_receipt_id"] is None
    assert r["witness_interface"]["independent_timestamp_witness_connected"] is False
    assert r["witness_interface"]["independent_predecision_receipts_verified"] == 0


def test_p818_forecast_id_distinct_from_real_registration(tmp_path):
    r = checked(tmp_path, forecast=True)
    assert len(case(r)["synthetic_prediction_id"]) == 64
    assert r["registered_predictions"] == 0
    assert r["real_council_seats_dispatched"] == 0


def test_p818_forecast_request_deterministic(tmp_path):
    path, doc = fixtures(tmp_path, forecast=True)
    first = case(assess(path, tmp_path))["witness_handoff"]
    second = case(assess(path, tmp_path))["witness_handoff"]
    assert first == second


def test_p818_local_claim_no_independent_custody(tmp_path):
    r = checked(tmp_path, forecast=True)
    assert r["independent_external_gates_verified"] == 0
    assert r["independently_attested_source_families"] == 0
    assert r["shadow_gate"]["shadow_scope_authorized"] is False


def test_p818_memory_snapshot_edit_refused_at_replay(tmp_path):
    path, doc = fixtures(tmp_path, forecast=True)
    memory = tmp_path / "synthetic_memory_report.json"
    data = json.loads(memory.read_text("utf-8"))
    data["forecast_envelope"]["registration_status"] = "REGISTERED"
    data["report_sha256"] = _hash({k: v for k, v in data.items() if k != "report_sha256"})
    save(memory, data)
    r = assess(path, tmp_path)
    assert "EXPLAINABLE_MEMORY_REPLAY_MISMATCH" in case(r)["reason_codes"]
    assert case(r)["witness_handoff"]["receipt_id"] is None


def test_p818_changed_forecast_after_freeze_fails_local_replay(tmp_path):
    path, doc = fixtures(tmp_path, forecast=True)
    proposal = tmp_path / "synthetic_unregistered_forecast.json"
    data = json.loads(proposal.read_text("utf-8"))
    data["probability_up"] = "0.850"
    save(proposal, data)
    c = case(assess(path, tmp_path))
    assert c["state"].startswith("HELD_")
    assert c["witness_handoff"]["request_sha256"] is None


# P8-19 -- separate RESEARCH_SHADOW_ONLY fail-closed gate.


def test_p819_shadow_scope_without_gate_never_replays(tmp_path):
    path, doc = fixtures(tmp_path)
    change(path, doc, requested_scope="RESEARCH_SHADOW_ONLY")
    r = assess(path, tmp_path)
    assert r["requested_scope"] == "RESEARCH_SHADOW_ONLY"
    assert case(r)["state"].startswith("HELD_")
    assert case(r)["completed_frozen_stages"] == 0
    assert r["candidate_shadow_cycles_started"] == 0


def test_p819_four_positive_local_claims_still_cannot_grant_shadow(tmp_path):
    path, doc = fixtures(tmp_path)
    change(path, doc, requested_scope="RESEARCH_SHADOW_ONLY", shadow_gate=gate())
    r = assess(path, tmp_path)
    assert r["shadow_gate"]["local_claims_supplied"]
    assert not r["shadow_gate"]["local_claims_independently_verified"]
    assert not r["shadow_gate"]["shadow_scope_authorized"]
    assert not r["shadow_gate"]["shadow_runner_started"]
    assert r["candidate_shadow_cycles_started"] == 0
    assert r["final_action"] == "HOLD"


@pytest.mark.parametrize("name", (
    "operator_scope_claim", "source_admission_claim",
    "witness_service_claim", "stop_switch_claim",
))
def test_p819_external_permission_strings_cannot_be_self_asserted(tmp_path, name):
    path, doc = fixtures(tmp_path)
    item = gate()
    item[name] = "INDEPENDENTLY_APPROVED"
    change(path, doc, shadow_gate=item)
    with pytest.raises(ShadowRehearsalError, match="external authorization"):
        assess(path, tmp_path)


def test_p819_gate_contract_extra_approval_field_rejected(tmp_path):
    path, doc = fixtures(tmp_path)
    item = gate()
    item["live_orders_enabled"] = True
    change(path, doc, shadow_gate=item)
    with pytest.raises(ShadowRehearsalError, match="Strict schema"):
        assess(path, tmp_path)


def test_p819_live_execution_scope_refused(tmp_path):
    path, doc = fixtures(tmp_path)
    change(path, doc, requested_scope="LIVE_EXECUTION")
    with pytest.raises(ShadowRehearsalError, match="scope mismatch"):
        assess(path, tmp_path)


def test_p819_no_broker_or_model_or_file_side_effects(tmp_path):
    r = checked(tmp_path, forecast=True)
    for flag in (
        "files_written", "network_accessed", "broker_connected", "collector_started",
        "council_invoked", "model_loaded", "training_authorized", "settlement_invoked",
        "paper_orders_authorized", "live_orders_authorized", "operator_approval_granted",
        "historical_epoch_rebound", "future_epoch_created",
    ):
        assert r[flag] is False
    assert r["read_only"] is True and r["final_action"] == "HOLD"


# P8-20 -- failure rehearsals and strict local evidence controls.


def test_p820_delayed_m5_arrival_expired_and_held(tmp_path):
    path, doc = fixtures(tmp_path)
    hb_index = tmp_path / "heartbeat_index.json"
    hb = json.loads(hb_index.read_text("utf-8"))
    hb["envelopes"][0]["received_at"] = "2026-10-30T10:03:00Z"
    save(hb_index, hb)
    c = case(assess(path, tmp_path))
    assert "STALE_M5_DELIVERY" in c["reason_codes"]
    assert "M5_BAR_NOT_LOCALLY_ELIGIBLE" in c["reason_codes"]
    assert c["completed_frozen_stages"] == 0


def test_p820_partial_m5_candle_never_progresses(tmp_path):
    path, doc = fixtures(tmp_path)
    candle = tmp_path / "SYNTHETIC_HEARTBEAT/candles/m5.json"
    bar = json.loads(candle.read_text("utf-8"))
    bar["is_final"] = False
    save(candle, bar)
    hbpath = tmp_path / "heartbeat_index.json"
    hb = json.loads(hbpath.read_text("utf-8"))
    hb["envelopes"][0]["candle_sha256"] = sha256(candle.read_bytes()).hexdigest()
    save(hbpath, hb)
    assert "PARTIAL_M5_CANDLE" in case(assess(path, tmp_path))["reason_codes"]


def test_p820_holiday_session_blocked(tmp_path):
    path, doc = fixtures(tmp_path)
    hbpath = tmp_path / "heartbeat_index.json"
    hb = json.loads(hbpath.read_text("utf-8"))
    hb["envelopes"][0]["session_context"]["holiday_state"] = "HOLIDAY"
    save(hbpath, hb)
    assert "HOLIDAY_CALENDAR_NOT_CLEAR" in case(assess(path, tmp_path))["reason_codes"]


def test_p820_clock_uncertainty_blocks_cycle(tmp_path):
    path, doc = fixtures(tmp_path)
    hbpath = tmp_path / "heartbeat_index.json"
    hb = json.loads(hbpath.read_text("utf-8"))
    hb["envelopes"][0]["clock_uncertainty_ms"] = "750.000"
    save(hbpath, hb)
    assert "CLOCK_UNCERTAINTY_ABOVE_DESIGN_CEILING" in case(assess(path, tmp_path))["reason_codes"]


def test_p820_stale_quotes_quarantine_ancestor_and_hold(tmp_path):
    path, doc = fixtures(tmp_path)
    ci = tmp_path / "context_index.json"
    ctx = json.loads(ci.read_text("utf-8"))
    tick = next(i for i, e in enumerate(ctx["records"]) if e["label"] == "ticks")
    ctx["records"][tick]["observed_at"] = "2026-10-30T09:51:35Z"
    ctx["records"][tick]["published_at"] = "2026-10-30T09:51:35Z"
    ctx["records"][tick]["first_available_at"] = "2026-10-30T09:51:36Z"
    ctx["records"][tick]["received_at"] = "2026-10-30T09:51:37Z"
    ctx["records"][tick]["event_at"] = "2026-10-30T09:51:34Z"
    change_payload(tmp_path / "UNVERIFIED_MARKET_CONTEXT", ci, ctx, tick,
                   tick_at="2026-10-30T09:51:34Z")
    rebuild(tmp_path)
    c = case(assess(path, tmp_path))
    assert c["context_quarantined"] is True
    assert "STALE_TICK_INPUT" in c["reason_codes"]
    assert c["state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD"
    assert c["forecast_registered"] is False


def test_p820_late_macro_revision_withheld_without_lookahead(tmp_path):
    path, doc = fixtures(tmp_path)
    before = recalc_context(tmp_path)["context"]["context_sha256"]
    ci = tmp_path / "context_index.json"
    ctx = json.loads(ci.read_text("utf-8"))
    new = source_row("fed_data", rid="late_fed_revision", available=40, kind="REVISION")
    new["supersedes_id"] = "fed_data_01"
    append(tmp_path / "UNVERIFIED_MARKET_CONTEXT", ci, ctx, new)
    now = recalc_context(tmp_path)
    assert now["context"]["context_sha256"] == before
    rebuild(tmp_path)
    c = case(assess(path, tmp_path))
    assert c["postdecision_source_records_withheld"] == 1
    assert "POSTDECISION_RECORDS_WITHHELD_FROM_CAUSAL_CONTEXT" in c["audit_notes"]
    assert c["state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD"


def test_p820_missing_primary_seat_produces_no_implicit_abstain(tmp_path):
    path, doc = fixtures(tmp_path)
    sp = tmp_path / "synthetic_only_seat_results.json"
    council = json.loads(sp.read_text("utf-8"))
    council["seat_results"].pop(0)
    _refresh_after_schedule(tmp_path, council)
    c = case(assess(path, tmp_path))
    assert c["missing_seat_count"] == 1
    assert "MISSING_SEATS_ARE_NOT_IMPLICIT_ABSTENTIONS" in c["reason_codes"]
    assert c["synthetic_proposed_direction"] == "HOLD"
    assert c["state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD"


def test_p820_risk_officer_veto_blocks_all_agreement(tmp_path):
    path, doc = fixtures(tmp_path)
    sp = tmp_path / "synthetic_only_seat_results.json"
    schedule = json.loads(sp.read_text("utf-8"))
    schedule["seat_results"][29]["payload"]["veto"] = True
    _refresh_after_schedule(tmp_path, schedule)
    c = case(assess(path, tmp_path))
    assert c["risk_veto"] is True
    assert c["synthetic_proposed_direction"] == "HOLD"
    assert "RISK_OFFICER_VETO_FORCES_HOLD" in c["reason_codes"]
    assert c["state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD"


def test_p820_mismatched_fixture_epoch_refused(tmp_path):
    path, doc = fixtures(tmp_path)
    change(path, doc, historical_epoch_id="f" * 64)
    with pytest.raises(ShadowRehearsalError, match="epoch"):
        assess(path, tmp_path)


def test_p820_corrupt_heartbeat_epoch_held_no_later_replay(tmp_path):
    path, doc = fixtures(tmp_path)
    hb = tmp_path / "heartbeat_index.json"
    data = json.loads(hb.read_text("utf-8"))
    data["historical_epoch_id"] = "f" * 64
    save(hb, data)
    c = case(assess(path, tmp_path))
    assert c["completed_frozen_stages"] == 0
    assert "M5_HEARTBEAT_REPLAY_REJECTED" in c["reason_codes"]


def test_p820_corrupt_context_epoch_held(tmp_path):
    path, doc = fixtures(tmp_path)
    ci = tmp_path / "context_index.json"
    data = json.loads(ci.read_text("utf-8"))
    data["historical_epoch_id"] = "f" * 64
    save(ci, data)
    c = case(assess(path, tmp_path))
    assert "POINT_IN_TIME_CONTEXT_REPLAY_MISMATCH" in c["reason_codes"]


def test_p820_context_snapshot_rehashed_cannot_forge_prior_state(tmp_path):
    path, doc = fixtures(tmp_path)
    ci = tmp_path / "unverified_point_in_time_report.json"
    report = json.loads(ci.read_text("utf-8"))
    report["context"]["context_fields"][0]["selected_vintage_id"] = "changed"
    report["report_sha256"] = _hash({k: v for k, v in report.items() if k != "report_sha256"})
    save(ci, report)
    c = case(assess(path, tmp_path))
    assert "POINT_IN_TIME_CONTEXT_REPLAY_MISMATCH" in c["reason_codes"]


def test_p820_rehashed_forged_council_vote_rejected(tmp_path):
    path, doc = fixtures(tmp_path)
    cp = tmp_path / "synthetic_council_report.json"
    report = json.loads(cp.read_text("utf-8"))
    report["rehearsal"]["declared_risk_veto"] = True
    report["report_sha256"] = _hash({k: v for k, v in report.items() if k != "report_sha256"})
    save(cp, report)
    c = case(assess(path, tmp_path))
    assert "DECLARATIVE_COUNCIL_REPLAY_MISMATCH" in c["reason_codes"]


def test_p820_unregistered_forecast_never_promoted(tmp_path):
    path, doc = fixtures(tmp_path, forecast=True)
    c = case(assess(path, tmp_path))
    assert c["synthetic_prediction_id"] is not None
    assert not c["witness_handoff"]["independent_receipt_verified"]
    assert not c["forecast_registered"]
    assert c["state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD"


def test_p820_idempotent_duplicate_decision_suppressed(tmp_path):
    path, doc = fixtures(tmp_path)
    duplicate = deepcopy(doc["cycles"][0])
    duplicate["case_id"] = "CASE_TWO"
    doc["cycles"].append(duplicate)
    save(path, doc)
    r = assess(path, tmp_path)
    assert r["unique_decision_keys"] == 1
    assert r["duplicate_decisions_suppressed"] == 1
    assert r["full_frozen_offline_chains_reconstructed"] == 1
    assert case(r, 1)["state"] == "HELD_DUPLICATE"


def test_p820_conflicting_duplicate_decision_quarantines_both(tmp_path):
    path, doc = fixtures(tmp_path)
    alternate = deepcopy(doc["cycles"][0])
    alternate["case_id"] = "CASE_TWO"
    alternate["decision_at"] = "2026-10-30T10:00:21Z"
    doc["cycles"].append(alternate)
    save(path, doc)
    r = assess(path, tmp_path)
    assert r["unique_decision_keys"] == 1
    assert r["conflicting_decision_keys_quarantined"] == 1
    assert r["full_frozen_offline_chains_reconstructed"] == 0
    assert all(x["state"] == "HELD_CONFLICT" for x in r["cycles"])


def test_p820_conflicting_local_case_ids_rejected(tmp_path):
    path, doc = fixtures(tmp_path)
    doc["cycles"].append(deepcopy(doc["cycles"][0]))
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="Duplicate synthetic case ID"):
        assess(path, tmp_path)


# Parsers, explicit chronology, bounded inputs and privacy.


@pytest.mark.parametrize("bad", [
    "2026-10-30T11:00:00", "2026-10-30T14:00:00+03:00",
    "2026-10-30T11:00:60Z", "2026-11-31T11:00:00Z", "nonsense",
])
def test_p820_mock_time_requires_explicit_utc(bad):
    with pytest.raises(ShadowRehearsalError):
        _clock(bad)


@pytest.mark.parametrize("scope", ["LIVE_TRADING", "PAPER_ORDERS", "SHADOW_ENABLED", "REAL_COUNCIL", ""])
def test_p820_forbidden_requested_scopes_rejected(tmp_path, scope):
    path, doc = fixtures(tmp_path)
    change(path, doc, requested_scope=scope)
    with pytest.raises(ShadowRehearsalError, match="scope mismatch"):
        assess(path, tmp_path)


@pytest.mark.parametrize("unsafe", [
    "../secret.json", "C:/private.json", "/tmp/private.json",
    "a//b.json", "a/../b.json", "folder\\file.json", "./file.json",
    "account_number_1.json", "abc@example.com",
])
def test_p820_unsafe_relative_evidence_paths_refused(tmp_path, unsafe):
    path, doc = fixtures(tmp_path)
    doc["cycles"][0]["context_index"] = unsafe
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="Unsafe"):
        assess(path, tmp_path)


def test_p820_unexpected_manifest_authority_field_refused(tmp_path):
    path, doc = fixtures(tmp_path)
    change(path, doc, shadow_approved=True)
    with pytest.raises(ShadowRehearsalError, match="Strict schema"):
        assess(path, tmp_path)


def test_p820_duplicate_json_keys_refused(tmp_path):
    path, doc = fixtures(tmp_path)
    path.write_text('{"mode":"SYNTHETIC_OFFLINE","mode":"LIVE"}', encoding="utf-8")
    with pytest.raises(ShadowRehearsalError, match="malformed"):
        assess(path, tmp_path)


def test_p820_invalid_m5_bar_alignment_refused(tmp_path):
    path, doc = fixtures(tmp_path)
    doc["cycles"][0]["bar_close_at"] = "2026-10-30T10:01:00Z"
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="chronology invalid"):
        assess(path, tmp_path)


def test_p820_future_mock_clock_refused(tmp_path):
    path, doc = fixtures(tmp_path)
    change(path, doc, mock_clock_at="2030-10-30T11:00:00Z")
    with pytest.raises(ShadowRehearsalError, match="Mock clock"):
        assess(path, tmp_path)


def test_p820_future_asof_refused():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(ShadowRehearsalError, match="Future review time"):
        assess(asof=future)


def test_p820_root_without_manifest_refused(tmp_path):
    with pytest.raises(ShadowRehearsalError, match="paired"):
        assess(root=tmp_path)


def test_p820_manifest_without_root_refused(tmp_path):
    path, doc = fixtures(tmp_path)
    with pytest.raises(ShadowRehearsalError, match="paired"):
        assess(path=path)


def test_p820_policy_pin_must_match_exact_bytes(tmp_path):
    altered = tmp_path / "changed_policy.json"
    altered.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(ShadowRehearsalError, match="policy SHA-256"):
        assess(policy=altered)


def test_p820_wrong_historical_epoch_rebinding_refused():
    with pytest.raises(ShadowRehearsalError, match="rebinding refused"):
        assess_stage8_shadow_rehearsal(epoch_id="f" * 64, as_of=ASOF, policy_file=POLICY)


def test_p820_manifest_bound_to_synthetic_only(tmp_path):
    path, doc = fixtures(tmp_path)
    change(path, doc, mode="ARCHIVED_UNVERIFIED")
    with pytest.raises(ShadowRehearsalError, match="scope mismatch"):
        assess(path, tmp_path)


def test_p820_mock_clock_cannot_precede_decision(tmp_path):
    path, doc = fixtures(tmp_path)
    change(path, doc, mock_clock_at="2026-10-30T09:50:00Z")
    with pytest.raises(ShadowRehearsalError, match="chronology invalid"):
        assess(path, tmp_path)


def test_p820_context_index_root_report_must_all_be_paired(tmp_path):
    path, doc = fixtures(tmp_path)
    doc["cycles"][0]["context_report"] = None
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="must be paired"):
        assess(path, tmp_path)


def test_p820_council_requires_context_snapshot(tmp_path):
    path, doc = fixtures(tmp_path)
    for name in ("context_index", "context_root", "context_report"):
        doc["cycles"][0][name] = None
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="Council requires"):
        assess(path, tmp_path)


def test_p820_no_context_stops_at_heartbeat(tmp_path):
    path, doc = fixtures(tmp_path)
    for name in ("context_index", "context_root", "context_report", "seat_schedule",
                 "council_report", "memory_report"):
        doc["cycles"][0][name] = None
    save(path, doc)
    c = case(assess(path, tmp_path))
    assert c["heartbeat_candidate"] is True
    assert "POINT_IN_TIME_CONTEXT_NOT_SUPPLIED" in c["reason_codes"]


def test_p820_missing_memory_snapshot_is_distinct_failure(tmp_path):
    path, doc = fixtures(tmp_path)
    doc["cycles"][0]["memory_report"] = None
    save(path, doc)
    c = case(assess(path, tmp_path))
    assert c["completed_frozen_stages"] == 2
    assert "EXPLAINABLE_MEMORY_SNAPSHOT_NOT_SUPPLIED" in c["reason_codes"]


def test_p820_missing_council_stage_is_distinct_failure(tmp_path):
    path, doc = fixtures(tmp_path)
    doc["cycles"][0]["council_report"] = None
    doc["cycles"][0]["seat_schedule"] = None
    doc["cycles"][0]["memory_report"] = None
    save(path, doc)
    c = case(assess(path, tmp_path))
    assert c["completed_frozen_stages"] == 1
    assert "DECLARATIVE_COUNCIL_SCHEDULE_NOT_SUPPLIED" in c["reason_codes"]


def test_p820_reused_case_ids_do_not_create_multiple_forecasts(tmp_path):
    path, doc = fixtures(tmp_path, forecast=True)
    d = deepcopy(doc["cycles"][0])
    d["case_id"] = "ANOTHER_DELIVERY"
    doc["cycles"].append(d)
    save(path, doc)
    r = assess(path, tmp_path)
    assert r["duplicate_decisions_suppressed"] == 1
    assert r["registered_predictions"] == 0
    assert r["witness_interface"]["independent_predecision_receipts_verified"] == 0


def test_p820_report_contains_no_raw_ohlc_or_fixture_paths(tmp_path):
    r = checked(tmp_path)
    body = json.dumps(r)
    assert '"1.0840"' not in body
    assert "candles/m5.json" not in body
    assert "UNVERIFIED_MARKET_CONTEXT" not in body
    assert "synthetic_only_seat_results.json" not in body


def test_p820_readonly_cli_no_fixture_hold(capsys):
    code = run([
        "--read-only-shadow-rehearsal", "--synthetic-only",
        "--epoch-id", EPOCH, "--as-of", ASOF,
        "--policy-file", str(POLICY), "--contracts-file", str(CONTRACTS),
        "--manifest-file", str(MANIFEST),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "FINAL=HOLD" in out
    assert "Shadow cycles: 0" in out
    assert "Synthetic cases: 0" in out


def test_p820_cli_json_complete_fixture_always_hold(tmp_path, capsys):
    path, doc = fixtures(tmp_path, forecast=True)
    code = run([
        "--read-only-shadow-rehearsal", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", ASOF, "--fixture-manifest", str(path),
        "--evidence-root", str(tmp_path), "--policy-file", str(POLICY),
        "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST), "--json",
    ])
    assert code == 0
    r = json.loads(capsys.readouterr().out)
    assert r["full_frozen_offline_chains_reconstructed"] == 1
    assert r["registered_predictions"] == 0
    assert r["final_action"] == "HOLD"


def test_p820_cli_unsafe_future_time_exits_blocked(capsys):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    code = run([
        "--read-only-shadow-rehearsal", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", future, "--policy-file", str(POLICY),
    ])
    assert code == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p820_cli_requires_explicit_readonly_flags():
    with pytest.raises(SystemExit) as error:
        run(["--epoch-id", EPOCH, "--as-of", ASOF])
    assert error.value.code == 2


def test_p820_manifest_symlink_refused_if_supported(tmp_path):
    path, doc = fixtures(tmp_path)
    alias = tmp_path / "alias_manifest.json"
    try:
        alias.symlink_to(path)
    except (NotImplementedError, OSError):
        pytest.skip("Symlink not supported on Windows host")
    with pytest.raises(ShadowRehearsalError, match="unsafe|Unsafe|malformed"):
        assess(alias, tmp_path)


def test_p820_local_original_policy_bytes_untouched(tmp_path):
    old = sha256(POLICY.read_bytes()).hexdigest()
    _ = checked(tmp_path)
    assert sha256(POLICY.read_bytes()).hexdigest() == old == POLICY_SHA256


def test_p820_duplicate_forecast_conflict_withdraws_first_handoff(tmp_path):
    path, doc = fixtures(tmp_path, forecast=True)
    second = deepcopy(doc["cycles"][0])
    second["case_id"] = "SECOND_DELIVERY"
    second["decision_at"] = "2026-10-30T10:00:21Z"
    doc["cycles"].append(second)
    save(path, doc)
    r = assess(path, tmp_path)
    assert r["conflicting_decision_keys_quarantined"] == 1
    assert all(c["state"] == "HELD_CONFLICT" for c in r["cycles"])
    for c in r["cycles"]:
        assert c["synthetic_prediction_id"] is None
        assert c["witness_handoff"]["request_sha256"] is None
    assert r["registered_predictions"] == 0


def test_p820_shadow_claims_with_forecast_do_not_prepare_request(tmp_path):
    path, doc = fixtures(tmp_path, forecast=True)
    change(path, doc, requested_scope="RESEARCH_SHADOW_ONLY", shadow_gate=gate())
    r = assess(path, tmp_path)
    assert r["full_frozen_offline_chains_reconstructed"] == 0
    assert case(r)["witness_handoff"]["request_sha256"] is None
    assert r["candidate_shadow_cycles_started"] == 0


def test_p820_forecast_proposal_without_frozen_memory_refused(tmp_path):
    path, doc = fixtures(tmp_path, forecast=True)
    doc["cycles"][0]["memory_report"] = None
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="Forecast/journal requires"):
        assess(path, tmp_path)


def test_p820_wrong_case_schema_rejected(tmp_path):
    path, doc = fixtures(tmp_path)
    doc["cycles"][0]["trade_action"] = "BUY"
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="Strict schema"):
        assess(path, tmp_path)


def test_p820_case_decision_before_bar_refused(tmp_path):
    path, doc = fixtures(tmp_path)
    doc["cycles"][0]["decision_at"] = "2026-10-30T09:59:59Z"
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="chronology invalid"):
        assess(path, tmp_path)


def test_p820_at_most_eight_cases_allowed(tmp_path):
    path, doc = fixtures(tmp_path)
    for n in range(2, 11):
        other = deepcopy(doc["cycles"][0])
        other["case_id"] = "CASE_" + str(n)
        doc["cycles"].append(other)
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="Bounded replay"):
        assess(path, tmp_path)


def test_p820_manifest_oversize_rejected(tmp_path):
    path, doc = fixtures(tmp_path)
    path.write_bytes(b"X" * 65537)
    with pytest.raises(ShadowRehearsalError, match="oversized"):
        assess(path, tmp_path)


def test_p820_nonexistent_local_snapshot_refused(tmp_path):
    path, doc = fixtures(tmp_path)
    doc["cycles"][0]["council_report"] = "nonexistent/frozen.json"
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="Fixture file"):
        assess(path, tmp_path)


def test_p820_no_initial_fixture_is_report_sha_deterministic():
    a, b = assess(), assess()
    assert a == b
    assert a["report_sha256"] == _hash({k: v for k, v in a.items() if k != "report_sha256"})


def test_p818_witness_request_hash_not_prediction_registration_digest(tmp_path):
    c = case(checked(tmp_path, forecast=True))
    assert c["witness_handoff"]["request_sha256"] != c["synthetic_prediction_id"]
    assert c["witness_handoff"]["interface_version"] == WITNESS_VERSION
    assert c["witness_handoff"]["submitted_to_service"] is False


def test_p820_research_replay_reports_local_reconstruction_not_shadow_readiness(tmp_path):
    r = checked(tmp_path, forecast=True)
    assert r["full_frozen_offline_chains_reconstructed"] == 1
    assert r["research_operational_readiness"] == "BLOCKED_EXTERNAL_EVIDENCE"
    assert "RESEARCH_SHADOW_SCOPE_NOT_SEPARATELY_AUTHORIZED" in r["blockers"]


def test_p820_symlink_evidence_root_rejected_if_supported(tmp_path):
    path, doc = fixtures(tmp_path)
    alias = tmp_path / "linked_evidence"
    try:
        alias.symlink_to(tmp_path / "UNVERIFIED_MARKET_CONTEXT", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink unavailable on this Windows host")
    doc["cycles"][0]["context_root"] = "linked_evidence"
    save(path, doc)
    with pytest.raises(ShadowRehearsalError, match="Unsafe"):
        assess(path, tmp_path)
