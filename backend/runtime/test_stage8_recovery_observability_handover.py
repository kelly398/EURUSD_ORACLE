
"""P8-21..24: synthetic crash recovery, structured telemetry and HOLD handover.

No production files are changed. Temporary test fixtures are the only writers.
All journal, test, STOP/HOLD and release claims remain local and UNATTESTED.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import EPOCH, POLICY_SHA256
from backend.runtime.stage8_offline_shadow_cycle_rehearsal import (
    assess_stage8_shadow_rehearsal,
)
from backend.runtime.test_stage8_offline_shadow_cycle_rehearsal import (
    fixtures, save, recalc_context, rebuild, _refresh_after_schedule,
    POLICY, CONTRACTS, MANIFEST, ASOF, gate as old_gate,
)
from backend.runtime.test_stage8_point_in_time_market_state import change_payload
from backend.runtime.stage8_recovery_observability_handover import (
    VERSION, RECOVERY_VERSION, OBSERVABILITY_VERSION, RELEASE_VERSION,
    HANDOVER_VERSION, ZERO, FAMILIES, TEST_FILES, RecoveryHandoverError,
    _hash, _time, assess_stage8_recovery_handover, run,
)

ROOT = Path(__file__).resolve().parents[2]
START = "2026-10-30T10:00:20Z"
TEST_AT = "2026-10-30T10:55:00Z"


def prepare(tmp_path, *, forecast=False, full=True):
    manifest, doc = fixtures(tmp_path, forecast=forecast, full=full)
    frozen = assess_stage8_shadow_rehearsal(
        epoch_id=EPOCH, as_of=ASOF, policy_file=POLICY,
        fixture_manifest=manifest, evidence_root=tmp_path,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )
    path = tmp_path / "frozen_stage8_batch5_report.json"
    save(path, frozen)
    return manifest, doc, path, frozen


def re_frozen(tmp_path):
    manifest = tmp_path / "batch5_manifest.json"
    frozen = assess_stage8_shadow_rehearsal(
        epoch_id=EPOCH, as_of=ASOF, policy_file=POLICY,
        fixture_manifest=manifest, evidence_root=tmp_path,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )
    save(tmp_path / "frozen_stage8_batch5_report.json", frozen)
    return frozen


def assess(replay=None, fixture=None, root=None, *, journal=None, gate=None,
           tests=None, policy=POLICY, cutoff=ASOF, contracts=CONTRACTS,
           manifest=MANIFEST):
    return assess_stage8_recovery_handover(
        epoch_id=EPOCH, as_of=cutoff, policy_file=policy,
        replay_report=replay, fixture_manifest=fixture, evidence_root=root,
        recovery_journal=journal, shadow_gate_claim=gate,
        test_result_claim=tests, contracts_file=contracts,
        manifest_file=manifest,
    )


def checked(tmp_path, **kwargs):
    manifest, doc, replay, frozen = prepare(tmp_path)
    return assess(replay, manifest, tmp_path, **kwargs)


def materialized(tmp_path, **kwargs):
    manifest, doc, replay, frozen = prepare(tmp_path)
    return assess(replay, manifest, tmp_path, **kwargs), frozen


def make_journal(tmp_path, frozen, kinds, *, effective_at=START):
    decision = frozen["cycles"][0]
    entries = []
    for kind in kinds:
        anchors = {
            "STARTED": None,
            "CRASH_MARK": None,
            "CONTEXT_COMMITTED": decision["context_report_sha256"],
            "COUNCIL_COMMITTED": decision["council_report_sha256"],
            "MEMORY_COMMITTED": decision["memory_report_sha256"],
            "HELD_TERMINAL": decision["evidence_events"][-1]["event_sha256"],
        }
        event = {
            "offset": len(entries), "decision_key": decision["decision_key"],
            "case_id": decision["case_id"], "event_type": kind,
            "effective_at": effective_at,
            "checkpoint_sha256": anchors[kind],
            "previous_entry_sha256": entries[-1]["entry_sha256"] if entries else ZERO,
        }
        entries.append({**event, "entry_sha256": _hash(event)})
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "LOCAL_UNVERIFIED_RECOVERY_JOURNAL",
        "mode": "LOCAL_UNATTESTED_CRASH_RECOVERY",
        "replay_report_sha256": frozen["report_sha256"],
        "entries": entries,
    }
    path = tmp_path / "local_unverified_recovery.json"
    save(path, doc)
    return path, doc


def rechain(doc):
    previous = ZERO
    for i, item in enumerate(doc["entries"]):
        item["offset"] = i
        item["previous_entry_sha256"] = previous
        item["entry_sha256"] = _hash({k: v for k, v in item.items() if k != "entry_sha256"})
        previous = item["entry_sha256"]


def make_gate(tmp_path, *, fully_claimed=False):
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "UNVERIFIED_DRY_RUN_SHADOW_CLAIM",
        "mode": "LOCAL_UNATTESTED_RELEASE_CLAIMS",
        "requested_scope": "RESEARCH_SHADOW_ONLY",
        "operator_scope_claim": "LOCAL_UNVERIFIED_CLAIM" if fully_claimed else "NOT_SUPPLIED",
        "source_family_claims": list(FAMILIES) if fully_claimed else [],
        "forecast_witness_plan_sha256": sha256(b"UNVERIFIED_WITNESS_PLAN").hexdigest() if fully_claimed else None,
        "runner_code_sha256": sha256(b"UNVERIFIED_RUNNER").hexdigest() if fully_claimed else None,
        "stop_hold_test_claim": {
            "test_id": "FAKE_LOCAL_STOP_REHEARSAL", "result": "LOCAL_PASS_CLAIM",
            "test_artifact_sha256": sha256(b"NO_INDEPENDENT_STOP_TEST").hexdigest(),
        } if fully_claimed else None,
    }
    path = tmp_path / "local_shadow_gate_claim.json"
    save(path, doc)
    return path, doc


def make_test_claim(tmp_path):
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "LOCAL_UNVERIFIED_STAGE8_PYTEST",
        "mode": "LOCAL_TEST_RUN_UNATTESTED", "suite": "STAGE8_ALLOWLIST_ONLY",
        "collected": 477, "passed": 471, "skipped": 6, "failed": 0,
        "run_at": TEST_AT,
        "result_digest": sha256(b"UNVERIFIED_PYTEST_TRANSCRIPT").hexdigest(),
        "selected_test_files": list(TEST_FILES[:5]),
    }
    path = tmp_path / "local_stage8_test_claim.json"
    save(path, doc)
    return path, doc


def context_council_mutation(tmp_path, *, timeout=False, veto=False):
    path = tmp_path / "synthetic_only_seat_results.json"
    doc = json.loads(path.read_text("utf-8"))
    if timeout:
        doc["seat_results"][2]["elapsed_ms"] = 251
    if veto:
        doc["seat_results"][29]["payload"]["veto"] = True
    _refresh_after_schedule(tmp_path, doc)
    return re_frozen(tmp_path)


# P8-21: logical recovery journal, read-only offsets, crash/no-edit policies.

def test_p821_no_replay_has_no_journal_or_recovery_claim():
    r = assess()
    assert r["recovery"]["status"] == "NOT_SUPPLIED"
    assert r["recovery"]["logical_decisions"] == []
    assert r["recovery"]["journal_fsync_verified"] is False
    assert r["final_action"] == "HOLD"


def test_p821_local_frozen_replay_does_not_self_create_journal(tmp_path):
    r = checked(tmp_path)
    assert r["recovery"]["logical_decision_count"] == 1
    assert r["recovery"]["locally_verified_entry_count"] == 0
    assert r["recovery"]["logical_decisions"][0]["recovery_state"] == "NO_LOCAL_JOURNAL_EVENT"
    assert not r["recovery"]["journal_mutated"]


def test_p821_partial_started_state_preserves_same_decision_key(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    journal, doc = make_journal(tmp_path, frozen, ["STARTED"])
    r = assess(p, m, tmp_path, journal=journal)
    state = r["recovery"]["logical_decisions"][0]
    assert state["recovery_state"] == "PARTIAL_HELD_NO_DISPATCH"
    assert state["preview_resume_from"] == "STARTED"
    assert state["logical_replay_decision_key_preserved"]
    assert not state["duplicate_forecast_registration_permitted"]


def test_p821_crash_after_context_checkpoint_is_not_terminal(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    journal, doc = make_journal(tmp_path, frozen, ["STARTED", "CONTEXT_COMMITTED", "CRASH_MARK"])
    state = assess(p, m, tmp_path, journal=journal)["recovery"]["logical_decisions"][0]
    assert state["recovery_state"] == "CRASHED_PARTIAL_HELD"
    assert state["preview_resume_from"] == "CONTEXT_COMMITTED"
    assert state["last_locally_claimed_offset"] == 2


def test_p821_context_and_council_and_memory_checkpoint_bound_to_frozen_sha(tmp_path):
    m, d, p, frozen = prepare(tmp_path)

    journal, doc = make_journal(tmp_path, frozen, [
        "STARTED",
        "CONTEXT_COMMITTED",
        "COUNCIL_COMMITTED",
        "MEMORY_COMMITTED",
    ])

    r = assess(p, m, tmp_path, journal=journal)

    assert r["recovery"]["locally_verified_entry_count"] == 4
    assert r["recovery"]["partial_or_crashed_count"] == 1
    assert r["recovery"]["terminal_held_count"] == 0


def test_p821_held_terminal_no_duplicate_recovery(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    journal, doc = make_journal(tmp_path, frozen, [
        "STARTED", "CONTEXT_COMMITTED", "COUNCIL_COMMITTED",
        "MEMORY_COMMITTED", "HELD_TERMINAL",
    ])
    result = assess(p, m, tmp_path, journal=journal)
    state = result["recovery"]["logical_decisions"][0]
    assert state["recovery_state"] == "TERMINAL_HELD_ALREADY_RECORDED"
    assert state["preview_resume_from"] is None
    assert result["recovery"]["terminal_held_count"] == 1
    assert result["predictions_registered"] == 0


def test_p821_crashed_cycle_may_be_marked_held_without_restarting(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    journal, doc = make_journal(tmp_path, frozen, ["STARTED", "CRASH_MARK", "HELD_TERMINAL"])
    result = assess(p, m, tmp_path, journal=journal)
    assert result["recovery"]["terminal_held_count"] == 1
    assert result["recovery"]["recovery_process_started"] is False


def test_p821_recovery_journal_requires_frozen_replay(tmp_path):
    path = tmp_path / "journal.json"
    save(path, {})
    with pytest.raises(RecoveryHandoverError, match="requires a verified frozen"):
        assess(journal=path)


def test_p821_duplicate_start_rejected_even_after_rechain(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, ["STARTED", "STARTED"])
    with pytest.raises(RecoveryHandoverError, match="Duplicate STARTED"):
        assess(p, m, tmp_path, journal=path)


@pytest.mark.parametrize("sequence", [
    ["CONTEXT_COMMITTED"],
    ["HELD_TERMINAL"],
    ["STARTED", "COUNCIL_COMMITTED"],
    ["STARTED", "MEMORY_COMMITTED"],
    ["STARTED", "CONTEXT_COMMITTED", "MEMORY_COMMITTED"],
    ["STARTED", "CONTEXT_COMMITTED", "CONTEXT_COMMITTED"],
    ["STARTED", "CRASH_MARK", "CRASH_MARK"],
    ["STARTED", "HELD_TERMINAL", "CRASH_MARK"],
    ["STARTED", "HELD_TERMINAL", "HELD_TERMINAL"],
])
def test_p821_skipped_duplicate_or_postterminal_event_refused(tmp_path, sequence):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, sequence)
    with pytest.raises(RecoveryHandoverError):
        assess(p, m, tmp_path, journal=path)


@pytest.mark.parametrize("key,value", [
    ("offset", 10), ("previous_entry_sha256", "f" * 64),
    ("entry_sha256", "f" * 64), ("decision_key", "f" * 64),
    ("case_id", "FORGED_CASE"), ("checkpoint_sha256", "f" * 64),
    ("effective_at", "2026-10-30T09:59:59Z"),
    ("event_type", "REGISTERED"),
])
def test_p821_tampered_unrehashed_record_refused(tmp_path, key, value):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, ["STARTED", "CONTEXT_COMMITTED"])
    doc["entries"][1][key] = value
    save(path, doc)
    with pytest.raises(RecoveryHandoverError):
        assess(p, m, tmp_path, journal=path)


def test_p821_rehashed_wrong_checkpoint_still_rejected(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, ["STARTED", "CONTEXT_COMMITTED"])
    doc["entries"][1]["checkpoint_sha256"] = "f" * 64
    rechain(doc)
    save(path, doc)
    with pytest.raises(RecoveryHandoverError, match="checkpoint disagrees"):
        assess(p, m, tmp_path, journal=path)


def test_p821_rehashed_duplicate_terminal_still_rejected(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, ["STARTED", "HELD_TERMINAL", "HELD_TERMINAL"])
    with pytest.raises(RecoveryHandoverError, match="Terminal recovery"):
        assess(p, m, tmp_path, journal=path)


def test_p821_journal_wrong_replay_digest_refused(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, ["STARTED"])
    doc["replay_report_sha256"] = "f" * 64
    save(path, doc)
    with pytest.raises(RecoveryHandoverError, match="binding mismatch"):
        assess(p, m, tmp_path, journal=path)


def test_p821_journal_wrong_epoch_refused(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, ["STARTED"])
    doc["historical_epoch_id"] = "f" * 64
    save(path, doc)
    with pytest.raises(RecoveryHandoverError, match="binding mismatch"):
        assess(p, m, tmp_path, journal=path)


def test_p821_crash_event_after_terminal_never_restart(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, ["STARTED", "HELD_TERMINAL", "CRASH_MARK"])
    with pytest.raises(RecoveryHandoverError, match="Terminal recovery"):
        assess(p, m, tmp_path, journal=path)


def test_p821_local_recovery_journal_bytes_not_modified(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, ["STARTED", "CRASH_MARK"])
    before = sha256(path.read_bytes()).hexdigest()
    r = assess(p, m, tmp_path, journal=path)
    assert sha256(path.read_bytes()).hexdigest() == before
    assert r["recovery"]["exactly_once_durable_delivery_proven"] is False
    assert r["recovery"]["independent_journal_custody_verified"] is False


def test_p821_forged_journal_field_refused(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, ["STARTED"])
    doc["allow_live_trading"] = True
    save(path, doc)
    with pytest.raises(RecoveryHandoverError, match="Strict schema"):
        assess(p, m, tmp_path, journal=path)


def test_p821_empty_existing_local_journal_has_unverified_prefix(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, doc = make_journal(tmp_path, frozen, [])
    r = assess(p, m, tmp_path, journal=path)
    assert r["recovery"]["status"].startswith("LOCAL_JOURNAL")
    assert r["recovery"]["locally_verified_prefix_tip_sha256"] is None
    assert r["recovery"]["journal_fsync_verified"] is False


# P8-22: telemetry is descriptive local fixture evidence, not proof of edge.

def test_p822_no_replay_reports_no_invented_latency():
    result = assess()
    metric = result["observability"]
    assert metric["status"] == "NOT_SUPPLIED"
    assert metric["case_observations"] == []
    assert metric["independent_prediction_receipt_measurements"] == 0


def test_p822_full_fixture_bar_lateness_and_missing_families(tmp_path):
    result = checked(tmp_path)
    case = result["observability"]["case_observations"][0]
    assert case["bar_lateness_seconds_claim"] == "3.0"
    assert case["bar_lateness_measurement"] == "SYNTHETIC_FIXTURE_CLAIM"
    assert case["missing_source_family_count_claim"] == 0
    assert case["quarantined_source_family_count_claim"] == 0
    assert case["frozen_chain_complete"] is True


def test_p822_timeout_count_zero_when_seats_replayed(tmp_path):
    r = checked(tmp_path)
    case = r["observability"]["case_observations"][0]
    assert case["seat_timeout_count_claim"] == 0
    assert r["observability"]["councils_with_timeout_measurement"] == 1


def test_p822_timeout_count_from_declarative_council_claim(tmp_path):
    prepare(tmp_path)
    context_council_mutation(tmp_path, timeout=True)
    r = assess(tmp_path / "frozen_stage8_batch5_report.json",
               tmp_path / "batch5_manifest.json", tmp_path)
    assert r["observability"]["declared_seat_timeouts_in_observed_councils"] == 1
    assert r["observability"]["case_observations"][0]["seat_timeout_count_claim"] == 1
    assert r["final_action"] == "HOLD"


def test_p822_risk_veto_observed_but_not_risk_approval(tmp_path):
    prepare(tmp_path)
    context_council_mutation(tmp_path, veto=True)
    r = assess(tmp_path / "frozen_stage8_batch5_report.json",
               tmp_path / "batch5_manifest.json", tmp_path)
    assert r["observability"]["risk_veto_true_cases"] == 1
    assert r["release_gate"]["real_stop_hold_mechanism_tested"] is False
    assert r["final_action"] == "HOLD"


def test_p822_missing_source_family_count_from_partial_context(tmp_path):
    m, d, p, frozen = prepare(tmp_path, full=False)
    r = assess(p, m, tmp_path)
    case = r["observability"]["case_observations"][0]
    assert case["missing_source_family_count_claim"] == 12
    assert case["missing_seat_count"] == 0


def test_p822_receipt_lag_not_zero_when_receipt_missing(tmp_path):
    r = checked(tmp_path)
    case = r["observability"]["case_observations"][0]
    assert case["independent_prediction_receipt_lag_seconds"] is None
    assert "NOT_MEASURED" in case["independent_prediction_receipt_lag_state"]
    assert r["observability"]["independent_prediction_receipt_measurements"] == 0


def test_p822_no_trading_quality_implied_by_agreement(tmp_path):
    r = checked(tmp_path)
    metric = r["observability"]
    assert metric["trading_quality_or_predictive_edge_proven"] is False
    assert metric["actual_runtime_or_process_isolation_measured"] is False
    assert metric["network_metrics_published"] is False


def test_p822_stale_bar_rehearsal_is_not_counted_as_complete(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    hbpath = tmp_path / "heartbeat_index.json"
    h = json.loads(hbpath.read_text("utf-8"))
    h["envelopes"][0]["received_at"] = "2026-10-30T10:03:00Z"
    save(hbpath, h)
    re_frozen(tmp_path)
    r = assess(p, m, tmp_path)
    assert r["observability"]["stale_bar_case_count"] == 1
    assert r["observability"]["frozen_chain_reconstruction_count"] == 0
    assert r["observability"]["case_observations"][0]["bar_lateness_seconds_claim"] == "180.0"


def test_p822_no_report_uses_none_not_false_for_unknown_veto():
    r = assess()
    assert r["observability"]["case_observations"] == []


def test_p822_all_observations_have_decision_key_and_hold(tmp_path):
    r = checked(tmp_path)
    for x in r["observability"]["case_observations"]:
        assert len(x["decision_key"]) == 64
        assert x["terminal_cycle_held"]
        assert x["real_cycle_dispatched"] is False
        assert x["metrics_independently_witnessed"] is False


# P8-23: release gate never promotes locally supplied authorization claims.

def test_p823_no_gate_packet_is_blocked():
    r = assess()
    assert r["release_gate"]["status"] == "BLOCKED_EXTERNAL_EVIDENCE"
    assert r["release_gate"]["dry_run_release_decision"] == "BLOCKED_NO_SHADOW"
    assert r["real_shadow_cycles_started"] == 0


def test_p823_full_local_claims_still_cannot_authorize_shadow(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    gate, doc = make_gate(tmp_path, fully_claimed=True)
    r = assess(p, m, tmp_path, gate=gate)
    assert r["release_gate"]["local_all_checklist_claims_present"]
    assert r["release_gate"]["local_source_family_claim_count"] == 13
    assert r["release_gate"]["independent_operator_scope_approved"] is False
    assert r["release_gate"]["independent_source_families_admitted"] == 0
    assert r["release_gate"]["real_stop_hold_mechanism_tested"] is False
    assert r["release_gate"]["shadow_process_started"] is False
    assert r["final_action"] == "HOLD"


def test_p823_full_gate_claims_without_replay_still_blocked(tmp_path):
    gate, doc = make_gate(tmp_path, fully_claimed=True)
    r = assess(gate=gate)
    assert r["release_gate"]["local_all_checklist_claims_present"]
    assert "NO_FROZEN_END_TO_END_OFFLINE_REPLAY_PROOF_SUPPLIED" in r["release_gate"]["blockers"]
    assert r["release_gate"]["dry_run_release_decision"] == "BLOCKED_NO_SHADOW"


@pytest.mark.parametrize("value", ["APPROVED", "SIGNED", "INDEPENDENTLY_VERIFIED", "REAL_GO", True])
def test_p823_self_approved_operator_claim_rejected(tmp_path, value):
    gate, doc = make_gate(tmp_path)
    doc["operator_scope_claim"] = value
    save(gate, doc)
    with pytest.raises(RecoveryHandoverError, match="cannot be self-authorized"):
        assess(gate=gate)


@pytest.mark.parametrize("value", ["SIGNED", "OPERATIONAL", "GO", True])
def test_p823_forged_stop_control_result_rejected(tmp_path, value):
    gate, doc = make_gate(tmp_path, fully_claimed=True)
    doc["stop_hold_test_claim"]["result"] = value
    save(gate, doc)
    with pytest.raises(RecoveryHandoverError, match="self-assert approval"):
        assess(gate=gate)


def test_p823_local_failed_stop_test_always_blocks(tmp_path):
    gate, doc = make_gate(tmp_path, fully_claimed=True)
    doc["stop_hold_test_claim"]["result"] = "LOCAL_FAIL_CLAIM"
    save(gate, doc)
    r = assess(gate=gate)
    assert "LOCAL_STOP_HOLD_TEST_REPORTED_FAILED" in r["release_gate"]["blockers"]
    assert r["release_gate"]["shadow_process_started"] is False


@pytest.mark.parametrize("changed", [
    {"requested_scope": "LIVE_TRADING"},
    {"historical_epoch_id": "f" * 64},
    {"mode": "EXTERNALLY_APPROVED"},
    {"source_family_claims": ["candles", "candles"]},
    {"source_family_claims": ["illegal_family"]},
    {"forecast_witness_plan_sha256": "bad"},
    {"runner_code_sha256": "bad"},
    {"allow_live_orders": True},
])
def test_p823_invalid_gate_schema_and_scope_refused(tmp_path, changed):
    gate, doc = make_gate(tmp_path, fully_claimed=True)
    doc.update(changed)
    save(gate, doc)
    with pytest.raises(RecoveryHandoverError):
        assess(gate=gate)


def test_p823_stop_result_digest_must_be_paired(tmp_path):
    gate, doc = make_gate(tmp_path, fully_claimed=True)
    doc["stop_hold_test_claim"]["test_artifact_sha256"] = None
    save(gate, doc)
    with pytest.raises(RecoveryHandoverError, match="claim/result digest mismatch"):
        assess(gate=gate)


def test_p823_gate_local_hash_is_not_independent_receipt(tmp_path):
    gate, doc = make_gate(tmp_path, fully_claimed=True)
    r = assess(gate=gate)
    assert len(r["release_gate"]["local_gate_packet_sha256"]) == 64
    assert r["independent_forecast_receipts_verified"] == 0
    assert not r["operator_approval_granted"]


# P8-24: stage handover, tests, code versions and no promotion.

def test_p824_handover_has_six_version_pinned_modules():
    r = assess()
    inventory = r["evidence_handover"]["code_inventory"]
    assert len(inventory) == 6
    assert all(len(x["local_code_sha256"]) == 64 for x in inventory)
    assert all(not x["independently_reviewed"] for x in inventory)
    assert r["evidence_handover"]["handover_sha256"]


def test_p824_frozen_sample_decision_packet_has_terminal_hold(tmp_path):
    r = checked(tmp_path)
    samples = r["evidence_handover"]["sample_decision_packets"]
    assert len(samples) == 1
    assert samples[0]["terminal_research_state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD"
    assert samples[0]["witness_receipt_present"] is False
    assert samples[0]["last_local_evidence_event_sha256"]


def test_p824_handover_is_unapproved_even_when_replay_complete(tmp_path):
    r = checked(tmp_path)
    handover = r["evidence_handover"]
    assert handover["exit_mode"] == "OFFLINE_SYNTHETIC_ENGINEERING_ONLY_UNAPPROVED_SHADOW"
    assert handover["shadow_approved"] is False
    assert handover["independent_external_handover_accepted"] is False
    assert handover["engineering_handover_written"] is False
    assert handover["final_action"] == "HOLD"


def test_p824_unverified_test_claim_is_not_independent_acceptance(tmp_path):
    path, doc = make_test_claim(tmp_path)
    r = assess(tests=path)
    claim = r["evidence_handover"]["test_artifact_claim"]
    assert claim["claimed_collected"] == 477
    assert claim["claimed_passed"] == 471
    assert claim["claimed_skipped"] == 6
    assert claim["independent_test_custody"] is False
    assert r["release_gate"]["dry_run_release_decision"] == "BLOCKED_NO_SHADOW"


def test_p824_test_claim_uses_only_stage8_allowlist(tmp_path):
    path, doc = make_test_claim(tmp_path)
    doc["selected_test_files"].append("backend/runtime/test_stage7_evidence_board.py")
    save(path, doc)
    with pytest.raises(RecoveryHandoverError, match="Only known Stage 8"):
        assess(tests=path)


@pytest.mark.parametrize("changed", [
    {"mode": "INDEPENDENTLY_VERIFIED"},
    {"suite": "ALL_STAGES"},
    {"historical_epoch_id": "f" * 64},
    {"collected": 1},
    {"passed": 99999},
    {"collected": True},
    {"selected_test_files": []},
    {"result_digest": "bad"},
])
def test_p824_invalid_test_claim_refused(tmp_path, changed):
    path, doc = make_test_claim(tmp_path)
    doc.update(changed)
    save(path, doc)
    with pytest.raises(RecoveryHandoverError):
        assess(tests=path)


def test_p824_test_claim_from_future_refused(tmp_path):
    path, doc = make_test_claim(tmp_path)
    doc["run_at"] = "2030-01-01T00:00:00Z"
    save(path, doc)
    with pytest.raises(RecoveryHandoverError):
        assess(tests=path)


def test_p824_handover_hash_reproducible(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    a = assess(p, m, tmp_path)
    b = assess(p, m, tmp_path)
    assert a == b
    assert a["report_sha256"] == _hash({k: v for k, v in a.items() if k != "report_sha256"})
    assert a["evidence_handover"]["handover_sha256"] == _hash({
        k: v for k, v in a["evidence_handover"].items()
        if k not in ("handover_sha256", "engineering_handover_written", "independent_external_handover_accepted")
    })


def test_p824_frozen_replay_file_tamper_refused(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    report = json.loads(p.read_text("utf-8"))
    report["cycles"][0]["state"] = "REGISTERED"
    report["report_sha256"] = _hash({k: v for k, v in report.items() if k != "report_sha256"})
    save(p, report)
    with pytest.raises(RecoveryHandoverError, match="changed when recomputed"):
        assess(p, m, tmp_path)


def test_p824_frozen_report_manifest_without_root_refused(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    with pytest.raises(RecoveryHandoverError, match="must be paired"):
        assess(p, m)


def test_p824_replay_module_execution_no_file_mutation(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    files = [p, m, tmp_path / "synthetic_council_report.json"]
    before = [sha256(x.read_bytes()).hexdigest() for x in files]
    _ = assess(p, m, tmp_path)
    assert [sha256(x.read_bytes()).hexdigest() for x in files] == before


def test_p824_no_execution_or_trading_side_effect_flags(tmp_path):
    r = checked(tmp_path)
    for flag in (
        "files_written", "network_accessed", "broker_connected", "collector_started",
        "council_invoked", "model_loaded", "training_authorized", "settlement_invoked",
        "paper_orders_authorized", "live_orders_authorized", "operator_approval_granted",
        "historical_epoch_rebound", "future_epoch_created",
    ):
        assert r[flag] is False
    assert r["predictions_registered"] == 0
    assert r["council_seats_dispatched"] == 0
    assert r["final_action"] == "HOLD"


def test_p824_no_raw_price_or_file_paths_in_handover(tmp_path):
    text = json.dumps(checked(tmp_path))
    assert '"1.0840"' not in text
    assert "SYNTHETIC_HEARTBEAT/candles/m5.json" not in text
    assert "UNVERIFIED_MARKET_CONTEXT/payload/" not in text
    assert '"password"' not in text


# Global strict boundaries and CLI no-evidence report.

def test_p824_original_historical_clock_policy_unchanged(tmp_path):
    old = sha256(POLICY.read_bytes()).hexdigest()
    _ = checked(tmp_path)
    assert sha256(POLICY.read_bytes()).hexdigest() == old == POLICY_SHA256


def test_p824_wrong_historical_epoch_cannot_rebind():
    with pytest.raises(RecoveryHandoverError, match="rebinding refused"):
        assess_stage8_recovery_handover(epoch_id="f" * 64, as_of=ASOF, policy_file=POLICY)


def test_p824_tampered_october_27_policy_rejected(tmp_path):
    p = tmp_path / "tampered_policy.json"
    p.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(RecoveryHandoverError, match="historical policy SHA-256 mismatch"):
        assess(policy=p)


@pytest.mark.parametrize("bad", [
    "2026-10-30T11:00:00", "2026-10-30T14:00:00+03:00",
    "2026-10-30T11:00:60Z", "2026-11-31T11:00:00Z", "bad",
])
def test_p824_utc_required(bad):
    with pytest.raises(RecoveryHandoverError):
        _time(bad)


def test_p824_future_cutoff_rejected():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(RecoveryHandoverError, match="Future review cutoff"):
        assess(cutoff=future)


def test_p824_cli_no_evidence_prints_hold(capsys):
    code = run([
        "--read-only-recovery-handover", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", ASOF, "--policy-file", str(POLICY),
        "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "Cases observed: 0" in out
    assert "BLOCKED_NO_SHADOW" in out
    assert "FINAL=HOLD" in out


def test_p824_cli_json_with_frozen_replay_remains_hold(tmp_path, capsys):
    m, d, p, frozen = prepare(tmp_path)
    assert run([
        "--read-only-recovery-handover", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", ASOF, "--policy-file", str(POLICY),
        "--replay-report", str(p), "--fixture-manifest", str(m),
        "--evidence-root", str(tmp_path),
        "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST),
        "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["observability"]["cases_observed"] == 1
    assert report["replay_source"]["locally_reproduced"] is True
    assert report["final_action"] == "HOLD"


def test_p824_cli_requires_safety_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", ASOF])
    assert exc.value.code == 2


def test_p824_cli_future_cutoff_exits_blocked(capsys):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert run([
        "--read-only-recovery-handover", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", future, "--policy-file", str(POLICY),
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p824_duplicate_json_key_refused(tmp_path):
    gate, doc = make_gate(tmp_path)
    gate.write_text('{"mode":"LOCAL_UNATTESTED_RELEASE_CLAIMS","mode":"APPROVED"}', encoding="utf-8")
    with pytest.raises(RecoveryHandoverError):
        assess(gate=gate)


def test_p824_symlink_gate_file_refused_if_supported(tmp_path):
    gate, doc = make_gate(tmp_path)
    alias = tmp_path / "linked_gate.json"
    try:
        alias.symlink_to(gate)
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlink creation not permitted")
    with pytest.raises(RecoveryHandoverError, match="Unsafe"):
        assess(gate=alias)


def test_p824_recovery_journal_over_size_refused(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path = tmp_path / "oversized_journal.json"
    path.write_bytes(b"X" * 131073)
    with pytest.raises(RecoveryHandoverError, match="Unsafe"):
        assess(p, m, tmp_path, journal=path)


def test_p824_successful_tests_are_not_approval(tmp_path):
    m, d, p, frozen = prepare(tmp_path, forecast=True)
    test_path, test_doc = make_test_claim(tmp_path)
    gate_path, gate_doc = make_gate(tmp_path, fully_claimed=True)
    journal_path, journal_doc = make_journal(tmp_path, frozen, [
        "STARTED", "CONTEXT_COMMITTED", "COUNCIL_COMMITTED",
        "MEMORY_COMMITTED", "HELD_TERMINAL",
    ])
    r = assess(p, m, tmp_path, journal=journal_path,
               gate=gate_path, tests=test_path)
    assert r["recovery"]["terminal_held_count"] == 1
    assert r["release_gate"]["local_all_checklist_claims_present"]
    assert r["release_gate"]["dry_run_release_decision"] == "BLOCKED_NO_SHADOW"
    assert r["evidence_handover"]["shadow_approved"] is False
    assert r["predictions_registered"] == 0
    assert r["final_action"] == "HOLD"


def test_p821_rehashed_stage_claim_without_frozen_context_is_rejected(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    heartbeat = tmp_path / "heartbeat_index.json"
    hb = json.loads(heartbeat.read_text("utf-8"))
    hb["envelopes"][0]["received_at"] = "2026-10-30T10:03:00Z"
    save(heartbeat, hb)
    frozen = re_frozen(tmp_path)
    assert frozen["cycles"][0]["completed_frozen_stages"] == 0
    path, doc = make_journal(tmp_path, frozen, ["STARTED", "CONTEXT_COMMITTED"])
    with pytest.raises(RecoveryHandoverError, match="stage absent"):
        assess(p, m, tmp_path, journal=path)


def test_p821_duplicate_case_cannot_claim_second_journal_owner(tmp_path):
    m, doc, p, frozen = prepare(tmp_path)
    duplicated = deepcopy(doc["cycles"][0])
    duplicated["case_id"] = "CASE_TWO"
    doc["cycles"].append(duplicated)
    save(m, doc)
    frozen = re_frozen(tmp_path)
    assert frozen["duplicate_decisions_suppressed"] == 1
    path, journal = make_journal(tmp_path, frozen, ["STARTED"])
    journal["entries"][0]["case_id"] = "CASE_TWO"
    rechain(journal)
    save(path, journal)
    with pytest.raises(RecoveryHandoverError, match="canonical case mismatch"):
        assess(p, m, tmp_path, journal=path)


def test_p821_rehashed_backdated_journal_event_cannot_recover(tmp_path):
    m, d, p, frozen = prepare(tmp_path)
    path, journal = make_journal(tmp_path, frozen, ["STARTED", "CRASH_MARK"])
    journal["entries"][1]["effective_at"] = "2026-10-30T10:00:19Z"
    rechain(journal)
    save(path, journal)
    with pytest.raises(RecoveryHandoverError, match="outside causal review"):
        assess(p, m, tmp_path, journal=path)


def test_p824_report_has_frozen_version_contracts_and_never_approves(tmp_path):
    r = checked(tmp_path)
    assert r["version"] == VERSION
    assert r["recovery"]["contract_version"] == RECOVERY_VERSION
    assert r["observability"]["contract_version"] == OBSERVABILITY_VERSION
    assert r["release_gate"]["contract_version"] == RELEASE_VERSION
    assert r["evidence_handover"]["contract_version"] == HANDOVER_VERSION
    assert r["research_operational_readiness"] == "BLOCKED_EXTERNAL_EVIDENCE"
    assert r["independent_external_gates_verified"] == 0
    assert r["independent_source_families_verified"] == 0
    assert r["final_action"] == "HOLD"
