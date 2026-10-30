
"""P8-13..16 offline Council explanations, ancestry, proposal and journal safety.

Temporary synthetic fixtures only; consumes prior Stage 8 test fixtures and
never calls agents, real memory storage, model inference, broker or collectors.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage8_offline_council_sandbox import (
    assess_stage8_council, _digest,
)
from backend.runtime.test_stage8_offline_council_sandbox import (
    files as synthetic_files, save, POLICY, CONTRACTS, MANIFEST,
    ASOF, MOCK_SHA,
)
from backend.runtime.stage8_explainable_research_memory import (
    EPOCH, POLICY_SHA256, VERSION, EXPLANATION_VERSION,
    ZERO_SHA, SUPPORTED_HORIZONS, EVENT_KINDS,
    ResearchMemoryError, _time, assess_stage8_explainable_memory, run,
)

DECISION_KEY = None


def setup(tmp_path, *, full=True):
    ctx, context_doc, schedule, schedule_doc = synthetic_files(tmp_path, full=full)
    council = tmp_path / "synthetic_council_report.json"
    recompute(ctx, schedule, council)
    return ctx, context_doc, schedule, schedule_doc, council


def recompute(context, schedule, path):
    value = assess_stage8_council(
        epoch_id=EPOCH, as_of=ASOF, policy_file=POLICY,
        context_report=context, seat_schedule=schedule,
        contracts_file=CONTRACTS, manifest_file=MANIFEST,
    )
    save(path, value)
    return value


def assess(council=None, context=None, schedule=None, *, forecast=None,
           journal=None, asof=ASOF, policy=POLICY, manifest=MANIFEST,
           contracts=CONTRACTS):
    return assess_stage8_explainable_memory(
        epoch_id=EPOCH, as_of=asof, policy_file=policy,
        council_report=council, context_report=context, seat_schedule=schedule,
        forecast_proposal=forecast, prior_journal=journal,
        contracts_file=contracts, manifest_file=manifest,
    )


def checked(tmp_path, *, full=True):
    context, context_doc, schedule, schedule_doc, council = setup(tmp_path, full=full)
    return assess(council, context, schedule)


def proposal(tmp_path, council, *, direction="BUY", horizon=3600,
             proposed_at=ASOF, probability="0.750", model=MOCK_SHA):
    doc = json.loads(council.read_text("utf-8"))
    payload = {
        "schema_version": 1,
        "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_FORECAST_PROPOSAL",
        "mode": "SYNTHETIC_OFFLINE_UNREGISTERED",
        "decision_key": doc["context"]["decision_key"],
        "context_sha256": doc["context"]["context_sha256"],
        "council_report_sha256": doc["report_sha256"],
        "proposed_at": proposed_at,
        "horizon_seconds": horizon,
        "horizon_definition": "DECISION_AT_PLUS_SECONDS",
        "direction": direction,
        "probability_up": probability,
        "model_context_sha256": model,
    }
    path = tmp_path / "synthetic_unregistered_forecast.json"
    save(path, payload)
    return path, payload


def journal_index(tmp_path, result, prefix=None):
    events = result["decision_journal"]["proposed_append_events"]
    if prefix is not None:
        events = events[:prefix]
    doc = {
        "schema_version": 1,
        "historical_epoch_id": EPOCH,
        "packet_id": "UNATTESTED_LOCAL_JOURNAL_PREFIX",
        "mode": "LOCAL_UNATTESTED_APPEND_ONLY_CLAIM",
        "as_of": ASOF,
        "decision_key": result["council_input"]["status"] != "NOT_SUPPLIED" and result["explanation"]["decision_key"],
        "context_sha256": result["council_input"]["context_sha256"],
        "council_report_sha256": result["council_input"]["council_report_sha256"],
        "events": deepcopy(events),
    }
    path = tmp_path / "local_unattested_journal.json"
    save(path, doc)
    return path, doc


def edit(path, doc, **changes):
    doc.update(changes)
    save(path, doc)


def edit_seat(schedule, data, council, ctx, number, **changes):
    data["seat_results"][number - 1].update(changes)
    save(schedule, data)
    recompute(ctx, schedule, council)


def state(result):
    return result["explanation"]["consensus"]


# P8-13: exact seat explanations and risk provenance.

def test_p813_empty_input_has_no_invented_seat_explanations():
    r = assess()
    assert r["explanation"]["status"] == "NOT_SUPPLIED"
    assert r["explanation"]["contributing_seats"] == []
    assert r["explanation"]["contrary_seats"] == []
    assert r["explanation"]["explanation_sha256"] is None
    assert r["final_action"] == "HOLD"


def test_p813_replay_full_prior_council_before_explaining(tmp_path):
    r = checked(tmp_path)
    assert r["council_input"]["status"] == "REPLAYED_LOCAL_SYNTHETIC_UNATTESTED"
    assert r["council_input"]["council_replay_matches_supplied"] is True
    assert r["explanation"]["status"] == "LOCAL_SYNTHETIC_EXPLANATION_UNATTESTED"
    assert len(r["explanation"]["explanation_sha256"]) == 64
    assert r["council_seats_executed"] == 0


def test_p813_contributing_seats_have_cited_original_source_record_ids(tmp_path):
    r = checked(tmp_path)
    contributors = r["explanation"]["contributing_seats"]
    assert len(contributors) == 28
    assert contributors[0]["seat_id"] == "trend_master"
    assert "candles_01" in contributors[0]["source_ancestry_record_ids"]
    assert contributors[0]["direction"] == "BUY"
    assert contributors[0]["confidence_claim"] == "0.700"
    assert contributors[0]["reason_code"] == "SYNTHETIC_REASON"


def test_p813_held_and_blocked_seats_not_mislabeled_contributors(tmp_path):
    r = checked(tmp_path, full=False)
    assert r["explanation"]["contributing_seats"] == [] or len(r["explanation"]["blocked_seats"]) > 0
    assert r["final_action"] == "HOLD"


def test_p813_risk_and_reliability_vintages_reported(tmp_path):
    r = checked(tmp_path)
    governance = r["explanation"]["governance"]
    assert governance["risk_limits_vintage_id"] == "v_risk_limits_01"
    assert governance["risk_state_vintage_id"] == "v_risk_state_01"
    assert governance["reliability_vintage_id"] == "v_reliability_01"
    assert governance["council_ruleset_version"].startswith("SYNTHETIC")
    assert governance["risk_rules_independently_approved"] is False


def test_p813_chief_reason_code_is_structured_not_free_text(tmp_path):
    r = checked(tmp_path)
    assert r["explanation"]["governance"]["chief_strategist"]["reason_code"] == "SYNTHETIC_STRATEGY"
    assert r["explanation"]["governance"]["risk_officer"]["reason_code"] == "SYNTHETIC_RISK_CHECK"


def test_p813_risk_veto_remains_visible_and_proposal_held(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    doc["seat_results"][29]["payload"]["veto"] = True
    save(path, doc)
    recompute(ctx, path, council)
    r = assess(council, ctx, path)
    assert r["explanation"]["governance"]["risk_veto"] is True
    assert "RISK_OFFICER_VETO_FORCES_HOLD" in r["explanation"]["reason_codes"]
    assert r["explanation"]["governance"]["synthetic_proposed_direction"] == "HOLD"


def test_p813_contrary_seat_remains_visible(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    doc["seat_results"][0]["payload"]["direction"] = "SELL"
    save(path, doc)
    recompute(ctx, path, council)
    r = assess(council, ctx, path)
    assert len(r["explanation"]["contrary_seats"]) == 1
    assert r["explanation"]["contrary_seats"][0]["seat_id"] == "trend_master"
    assert state(r)["primary_buy_votes"] == 27
    assert state(r)["primary_sell_votes"] == 1


def test_p813_missing_seat_is_auditable_not_implicit_abstain(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    doc["seat_results"].pop(1)
    save(path, doc)
    recompute(ctx, path, council)
    r = assess(council, ctx, path)
    assert any(x["seat_id"] == "price_action" and x["state"] == "NOT_SUBMITTED"
               for x in r["explanation"]["blocked_seats"])
    assert "MISSING_SEATS_ARE_NOT_IMPLICIT_ABSTENTIONS" in r["explanation"]["reason_codes"]


def test_p813_context_file_changed_without_recomputed_council_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    context_json = json.loads(ctx.read_text("utf-8"))
    context_json["context"]["status"] = "NOT_SUPPLIED"
    save(ctx, context_json)
    with pytest.raises(ResearchMemoryError):
        assess(council, ctx, path)


def test_p813_council_report_rehashed_with_altered_veto_still_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    report = json.loads(council.read_text("utf-8"))
    report["rehearsal"]["declared_risk_veto"] = True
    report["report_sha256"] = _digest({k: v for k, v in report.items() if k != "report_sha256"})
    save(council, report)
    with pytest.raises(ResearchMemoryError, match="changed on causal replay"):
        assess(council, ctx, path)


def test_p813_changing_schedule_without_recomputed_council_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    doc["seat_results"][0]["payload"]["direction"] = "SELL"
    save(path, doc)
    with pytest.raises(ResearchMemoryError):
        assess(council, ctx, path)


def test_p813_partial_bundle_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    with pytest.raises(ResearchMemoryError, match="supplied together"):
        assess(council, ctx)


def test_p813_no_forged_independent_evidence_claim_from_local_replay(tmp_path):
    r = checked(tmp_path)
    assert r["independent_external_gates_verified"] == 0
    assert r["independent_source_families_verified"] == 0
    assert r["council_input"]["independently_attested"] is False
    assert r["prediction_registered"] is False


# P8-14: agreement and structural diversity, not invented error correlations.

def test_p814_full_synthetic_votes_are_not_28_independent_evidence_sources(tmp_path):
    r = checked(tmp_path)
    c = state(r)
    assert c["primary_directional_votes"] == 28
    assert c["primary_buy_votes"] == 28
    assert 1 <= c["structural_effective_diversity_upper_bound"] < 28
    assert c["observed_error_correlation_measured"] is False
    assert c["independent_evidence_or_vote_confirmed"] is False


def test_p814_shared_candles_group_primary_seats(tmp_path):
    r = checked(tmp_path)
    group = next(g for g in state(r)["source_overlap_clusters"] if 1 in g["seat_numbers"])
    assert 2 in group["seat_numbers"]
    assert "candles_01" in group["shared_source_ancestry_record_ids"]


def test_p814_clusters_have_no_reused_or_missing_directional_seat(tmp_path):
    r = checked(tmp_path)
    clusters = state(r)["source_overlap_clusters"]
    seatnums = [n for x in clusters for n in x["seat_numbers"]]
    assert sorted(seatnums) == list(range(1, 29))
    assert all(g["structural_overlap_only"] for g in clusters)


def test_p814_declared_confidence_has_no_calibration_claim(tmp_path):
    r = checked(tmp_path)
    assert state(r)["mean_declared_confidence"] == "0.700"
    assert state(r)["declared_confidence_not_calibrated"] is True


def test_p814_declared_confidence_change_changes_explanation_sha(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    initial = assess(council, ctx, path)
    doc["seat_results"][0]["payload"]["confidence"] = "0.900"
    save(path, doc)
    recompute(ctx, path, council)
    revised = assess(council, ctx, path)
    assert initial["explanation"]["explanation_sha256"] != revised["explanation"]["explanation_sha256"]
    assert revised["explanation"]["consensus"]["mean_declared_confidence"] != "0.700"


def test_p814_divergent_direction_remains_a_structural_cluster(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    doc["seat_results"][1]["payload"]["direction"] = "SELL"
    save(path, doc)
    recompute(ctx, path, council)
    r = assess(council, ctx, path)
    shared = next(g for g in state(r)["source_overlap_clusters"] if 1 in g["seat_numbers"])
    assert shared["cluster_direction"] == "MIXED"
    assert state(r)["unanimous_direction_clusters"] < state(r)["structural_effective_diversity_upper_bound"]


def test_p814_explicit_abstention_not_counted_as_directional(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    doc["seat_results"][0]["outcome"] = "ABSTAIN"
    doc["seat_results"][0]["payload"] = {
        "direction": "ABSTAIN", "confidence": "0.000",
        "reason_code": "NO_SIGNAL", "cited_labels": [],
    }
    save(path, doc)
    recompute(ctx, path, council)
    r = assess(council, ctx, path)
    assert state(r)["primary_directional_votes"] == 27
    assert len(r["explanation"]["blocked_seats"]) >= 1


def test_p814_ancestry_and_council_manifest_hash_bound(tmp_path):
    r = checked(tmp_path)
    assert len(r["explanation"]["source_ancestry_record_ids"]) > 0
    assert len(r["explanation"]["council_manifest_sha256"]) == 64
    assert r["explanation"]["version"] == EXPLANATION_VERSION


# P8-15: deterministic synthetic prediction ID, pre-target and no registration.

def test_p815_no_forecast_does_not_invent_prediction_id(tmp_path):
    r = checked(tmp_path)
    assert r["forecast_envelope"]["prediction_id"] is None
    assert r["forecast_envelope"]["registration_status"] == "NO_FORECAST_REGISTERED"
    assert r["prediction_registered"] is False


def test_p815_synthetic_proposal_id_is_deterministic_but_not_receipt(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council)
    a = assess(council, ctx, path, forecast=fp)
    b = assess(council, ctx, path, forecast=fp)
    assert a == b
    f = a["forecast_envelope"]
    assert len(f["prediction_id"]) == 64
    assert f["prediction_id_is_real_registration"] is False
    assert f["registration_status"] == "UNREGISTERED_NO_INDEPENDENT_PREDECISION_RECEIPT"
    assert f["independent_predecision_receipt"] is False
    assert f["forecast_outcome_registered"] is False
    assert a["final_action"] == "HOLD"


def test_p815_target_from_decision_time_not_assessment_time(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council)
    r = assess(council, ctx, path, forecast=fp)
    assert r["forecast_envelope"]["target_at"] == "2026-10-30T11:00:20+00:00"
    assert r["forecast_envelope"]["horizon_seconds"] == 3600


@pytest.mark.parametrize("horizon", [300, 900])
def test_p815_forecast_after_target_refused_even_when_offline(tmp_path, horizon):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council, horizon=horizon)
    with pytest.raises(ResearchMemoryError, match="pre-target chronology"):
        assess(council, ctx, path, forecast=fp)


@pytest.mark.parametrize("bad", [0, -300, 301, 999999, "3600", True])
def test_p815_unrecognized_horizon_refused(tmp_path, bad):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, data = proposal(tmp_path, council)
    edit(fp, data, horizon_seconds=bad)
    with pytest.raises(ResearchMemoryError, match="horizon"):
        assess(council, ctx, path, forecast=fp)


@pytest.mark.parametrize("bad", ["1.001", "-0.1", 0.7, "nan", "0.5e0", "abc", "0.1234"])
def test_p815_invalid_probability_type_or_bounds_refused(tmp_path, bad):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, data = proposal(tmp_path, council)
    edit(fp, data, probability_up=bad)
    with pytest.raises(ResearchMemoryError, match="probability"):
        assess(council, ctx, path, forecast=fp)


def test_p815_direction_probability_contradiction_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, data = proposal(tmp_path, council)
    edit(fp, data, probability_up="0.400")
    with pytest.raises(ResearchMemoryError, match="contradicts"):
        assess(council, ctx, path, forecast=fp)


def test_p815_forecast_direction_must_equal_council_proposal(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, data = proposal(tmp_path, council)
    edit(fp, data, direction="SELL", probability_up="0.250")
    with pytest.raises(ResearchMemoryError, match="override"):
        assess(council, ctx, path, forecast=fp)


@pytest.mark.parametrize("changed", [
    {"context_sha256": "f" * 64},
    {"decision_key": "f" * 64},
    {"council_report_sha256": "f" * 64},
    {"historical_epoch_id": "f" * 64},
    {"mode": "LIVE_REGISTERED"},
    {"horizon_definition": "OUTCOME_FIRST"},
])
def test_p815_wrong_forecast_binding_or_mode_refused(tmp_path, changed):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, data = proposal(tmp_path, council)
    edit(fp, data, **changed)
    with pytest.raises(ResearchMemoryError, match="scope mismatch"):
        assess(council, ctx, path, forecast=fp)


def test_p815_model_mock_changed_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, data = proposal(tmp_path, council)
    edit(fp, data, model_context_sha256="f" * 64)
    with pytest.raises(ResearchMemoryError, match="ancestry"):
        assess(council, ctx, path, forecast=fp)


def test_p815_forged_receipt_or_registration_field_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, data = proposal(tmp_path, council)
    edit(fp, data, independently_registered=True)
    with pytest.raises(ResearchMemoryError, match="Strict schema"):
        assess(council, ctx, path, forecast=fp)


def test_p815_local_hold_candidate_never_directional_prediction(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    doc["seat_results"][29]["payload"]["veto"] = True
    save(path, doc)
    recompute(ctx, path, council)
    fp, data = proposal(tmp_path, council, direction="HOLD", probability=None)
    r = assess(council, ctx, path, forecast=fp)
    assert r["forecast_envelope"]["direction"] == "HOLD"
    assert r["forecast_envelope"]["probability_up"] is None
    assert not r["prediction_registered"]


def test_p815_no_directional_probability_for_hold(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    doc["seat_results"][29]["payload"]["veto"] = True
    save(path, doc)
    recompute(ctx, path, council)
    fp, _ = proposal(tmp_path, council, direction="HOLD", probability="0.700")
    with pytest.raises(ResearchMemoryError, match="HOLD cannot"):
        assess(council, ctx, path, forecast=fp)


def test_p815_forecast_claim_predates_council_replay_cutoff_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council, proposed_at="2026-10-30T10:05:00Z")
    with pytest.raises(ResearchMemoryError, match="pre-target chronology"):
        assess(council, ctx, path, forecast=fp)


def test_p815_forecast_with_no_bundle_refused(tmp_path):
    path = tmp_path / "untrusted_forecast.json"
    save(path, {})
    with pytest.raises(ResearchMemoryError, match="fully replayed"):
        assess(forecast=path)


# P8-16: no-write journal preview with verified local hash-chain prefix.

def test_p816_complete_rehearsal_previews_three_unpersisted_events(tmp_path):
    r = checked(tmp_path)
    j = r["decision_journal"]
    assert [x["event_type"] for x in j["proposed_append_events"]] == list(EVENT_KINDS[:3])
    assert j["existing_entries"] == 0
    assert j["journal_persisted"] is False
    assert j["events_written"] == 0
    assert j["independent_append_only_custody_verified"] is False


def test_p816_forecast_adds_fourth_held_proposal_event_not_registration(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council)
    r = assess(council, ctx, path, forecast=fp)
    events = r["decision_journal"]["proposed_append_events"]
    assert len(events) == 4
    assert events[-1]["event_type"] == "FORECAST_PROPOSAL_HELD"
    assert all(x["terminal_state"] == "HELD" for x in events)


def test_p816_proposal_journal_event_uses_proposal_time_not_earlier_council_time(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    # Four-hour horizon makes a later, still-pre-target, synthetic proposal possible.
    fp, _ = proposal(tmp_path, council, horizon=14400,
                     proposed_at="2026-10-30T11:30:00Z")
    r = assess(council, ctx, path, forecast=fp, asof="2026-10-30T12:00:00Z")
    events = r["decision_journal"]["proposed_append_events"]
    assert events[2]["effective_at"] == "2026-10-30T11:00:00+00:00"
    assert events[3]["effective_at"] == "2026-10-30T11:30:00+00:00"
    assert events[3]["previous_sha256"] == events[2]["event_sha256"]


def test_p816_hash_chain_integrity_proposed_entries(tmp_path):
    r = checked(tmp_path)
    prev = ZERO_SHA
    for i, event in enumerate(r["decision_journal"]["proposed_append_events"], 1):
        assert event["sequence"] == i
        assert event["previous_sha256"] == prev
        assert event["event_sha256"] == _digest({k: v for k, v in event.items() if k != "event_sha256"})
        prev = event["event_sha256"]


def test_p816_exact_local_prior_prefix_suppresses_duplicate_preview(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    initial = assess(council, ctx, path)
    prior, _ = journal_index(tmp_path, initial)
    replay = assess(council, ctx, path, journal=prior)
    assert replay["decision_journal"]["existing_entries"] == 3
    assert replay["decision_journal"]["proposed_append_events"] == []
    assert replay["decision_journal"]["prefix_locally_verified"] is True
    assert replay["decision_journal"]["independent_append_only_custody_verified"] is False


def test_p816_three_event_prior_and_new_forecast_appends_one(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    initial = assess(council, ctx, path)
    prior, _ = journal_index(tmp_path, initial)
    fp, _ = proposal(tmp_path, council)
    result = assess(council, ctx, path, forecast=fp, journal=prior)
    append = result["decision_journal"]["proposed_append_events"]
    assert len(append) == 1
    assert append[0]["event_type"] == "FORECAST_PROPOSAL_HELD"
    assert append[0]["sequence"] == 4


def test_p816_full_prior_with_proposal_no_duplicate(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council)
    result = assess(council, ctx, path, forecast=fp)
    prior, _ = journal_index(tmp_path, result)
    again = assess(council, ctx, path, forecast=fp, journal=prior)
    assert again["decision_journal"]["existing_entries"] == 4
    assert again["decision_journal"]["proposed_append_events"] == []


def test_p816_changed_forecast_after_locally_held_proposal_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, data = proposal(tmp_path, council)
    before = assess(council, ctx, path, forecast=fp)
    prior, _ = journal_index(tmp_path, before)
    edit(fp, data, probability_up="0.850")
    with pytest.raises(ResearchMemoryError, match="edit/rewrite"):
        assess(council, ctx, path, forecast=fp, journal=prior)


@pytest.mark.parametrize("field,bad", [
    ("event_type", "REGISTERED"),
    ("sequence", 2),
    ("previous_sha256", "f" * 64),
    ("payload_sha256", "f" * 64),
    ("terminal_state", "REGISTERED"),
    ("event_sha256", "f" * 64),
])
def test_p816_prior_event_changed_or_reordered_refused(tmp_path, field, bad):
    ctx, _, path, doc, council = setup(tmp_path)
    first = assess(council, ctx, path)
    prior, prev = journal_index(tmp_path, first)
    prev["events"][0][field] = bad
    save(prior, prev)
    with pytest.raises(ResearchMemoryError):
        assess(council, ctx, path, journal=prior)


def test_p816_unauthorized_extra_prior_event_rejected(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    first = assess(council, ctx, path)
    prior, prev = journal_index(tmp_path, first)
    prev["events"].append(deepcopy(prev["events"][-1]))
    save(prior, prev)
    with pytest.raises(ResearchMemoryError):
        assess(council, ctx, path, journal=prior)


def test_p816_journal_index_without_council_refused(tmp_path):
    path = tmp_path / "journal.json"
    save(path, {})
    with pytest.raises(ResearchMemoryError, match="requires"):
        assess(journal=path)


def test_p816_local_journal_wrong_context_binding_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    result = assess(council, ctx, path)
    prior, data = journal_index(tmp_path, result)
    edit(prior, data, context_sha256="f" * 64)
    with pytest.raises(ResearchMemoryError, match="binding mismatch"):
        assess(council, ctx, path, journal=prior)


def test_p816_later_outcomes_not_used_for_explanation(tmp_path):
    result = checked(tmp_path)
    assert result["decision_journal"]["later_outcome_or_label_used"] is False
    assert result["settlement_invoked"] is False
    assert result["prediction_registered"] is False


# Global invariants, strict UTC, privacy, deterministic CLI and safety.

def test_p816_report_deterministic_content_hash(tmp_path):
    r = checked(tmp_path)
    assert r["report_sha256"] == _digest({k: v for k, v in r.items() if k != "report_sha256"})
    assert r["report_sha256"] == checked_again(tmp_path, r["council_input"]["status"])


def checked_again(tmp_path, expected):
    # The same fixture was already created; assess the original frozen files.
    assert expected == "REPLAYED_LOCAL_SYNTHETIC_UNATTESTED"
    return assess(tmp_path / "synthetic_council_report.json",
                  tmp_path / "unverified_point_in_time_report.json",
                  tmp_path / "synthetic_only_seat_results.json")["report_sha256"]


def test_p816_no_raw_price_or_personal_data_in_report(tmp_path):
    report = checked(tmp_path)
    text = json.dumps(report)
    assert '"1.0840"' not in text
    assert '"SYNTHETIC_REASON"' in text
    assert "candles/m5.json" not in text
    assert "payload/candles_01.json" not in text


def test_p816_no_execution_or_write_flags_even_full_proposal(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council)
    r = assess(council, ctx, path, forecast=fp)
    for flag in (
        "files_written", "network_accessed", "broker_connected", "collector_started",
        "council_invoked", "model_loaded", "training_authorized", "settlement_invoked",
        "paper_orders_authorized", "live_orders_authorized", "historical_epoch_rebound",
        "future_epoch_created", "operator_approval_granted", "prediction_registered",
    ):
        assert r[flag] is False
    assert r["final_action"] == "HOLD"


def test_p816_wrong_epoch_rebinding_refused():
    with pytest.raises(ResearchMemoryError, match="rebinding refused"):
        assess_stage8_explainable_memory(epoch_id="f" * 64, as_of=ASOF, policy_file=POLICY)


def test_p816_historical_policy_pin_enforced(tmp_path):
    bad = tmp_path / "changed_policy.json"
    bad.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(ResearchMemoryError, match="historical policy SHA-256 mismatch"):
        assess(policy=bad)


def test_p816_original_policy_pin_unchanged(tmp_path):
    _ = checked(tmp_path)
    assert sha256(POLICY.read_bytes()).hexdigest() == POLICY_SHA256


@pytest.mark.parametrize("bad", [
    "2026-10-30T11:00:00", "2026-10-30T14:00:00+03:00",
    "2026-10-30T11:00:60Z", "2026-11-31T11:00:00Z", "bad",
])
def test_p816_strict_utc_required(bad):
    with pytest.raises(ResearchMemoryError):
        _time(bad)


def test_p816_future_review_cutoff_refused():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    with pytest.raises(ResearchMemoryError, match="Future assessment cutoff"):
        assess(asof=future)


def test_p816_no_bundle_cli_remains_hold(capsys):
    assert run([
        "--read-only-memory", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", ASOF, "--policy-file", str(POLICY),
        "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST),
    ]) == 0
    stdout = capsys.readouterr().out
    assert "FINAL=HOLD" in stdout
    assert "Council replay: NOT_SUPPLIED" in stdout
    assert "Forecasts registered: 0" in stdout


def test_p816_cli_json_with_full_fixture_still_hold(capsys, tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council)
    assert run([
        "--read-only-memory", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", ASOF, "--policy-file", str(POLICY),
        "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST),
        "--context-report", str(ctx), "--council-report", str(council),
        "--seat-schedule", str(path), "--forecast-proposal", str(fp), "--json",
    ]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["explanation"]["consensus"]["primary_directional_votes"] == 28
    assert r["forecast_envelope"]["prediction_id_is_real_registration"] is False
    assert r["final_action"] == "HOLD"


def test_p816_cli_requires_safety_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", ASOF])
    assert exc.value.code == 2


def test_p816_cli_future_review_refused(capsys):
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    assert run([
        "--read-only-memory", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", future, "--policy-file", str(POLICY),
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p816_duplicate_json_key_in_proposal_refused(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council)
    fp.write_text('{"mode":"SYNTHETIC_OFFLINE_UNREGISTERED","mode":"LIVE"}', encoding="utf-8")
    with pytest.raises(ResearchMemoryError):
        assess(council, ctx, path, forecast=fp)


def test_p816_tamper_with_council_input_after_forecast_rejected(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    fp, _ = proposal(tmp_path, council)
    edit_seat(path, doc, council, ctx, 2, payload={**doc["seat_results"][1]["payload"], "confidence": "0.800"})
    with pytest.raises(ResearchMemoryError, match="scope mismatch"):
        assess(council, ctx, path, forecast=fp)


def test_p816_context_report_symlink_refused_if_supported(tmp_path):
    ctx, _, path, doc, council = setup(tmp_path)
    link = tmp_path / "context_alias.json"
    try:
        link.symlink_to(ctx)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink unavailable on Windows host")
    with pytest.raises(ResearchMemoryError, match="Unsafe"):
        assess(council, link, path)
