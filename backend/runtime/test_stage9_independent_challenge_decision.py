
"""P9-21..24: local blind-alias trial audit, reviewer and decision HOLD tests.

Only temporary test fixtures write files; all research inputs are synthetic,
unattested and cannot authorize further-stage data-driven model selection.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import EPOCH, POLICY_SHA256
from backend.runtime.stage9_predeclared_experiment_protocol import BASELINE_ORDER, _digest
from backend.runtime.stage9_offline_costed_robustness_scorecard import assess_stage9_offline_scorecard
from backend.runtime.test_stage9_predeclared_experiment_protocol import save, z, cutoff, POLICY
from backend.runtime.test_stage9_offline_costed_robustness_scorecard import fixture as score_fixture
from backend.runtime.stage9_independent_challenge_decision import (
    VERSION, CHALLENGE_VERSION, REVIEW_VERSION, MEMO_VERSION, SELECTION_VERSION,
    CHALLENGERS, DECISIONS, TRIAL_KINDS, ZERO, ChallengeDecisionError,
    assess_stage9_challenge_decision, run,
)

UTC = timezone.utc


def stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def trial_log(doc):
    parent = ZERO
    for offset, record in enumerate(doc["trials"]):
        record["ordinal"] = offset
        record["previous_attempt_sha256"] = parent
        record["attempt_sha256"] = _digest({k: v for k, v in record.items() if k != "attempt_sha256"})
        parent = record["attempt_sha256"]
    doc["declared_total_trials"] = len(doc["trials"])


def setup(tmp_path, *, with_review=False, variants=False, witness=False, counterfactuals=False):
    pp, protocol, lp, ledger, rp, op, data, sp, scenario, at = score_fixture(
        tmp_path, variants=variants, witness=witness, counterfactuals=counterfactuals,
    )
    scorer = assess_stage9_offline_scorecard(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        protocol_file=pp, ledger_file=lp, receipt_candidates=rp,
        outcome_file=op, scenario_file=sp,
    )
    selected = next(x for x in protocol["holdout"]["windows"]
                    if x["phase"] == "CHALLENGER_SELECTION")
    final = next(x for x in protocol["holdout"]["windows"]
                 if x["phase"] == "FINAL_TEST")
    train = next(x for x in protocol["holdout"]["windows"] if x["phase"] == "TRAIN")
    valid = next(x for x in protocol["holdout"]["windows"] if x["phase"] == "VALIDATION")
    calibr = next(x for x in protocol["holdout"]["windows"] if x["phase"] == "CALIBRATION")

    def event(i, algorithm, kind, window, day, *, digest=None, lookback=12, restart=None):
        return {
            "ordinal": i, "attempt_id": "LOCAL_ATTEMPT_%03d" % i,
            "algorithm_id": algorithm, "attempt_kind": kind,
            "configuration_sha256": digest or "a" * 64,
            "lookback_m5_bars": lookback, "partition": window["phase"],
            "run_at": z(stamp(window["start_at"]) + timedelta(days=day)),
            "result_digest_claim": "d" * 64,
            "restart_reason": restart,
            "previous_attempt_sha256": ZERO, "attempt_sha256": ZERO,
        }

    trials = [
        event(0, "ORACLE", "INITIAL", train, 1),
        event(1, "ORACLE", "CONFIG_CHANGE", train, 2, digest="b" * 64),
        event(2, "ORACLE", "LOOKBACK_CHANGE", valid, 1, digest="b" * 64, lookback=24),
        event(3, "ORACLE", "ABANDONED", calibr, 1, digest="b" * 64, lookback=24),
        event(4, "ORACLE", "EVALUATION_RESTART", calibr, 2, digest="b" * 64,
              lookback=24, restart="CODE_CHANGE"),
    ]
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_LOCAL_CHALLENGE_001",
        "mode": "LOCAL_UNATTESTED_CHALLENGE_DESIGN_ONLY",
        "contract_version": CHALLENGE_VERSION,
        "experiment_id": protocol["experiment_id"],
        "protocol_candidate_sha256": scorer["protocol_candidate_sha256"],
        "custody_report_sha256": scorer["custody_report_sha256"],
        "outcome_report_sha256": scorer["outcome_report_sha256"],
        "scorecard_report_sha256": scorer["report_sha256"],
        "locked_at": at,
        "selection_window_start_at": selected["start_at"],
        "final_test_start_at": final["start_at"],
        "final_test_opened": False,
        "all_candidate_test_results_unseen": True,
        "publication_policy": "PUBLISH_ALL_BENCHMARKS_MISSINGNESS_AND_NEGATIVE_RESULTS",
        "completeness_claim": "LOCAL_UNVERIFIED_ATTEMPT_INVENTORY",
        "blind_salt_sha256": "e" * 64,
        "candidates": [
            {"algorithm_id": algo, "blind_alias": "BLIND_%03d" % i,
             "configuration_sha256": ("b" * 64 if i == 0 else ("%x" % (i+1)) * 64)}
            for i, algo in enumerate(CHALLENGERS)
        ],
        "trials": trials, "declared_total_trials": len(trials),
    }
    # Parent hashes bind the local trial prefix but cannot make it externally witnessed.
    trial_log(doc)
    cp = save(tmp_path / "synthetic_local_challenge.json", doc)
    rp_review = rdoc = None
    if with_review:
        reviewers = []
        for i, role in enumerate(("DATA_CUSTODY_REVIEWER_CLAIM",
                                  "STATISTICAL_REVIEWER_CLAIM", "RISK_REVIEWER_CLAIM")):
            reviewers.append({
                "reviewer_id": "LOCAL_REVIEWER_%03d" % i, "reviewer_role": role,
                "reviewed_at": at,
                "local_review_status": "LOCAL_BLOCKERS_IDENTIFIED",
                "review_digest_claim": "f" * 64,
                "conflict_disclosure": "NONE_CLAIMED_UNVERIFIED",
                "signature_verified": False,
            })
        rdoc = {
            "schema_version": 1, "historical_epoch_id": EPOCH,
            "packet_id": "LOCAL_UNVERIFIED_REVIEW_001",
            "mode": "LOCAL_UNATTESTED_REVIEW_CLAIMS_ONLY",
            "contract_version": REVIEW_VERSION,
            "experiment_id": protocol["experiment_id"],
            "challenge_file_sha256": sha256(cp.read_bytes()).hexdigest(),
            "scorecard_report_sha256": scorer["report_sha256"],
            "independent_approval_claim": False, "request_transmitted": False,
            "reviewers": reviewers,
        }
        rp_review = save(tmp_path / "untrusted_local_reviewer_claims.json", rdoc)
    return pp, protocol, lp, ledger, rp, op, sp, scorer, cp, doc, rp_review, rdoc, at


def assess(pp=None, lp=None, op=None, sp=None, cp=None, *, rp=None, prior=None,
           reviewer=None, at=None, policy=POLICY, epoch=EPOCH):
    return assess_stage9_challenge_decision(
        epoch_id=epoch, as_of=cutoff() if at is None else at,
        policy_file=policy, protocol_file=pp, ledger_file=lp,
        receipt_candidates=rp, outcome_file=op, scenario_file=sp,
        challenge_file=cp, prior_challenge=prior, reviewer_claims=reviewer,
    )


def valid(tmp_path, *, review=False, **options):
    pp, protocol, lp, ledger, rp, op, sp, scorer, cp, doc, rpath, rdoc, at = setup(
        tmp_path, with_review=review, **options,
    )
    return assess(pp, lp, op, sp, cp, rp=rp, reviewer=rpath, at=at)


def altered(tmp_path, change):
    pp, pdoc, lp, ledger, rp, op, sp, score, cp, doc, reviewer, rdoc, at = setup(tmp_path)
    change(doc)
    save(cp, doc)
    with pytest.raises(ChallengeDecisionError):
        assess(pp, lp, op, sp, cp, rp=rp, at=at)


# P9-21: locked candidate controls and honest blind-alias claim.

def test_p921_no_source_chain_is_reviewable_but_never_approved():
    report = assess()
    assert report["challenge_status"] == "NOT_SUPPLIED"
    assert report["research_decision_memorandum"]["research_decision"] == "NOT_ENOUGH_EVIDENCE"
    assert report["final_action"] == "HOLD"


def test_p921_all_six_challengers_locked_without_running_blind_test(tmp_path):
    result = valid(tmp_path)
    c = result["challenge"]
    assert c["locked_algorithm_order"] == list(CHALLENGERS)
    assert c["all_five_baselines_included"]
    assert len(set(c["blind_aliases"])) == 6
    assert c["independent_blind_assignment_or_holdout_custody_verified"] is False
    assert result["blind_challenger_comparison_executed"] is False


def test_p921_council_and_predeclared_five_benchmarks_not_replaced(tmp_path):
    result = valid(tmp_path)
    assert result["independent_review_request"]["five_baseline_ids"] == list(BASELINE_ORDER)
    assert result["challenge"]["locked_algorithm_order"] == ["ORACLE", *BASELINE_ORDER]


@pytest.mark.parametrize("name,value", [
    ("mode", "EXTERNALLY_SIGNED"),
    ("historical_epoch_id", "f" * 64),
    ("contract_version", "BLIND_EXTERNAL_APPROVED"),
    ("final_test_opened", True),
    ("all_candidate_test_results_unseen", False),
    ("publication_policy", "PUBLISH_WINNERS_ONLY"),
    ("completeness_claim", "ALL_TRIALS_EXTERNALLY_VERIFIED"),
    ("scorecard_report_sha256", "f" * 64),
    ("outcome_report_sha256", "f" * 64),
    ("custody_report_sha256", "f" * 64),
    ("protocol_candidate_sha256", "f" * 64),
    ("experiment_id", "OTHER_RESEARCH"),
])
def test_p921_wrong_challenger_scope_or_parent_refused(tmp_path, name, value):
    altered(tmp_path, lambda x: x.__setitem__(name, value))


def test_p921_missing_mandatory_challenger_rejected(tmp_path):
    altered(tmp_path, lambda x: x["candidates"].pop())


def test_p921_reordered_baselines_rejected(tmp_path):
    altered(tmp_path, lambda x: x["candidates"].reverse())


def test_p921_duplicate_alias_rejected(tmp_path):
    altered(tmp_path, lambda x: x["candidates"][1].update(
        blind_alias=x["candidates"][0]["blind_alias"]))


def test_p921_invalid_candidate_configuration_digest_rejected(tmp_path):
    altered(tmp_path, lambda x: x["candidates"][0].update(configuration_sha256="SIGNED"))


def test_p921_challenge_locked_after_selection_start_rejected(tmp_path):
    altered(tmp_path, lambda x: x.update(locked_at=x["selection_window_start_at"]))


def test_p921_changed_final_test_start_rejected(tmp_path):
    altered(tmp_path, lambda x: x.update(final_test_start_at="2030-01-01T00:00:00Z"))


# P9-22: all local attempts, abandoned configurations, restarts and multiplicity.

def test_p922_records_all_five_local_trial_events_and_kinds(tmp_path):
    report = valid(tmp_path)
    audit = report["selection_audit"]
    assert audit["claimed_trial_events"] == 5
    assert audit["locally_claimed_abandoned_trials"] == 1
    assert audit["locally_claimed_lookback_changes"] == 1
    assert audit["locally_claimed_evaluation_restarts"] == 1
    assert audit["attempt_kind_counts"]["CONFIG_CHANGE"] == 1
    assert audit["independent_complete_attempt_history_verified"] is False


def test_p922_multiplicity_schedule_is_predeclared_but_no_statistics(tmp_path):
    audit = valid(tmp_path)["selection_audit"]
    assert audit["minimum_planned_family_size"] == 20
    assert audit["predeclared_familywise_alpha"] == "0.025"
    assert audit["holm_bonferroni_critical_values_by_order"][0] == "0.0012500000"
    assert audit["holm_bonferroni_critical_values_by_order"][-1] == "0.0250000000"
    assert audit["holm_corrected_p_values_computed"] is False
    assert audit["selection_bias_independently_excluded"] is False


def test_p922_attempt_hash_prefix_tip_is_local_not_external(tmp_path):
    result = valid(tmp_path)
    a = result["selection_audit"]
    assert len(a["local_prefix_tip_sha256"]) == 64
    assert len(a["trial_receipts"]) == 5
    assert a["unreported_or_abandoned_attempts_ruled_out"] is False


def test_p922_local_attempt_count_mismatch_rejected(tmp_path):
    altered(tmp_path, lambda x: x.update(declared_total_trials=1))


def test_p922_local_attempt_payload_tamper_without_rehash_rejected(tmp_path):
    altered(tmp_path, lambda x: x["trials"][2].update(lookback_m5_bars=120))


def test_p922_rehash_cannot_launder_invalid_run_partition(tmp_path):
    def mutate(x):
        x["trials"][2]["partition"] = "FINAL_TEST"
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_future_test_metric_partition_rejected(tmp_path):
    def mutate(x):
        x["trials"][2]["partition"] = "CHALLENGER_SELECTION"
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_reordered_trial_history_refused_even_when_rechained(tmp_path):
    def mutate(x):
        a = x["trials"]
        a[1], a[2] = a[2], a[1]
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_duplicate_attempt_identity_refused(tmp_path):
    def mutate(x):
        x["trials"][1]["attempt_id"] = x["trials"][0]["attempt_id"]
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_duplicate_initial_for_same_algorithm_refused(tmp_path):
    def mutate(x):
        x["trials"][1]["attempt_kind"] = "INITIAL"
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_config_change_without_new_digest_refused(tmp_path):
    def mutate(x):
        x["trials"][1]["configuration_sha256"] = x["trials"][0]["configuration_sha256"]
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_lookback_change_without_change_refused(tmp_path):
    def mutate(x):
        x["trials"][2]["lookback_m5_bars"] = 12
        trial_log(x)
    altered(tmp_path, mutate)


@pytest.mark.parametrize("bad", [-1, 0, True, "100", 99999])
def test_p922_lookback_must_be_bounded_m5_integer(tmp_path, bad):
    def mutate(x):
        x["trials"][2]["lookback_m5_bars"] = bad
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_restart_requires_explicit_reason(tmp_path):
    def mutate(x):
        x["trials"][4]["restart_reason"] = None
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_restart_reason_cannot_self_authorize(tmp_path):
    def mutate(x):
        x["trials"][4]["restart_reason"] = "APPROVED_AFTER_WIN"
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_restart_reason_for_non_restart_refused(tmp_path):
    def mutate(x):
        x["trials"][0]["restart_reason"] = "CODE_CHANGE"
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_unrecognized_algorithm_trial_refused(tmp_path):
    def mutate(x):
        x["trials"][2]["algorithm_id"] = "SECRET_CHALLENGER"
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_negative_result_selection_cannot_be_hidden(tmp_path):
    altered(tmp_path, lambda x: x.update(publication_policy="DROP_ABANDONED_RESULTS"))


def test_p922_rehashed_prior_challenge_change_rejected(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rev, rdoc, at = setup(tmp_path)
    prior = tmp_path / "previous_local_challenge.json"
    prior.write_bytes(cp.read_bytes())
    doc["candidates"][1]["configuration_sha256"] = "c" * 64
    save(cp, doc)
    with pytest.raises(ChallengeDecisionError, match="changed bytes"):
        assess(pp, lp, op, sp, cp, prior=prior, at=at)


def test_p922_exact_same_local_prior_bytes_are_not_witness(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rev, rdoc, at = setup(tmp_path)
    prior = tmp_path / "previous_local_challenge.json"
    prior.write_bytes(cp.read_bytes())
    result = assess(pp, lp, op, sp, cp, prior=prior, at=at)
    assert result["independent_protocol_receipts_verified"] == 0
    assert result["independent_review_request"]["reproducible_local_input_hashes"][
        "prior_challenge_file_sha256"] == sha256(cp.read_bytes()).hexdigest()


# P9-23: reviewer claims and unsent reproducibility packet.

def test_p923_review_request_only_hash_inventory_no_network(tmp_path):
    report = valid(tmp_path)
    request = report["independent_review_request"]
    assert request["request_only"] is True
    assert request["transmitted"] is False
    assert request["source_family_receipts"] == 0
    assert len(request["code_inventory"]) == 6
    assert all(len(item["local_code_sha256"]) == 64 for item in request["code_inventory"])
    assert all(item["independently_reviewed"] is False for item in request["code_inventory"])
    assert report["independent_research_review_completed"] is False


def test_p923_full_local_reviewer_claims_still_not_independent(tmp_path):
    report = valid(tmp_path, review=True)
    r = report["reviewer_claims"]
    assert r["reviewer_claim_count"] == 3
    assert r["locally_claimed_blocker_reviews"] == 3
    assert r["independently_verified_reviewer_signatures"] == 0
    assert r["independent_reviewers_accepted"] == 0
    assert report["final_action"] == "HOLD"


def test_p923_local_review_never_self_authorizes_research(tmp_path):
    report = valid(tmp_path, review=True, witness=True)
    assert report["prospectively_scorable_pairs"] == 0
    assert report["independent_forecast_receipts_verified"] == 0
    assert report["predictive_edge_proven"] is False


@pytest.mark.parametrize("field,bad", [
    ("mode", "INDEPENDENTLY_SIGNED_REVIEW"),
    ("contract_version", "RELEASE_APPROVED"),
    ("independent_approval_claim", True),
    ("request_transmitted", True),
    ("experiment_id", "f" * 64),
    ("challenge_file_sha256", "f" * 64),
    ("scorecard_report_sha256", "f" * 64),
])
def test_p923_local_reviewer_claim_authority_or_parent_changed_refused(tmp_path, field, bad):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(
        tmp_path, with_review=True)
    rdoc[field] = bad
    save(rpath, rdoc)
    with pytest.raises(ChallengeDecisionError):
        assess(pp, lp, op, sp, cp, reviewer=rpath, at=at)


def test_p923_duplicate_reviewer_id_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(
        tmp_path, with_review=True)
    rdoc["reviewers"][1]["reviewer_id"] = rdoc["reviewers"][0]["reviewer_id"]
    save(rpath, rdoc)
    with pytest.raises(ChallengeDecisionError, match="Duplicate"):
        assess(pp, lp, op, sp, cp, reviewer=rpath, at=at)


def test_p923_reviewer_claim_verified_signature_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(
        tmp_path, with_review=True)
    rdoc["reviewers"][0]["signature_verified"] = True
    save(rpath, rdoc)
    with pytest.raises(ChallengeDecisionError, match="self-certify"):
        assess(pp, lp, op, sp, cp, reviewer=rpath, at=at)


def test_p923_reviewer_future_time_rejected(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(
        tmp_path, with_review=True)
    rdoc["reviewers"][0]["reviewed_at"] = z(stamp(at) + timedelta(days=1))
    save(rpath, rdoc)
    with pytest.raises(ChallengeDecisionError, match="review clock"):
        assess(pp, lp, op, sp, cp, reviewer=rpath, at=at)


def test_p923_reviewer_status_and_digest_cannot_disagree(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(
        tmp_path, with_review=True)
    rdoc["reviewers"][0]["local_review_status"] = "NOT_REVIEWED"
    save(rpath, rdoc)
    with pytest.raises(ChallengeDecisionError, match="inconsistent"):
        assess(pp, lp, op, sp, cp, reviewer=rpath, at=at)


def test_p923_reviewer_packet_without_challenge_rejected(tmp_path):
    p = save(tmp_path / "fake_review.json", {})
    with pytest.raises(ChallengeDecisionError, match="require verified local challenger"):
        assess(reviewer=p)


# P9-24: outcome classification is lack of evidence, not a negative result.

def test_p924_four_research_decision_labels_are_documented():
    assert DECISIONS == (
        "NOT_ENOUGH_EVIDENCE", "NO_DEMONSTRATED_EDGE", "CONDITIONAL_CANDIDATE",
        "INDEPENDENTLY_SUPPORTED_FOR_RESEARCH_ONLY",
    )


def test_p924_even_all_local_claims_classify_not_enough_evidence(tmp_path):
    report = valid(tmp_path, review=True, witness=True, variants=True, counterfactuals=True)
    memo = report["research_decision_memorandum"]
    assert memo["contract_version"] == MEMO_VERSION
    assert memo["research_decision"] == "NOT_ENOUGH_EVIDENCE"
    assert memo["independent_research_support_granted"] is False
    assert memo["stage9_independent_research_exit_criteria_met"] is False
    assert memo["shadow_or_trading_rights_granted"] is False
    assert memo["stage10_reliability_training_or_selection_authorized"] is False
    assert memo["final_action"] == "HOLD"


def test_p924_not_enough_evidence_is_not_demonstrated_no_edge(tmp_path):
    result = valid(tmp_path)
    memo = result["research_decision_memorandum"]
    assert memo["no_demonstrated_edge_is_not_equivalent_to_no_evidence"]
    assert memo["decision_basis"] == "ZERO_INDEPENDENTLY_SCORABLE_PROSPECTIVE_FORECAST_OUTCOME_PAIRS"
    assert result["predictive_edge_proven"] is False


def test_p924_final_handover_hash_is_stable_for_fixed_inputs(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(tmp_path)
    a = assess(pp, lp, op, sp, cp, at=at)
    b = assess(pp, lp, op, sp, cp, at=at)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})
    assert len(a["independent_review_request"]["unsent_request_sha256"]) == 64


def test_p924_frozen_source_scenario_edit_invalidates_challenge(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(tmp_path)
    scen = json.loads(sp.read_text("utf-8"))
    scen["rows"][0]["regime"] = "NEW_YORK"
    save(sp, scen)
    with pytest.raises(ChallengeDecisionError, match="upstream frozen"):
        assess(pp, lp, op, sp, cp, at=at)


def test_p924_real_broker_and_network_flags_unchanged(tmp_path):
    report = valid(tmp_path)
    for name in (
        "files_written", "network_accessed", "broker_connected", "collector_started",
        "council_invoked", "model_loaded", "training_authorized", "settlement_invoked",
        "paper_orders_authorized", "live_orders_authorized", "operator_approval_granted",
        "historical_epoch_rebound", "future_epoch_created",
        "eligible_final_test_outcomes_inspected", "blind_challenger_comparison_executed",
        "multiple_testing_adjusted_significance_claim_made", "independent_research_review_completed",
        "predictive_edge_proven",
    ):
        assert report[name] is False
    assert report["final_action"] == "HOLD"


def test_p924_historical_policy_pinned_and_unchanged(tmp_path):
    before = sha256(POLICY.read_bytes()).hexdigest()
    result = valid(tmp_path)
    assert result["historical_policy_sha256"] == POLICY_SHA256
    assert sha256(POLICY.read_bytes()).hexdigest() == before == POLICY_SHA256


def test_p924_historical_epoch_rebinding_refused():
    with pytest.raises(ChallengeDecisionError, match="rebinding refused"):
        assess(epoch="f" * 64)


def test_p924_historical_clock_policy_byte_tamper_refused(tmp_path):
    p = tmp_path / "tampered_clock.json"
    p.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(ChallengeDecisionError, match="SHA-256 mismatch"):
        assess(policy=p)


def test_p924_future_review_cutoff_fails_closed():
    with pytest.raises(ChallengeDecisionError, match="Future review"):
        assess(at=z(datetime.now(UTC) + timedelta(days=1)))


def test_p924_partial_scorecard_chain_refused(tmp_path):
    f = save(tmp_path / "partial.json", {})
    with pytest.raises(ChallengeDecisionError, match="must be paired"):
        assess(sp=f)


def test_p924_challenge_without_scorecard_chain_refused(tmp_path):
    f = save(tmp_path / "challenge.json", {})
    with pytest.raises(ChallengeDecisionError, match="requires the complete"):
        assess(cp=f)


def test_p924_duplicate_json_key_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(tmp_path)
    cp.write_text('{"mode":"LOCAL_UNATTESTED_CHALLENGE_DESIGN_ONLY","mode":"RELEASED"}')
    with pytest.raises(ChallengeDecisionError, match="Unsafe"):
        assess(pp, lp, op, sp, cp, at=at)


def test_p924_overly_large_challenge_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(tmp_path)
    cp.write_bytes(b"X" * 131073)
    with pytest.raises(ChallengeDecisionError, match="Unsafe"):
        assess(pp, lp, op, sp, cp, at=at)


def test_p924_no_source_or_challenge_mutation(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(tmp_path)
    files = (pp, lp, op, sp, cp)
    before = [sha256(p.read_bytes()).hexdigest() for p in files]
    report = assess(pp, lp, op, sp, cp, at=at)
    assert [sha256(p.read_bytes()).hexdigest() for p in files] == before
    assert report["files_written"] is False


def test_p924_cli_no_evidence_reports_hold(capsys):
    assert run([
        "--read-only-review", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", cutoff(), "--policy-file", str(POLICY),
    ]) == 0
    stdout = capsys.readouterr().out
    assert "Decision: NOT_ENOUGH_EVIDENCE" in stdout
    assert "FINAL=HOLD" in stdout
    assert "Independent gates/families: 0/11 | 0/13" in stdout


def test_p924_cli_json_with_fixtures_does_not_approve(tmp_path, capsys):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(tmp_path, with_review=True)
    assert run([
        "--read-only-review", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", at, "--policy-file", str(POLICY),
        "--protocol-file", str(pp), "--ledger-file", str(lp),
        "--outcome-file", str(op), "--scenario-file", str(sp),
        "--challenge-file", str(cp), "--reviewer-claims", str(rpath), "--json",
    ]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["reviewer_claims"]["reviewer_claim_count"] == 3
    assert report["research_decision_memorandum"]["research_decision"] == "NOT_ENOUGH_EVIDENCE"
    assert report["final_action"] == "HOLD"


def test_p924_cli_requires_explicit_safety_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", cutoff()])
    assert exc.value.code == 2


def test_p924_cli_future_review_exits_blocked(capsys):
    assert run([
        "--read-only-review", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", z(datetime.now(UTC) + timedelta(days=1)),
        "--policy-file", str(POLICY),
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p924_report_never_leaks_prices_or_local_paths(tmp_path):
    report = valid(tmp_path)
    dump = json.dumps(report)
    assert "1.0804" not in dump
    assert "synthetic_local_challenge.json" not in dump
    assert "private_key" not in dump


def test_p924_symlink_challenge_rejected_where_supported(tmp_path):
    pp, proto, lp, ledger, rp, op, sp, score, cp, doc, rpath, rdoc, at = setup(tmp_path)
    alias = tmp_path / "linked_challenge.json"
    try:
        alias.symlink_to(cp)
    except (OSError, NotImplementedError):
        pytest.skip("Windows symlink privilege unavailable")
    with pytest.raises(ChallengeDecisionError, match="Unsafe"):
        assess(pp, lp, op, sp, alias, at=at)


def test_p924_contract_versions_are_frozen(tmp_path):
    result = valid(tmp_path)
    assert result["version"] == VERSION
    assert result["challenge"]["contract_version"] == CHALLENGE_VERSION
    assert result["selection_audit"]["contract_version"] == SELECTION_VERSION
    assert result["independent_review_request"]["contract_version"] == REVIEW_VERSION
    assert result["research_decision_memorandum"]["contract_version"] == MEMO_VERSION


def test_p922_selected_oracle_config_must_match_final_nonabandoned_trial(tmp_path):
    altered(tmp_path, lambda x: x["candidates"][0].update(configuration_sha256="c" * 64))


def test_p922_abandoned_final_config_cannot_be_selected(tmp_path):
    def mutate(x):
        x["trials"][-1]["attempt_kind"] = "ABANDONED"
        x["trials"][-1]["restart_reason"] = None
        trial_log(x)
    altered(tmp_path, mutate)


def test_p922_oracle_candidate_binds_local_trial_config_digest(tmp_path):
    report = valid(tmp_path)
    final = report["selection_audit"]["last_attempt_state_by_algorithm"]["ORACLE"]
    assert final["configuration"] == "b" * 64
    assert final["kind"] == "EVALUATION_RESTART"


@pytest.mark.parametrize("orphan", ["prior_protocol", "prior_ledger", "receipt_candidates", "prior_outcome", "prior_scenario"])
def test_p924_orphan_auxiliary_without_original_chain_refused(tmp_path, orphan):
    file = save(tmp_path / "orphan.json", {})
    with pytest.raises(ChallengeDecisionError, match="Auxiliary evidence requires"):
        assess_stage9_challenge_decision(
            epoch_id=EPOCH, as_of=cutoff(), policy_file=POLICY,
            **{orphan: file},
        )
