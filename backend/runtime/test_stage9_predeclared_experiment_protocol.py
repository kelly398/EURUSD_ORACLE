
"""P9-01..04: frozen synthetic evaluation designs, no evaluation or approval.

Only these tests write their temporary JSON fixtures. The module under test
never creates a forward epoch, forecast, model, witness or trading activity.
"""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

import pytest

from backend.runtime.stage9_predeclared_experiment_protocol import (
    EPOCH, POLICY_SHA256, VERSION, PROTOCOL_VERSION, SPLIT_VERSION,
    COST_VERSION, BASELINE_VERSION, BASELINE_ORDER, BASELINE_RULES,
    EXCLUSION_CODES, PHASES, PHASE_PURPOSES, HORIZONS, MAX_HYPOTHESES,
    ExperimentProtocolError, _time, _digest, assess_stage9_experiment_protocol,
    run,
)

ROOT = Path(__file__).resolve().parents[2]
POLICY = ROOT / "docs/clock_profiles/hfm_forward_20261027.json"
UTC = timezone.utc


def z(value):
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def cutoff():
    return z(datetime.now(UTC).replace(microsecond=0))


def save(path, obj):
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return path


def fixture(tmp_path):
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    bounds = (
        (-120, -106), (-99, -85), (-78, -64), (7, 18), (27, 100),
    )
    windows = [
        {"phase": phase, "start_at": z(today + timedelta(days=start)),
         "end_at": z(today + timedelta(days=end)),
         "purpose_code": PHASE_PURPOSES[phase]}
        for phase, (start, end) in zip(PHASES, bounds)
    ]
    benchmarks = []
    for key in BASELINE_ORDER:
        rule, source, reason, missing, probability_rule = BASELINE_RULES[key]
        benchmarks.append({
            "baseline_id": key, "rule": rule, "source_families": list(source),
            "same_cutoff_rule": "SAME_M5_DECISION_CUTOFF_AS_COUNCIL",
            "same_eligibility_universe": True,
            "missing_input_disposition": missing, "rationale_code": reason,
            "probability_mapping": probability_rule,
        })
    doc = {
        "schema_version": 1, "historical_epoch_id": EPOCH,
        "experiment_id": "SYNTHETIC_PREDECLARED_RESEARCH_001",
        "mode": "SYNTHETIC_OFFLINE_PROTOCOL_CANDIDATE",
        "protocol_version": PROTOCOL_VERSION,
        "declared_at": z(today - timedelta(days=1)),
        "local_declaration_claim": "LOCAL_UNATTESTED_NO_WITNESS",
        "market": {
            "symbol": "EURUSD", "timeframe": "M5",
            "decision_clock": "COMPLETED_M5_ONLY",
            "decision_cutoff": "FIRST_AVAILABLE_NOT_AFTER_DECISION",
            "source_admission_rule": "INDEPENDENT_SOURCE_ADMISSION_REQUIRED",
            "forecast_freeze_rule": "NO_REAL_FORECAST_REGISTERED_IN_THIS_STAGE",
        },
        "eligibility": {
            "decision_universe": "EVERY_ELIGIBLE_COMPLETED_M5",
            "record_hold_failure_abstention": True,
            "source_failure_counts_in_coverage": True,
            "eligible_decisions_include_missing_forecasts": True,
            "permitted_exclusion_codes": [
                "INVALID_M5_CANDLE", "PREDECLARED_MARKET_CLOSURE", "UNVERIFIED_DECISION_CLOCK",
            ],
            "exclusions_require_independent_reason_receipts": True,
            "missing_outcome_rule": "MISSING_CENSORED_NEVER_ZERO_OR_FAVORABLE_SUBSTITUTE",
            "abstention_rule": "HOLD_IN_DENOMINATOR_NOT_DIRECTIONAL_CORRECTNESS",
            "neutral_return_rule": "ZERO_MOVE_REPORTED_SEPARATELY_NOT_DROPPED",
            "outcome_receipt_rule": "INDEPENDENT_POST_HORIZON_SOURCE_REQUIRED",
        },
        "hypotheses": [
            {"hypothesis_id": "H_P9_BRIER_1H", "horizon_seconds": 3600,
             "outcome_label": "MID_RETURN_SIGN",
             "forecast_output": "DIRECTION_AND_PROBABILITY_UP",
             "primary_metric": "BRIER_SCORE",
             "secondary_metrics": ["COVERAGE", "ABSTENTION_RATE", "DIRECTIONAL_ACCURACY"],
             "probability_threshold": "0.500",
             "no_forecast_rule": "COUNT_AS_MISSING_FORECAST_IN_ELIGIBLE_DENOMINATOR",
             "tie_rule": "COUNT_TIE_SEPARATELY_DO_NOT_DROP",
             "metric_direction": "LOWER_IS_BETTER"},
            {"hypothesis_id": "H_P9_ACCURACY_1D", "horizon_seconds": 86400,
             "outcome_label": "BID_ASK_COST_ADJUSTED_SIGN_HYPOTHETICAL",
             "forecast_output": "DIRECTION_ONLY", "primary_metric": "BALANCED_ACCURACY",
             "secondary_metrics": ["COVERAGE", "ABSTENTION_RATE", "NO_TRADE_OPPORTUNITY_RATE"],
             "probability_threshold": "NOT_APPLICABLE",
             "no_forecast_rule": "COUNT_AS_MISSING_FORECAST_IN_ELIGIBLE_DENOMINATOR",
             "tie_rule": "COUNT_TIE_SEPARATELY_DO_NOT_DROP",
             "metric_direction": "HIGHER_IS_BETTER"},
        ],
        "benchmarks": benchmarks,
        "holdout": {
            "split_version": SPLIT_VERSION, "windows": windows,
            "max_outcome_release_lag_seconds": 3600,
            "purge_seconds": 86400, "embargo_seconds": 86400,
            "event_overlap_rule": "PURGE_OVERLAPPING_HORIZONS_AND_EMBARGO_RELEASES",
            "final_test_outcomes_opened": False, "random_split_allowed": False,
            "model_selection_sees_final_test": False,
        },
        "cost_policy": {
            "cost_version": COST_VERSION,
            "quote_side_policy": "BID_ASK_SIDE_AWARE_NOT_EXECUTABLE_MID",
            "evidence_class": "HYPOTHETICAL_ASSUMPTIONS_NOT_BROKER_ORIGIN",
            "half_spread_pips": "0.450", "round_trip_commission_pips": "0.150",
            "slippage_each_side_pips": "0.250", "financing_per_day_pips": "0.080",
            "assumed_holding_days": "0.250", "latency_penalty_pips": "0.100",
            "stress_multipliers": ["1.000", "1.500", "2.000"],
            "no_trade_counterfactual_rule": "HOLD_COUNTS_IN_COVERAGE_AND_SEPARATE_OPPORTUNITY",
            "actual_broker_costs_verified": False,
        },
        "decision_thresholds": {
            "minimum_eligible_decisions": 500,
            "minimum_evaluable_predictions": 200,
            "minimum_final_test_days": 20,
            "minimum_effective_time_blocks": 10,
            "time_block_seconds": 172800,
            "minimum_coverage": "0.200", "minimum_metric_gain": "0.020",
            "minimum_practical_net_gain_pips": "0.500",
            "familywise_alpha": "0.025",
            "multiple_testing_rule": "HOLM_BONFERRONI_ALL_DECLARED_CHALLENGERS",
            "uncertainty_rule": "TIME_BLOCK_DEPENDENCE_AWARE_INTERVALS",
            "comparison_rule": "ALL_FIVE_BASELINES_IDENTICAL_ELIGIBILITY_AND_COST",
            "max_challenger_configurations": 20,
            "claim_gate": "NO_CLAIM_WITHOUT_INDEPENDENT_PROSPECTIVE_CUSTODY",
        },
    }
    return save(tmp_path / "synthetic_predeclared_protocol.json", doc), doc


def assess(path=None, prior=None, *, policy=POLICY, asof=None, epoch=EPOCH):
    return assess_stage9_experiment_protocol(
        epoch_id=epoch, as_of=cutoff() if asof is None else asof,
        policy_file=policy, protocol_file=path, prior_protocol=prior,
    )


def checked(tmp_path):
    path, _ = fixture(tmp_path)
    return assess(path)


def mutate(path, doc, **changed):
    doc.update(changed)
    save(path, doc)


def invalid(tmp_path, branch, field, value):
    path, doc = fixture(tmp_path)
    doc[branch][field] = value
    save(path, doc)
    with pytest.raises(ExperimentProtocolError):
        assess(path)


# P9-01: predeclared hypotheses, coverage and honest metric contracts.

def test_p901_missing_protocol_has_no_registered_experiment_or_witness():
    r = assess()
    assert r["candidate"] is None
    assert r["protocol_status"] == "NOT_SUPPLIED"
    assert r["registered_experiments"] == 0
    assert r["externally_timestamped_protocols"] == 0
    assert r["final_action"] == "HOLD"


def test_p901_valid_synthetic_protocol_is_not_independently_predeclared(tmp_path):
    r = checked(tmp_path)
    assert r["protocol_status"] == "LOCAL_UNATTESTED_FROZEN_CANDIDATE"
    assert r["protocol_preoutcome_status"] == "NOT_INDEPENDENTLY_ESTABLISHED"
    assert len(r["candidate"]["candidate_protocol_sha256"]) == 64
    assert r["hypotheses_evaluated"] == 0


def test_p901_two_horizons_and_metric_directions_are_frozen(tmp_path):
    r = checked(tmp_path)
    h = r["candidate"]["hypotheses"]
    assert r["candidate"]["horizons_seconds"] == [3600, 86400]
    assert h[0]["primary_metric"] == "BRIER_SCORE"
    assert h[0]["metric_direction"] == "LOWER_IS_BETTER"
    assert h[1]["primary_metric"] == "BALANCED_ACCURACY"
    assert h[1]["metric_direction"] == "HIGHER_IS_BETTER"


def test_p901_hold_failed_and_abstained_cycles_in_coverage(tmp_path):
    r = checked(tmp_path)
    assert r["candidate"]["all_eligible_m5_decisions_counted"]
    assert r["candidate"]["hold_failure_abstention_counted"]
    assert r["candidate"]["missing_outcomes_never_imputed"]


def test_p901_exclusion_codes_are_predeclared_and_bounded(tmp_path):
    r = checked(tmp_path)
    assert r["candidate"]["permitted_exclusion_codes"] == [
        "INVALID_M5_CANDLE", "PREDECLARED_MARKET_CLOSURE", "UNVERIFIED_DECISION_CLOCK"]
    assert "MISSING_OUTCOME" not in r["candidate"]["permitted_exclusion_codes"]


@pytest.mark.parametrize("field,change", [
    ("decision_universe", "ONLY_PROFITABLE_M5"),
    ("record_hold_failure_abstention", False),
    ("source_failure_counts_in_coverage", False),
    ("eligible_decisions_include_missing_forecasts", False),
    ("exclusions_require_independent_reason_receipts", False),
    ("missing_outcome_rule", "IMPUTE_ZERO"),
    ("abstention_rule", "IGNORE_HOLD"),
    ("neutral_return_rule", "DROP_ZERO_MOVES"),
    ("outcome_receipt_rule", "SELF_ISSUED"),
])
def test_p901_eligibility_denominator_cannot_be_gamed(tmp_path, field, change):
    invalid(tmp_path, "eligibility", field, change)


@pytest.mark.parametrize("bad", [
    ["MISSING_OUTCOME"], ["INVALID_M5_CANDLE", "INVALID_M5_CANDLE"],
    ["PREDECLARED_MARKET_CLOSURE", "INVALID_M5_CANDLE"],
    [], ["FAVORABLE_RETURNS_ONLY"], True,
])
def test_p901_undeclared_duplicate_or_postoutcome_exclusions_refused(tmp_path, bad):
    invalid(tmp_path, "eligibility", "permitted_exclusion_codes", bad)


@pytest.mark.parametrize("bad", [0, True, "3600", 301, 999999, -300])
def test_p901_invalid_hypothesis_horizon_refused(tmp_path, bad):
    path, doc = fixture(tmp_path)
    doc["hypotheses"][0]["horizon_seconds"] = bad
    save(path, doc)
    with pytest.raises(ExperimentProtocolError):
        assess(path)


@pytest.mark.parametrize("field,bad", [
    ("forecast_output", "LIVE_SIGNALS"), ("primary_metric", "PAPER_PROFIT"),
    ("outcome_label", "BROKER_TRADES"), ("tie_rule", "DROP"),
    ("no_forecast_rule", "EXCLUDE_FAILS"),
    ("metric_direction", "HIGHER_IS_BETTER"),
    ("probability_threshold", "0.750"),
])
def test_p901_invalid_hypothesis_semantics_rejected(tmp_path, field, bad):
    path, doc = fixture(tmp_path)
    doc["hypotheses"][0][field] = bad
    save(path, doc)
    with pytest.raises(ExperimentProtocolError):
        assess(path)


def test_p901_probability_score_cannot_attach_to_direction_only(tmp_path):
    path, doc = fixture(tmp_path)
    doc["hypotheses"][1]["primary_metric"] = "BRIER_SCORE"
    doc["hypotheses"][1]["metric_direction"] = "LOWER_IS_BETTER"
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="incompatible"):
        assess(path)


def test_p901_direction_only_cannot_sneak_probability_secondary(tmp_path):
    path, doc = fixture(tmp_path)
    doc["hypotheses"][1]["secondary_metrics"].append("LOG_LOSS")
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="cannot imply probability"):
        assess(path)


def test_p901_coverage_required_for_every_hypothesis(tmp_path):
    path, doc = fixture(tmp_path)
    doc["hypotheses"][0]["secondary_metrics"].remove("COVERAGE")
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="coverage"):
        assess(path)


def test_p901_duplicate_hypothesis_id_rejected(tmp_path):
    path, doc = fixture(tmp_path)
    doc["hypotheses"][1]["hypothesis_id"] = doc["hypotheses"][0]["hypothesis_id"]
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="Duplicate"):
        assess(path)


def test_p901_duplicate_hypothesis_configuration_rejected(tmp_path):
    path, doc = fixture(tmp_path)
    duplicate = deepcopy(doc["hypotheses"][0])
    duplicate["hypothesis_id"] = "ANOTHER_ID_SAME_DESIGN"
    doc["hypotheses"].append(duplicate)
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="Duplicated hypothesis"):
        assess(path)


def test_p901_more_than_eight_hypotheses_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["hypotheses"] = [deepcopy(doc["hypotheses"][0]) for _ in range(MAX_HYPOTHESES + 1)]
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="Bounded"):
        assess(path)


# P9-02: five fixed transparent baseline comparators at same cutoff.

def test_p902_five_locked_comparators_and_same_cutoff(tmp_path):
    r = checked(tmp_path)
    c = r["candidate"]
    assert c["benchmark_contract_version"] == BASELINE_VERSION
    assert c["mandatory_benchmarks"] == list(BASELINE_ORDER)
    assert all(x["same_cutoff_rule"] == "SAME_M5_DECISION_CUTOFF_AS_COUNCIL"
               for x in c["baseline_input_contracts"])
    assert r["benchmarks_executed"] == 0


def test_p902_no_trade_and_constant_controls_need_no_input(tmp_path):
    r = checked(tmp_path)
    controls = r["candidate"]["baseline_input_contracts"]
    assert all(x["source_families"] == [] for x in controls[:3])
    assert controls[0]["rule"] == "ALWAYS_HOLD"
    assert controls[0]["probability_mapping"] == "NEUTRAL_0_500_REFERENCE_NOT_ACTION"
    assert controls[1]["probability_mapping"] == "BOUNDED_UP_0_999"
    assert controls[2]["probability_mapping"] == "BOUNDED_UP_0_001"


def test_p902_momentum_and_single_candle_require_only_asof_candles(tmp_path):
    r = checked(tmp_path)
    c = r["candidate"]["baseline_input_contracts"]
    assert c[3]["source_families"] == ["candles"]
    assert c[4]["source_families"] == ["candles"]
    assert c[3]["probability_mapping"] == "SIGN_TO_UP_0_600_OR_0_400_NEUTRAL_0_500"
    assert r["candidate"]["baseline_probabilities_predeclared_not_calibrated"] is True


@pytest.mark.parametrize("field,bad", [
    ("rule", "AFTER_HORIZON_MOMENTUM"),
    ("source_families", ["candles", "future_candles"]),
    ("same_cutoff_rule", "AFTER_THE_FACT"),
    ("same_eligibility_universe", False),
    ("missing_input_disposition", "SKIP"),
    ("rationale_code", "BEST_BACKTEST_PROFIT"),
    ("probability_mapping", "OPTIMIZED_AFTER_RESULTS"),
])
def test_p902_benchmark_cannot_change_lookahead_or_universe(tmp_path, field, bad):
    path, doc = fixture(tmp_path)
    doc["benchmarks"][3][field] = bad
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="Benchmark"):
        assess(path)


def test_p902_missing_simple_baseline_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["benchmarks"].pop()
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="Five locked"):
        assess(path)


def test_p902_baseline_order_and_duplicate_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["benchmarks"][0], doc["benchmarks"][1] = doc["benchmarks"][1], doc["benchmarks"][0]
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="reordered"):
        assess(path)


def test_p902_no_trade_cannot_be_relabelled_profitable(tmp_path):
    path, doc = fixture(tmp_path)
    doc["benchmarks"][0]["rule"] = "EARN_POSITIVE_RETURN_WHEN_HOLD"
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="Benchmark"):
        assess(path)


# P9-03: exact, ordered holdouts with horizon-aware purge, embargo, lag.

def test_p903_five_roles_and_untouched_final_test_future(tmp_path):
    r = checked(tmp_path)
    c = r["candidate"]
    assert [w["phase"] for w in c["evaluation_windows"]] == list(PHASES)
    assert c["final_test_outcomes_opened"] is False
    assert r["final_test_outcomes_inspected"] is False
    assert c["untouched_final_test_start"]


def test_p903_required_gap_combines_max_horizon_release_and_embargo(tmp_path):
    r = checked(tmp_path)
    assert r["candidate"]["required_minimum_gap_seconds"] == 176400


def test_p903_boundary_exact_gap_is_accepted(tmp_path):
    path, doc = fixture(tmp_path)
    window = doc["holdout"]["windows"]
    previous_end = _time(window[0]["end_at"])
    window[1]["start_at"] = z(previous_end + timedelta(seconds=176400))
    save(path, doc)
    assert assess(path)["candidate"]["required_minimum_gap_seconds"] == 176400


def test_p903_insufficient_gap_one_m5_interval_refused(tmp_path):
    path, doc = fixture(tmp_path)
    window = doc["holdout"]["windows"]
    previous_end = _time(window[0]["end_at"])
    window[1]["start_at"] = z(previous_end + timedelta(seconds=176100))
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="insufficient"):
        assess(path)


def test_p903_overlapping_train_validation_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["holdout"]["windows"][1]["start_at"] = doc["holdout"]["windows"][0]["end_at"]
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="insufficient"):
        assess(path)


def test_p903_random_split_refused(tmp_path):
    invalid(tmp_path, "holdout", "random_split_allowed", True)


def test_p903_peeking_final_test_refused(tmp_path):
    invalid(tmp_path, "holdout", "final_test_outcomes_opened", True)


def test_p903_model_selection_cannot_see_final_test(tmp_path):
    invalid(tmp_path, "holdout", "model_selection_sees_final_test", True)


@pytest.mark.parametrize("field,bad", [
    ("event_overlap_rule", "NO_GAP"),
    ("split_version", "UNPURGED_V2"),
    ("max_outcome_release_lag_seconds", 999999999),
    ("purge_seconds", 299),
    ("embargo_seconds", 0),
    ("purge_seconds", True),
])
def test_p903_shortened_or_unsupported_embargo_refused(tmp_path, field, bad):
    invalid(tmp_path, "holdout", field, bad)


def test_p903_shorter_than_declared_horizon_purge_refused(tmp_path):
    invalid(tmp_path, "holdout", "purge_seconds", 3600)


def test_p903_window_role_mutation_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["holdout"]["windows"][4]["purpose_code"] = "HYPERPARAMETER_SCREEN_ONLY"
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="phase order"):
        assess(path)


def test_p903_window_order_mutation_refused(tmp_path):
    path, doc = fixture(tmp_path)
    a = doc["holdout"]["windows"]
    a[1], a[2] = a[2], a[1]
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="phase order"):
        assess(path)


def test_p903_protocol_declared_after_selection_start_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["declared_at"] = doc["holdout"]["windows"][3]["start_at"]
    save(path, doc)
    with pytest.raises(ExperimentProtocolError):
        assess(path)


def test_p903_selection_start_already_open_asof_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["holdout"]["windows"][3]["start_at"] = z(datetime.now(UTC) - timedelta(days=2))
    save(path, doc)
    with pytest.raises(ExperimentProtocolError):
        assess(path)


def test_p903_non_aligned_m5_boundary_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["holdout"]["windows"][0]["start_at"] = "2026-01-01T00:01:00Z"
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="M5 aligned"):
        assess(path)


def test_p903_zero_length_window_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["holdout"]["windows"][0]["end_at"] = doc["holdout"]["windows"][0]["start_at"]
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="positive"):
        assess(path)


# P9-04: declared hypothetical cost-side assumptions and success threshold.

def test_p904_roundtrip_cost_calculation_is_hypothetical(tmp_path):
    r = checked(tmp_path)
    assumed = r["candidate"]["predeclared_cost_assumptions"]
    assert assumed["assumed_round_trip_cost_pips"] == "1.6700"
    assert assumed["cost_stress_scenarios"] == ["1.6700", "2.5050", "3.3400"]
    assert assumed["costs_are_actual_trades"] is False
    assert r["costs_observed_from_broker"] is False


def test_p904_bid_ask_policy_not_mid_executable(tmp_path):
    r = checked(tmp_path)
    c = r["candidate"]["cost_policy"]
    assert c["quote_side_policy"] == "BID_ASK_SIDE_AWARE_NOT_EXECUTABLE_MID"
    assert c["actual_broker_costs_verified"] is False


@pytest.mark.parametrize("field,bad", [
    ("quote_side_policy", "FREE_MID_EXECUTION"),
    ("evidence_class", "ACTUAL_BROKER_PROVEN"),
    ("actual_broker_costs_verified", True),
    ("no_trade_counterfactual_rule", "DROP_HOLD"),
    ("cost_version", "AUTHORIZE_TRADING"),
])
def test_p904_costs_cannot_claim_trading_or_hide_hold(tmp_path, field, bad):
    invalid(tmp_path, "cost_policy", field, bad)


@pytest.mark.parametrize("field,bad", [
    ("half_spread_pips", "-1.00"), ("round_trip_commission_pips", 0.5),
    ("slippage_each_side_pips", "1000000"),
    ("financing_per_day_pips", "nan"),
    ("latency_penalty_pips", "0.12345"),
    ("assumed_holding_days", "0"),
])
def test_p904_cost_numeric_bounds_refused(tmp_path, field, bad):
    invalid(tmp_path, "cost_policy", field, bad)


@pytest.mark.parametrize("bad", [
    [], ["1.000", "2.000"], ["1.000", "1.000", "2.000"],
    ["0.900", "1.500", "2.000"], ["1.000", "4.000", "3.000"],
    ["1.000", 1.5, "2.000"],
])
def test_p904_stress_multiplier_monotonic_and_baseline_one(tmp_path, bad):
    invalid(tmp_path, "cost_policy", "stress_multipliers", bad)


def test_p904_tiny_cost_assumption_under_decimal_pip_floor_refused(tmp_path):
    path, doc = fixture(tmp_path)
    for x in ("half_spread_pips", "round_trip_commission_pips", "slippage_each_side_pips",
              "financing_per_day_pips", "latency_penalty_pips"):
        doc["cost_policy"][x] = "0.0001"
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="implausibly small"):
        assess(path)


def test_p904_costless_backtest_not_allowed(tmp_path):
    path, doc = fixture(tmp_path)
    for x in ("half_spread_pips", "round_trip_commission_pips", "slippage_each_side_pips",
              "financing_per_day_pips", "latency_penalty_pips"):
        doc["cost_policy"][x] = "0"
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="Zero-cost"):
        assess(path)


def test_p904_significance_and_practical_gain_both_predeclared(tmp_path):
    r = checked(tmp_path)
    t = r["candidate"]["statistical_decision_lock"]
    assert t["minimum_practical_net_gain_pips"] == "0.500"
    assert t["familywise_alpha"] == "0.025"
    assert t["multiple_testing_rule"] == "HOLM_BONFERRONI_ALL_DECLARED_CHALLENGERS"
    assert r["candidate"]["independent_statistical_power_proven"] is False


@pytest.mark.parametrize("field,bad", [
    ("minimum_eligible_decisions", 1),
    ("minimum_evaluable_predictions", 100000),
    ("minimum_final_test_days", 0),
    ("minimum_effective_time_blocks", 3),
    ("time_block_seconds", 300),
    ("minimum_coverage", "0.010"),
    ("minimum_metric_gain", "0"),
    ("minimum_practical_net_gain_pips", "0"),
    ("familywise_alpha", "0.200"),
    ("max_challenger_configurations", True),
    ("max_challenger_configurations", 1000),
    ("multiple_testing_rule", "NONE"),
    ("uncertainty_rule", "IID_ONLY"),
    ("comparison_rule", "PICK_WINNING_BASELINE_ONLY"),
    ("claim_gate", "LOCAL_BEST_SCORE_IS_PROOF"),
])
def test_p904_unsafe_statistical_or_practical_success_lock_refused(tmp_path, field, bad):
    invalid(tmp_path, "decision_thresholds", field, bad)


def test_p904_final_test_duration_must_cover_minimum_blocks(tmp_path):
    path, doc = fixture(tmp_path)
    window = doc["holdout"]["windows"][-1]
    window["end_at"] = z(_time(window["start_at"]) + timedelta(days=5))
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="too short"):
        assess(path)


# Local candidate identity, file safety, historical boundary, CLI.

def test_p904_identical_local_snapshot_compared_without_false_custody(tmp_path):
    path, doc = fixture(tmp_path)
    prior = tmp_path / "prior_local_copy.json"
    prior.write_bytes(path.read_bytes())
    r = assess(path, prior)
    assert r["same_local_protocol_bytes"] is True
    assert r["local_protocol_snapshot_is_independent_receipt"] is False
    assert r["externally_timestamped_protocols"] == 0


def test_p904_local_freeze_rejects_changed_metric_even_if_rehashed(tmp_path):
    path, doc = fixture(tmp_path)
    prior = tmp_path / "prior.json"
    prior.write_bytes(path.read_bytes())
    doc["decision_thresholds"]["minimum_metric_gain"] = "0.030"
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="new experiment ID"):
        assess(path, prior)


def test_p904_local_freeze_refuses_reformatting_existing_candidate_bytes(tmp_path):
    path, doc = fixture(tmp_path)
    prior = tmp_path / "prior.json"
    prior.write_bytes(path.read_bytes())
    path.write_bytes(json.dumps(doc, sort_keys=True, separators=(",", ":")).encode())
    with pytest.raises(ExperimentProtocolError, match="new experiment ID"):
        assess(path, prior)


def test_p904_new_experiment_id_is_new_unattested_candidate(tmp_path):
    path, doc = fixture(tmp_path)
    old = assess(path)["candidate"]["candidate_protocol_sha256"]
    doc["experiment_id"] = "SYNTHETIC_DIFFERENT_EXPERIMENT_002"
    save(path, doc)
    new = assess(path)
    assert new["candidate"]["candidate_protocol_sha256"] != old
    assert new["registered_experiments"] == 0


def test_p904_same_protocol_and_cutoff_has_deterministic_digest(tmp_path):
    path, doc = fixture(tmp_path)
    at = cutoff()
    a, b = assess(path, asof=at), assess(path, asof=at)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})


def test_p904_experiment_receipt_cannot_be_injected_into_candidate(tmp_path):
    path, doc = fixture(tmp_path)
    doc["registered_experiment_id"] = "FAKE_EXTERNAL_RECEIPT"
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="Strict schema"):
        assess(path)


def test_p904_identical_path_cannot_claim_prior_snapshot(tmp_path):
    path, doc = fixture(tmp_path)
    with pytest.raises(ExperimentProtocolError, match="Distinct local"):
        assess(path, prior=path)


def test_p904_prior_without_candidate_refused(tmp_path):
    path, doc = fixture(tmp_path)
    with pytest.raises(ExperimentProtocolError, match="cannot stand"):
        assess(prior=path)


def test_p904_unwitnessed_declaration_cannot_be_independent_proof(tmp_path):
    r = checked(tmp_path)
    assert r["statistical_edge_demonstrated"] is False
    assert r["outcomes_or_forecasts_loaded"] is False
    assert r["stage8_git_baseline_independently_verified"] is False
    assert r["final_action"] == "HOLD"


def test_p904_policy_original_bytes_untouched(tmp_path):
    before = sha256(POLICY.read_bytes()).hexdigest()
    _ = checked(tmp_path)
    assert before == sha256(POLICY.read_bytes()).hexdigest() == POLICY_SHA256


def test_p904_historical_epoch_rebinding_refused():
    with pytest.raises(ExperimentProtocolError, match="rebinding"):
        assess(epoch="f" * 64)


def test_p904_historical_policy_tamper_refused(tmp_path):
    bad = tmp_path / "changed_clock_policy.json"
    bad.write_bytes(POLICY.read_bytes() + b" ")
    with pytest.raises(ExperimentProtocolError, match="SHA-256"):
        assess(policy=bad)


@pytest.mark.parametrize("bad", [
    "2026-10-30T11:00:00", "2026-10-30T14:00:00+03:00",
    "2026-10-30T11:00:60Z", "2026-11-31T11:00:00Z", "nonsense", 42,
])
def test_p904_strict_canonical_utc_required(bad):
    with pytest.raises(ExperimentProtocolError):
        _time(bad)


def test_p904_future_review_cutoff_refused():
    future = z(datetime.now(UTC) + timedelta(days=1))
    with pytest.raises(ExperimentProtocolError, match="Future review"):
        assess(asof=future)


def test_p904_declared_after_review_cutoff_refused(tmp_path):
    path, doc = fixture(tmp_path)
    doc["declared_at"] = z(datetime.now(UTC) + timedelta(days=1))
    save(path, doc)
    with pytest.raises(ExperimentProtocolError, match="Future/backdated"):
        assess(path)


def test_p904_duplicate_json_key_rejected(tmp_path):
    path = tmp_path / "duplicate.json"
    path.write_text('{"mode":"SYNTHETIC_OFFLINE_PROTOCOL_CANDIDATE","mode":"LIVE"}')
    with pytest.raises(ExperimentProtocolError, match="Unsafe"):
        assess(path)


def test_p904_oversized_protocol_refused(tmp_path):
    path = tmp_path / "oversized.json"
    path.write_bytes(b"X" * 65537)
    with pytest.raises(ExperimentProtocolError, match="Unsafe"):
        assess(path)


def test_p904_protocol_symlink_rejected_if_supported(tmp_path):
    path, doc = fixture(tmp_path)
    alias = tmp_path / "alias_protocol.json"
    try:
        alias.symlink_to(path)
    except (OSError, NotImplementedError):
        pytest.skip("Host does not permit symlink creation")
    with pytest.raises(ExperimentProtocolError, match="Unsafe"):
        assess(alias)


def test_p904_no_evaluation_side_effects_when_candidate_supplied(tmp_path):
    path, doc = fixture(tmp_path)
    original_bytes = path.read_bytes()
    r = assess(path)
    assert path.read_bytes() == original_bytes
    for flag in (
        "files_written", "network_accessed", "broker_connected", "collector_started",
        "council_invoked", "model_loaded", "training_authorized", "settlement_invoked",
        "paper_orders_authorized", "live_orders_authorized", "operator_approval_granted",
        "historical_epoch_rebound", "future_epoch_created", "final_test_outcomes_inspected",
        "statistical_edge_demonstrated", "outcomes_or_forecasts_loaded",
    ):
        assert r[flag] is False
    assert r["final_action"] == "HOLD"


def test_p904_no_candidate_cli_prints_hold(capsys):
    assert run([
        "--read-only-protocol", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", cutoff(), "--policy-file", str(POLICY),
    ]) == 0
    out = capsys.readouterr().out
    assert "Candidate: NOT_SUPPLIED" in out
    assert "FINAL=HOLD" in out
    assert "Independent protocol witnesses: 0" in out


def test_p904_candidate_cli_json_still_has_no_registration(tmp_path, capsys):
    path, doc = fixture(tmp_path)
    assert run([
        "--read-only-protocol", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", cutoff(), "--policy-file", str(POLICY),
        "--protocol-file", str(path), "--json",
    ]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["candidate"]["hypothesis_count"] == 2
    assert r["registered_experiments"] == 0
    assert r["final_action"] == "HOLD"


def test_p904_cli_rejects_missing_required_safety_flags():
    with pytest.raises(SystemExit) as exc:
        run(["--epoch-id", EPOCH, "--as-of", cutoff()])
    assert exc.value.code == 2


def test_p904_cli_future_cutoff_fails_closed(capsys):
    future = z(datetime.now(UTC) + timedelta(days=1))
    assert run([
        "--read-only-protocol", "--synthetic-only", "--epoch-id", EPOCH,
        "--as-of", future, "--policy-file", str(POLICY),
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_p904_no_sensitive_raw_fixture_or_real_market_data_in_report(tmp_path):
    report = checked(tmp_path)
    body = json.dumps(report)
    assert "account_number" not in body
    assert '"bid":' not in body
    assert '"ask":' not in body
    assert "2026-10-30T10:03:00Z" not in body
    assert "final_action" in body


def test_p904_no_protocol_cli_hash_deterministic_for_same_cutoff():
    at = cutoff()
    a, b = assess(asof=at), assess(asof=at)
    assert a == b
    assert a["report_sha256"] == _digest({k: v for k, v in a.items() if k != "report_sha256"})
