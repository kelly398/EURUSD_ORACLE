
"""P9-13..20: synthetic-only, full-denominator costed diagnostics and guards.

Temporary fixture writers are exclusively in tests. No models, real trade
receipts, witness signatures, calibration fit, training or live data are used.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

import pytest

from backend.runtime.stage8_m5_offline_heartbeat import EPOCH, POLICY_SHA256
from backend.runtime.stage9_predeclared_experiment_protocol import _digest
from backend.runtime.stage9_forecast_custody_reconciliation import assess_stage9_forecast_custody
from backend.runtime.stage9_outcome_maturation_reconciliation import assess_stage9_outcome_maturation
from backend.runtime.test_stage9_predeclared_experiment_protocol import save, cutoff, z, POLICY
from backend.runtime.test_stage9_outcome_maturation_reconciliation import (
    outcome_fixture, q, requote,
)
from backend.runtime.stage9_offline_costed_robustness_scorecard import (
    VERSION, SCENARIO_VERSION, BASELINE_ORDER, OfflineScorecardError,
    _calculation, _probability_stats, _block_status,
    _s, assess_stage9_offline_scorecard, run,
)

UTC = timezone.utc


def _parse(stamp):
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def fixture(tmp_path, *, counterfactuals=False, variants=False, witness=False, missing=False):
    pp, proto, lp, ledger, rp, op, outcome, at = outcome_fixture(
        tmp_path, include=not missing, witness=witness)
    custody = assess_stage9_forecast_custody(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        protocol_file=pp, ledger_file=lp, receipt_candidates=rp,
    )
    maturation = assess_stage9_outcome_maturation(
        epoch_id=EPOCH, as_of=at, policy_file=POLICY,
        protocol_file=pp, ledger_file=lp, receipt_candidates=rp, outcome_file=op,
    )
    held = set(r["decision_key"] for r in ledger["decisions"] if r["forecast"] is None)
    ablation_variants = ([{
        "variant_id": "NO_WEAK_SEAT",
        "definition_sha256": "a" * 64,
        "frozen_at": z(_parse(ledger["window_first_bar_at"]) - timedelta(hours=1)),
        "removed_seat_ids": ["SEAT_29"],
    }] if variants else [])
    rows = []
    for rec in ledger["decisions"]:
        bar = _parse(rec["bar_close_at"])
        key = rec["decision_key"]
        hist = [{
            "bar_close_at": z(bar - timedelta(minutes=(2 - i) * 5)),
            "published_at": z(bar - timedelta(minutes=(2 - i) * 5) + timedelta(seconds=1)),
            "open": ["1.0800", "1.0801", "1.0802"][i],
            "close": ["1.0801", "1.0802", "1.0803"][i],
        } for i in range(3)]
        counter = []
        if counterfactuals and key in held:
            target = bar + timedelta(seconds=20 + proto["hypotheses"][0]["horizon_seconds"])
            counter = [{
                "hypothesis_id": proto["hypotheses"][0]["hypothesis_id"],
                "target_at": z(target),
                "decision_quote": q(bar), "target_quote": q(target, target=True),
            }]
        actions = ([{
            "variant_id": "NO_WEAK_SEAT", "hypothesis_id": h["hypothesis_id"],
            "direction": "SELL" if rec["forecast"] is not None else "HOLD",
        } for h in proto["hypotheses"]] if variants else [])
        rows.append({
            "decision_key": key,
            "regime": "LONDON" if rec["forecast"] is not None else "UNKNOWN",
            "news_status": "NO_KNOWN_RELEASE",
            "context_available_at": z(bar), "context_claim_sha256": "b" * 64,
            "candle_history": hist, "hold_counterfactuals": counter,
            "ablations": actions,
        })
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "packet_id": "SYNTHETIC_SCORECARD_REHEARSAL",
        "mode": "SYNTHETIC_OFFLINE_SCORECARD_ONLY",
        "contract_version": SCENARIO_VERSION,
        "experiment_id": proto["experiment_id"],
        "protocol_candidate_sha256": _digest(proto),
        "custody_report_sha256": custody["report_sha256"],
        "outcome_report_sha256": maturation["report_sha256"],
        "mock_clock_at": at,
        "window_role": "SYNTHETIC_DESIGN_DEMONSTRATION_NOT_A_HOLDOUT",
        "final_test_opened": False,
        "rows": rows, "ablation_variants": ablation_variants,
    }
    path = save(tmp_path / "synthetic_offline_scoring_demo.json", doc)
    return pp, proto, lp, ledger, rp, op, outcome, path, doc, at


def assess(pp=None, lp=None, op=None, sp=None, *, rp=None, prior=None, at=None,
           policy=POLICY, epoch=EPOCH):
    return assess_stage9_offline_scorecard(
        epoch_id=epoch, as_of=cutoff() if at is None else at,
        policy_file=policy, protocol_file=pp, ledger_file=lp,
        receipt_candidates=rp, outcome_file=op,
        scenario_file=sp, prior_scenario=prior,
    )


def valid(tmp_path, **opts):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path, **opts)
    return assess(pp, lp, op, sp, rp=rp, at=at)


def edited(tmp_path, updater, *args):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    updater(scenario, *args)
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError):
        assess(pp, lp, op, sp, at=at)


# P9-13: quote-side cost, separation from broker receipts and fixed stress.

def test_p913_no_inputs_never_imply_evaluated_performance():
    result = assess()
    assert result["scenario_status"] == "NOT_SUPPLIED"
    assert result["local_matured_unattested_forecast_pairs"] == 0
    assert result["prospectively_scorable_pairs"] == 0
    assert result["final_action"] == "HOLD"


def test_p913_local_matured_quote_pair_yields_only_synthetic_stats(tmp_path):
    result = valid(tmp_path)
    assert result["local_matured_unattested_forecast_pairs"] == 1
    assert result["hypotheses"][0]["local_matured_pairs_examined"] == 1
    assert result["hypotheses"][0]["oracle"]["acted_on_observed_pairs"] == 1
    assert result["realized_trades_or_broker_returns_used"] == 0
    assert result["calibration_or_edge_proven"] is False


def test_p913_buy_uses_entry_ask_and_exit_bid_without_double_spread():
    start = {"bid": "1.0800", "ask": "1.0802"}
    end = {"bid": "1.0804", "ask": "1.0806"}
    cost = {"round_trip_commission_pips": "0.150", "slippage_each_side_pips": "0.250",
            "financing_per_day_pips": "0.080", "assumed_holding_days": "0.250",
            "latency_penalty_pips": "0.100"}
    assert _s(_calculation(start, end, cost, "BUY", Decimal("1.0"))) == "1.2300"
    assert _s(_calculation(start, end, cost, "SELL", Decimal("1.0"))) == "-6.7700"
    assert _s(_calculation(start, end, cost, "HOLD", Decimal("1.0"))) == "0.0000"


def test_p913_cost_stress_never_improves_buy_net_on_same_quotes(tmp_path):
    r = valid(tmp_path)["hypotheses"][0]["oracle_hypothetical_stressed_sum_pips"]
    assert Decimal(r["1.000"]) > Decimal(r["1.500"]) > Decimal(r["2.000"])


def test_p913_quote_costs_are_not_realized_trade_evidence(tmp_path):
    r = valid(tmp_path)
    assert r["realized_trades_or_broker_returns_used"] == 0
    assert r["paper_orders_authorized"] is False
    assert r["broker_connected"] is False


def test_p913_risk_drawdown_has_hypothetical_label(tmp_path):
    first = valid(tmp_path)["hypotheses"][0]["oracle"]
    assert first["hypothetical_max_drawdown_pips"] is not None
    assert first["risk_statistics_are_trade_records"] is False


# P9-14: five predeclared baselines on identical observed pairs and full coverage.

def test_p914_five_baselines_have_same_local_outcome_subset(tmp_path):
    r = valid(tmp_path)
    h = r["hypotheses"][0]
    assert list(h["five_locked_baselines"]) == list(BASELINE_ORDER)
    assert all(x["observed_outcome_pairs"] == 1 for x in h["five_locked_baselines"].values())
    assert all(x["full_m5_denominator"] == 5 for x in h["five_locked_baselines"].values())


def test_p914_all_five_baselines_have_fixed_probability_references(tmp_path):
    h = valid(tmp_path)["hypotheses"][0]
    probs = h["five_baseline_probability_reliability"]
    assert list(probs) == list(BASELINE_ORDER)
    assert all(x["nonneutral_scored_n"] == 1 for x in probs.values())
    assert probs["NO_TRADE"]["brier_score_synthetic"] == "0.2500"
    assert probs["ALWAYS_LONG"]["brier_score_synthetic"] == "0.0000"
    assert all(x["calibration_claim_permitted"] is False for x in probs.values())


def test_p914_no_trade_has_zero_actions_not_infinite_accuracy(tmp_path):
    h = valid(tmp_path)["hypotheses"][0]["five_locked_baselines"]["NO_TRADE"]
    assert h["acted_on_observed_pairs"] == 0
    assert h["directional_accuracy_non_tie"] is None
    assert h["hypothetical_net_pips_sum"] == "0.0000"


def test_p914_locked_long_short_baselines_have_opposite_direction(tmp_path):
    base = valid(tmp_path)["hypotheses"][0]["five_locked_baselines"]
    assert base["ALWAYS_LONG"]["correct"] == 1
    assert base["ALWAYS_SHORT"]["wrong"] == 1


def test_p914_late_or_missing_outcome_not_selected_into_pnl(tmp_path):
    r = valid(tmp_path, missing=True)
    first = r["hypotheses"][0]
    assert first["local_matured_pairs_examined"] == 0
    assert first["oracle"]["acted_on_observed_pairs"] == 0
    assert first["oracle"]["hypothetical_net_pips_sum"] == "0.0000"
    assert first["oracle"]["directional_accuracy_non_tie"] is None
    assert r["prospectively_scorable_pairs"] == 0


def test_p914_full_universe_includes_holds_failures_abstentions(tmp_path):
    r = valid(tmp_path)
    assert r["full_m5_denominator"] == 5
    assert r["hypotheses"][0]["coverage_including_hold_abstentions_and_failures"] == "0.2000"
    assert r["hypotheses"][0]["missing_or_censored_and_not_imputed"] == 4


def test_p914_predecision_momentum_and_last_candle_are_not_future_data(tmp_path):
    base = valid(tmp_path)["hypotheses"][0]["five_locked_baselines"]
    assert base["MOMENTUM_SIGN"]["correct"] == 1
    assert base["LAST_CANDLE_SIGN"]["correct"] == 1


# P9-15: hypothetical risk stress, no realized portfolio or drawdown claim.

def test_p915_stress_scenarios_share_one_horizon(tmp_path):
    h = valid(tmp_path)["hypotheses"][0]
    assert h["stress_multipliers"] == ["1.000", "1.500", "2.000"]
    assert set(h["oracle_hypothetical_stressed_sum_pips"]) == set(h["stress_multipliers"])


def test_p915_no_real_broker_cost_receipt_registered(tmp_path):
    r = valid(tmp_path)
    assert r["realized_trades_or_broker_returns_used"] == 0
    assert r["research_operational_readiness"] == "BLOCKED_EXTERNAL_EVIDENCE"


# P9-16: HOLD opportunity is separately derived from optional toy quote pair.

def test_p916_no_hold_quotes_impute_no_counterfactual(tmp_path):
    r = valid(tmp_path)["no_trade_hindsight_counterfactual"]
    assert r["locally_supplied_hypothetical_pairs"] == 0
    assert r["missing_hold_counterfactual_pairs"] == 8
    assert r["hindsight_any_side_favorable"] == 0


def test_p916_hold_quote_hindsight_never_becomes_strategy(tmp_path):
    r = valid(tmp_path, counterfactuals=True)["no_trade_hindsight_counterfactual"]
    assert r["locally_supplied_hypothetical_pairs"] == 4
    assert r["hindsight_any_side_favorable"] == 4
    assert "HINDSIGHT_UPPER_BOUND" in r["caution"]


def test_p916_hold_counterfactual_not_allowed_on_forecast(tmp_path):
    def corrupt(doc):
        c = deepcopy(doc["rows"][1]["hold_counterfactuals"][0]) if doc["rows"][1]["hold_counterfactuals"] else {
            "hypothesis_id": "INVALID", "target_at": doc["mock_clock_at"],
            "decision_quote": {}, "target_quote": {},
        }
        doc["rows"][0]["hold_counterfactuals"] = [c]
    edited(tmp_path, corrupt)


def test_p916_hold_quote_target_cannot_be_shifted(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path, counterfactuals=True)
    scenario["rows"][1]["hold_counterfactuals"][0]["target_at"] = z(
        _parse(scenario["rows"][1]["hold_counterfactuals"][0]["target_at"]) + timedelta(seconds=60))
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="target horizon"):
        assess(pp, lp, op, sp, at=at)


def test_p916_hindsight_quote_future_availability_not_usable(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path, counterfactuals=True)
    cq = scenario["rows"][1]["hold_counterfactuals"][0]["decision_quote"]
    cq["received_at"] = z(_parse(ledger["decisions"][1]["decision_at"]) + timedelta(seconds=1))
    requote(cq)
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="noncausal"):
        assess(pp, lp, op, sp, at=at)


# P9-17: explicit sample counts and fixed-bin calibration, no small-n claims.

def test_p917_five_fixed_probability_bins_and_sample_counts(tmp_path):
    r = valid(tmp_path)["hypotheses"][0]["probability_reliability"]
    assert len(r["bins"]) == 5
    assert sum(x["count"] for x in r["bins"]) == 1
    assert r["nonneutral_scored_n"] == 1
    assert r["calibration_claim_permitted"] is False


def test_p917_local_brier_and_log_loss_are_descriptive(tmp_path):
    r = valid(tmp_path)["hypotheses"][0]["probability_reliability"]
    assert r["brier_score_synthetic"] == "0.0900"
    assert Decimal(r["log_loss_synthetic"]) > Decimal("0")
    assert r["small_bins_not_conclusive"]


def test_p917_empty_calibration_has_null_not_zero():
    r = _probability_stats([])
    assert r["brier_score_synthetic"] is None
    assert r["ece_synthetic"] is None
    assert all(x["count"] == 0 for x in r["bins"])


def test_p917_probability_score_cannot_accept_zero_move_as_loss():
    with pytest.raises(OfflineScorecardError, match="Neutral"):
        _probability_stats([("0.7", "TIE")])


def test_p917_extreme_probabilities_bounded_for_log_loss():
    x = _probability_stats([("0.0000", 1), ("1.0000", 0)])
    assert Decimal(x["log_loss_synthetic"]) < Decimal("100")


def test_p917_calibration_drift_small_n_refuses_conclusion(tmp_path):
    r = valid(tmp_path)["hypotheses"][0]["calibration_drift"]
    assert r["first_half_n"] == 0
    assert r["second_half_n"] == 1
    assert r["synthetic_ece_difference"] is None
    assert r["drift_conclusion_permitted"] is False


def test_p917_single_sample_bin_not_accepted_as_confidence(tmp_path):
    r = valid(tmp_path)["hypotheses"][0]["probability_reliability"]
    assert all(x["individual_bin_inference_permitted"] is False for x in r["bins"])


# P9-18: complete case-wise hypothetical ablations, never causal seat proof.

def test_p918_ablation_without_frozen_counterfactuals_is_absent(tmp_path):
    r = valid(tmp_path)
    assert r["local_ablation_variant_count"] == 0
    assert r["hypotheses"][0]["ablation_previews"] == {}


def test_p918_ablation_preview_never_claims_retrained_model(tmp_path):
    r = valid(tmp_path, variants=True)
    x = r["hypotheses"][0]["ablation_previews"]["NO_WEAK_SEAT"]
    assert x["ablation_effect_proven"] is False
    assert x["counterfactual_model_retrained"] is False
    assert x["synthetic_counterfactual"]["acted_on_observed_pairs"] == 1


def test_p918_partial_ablation_submission_cannot_cherrypick(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path, variants=True)
    scenario["rows"][0]["ablations"].pop()
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="all hypotheses"):
        assess(pp, lp, op, sp, at=at)


def test_p918_duplicate_ablation_variant_id_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path, variants=True)
    scenario["ablation_variants"].append(deepcopy(scenario["ablation_variants"][0]))
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="Duplicate ablation"):
        assess(pp, lp, op, sp, at=at)


def test_p918_ablation_declared_after_first_decision_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path, variants=True)
    scenario["ablation_variants"][0]["frozen_at"] = ledger["decisions"][1]["decision_at"]
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="created after"):
        assess(pp, lp, op, sp, at=at)


# P9-19: time dependence can never be silently replaced by IID error bars.

def test_p919_too_few_time_blocks_prevents_interval(tmp_path):
    r = valid(tmp_path)["hypotheses"][0]["time_block_uncertainty"]
    assert r["effective_blocks"] < r["predeclared_minimum_blocks"]
    assert r["interval_status"] == "NOT_ESTIMABLE_INSUFFICIENT_BLOCKS"
    assert r["confidence_interval"] is None
    assert r["independent_edge_inference"] is False


def test_p919_empty_block_set_is_not_zero_confidence_interval():
    r = _block_status([], 86400, 10)
    assert r["effective_blocks"] == 0
    assert r["confidence_interval"] is None


def test_p919_when_blocks_sufficient_still_descriptive_only():
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    rows = [{"decision_at": today + timedelta(days=i), "net_pips": {"BUY": Decimal(i)},
             "oracle_action": "BUY"} for i in range(10)]
    r = _block_status(rows, 86400, 10)
    assert r["effective_blocks"] == 10
    assert r["interval_status"] == "DESCRIPTIVE_BLOCK_RANGE_ONLY"
    assert r["confidence_interval"] is None


def test_p919_multiple_testing_correction_not_falsely_executed(tmp_path):
    r = valid(tmp_path)
    assert r["multiple_testing_correction_executed"] is False
    assert r["independent_performance_replicates"] == 0


# P9-20: predecision-only news, regime and data-vintage context.

def test_p920_all_regimes_disclose_n_including_zero(tmp_path):
    r = valid(tmp_path)["hypotheses"][0]["regime_breakdown"]
    assert sum(x["n"] for x in r) == 1
    assert all(x["regime_claim_permitted"] is False for x in r)


def test_p920_cannot_discover_high_volatility_after_decision(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    scenario["rows"][0]["regime"] = "HIGH_VOLATILITY"
    scenario["rows"][0]["context_available_at"] = z(
        _parse(ledger["decisions"][0]["decision_at"]) + timedelta(seconds=1))
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="unavailable at decision"):
        assess(pp, lp, op, sp, at=at)


def test_p920_invalid_news_label_refused(tmp_path):
    edited(tmp_path, lambda x: x["rows"][0].update(news_status="WINNING_RELEASE_ONLY"))


def test_p920_invalid_regime_label_refused(tmp_path):
    edited(tmp_path, lambda x: x["rows"][0].update(regime="PROFITABLE_CANDLES_ONLY"))


def test_p920_macro_vintage_claim_is_never_independent(tmp_path):
    r = valid(tmp_path)
    assert r["independent_source_families_verified"] == 0
    assert "NO_INDEPENDENT_OUTCOME_PROVIDER_OR_ECONOMIC_VINTAGE_RECEIPTS" in r["blockers"]


# Global strict input identity, full-denominator, and CLI safety.

def test_global_scenario_exactly_one_per_decision_required(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    scenario["rows"].pop()
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="full M5 denominator"):
        assess(pp, lp, op, sp, at=at)


def test_global_duplicate_decision_context_rejected(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    scenario["rows"][1] = deepcopy(scenario["rows"][0])
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="duplicate decision context"):
        assess(pp, lp, op, sp, at=at)


def test_global_mock_clock_before_latest_decision_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    scenario["mock_clock_at"] = ledger["window_first_bar_at"]
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="mock clock precedes"):
        assess(pp, lp, op, sp, at=at)


def test_global_candle_after_decision_rejected(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    scenario["rows"][0]["candle_history"][2]["published_at"] = z(
        _parse(ledger["decisions"][0]["decision_at"]) + timedelta(seconds=1))
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="future, late"):
        assess(pp, lp, op, sp, at=at)


def test_global_gapped_momentum_input_rejected(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    scenario["rows"][0]["candle_history"][1]["bar_close_at"] = z(
        _parse(scenario["rows"][0]["candle_history"][1]["bar_close_at"]) - timedelta(seconds=300))
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="gapped"):
        assess(pp, lp, op, sp, at=at)


@pytest.mark.parametrize("field,value", [
    ("mode", "LIVE_SCORING"), ("historical_epoch_id", "f" * 64),
    ("final_test_opened", True),
    ("window_role", "FINAL_TEST"),
    ("protocol_candidate_sha256", "f" * 64),
    ("custody_report_sha256", "f" * 64),
    ("outcome_report_sha256", "f" * 64),
    ("contract_version", "PERFORMANCE_APPROVED"),
])
def test_global_noncausal_scenario_or_self_approval_rejected(tmp_path, field, value):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    scenario[field] = value
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="not frozen"):
        assess(pp, lp, op, sp, at=at)


def test_global_witness_receipt_claim_does_not_permit_edge(tmp_path):
    r = valid(tmp_path, witness=True)
    assert r["calibration_or_edge_proven"] is False
    assert r["prospectively_scorable_pairs"] == 0
    assert r["historical_final_test_opened"] is False


def test_global_prior_scenario_change_detects_rehashed_edit(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    earlier = tmp_path / "prior_local_scorecard.json"
    earlier.write_bytes(sp.read_bytes())
    scenario["rows"][0]["regime"] = "NEW_YORK"
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="changed after local freeze"):
        assess(pp, lp, op, sp, prior=earlier, at=at)


def test_global_prior_scenario_bytes_exactly_match(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    earlier = tmp_path / "prior_local_scorecard.json"
    earlier.write_bytes(sp.read_bytes())
    r = assess(pp, lp, op, sp, prior=earlier, at=at)
    assert r["prior_local_scenario_file_sha256"] == sha256(sp.read_bytes()).hexdigest()
    assert r["calibration_or_edge_proven"] is False


def test_global_change_original_outcome_breaks_scenario_binding(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    data["observations"][0]["observation_id"] = "OTHER_OBSERVATION"
    save(op, data)
    with pytest.raises(OfflineScorecardError, match="not frozen"):
        assess(pp, lp, op, sp, at=at)


def test_global_change_original_ledger_breaks_original_replay(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    ledger["decisions"][1]["attempt_status"] = "FAILED"
    save(lp, ledger)
    with pytest.raises(OfflineScorecardError, match="Original protocol"):
        assess(pp, lp, op, sp, at=at)


def test_global_historical_epoch_rebinding_refused():
    with pytest.raises(OfflineScorecardError, match="rebinding refused"):
        assess(epoch="f" * 64)


def test_global_policy_unchanged_and_exact_sha(tmp_path):
    before = sha256(POLICY.read_bytes()).hexdigest()
    _ = valid(tmp_path)
    assert sha256(POLICY.read_bytes()).hexdigest() == before == POLICY_SHA256


def test_global_tampered_historical_policy_refused(tmp_path):
    bad = tmp_path / "bad_policy.json"
    bad.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(OfflineScorecardError, match="SHA-256 mismatch"):
        assess(policy=bad)


def test_global_future_cutoff_refused():
    with pytest.raises(OfflineScorecardError, match="Future cutoff"):
        assess(at=z(datetime.now(UTC) + timedelta(days=1)))


def test_global_partial_chain_refused(tmp_path):
    path = tmp_path / "fake.json"
    save(path, {})
    with pytest.raises(OfflineScorecardError, match="supplied together"):
        assess(sp=path)


def test_global_duplicate_json_keys_refused(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    sp.write_text('{"mode":"SYNTHETIC_OFFLINE_SCORECARD_ONLY","mode":"LIVE"}', encoding="utf-8")
    with pytest.raises(OfflineScorecardError, match="Unsafe"):
        assess(pp, lp, op, sp, at=at)


def test_global_scenario_cannot_claim_authorization(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    scenario["approved_to_trade"] = True
    save(sp, scenario)
    with pytest.raises(OfflineScorecardError, match="Strict schema"):
        assess(pp, lp, op, sp, at=at)


def test_global_scenario_payloads_are_not_written(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    paths = (pp, lp, op, sp)
    original = [sha256(p.read_bytes()).hexdigest() for p in paths]
    result = assess(pp, lp, op, sp, at=at)
    assert [sha256(p.read_bytes()).hexdigest() for p in paths] == original
    assert result["files_written"] is False


def test_global_report_has_no_raw_quotes_broker_connections_or_trades(tmp_path):
    r = valid(tmp_path)
    text = json.dumps(r)
    assert "1.0804" not in text
    assert "synthetic_offline_scoring_demo.json" not in text
    assert r["realized_trades_or_broker_returns_used"] == 0
    assert r["network_accessed"] is False
    assert r["model_loaded"] is False
    assert r["final_action"] == "HOLD"


def test_global_cli_missing_fixture_reports_hold(capsys):
    code = run(["--read-only-scorecard", "--synthetic-only", "--epoch-id", EPOCH,
                "--as-of", cutoff(), "--policy-file", str(POLICY)])
    assert code == 0
    out = capsys.readouterr().out
    assert "FINAL=HOLD" in out
    assert "M5 denominator: 0" in out
    assert "Prospectively scorable: 0" in out


def test_global_cli_synthetic_json_never_claims_edge(tmp_path, capsys):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    code = run(["--read-only-scorecard", "--synthetic-only", "--epoch-id", EPOCH,
                "--as-of", at, "--policy-file", str(POLICY),
                "--protocol-file", str(pp), "--ledger-file", str(lp),
                "--outcome-file", str(op), "--scenario-file", str(sp), "--json"])
    assert code == 0
    r = json.loads(capsys.readouterr().out)
    assert r["local_matured_unattested_forecast_pairs"] == 1
    assert r["prospectively_scorable_pairs"] == 0
    assert r["final_action"] == "HOLD"


def test_global_cli_requires_explicit_read_only_flags():
    with pytest.raises(SystemExit) as ex:
        run(["--epoch-id", EPOCH, "--as-of", cutoff()])
    assert ex.value.code == 2


def test_global_sha_determinism_for_fixed_cutoff(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    a, b = assess(pp, lp, op, sp, at=at), assess(pp, lp, op, sp, at=at)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_global_symlink_scenario_refused_if_supported(tmp_path):
    pp, proto, lp, ledger, rp, op, data, sp, scenario, at = fixture(tmp_path)
    alias = tmp_path / "linked.json"
    try:
        alias.symlink_to(sp)
    except (NotImplementedError, OSError):
        pytest.skip("Windows host does not permit symlink creation")
    with pytest.raises(OfflineScorecardError, match="Unsafe"):
        assess(pp, lp, op, alias, at=at)
