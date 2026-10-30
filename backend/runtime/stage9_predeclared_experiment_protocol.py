
"""P9-01..04: validate frozen *candidate* EURUSD evaluation plans, offline.

A predeclared experiment cannot be proven by a local timestamp or hash. This
read-only module accepts synthetic local protocol candidates, rejects changed
local snapshots, and reports the exact benchmark, horizon, split, embargo and
cost contracts that future independent reviewers would need to witness.

No predictions, outcomes, evaluation, models, workers, broker connections,
network, file writes, new epoch, trading or shadow execution. FINAL=HOLD.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError,
    _read, _decode, _digest, _ident, _utc,
)
from backend.runtime.stage8_recovery_observability_handover import (
    VERSION as STAGE8_HANDOVER_CONTRACT,
)

VERSION = "EURUSD_STAGE9_PREDECLARED_PROTOCOL_P9_01_04_V1"
PROTOCOL_VERSION = "EURUSD_PROSPECTIVE_EVALUATION_CANDIDATE_V1"
BASELINE_VERSION = "SAME_ASOF_FIVE_BASELINE_POLICY_V1"
SPLIT_VERSION = "CAUSAL_PURGED_EMBARGOED_WINDOWS_V1"
COST_VERSION = "HYPOTHETICAL_BID_ASK_COST_LOCK_V1"
MAX_PROTOCOL_BYTES = 65536
MAX_HYPOTHESES = 8
HORIZONS = (300, 900, 3600, 14400, 86400)
UTC = timezone.utc
DECIMAL = re.compile(r"^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,4})?$")
PROBABILITY = re.compile(r"^(?:0(?:\.[0-9]{1,4})?|1(?:\.0{1,4})?)$")
TIME = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
PROTOCOL_KEYS = {
    "schema_version", "historical_epoch_id", "experiment_id", "mode",
    "protocol_version", "declared_at", "local_declaration_claim",
    "market", "eligibility", "hypotheses", "benchmarks", "holdout",
    "cost_policy", "decision_thresholds",
}
MARKET_KEYS = {
    "symbol", "timeframe", "decision_clock", "decision_cutoff",
    "source_admission_rule", "forecast_freeze_rule",
}
ELIGIBILITY_KEYS = {
    "decision_universe", "record_hold_failure_abstention", "source_failure_counts_in_coverage",
    "eligible_decisions_include_missing_forecasts", "permitted_exclusion_codes",
    "exclusions_require_independent_reason_receipts", "missing_outcome_rule",
    "abstention_rule", "neutral_return_rule", "outcome_receipt_rule",
}
EXCLUSION_CODES = {
    "PREDECLARED_MARKET_CLOSURE", "UNVERIFIED_DECISION_CLOCK",
    "INVALID_M5_CANDLE", "INDEPENDENT_PROVIDER_OUTAGE",
    "PREDECLARED_HOLIDAY", "PREDECISION_DATA_INELIGIBLE",
}
HYPOTHESIS_KEYS = {
    "hypothesis_id", "horizon_seconds", "outcome_label", "forecast_output",
    "primary_metric", "secondary_metrics", "probability_threshold",
    "no_forecast_rule", "tie_rule", "metric_direction",
}
OUTPUTS = ("DIRECTION_ONLY", "PROBABILITY_UP_ONLY", "DIRECTION_AND_PROBABILITY_UP")
METRICS = (
    "BALANCED_ACCURACY", "DIRECTIONAL_ACCURACY", "BRIER_SCORE", "LOG_LOSS",
    "COVERAGE", "ABSTENTION_RATE", "NO_TRADE_OPPORTUNITY_RATE",
)
PRIMARY_METRICS = {"BALANCED_ACCURACY", "DIRECTIONAL_ACCURACY", "BRIER_SCORE", "LOG_LOSS"}
PROBABILITY_METRICS = {"BRIER_SCORE", "LOG_LOSS"}
BENCHMARK_KEYS = {
    "baseline_id", "rule", "source_families", "same_cutoff_rule",
    "same_eligibility_universe", "missing_input_disposition", "rationale_code",
    "probability_mapping",
}
BASELINE_ORDER = (
    "NO_TRADE", "ALWAYS_LONG", "ALWAYS_SHORT", "MOMENTUM_SIGN", "LAST_CANDLE_SIGN",
)
BASELINE_RULES = {
    "NO_TRADE": ("ALWAYS_HOLD", (), "NULL_ACTION_CONTROL", "NOT_APPLICABLE",
                 "NEUTRAL_0_500_REFERENCE_NOT_ACTION"),
    "ALWAYS_LONG": ("BUY_EVERY_ELIGIBLE", (), "DIRECTIONAL_BASE_RATE_CONTROL",
                    "NOT_APPLICABLE", "BOUNDED_UP_0_999"),
    "ALWAYS_SHORT": ("SELL_EVERY_ELIGIBLE", (), "DIRECTIONAL_BASE_RATE_CONTROL",
                     "NOT_APPLICABLE", "BOUNDED_UP_0_001"),
    "MOMENTUM_SIGN": (
        "THREE_COMPLETED_M5_CLOSE_MOMENTUM", ("candles",),
        "SIMPLE_MOMENTUM_CONTROL", "HOLD_AND_COUNT_MISSING_INPUT",
        "SIGN_TO_UP_0_600_OR_0_400_NEUTRAL_0_500",
    ),
    "LAST_CANDLE_SIGN": (
        "LAST_COMPLETED_M5_BODY_SIGN", ("candles",),
        "SINGLE_BAR_STRUCTURE_CONTROL", "HOLD_AND_COUNT_MISSING_INPUT",
        "SIGN_TO_UP_0_600_OR_0_400_NEUTRAL_0_500",
    ),
}
HOLDOUT_KEYS = {
    "split_version", "windows", "max_outcome_release_lag_seconds",
    "purge_seconds", "embargo_seconds", "event_overlap_rule",
    "final_test_outcomes_opened", "random_split_allowed", "model_selection_sees_final_test",
}
WINDOW_KEYS = {"phase", "start_at", "end_at", "purpose_code"}
PHASES = (
    "TRAIN", "VALIDATION", "CALIBRATION", "CHALLENGER_SELECTION", "FINAL_TEST",
)
PHASE_PURPOSES = {
    "TRAIN": "MODEL_AND_FEATURE_FIT_ONLY",
    "VALIDATION": "HYPERPARAMETER_SCREEN_ONLY",
    "CALIBRATION": "PROBABILITY_CALIBRATION_ONLY",
    "CHALLENGER_SELECTION": "FROZEN_CHALLENGER_SELECTION_ONLY",
    "FINAL_TEST": "UNTOUCHED_PROSPECTIVE_EVALUATION_ONLY",
}
COST_KEYS = {
    "cost_version", "quote_side_policy", "evidence_class", "half_spread_pips",
    "round_trip_commission_pips", "slippage_each_side_pips",
    "financing_per_day_pips", "assumed_holding_days", "latency_penalty_pips",
    "stress_multipliers", "no_trade_counterfactual_rule", "actual_broker_costs_verified",
}
THRESHOLD_KEYS = {
    "minimum_eligible_decisions", "minimum_evaluable_predictions",
    "minimum_final_test_days", "minimum_effective_time_blocks",
    "time_block_seconds", "minimum_coverage", "minimum_metric_gain",
    "minimum_practical_net_gain_pips", "familywise_alpha",
    "multiple_testing_rule", "uncertainty_rule", "comparison_rule",
    "max_challenger_configurations", "claim_gate",
}


class ExperimentProtocolError(ValueError):
    """Untrusted, changed or causally invalid local experiment design."""


def _strict(doc, fields, name):
    if not isinstance(doc, dict) or set(doc) != fields:
        raise ExperimentProtocolError("Strict schema mismatch: " + name)


def _id(value, label):
    try:
        return _ident(value, label)
    except OfflineHeartbeatError:
        raise ExperimentProtocolError("Invalid experiment identifier: " + label) from None


def _time(value):
    if not isinstance(value, str) or not TIME.fullmatch(value):
        raise ExperimentProtocolError("Canonical Z UTC timestamp required.")
    try:
        return _utc(value)
    except (OfflineHeartbeatError, TypeError, ValueError):
        raise ExperimentProtocolError("Invalid Z UTC timestamp.") from None


def _int(value, label, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ExperimentProtocolError("Bounded integer required: " + label)
    return value


def _dec(value, label, upper, *, probability=False, positive=False):
    pattern = PROBABILITY if probability else DECIMAL
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ExperimentProtocolError("Bounded decimal string required: " + label)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ExperimentProtocolError("Invalid decimal: " + label) from None
    if not number.is_finite() or number > Decimal(str(upper)) or (positive and number <= 0):
        raise ExperimentProtocolError("Decimal outside permitted bound: " + label)
    return number


def _file(path):
    try:
        raw = _read(path, MAX_PROTOCOL_BYTES)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, UnicodeError, ValueError, TypeError):
        raise ExperimentProtocolError("Unsafe or invalid local protocol file.") from None


def _market(item):
    _strict(item, MARKET_KEYS, "market_cutoff_contract")
    expected = {
        "symbol": "EURUSD", "timeframe": "M5",
        "decision_clock": "COMPLETED_M5_ONLY",
        "decision_cutoff": "FIRST_AVAILABLE_NOT_AFTER_DECISION",
        "source_admission_rule": "INDEPENDENT_SOURCE_ADMISSION_REQUIRED",
        "forecast_freeze_rule": "NO_REAL_FORECAST_REGISTERED_IN_THIS_STAGE",
    }
    if item != expected:
        raise ExperimentProtocolError("Unsupported EURUSD/M5 causal market policy.")
    return expected


def _eligibility(item):
    _strict(item, ELIGIBILITY_KEYS, "decision_eligibility")
    expected = {
        "decision_universe": "EVERY_ELIGIBLE_COMPLETED_M5",
        "record_hold_failure_abstention": True,
        "source_failure_counts_in_coverage": True,
        "eligible_decisions_include_missing_forecasts": True,
        "exclusions_require_independent_reason_receipts": True,
        "missing_outcome_rule": "MISSING_CENSORED_NEVER_ZERO_OR_FAVORABLE_SUBSTITUTE",
        "abstention_rule": "HOLD_IN_DENOMINATOR_NOT_DIRECTIONAL_CORRECTNESS",
        "neutral_return_rule": "ZERO_MOVE_REPORTED_SEPARATELY_NOT_DROPPED",
        "outcome_receipt_rule": "INDEPENDENT_POST_HORIZON_SOURCE_REQUIRED",
    }
    if any((item.get(k) is not v) if type(v) is bool else (item.get(k) != v)
           for k, v in expected.items()):
        raise ExperimentProtocolError("Coverage, missingness or abstention rule weakened.")
    excluded = item["permitted_exclusion_codes"]
    if (not isinstance(excluded, list) or not 1 <= len(excluded) <= len(EXCLUSION_CODES)
            or any(type(x) is not str or x not in EXCLUSION_CODES for x in excluded)
            or len(set(excluded)) != len(excluded)
            or excluded != sorted(excluded)):
        raise ExperimentProtocolError("Invalid predeclared exclusion codes.")
    return dict(item)


def _hypotheses(items):
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_HYPOTHESES:
        raise ExperimentProtocolError("Bounded nonempty hypotheses required.")
    ids, identities, horizons = set(), set(), []
    for item in items:
        _strict(item, HYPOTHESIS_KEYS, "research_hypothesis")
        hid = _id(item["hypothesis_id"], "hypothesis_id")
        if hid in ids:
            raise ExperimentProtocolError("Duplicate hypothesis ID.")
        ids.add(hid)
        h = _int(item["horizon_seconds"], "horizon_seconds", 300, 86400)
        if h not in HORIZONS:
            raise ExperimentProtocolError("Unsupported declared forecast horizon.")
        horizons.append(h)
        if item["outcome_label"] not in ("MID_RETURN_SIGN", "BID_ASK_COST_ADJUSTED_SIGN_HYPOTHETICAL"):
            raise ExperimentProtocolError("Unsupported predeclared outcome label.")
        output = item["forecast_output"]
        metric = item["primary_metric"]
        if output not in OUTPUTS or metric not in PRIMARY_METRICS:
            raise ExperimentProtocolError("Unknown forecast output or primary metric.")
        if (output == "DIRECTION_ONLY" and metric in PROBABILITY_METRICS
                or output == "PROBABILITY_UP_ONLY" and metric not in PROBABILITY_METRICS):
            raise ExperimentProtocolError("Primary metric incompatible with forecast output.")
        secondary = item["secondary_metrics"]
        if (not isinstance(secondary, list) or not 1 <= len(secondary) <= len(METRICS)
                or any(type(x) is not str or x not in METRICS for x in secondary)
                or len(secondary) != len(set(secondary))
                or "COVERAGE" not in secondary or "ABSTENTION_RATE" not in secondary):
            raise ExperimentProtocolError("Required coverage and abstention metrics absent.")
        if output == "DIRECTION_ONLY":
            if item["probability_threshold"] != "NOT_APPLICABLE" or any(
                x in PROBABILITY_METRICS for x in secondary
            ):
                raise ExperimentProtocolError("Directional-only metric cannot imply probability.")
        elif item["probability_threshold"] != "0.500":
            raise ExperimentProtocolError("Probability cutoff must be locked at 0.500.")
        if (item["no_forecast_rule"] != "COUNT_AS_MISSING_FORECAST_IN_ELIGIBLE_DENOMINATOR"
                or item["tie_rule"] != "COUNT_TIE_SEPARATELY_DO_NOT_DROP"
                or item["metric_direction"] != (
                    "LOWER_IS_BETTER" if metric in PROBABILITY_METRICS else "HIGHER_IS_BETTER"
                )):
            raise ExperimentProtocolError("Metric direction or no-prediction rule changed.")
        ident = (h, item["outcome_label"], output, metric)
        if ident in identities:
            raise ExperimentProtocolError("Duplicated hypothesis specification.")
        identities.add(ident)
    return horizons


def _benchmarks(items):
    if not isinstance(items, list) or len(items) != len(BASELINE_ORDER):
        raise ExperimentProtocolError("Five locked benchmarks required.")
    if [x.get("baseline_id") if isinstance(x, dict) else None for x in items] != list(BASELINE_ORDER):
        raise ExperimentProtocolError("Missing, reordered or duplicate benchmark.")
    for item in items:
        _strict(item, BENCHMARK_KEYS, "predeclared_baseline")
        rule, sources, reason, missing, probability_map = BASELINE_RULES[item["baseline_id"]]
        if (item["rule"] != rule or item["source_families"] != list(sources)
                or item["same_cutoff_rule"] != "SAME_M5_DECISION_CUTOFF_AS_COUNCIL"
                or item["same_eligibility_universe"] is not True
                or item["missing_input_disposition"] != missing
                or item["rationale_code"] != reason
                or item["probability_mapping"] != probability_map):
            raise ExperimentProtocolError("Benchmark changed cutoff, inputs or rationale.")


def _holdout(item, horizon_max, declared, cutoff):
    _strict(item, HOLDOUT_KEYS, "purged_holdout_contract")
    if (item["split_version"] != SPLIT_VERSION
            or item["event_overlap_rule"] != "PURGE_OVERLAPPING_HORIZONS_AND_EMBARGO_RELEASES"
            or item["final_test_outcomes_opened"] is not False
            or item["random_split_allowed"] is not False
            or item["model_selection_sees_final_test"] is not False):
        raise ExperimentProtocolError("Unblinded or unpurged holdout policy refused.")
    lag = _int(item["max_outcome_release_lag_seconds"], "release_lag_seconds", 0, 604800)
    purge = _int(item["purge_seconds"], "purge_seconds", 300, 604800)
    embargo = _int(item["embargo_seconds"], "embargo_seconds", 300, 604800)
    if purge < horizon_max or embargo < lag:
        raise ExperimentProtocolError("Embargo/purge shorter than outcome horizon or release lag.")
    gap = horizon_max + lag + max(purge, embargo)
    windows = item["windows"]
    if not isinstance(windows, list) or len(windows) != 5:
        raise ExperimentProtocolError("Exactly five split windows required.")
    prior_end, periods = None, []
    for index, window in enumerate(windows):
        _strict(window, WINDOW_KEYS, "evaluation_window")
        phase = PHASES[index]
        if (window["phase"] != phase or window["purpose_code"] != PHASE_PURPOSES[phase]):
            raise ExperimentProtocolError("Holdout phase order or role was changed.")
        start, end = _time(window["start_at"]), _time(window["end_at"])
        if (start >= end or start.second or end.second or start.microsecond or end.microsecond
                or start.minute % 5 or end.minute % 5):
            raise ExperimentProtocolError("Split window must be positive and M5 aligned.")
        if prior_end is not None and (start - prior_end).total_seconds() < gap:
            raise ExperimentProtocolError("Overlapping windows or insufficient horizon/release embargo.")
        prior_end = end
        periods.append((start, end))
    if not (declared <= cutoff < periods[3][0] and declared < periods[4][0]):
        raise ExperimentProtocolError("Experiment must be declared before future selection/test windows.")
    return periods, gap


def _costs(item):
    _strict(item, COST_KEYS, "predeclared_hypothetical_costs")
    if (item["cost_version"] != COST_VERSION
            or item["quote_side_policy"] != "BID_ASK_SIDE_AWARE_NOT_EXECUTABLE_MID"
            or item["evidence_class"] != "HYPOTHETICAL_ASSUMPTIONS_NOT_BROKER_ORIGIN"
            or item["no_trade_counterfactual_rule"] != "HOLD_COUNTS_IN_COVERAGE_AND_SEPARATE_OPPORTUNITY"
            or item["actual_broker_costs_verified"] is not False):
        raise ExperimentProtocolError("Execution-cost assumptions disguised as broker evidence.")
    parts = {}
    for name in (
        "half_spread_pips", "round_trip_commission_pips", "slippage_each_side_pips",
        "financing_per_day_pips", "latency_penalty_pips",
    ):
        parts[name] = _dec(item[name], name, "50")
    days = _dec(item["assumed_holding_days"], "assumed_holding_days", "30", positive=True)
    stressed = item["stress_multipliers"]
    if (not isinstance(stressed, list) or len(stressed) != 3
            or any(not isinstance(x, str) for x in stressed)):
        raise ExperimentProtocolError("Exactly three declared cost stress multipliers required.")
    factors = [_dec(x, "stress_multiplier", "10", positive=True) for x in stressed]
    if factors[0] != 1 or not factors[0] < factors[1] < factors[2]:
        raise ExperimentProtocolError("Cost stress multipliers must increase from 1.000.")
    round_trip = (parts["half_spread_pips"] * 2
                  + parts["round_trip_commission_pips"]
                  + parts["slippage_each_side_pips"] * 2
                  + parts["financing_per_day_pips"] * days
                  + parts["latency_penalty_pips"])
    if round_trip < Decimal("0.1000"):
        raise ExperimentProtocolError("Zero-cost or implausibly small cost assumption refused.")
    return {
        "assumed_round_trip_cost_pips": format(round_trip.quantize(Decimal("0.0001")), "f"),
        "cost_stress_scenarios": [
            format((round_trip * factor).quantize(Decimal("0.0001")), "f")
            for factor in factors
        ],
        "costs_are_provider_verified": False,
        "costs_are_actual_trades": False,
    }


def _thresholds(item, periods, horizon_max, lag):
    _strict(item, THRESHOLD_KEYS, "research_success_decision_lock")
    if (item["multiple_testing_rule"] != "HOLM_BONFERRONI_ALL_DECLARED_CHALLENGERS"
            or item["uncertainty_rule"] != "TIME_BLOCK_DEPENDENCE_AWARE_INTERVALS"
            or item["comparison_rule"] != "ALL_FIVE_BASELINES_IDENTICAL_ELIGIBILITY_AND_COST"
            or item["claim_gate"] != "NO_CLAIM_WITHOUT_INDEPENDENT_PROSPECTIVE_CUSTODY"):
        raise ExperimentProtocolError("Statistical comparison or independent proof rule weakened.")
    eligible = _int(item["minimum_eligible_decisions"], "minimum_eligible_decisions", 200, 10000000)
    evaluable = _int(item["minimum_evaluable_predictions"], "minimum_evaluable_predictions", 100, eligible)
    days = _int(item["minimum_final_test_days"], "minimum_final_test_days", 5, 365)
    blocks = _int(item["minimum_effective_time_blocks"], "minimum_time_blocks", 10, 365)
    length = _int(item["time_block_seconds"], "time_block_seconds", 3600, 604800)
    _int(item["max_challenger_configurations"], "max_challenger_configs", 1, 100)
    if length < horizon_max + lag:
        raise ExperimentProtocolError("Time blocks cannot be shorter than label horizon and lag.")
    duration = (periods[-1][1] - periods[-1][0]).total_seconds()
    if duration < max(days * 86400, blocks * length):
        raise ExperimentProtocolError("Final test window too short for locked sample-time requirements.")
    coverage = _dec(item["minimum_coverage"], "minimum_coverage", 1, probability=True, positive=True)
    if coverage < Decimal("0.05"):
        raise ExperimentProtocolError("Coverage floor cannot be under five percent.")
    _dec(item["minimum_metric_gain"], "minimum_metric_gain", 1, probability=True, positive=True)
    _dec(item["minimum_practical_net_gain_pips"], "minimum_practical_net_gain_pips", 100, positive=True)
    alpha = _dec(item["familywise_alpha"], "familywise_alpha", 1, probability=True, positive=True)
    if alpha > Decimal("0.05"):
        raise ExperimentProtocolError("Familywise alpha exceeds predeclared ceiling.")
    return {"minimum_sample_bound_locally_consistent": True, "independent_statistical_power_proven": False}


def _protocol(doc, cutoff):
    _strict(doc, PROTOCOL_KEYS, "frozen_experiment_candidate")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH
            or doc["mode"] != "SYNTHETIC_OFFLINE_PROTOCOL_CANDIDATE"
            or doc["protocol_version"] != PROTOCOL_VERSION
            or doc["local_declaration_claim"] != "LOCAL_UNATTESTED_NO_WITNESS"):
        raise ExperimentProtocolError("Historical epoch or non-authorizing protocol scope mismatch.")
    _id(doc["experiment_id"], "experiment_id")
    declared = _time(doc["declared_at"])
    if declared > cutoff:
        raise ExperimentProtocolError("Future/backdated-as-of protocol declaration refused.")
    _market(doc["market"])
    _eligibility(doc["eligibility"])
    horizons = _hypotheses(doc["hypotheses"])
    _benchmarks(doc["benchmarks"])
    periods, embargo_gap = _holdout(doc["holdout"], max(horizons), declared, cutoff)
    prices = _costs(doc["cost_policy"])
    power = _thresholds(doc["decision_thresholds"], periods, max(horizons),
                        doc["holdout"]["max_outcome_release_lag_seconds"])
    return {
        "candidate_protocol_sha256": _digest(doc),
        "experiment_id": doc["experiment_id"],
        "declared_at_local_claim": declared.isoformat(),
        "hypothesis_count": len(horizons),
        "horizons_seconds": sorted(set(horizons)),
        "hypotheses": [dict(x) for x in doc["hypotheses"]],
        "benchmark_contract_version": BASELINE_VERSION,
        "mandatory_benchmarks": list(BASELINE_ORDER),
        "baseline_input_contracts": [{
            "baseline_id": x["baseline_id"],
            "rule": x["rule"], "source_families": list(x["source_families"]),
            "same_cutoff_rule": x["same_cutoff_rule"],
            "probability_mapping": x["probability_mapping"],
        } for x in doc["benchmarks"]],
        "baseline_probabilities_predeclared_not_calibrated": True,
        "all_eligible_m5_decisions_counted": True,
        "hold_failure_abstention_counted": True,
        "permitted_exclusion_codes": list(doc["eligibility"]["permitted_exclusion_codes"]),
        "missing_outcomes_never_imputed": True,
        "split_contract_version": SPLIT_VERSION,
        "evaluation_windows": [{
            "phase": x["phase"], "start_at": x["start_at"], "end_at": x["end_at"]
        } for x in doc["holdout"]["windows"]],
        "required_minimum_gap_seconds": embargo_gap,
        "untouched_final_test_start": periods[-1][0].isoformat(),
        "untouched_final_test_end": periods[-1][1].isoformat(),
        "final_test_outcomes_opened": False,
        "cost_contract_version": COST_VERSION,
        "cost_policy": dict(doc["cost_policy"]),
        "predeclared_cost_assumptions": prices,
        "statistical_decision_lock": dict(doc["decision_thresholds"]),
        **power,
    }


def assess_stage9_experiment_protocol(*, epoch_id, as_of,
                                       policy_file=POLICY_PATH,
                                       protocol_file=None, prior_protocol=None):
    if epoch_id != EPOCH:
        raise ExperimentProtocolError("Historical epoch rebinding refused.")
    cutoff = _time(as_of)
    if cutoff > datetime.now(UTC):
        raise ExperimentProtocolError("Future review cutoff refused.")
    try:
        pinned = sha256(_read(policy_file, 16384)).hexdigest()
    except (OfflineHeartbeatError, OSError):
        raise ExperimentProtocolError("Historical policy bytes unavailable or unsafe.") from None
    if pinned != POLICY_SHA256:
        raise ExperimentProtocolError("Immutable historical policy SHA-256 mismatch.")
    if protocol_file is None and prior_protocol is not None:
        raise ExperimentProtocolError("Local prior snapshot cannot stand in for candidate.")
    candidate = None
    candidate_file_sha = None
    local_prior_sha = None
    same_bytes = False
    if protocol_file is not None:
        doc, candidate_file_sha = _file(protocol_file)
        candidate = _protocol(doc, cutoff)
        if prior_protocol is not None:
            earlier, local_prior_sha = _file(prior_protocol)
            if Path(protocol_file).absolute() == Path(prior_protocol).absolute():
                raise ExperimentProtocolError("Distinct local prior snapshot file required.")
            # This is comparison of LOCAL bytes only, not proof of an earlier
            # timestamp, immutable external storage, registration or custody.
            if earlier != doc or local_prior_sha != candidate_file_sha:
                raise ExperimentProtocolError(
                    "Previously frozen local protocol differs; new experiment ID required."
                )
            same_bytes = True
    blockers = [
        "STAGE7_EXTERNAL_GATES_0_OF_11",
        "THIRTEEN_INDEPENDENT_SOURCE_ADMISSIONS_0_OF_13",
        "NO_INDEPENDENT_PREOUTCOME_PROTOCOL_TIMESTAMP_WITNESS",
        "NO_PROSPECTIVE_REGISTERED_FORECASTS_OR_OUTCOME_RECEIPTS",
        "NO_INDEPENDENT_BROKER_COST_OR_REAL_TRADE_EVIDENCE",
        "NO_UNTOUCHED_PROSPECTIVE_TEST_RESULTS_ADMITTED",
        "NO_OPERATOR_APPROVAL_OR_SHADOW_SCOPE",
    ]
    if candidate is None:
        blockers.append("NO_SYNTHETIC_EXPERIMENT_PROTOCOL_SUPPLIED")
    if prior_protocol is None:
        blockers.append("NO_LOCAL_FROZEN_PROTOCOL_SNAPSHOT_SUPPLIED")
    report = {
        "version": VERSION, "scope": "P9_01_TO_P9_04_PREDECLARED_EXPERIMENT_CANDIDATE",
        "historical_epoch_id": EPOCH, "as_of": cutoff.isoformat(),
        "historical_policy_sha256": pinned,
        "stage8_handover_contract_version": STAGE8_HANDOVER_CONTRACT,
        "stage8_operator_reported_baseline_commit": "7b07586448ccef6b9c0bfabd65e2e1e29c2a4329",
        "stage8_git_baseline_independently_verified": False,
        "mode": "READ_ONLY_SYNTHETIC_PROTOCOL_REVIEW",
        "protocol_status": ("NOT_SUPPLIED" if candidate is None
                            else "LOCAL_UNATTESTED_FROZEN_CANDIDATE"),
        "protocol_file_sha256": candidate_file_sha,
        "local_prior_protocol_file_sha256": local_prior_sha,
        "same_local_protocol_bytes": same_bytes,
        "candidate": candidate,
        "registered_experiments": 0,
        "externally_timestamped_protocols": 0,
        "protocol_preoutcome_status": "NOT_INDEPENDENTLY_ESTABLISHED",
        "final_test_outcomes_inspected": False,
        "outcomes_or_forecasts_loaded": False,
        "hypotheses_evaluated": 0,
        "benchmarks_executed": 0,
        "costs_observed_from_broker": False,
        "statistical_edge_demonstrated": False,
        "local_protocol_snapshot_is_independent_receipt": False,
        "read_only": True, "files_written": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_loaded": False,
        "training_authorized": False, "settlement_invoked": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "operator_approval_granted": False, "historical_epoch_rebound": False,
        "future_epoch_created": False,
        "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-protocol", action="store_true", required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--protocol-file")
    parser.add_argument("--prior-protocol")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_stage9_experiment_protocol(
            epoch_id=args.epoch_id, as_of=args.as_of, policy_file=args.policy_file,
            protocol_file=args.protocol_file, prior_protocol=args.prior_protocol,
        )
    except (ExperimentProtocolError, OfflineHeartbeatError, OSError, TypeError,
            ValueError, KeyError, OverflowError):
        print("STAGE 9 PROTOCOL BLOCKED: Unsafe, changed or noncausal local candidate.")
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 9 P9-01..04 predeclared research protocol")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Candidate: " + report["protocol_status"])
        count = 0 if report["candidate"] is None else report["candidate"]["hypothesis_count"]
        print("Declared hypotheses: %d | Benchmarks executed: 0" % count)
        print("Independent protocol witnesses: 0 | Registered experiments: 0")
        print("Forecasts/outcomes loaded: 0 | Broker cost receipts: 0")
        print("Independent gates/families: 0/11 | 0/13")
        for reason in report["blockers"]:
            print("  BLOCKER " + reason)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO PROTOCOL REGISTRATION | NO MODEL | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
