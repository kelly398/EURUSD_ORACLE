"""P10-01..08: synthetic 31-seat history, embargo and fail-closed design tests."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage6_council_context_readiness import inspect_static_council
from backend.runtime.stage8_m5_offline_heartbeat import _digest, _lineage_key
from backend.runtime.stage9_independent_challenge_decision import assess_stage9_challenge_decision
from backend.runtime.stage10_council_reliability_diversity_design import (
    EPOCH, POLICY_SHA256, PACKET_VERSION, RELIABILITY_VERSION,
    DIVERSITY_VERSION, MIN_SAMPLES, MIN_TIME_BLOCKS, MAX_CASES,
    Stage10DesignError, assess_stage10_council_reliability, run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
CONTRACTS = ROOT / "backend/agents/contracts.py"
MANIFEST = ROOT / "backend/agents/council_manifest.py"
ROSTER = inspect_static_council(contracts_file=CONTRACTS, manifest_file=MANIFEST)
UTC = timezone.utc


def z(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def review_at():
    return z(datetime.now(UTC).replace(microsecond=0))


def save(path, obj):
    path.write_text(json.dumps(obj, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def freeze(packet):
    for case in packet["cases"]:
        for seat in case["seat_claims"]:
            seat["seat_claim_sha256"] = _digest({k: v for k, v in seat.items()
                                                if k != "seat_claim_sha256"})
        case["case_claim_sha256"] = _digest({k: v for k, v in case.items()
                                            if k != "case_claim_sha256"})
    return packet


def seat_claim(spec, bar, num, *, family_shared=True):
    index = spec["seat"]
    source = "SHARED_RECORD" if family_shared and index <= 2 else "SEAT_RECORD_%02d" % index
    feature = "a" * 64 if family_shared and index <= 2 else "%064x" % index
    direction = "BUY" if index % 2 else "SELL"
    status = "VOTE"
    if index == 29:
        status, direction = "NO_CHALLENGE", "HOLD"
    elif index == 30:
        status, direction = "NO_VETO", "HOLD"
    elif index == 31:
        status, direction = "STRATEGY", "HOLD"
    return {
        "seat_number": index, "seat_id": spec["agent_id"], "stage": spec["stage"],
        "status": status, "direction": direction,
        "confidence": "0.7000" if index <= 28 else None,
        "input_first_available_at": z(bar + timedelta(seconds=2)),
        "opinion_created_at": z(bar + timedelta(seconds=10)),
        "source_families": list(spec["required_source_families"]),
        "source_record_ids": [source],
        "feature_lineage_sha256": feature,
        "model_family_id": "MODEL_SAME" if family_shared and index <= 2 else "MODEL_%02d" % index,
        "seat_claim_sha256": "0" * 64,
    }


def case_record(bar, *, outcome="BUY", available=True, event=None, horizon=3600,
                family_shared=True):
    decision = bar + timedelta(seconds=20)
    target = decision + timedelta(seconds=horizon)
    return {
        "decision_key": _lineage_key(bar), "bar_close_at": z(bar),
        "decision_at": z(decision), "event_id": event or "EVENT_" + bar.strftime("%Y%m%d"),
        "horizon_seconds": horizon, "target_at": z(target),
        "outcome_status": "MATURED_LOCAL_UNATTESTED" if available else "NOT_AVAILABLE",
        "outcome_direction": outcome if available else None,
        "outcome_available_at": z(target + timedelta(seconds=300)) if available else None,
        "regime": "TREND", "session": "LONDON",
        "regime_first_available_at": z(bar),
        "source_snapshot_sha256": "b" * 64,
        "seat_claims": [seat_claim(spec, bar, spec["seat"], family_shared=family_shared)
                        for spec in ROSTER["seats"]],
        "risk_veto_declared": False,
        "devils_advocate_challenge_declared": False,
        "strategist_proposal": "HOLD", "case_claim_sha256": "0" * 64,
    }


def setup(tmp_path, *, n=3, step_days=4, same_event=False, missing=(), shared=True):
    at = review_at()
    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    first = now - timedelta(days=220)
    cases = [case_record(first + timedelta(days=i * step_days),
                         event="SINGLE_EVENT" if same_event else None,
                         available=i not in missing, family_shared=shared)
             for i in range(n)]
    review = assess_stage9_challenge_decision(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
    )
    packet = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_SEAT_HISTORY_001",
        "contract_version": PACKET_VERSION, "mode": "SYNTHETIC_LOCAL_SEAT_HISTORY_ONLY",
        "stage9_review_report_sha256": review["report_sha256"],
        "council_manifest_sha256": ROSTER["manifest_sha256"],
        "council_contracts_sha256": ROSTER["contracts_sha256"],
        "declared_at": z(first - timedelta(days=5)),
        "embargo_seconds": 86400,
        "minimum_prior_samples": MIN_SAMPLES,
        "minimum_prior_time_blocks": MIN_TIME_BLOCKS,
        "max_prior_cases": 128,
        "decay_factor": "0.9700", "cases": cases,
    }
    freeze(packet)
    path = save(tmp_path / "local_synthetic_seat_history.json", packet)
    return path, packet, at


def assess(path=None, *, at=None, prior=None, policy=POLICY, epoch=EPOCH,
           contracts=CONTRACTS, manifest=MANIFEST):
    return assess_stage10_council_reliability(
        epoch_id=epoch, as_of=at or review_at(), policy_file=policy,
        history_file=path, prior_history=prior,
        contracts_file=contracts, manifest_file=manifest,
    )


def valid(tmp_path, **kwargs):
    path, packet, at = setup(tmp_path, **kwargs)
    return assess(path, at=at)


def tamper(tmp_path, action, *, rehash=True):
    path, packet, at = setup(tmp_path)
    action(packet)
    if rehash:
        freeze(packet)
    save(path, packet)
    with pytest.raises(Stage10DesignError):
        assess(path, at=at)


# Stage 9 boundary and static roster.

def test_p1001_no_history_keeps_stage9_no_evidence_and_hold():
    r = assess()
    assert r["stage9_research_decision"] == "NOT_ENOUGH_EVIDENCE"
    assert r["history_status"] == "NOT_SUPPLIED"
    assert r["historical_cases_examined"] == 0
    assert r["real_forecast_outcome_pairs_eligible_for_learning"] == 0
    assert r["final_action"] == "HOLD"


def test_p1001_valid_local_histories_never_become_trusted(tmp_path):
    r = valid(tmp_path)
    assert r["historical_cases_examined"] == 3
    assert r["independent_seat_attributions_accepted"] == 0
    assert r["trusted_prior_performance_records"] == 0
    assert r["nondefault_weights_promoted"] == 0
    assert r["stage9_independent_research_exit_criteria_met"] is False


def test_p1001_canonical_31_seats_are_retained_without_dispatch(tmp_path):
    r = valid(tmp_path)
    assert (r["static_primary_seats"], r["static_governance_seats"],
            r["static_synthesis_seats"]) == (28, 2, 1)
    assert r["council_invoked"] is False
    assert len(r["evaluation_snapshots"][0]["seat_reliability_preview"]) == 28


def test_p1001_locally_scored_outcomes_never_register_real_pairs(tmp_path):
    r = valid(tmp_path)
    assert r["real_forecast_outcome_pairs_eligible_for_learning"] == 0
    assert r["reliability_models_trained"] == 0
    assert r["consensus_probabilities_calibrated"] == 0


def test_p1001_outcome_can_be_missing_not_imputed(tmp_path):
    r = valid(tmp_path, missing=(1,))
    assert r["evaluation_snapshots"][2]["prior_history_count_after_causal_embargo"] == 1
    assert r["evaluation_snapshots"][1]["outcome_status"] == "NOT_AVAILABLE"


def test_p1001_first_sample_cannot_learn_its_own_outcome(tmp_path):
    r = valid(tmp_path)
    first = r["evaluation_snapshots"][0]
    assert first["prior_history_count_after_causal_embargo"] == 0
    assert first["seat_reliability_preview"][0]["directional_outcomes"] == 0
    assert first["seat_reliability_preview"][0]["raw_accuracy_local_only"] is None


def test_p1002_matured_prior_only_after_embargo(tmp_path):
    r = valid(tmp_path)
    assert [x["prior_history_count_after_causal_embargo"] for x in r["evaluation_snapshots"]] == [0, 1, 2]
    last = r["evaluation_snapshots"][-1]["seat_reliability_preview"][0]
    assert last["correct"] == 2
    assert last["distinct_utc_days"] == 2
    assert last["raw_accuracy_local_only"] == "1.0000"
    assert last["decayed_accuracy_local_only"] == "1.0000"
    assert last["trusted_sample_count"] == 0


def test_p1002_wrong_direction_and_ties_separate(tmp_path):
    path, packet, at = setup(tmp_path)
    packet["cases"][1]["outcome_direction"] = "SELL"
    packet["cases"][0]["outcome_direction"] = "TIE"
    freeze(packet)
    save(path, packet)
    result = assess(path, at=at)
    stats = result["evaluation_snapshots"][2]["seat_reliability_preview"][0]
    assert (stats["correct"], stats["wrong"], stats["ties_not_scored"]) == (0, 1, 1)
    assert stats["raw_accuracy_local_only"] == "0.0000"


def test_p1002_duplicate_events_count_once(tmp_path):
    r = valid(tmp_path, same_event=True)
    assert r["evaluation_snapshots"][2]["prior_history_count_after_causal_embargo"] == 1


def test_p1002_one_hour_lag_and_one_day_embargo_enforced(tmp_path):
    r = valid(tmp_path, step_days=1)
    assert [x["prior_history_count_after_causal_embargo"] for x in r["evaluation_snapshots"]] == [0, 0, 1]


def test_p1002_five_minute_history_cannot_learn_one_day_label(tmp_path):
    path, packet, at = setup(tmp_path)
    packet["cases"][0]["horizon_seconds"] = 86400
    packet["cases"][0]["target_at"] = z(read_time(packet["cases"][0]["decision_at"]) + timedelta(days=1))
    packet["cases"][0]["outcome_available_at"] = z(read_time(packet["cases"][0]["target_at"]) + timedelta(seconds=300))
    freeze(packet)
    save(path, packet)
    result = assess(path, at=at)
    assert result["evaluation_snapshots"][2]["prior_history_count_after_causal_embargo"] == 1


def test_p1002_many_local_samples_still_cannot_authorize_weights(tmp_path):
    r = valid(tmp_path, n=55, step_days=2)
    last = r["evaluation_snapshots"][-1]["seat_reliability_preview"][0]
    assert last["adequate_local_sample_count_only"] is True
    assert last["directional_outcomes"] == 54
    assert last["nondefault_influence_permitted"] is False
    assert r["nondefault_weights_promoted"] == 0


def test_p1003_session_regime_conditional_buckets_are_descriptive(tmp_path):
    r = valid(tmp_path)
    s = r["evaluation_snapshots"][-1]["seat_reliability_preview"][0]
    assert {x["bucket"] for x in s["conditional_buckets"]} == {"session:LONDON", "regime:TREND"}
    assert all(x["independent_subgroup_competence_established"] is False for x in s["conditional_buckets"])


def test_p1003_regime_cannot_be_reclassified_after_decision(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0].update(
        regime_first_available_at=z(read_time(p["cases"][0]["decision_at"]) + timedelta(seconds=1))))


def test_p1003_no_confidence_from_small_sample(tmp_path):
    r = valid(tmp_path)
    assert r["evaluation_snapshots"][-1]["seat_reliability_preview"][0]["adequate_local_sample_count_only"] is False
    assert all(x["real_probability_of_up"] is None for x in r["evaluation_snapshots"])


def test_p1004_holds_failures_counted_as_abstention(tmp_path):
    path, packet, at = setup(tmp_path)
    seat = packet["cases"][0]["seat_claims"][0]
    seat.update(status="UNAVAILABLE", direction="ABSTAIN", confidence=None,
                source_record_ids=[], model_family_id=None)
    freeze(packet)
    save(path, packet)
    r = assess(path, at=at)
    s = r["evaluation_snapshots"][2]["seat_reliability_preview"][0]
    assert s["unavailable"] == 1
    assert s["directional_outcomes"] == 1


def test_p1004_abstaining_primary_not_scored_directionally(tmp_path):
    path, packet, at = setup(tmp_path)
    seat = packet["cases"][0]["seat_claims"][0]
    seat.update(direction="ABSTAIN", confidence=None)
    freeze(packet)
    save(path, packet)
    r = assess(path, at=at)
    assert r["evaluation_snapshots"][2]["seat_reliability_preview"][0]["abstained"] == 1


def test_p1004_no_direct_influence_weight_application(tmp_path):
    r = valid(tmp_path)
    assert all(x["influence_weights_applied"] is False for x in r["evaluation_snapshots"])
    assert all(x["reliability_selection_authorized"] is False for x in r["evaluation_snapshots"])


# P10-05..08 shared ancestry, effective redundancy, governance walls.

def test_p1005_shared_ancestry_joins_first_two_primary_seats(tmp_path):
    graph = valid(tmp_path)["evaluation_snapshots"][0]["shared_ancestry"]
    assert graph["contract_version"] == DIVERSITY_VERSION
    assert graph["active_directional_primary_votes"] == 28
    assert [1, 2] in graph["connected_claim_components"]
    assert graph["local_redundancy_cluster_count_not_independent_sources"] == 27
    assert graph["independent_effective_source_count_verified"] == 0


def test_p1005_no_shared_ancestry_has_28_separate_claim_groups(tmp_path):
    graph = valid(tmp_path, shared=False)["evaluation_snapshots"][0]["shared_ancestry"]
    assert graph["overlapping_claim_pairs"] == 0
    assert graph["local_redundancy_cluster_count_not_independent_sources"] == 28
    assert graph["independent_effective_source_count_verified"] == 0


def test_p1006_diversity_preview_cannot_be_promoted(tmp_path):
    graph = valid(tmp_path)["evaluation_snapshots"][0]["shared_ancestry"]
    assert graph["diversity_adjusted_consensus_authorized"] is False
    assert graph["reliability_weighted_consensus_authorized"] is False
    assert graph["probability_calibration_performed"] is False
    assert graph["diversity_preview_weights_by_seat"]["1"] == "0.0185"


def test_p1006_equal_vote_preview_is_not_actual_decision(tmp_path):
    snapshot = valid(tmp_path)["evaluation_snapshots"][0]
    assert snapshot["equal_vote_preview_buy"] == 14
    assert snapshot["equal_vote_preview_sell"] == 14
    assert snapshot["equal_vote_preview_majority"] == "HOLD"
    assert snapshot["real_council_decision"] == "HOLD"


def test_p1007_risk_officer_declared_veto_forces_local_hold(tmp_path):
    path, packet, at = setup(tmp_path)
    case = packet["cases"][1]
    case["risk_veto_declared"] = True
    case["seat_claims"][29]["status"] = "VETO"
    case["strategist_proposal"] = "HOLD"
    freeze(packet)
    save(path, packet)
    r = assess(path, at=at)
    assert r["evaluation_snapshots"][1]["risk_officer_veto_declared"] is True
    assert r["evaluation_snapshots"][1]["real_council_decision"] == "HOLD"


def test_p1007_risk_veto_can_never_authorize_buy(tmp_path):
    def bad(p):
        c = p["cases"][0]
        c["risk_veto_declared"] = True
        c["seat_claims"][29]["status"] = "VETO"
        c["strategist_proposal"] = "BUY"
        c["seat_claims"][30]["direction"] = "BUY"
    tamper(tmp_path, bad)


def test_p1007_devils_advocate_challenge_cannot_override_veto(tmp_path):
    path, packet, at = setup(tmp_path)
    case = packet["cases"][1]
    case["devils_advocate_challenge_declared"] = True
    case["seat_claims"][28]["status"] = "CHALLENGE"
    freeze(packet)
    save(path, packet)
    r = assess(path, at=at)
    assert r["evaluation_snapshots"][1]["devils_advocate_challenge_declared"] is True
    assert r["evaluation_snapshots"][1]["influence_weights_applied"] is False


def test_p1008_no_calibrated_probability_from_agreement(tmp_path):
    r = valid(tmp_path)
    assert r["consensus_probabilities_calibrated"] == 0
    assert all(x["real_probability_of_up"] is None for x in r["evaluation_snapshots"])


# Strict source, clock, identity, prior local snapshot and CLI tests.

@pytest.mark.parametrize("field,value", [
    ("historical_epoch_id", "f" * 64), ("mode", "LIVE_LEARNING"),
    ("contract_version", "PROMOTED_WEIGHTS"),
    ("stage9_review_report_sha256", "f" * 64),
    ("council_manifest_sha256", "f" * 64),
    ("council_contracts_sha256", "f" * 64),
    ("embargo_seconds", 0), ("minimum_prior_samples", 3),
    ("minimum_prior_time_blocks", 1), ("max_prior_cases", 500),
    ("decay_factor", "1.0000"),
])
def test_global_pinned_packet_contract_rejects_mutations(tmp_path, field, value):
    tamper(tmp_path, lambda p: p.update({field: value}))


def test_global_future_packet_declaration_refused(tmp_path):
    tamper(tmp_path, lambda p: p.update(declared_at=z(datetime.now(UTC) + timedelta(days=1))))


def test_global_unknown_packet_property_refused(tmp_path):
    tamper(tmp_path, lambda p: p.update(reliability_approved=True))


def test_global_future_case_decision_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][2].update(
        decision_at=z(datetime.now(UTC) + timedelta(days=1))))


def test_global_wrong_case_decision_identity_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0].update(decision_key="e" * 64))


def test_global_backdated_outcome_availability_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0].update(
        outcome_available_at=p["cases"][0]["decision_at"]))


def test_global_unknown_outcome_cannot_have_positive_label(tmp_path):
    def bad(p):
        p["cases"][1]["outcome_status"] = "NOT_AVAILABLE"
        p["cases"][1]["outcome_direction"] = "BUY"
    tamper(tmp_path, bad)


def test_global_case_double_count_duplicate_id_refused(tmp_path):
    def bad(p):
        p["cases"][1]["decision_key"] = p["cases"][0]["decision_key"]
    tamper(tmp_path, bad)


def test_global_case_swapped_chronology_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"].reverse())


def test_global_case_unknown_regime_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0].update(regime="WINNER_ONLY"))


def test_global_case_forged_digest_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0].update(regime="RANGE"), rehash=False)


def test_global_seat_forged_digest_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(direction="SELL"), rehash=False)


def test_global_wrong_seat_id_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(seat_id="OTHER_MODEL"))


def test_global_swapped_seat_order_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"].reverse())


def test_global_removed_seat_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"].pop())


def test_global_future_seat_input_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(
        input_first_available_at=z(read_time(p["cases"][0]["decision_at"]) + timedelta(seconds=1))))


def test_global_postdecision_seat_opinion_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(
        opinion_created_at=z(read_time(p["cases"][0]["decision_at"]) + timedelta(seconds=1))))


def test_global_unauthorized_source_family_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(source_families=["future_market_feed"]))


def test_global_missing_mandatory_family_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(source_families=[]))


def test_global_duplicate_source_ids_refused(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(
        source_record_ids=["SHARED_RECORD", "SHARED_RECORD"]))


def test_global_source_ancestry_cannot_claim_private_key(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(
        source_record_ids=["private_key"] ))


def test_global_primary_vote_cannot_claim_verified_origin(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(
        independently_admitted=True))


def test_global_confidence_cannot_exceed_one(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(confidence="1.2000"))


def test_global_confidence_cannot_be_float(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0]["seat_claims"][0].update(confidence=0.75))


def test_global_synthesis_direction_consistency(tmp_path):
    tamper(tmp_path, lambda p: p["cases"][0].update(strategist_proposal="BUY"))


def test_global_review_sha_is_cutoff_bound(tmp_path):
    path, packet, at = setup(tmp_path)
    with pytest.raises(Stage10DesignError, match="reliability policy"):
        assess(path, at=z(read_time(at) - timedelta(seconds=1)))


def test_global_distinct_prior_identical_bytes_local_only(tmp_path):
    path, packet, at = setup(tmp_path)
    prior = tmp_path / "prior_copy.json"
    prior.write_bytes(path.read_bytes())
    r = assess(path, at=at, prior=prior)
    assert r["prior_history_file_sha256"] == sha256(path.read_bytes()).hexdigest()
    assert r["independent_seat_attributions_accepted"] == 0


def test_global_changed_prior_rejected_after_rehash(tmp_path):
    path, packet, at = setup(tmp_path)
    prior = tmp_path / "prior_copy.json"
    prior.write_bytes(path.read_bytes())
    packet["cases"][0]["outcome_direction"] = "SELL"
    freeze(packet)
    save(path, packet)
    with pytest.raises(Stage10DesignError, match="edited after freeze"):
        assess(path, at=at, prior=prior)


def test_global_same_file_prior_refused(tmp_path):
    path, packet, at = setup(tmp_path)
    with pytest.raises(Stage10DesignError, match="Distinct prior"):
        assess(path, at=at, prior=path)


def test_global_orphan_prior_refused(tmp_path):
    bogus = save(tmp_path / "prior.json", {})
    with pytest.raises(Stage10DesignError, match="Prior history cannot replace"):
        assess(prior=bogus)


def test_global_historical_policy_hash_unchanged(tmp_path):
    before = sha256(POLICY.read_bytes()).hexdigest()
    _ = valid(tmp_path)
    assert sha256(POLICY.read_bytes()).hexdigest() == before == POLICY_SHA256


def test_global_changed_historical_policy_refused(tmp_path):
    changed = tmp_path / "changed_policy.json"
    changed.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(Stage10DesignError, match="SHA-256 mismatch"):
        assess(policy=changed)


def test_global_epoch_rebinding_refused():
    with pytest.raises(Stage10DesignError, match="rebinding"):
        assess(epoch="f" * 64)


def test_global_future_review_cutoff_refused():
    with pytest.raises(Stage10DesignError, match="Future"):
        assess(at=z(datetime.now(UTC) + timedelta(days=1)))


def test_global_duplicate_json_keys_refused(tmp_path):
    file = tmp_path / "duplicate.json"
    file.write_text('{"mode":"SYNTHETIC_LOCAL_SEAT_HISTORY_ONLY","mode":"LIVE"}')
    with pytest.raises(Stage10DesignError, match="Unsafe"):
        assess(file)


def test_global_symlink_input_refused_if_supported(tmp_path):
    path, packet, at = setup(tmp_path)
    alias = tmp_path / "alias.json"
    try:
        alias.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink privilege unavailable on Windows")
    with pytest.raises(Stage10DesignError, match="Unsafe"):
        assess(alias, at=at)


def test_global_file_size_over_bound_refused(tmp_path):
    fake = tmp_path / "large.json"
    fake.write_bytes(b"X" * (8388608 + 1))
    with pytest.raises(Stage10DesignError, match="Unsafe"):
        assess(fake)


def test_global_cli_without_packet_still_hold(capsys):
    assert run(["--read-only-reliability", "--synthetic-only", "--epoch-id", EPOCH,
                "--as-of", review_at(), "--policy-file", str(POLICY),
                "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST)]) == 0
    output = capsys.readouterr().out
    assert "FINAL=HOLD" in output
    assert "Real learning pairs: 0" in output
    assert "NOT_ENOUGH_EVIDENCE" in output


def test_global_cli_with_packet_is_unattested(tmp_path, capsys):
    path, packet, at = setup(tmp_path)
    assert run(["--read-only-reliability", "--synthetic-only", "--epoch-id", EPOCH,
                "--as-of", at, "--policy-file", str(POLICY),
                "--contracts-file", str(CONTRACTS), "--manifest-file", str(MANIFEST),
                "--history-file", str(path), "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["historical_cases_examined"] == 3
    assert r["final_action"] == "HOLD"
    assert r["nondefault_weights_promoted"] == 0


def test_global_cli_explicit_safety_flags_required():
    with pytest.raises(SystemExit) as err:
        run(["--as-of", review_at(), "--epoch-id", EPOCH])
    assert err.value.code == 2


def test_global_report_deterministic_fixed_cutoff(tmp_path):
    path, packet, at = setup(tmp_path)
    first, second = assess(path, at=at), assess(path, at=at)
    assert first == second
    assert first["report_sha256"] == _digest({k: v for k, v in first.items() if k != "report_sha256"})


def test_global_no_real_execution_or_file_mutation(tmp_path):
    path, packet, at = setup(tmp_path)
    before = sha256(path.read_bytes()).hexdigest()
    r = assess(path, at=at)
    assert sha256(path.read_bytes()).hexdigest() == before
    for key in ("files_written", "network_accessed", "broker_connected", "council_invoked",
                "model_loaded", "training_authorized", "paper_orders_authorized",
                "live_orders_authorized", "operator_approval_granted", "future_epoch_created"):
        assert r[key] is False
    assert r["final_action"] == "HOLD"


def test_global_result_contains_no_raw_market_prices_or_paths(tmp_path):
    path, packet, at = setup(tmp_path)
    r = assess(path, at=at)
    body = json.dumps(r)
    assert "private_key" not in body
    assert str(path) not in body
    assert "1.0804" not in body
    assert r["research_operational_readiness"] == "BLOCKED_EXTERNAL_EVIDENCE"


def test_p1002_changed_model_family_excludes_old_history(tmp_path):
    path, packet, at = setup(tmp_path)
    packet["cases"][0]["seat_claims"][0]["model_family_id"] = "OLDER_MODEL_FAMILY"
    freeze(packet)
    save(path, packet)
    r = assess(path, at=at)
    s = r["evaluation_snapshots"][2]["seat_reliability_preview"][0]
    assert s["incompatible_prior_seat_configurations"] == 1
    assert s["directional_outcomes"] == 1
    assert s["nondefault_influence_permitted"] is False


def test_p1002_changed_family_requirements_exclude_old_history(tmp_path):
    path, packet, at = setup(tmp_path)
    seat = packet["cases"][0]["seat_claims"][0]
    seat["source_families"] = sorted(set(seat["source_families"]) | {"ticks"})
    freeze(packet)
    save(path, packet)
    r = assess(path, at=at)
    s = r["evaluation_snapshots"][2]["seat_reliability_preview"][0]
    assert s["incompatible_prior_seat_configurations"] == 1
    assert s["directional_outcomes"] == 1


def test_global_undeclared_historical_case_refused(tmp_path):
    def bad(p):
        p["declared_at"] = z(read_time(p["cases"][0]["bar_close_at"]) + timedelta(days=1))
    tamper(tmp_path, bad)
