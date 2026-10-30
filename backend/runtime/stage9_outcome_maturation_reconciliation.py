
"""P9-09..12: synthetic horizon maturation and outcome-custody reconciliation.

Rechecks the Stage 9 protocol and all complete M5 custody slots. An outcome
candidate is never an independently sourced outcome, even when its local
hashes, quote-side fields and time ordering agree. Missing or post-horizon
quotes never become zero returns. This module does not compute any edge,
model score, baseline result or trading return and never registers a forecast.

All input files are preexisting and local. No writes, networking, providers,
model loading, agents, execution, settlement or new research epochs. HOLD.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError, _read, _decode, _digest,
    _ident, _utc,
)
from backend.runtime.stage9_predeclared_experiment_protocol import (
    ExperimentProtocolError, BASELINE_ORDER, _time, assess_stage9_experiment_protocol,
)
from backend.runtime.stage9_forecast_custody_reconciliation import (
    ForecastCustodyError, assess_stage9_forecast_custody,
)

VERSION = "EURUSD_STAGE9_OUTCOME_MATURATION_P9_09_12_V1"
QUOTE_CONTRACT = "LOCAL_BID_ASK_ORIGIN_AND_TARGET_QUOTE_V1"
DATASET_CONTRACT = "COMPLETE_M5_BY_HYPOTHESIS_SYNTHETIC_MATRIX_V1"
MAX_OUTCOME_BYTES = 262144
MAX_OBSERVATIONS = 192
MAX_EXCLUSION_CLAIMS = 192
MAX_QUOTE_WINDOW_SECONDS = 30
DECIMAL = re.compile(r"^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,6})?$")
OUTCOME_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode", "experiment_id",
    "protocol_candidate_sha256", "ledger_file_sha256", "custody_report_sha256",
    "mock_clock_at", "observations", "censor_claims",
}
OBS_KEYS = {
    "observation_id", "decision_key", "synthetic_prediction_id", "hypothesis_id",
    "horizon_seconds", "claimed_target_at", "decision_quote", "target_quote", "economic_vintage",
    "provenance_claim",
}
QUOTE_KEYS = {
    "quote_id", "provider_id", "vintage_id", "observed_at", "published_at",
    "first_available_at", "received_at", "bid", "ask", "quote_side",
    "clock_uncertainty_ms", "source_class", "source_sha256",
}
ECON_KEYS = {
    "provider_id", "vintage_id", "published_at", "first_available_at",
    "received_at", "source_class", "source_sha256",
}
CENSOR_KEYS = {
    "decision_key", "synthetic_prediction_id", "reason_code", "claimed_at",
}
CENSOR_REASONS = (
    "ORIGINAL_TARGET_QUOTE_MISSING", "PROVIDER_OUTAGE_CLAIM",
    "EXCESSIVE_OUTCOME_DELAY_CLAIM", "SUSPICIOUS_QUOTE_CLAIM",
)
UTC = timezone.utc


class OutcomeMaturationError(ValueError):
    """Offline causal, hash, vintage or source contract mismatch."""


def _strict(value, required, label):
    if not isinstance(value, dict) or set(value) != required:
        raise OutcomeMaturationError("Strict schema mismatch: " + label)


def _id(value, label):
    try:
        return _ident(value, label)
    except (OfflineHeartbeatError, ValueError, TypeError):
        raise OutcomeMaturationError("Unsafe identifier: " + label) from None


def _hex(value, label):
    if (not isinstance(value, str) or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)):
        raise OutcomeMaturationError("SHA-256 required: " + label)
    return value


def _clock(value):
    try:
        return _time(value)
    except (ExperimentProtocolError, ValueError, TypeError):
        raise OutcomeMaturationError("Strict UTC timestamp required.") from None


def _report_time(value):
    """Previously validated internal report uses +00:00 rather than Z."""
    try:
        return _utc(value)
    except (OfflineHeartbeatError, TypeError, ValueError):
        raise OutcomeMaturationError("Corrupt internal report UTC timestamp.") from None


def _file(path, maximum=MAX_OUTCOME_BYTES):
    try:
        raw = _read(path, maximum)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, TypeError, ValueError, UnicodeError):
        raise OutcomeMaturationError("Unsafe, oversized or invalid local evidence.") from None


def _decimal(value, label, upper, *, positive=True):
    if not isinstance(value, str) or not DECIMAL.fullmatch(value):
        raise OutcomeMaturationError("Canonical decimal required: " + label)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise OutcomeMaturationError("Nonfinite decimal rejected.") from None
    if not number.is_finite() or number > Decimal(str(upper)) or (positive and number <= 0):
        raise OutcomeMaturationError("Decimal outside permitted range: " + label)
    return number


def _quote(item, cutoff):
    _strict(item, QUOTE_KEYS, "original_quote")
    for label in ("quote_id", "provider_id", "vintage_id"):
        _id(item[label], label)
    if item["quote_side"] != "BID_ASK_PAIR" or item["source_class"] != "LOCAL_SYNTHETIC_UNVERIFIED":
        raise OutcomeMaturationError("Quote cannot self-assert broker/provider origin.")
    stamps = [_clock(item[k]) for k in (
        "observed_at", "published_at", "first_available_at", "received_at")]
    if stamps != sorted(stamps):
        raise OutcomeMaturationError("Quote observed/published/available/received order invalid.")
    if stamps[-1] > cutoff:
        raise OutcomeMaturationError("Post-cutoff quote evidence must not be opened.")
    bid = _decimal(item["bid"], "bid", 10)
    ask = _decimal(item["ask"], "ask", 10)
    if ask < bid:
        raise OutcomeMaturationError("Bid/ask inversion refused.")
    uncertainty = _decimal(item["clock_uncertainty_ms"], "clock_uncertainty_ms", 10000,
                           positive=False)
    expected = _digest({k: v for k, v in item.items() if k != "source_sha256"})
    if item["source_sha256"] != expected:
        raise OutcomeMaturationError("Local quote snapshot content changed.")
    return {
        "quote_sha256": expected,
        "provider_id": item["provider_id"],
        "observed_at": stamps[0], "available_at": stamps[2],
        "received_at": stamps[3], "spread": ask - bid,
        "uncertainty_ms": uncertainty,
    }


def _economic(item, decision, cutoff):
    if item is None:
        return None, []
    _strict(item, ECON_KEYS, "economic_vintage_claim")
    _id(item["provider_id"], "economic_provider_id")
    _id(item["vintage_id"], "economic_vintage_id")
    if item["source_class"] != "LOCAL_SYNTHETIC_UNVERIFIED":
        raise OutcomeMaturationError("Economic receipt cannot claim independent attestation.")
    stamps = [_clock(item[k]) for k in ("published_at", "first_available_at", "received_at")]
    if stamps != sorted(stamps) or stamps[-1] > cutoff:
        raise OutcomeMaturationError("Invalid or future economic-vintage evidence.")
    expected = _digest({k: v for k, v in item.items() if k != "source_sha256"})
    if item["source_sha256"] != expected:
        raise OutcomeMaturationError("Economic vintage content changed.")
    issues = (["POSTDECISION_ECONOMIC_REVISION_CANNOT_BECOME_FEATURE"]
              if stamps[1] > decision or stamps[2] > decision else [])
    return expected, issues


def _observations(path, custody, protocol, cutoff):
    if path is None:
        return {}, set(), 0, 0, None
    doc, file_sha = _file(path)
    _strict(doc, OUTCOME_KEYS, "outcome_candidate_packet")
    _id(doc["packet_id"], "outcome_packet_id")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH
            or doc["mode"] != "LOCAL_SYNTHETIC_OUTCOMES_UNATTESTED"
            or doc["experiment_id"] != protocol["experiment_id"]
            or doc["protocol_candidate_sha256"] != protocol["candidate_protocol_sha256"]
            or doc["ledger_file_sha256"] != custody["ledger_file_sha256"]
            or doc["custody_report_sha256"] != custody["report_sha256"]
            or _clock(doc["mock_clock_at"]) > cutoff):
        raise OutcomeMaturationError("Outcome file not bound to original custody/clock.")
    samples = doc["observations"]
    claims = doc["censor_claims"]
    if (not isinstance(samples, list) or len(samples) > MAX_OBSERVATIONS
            or not isinstance(claims, list) or len(claims) > MAX_EXCLUSION_CLAIMS):
        raise OutcomeMaturationError("Bounded outcome/censor records required.")
    candidates = {}
    for row in custody["decisions"]:
        if row["synthetic_forecast"] is not None:
            f = row["synthetic_forecast"]
            candidates[(row["decision_key"], f["synthetic_prediction_id"])] = (row, f)
    by_key, seen_ids, orphans = {}, set(), 0
    for item in samples:
        _strict(item, OBS_KEYS, "outcome_observation")
        obs_id = _id(item["observation_id"], "outcome_observation_id")
        if obs_id in seen_ids:
            raise OutcomeMaturationError("Duplicate observation ID refused.")
        seen_ids.add(obs_id)
        _hex(item["decision_key"], "outcome_decision_key")
        _hex(item["synthetic_prediction_id"], "outcome_prediction_id")
        if item["provenance_claim"] != "LOCAL_UNVERIFIED_QUOTE_AND_VINTAGE":
            raise OutcomeMaturationError("Untrusted observation cannot self-assert provenance.")
        target = _clock(item["claimed_target_at"])
        q0 = _quote(item["decision_quote"], cutoff)
        q1 = _quote(item["target_quote"], cutoff)
        pair = (item["decision_key"], item["synthetic_prediction_id"])
        known = candidates.get(pair)
        if known is None:
            orphans += 1
            continue
        row, f = known
        if (item["hypothesis_id"] != f["hypothesis_id"]
                or type(item["horizon_seconds"]) is not int
                or item["horizon_seconds"] != f["horizon_seconds"]
                or target != _report_time(f["target_at"])):
            raise OutcomeMaturationError("Outcome target/horizon differs from frozen prediction.")
        if target > cutoff:
            raise OutcomeMaturationError("Unmatured target cannot supply outcome observations.")
        entry = _clock(row["decision_at"])
        econ_sha, econ_issues = _economic(item["economic_vintage"], entry, cutoff)
        issues = list(econ_issues)
        if (q0["observed_at"] > entry or q0["available_at"] > entry
                or q0["received_at"] > entry):
            issues.append("ENTRY_QUOTE_UNAVAILABLE_AT_DECISION")
        if q1["observed_at"] < target or q1["observed_at"] > target + timedelta(seconds=MAX_QUOTE_WINDOW_SECONDS):
            issues.append("NONCANONICAL_TARGET_QUOTE_WINDOW")
        if q1["provider_id"] != q0["provider_id"]:
            issues.append("QUOTE_PROVIDER_SWITCH")
        if max(q0["uncertainty_ms"], q1["uncertainty_ms"]) > Decimal("250"):
            issues.append("CLOCK_UNCERTAINTY_EXCEEDS_RESEARCH_CEILING")
        if max(q0["spread"], q1["spread"]) > Decimal("0.0100"):
            issues.append("ILLQUID_QUOTE_SPREAD")
        # A late provider release is censored, never substituted by a better later quote.
        lag = protocol["maximum_release_lag_seconds"]
        if (q1["available_at"] > target + timedelta(seconds=lag)
                or q1["received_at"] > target + timedelta(seconds=lag)):
            issues.append("TARGET_RELEASE_AFTER_FIXED_LAG")
        by_key.setdefault(pair, []).append({
            "observation_id": obs_id,
            "decision_quote_sha256": q0["quote_sha256"],
            "target_quote_sha256": q1["quote_sha256"],
            "economic_vintage_sha256": econ_sha,
            "issues": sorted(set(issues)),
            "locally_matured": target <= cutoff,
        })
    censored = set()
    for claim in claims:
        _strict(claim, CENSOR_KEYS, "unverified_censor_reason")
        pair = (_hex(claim["decision_key"], "censor_decision_key"),
                _hex(claim["synthetic_prediction_id"], "censor_prediction_id"))
        if claim["reason_code"] not in CENSOR_REASONS:
            raise OutcomeMaturationError("Nonpredeclared outcome-censor claim refused.")
        if _clock(claim["claimed_at"]) > cutoff:
            raise OutcomeMaturationError("Future censor claim refused.")
        if pair not in candidates or pair in censored:
            raise OutcomeMaturationError("Orphan or duplicate censor claim refused.")
        censored.add(pair)
    return by_key, censored, len(samples), orphans, file_sha


def assess_stage9_outcome_maturation(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                      protocol_file=None, prior_protocol=None,
                                      ledger_file=None, prior_ledger=None,
                                      receipt_candidates=None, outcome_file=None,
                                      prior_outcome=None):
    if epoch_id != EPOCH:
        raise OutcomeMaturationError("Historical epoch rebinding refused.")
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise OutcomeMaturationError("Future review cutoff refused.")
    try:
        pinned = sha256(_read(policy_file, 16384)).hexdigest()
    except (OfflineHeartbeatError, OSError):
        raise OutcomeMaturationError("Historical policy file unavailable or unsafe.") from None
    if pinned != POLICY_SHA256:
        raise OutcomeMaturationError("Historical policy SHA-256 mismatch.")
    if (outcome_file is not None and (protocol_file is None or ledger_file is None)
            or prior_outcome is not None and outcome_file is None):
        raise OutcomeMaturationError("Outcomes require original protocol and custody evidence.")
    try:
        custody = assess_stage9_forecast_custody(
            epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
            protocol_file=protocol_file, prior_protocol=prior_protocol,
            ledger_file=ledger_file, prior_ledger=prior_ledger,
            receipt_candidates=receipt_candidates,
        )
        protocol_report = assess_stage9_experiment_protocol(
            epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
            protocol_file=protocol_file, prior_protocol=prior_protocol,
        )
    except (ExperimentProtocolError, ForecastCustodyError, OSError, ValueError, TypeError):
        raise OutcomeMaturationError("Original Stage 9 protocol/custody cannot replay.") from None
    candidate = protocol_report["candidate"]
    protocol = None
    if protocol_file is not None:
        original, original_sha = _file(protocol_file, 65536)
        if _digest(original) != candidate["candidate_protocol_sha256"]:
            raise OutcomeMaturationError("Original protocol differs from replayed candidate.")
        protocol = {
            **candidate,
            "maximum_release_lag_seconds": original["holdout"]["max_outcome_release_lag_seconds"],
        }
    if outcome_file is not None and custody["ledger_status"] == "NOT_SUPPLIED":
        raise OutcomeMaturationError("Outcome packet requires complete local custody ledger.")
    records, censored, n_samples, orphans, file_sha = _observations(
        outcome_file, custody, protocol, cutoff,
    ) if outcome_file is not None else ({}, set(), 0, 0, None)
    prior_sha = None
    if prior_outcome is not None:
        if str(outcome_file) == str(prior_outcome):
            raise OutcomeMaturationError("Distinct local prior outcome snapshot required.")
        _, prior_sha = _file(prior_outcome)
        if prior_sha != file_sha:
            raise OutcomeMaturationError("Local outcome snapshot was modified after freeze.")
    matrix = []
    matrix_ids = set()
    cases = custody["decisions"]
    hypotheses = [] if candidate is None else candidate["hypotheses"]
    for row in cases:
        for h in hypotheses:
            f = row["synthetic_forecast"]
            matching = f is not None and f["hypothesis_id"] == h["hypothesis_id"]
            status = "MISSING_HYPOTHESIS_FORECAST_COUNTED"
            evidence = None
            issues = []
            if matching:
                pair = (row["decision_key"], f["synthetic_prediction_id"])
                target = _report_time(f["target_at"])
                matches = records.get(pair, [])
                if target > cutoff:
                    status = "PENDING_PREDECLARED_HORIZON"
                elif len(matches) > 1:
                    status = "QUARANTINED_DUPLICATE_OR_CONFLICTING_OUTCOME"
                elif not matches:
                    status = ("PENDING_ORIGINAL_SOURCE_RELEASE" if
                              cutoff < target + timedelta(seconds=protocol["maximum_release_lag_seconds"])
                              else "CENSORED_MISSING_ORIGINAL_OUTCOME")
                elif matches[0]["issues"]:
                    issues = matches[0]["issues"]
                    if "NONCANONICAL_TARGET_QUOTE_WINDOW" in issues:
                        status = "CENSORED_STALE_TARGET_QUOTE"
                    elif "TARGET_RELEASE_AFTER_FIXED_LAG" in issues:
                        status = "CENSORED_LATE_TARGET_RELEASE"
                    else:
                        status = "QUARANTINED_SOURCE_OR_VINTAGE_CONFLICT"
                elif pair in censored:
                    status = "CENSORED_SOURCE_UNAVAILABLE_CLAIM"
                else:
                    status = "LOCALLY_MATURED_UNATTESTED_NOT_SCORABLE"
                    evidence = {
                        "decision_quote_sha256": matches[0]["decision_quote_sha256"],
                        "target_quote_sha256": matches[0]["target_quote_sha256"],
                        "economic_vintage_sha256": matches[0]["economic_vintage_sha256"],
                    }
            matrix_id = _digest({
                "historical_epoch_id": EPOCH, "experiment_id": candidate["experiment_id"],
                "decision_key": row["decision_key"], "hypothesis_id": h["hypothesis_id"],
            })
            if matrix_id in matrix_ids:
                raise OutcomeMaturationError("Duplicate evaluation matrix identity.")
            matrix_ids.add(matrix_id)
            matrix.append({
                "evaluation_pair_id": matrix_id, "decision_key": row["decision_key"],
                "hypothesis_id": h["hypothesis_id"],
                "attempt_status": row["attempt_status"],
                "custody_state": row["custody_state"],
                "maturation_state": status, "source_issues": issues,
                "local_quote_hashes_only": evidence,
                "baseline_policy_ids": list(BASELINE_ORDER),
                "same_m5_denominator_for_all_baselines": True,
                "independent_forecast_receipt_verified": False,
                "independent_outcome_provider_verified": False,
                "eligible_for_prospective_scoring": False,
                "return_or_label_calculated": False,
            })
    count = lambda prefix: sum(x["maturation_state"].startswith(prefix) for x in matrix)
    blockers = [
        "NO_INDEPENDENT_FORECAST_TIMESTAMP_RECEIPTS",
        "NO_INDEPENDENT_POSTHORIZON_QUOTE_PROVIDER_RECEIPTS",
        "NO_INDEPENDENT_ECONOMIC_VINTAGE_ATTESTATIONS",
        "NO_PREOUTCOME_PROTOCOL_CUSTODY",
        "STAGE7_EVIDENCE_GATES_AND_SOURCE_FAMILIES_NOT_ADMITTED",
        "NO_ELIGIBLE_PROSPECTIVE_EVALUATION_DATASET",
        "NO_REAL_MODEL_OR_BROKER_EXECUTION_AUTHORIZATION",
    ]
    if outcome_file is None:
        blockers.append("NO_UNVERIFIED_OUTCOME_PACKET_SUPPLIED")
    if orphans:
        blockers.append("ORPHAN_OUTCOME_RECORDS_QUARANTINED")
    if count("QUARANTINED"):
        blockers.append("UNTRUSTED_OR_CONFLICTING_OUTCOMES_QUARANTINED")
    report = {
        "version": VERSION,
        "scope": "P9_09_TO_P9_12_OFFLINE_OUTCOME_MATURATION",
        "historical_epoch_id": EPOCH, "as_of": cutoff.isoformat(),
        "historical_policy_sha256": pinned,
        "mode": "READ_ONLY_SYNTHETIC_OUTCOME_CUSTODY_NO_SCORING",
        "protocol_candidate_sha256": None if candidate is None else candidate["candidate_protocol_sha256"],
        "custody_report_sha256": custody["report_sha256"],
        "outcome_file_sha256": file_sha,
        "prior_local_outcome_file_sha256": prior_sha,
        "outcome_status": "NOT_SUPPLIED" if outcome_file is None else "LOCAL_UNVERIFIED_OUTCOME_CANDIDATES",
        "receipt_reconciliation_status": custody["reconciliation"]["status"],
        "coverage": {
            "contract_version": DATASET_CONTRACT,
            "m5_decision_denominator": custody["coverage"]["research_denominator_including_missing_and_holds"],
            "hypotheses_per_decision": len(hypotheses),
            "all_hypothesis_decision_pairs": len(matrix),
            "baseline_comparison_pairs": len(matrix) * len(BASELINE_ORDER),
            "locally_matured_unattested_pairs": count("LOCALLY_MATURED"),
            "missing_hypothesis_forecasts": count("MISSING_HYPOTHESIS"),
            "pending_horizon_pairs": count("PENDING_PREDECLARED"),
            "pending_release_pairs": count("PENDING_ORIGINAL"),
            "censored_missing_pairs": count("CENSORED_MISSING"),
            "censored_claim_pairs": count("CENSORED_SOURCE"),
            "censored_stale_pairs": count("CENSORED_STALE"),
            "censored_late_release_pairs": count("CENSORED_LATE"),
            "quarantined_pairs": count("QUARANTINED"),
            "observations_submitted": n_samples,
            "orphan_observations_quarantined": orphans,
            "local_censor_claims_not_independently_verified": len(censored),
            "independently_verified_forecasts": 0,
            "independently_verified_outcome_records": 0,
            "prospectively_scorable_pairs": 0,
            "independent_eligible_decision_universe_verified": False,
        },
        "evaluation_matrix": matrix,
        "economic_receipts_independently_verified": 0,
        "provider_quote_receipts_independently_verified": 0,
        "quote_contract_version": QUOTE_CONTRACT,
        "hypotheses_evaluated": 0, "benchmarks_evaluated": 0,
        "realized_return_estimated": False, "broker_trade_evidence_loaded": False,
        "statistical_edge_demonstrated": False,
        "real_forecasts_registered": 0,
        "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "read_only": True, "files_written": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_loaded": False,
        "training_authorized": False, "settlement_invoked": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "operator_approval_granted": False, "historical_epoch_rebound": False,
        "future_epoch_created": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(set(blockers)), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-outcomes", action="store_true", required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--protocol-file")
    parser.add_argument("--prior-protocol")
    parser.add_argument("--ledger-file")
    parser.add_argument("--prior-ledger")
    parser.add_argument("--receipt-candidates")
    parser.add_argument("--outcome-file")
    parser.add_argument("--prior-outcome")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_stage9_outcome_maturation(
            epoch_id=args.epoch_id, as_of=args.as_of, policy_file=args.policy_file,
            protocol_file=args.protocol_file, prior_protocol=args.prior_protocol,
            ledger_file=args.ledger_file, prior_ledger=args.prior_ledger,
            receipt_candidates=args.receipt_candidates,
            outcome_file=args.outcome_file, prior_outcome=args.prior_outcome,
        )
    except (OutcomeMaturationError, ForecastCustodyError, ExperimentProtocolError,
            OfflineHeartbeatError, OSError, ValueError, TypeError, KeyError, OverflowError):
        print("STAGE 9 MATURATION BLOCKED: Invalid or noncausal local receipt/outcome.")
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        c = report["coverage"]
        print("EURUSD ORACLE | Stage 9 P9-09..12 outcome maturation rehearsal")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Outcome packet: " + report["outcome_status"])
        print("Eligible M5 denominator: %d | Hypothesis pairs: %d" % (
            c["m5_decision_denominator"], c["all_hypothesis_decision_pairs"]))
        print("Locally matured unverified: %d | Independently verified: 0" %
              c["locally_matured_unattested_pairs"])
        print("Censored: %d | Quarantined: %d" % (
            c["censored_missing_pairs"] + c["censored_claim_pairs"] +
            c["censored_stale_pairs"] + c["censored_late_release_pairs"],
            c["quarantined_pairs"]))
        print("Predictions registered: 0 | Outcomes scored: 0 | Benchmarks: 0")
        print("Independent gates/families: 0/11 | 0/13")
        for code in report["blockers"]:
            print("  BLOCKER " + code)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO VERIFIED OUTCOMES | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
