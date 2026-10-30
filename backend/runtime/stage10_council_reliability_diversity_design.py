"""Stage 10 P10-01..08: strict synthetic seat-history/redundancy diagnostics.

Stage 9 independent research decision is rechecked as NOT_ENOUGH_EVIDENCE.
Statistics about local sample claims cannot grant reliability weights, calibrate
consensus probabilities, override governance vetoes or train models. The static
Council manifest is parsed as AST only; no agent/model is ever imported or run.
Input files are existing local JSON, with no writes or network. FINAL=HOLD.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path

from backend.runtime.stage6_council_context_readiness import (
    CouncilContextError, FAMILIES, inspect_static_council,
)
from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError,
    _read, _decode, _digest, _ident, _lineage_key,
)
from backend.runtime.stage9_predeclared_experiment_protocol import (
    HORIZONS, ExperimentProtocolError, _time,
)
from backend.runtime.stage9_independent_challenge_decision import (
    ChallengeDecisionError, assess_stage9_challenge_decision,
)

VERSION = "EURUSD_STAGE10_COUNCIL_RELIABILITY_DIVERSITY_DESIGN_P10_01_08_V1"
PACKET_VERSION = "SYNTHETIC_SEAT_HISTORY_WITH_CAUSAL_OUTCOME_GATES_V1"
DIVERSITY_VERSION = "SHARED_CLAIMED_ANCESTRY_UNATTESTED_V1"
RELIABILITY_VERSION = "PRIOR_ONLY_DECAYED_SEAT_DIAGNOSTICS_V1"
UTC = timezone.utc
MAX_HISTORY_BYTES = 8388608
MAX_CASES = 256
MAX_SOURCE_IDS = 12
MAX_LOOKBACK = 128
MIN_SAMPLES = 50
MIN_TIME_BLOCKS = 8
DECAY = Decimal("0.9700")
Q = Decimal("0.0001")
SESSIONS = ("ASIAN", "LONDON", "NEW_YORK", "OVERLAP", "CLOSED", "UNKNOWN")
REGIMES = ("TREND", "RANGE", "HIGH_VOLATILITY", "LIQUIDITY_STRESS", "UNKNOWN")
PACKET_FIELDS = {
    "schema_version", "historical_epoch_id", "packet_id", "contract_version",
    "mode", "stage9_review_report_sha256", "council_manifest_sha256",
    "council_contracts_sha256", "declared_at", "embargo_seconds",
    "minimum_prior_samples", "minimum_prior_time_blocks", "max_prior_cases",
    "decay_factor", "cases",
}
CASE_FIELDS = {
    "decision_key", "bar_close_at", "decision_at", "event_id", "horizon_seconds",
    "target_at", "outcome_status", "outcome_direction", "outcome_available_at",
    "regime", "session", "regime_first_available_at", "source_snapshot_sha256",
    "seat_claims", "risk_veto_declared", "devils_advocate_challenge_declared",
    "strategist_proposal", "case_claim_sha256",
}
SEAT_FIELDS = {
    "seat_number", "seat_id", "stage", "status", "direction", "confidence",
    "input_first_available_at", "opinion_created_at", "source_families",
    "source_record_ids", "feature_lineage_sha256", "model_family_id",
    "seat_claim_sha256",
}
PRIMARY_STATUSES = ("VOTE", "UNAVAILABLE")
LABELS = ("BUY", "SELL", "TIE", "NOT_AVAILABLE")


class Stage10DesignError(ValueError):
    """Local safety or historical-causal contract failed."""


def _strict(obj, fields, name):
    if not isinstance(obj, dict) or set(obj) != fields:
        raise Stage10DesignError("Strict schema mismatch: " + name)


def _id(value, name):
    try:
        return _ident(value, name)
    except (OfflineHeartbeatError, TypeError, ValueError):
        raise Stage10DesignError("Invalid or sensitive identifier: " + name) from None


def _sha(value, name):
    if not isinstance(value, str) or len(value) != 64 or any(
        ch not in "0123456789abcdef" for ch in value
    ):
        raise Stage10DesignError("SHA-256 required: " + name)
    return value


def _clock(value):
    try:
        return _time(value)
    except (ExperimentProtocolError, TypeError, ValueError):
        raise Stage10DesignError("Canonical UTC Z timestamp required.") from None


def _read_doc(path):
    try:
        raw = _read(path, MAX_HISTORY_BYTES)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, TypeError, ValueError, UnicodeError):
        raise Stage10DesignError("Unsafe or invalid local seat-history file.") from None


def _confidence(value):
    if not isinstance(value, str):
        raise Stage10DesignError("Decimal string confidence required.")
    import re
    if not re.fullmatch(r"(?:0(?:\.[0-9]{1,4})?|1(?:\.0{1,4})?)", value):
        raise Stage10DesignError("Bounded probability confidence required.")
    return Decimal(value)


def _seat(item, canonical, bar, decision):
    _strict(item, SEAT_FIELDS, "seat_vote_claim")
    if (type(item["seat_number"]) is not int
            or item["seat_number"] != canonical["seat"]
            or item["seat_id"] != canonical["agent_id"]
            or item["stage"] != canonical["stage"]):
        raise Stage10DesignError("Canonical 31-seat roster or role mismatch.")
    available = _clock(item["input_first_available_at"])
    issued = _clock(item["opinion_created_at"])
    if available > issued or issued > decision or available > decision or issued < bar:
        raise Stage10DesignError("Seat opinion or input violates original decision cutoff.")
    supplied = item["source_families"]
    if (not isinstance(supplied, list) or supplied != sorted(set(supplied))
            or any(type(s) is not str or s not in FAMILIES for s in supplied)):
        raise Stage10DesignError("Invalid source-family claims.")
    ids = item["source_record_ids"]
    if (not isinstance(ids, list) or len(ids) > MAX_SOURCE_IDS or ids != sorted(set(ids))):
        raise Stage10DesignError("Invalid or duplicate source ancestry IDs.")
    for source_id in ids:
        _id(source_id, "source_record_id")
    feature = item["feature_lineage_sha256"]
    if feature is not None:
        _sha(feature, "feature_lineage_sha256")
    family = item["model_family_id"]
    if family is not None:
        _id(family, "model_family_id")
    stage = canonical["stage"]
    status, direction = item["status"], item["direction"]
    if stage == "PRIMARY":
        if status not in PRIMARY_STATUSES:
            raise Stage10DesignError("Primary seat status outside supported rehearsal scope.")
        if status == "VOTE":
            if (direction not in ("BUY", "SELL", "ABSTAIN")
                    or not set(canonical["required_source_families"]).issubset(supplied)
                    or not ids):
                raise Stage10DesignError("Vote lacks required sources or declared ancestry.")
        elif direction != "ABSTAIN" or ids or family is not None:
            raise Stage10DesignError("Unavailable seat cannot carry a vote or model lineage.")
    else:
        expected = ({29: ("CHALLENGE", "NO_CHALLENGE"),
                     30: ("VETO", "NO_VETO"),
                     31: ("STRATEGY",)}[canonical["seat"]])
        if status not in expected or direction not in ("BUY", "SELL", "HOLD"):
            raise Stage10DesignError("Governance/synthesis seat contract mismatch.")
    if direction in ("BUY", "SELL") and status == "VOTE":
        if item["confidence"] is None or _confidence(item["confidence"]) < Decimal("0.5"):
            raise Stage10DesignError("Directional confidence out of stated bounds.")
    elif item["confidence"] is not None:
        raise Stage10DesignError("Unscored or nonprimary seat cannot report confidence.")
    expected_sha = _digest({k: v for k, v in item.items() if k != "seat_claim_sha256"})
    if item["seat_claim_sha256"] != expected_sha:
        raise Stage10DesignError("Seat local evidence was modified after hash claim.")
    return item


def _cases(doc, roster, cutoff, embargo):
    declared = _clock(doc["declared_at"])
    values = doc["cases"]
    if not isinstance(values, list) or len(values) > MAX_CASES:
        raise Stage10DesignError("Bounded case history required.")
    seen, last, cases = set(), None, []
    for case in values:
        _strict(case, CASE_FIELDS, "historical_case")
        bar = _clock(case["bar_close_at"])
        decision = _clock(case["decision_at"])
        target = _clock(case["target_at"])
        horizon = case["horizon_seconds"]
        if (type(horizon) is not int or horizon not in HORIZONS
                or decision < bar or decision > bar + timedelta(seconds=60)
                or target != decision + timedelta(seconds=horizon)
                or decision > cutoff or bar < declared or bar.second or bar.microsecond
                or bar.minute % 5 or (last is not None and bar <= last)):
            raise Stage10DesignError("Case window, M5 slot, horizon or time ordering changed.")
        last = bar
        if case["decision_key"] != _lineage_key(bar) or case["decision_key"] in seen:
            raise Stage10DesignError("Duplicate or forged synthetic M5 decision identity.")
        seen.add(case["decision_key"])
        _id(case["event_id"], "event_id")
        if (case["session"] not in SESSIONS or case["regime"] not in REGIMES
                or _clock(case["regime_first_available_at"]) > decision):
            raise Stage10DesignError("Regime/session discovered after decision.")
        _sha(case["source_snapshot_sha256"], "source_snapshot_sha256")
        if case["outcome_status"] == "MATURED_LOCAL_UNATTESTED":
            available = _clock(case["outcome_available_at"])
            if (case["outcome_direction"] not in ("BUY", "SELL", "TIE")
                    or not target <= available <= cutoff):
                raise Stage10DesignError("Unmatured or future outcome masquerades as a label.")
        elif case["outcome_status"] == "NOT_AVAILABLE":
            if case["outcome_direction"] is not None or case["outcome_available_at"] is not None:
                raise Stage10DesignError("Missing outcomes cannot be imputed.")
            available = None
        else:
            raise Stage10DesignError("Synthetic outcome origin status cannot self-authorize.")
        if (type(case["risk_veto_declared"]) is not bool
                or type(case["devils_advocate_challenge_declared"]) is not bool
                or case["strategist_proposal"] not in ("BUY", "SELL", "HOLD")):
            raise Stage10DesignError("Non-boolean governance claims or invalid strategist direction.")
        seats = case["seat_claims"]
        if not isinstance(seats, list) or len(seats) != 31:
            raise Stage10DesignError("Complete canonical 31-seat history required.")
        for item, spec in zip(seats, roster):
            _seat(item, spec, bar, decision)
        if ((seats[28]["status"] == "CHALLENGE") != case["devils_advocate_challenge_declared"]
                or (seats[29]["status"] == "VETO") != case["risk_veto_declared"]
                or seats[30]["direction"] != case["strategist_proposal"]
                or case["risk_veto_declared"] and case["strategist_proposal"] != "HOLD"):
            raise Stage10DesignError("Governance challenge/veto and strategist claims conflict.")
        expected = _digest({k: v for k, v in case.items() if k != "case_claim_sha256"})
        if case["case_claim_sha256"] != expected:
            raise Stage10DesignError("Case modified after local digest claim.")
        cases.append({**case, "_bar": bar, "_decision": decision,
                      "_target": target, "_outcome_available": available})
    return cases


def _history(prior_cases, current, horizon, embargo, max_back):
    """Never inspect current/future outcomes; deduplicate overlapping event IDs."""
    eligible = []
    seen_events = set()
    for case in reversed(prior_cases):
        if (case["horizon_seconds"] != horizon or case["outcome_status"] != "MATURED_LOCAL_UNATTESTED"
                or case["_outcome_available"] > current["_decision"]
                or case["_target"] + timedelta(seconds=embargo) > current["_decision"]
                or case["event_id"] in seen_events):
            continue
        seen_events.add(case["event_id"])
        eligible.append(case)
        if len(eligible) >= max_back:
            break
    return list(reversed(eligible))


def _decimal_ratio(part, whole):
    return None if not whole else format((Decimal(part) / Decimal(whole)).quantize(Q), "f")


def _reliability(prior, seat_number, current):
    correct = wrong = neutral = abstain = unavailable = incompatible = 0
    current_claim = current["seat_claims"][seat_number - 1]
    weighted_correct = Decimal(0)
    weighted_total = Decimal(0)
    distinct_days = set()
    conditional = {}
    for age, case in enumerate(reversed(prior)):
        record = case["seat_claims"][seat_number - 1]
        direction = record["direction"]
        if record["status"] != "VOTE":
            unavailable += 1
            continue
        if direction == "ABSTAIN":
            abstain += 1
            continue
        if (record["source_families"] != current_claim["source_families"]
                or record["model_family_id"] != current_claim["model_family_id"]):
            incompatible += 1
            continue
        label = case["outcome_direction"]
        if label == "TIE":
            neutral += 1
            continue
        win = direction == label
        correct += int(win)
        wrong += int(not win)
        distinct_days.add(case["_decision"].date().isoformat())
        weight = DECAY ** age
        weighted_correct += weight if win else Decimal(0)
        weighted_total += weight
        for kind, label_value in (("session", case["session"]), ("regime", case["regime"])):
            key = kind + ":" + label_value
            group = conditional.setdefault(key, [0, 0])
            group[0] += 1
            group[1] += int(win)
    directional = correct + wrong
    adequate_local_count = directional >= MIN_SAMPLES and len(distinct_days) >= MIN_TIME_BLOCKS
    groups = [{"bucket": name, "local_directional_n": counts[0],
               "local_correct_n": counts[1], "locally_descriptive_accuracy": _decimal_ratio(counts[1], counts[0]),
               "independent_subgroup_competence_established": False}
              for name, counts in sorted(conditional.items())]
    return {
        "seat_number": seat_number, "prior_only_history_count": len(prior),
        "directional_outcomes": directional, "correct": correct, "wrong": wrong,
        "ties_not_scored": neutral, "abstained": abstain, "unavailable": unavailable,
        "incompatible_prior_seat_configurations": incompatible,
        "distinct_utc_days": len(distinct_days),
        "raw_accuracy_local_only": _decimal_ratio(correct, directional),
        "decayed_accuracy_local_only": (
            format((weighted_correct / weighted_total).quantize(Q), "f")
            if weighted_total else None),
        "adequate_local_sample_count_only": adequate_local_count,
        "conditional_buckets": groups,
        "trusted_sample_count": 0,
        "nondefault_influence_permitted": False,
        "prospective_reliability_verified": False,
    }


def _diversity(case):
    active = [r for r in case["seat_claims"][:28]
              if r["status"] == "VOTE" and r["direction"] in ("BUY", "SELL")]
    parents = {s["seat_number"]: s["seat_number"] for s in active}

    def find(x):
        while parents[x] != x:
            x = parents[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parents[max(ra, rb)] = min(ra, rb)

    overlaps = []
    for i, left in enumerate(active):
        for right in active[i + 1:]:
            shared = sorted(set(left["source_record_ids"]) & set(right["source_record_ids"]))
            same_feature = (left["feature_lineage_sha256"] is not None and
                            left["feature_lineage_sha256"] == right["feature_lineage_sha256"])
            same_model = (left["model_family_id"] is not None and
                          left["model_family_id"] == right["model_family_id"])
            if shared or same_feature or same_model:
                union(left["seat_number"], right["seat_number"])
                overlaps.append({"seat_a": left["seat_number"], "seat_b": right["seat_number"],
                                 "shared_record_count": len(shared),
                                 "same_feature_claim": bool(same_feature),
                                 "same_model_family_claim": bool(same_model)})
    groups = {}
    for seat in active:
        number = seat["seat_number"]
        groups.setdefault(find(number), []).append(number)
    components = [sorted(group) for group in groups.values()]
    components.sort(key=lambda x: x[0])
    count = len(active)
    diversities = {}
    for component in components:
        for number in component:
            diversities[str(number)] = _decimal_ratio(1, len(components) * len(component))
    return {
        "contract_version": DIVERSITY_VERSION,
        "active_directional_primary_votes": count,
        "overlapping_claim_pairs": len(overlaps),
        "shared_ancestry_pairs": overlaps,
        "connected_claim_components": components,
        "local_redundancy_cluster_count_not_independent_sources": len(components),
        "equal_vote_reference_weight": _decimal_ratio(1, count),
        "diversity_preview_weights_by_seat": diversities,
        "independent_effective_source_count_verified": 0,
        "reliability_weighted_consensus_authorized": False,
        "diversity_adjusted_consensus_authorized": False,
        "probability_calibration_performed": False,
    }


def assess_stage10_council_reliability(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                        history_file=None, prior_history=None,
                                        contracts_file="backend/agents/contracts.py",
                                        manifest_file="backend/agents/council_manifest.py"):
    if epoch_id != EPOCH:
        raise Stage10DesignError("Historical epoch rebinding refused.")
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise Stage10DesignError("Future review cutoff refused.")
    try:
        policy = sha256(_read(policy_file, 16384)).hexdigest()
    except (OfflineHeartbeatError, OSError):
        raise Stage10DesignError("Pinned historical policy unavailable or unsafe.") from None
    if policy != POLICY_SHA256:
        raise Stage10DesignError("Historical policy SHA-256 mismatch.")
    if prior_history is not None and history_file is None:
        raise Stage10DesignError("Prior history cannot replace current history.")
    try:
        manifest = inspect_static_council(contracts_file=contracts_file, manifest_file=manifest_file)
        review = assess_stage9_challenge_decision(
            epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
        )
    except (CouncilContextError, ChallengeDecisionError, OfflineHeartbeatError, OSError):
        raise Stage10DesignError("Canonical Council or prior research boundary unavailable.") from None
    memo = review["research_decision_memorandum"]
    if (memo["research_decision"] != "NOT_ENOUGH_EVIDENCE"
            or memo["stage10_reliability_training_or_selection_authorized"] is not False
            or review["final_action"] != "HOLD"):
        raise Stage10DesignError("Unexpected prior research authorization state; fail closed.")
    packet_sha, prior_sha = None, None
    cases = []
    if history_file is not None:
        packet, packet_sha = _read_doc(history_file)
        _strict(packet, PACKET_FIELDS, "local_seat_history_packet")
        _id(packet["packet_id"], "history_packet_id")
        if (type(packet["schema_version"]) is not int or packet["schema_version"] != 1
                or packet["historical_epoch_id"] != EPOCH
                or packet["mode"] != "SYNTHETIC_LOCAL_SEAT_HISTORY_ONLY"
                or packet["contract_version"] != PACKET_VERSION
                or packet["stage9_review_report_sha256"] != review["report_sha256"]
                or packet["council_manifest_sha256"] != manifest["manifest_sha256"]
                or packet["council_contracts_sha256"] != manifest["contracts_sha256"]
                or _clock(packet["declared_at"]) > cutoff
                or type(packet["embargo_seconds"]) is not int or packet["embargo_seconds"] < 86400
                or packet["embargo_seconds"] > 604800
                or type(packet["minimum_prior_samples"]) is not int
                or packet["minimum_prior_samples"] != MIN_SAMPLES
                or type(packet["minimum_prior_time_blocks"]) is not int
                or packet["minimum_prior_time_blocks"] != MIN_TIME_BLOCKS
                or type(packet["max_prior_cases"]) is not int
                or packet["max_prior_cases"] != MAX_LOOKBACK
                or packet["decay_factor"] != "0.9700"):
            raise Stage10DesignError("Noncausal, changed or self-authorizing reliability policy.")
        cases = _cases(packet, manifest["seats"], cutoff, packet["embargo_seconds"])
        if prior_history is not None:
            if Path(history_file).absolute() == Path(prior_history).absolute():
                raise Stage10DesignError("Distinct prior local snapshot required.")
            _, prior_sha = _read_doc(prior_history)
            if packet_sha != prior_sha:
                raise Stage10DesignError("Local history snapshot edited after freeze.")
        embargo = packet["embargo_seconds"]
    else:
        embargo = 86400
    snapshots = []
    for index, case in enumerate(cases):
        prior = _history(cases[:index], case, case["horizon_seconds"], embargo, MAX_LOOKBACK)
        stats = [_reliability(prior, seat, case) for seat in range(1, 29)]
        ancestry = _diversity(case)
        votes = [x["direction"] for x in case["seat_claims"][:28]
                 if x["status"] == "VOTE" and x["direction"] in ("BUY", "SELL")]
        buys, sells = votes.count("BUY"), votes.count("SELL")
        raw = "BUY" if buys > sells else "SELL" if sells > buys else "HOLD"
        snapshots.append({
            "decision_key": case["decision_key"], "decision_at": case["decision_at"],
            "horizon_seconds": case["horizon_seconds"],
            "outcome_status": case["outcome_status"],
            "local_case_sha256": case["case_claim_sha256"],
            "prior_history_count_after_causal_embargo": len(prior),
            "seat_reliability_preview": stats,
            "shared_ancestry": ancestry,
            "equal_vote_preview_buy": buys, "equal_vote_preview_sell": sells,
            "equal_vote_preview_majority": raw,
            "devils_advocate_challenge_declared": case["devils_advocate_challenge_declared"],
            "risk_officer_veto_declared": case["risk_veto_declared"],
            "local_strategist_proposal": case["strategist_proposal"],
            "risk_veto_immutable_policy": "VETO_CAN_ONLY_HOLD_NEVER_OVERRIDE_TO_TRADE",
            "influence_weights_applied": False,
            "reliability_selection_authorized": False,
            "real_council_decision": "HOLD",
            "real_probability_of_up": None,
        })
    blockers = [
        "STAGE9_INDEPENDENT_EXIT_CRITERIA_NOT_MET",
        "NO_INDEPENDENT_PROSPECTIVE_OUTCOMES_OR_FORECAST_RECEIPTS",
        "NO_INDEPENDENT_SOURCE_FAMILY_ADMISSION",
        "NO_APPROVED_REAL_COUNCIL_SEAT_DISPATCH",
        "NO_INDEPENDENT_RELIABILITY_CALIBRATION_OR_EFFECTIVE_SAMPLE_PROOF",
        "NO_CAUSAL_EVIDENCE_OF_DIVERSITY_WEIGHTING_IMPROVEMENT",
        "NO_PERMITTED_MODEL_TRAINING_SELECTION_OR_PROMOTION",
    ]
    if history_file is None:
        blockers.append("NO_LOCAL_SYNTHETIC_31_SEAT_HISTORY_SUPPLIED")
    report = {
        "version": VERSION, "scope": "P10_01_TO_P10_08_RELIABILITY_AND_DIVERSITY_DESIGN",
        "historical_epoch_id": EPOCH, "as_of": cutoff.isoformat(),
        "historical_policy_sha256": policy,
        "stage9_review_report_sha256": review["report_sha256"],
        "stage9_research_decision": memo["research_decision"],
        "stage9_independent_research_exit_criteria_met": False,
        "static_council_manifest_sha256": manifest["manifest_sha256"],
        "static_council_contracts_sha256": manifest["contracts_sha256"],
        "static_primary_seats": 28, "static_governance_seats": 2,
        "static_synthesis_seats": 1,
        "mode": "READ_ONLY_LOCAL_UNATTESTED_SEAT_DIAGNOSTICS",
        "history_status": "NOT_SUPPLIED" if history_file is None else "LOCAL_SYNTHETIC_UNATTESTED",
        "history_file_sha256": packet_sha,
        "prior_history_file_sha256": prior_sha,
        "historical_cases_examined": len(snapshots),
        "evaluation_snapshots": snapshots,
        "real_forecast_outcome_pairs_eligible_for_learning": 0,
        "independent_seat_attributions_accepted": 0,
        "trusted_prior_performance_records": 0,
        "nondefault_weights_promoted": 0,
        "reliability_models_trained": 0,
        "consensus_probabilities_calibrated": 0,
        "live_or_shadow_consensus_invoked": False,
        "read_only": True, "files_written": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_loaded": False, "training_authorized": False,
        "settlement_invoked": False, "paper_orders_authorized": False,
        "live_orders_authorized": False, "operator_approval_granted": False,
        "historical_epoch_rebound": False, "future_epoch_created": False,
        "independent_external_gates_verified": 0, "independent_source_families_verified": 0,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-reliability", required=True, action="store_true")
    parser.add_argument("--synthetic-only", required=True, action="store_true")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--contracts-file", default="backend/agents/contracts.py")
    parser.add_argument("--manifest-file", default="backend/agents/council_manifest.py")
    parser.add_argument("--history-file")
    parser.add_argument("--prior-history")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_stage10_council_reliability(
            epoch_id=args.epoch_id, as_of=args.as_of, policy_file=args.policy_file,
            contracts_file=args.contracts_file, manifest_file=args.manifest_file,
            history_file=args.history_file, prior_history=args.prior_history,
        )
    except (Stage10DesignError, ChallengeDecisionError, CouncilContextError,
            OfflineHeartbeatError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print("STAGE 10 RELIABILITY BLOCKED: Unsafe or noncausal synthetic source.")
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 10 P10-01..08 Council reliability/diversity design")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Stage 9 research decision: " + report["stage9_research_decision"])
        print("Local synthetic cases: %d | Real learning pairs: 0" % report["historical_cases_examined"])
        print("Primary/governance/synthesis: 28/2/1 | New operational weights: 0")
        print("Independent gates/families: 0/11 | 0/13")
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | SYNTHETIC HISTORY ONLY | NO MODEL TRAINING | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
