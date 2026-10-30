
"""P9-13..20: bounded synthetic *diagnostics*, never admissible edge evidence.

Consumes the exact Stage 9 protocol/custody/outcome chain and a read-only,
full-denominator local scenario. The scenario can exercise fair five-baseline
cost accounting, HOLD counterfactuals, calibration, drawdown, regime buckets,
predeclared ablation previews, and dependence-aware insufficiency checks.

No real forecast or outcome receipt has been independently verified by the
upstream chain. Therefore ALL results are synthetic demonstrations; they must
never be presented as a prospective result or a trading approval. No training,
models, agents, providers, network, disk writes, order adapters or broker.
FINAL=HOLD always.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import math

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError, _read, _decode, _digest, _ident, _utc,
)
from backend.runtime.stage9_predeclared_experiment_protocol import (
    ExperimentProtocolError, BASELINE_ORDER, _time, assess_stage9_experiment_protocol,
)
from backend.runtime.stage9_forecast_custody_reconciliation import (
    ForecastCustodyError, assess_stage9_forecast_custody,
)
from backend.runtime.stage9_outcome_maturation_reconciliation import (
    OutcomeMaturationError, _quote, assess_stage9_outcome_maturation,
)

VERSION = "EURUSD_STAGE9_OFFLINE_SCORECARD_P9_13_20_V1"
SCENARIO_VERSION = "SYNTHETIC_FULL_DENOMINATOR_SCORE_SCENARIO_V1"
MAX_SCENARIO_BYTES = 262144
MAX_ROWS = 96
MAX_VARIANTS = 4
PIP = Decimal("10000")
ZERO = Decimal("0")
TWO = Decimal("2")
FOUR = Decimal("4")
ONE = Decimal("1")
Q = Decimal("0.0001")
UTC = timezone.utc
REGIMES = ("ASIAN_QUIET", "LONDON", "NEW_YORK", "HIGH_VOLATILITY",
           "RATES_DIVERGENCE", "LIQUIDITY_GAP", "LARGE_SPREAD", "UNKNOWN")
NEWS = ("NO_KNOWN_RELEASE", "PREDECLARED_RELEASE", "UNAVAILABLE_AT_DECISION")
SCENARIO_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode", "contract_version",
    "experiment_id", "protocol_candidate_sha256", "custody_report_sha256",
    "outcome_report_sha256", "mock_clock_at", "window_role", "final_test_opened",
    "rows", "ablation_variants",
}
ROW_KEYS = {
    "decision_key", "regime", "news_status", "context_available_at",
    "context_claim_sha256", "candle_history", "hold_counterfactuals", "ablations",
}
CANDLE_KEYS = {"bar_close_at", "published_at", "open", "close"}
HOLD_KEYS = {"hypothesis_id", "target_at", "decision_quote", "target_quote"}
VARIANT_KEYS = {"variant_id", "definition_sha256", "frozen_at", "removed_seat_ids"}
ABLATION_KEYS = {"variant_id", "hypothesis_id", "direction"}
ABSTAIN = "HOLD"


class OfflineScorecardError(ValueError):
    """Malformed, noncausal or falsely authorized local scorecard evidence."""


def _strict(value, keys, title):
    if not isinstance(value, dict) or set(value) != keys:
        raise OfflineScorecardError("Strict schema mismatch: " + title)


def _id(value, title):
    try:
        return _ident(value, title)
    except (OfflineHeartbeatError, ValueError, TypeError):
        raise OfflineScorecardError("Unsafe identifier: " + title) from None


def _hex(value, title):
    if not isinstance(value, str) or len(value) != 64 or any(
        char not in "0123456789abcdef" for char in value
    ):
        raise OfflineScorecardError("Lowercase SHA-256 required: " + title)
    return value


def _clock(value):
    try:
        return _time(value)
    except (ExperimentProtocolError, TypeError, ValueError):
        raise OfflineScorecardError("Canonical Z UTC timestamp required.") from None


def _report_clock(value):
    try:
        return _utc(value)
    except (OfflineHeartbeatError, TypeError, ValueError):
        raise OfflineScorecardError("Invalid previously validated internal UTC time.") from None


def _file(path, maximum):
    try:
        raw = _read(path, maximum)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, ValueError, TypeError, UnicodeError):
        raise OfflineScorecardError("Unsafe, oversized or invalid local research fixture.") from None


def _number(value, title, maxval=10):
    if not isinstance(value, str):
        raise OfflineScorecardError("Decimal string required: " + title)
    from backend.runtime.stage9_outcome_maturation_reconciliation import DECIMAL
    if not DECIMAL.fullmatch(value):
        raise OfflineScorecardError("Canonical decimal string required: " + title)
    try:
        num = Decimal(value)
    except InvalidOperation:
        raise OfflineScorecardError("Invalid decimal string: " + title) from None
    if not num.is_finite() or num <= 0 or num > Decimal(str(maxval)):
        raise OfflineScorecardError("Decimal out of bounds: " + title)
    return num


def _s(number):
    if number is None:
        return None
    if isinstance(number, Decimal):
        return format(number.quantize(Q), "f")
    return format(Decimal(str(number)).quantize(Q), "f")


def _safe_ratio(a, b):
    return None if b == 0 else _s(Decimal(a) / Decimal(b))


def _mid(quote):
    return (_number(quote["bid"], "bid") + _number(quote["ask"], "ask")) / TWO


def _direction(sign):
    return "BUY" if sign > 0 else "SELL" if sign < 0 else "HOLD"


def _calculation(entry, target, cost, action, factor):
    """Synthetic quote-side crossing plus scaled assumptions; never broker P&L."""
    if action == "HOLD":
        return ZERO
    fee = (Decimal(cost["round_trip_commission_pips"])
           + TWO * Decimal(cost["slippage_each_side_pips"])
           + Decimal(cost["financing_per_day_pips"]) * Decimal(cost["assumed_holding_days"])
           + Decimal(cost["latency_penalty_pips"]))
    move = (_mid(target) - _mid(entry)) * PIP
    entry_half = (_number(entry["ask"], "ask") - _number(entry["bid"], "bid")) * PIP / TWO
    exit_half = (_number(target["ask"], "ask") - _number(target["bid"], "bid")) * PIP / TWO
    # Unlike a separate assumed spread line, this uses the two actual
    # LOCAL SYNTHETIC quote-side spreads. Do not charge them again.
    crossing = entry_half + exit_half
    return (move if action == "BUY" else -move) - factor * (crossing + fee)


def _history(values, bar, decision):
    if not isinstance(values, list) or len(values) not in (0, 3):
        raise OfflineScorecardError("Exactly three or zero completed candle inputs required.")
    if not values:
        return {"MOMENTUM_SIGN": ABSTAIN, "LAST_CANDLE_SIGN": ABSTAIN}, False
    previous = None
    for index, candle in enumerate(values):
        _strict(candle, CANDLE_KEYS, "completed_m5_baseline_candle")
        closed = _clock(candle["bar_close_at"])
        available = _clock(candle["published_at"])
        if (closed > bar or available < closed or available > decision
                or (previous is not None and (closed - previous).total_seconds() != 300)):
            raise OfflineScorecardError("Baseline candle is future, late, gapped or backdated.")
        _number(candle["open"], "candle_open")
        _number(candle["close"], "candle_close")
        previous = closed
        if index == 2 and closed != bar:
            raise OfflineScorecardError("Last baseline candle must be the completed M5 bar.")
    momentum = Decimal(values[-1]["close"]) - Decimal(values[0]["close"])
    last_body = Decimal(values[-1]["close"]) - Decimal(values[-1]["open"])
    return {"MOMENTUM_SIGN": _direction(momentum),
            "LAST_CANDLE_SIGN": _direction(last_body)}, True


def _scenario(path, protocol, custody, outcome, cutoff):
    doc, byte_sha = _file(path, MAX_SCENARIO_BYTES)
    _strict(doc, SCENARIO_KEYS, "offline_scorecard_scenario")
    _id(doc["packet_id"], "packet_id")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH
            or doc["mode"] != "SYNTHETIC_OFFLINE_SCORECARD_ONLY"
            or doc["contract_version"] != SCENARIO_VERSION
            or doc["experiment_id"] != protocol["experiment_id"]
            or doc["protocol_candidate_sha256"] != protocol["candidate_protocol_sha256"]
            or doc["custody_report_sha256"] != custody["report_sha256"]
            or doc["outcome_report_sha256"] != outcome["report_sha256"]
            or doc["window_role"] != "SYNTHETIC_DESIGN_DEMONSTRATION_NOT_A_HOLDOUT"
            or doc["final_test_opened"] is not False
            or _clock(doc["mock_clock_at"]) > cutoff):
        raise OfflineScorecardError("Synthetic scenario not frozen to original research chain.")
    source_rows = custody["decisions"]
    if _clock(doc["mock_clock_at"]) < max(
            _report_clock(r["decision_at"]) if r["decision_at"] is not None
            else _report_clock(r["bar_close_at"]) for r in source_rows):
        raise OfflineScorecardError("Scenario mock clock precedes source decision.")
    submitted = doc["rows"]
    if (not isinstance(submitted, list) or len(submitted) != len(source_rows)
            or len(submitted) > MAX_ROWS):
        raise OfflineScorecardError("Exact full M5 denominator must be present in scenario.")
    variants = doc["ablation_variants"]
    if not isinstance(variants, list) or len(variants) > MAX_VARIANTS:
        raise OfflineScorecardError("Bounded predeclared ablation variant set required.")
    variant_ids = set()
    for variant in variants:
        _strict(variant, VARIANT_KEYS, "unverified_ablation_definition")
        vid = _id(variant["variant_id"], "ablation_variant_id")
        if vid in variant_ids:
            raise OfflineScorecardError("Duplicate ablation variant definition.")
        variant_ids.add(vid)
        _hex(variant["definition_sha256"], "ablation_definition_sha256")
        if not isinstance(variant["removed_seat_ids"], list) or not variant["removed_seat_ids"]:
            raise OfflineScorecardError("Ablation requires an explicit nonempty seat removal set.")
        if (len(variant["removed_seat_ids"]) > 31
                or any(not isinstance(s, str) for s in variant["removed_seat_ids"])
                or len(set(variant["removed_seat_ids"])) != len(variant["removed_seat_ids"])):
            raise OfflineScorecardError("Duplicate/invalid ablation seat identifiers.")
        for seat in variant["removed_seat_ids"]:
            _id(seat, "ablation_seat_id")
        if _clock(variant["frozen_at"]) > min(
                _report_clock(r["bar_close_at"]) for r in source_rows):
            raise OfflineScorecardError("Ablation variant created after decision universe began.")
    indices = {r["decision_key"]: r for r in source_rows}
    seen, clean = set(), {}
    expected_ablations = {(v, h["hypothesis_id"]) for v in variant_ids
                          for h in protocol["hypotheses"]}
    for row in submitted:
        _strict(row, ROW_KEYS, "synthetic_decision_context")
        key = _hex(row["decision_key"], "scenario_decision_key")
        if key not in indices or key in seen:
            raise OfflineScorecardError("Missing, unexpected or duplicate decision context.")
        seen.add(key)
        source = indices[key]
        bar = _report_clock(source["bar_close_at"])
        if source["decision_at"] is None:
            # Missing M5 record must remain counted. It cannot gain an outcome,
            # candles or seat actions from untrusted post-hoc scenario data.
            if (row["candle_history"] or row["hold_counterfactuals"] or row["ablations"]):
                raise OfflineScorecardError("Missing decision cannot obtain fabricated context.")
            decision = bar
        else:
            decision = _report_clock(source["decision_at"])
        if (row["regime"] not in REGIMES or row["news_status"] not in NEWS
                or _clock(row["context_available_at"]) > decision):
            raise OfflineScorecardError("Regime/news information unavailable at decision.")
        _hex(row["context_claim_sha256"], "local_context_digest")
        baseline, available = _history(row["candle_history"], bar, decision)
        hold = row["hold_counterfactuals"]
        if not isinstance(hold, list) or len(hold) > len(protocol["hypotheses"]):
            raise OfflineScorecardError("Invalid hold counterfactual count.")
        if hold and source["attempt_status"] not in ("HOLD", "ABSTAIN", "FAILED", "NO_FORECAST"):
            raise OfflineScorecardError("HOLD counterfactual only for recorded nonforecast decisions.")
        hmap = {h["hypothesis_id"]: h for h in protocol["hypotheses"]}
        hold_map = {}
        for candidate in hold:
            _strict(candidate, HOLD_KEYS, "synthetic_hold_counterfactual")
            hid = candidate["hypothesis_id"]
            if hid not in hmap or hid in hold_map:
                raise OfflineScorecardError("Unknown or duplicate HOLD counterfactual horizon.")
            h = hmap[hid]
            target = decision + timedelta(seconds=h["horizon_seconds"])
            if _clock(candidate["target_at"]) != target or target > cutoff:
                raise OfflineScorecardError("HOLD counterfactual cannot change target horizon.")
            try:
                q0 = _quote(candidate["decision_quote"], cutoff)
                q1 = _quote(candidate["target_quote"], cutoff)
            except (OutcomeMaturationError, TypeError, KeyError):
                raise OfflineScorecardError("Unsafe hold counterfactual quote origin or timing.") from None
            if (q0["observed_at"] > decision or q0["received_at"] > decision
                    or q1["observed_at"] < target or q1["observed_at"] > target + timedelta(seconds=30)
                    or q0["provider_id"] != q1["provider_id"]
                    or q1["received_at"] > target + timedelta(hours=1)
                    or max(q0["uncertainty_ms"], q1["uncertainty_ms"]) > Decimal("250")):
                raise OfflineScorecardError("Future or noncausal HOLD quote cannot be scored.")
            hold_map[hid] = candidate
        ablations = row["ablations"]
        if not isinstance(ablations, list) or len(ablations) != len(expected_ablations):
            raise OfflineScorecardError("All ablation variants must cover all hypotheses and decisions.")
        found = set()
        variant_map = {}
        for item in ablations:
            _strict(item, ABLATION_KEYS, "local_ablation_action")
            pair = (item["variant_id"], item["hypothesis_id"])
            if pair not in expected_ablations or pair in found or item["direction"] not in (
                    "BUY", "SELL", "HOLD"):
                raise OfflineScorecardError("Invalid, duplicate or selectively omitted ablation action.")
            if source["attempt_status"] == "MISSING_DECISION_RECORD" and item["direction"] != "HOLD":
                raise OfflineScorecardError("Missing decision cannot gain ablation forecast.")
            found.add(pair)
            variant_map[pair] = item["direction"]
        clean[key] = {
            "regime": row["regime"], "news_status": row["news_status"],
            "baseline": baseline, "candles_available": available,
            "hold": hold_map, "ablations": variant_map,
        }
    return clean, byte_sha, sorted(variant_ids)


def _observed_quotes(path, matrix):
    if path is None:
        return {}
    doc, _ = _file(path, 262144)
    original = {}
    for obj in doc["observations"]:
        pair = (obj["decision_key"], obj["hypothesis_id"])
        original.setdefault(pair, []).append(obj)
    resolved = {}
    for item in matrix:
        if item["maturation_state"] != "LOCALLY_MATURED_UNATTESTED_NOT_SCORABLE":
            continue
        pair = (item["decision_key"], item["hypothesis_id"])
        candidates = original.get(pair, [])
        if len(candidates) != 1 or item["local_quote_hashes_only"] is None:
            raise OfflineScorecardError("Observed quote does not uniquely match verified synthetic matrix.")
        entry, target = candidates[0]["decision_quote"], candidates[0]["target_quote"]
        hashes = item["local_quote_hashes_only"]
        if (_digest({k: v for k, v in entry.items() if k != "source_sha256"}) != hashes["decision_quote_sha256"]
                or _digest({k: v for k, v in target.items() if k != "source_sha256"}) != hashes["target_quote_sha256"]):
            raise OfflineScorecardError("Quote digest conflicts with original maturation report.")
        resolved[pair] = (entry, target)
    return resolved


def _directional_stats(actions, observed, *, denom):
    correct = wrong = tie = holding = 0
    returns = []
    probabilities = []
    for item in observed:
        action = actions(item)
        if action == "HOLD":
            holding += 1
            continue
        if item["actual_direction"] == "HOLD":
            tie += 1
        elif action == item["actual_direction"]:
            correct += 1
        else:
            wrong += 1
        returns.append(item["net_pips"][action])
    cumulative = ZERO
    highwater = ZERO
    max_drawdown = ZERO
    streak = longest_streak = 0
    for value in returns:
        cumulative += value
        highwater = max(highwater, cumulative)
        max_drawdown = max(max_drawdown, highwater - cumulative)
        streak = streak + 1 if value < 0 else 0
        longest_streak = max(longest_streak, streak)
    return {
        "observed_outcome_pairs": len(observed), "full_m5_denominator": denom,
        "acted_on_observed_pairs": len(returns), "held_on_observed_pairs": holding,
        "correct": correct, "wrong": wrong, "ties_separate": tie,
        "directional_accuracy_non_tie": _safe_ratio(correct, correct + wrong),
        "full_universe_coverage_not_inferred": True,
        "hypothetical_net_pips_sum": _s(sum(returns, ZERO)),
        "hypothetical_max_drawdown_pips": _s(max_drawdown),
        "hypothetical_longest_loss_streak": longest_streak,
        "risk_statistics_are_trade_records": False,
    }


def _probability_stats(rows, *, min_bin=30):
    bins = [{"bin": f"{i*20}-{(i+1)*20}%", "count": 0,
             "up_events": 0, "sum_probability": ZERO} for i in range(5)]
    loss = ZERO
    ll = 0.0
    for p, outcome in rows:
        if outcome not in (0, 1):
            raise OfflineScorecardError("Neutral returns cannot be relabeled binary winners.")
        p = Decimal(str(p))
        if p < 0 or p > 1:
            raise OfflineScorecardError("Probability outside [0,1].")
        idx = min(4, int(p * 5))
        bins[idx]["count"] += 1
        bins[idx]["up_events"] += outcome
        bins[idx]["sum_probability"] += p
        loss += (p - Decimal(outcome)) ** 2
        bounded = min(0.9999, max(0.0001, float(p)))
        ll -= math.log(bounded if outcome else 1.0 - bounded)
    ece = ZERO
    detailed = []
    for item in bins:
        n = item["count"]
        mean = item["sum_probability"] / n if n else None
        realized = Decimal(item["up_events"]) / n if n else None
        if n:
            ece += Decimal(n) / len(rows) * abs(mean - realized)
        detailed.append({
            "bin": item["bin"], "count": n, "up_events": item["up_events"],
            "mean_probability": _s(mean), "observed_up_rate": _s(realized),
            "individual_bin_inference_permitted": n >= min_bin,
        })
    return {
        "nonneutral_scored_n": len(rows),
        "brier_score_synthetic": None if not rows else _s(loss / len(rows)),
        "log_loss_synthetic": None if not rows else _s(ll / len(rows)),
        "ece_synthetic": None if not rows else _s(ece),
        "bins": detailed,
        "calibration_claim_permitted": len(rows) >= 5 * min_bin and all(
            x["count"] >= min_bin for x in detailed),
        "small_bins_not_conclusive": True,
    }


def _baseline_probability(name, action):
    if name == "NO_TRADE":
        return "0.500"  # reference probability, explicitly not a trade
    if name == "ALWAYS_LONG":
        return "0.999"
    if name == "ALWAYS_SHORT":
        return "0.001"
    return "0.600" if action == "BUY" else "0.400" if action == "SELL" else "0.500"


def _block_status(observed, block_seconds, minimum):
    blocks = {}
    for entry in observed:
        key = int(entry["decision_at"].timestamp()) // block_seconds
        blocks.setdefault(key, []).append(entry)
    if len(blocks) < minimum:
        return {"method": "NONOVERLAPPING_UTC_TIME_BLOCKS", "effective_blocks": len(blocks),
                "predeclared_minimum_blocks": minimum, "interval_status": "NOT_ESTIMABLE_INSUFFICIENT_BLOCKS",
                "confidence_interval": None, "independent_edge_inference": False}
    # Descriptive block-mean range only; no IID standard error or prospective p-value.
    means = []
    for records in blocks.values():
        values = [r["net_pips"].get(r["oracle_action"], ZERO) for r in records]
        means.append(sum(values, ZERO) / len(values))
    return {"method": "NONOVERLAPPING_UTC_TIME_BLOCKS", "effective_blocks": len(blocks),
            "predeclared_minimum_blocks": minimum, "interval_status": "DESCRIPTIVE_BLOCK_RANGE_ONLY",
            "confidence_interval": None, "descriptive_min_block_pips": _s(min(means)),
            "descriptive_max_block_pips": _s(max(means)),
            "independent_edge_inference": False}


def assess_stage9_offline_scorecard(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                       protocol_file=None, prior_protocol=None,
                                       ledger_file=None, prior_ledger=None,
                                       receipt_candidates=None, outcome_file=None,
                                       prior_outcome=None, scenario_file=None,
                                       prior_scenario=None):
    if epoch_id != EPOCH:
        raise OfflineScorecardError("Historical epoch rebinding refused.")
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise OfflineScorecardError("Future cutoff refused.")
    try:
        pin = sha256(_read(policy_file, 16384)).hexdigest()
    except (OfflineHeartbeatError, OSError):
        raise OfflineScorecardError("Pinned policy unavailable or unsafe.") from None
    if pin != POLICY_SHA256:
        raise OfflineScorecardError("Pinned historical clock policy SHA-256 mismatch.")
    provided = (protocol_file, ledger_file, outcome_file, scenario_file)
    if any(x is not None for x in provided) and not all(x is not None for x in provided):
        raise OfflineScorecardError("Protocol, ledger, outcome, scenario must be supplied together.")
    if prior_scenario is not None and scenario_file is None:
        raise OfflineScorecardError("Prior scenario cannot substitute current scenario.")
    proto, custody, outcome = None, None, None
    rows, scenario_sha, variants = {}, None, []
    prior_sha = None
    if all(x is not None for x in provided):
        try:
            proto_report = assess_stage9_experiment_protocol(
                epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
                protocol_file=protocol_file, prior_protocol=prior_protocol,
            )
            custody = assess_stage9_forecast_custody(
                epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
                protocol_file=protocol_file, prior_protocol=prior_protocol,
                ledger_file=ledger_file, prior_ledger=prior_ledger,
                receipt_candidates=receipt_candidates,
            )
            outcome = assess_stage9_outcome_maturation(
                epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
                protocol_file=protocol_file, prior_protocol=prior_protocol,
                ledger_file=ledger_file, prior_ledger=prior_ledger,
                receipt_candidates=receipt_candidates, outcome_file=outcome_file,
                prior_outcome=prior_outcome,
            )
        except (ExperimentProtocolError, ForecastCustodyError, OutcomeMaturationError,
                OSError, ValueError, TypeError, KeyError):
            raise OfflineScorecardError("Original protocol/custody/outcome failed replay.") from None
        proto = proto_report["candidate"]
        rows, scenario_sha, variants = _scenario(scenario_file, proto, custody, outcome, cutoff)
        if prior_scenario is not None:
            if str(scenario_file) == str(prior_scenario):
                raise OfflineScorecardError("Distinct prior local scenario required.")
            _, prior_sha = _file(prior_scenario, MAX_SCENARIO_BYTES)
            if prior_sha != scenario_sha:
                raise OfflineScorecardError("Scenario changed after local freeze.")
    sample_stats = []
    forecast_refs = {}
    if custody is not None:
        ledger, _ = _file(ledger_file, 262144)
        forecast_refs = {r["decision_key"]: r["forecast"] for r in ledger["decisions"]}
        actuals = _observed_quotes(outcome_file, outcome["evaluation_matrix"])
        costs = proto["cost_policy"]
        factors = [Decimal(x) for x in costs["stress_multipliers"]]
        row_lookup = {r["decision_key"]: r for r in custody["decisions"]}
        for matrix in outcome["evaluation_matrix"]:
            key, hid = matrix["decision_key"], matrix["hypothesis_id"]
            if (key, hid) not in actuals:
                continue
            entry, target = actuals[(key, hid)]
            decision = row_lookup[key]
            original = forecast_refs.get(key)
            if (original is None or original["hypothesis_id"] != hid):
                raise OfflineScorecardError("Scenario attempted scoring without frozen candidate forecast.")
            direction = original["direction"]
            if direction is None:
                # A probability-only candidate maps to direction at predeclared
                # 0.500 threshold, but it never becomes an executable order.
                direction = _direction(Decimal(original["probability_up"]) - Decimal("0.5"))
            diff = _mid(target) - _mid(entry)
            choices = {action: _calculation(entry, target, costs, action, factors[0])
                       for action in ("BUY", "SELL", "HOLD")}
            baselines = {"NO_TRADE": "HOLD", "ALWAYS_LONG": "BUY", "ALWAYS_SHORT": "SELL",
                         **rows[key]["baseline"]}
            sample_stats.append({
                "decision_key": key, "hypothesis_id": hid,
                "decision_at": _report_clock(decision["decision_at"]),
                "regime": rows[key]["regime"], "news_status": rows[key]["news_status"],
                "oracle_action": direction, "original_probability": original["probability_up"],
                "actual_direction": _direction(diff), "net_pips": choices,
                "stress_pips": {str(f): _calculation(entry, target, costs, direction, f)
                                for f in factors},
                "baseline_actions": baselines, "ablations": rows[key]["ablations"],
            })
    studies = []
    counts = []
    held_counterfactual = {"locally_supplied_hypothetical_pairs": 0,
                           "hindsight_both_sides_unfavorable": 0,
                           "hindsight_any_side_favorable": 0,
                           "missing_hold_counterfactual_pairs": 0,
                           "caution": "HINDSIGHT_UPPER_BOUND_LOCAL_SYNTHETIC_NOT_AN_ATTAINABLE_STRATEGY"}
    if proto is not None:
        costs = proto["cost_policy"]
        factors = [Decimal(x) for x in costs["stress_multipliers"]]
        for decision in custody["decisions"]:
            if decision["attempt_status"] not in ("HOLD", "ABSTAIN", "FAILED", "NO_FORECAST"):
                continue
            for h in proto["hypotheses"]:
                c = rows[decision["decision_key"]]["hold"].get(h["hypothesis_id"])
                if c is None:
                    held_counterfactual["missing_hold_counterfactual_pairs"] += 1
                    continue
                held_counterfactual["locally_supplied_hypothetical_pairs"] += 1
                b = _calculation(c["decision_quote"], c["target_quote"], costs, "BUY", ONE)
                s = _calculation(c["decision_quote"], c["target_quote"], costs, "SELL", ONE)
                held_counterfactual["hindsight_both_sides_unfavorable"] += int(max(b, s) <= 0)
                held_counterfactual["hindsight_any_side_favorable"] += int(max(b, s) > 0)
        denominator = custody["coverage"]["research_denominator_including_missing_and_holds"]
        for hypo in proto["hypotheses"]:
            hid = hypo["hypothesis_id"]
            observed = [x for x in sample_stats if x["hypothesis_id"] == hid]
            oracle = _directional_stats(lambda x: x["oracle_action"], observed, denom=denominator)
            baseline_stats = {name: _directional_stats(
                lambda x, name=name: x["baseline_actions"][name], observed, denom=denominator,
            ) for name in BASELINE_ORDER}
            probs = [(x["original_probability"], int(x["actual_direction"] == "BUY"))
                     for x in observed if x["original_probability"] is not None
                     and x["actual_direction"] != "HOLD"]
            reliability = _probability_stats(probs)
            baseline_probabilities = {
                name: _probability_stats([
                    (_baseline_probability(name, x["baseline_actions"][name]),
                     int(x["actual_direction"] == "BUY"))
                    for x in observed if x["actual_direction"] != "HOLD"
                ]) for name in BASELINE_ORDER
            }
            chronological = sorted(observed, key=lambda x: x["decision_at"])
            chronological_probs = [(x["original_probability"],
                                    int(x["actual_direction"] == "BUY"))
                                   for x in chronological if x["original_probability"] is not None
                                   and x["actual_direction"] != "HOLD"]
            halfway = len(chronological_probs) // 2
            first_part = _probability_stats(chronological_probs[:halfway])
            second_part = _probability_stats(chronological_probs[halfway:])
            enough_for_drift = halfway >= 30 and len(chronological_probs) - halfway >= 30
            drift = {
                "first_half_n": halfway,
                "second_half_n": len(chronological_probs) - halfway,
                "synthetic_ece_difference": (
                    _s(abs(Decimal(first_part["ece_synthetic"]) -
                           Decimal(second_part["ece_synthetic"]))) if enough_for_drift else None
                ),
                "drift_conclusion_permitted": False,
                "min_half_n_for_descriptive_drift": 30,
            }
            regimes = []
            for name in REGIMES:
                region = [x for x in observed if x["regime"] == name]
                regimes.append({"regime": name, "n": len(region),
                                "oracle": _directional_stats(lambda x: x["oracle_action"], region,
                                                             denom=denominator),
                                "regime_claim_permitted": False})
            ablations = {}
            for v in variants:
                contrast = _directional_stats(
                    lambda x, v=v, hid=hid: x["ablations"][(v, hid)], observed, denom=denominator)
                ablations[v] = {"synthetic_counterfactual": contrast,
                                "ablation_effect_proven": False,
                                "counterfactual_model_retrained": False,
                                "same_sample_definition": "LOCALLY_MATURED_CANDIDATE_OUTCOMES_ONLY"}
            thresholds = proto["statistical_decision_lock"]
            studies.append({
                "hypothesis_id": hid, "metric": hypo["primary_metric"],
                "full_m5_denominator": denominator,
                "oracle_candidate_forecasts": sum(x["synthetic_forecast"] is not None
                    and x["synthetic_forecast"]["hypothesis_id"] == hid
                    for x in custody["decisions"]),
                "local_matured_pairs_examined": len(observed),
                "coverage_including_hold_abstentions_and_failures": _safe_ratio(
                    sum(x["synthetic_forecast"] is not None
                        and x["synthetic_forecast"]["hypothesis_id"] == hid
                        for x in custody["decisions"]), denominator),
                "missing_or_censored_and_not_imputed": denominator - len(observed),
                "oracle": oracle,
                "five_locked_baselines": baseline_stats,
                "probability_reliability": reliability,
                "five_baseline_probability_reliability": baseline_probabilities,
                "calibration_drift": drift,
                "regime_breakdown": regimes,
                "ablation_previews": ablations,
                "time_block_uncertainty": _block_status(
                    observed, thresholds["time_block_seconds"],
                    thresholds["minimum_effective_time_blocks"]),
                "stress_multipliers": list(costs["stress_multipliers"]),
                "oracle_hypothetical_stressed_sum_pips": {
                    str(f): _s(sum((x["stress_pips"][str(f)] for x in observed), ZERO))
                    for f in factors},
                "independent_performance_claim_permitted": False,
                "selection_test_data_seen": False,
            })
        counts = [x["hypothesis_id"] for x in proto["hypotheses"]]
    blockers = [
        "NO_INDEPENDENT_REGISTERED_FORECAST_OR_PROTOCOL_RECEIPTS",
        "NO_INDEPENDENT_OUTCOME_PROVIDER_OR_ECONOMIC_VINTAGE_RECEIPTS",
        "NO_REPRESENTATIVE_PROSPECTIVE_SCOREABLE_DATASET",
        "NO_ACTUAL_BROKER_COSTS_OR_TRADE_LEDGER",
        "NO_INDEPENDENT_TIME_BLOCKED_SIGNIFICANCE_OR_SELECTION_CONTROL",
        "NO_FROZEN_INDEPENDENT_CHALLENGER_ABLATIONS",
        "STAGE7_GATES_0_OF_11_AND_SOURCE_FAMILIES_0_OF_13",
        "NO_SHADOW_OR_OPERATOR_SCOPE_AUTHORIZATION",
    ]
    if not all(x is not None for x in provided):
        blockers.append("NO_LOCAL_SYNTHETIC_SCORING_CHAIN_SUPPLIED")
    report = {
        "version": VERSION, "scope": "P9_13_TO_P9_20_OFFLINE_COSTED_ROBUSTNESS_DIAGNOSTIC",
        "historical_epoch_id": EPOCH, "as_of": cutoff.isoformat(),
        "historical_policy_sha256": pin,
        "mode": "READ_ONLY_SYNTHETIC_NOT_PROSPECTIVE_EVIDENCE",
        "scenario_contract_version": SCENARIO_VERSION,
        "scenario_status": "NOT_SUPPLIED" if scenario_sha is None else "LOCAL_SYNTHETIC_SCORECARD_ONLY",
        "scenario_file_sha256": scenario_sha, "prior_local_scenario_file_sha256": prior_sha,
        "protocol_candidate_sha256": None if proto is None else proto["candidate_protocol_sha256"],
        "custody_report_sha256": None if custody is None else custody["report_sha256"],
        "outcome_report_sha256": None if outcome is None else outcome["report_sha256"],
        "full_m5_denominator": 0 if custody is None else custody["coverage"]["expected_m5_decisions"],
        "local_matured_unattested_forecast_pairs": len(sample_stats),
        "hypotheses": studies, "hypothesis_ids": counts,
        "no_trade_hindsight_counterfactual": held_counterfactual,
        "five_benchmarks_locked": list(BASELINE_ORDER),
        "local_ablation_variant_count": len(variants),
        "multiple_testing_correction_executed": False,
        "independent_performance_replicates": 0,
        "prospectively_scorable_pairs": 0,
        "realized_trades_or_broker_returns_used": 0,
        "calibration_or_edge_proven": False,
        "historical_final_test_opened": False,
        "read_only": True, "files_written": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_loaded": False,
        "training_authorized": False, "settlement_invoked": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "operator_approval_granted": False, "historical_epoch_rebound": False,
        "future_epoch_created": False, "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--read-only-scorecard", required=True, action="store_true")
    p.add_argument("--synthetic-only", required=True, action="store_true")
    p.add_argument("--epoch-id", required=True)
    p.add_argument("--as-of", required=True)
    p.add_argument("--policy-file", default=POLICY_PATH)
    p.add_argument("--protocol-file")
    p.add_argument("--prior-protocol")
    p.add_argument("--ledger-file")
    p.add_argument("--prior-ledger")
    p.add_argument("--receipt-candidates")
    p.add_argument("--outcome-file")
    p.add_argument("--prior-outcome")
    p.add_argument("--scenario-file")
    p.add_argument("--prior-scenario")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        report = assess_stage9_offline_scorecard(
            epoch_id=a.epoch_id, as_of=a.as_of, policy_file=a.policy_file,
            protocol_file=a.protocol_file, prior_protocol=a.prior_protocol,
            ledger_file=a.ledger_file, prior_ledger=a.prior_ledger,
            receipt_candidates=a.receipt_candidates, outcome_file=a.outcome_file,
            prior_outcome=a.prior_outcome, scenario_file=a.scenario_file,
            prior_scenario=a.prior_scenario,
        )
    except (OfflineScorecardError, ForecastCustodyError, OutcomeMaturationError,
            ExperimentProtocolError, OfflineHeartbeatError, ValueError, OSError,
            TypeError, KeyError, OverflowError):
        print("STAGE 9 SCORECARD BLOCKED: Unverified or noncausal offline inputs.")
        return 1
    if a.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 9 P9-13..20 offline scorecard and robustness")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Scenario: " + report["scenario_status"])
        print("M5 denominator: %d | Locally matured toy pairs: %d" % (
            report["full_m5_denominator"], report["local_matured_unattested_forecast_pairs"]))
        print("Hypotheses: %d | Baselines locked: 5 | Ablation variants: %d" % (
            len(report["hypotheses"]), report["local_ablation_variant_count"]))
        print("Prospectively scorable: 0 | Edge proven: NO | Registered forecasts: 0")
        print("Independent gates/families: 0/11 | 0/13")
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | SYNTHETIC ONLY | NO BROKER | NO REGISTRATION | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
