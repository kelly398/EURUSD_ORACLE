
"""P8-09..12 offline 31-seat contract, declared budgets and fail-closed governance.

Uses existing Stage 8 Batch 2 test fixture helpers for REAL Batch 2 synthetic
context generation. Nothing here launches an agent, model, network or broker.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage6_council_context_readiness import inspect_static_council
from backend.runtime.test_stage8_point_in_time_market_state import (
    setup as setup_context, row as context_row, assess as context_assess,
)
from backend.runtime.stage8_offline_council_sandbox import (
    EPOCH, POLICY_SHA256, FAMILIES, DERIVED_INPUTS, PRIMARY_BUDGET_MS,
    GOVERNANCE_BUDGET_MS, MAX_DECLARED_MEMORY_BYTES, MIN_DIRECTIONAL,
    CouncilSandboxError, _digest, _utc, assess_stage8_council, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
CONTRACTS = ROOT / "backend/agents/contracts.py"
MANIFEST = ROOT / "backend/agents/council_manifest.py"
ASOF = "2026-10-30T11:00:00Z"
MOCK_SHA = sha256(b"SYNTHETIC_MODEL_MOCK_NOT_REAL_MODEL").hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")


def manifest():
    return inspect_static_council(contracts_file=CONTRACTS, manifest_file=MANIFEST)


def all_context_rows():
    parents = {
        "market_state": ("candles_01", "ticks_01"),
        "calendar_coverage": ("economic_calendar_01",),
        "risk_state": ("risk_limits_01",),
        "market_structure": ("market_state_01", "candles_01"),
        "feature_vector": ("candles_01", "market_state_01", "ticks_01"),
        "tick_features": ("ticks_01",),
        "liquidity_state": ("market_state_01", "ticks_01"),
        "session_state": ("market_state_01", "candles_01"),
        "rates_data": ("ecb_data_01", "fed_data_01"),
    }
    return [
        context_row(label, parent_ids=parents.get(label, ()),
                    available=13 if label in parents else -70 if label == "economic_calendar" else 5)
        for label in tuple(FAMILIES) + tuple(DERIVED_INPUTS)
    ]


def context_fixture(tmp_path, full=True):
    rows = all_context_rows() if full else [context_row("candles")]
    cr, ci, doc, hr, hi, hb = setup_context(tmp_path, rows)
    report = context_assess(ci, cr, hb_index=hi, hb_root=hr)
    assert report["final_action"] == "HOLD"
    file = tmp_path / "unverified_point_in_time_report.json"
    save(file, report)
    return file, report


def seat_envelope(seat, *, direction="BUY"):
    number = seat["seat"]
    if number <= 28:
        cited = seat["required_source_families"] + seat["derived_labels"]
        payload = {
            "direction": direction, "confidence": "0.700",
            "reason_code": "SYNTHETIC_REASON", "cited_labels": cited[:1],
        }
        start = 0
    elif number == 29:
        start, payload = 50, {"challenge": "PASS", "reason_code": "SYNTHETIC_CHALLENGE"}
    elif number == 30:
        start, payload = 100, {"veto": False, "reason_code": "SYNTHETIC_RISK_CHECK"}
    else:
        start, payload = 150, {
            "direction": direction, "confidence": "0.700", "reason_code": "SYNTHETIC_STRATEGY",
        }
    return {
        "seat_number": number, "seat_id": seat["agent_id"], "stage": seat["stage"],
        "outcome": "RESULT", "started_ms": start, "elapsed_ms": 40,
        "peak_memory_bytes": 2048, "payload": payload, "exception_code": None,
    }


def schedule_fixture(tmp_path, report, *, mock=MOCK_SHA):
    schedule = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "UNTRUSTED_SYNTHETIC_COUNCIL_31", "as_of": ASOF,
        "mode": "SYNTHETIC_OFFLINE", "decision_key": report["context"]["decision_key"],
        "context_sha256": report["context"]["context_sha256"],
        "synthetic_model_mock_sha256": mock,
        "seat_results": [seat_envelope(seat) for seat in manifest()["seats"]],
    }
    path = tmp_path / "synthetic_only_seat_results.json"
    save(path, schedule)
    return path, schedule


def files(tmp_path, *, full=True, mock=MOCK_SHA):
    ctx_path, report = context_fixture(tmp_path, full=full)
    sched_path, doc = schedule_fixture(tmp_path, report, mock=mock)
    return ctx_path, report, sched_path, doc


def assess(ctx=None, schedule=None, *, policy=POLICY, asof=ASOF,
           manifest_file=MANIFEST, contracts_file=CONTRACTS):
    return assess_stage8_council(
        epoch_id=EPOCH, as_of=asof, policy_file=policy,
        context_report=ctx, seat_schedule=schedule,
        contracts_file=contracts_file, manifest_file=manifest_file,
    )


def change(path, doc, num, **values):
    doc["seat_results"][num - 1].update(values)
    save(path, doc)


def seat(report, number):
    return report["rehearsal"]["seat_records"][number - 1]


# P8-09 canonical seat contract, 31 deterministic roles, context binding.

def test_p809_no_inputs_has_31_not_submitted_not_abstained():
    result = assess()
    rehearsal = result["rehearsal"]
    assert len(rehearsal["seat_records"]) == 31
    assert rehearsal["submitted_seat_count"] == 0
    assert len(rehearsal["missing_seat_ids"]) == 31
    assert rehearsal["explicit_primary_abstention_count"] == 0
    assert rehearsal["synthetic_only_proposed_direction"] == "HOLD"
    assert result["final_action"] == "HOLD"


def test_p809_static_manifest_28_2_1_order():
    result = assess()
    rows = result["rehearsal"]["seat_records"]
    assert [r["seat_number"] for r in rows] == list(range(1, 32))
    assert [r["stage"] for r in rows] == ["PRIMARY"] * 28 + ["GOVERNANCE"] * 2 + ["SYNTHESIS"]
    assert [r["seat_id"] for r in rows[-3:]] == ["devils_advocate", "risk_officer", "chief_strategist"]


def test_p809_real_point_in_time_context_has_all_13_plus_6(tmp_path):
    ctx, report = context_fixture(tmp_path)
    assert report["context"]["local_candidate_family_count"] == 13
    assert len(report["context"]["context_fields"]) == 19
    result = assess(ctx)
    assert result["context"]["status"] == "LOCAL_CONTEXT_REPORT_UNATTESTED"
    assert result["rehearsal"]["submitted_seat_count"] == 0


def test_p809_all_31_synthetic_mock_results_never_dispatch(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    result = assess(ctx, path)
    rehearsal = result["rehearsal"]
    assert rehearsal["submitted_seat_count"] == 31
    assert rehearsal["locally_consistent_synthetic_governance"]
    assert rehearsal["synthetic_only_proposed_direction"] == "BUY"
    assert result["cycle_result"] == "HELD_NO_DISPATCH"
    assert result["council_seats_dispatched"] == 0
    assert not result["prediction_registered"]
    assert result["final_action"] == "HOLD"
    assert all(not x["seat_executed"] and not x["model_executed"] for x in rehearsal["seat_records"])


def test_p809_seat_runtime_dependencies_real_manifest(tmp_path):
    result = assess()
    trend = seat(result, 1)
    assert trend["required_source_families"] == ["candles", "market_state"]
    risk = seat(result, 30)
    assert "risk_limits" in risk["required_source_families"]
    assert "risk_state" in risk["required_source_families"]


def test_p809_no_sim_schedule_without_context(tmp_path):
    path = tmp_path / "fake.json"
    save(path, {})
    with pytest.raises(CouncilSandboxError, match="requires a prior synthetic context"):
        assess(schedule=path)


def test_p809_report_digest_tampering_rejected(tmp_path):
    ctx, report = context_fixture(tmp_path)
    report["context"]["status"] = "NOT_SUPPLIED"
    save(ctx, report)
    with pytest.raises(CouncilSandboxError, match="digest mismatch"):
        assess(ctx)


def test_p809_point_in_time_context_missing_refused(tmp_path):
    ctx, report = context_fixture(tmp_path)
    report["context"]["status"] = "NOT_SUPPLIED"
    report["report_sha256"] = _digest({k: v for k, v in report.items() if k != "report_sha256"})
    save(ctx, report)
    with pytest.raises(CouncilSandboxError, match="not available"):
        assess(ctx)


def test_p809_rehashed_fake_independently_admitted_field_refused(tmp_path):
    ctx, report = context_fixture(tmp_path)
    report["context"]["council_context_admitted"] = True
    report["report_sha256"] = _digest({k: v for k, v in report.items() if k != "report_sha256"})
    save(ctx, report)
    with pytest.raises(CouncilSandboxError, match="binding or admission"):
        assess(ctx)


def test_p809_context_report_provenance_never_proves_source(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    result = assess(ctx, path)
    assert result["independent_external_gates_verified"] == 0
    assert result["independent_source_families_verified"] == 0
    assert result["context"]["context_independently_attested"] is False
    assert result["synthetic_schedule"]["model_independently_accepted"] is False


@pytest.mark.parametrize("bad", ["OTHER", "LIVE", "HISTORICAL_BROKER", "SHADOW"])
def test_p809_non_synthetic_mode_refused(tmp_path, bad):
    ctx, report, path, doc = files(tmp_path)
    doc["mode"] = bad
    save(path, doc)
    with pytest.raises(CouncilSandboxError, match="binding mismatch"):
        assess(ctx, path)


@pytest.mark.parametrize("number", [1, 10, 28, 29, 30, 31])
def test_p809_inconsistent_seat_name_or_stage_refused(tmp_path, number):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, number, seat_id="FAKE_SEAT")
    with pytest.raises(CouncilSandboxError, match="identity mismatch"):
        assess(ctx, path)


def test_p809_wrong_context_sha_refused(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["context_sha256"] = "f" * 64
    save(path, doc)
    with pytest.raises(CouncilSandboxError, match="binding mismatch"):
        assess(ctx, path)


def test_p809_wrong_decision_key_refused(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["decision_key"] = "f" * 64
    save(path, doc)
    with pytest.raises(CouncilSandboxError, match="binding mismatch"):
        assess(ctx, path)


# P8-10 declared budget, malformed payload, simulated cancellation/fault.

@pytest.mark.parametrize("number,budget", [(1, PRIMARY_BUDGET_MS), (28, PRIMARY_BUDGET_MS), (29, GOVERNANCE_BUDGET_MS), (30, GOVERNANCE_BUDGET_MS), (31, GOVERNANCE_BUDGET_MS)])
def test_p810_declared_elapsed_budget_excess_requests_cancellation(tmp_path, number, budget):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, number, elapsed_ms=budget + 1)
    result = assess(ctx, path)
    row = seat(result, number)
    assert row["status"] == "TIMEOUT_CANCEL_REQUESTED"
    assert row["timeout_cancellation_requested"]
    assert not row["seat_executed"]
    assert result["final_action"] == "HOLD"


def test_p810_boundary_primary_budget_is_not_timeout(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 3, elapsed_ms=PRIMARY_BUDGET_MS)
    result = assess(ctx, path)
    assert seat(result, 3)["status"] == "LOCAL_SYNTHETIC_RESULT_UNATTESTED"


def test_p810_declared_memory_breach_quarantined(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 2, peak_memory_bytes=MAX_DECLARED_MEMORY_BYTES + 1)
    result = assess(ctx, path)
    assert seat(result, 2)["status"] == "RESOURCE_BUDGET_EXCEEDED"
    assert not result["rehearsal"]["locally_consistent_synthetic_governance"]


@pytest.mark.parametrize("outcome", ["TIMEOUT", "EXCEPTION", "CANCELLED"])
def test_p810_declared_timeout_exception_cancel_separate(tmp_path, outcome):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 4, outcome=outcome, payload=None, exception_code="SYNTHETIC_FAULT")
    result = assess(ctx, path)
    expected = "TIMEOUT_CANCEL_REQUESTED" if outcome == "TIMEOUT" else "FAULT_OR_CANCELLATION"
    assert seat(result, 4)["status"] == expected
    assert result["rehearsal"]["synthetic_only_proposed_direction"] == "HOLD"


def test_p810_exception_with_forged_vote_refused(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 3, outcome="EXCEPTION", exception_code="FAULT")
    with pytest.raises(CouncilSandboxError, match="cannot contain a vote"):
        assess(ctx, path)


def test_p810_success_with_exception_code_refused(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 3, exception_code="FAULT")
    with pytest.raises(CouncilSandboxError, match="cannot carry an exception"):
        assess(ctx, path)


@pytest.mark.parametrize("malformed", [
    {"direction": "EXECUTE", "confidence": "0.7", "reason_code": "FAKE", "cited_labels": ["candles"]},
    {"direction": "BUY", "confidence": 0.7, "reason_code": "FAKE", "cited_labels": ["candles"]},
    {"direction": "BUY", "confidence": "1.123", "reason_code": "FAKE", "cited_labels": ["candles"]},
    {"direction": "BUY", "confidence": "0.7", "reason_code": "FAKE", "cited_labels": []},
    {"direction": "BUY", "confidence": "0.7", "reason_code": "FAKE", "cited_labels": ["fed_data"]},
    {"direction": "BUY", "confidence": "0.7", "reason_code": "FAKE", "cited_labels": ["candles", "candles"]},
    {"direction": "BUY", "confidence": "0.7", "reason_code": "FAKE", "cited_labels": ["candles"], "trade_now": True},
])
def test_p810_malformed_primary_result_cannot_vote(tmp_path, malformed):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 2, payload=malformed)
    result = assess(ctx, path)
    assert seat(result, 2)["status"] == "INVALID_RESULT"
    assert "MALFORMED_SEAT_RESULT" in seat(result, 2)["reason_codes"]
    assert result["rehearsal"]["synthetic_only_proposed_direction"] == "HOLD"


def test_p810_record_over_serialization_budget_rejected(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][0]["payload"]["reason_code"] = "x" * 5000
    save(path, doc)
    with pytest.raises(CouncilSandboxError, match="serialized result exceeds"):
        assess(ctx, path)


def test_p810_cycle_window_breach_hold(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 31, started_ms=9999, elapsed_ms=100)
    result = assess(ctx, path)
    assert "DECLARED_CYCLE_DEADLINE_EXCEEDED" in seat(result, 31)["reason_codes"]
    assert result["final_action"] == "HOLD"


def test_p810_no_claim_of_real_isolation_from_declared_budgets(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    result = assess(ctx, path)
    assert result["rehearsal"]["logical_wall_clock_enforced"] is False
    assert result["rehearsal"]["real_memory_limit_enforced"] is False
    assert result["rehearsal"]["agent_processes_spawned"] == 0


# P8-11 strict primary -> Devil's Advocate -> Risk -> Chief sequence.

def test_p811_governance_sequenced_correctly_for_rehearsal(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    result = assess(ctx, path)
    assert [seat(result, n)["status"] for n in (29, 30, 31)] == [
        "LOCAL_SYNTHETIC_RESULT_UNATTESTED"] * 3
    assert result["rehearsal"]["declared_risk_veto"] is False


def test_p811_risk_veto_forces_hold_despite_28_buys(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][29]["payload"]["veto"] = True
    save(path, doc)
    result = assess(ctx, path)
    r = result["rehearsal"]
    assert r["primary_buy_count"] == 28
    assert r["declared_strategist_direction"] == "BUY"
    assert r["declared_risk_veto"] is True
    assert "RISK_OFFICER_VETO_FORCES_HOLD" in r["reason_codes"]
    assert r["synthetic_only_proposed_direction"] == "HOLD"
    assert result["final_action"] == "HOLD"


@pytest.mark.parametrize("number,started,reason", [
    (29, 39, "DEVILS_ADVOCATE_BEFORE_PRIMARY_BARRIER"),
    (30, 80, "RISK_OFFICER_BEFORE_DEVILS_ADVOCATE"),
    (31, 120, "STRATEGIST_BEFORE_RISK_OFFICER"),
])
def test_p811_governance_cannot_jump_barrier(tmp_path, number, started, reason):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, number, started_ms=started)
    result = assess(ctx, path)
    assert reason in seat(result, number)["reason_codes"]
    assert seat(result, number)["status"] == "INPUT_OR_ORDER_BLOCKED"
    assert result["rehearsal"]["synthetic_only_proposed_direction"] == "HOLD"


def test_p811_risk_veto_declared_even_if_sequencing_fault(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][29]["payload"]["veto"] = True
    change(path, doc, 30, started_ms=0)
    result = assess(ctx, path)
    assert result["rehearsal"]["declared_risk_veto"] is True
    assert result["rehearsal"]["synthetic_only_proposed_direction"] == "HOLD"


def test_p811_devils_advocate_challenge_is_not_silent_pass(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][28]["payload"]["challenge"] = "CHALLENGE"
    save(path, doc)
    result = assess(ctx, path)
    assert "DEVILS_ADVOCATE_UNRESOLVED_CHALLENGE" in result["rehearsal"]["reason_codes"]


def test_p811_malformed_risk_veto_is_never_clearance(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][29]["payload"]["veto"] = "false"
    save(path, doc)
    result = assess(ctx, path)
    assert seat(result, 30)["status"] == "INVALID_RESULT"
    assert result["rehearsal"]["declared_risk_veto"] is None
    assert "RISK_OFFICER_NO_VALID_VETO_CLEARANCE" in result["rehearsal"]["reason_codes"]


def test_p811_strategist_cannot_override_disagreement(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    for i in range(12):
        doc["seat_results"][i]["payload"]["direction"] = "SELL"
    save(path, doc)
    result = assess(ctx, path)
    assert "PRIMARY_DIRECTIONAL_DISAGREEMENT" in result["rehearsal"]["reason_codes"]
    assert result["rehearsal"]["synthetic_only_proposed_direction"] == "HOLD"


def test_p811_strategist_disagrees_with_majority_hold(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][30]["payload"]["direction"] = "SELL"
    save(path, doc)
    r = assess(ctx, path)["rehearsal"]
    assert "STRATEGIST_DIRECTION_CONTRADICTS_PRIMARY_MAJORITY" in r["reason_codes"]
    assert r["synthetic_only_proposed_direction"] == "HOLD"


def test_p811_strategist_explicit_hold_no_registration(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][30]["payload"]["direction"] = "HOLD"
    save(path, doc)
    result = assess(ctx, path)
    assert "STRATEGIST_EXPLICIT_HOLD" in result["rehearsal"]["reason_codes"]
    assert result["prediction_registered"] is False


# P8-12 missing, explicit abstention and disagreement are auditable.

def test_p812_one_missing_primary_not_implicit_abstain(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"].pop(0)
    save(path, doc)
    result = assess(ctx, path)
    r = result["rehearsal"]
    assert "trend_master" in r["missing_seat_ids"]
    assert r["explicit_primary_abstention_count"] == 0
    assert "MISSING_SEATS_ARE_NOT_IMPLICIT_ABSTENTIONS" in r["reason_codes"]
    assert r["synthetic_only_proposed_direction"] == "HOLD"


def test_p812_explicit_abstain_distinguished_from_missing(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][0]["outcome"] = "ABSTAIN"
    doc["seat_results"][0]["payload"] = {
        "direction": "ABSTAIN", "confidence": "0.000",
        "reason_code": "SYNTHETIC_NO_SIGNAL", "cited_labels": [],
    }
    save(path, doc)
    r = assess(ctx, path)["rehearsal"]
    assert r["explicit_primary_abstention_count"] == 1
    assert r["valid_primary_directional_count"] == 27
    assert r["missing_seat_ids"] == []
    assert r["locally_consistent_synthetic_governance"]


@pytest.mark.parametrize("abstentions", [5, 9, 28])
def test_p812_excessive_explicit_abstentions_force_hold(tmp_path, abstentions):
    ctx, report, path, doc = files(tmp_path)
    for i in range(abstentions):
        doc["seat_results"][i]["outcome"] = "ABSTAIN"
        doc["seat_results"][i]["payload"] = {
            "direction": "ABSTAIN", "confidence": "0.000",
            "reason_code": "SYNTHETIC_ABSTAIN", "cited_labels": [],
        }
    save(path, doc)
    result = assess(ctx, path)
    assert result["rehearsal"]["explicit_primary_abstention_count"] == abstentions
    assert "PRIMARY_DIRECTIONAL_QUORUM_NOT_MET" in result["rehearsal"]["reason_codes"]
    assert result["final_action"] == "HOLD"


def test_p812_local_input_quarantine_is_not_overridden_by_vote(tmp_path):
    ctx, report, path, doc = files(tmp_path, full=False)
    result = assess(ctx, path)
    assert seat(result, 1)["status"] == "INPUT_OR_ORDER_BLOCKED"
    assert any("market_state" in x for x in seat(result, 1)["reason_codes"])
    assert result["rehearsal"]["synthetic_only_proposed_direction"] == "HOLD"


def test_p812_no_model_mock_marks_exact_three_ml_seats(tmp_path):
    ctx, report, path, doc = files(tmp_path, mock=None)
    result = assess(ctx, path)
    for n in (11, 12, 13):
        assert "SYNTHETIC_MODEL_MOCK_NOT_SUPPLIED" in seat(result, n)["reason_codes"]
    assert result["rehearsal"]["valid_primary_directional_count"] == 25
    assert result["final_action"] == "HOLD"


def test_p812_missing_risk_seat_cannot_be_replaced_by_abstention(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"].pop(29)
    save(path, doc)
    result = assess(ctx, path)
    assert "risk_officer" in result["rehearsal"]["missing_seat_ids"]
    assert "RISK_OFFICER_MISSING" in result["rehearsal"]["reason_codes"]
    assert result["rehearsal"]["declared_risk_veto"] is None
    assert result["final_action"] == "HOLD"


def test_p812_index_order_invariance_for_same_envelopes(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    baseline = assess(ctx, path)
    doc["seat_results"].reverse()
    save(path, doc)
    shuffled = assess(ctx, path)
    assert shuffled["rehearsal"] == baseline["rehearsal"]
    assert shuffled["synthetic_schedule"]["index_sha256"] != baseline["synthetic_schedule"]["index_sha256"]


def test_p812_duplicate_seat_number_rejected(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][1]["seat_number"] = 1
    save(path, doc)
    with pytest.raises(CouncilSandboxError, match="Duplicate or invalid"):
        assess(ctx, path)


def test_p812_absent_governance_keeps_primary_votes_but_hold(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"] = doc["seat_results"][:28]
    save(path, doc)
    result = assess(ctx, path)
    assert result["rehearsal"]["valid_primary_directional_count"] == 28
    assert len(result["rehearsal"]["missing_seat_ids"]) == 3
    assert "GOVERNANCE_SEAT_NOT_VALIDATED" in result["rehearsal"]["reason_codes"]


# Strict parsing, immutable epoch, CLI, no execution safety.

@pytest.mark.parametrize("bad", [
    "2026-10-30T11:00:00", "2026-10-30T14:00:00+03:00",
    "2026-10-30T11:00:60Z", "2026-11-31T11:00:00Z", "unparseable",
])
def test_p812_strict_utc_required(bad):
    with pytest.raises(CouncilSandboxError):
        _utc(bad)


def test_p812_wrong_historical_epoch_never_rebound():
    with pytest.raises(CouncilSandboxError, match="rebinding refused"):
        assess_stage8_council(epoch_id="f" * 64, as_of=ASOF, policy_file=POLICY,
                              contracts_file=CONTRACTS, manifest_file=MANIFEST)


def test_p812_policy_byte_tamper_refused(tmp_path):
    policy = tmp_path / "modified_clock.json"
    policy.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(CouncilSandboxError, match="policy SHA-256 mismatch"):
        assess(policy=policy)


def test_p812_schedule_wrong_epoch_refused(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["historical_epoch_id"] = "f" * 64
    save(path, doc)
    with pytest.raises(CouncilSandboxError, match="binding mismatch"):
        assess(ctx, path)


def test_p812_schedule_extra_authority_field_refused(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["enable_live_order"] = True
    save(path, doc)
    with pytest.raises(CouncilSandboxError, match="Strict schema"):
        assess(ctx, path)


def test_p812_duplicate_json_key_refused(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    path.write_text('{"mode":"SYNTHETIC_OFFLINE","mode":"SHADOW"}', encoding="utf-8")
    with pytest.raises(CouncilSandboxError, match="Duplicate JSON"):
        assess(ctx, path)


def test_p812_context_manifest_version_mismatch_rejected(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    modified = tmp_path / "manifest_comment.py"
    modified.write_bytes(MANIFEST.read_bytes() + b"\n# synthetic comment\n")
    with pytest.raises(CouncilSandboxError, match="binding or admission"):
        assess(ctx, path, manifest_file=modified)


def test_p812_seat_context_sources_not_imported_as_agents(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    result = assess(ctx, path)
    for key in (
        "files_written", "network_accessed", "broker_connected", "collector_started",
        "council_invoked", "model_loaded", "training_authorized", "settlement_invoked",
        "paper_orders_authorized", "live_orders_authorized", "historical_epoch_rebound",
        "forward_epoch_created", "operator_approval_granted", "prediction_registered",
    ):
        assert result[key] is False
    assert result["read_only"] is True
    assert result["final_action"] == "HOLD"


def test_p812_repeat_same_packet_hash_and_report_determinism(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    a, b = assess(ctx, path), assess(ctx, path)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_p812_no_free_text_model_or_raw_context_in_output(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    body = json.dumps(assess(ctx, path))
    assert "UNVERIFIED_MARKET_CONTEXT" not in body
    assert "payload/candles_01.json" not in body
    assert "1.0840" not in body
    assert "SYNTHETIC_REASON" not in body


def test_p812_no_packet_cli_remains_hold(capsys):
    code = run(["--read-only-council", "--synthetic-only", "--epoch-id", EPOCH,
                "--as-of", ASOF, "--policy-file", str(POLICY),
                "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST)])
    assert code == 0
    output = capsys.readouterr().out
    assert "28 PRIMARY | 2 GOVERNANCE | 1 STRATEGIST" in output
    assert "Real Council seats dispatched: 0" in output
    assert "FINAL=HOLD" in output


def test_p812_cli_json_synthetic_payload_stays_hold(capsys, tmp_path):
    ctx, report, path, doc = files(tmp_path)
    assert run(["--read-only-council", "--synthetic-only", "--epoch-id", EPOCH,
                "--as-of", ASOF, "--policy-file", str(POLICY),
                "--context-report", str(ctx), "--seat-schedule", str(path),
                "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST),
                "--json"]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["rehearsal"]["submitted_seat_count"] == 31
    assert output["final_action"] == "HOLD"


def test_p812_cli_readonly_flags_required():
    with pytest.raises(SystemExit) as err:
        run(["--epoch-id", EPOCH, "--as-of", ASOF])
    assert err.value.code == 2


def test_p812_cli_future_cutoff_blocked(capsys):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    assert run([
        "--read-only-council",
        "--synthetic-only",
        "--epoch-id", EPOCH,
        "--as-of", future,
        "--policy-file", str(POLICY),
        "--contracts-file", str(CONTRACTS),
        "--manifest-file", str(MANIFEST),
    ]) == 1

    assert "BLOCKED" in capsys.readouterr().out

def test_p812_symlink_seat_schedule_rejected_if_supported(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    link = tmp_path / "alias.json"
    try:
        link.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink unavailable on this Windows host")
    with pytest.raises(CouncilSandboxError, match="Symlink"):
        assess(ctx, link)


# Extra fail-closed stage barriers and parser bounds.

def test_p811_timeout_primary_blocks_all_three_governance_stages(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 1, elapsed_ms=PRIMARY_BUDGET_MS + 1)
    result = assess(ctx, path)
    assert "PRIMARY_OPINION_BARRIER_NOT_READY" in seat(result, 29)["reason_codes"]
    assert "DEVILS_ADVOCATE_NOT_VALIDATED_BEFORE_RISK" in seat(result, 30)["reason_codes"]
    assert "RISK_OFFICER_NOT_VALIDATED_BEFORE_STRATEGY" in seat(result, 31)["reason_codes"]
    assert result["final_action"] == "HOLD"


def test_p811_invalid_devils_advocate_blocks_risk_and_synthesis(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"][28]["payload"]["challenge"] = "OVERRIDE"
    save(path, doc)
    result = assess(ctx, path)
    assert seat(result, 29)["status"] == "INVALID_RESULT"
    assert "DEVILS_ADVOCATE_NOT_VALIDATED_BEFORE_RISK" in seat(result, 30)["reason_codes"]
    assert "RISK_OFFICER_NOT_VALIDATED_BEFORE_STRATEGY" in seat(result, 31)["reason_codes"]


@pytest.mark.parametrize("bad", [-1, True, "12", 60001])
def test_p810_invalid_declared_started_ms_refused(tmp_path, bad):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 2, started_ms=bad)
    with pytest.raises(CouncilSandboxError, match="bounded integer"):
        assess(ctx, path)


def test_p810_invalid_declared_memory_type_refused(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    change(path, doc, 2, peak_memory_bytes="100")
    with pytest.raises(CouncilSandboxError, match="bounded integer"):
        assess(ctx, path)


def test_p812_more_than_31_results_refused(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    doc["seat_results"].append(deepcopy(doc["seat_results"][0]))
    save(path, doc)
    with pytest.raises(CouncilSandboxError, match="exceeds 31"):
        assess(ctx, path)


def test_p812_prior_context_seat_identity_tamper_rejected_even_if_rehashed(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    report["context"]["seat_input_preflight"][0]["seat_id"] = "other"
    report["report_sha256"] = _digest({k: v for k, v in report.items() if k != "report_sha256"})
    save(ctx, report)
    with pytest.raises(CouncilSandboxError, match="preflight identity mismatch"):
        assess(ctx, path)


def test_p812_synthetic_model_mock_does_not_grant_external_model_approval(tmp_path):
    ctx, report, path, doc = files(tmp_path)
    result = assess(ctx, path)
    assert result["synthetic_schedule"]["model_mock_digest"] == MOCK_SHA
    assert result["synthetic_schedule"]["model_independently_accepted"] is False
    assert result["model_loaded"] is False
    assert result["final_action"] == "HOLD"
