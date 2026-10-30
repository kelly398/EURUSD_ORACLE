
"""P9-05..08: local synthetic forecast custody and receipt-reconciliation design.

Only existing JSON files are read. No witness requests are sent and no external
signature is verified. Every matching receipt is still UNATTESTED/QUARANTINED.
The M5 denominator includes holds, failed cycles, abstentions and absent rows.
No prospective experiment, prediction or outcome is actually registered.
No network, agents, training, trading, broker or filesystem writes. FINAL=HOLD.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError,
    _read, _decode, _digest, _ident, _lineage_key,
)
from backend.runtime.stage9_predeclared_experiment_protocol import (
    ExperimentProtocolError, _time, assess_stage9_experiment_protocol,
)

VERSION = "EURUSD_STAGE9_FORECAST_CUSTODY_P9_05_08_V1"
WITNESS_VERSION = "UNSENT_EXTERNAL_WITNESS_REQUEST_CONTRACT_V1"
FORECAST_VERSION = "UNREGISTERED_FROZEN_FORECAST_CANDIDATE_V1"
COVERAGE_VERSION = "COMPLETE_SYNTHETIC_M5_DENOMINATOR_V1"
RECONCILIATION_VERSION = "UNTRUSTED_RECEIPT_RECONCILIATION_V1"
MAX_LEDGER_BYTES = 262144
MAX_RECEIPT_BYTES = 131072
MAX_DECISIONS = 96
MAX_RECEIPTS = 192
ZERO = "0" * 64
UTC = timezone.utc
LEDGER_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode",
    "experiment_id", "protocol_sha256", "mock_clock_at",
    "window_first_bar_at", "window_last_bar_at", "cadence_seconds", "decisions",
}
DECISION_KEYS = {
    "decision_key", "bar_close_at", "decision_at", "source_status",
    "attempt_status", "exclusion_claim_code", "exclusion_claim_digest", "forecast",
}
FORECAST_KEYS = {
    "contract_version", "hypothesis_id", "horizon_seconds", "direction",
    "probability_up", "model_version_sha256", "feature_schema_sha256",
    "feature_lineage_sha256", "forecast_created_at", "witness_request_claimed_at",
    "signature_claim_status", "signature_claim_sha256", "forecast_sha256",
    "prediction_id",
}
RECEIPTS_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode",
    "experiment_id", "ledger_file_sha256", "receipts",
}
RECEIPT_KEYS = {
    "receipt_id", "service_id", "decision_key", "prediction_id",
    "forecast_sha256", "claimed_received_at", "signature_claim_sha256",
}
SOURCE_STATUSES = ("READY", "MISSING", "QUARANTINED")
ATTEMPT_STATUSES = ("FORECAST", "HOLD", "ABSTAIN", "FAILED", "NO_FORECAST")


class ForecastCustodyError(ValueError):
    """Invalid local research record, immutable identity or causal ordering."""


def _strict(obj, keys, name):
    if not isinstance(obj, dict) or set(obj) != keys:
        raise ForecastCustodyError("Strict schema mismatch: " + name)


def _hex(value, name):
    if (not isinstance(value, str) or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)):
        raise ForecastCustodyError("Lowercase SHA-256 required: " + name)
    return value


def _id(value, name):
    try:
        return _ident(value, name)
    except OfflineHeartbeatError:
        raise ForecastCustodyError("Unsafe local identifier: " + name) from None


def _clock(value):
    try:
        return _time(value)
    except (ExperimentProtocolError, ValueError, TypeError):
        raise ForecastCustodyError("Canonical Z UTC timestamp required.") from None


def _file(path, bound):
    try:
        raw = _read(path, bound)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, UnicodeError, TypeError, ValueError):
        raise ForecastCustodyError("Unsafe, oversized or invalid local JSON evidence.") from None


def _forecast(f, decision_key, decision_at, bar_at, protocol):
    _strict(f, FORECAST_KEYS, "frozen_forecast_candidate")
    if f["contract_version"] != FORECAST_VERSION:
        raise ForecastCustodyError("Forecast contract or mode mismatch.")
    hypotheses = {x["hypothesis_id"]: x for x in protocol["hypotheses"]}
    hypothesis = hypotheses.get(f["hypothesis_id"])
    if hypothesis is None or type(f["horizon_seconds"]) is not int or (
            f["horizon_seconds"] != hypothesis["horizon_seconds"]):
        raise ForecastCustodyError("Forecast hypothesis or horizon was not predeclared.")
    output = hypothesis["forecast_output"]
    direction, prob = f["direction"], f["probability_up"]
    if output == "PROBABILITY_UP_ONLY":
        if direction is not None:
            raise ForecastCustodyError("Probability-only hypothesis cannot claim direction.")
    elif direction not in ("BUY", "SELL"):
        raise ForecastCustodyError("Directional hypothesis requires BUY/SELL proposal.")
    if output == "DIRECTION_ONLY":
        if prob is not None:
            raise ForecastCustodyError("Directional-only hypothesis cannot claim probability.")
    else:
        from backend.runtime.stage9_predeclared_experiment_protocol import PROBABILITY
        if not isinstance(prob, str) or not PROBABILITY.fullmatch(prob):
            raise ForecastCustodyError("Bounded string probability required.")
        p = Decimal(prob)
        if direction == "BUY" and p < Decimal("0.5"):
            raise ForecastCustodyError("Probability contradicts BUY proposal.")
        if direction == "SELL" and p > Decimal("0.5"):
            raise ForecastCustodyError("Probability contradicts SELL proposal.")
    for name in ("model_version_sha256", "feature_schema_sha256",
                 "feature_lineage_sha256", "signature_claim_sha256"):
        _hex(f[name], name)
    if f["signature_claim_status"] != "LOCAL_UNVERIFIED_SIGNATURE_DIGEST":
        raise ForecastCustodyError("Local signature cannot self-assert verification.")
    created, submitted = _clock(f["forecast_created_at"]), _clock(f["witness_request_claimed_at"])
    if not (bar_at <= created <= submitted <= decision_at):
        raise ForecastCustodyError("Late or backdated forecast submission chronology.")
    core = {k: v for k, v in f.items() if k not in ("forecast_sha256", "prediction_id")}
    expected = _digest(core)
    _hex(f["forecast_sha256"], "forecast_sha256")
    if f["forecast_sha256"] != expected:
        raise ForecastCustodyError("Forecast content edited after local freeze.")
    identity = _digest({
        "namespace": "SYNTHETIC_UNREGISTERED_ONLY",
        "historical_epoch_id": EPOCH,
        "decision_key": decision_key,
        "experiment_id": protocol["experiment_id"],
        "frozen_forecast_sha256": expected,
    })
    if f["prediction_id"] != identity:
        raise ForecastCustodyError("Forecast prediction ID inconsistent with frozen identity.")
    return {
        "synthetic_prediction_id": identity,
        "forecast_sha256": expected,
        "hypothesis_id": f["hypothesis_id"],
        "horizon_seconds": f["horizon_seconds"],
        "target_at": (decision_at + timedelta(seconds=f["horizon_seconds"])).isoformat(),
        "model_version_sha256": f["model_version_sha256"],
        "feature_schema_sha256": f["feature_schema_sha256"],
        "feature_lineage_sha256": f["feature_lineage_sha256"],
        "claimed_submitted_at": submitted.isoformat(),
    }


def _decision_universe(doc, protocol, cutoff):
    _strict(doc, LEDGER_KEYS, "local_decision_ledger")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH
            or doc["mode"] != "LOCAL_SYNTHETIC_CUSTODY_ONLY"
            or doc["experiment_id"] != protocol["experiment_id"]
            or doc["protocol_sha256"] != protocol["candidate_protocol_sha256"]
            or type(doc["cadence_seconds"]) is not int or doc["cadence_seconds"] != 300):
        raise ForecastCustodyError("Ledger does not bind to frozen protocol or M5 cadence.")
    _id(doc["packet_id"], "ledger_packet_id")
    first = _clock(doc["window_first_bar_at"])
    last = _clock(doc["window_last_bar_at"])
    mock = _clock(doc["mock_clock_at"])
    declared = datetime.fromisoformat(protocol["declared_at_local_claim"])
    selection = datetime.fromisoformat(next(
        x["start_at"] for x in protocol["evaluation_windows"]
        if x["phase"] == "CHALLENGER_SELECTION"
    ).replace("Z", "+00:00"))
    if (first > last or first < declared or last >= selection
            or first.second or first.microsecond or first.minute % 5
            or last.second or last.microsecond or last.minute % 5):
        raise ForecastCustodyError("Synthetic decision window violates declared chronology.")
    slots = int((last - first).total_seconds() // 300) + 1
    if not 1 <= slots <= MAX_DECISIONS:
        raise ForecastCustodyError("Decision universe exceeds bounded M5 slots.")
    if mock > cutoff or mock < last + timedelta(seconds=20):
        raise ForecastCustodyError("Synthetic decision window violates declared chronology.")
    supplied = doc["decisions"]
    if not isinstance(supplied, list) or len(supplied) > MAX_DECISIONS + 1:
        raise ForecastCustodyError("Decision records exceed bounded M5 universe.")
    observed = {}
    for record in supplied:
        _strict(record, DECISION_KEYS, "local_m5_decision_record")
        bar = _clock(record["bar_close_at"])
        decision = _clock(record["decision_at"])
        if (bar < first or bar > last or (bar-first).total_seconds() % 300
                or not bar <= decision <= bar + timedelta(seconds=60)
                or decision > mock):
            raise ForecastCustodyError("Decision outside declared M5 window/cutoff.")
        key = _lineage_key(bar)
        if record["decision_key"] != key or key in observed:
            raise ForecastCustodyError("Duplicate/forged M5 decision key.")
        source = record["source_status"]
        attempt = record["attempt_status"]
        if source not in SOURCE_STATUSES or attempt not in ATTEMPT_STATUSES:
            raise ForecastCustodyError("Unknown source or Council attempt status.")
        exclusion = record["exclusion_claim_code"]
        if exclusion is None:
            if record["exclusion_claim_digest"] is not None:
                raise ForecastCustodyError("Unpaired exclusion claim digest.")
        elif exclusion not in protocol["permitted_exclusion_codes"]:
            raise ForecastCustodyError("Exclusion not predeclared in protocol.")
        else:
            _hex(record["exclusion_claim_digest"], "exclusion_claim_digest")
        if attempt == "FORECAST":
            if source != "READY" or exclusion is not None:
                raise ForecastCustodyError("Forecast cannot bypass missing/quarantined source.")
            info = _forecast(record["forecast"], key, decision, bar, protocol)
        else:
            if record["forecast"] is not None:
                raise ForecastCustodyError("HOLD, failure or abstention cannot carry a prediction.")
            info = None
        observed[key] = (record, info)
    rows = []
    for number in range(slots):
        bar = first + timedelta(seconds=number * 300)
        key = _lineage_key(bar)
        if key not in observed:
            rows.append({
                "decision_key": key, "bar_close_at": bar.isoformat(),
                "decision_at": None, "attempt_status": "MISSING_DECISION_RECORD", "source_status": "NOT_SUPPLIED",
                "exclusion_claim_code": None, "synthetic_forecast": None,
                "custody_state": "QUARANTINED_MISSING_DECISION_RECORD",
                "receipt_claim_count": 0, "structural_receipt_match": False,
                "reconciliation_issues": ["MISSING_M5_SLOT_MUST_REMAIN_IN_DENOMINATOR"],
                "witness_request": None,
            })
            continue
        record, info = observed[key]
        state = "QUARANTINED_NO_TRUSTED_WITNESS" if info else (
            "HELD_NO_FORECAST" if record["attempt_status"] == "HOLD" else
            "ABSTAINED_NO_FORECAST" if record["attempt_status"] == "ABSTAIN" else
            "FAILED_NO_FORECAST" if record["attempt_status"] == "FAILED" else
            "MISSING_FORECAST_COUNTED"
        )
        issues = []
        if record["exclusion_claim_code"] is not None:
            issues.append("UNWITNESSED_EXCLUSION_CANNOT_REDUCE_DENOMINATOR")
        if record["source_status"] != "READY":
            issues.append("SOURCE_NOT_INDEPENDENTLY_ADMITTED_OR_UNAVAILABLE")
        request = None
        if info:
            material = {
                "contract_version": WITNESS_VERSION,
                "experiment_id": protocol["experiment_id"],
                "historical_epoch_id": EPOCH,
                "decision_key": key,
                "synthetic_prediction_id": info["synthetic_prediction_id"],
                "forecast_sha256": info["forecast_sha256"],
                "deadline_at": _clock(record["decision_at"]).isoformat(),
                "transmission": "UNSENT_LOCAL_INTERFACE_PREVIEW_ONLY",
            }
            request = {"request_sha256": _digest(material),
                       "status": "UNSENT_NO_WITNESS", "service_contacted": False}
        rows.append({
            "decision_key": key, "bar_close_at": bar.isoformat(),
            "decision_at": record["decision_at"],
            "attempt_status": record["attempt_status"],
            "source_status": record["source_status"],
            "exclusion_claim_code": record["exclusion_claim_code"],
            "synthetic_forecast": info, "custody_state": state,
            "receipt_claim_count": 0, "structural_receipt_match": False,
            "reconciliation_issues": issues, "witness_request": request,
        })
    return rows, slots, mock


def _reconcile(rows, receipts_file, ledger_sha, experiment_id, cutoff):
    if receipts_file is None:
        supplied, byte_sha = [], None
    else:
        doc, byte_sha = _file(receipts_file, MAX_RECEIPT_BYTES)
        _strict(doc, RECEIPTS_KEYS, "untrusted_receipt_candidate_packet")
        _id(doc["packet_id"], "receipt_packet_id")
        if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
                or doc["historical_epoch_id"] != EPOCH
                or doc["mode"] != "LOCAL_UNVERIFIED_RECEIPT_CANDIDATES"
                or doc["experiment_id"] != experiment_id
                or doc["ledger_file_sha256"] != ledger_sha):
            raise ForecastCustodyError("Receipt candidates do not bind to original ledger.")
        supplied = doc["receipts"]
        if not isinstance(supplied, list) or len(supplied) > MAX_RECEIPTS:
            raise ForecastCustodyError("Receipt candidate count exceeds safe bound.")
    indexed = {x["decision_key"]: x for x in rows}
    by_key, receipt_ids, duplicate_ids, orphan_count = {}, set(), set(), 0
    for receipt in supplied:
        _strict(receipt, RECEIPT_KEYS, "untrusted_receipt_candidate")
        rid = _id(receipt["receipt_id"], "receipt_id")
        _id(receipt["service_id"], "untrusted_service_id")
        key = _hex(receipt["decision_key"], "receipt_decision_key")
        _hex(receipt["prediction_id"], "receipt_prediction_id")
        _hex(receipt["forecast_sha256"], "receipt_forecast_sha256")
        _hex(receipt["signature_claim_sha256"], "receipt_signature_claim_sha256")
        when = _clock(receipt["claimed_received_at"])
        if when > cutoff:
            raise ForecastCustodyError("Receipt claim is dated after review cutoff.")
        if rid in receipt_ids:
            duplicate_ids.add(rid)
        receipt_ids.add(rid)
        if key not in indexed or indexed[key]["synthetic_forecast"] is None:
            orphan_count += 1
            continue
        by_key.setdefault(key, []).append((receipt, when))
    for row in rows:
        if row["synthetic_forecast"] is None:
            continue
        seen = by_key.get(row["decision_key"], [])
        row["receipt_claim_count"] = len(seen)
        if not seen:
            row["reconciliation_issues"].append("NO_UNTRUSTED_RECEIPT_CANDIDATE")
            continue
        if len(seen) != 1 or duplicate_ids:
            row["custody_state"] = "QUARANTINED_DUPLICATE_RECEIPT_CLAIM"
            row["reconciliation_issues"].append("DUPLICATE_RECEIPT_OR_ID_CLAIM")
            continue
        item, when = seen[0]
        f = row["synthetic_forecast"]
        if (item["prediction_id"] != f["synthetic_prediction_id"]
                or item["forecast_sha256"] != f["forecast_sha256"]):
            row["custody_state"] = "QUARANTINED_CONFLICTING_RECEIPT_CLAIM"
            row["reconciliation_issues"].append("RECEIPT_PAYLOAD_IDENTITY_MISMATCH")
        elif (when < datetime.fromisoformat(f["claimed_submitted_at"])
              or when > _clock(row["decision_at"])):
            row["custody_state"] = "QUARANTINED_NONCAUSAL_RECEIPT_CLAIM"
            row["reconciliation_issues"].append("RECEIPT_TIME_OUTSIDE_PREDECISION_WINDOW")
        else:
            # Still QUARANTINED: no independently configured public trust key,
            # signature verification, external custody or witness service.
            row["structural_receipt_match"] = True
            row["custody_state"] = "QUARANTINED_UNATTESTED_RECEIPT_CLAIM"
            row["reconciliation_issues"].append("LOCAL_MATCH_IS_NOT_INDEPENDENT_SIGNATURE")
    return {
        "contract_version": RECONCILIATION_VERSION,
        "status": "NOT_SUPPLIED" if receipts_file is None else "LOCAL_UNTRUSTED_RECEIPT_CANDIDATES",
        "receipt_candidate_file_sha256": byte_sha,
        "receipt_candidate_count": len(supplied),
        "duplicate_receipt_ids": sorted(duplicate_ids),
        "orphan_receipt_candidate_count": orphan_count,
        "structurally_matching_unverified_receipts": sum(x["structural_receipt_match"] for x in rows),
        "missing_witness_claims": sum(
            x["synthetic_forecast"] is not None and x["receipt_claim_count"] == 0
            for x in rows
        ),
        "externally_verified_receipts": 0,
        "trusted_witness_public_keys_loaded": 0,
        "cryptographic_signature_verifications": 0,
        "all_receipt_claims_independently_verified": False,
    }


def assess_stage9_forecast_custody(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                     protocol_file=None, prior_protocol=None,
                                     ledger_file=None, prior_ledger=None,
                                     receipt_candidates=None):
    if epoch_id != EPOCH:
        raise ForecastCustodyError("Historical epoch rebinding refused.")
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise ForecastCustodyError("Future review cutoff refused.")
    try:
        policy_sha = sha256(_read(policy_file, 16384)).hexdigest()
    except (OfflineHeartbeatError, OSError):
        raise ForecastCustodyError("Pinned clock policy file is unsafe or unavailable.") from None
    if policy_sha != POLICY_SHA256:
        raise ForecastCustodyError("Historical policy SHA-256 mismatch.")
    if ledger_file is not None and protocol_file is None:
        raise ForecastCustodyError("Ledger cannot exist without a frozen candidate protocol.")
    if prior_ledger is not None and ledger_file is None:
        raise ForecastCustodyError("Prior local ledger requires the current ledger.")
    if receipt_candidates is not None and ledger_file is None:
        raise ForecastCustodyError("Receipt candidates require original frozen ledger.")
    try:
        proto = assess_stage9_experiment_protocol(
            epoch_id=EPOCH, as_of=as_of, policy_file=policy_file,
            protocol_file=protocol_file, prior_protocol=prior_protocol,
        )
    except (ExperimentProtocolError, OfflineHeartbeatError, ValueError, OSError, TypeError):
        raise ForecastCustodyError("Stage 9 protocol failed original validation.") from None
    candidate = proto["candidate"]
    ledger_sha, prior_sha, local_same_bytes = None, None, False
    rows, slots, mock = [], 0, None
    if ledger_file is not None:
        ledger, ledger_sha = _file(ledger_file, MAX_LEDGER_BYTES)
        rows, slots, mock = _decision_universe(ledger, candidate, cutoff)
        if prior_ledger is not None:
            if str(ledger_file) == str(prior_ledger):
                raise ForecastCustodyError("Distinct local prior ledger required.")
            old, prior_sha = _file(prior_ledger, MAX_LEDGER_BYTES)
            if old != ledger or prior_sha != ledger_sha:
                raise ForecastCustodyError("Frozen local ledger was edited or reformatted.")
            local_same_bytes = True
    reconciliation = _reconcile(
        rows, receipt_candidates, ledger_sha,
        "NOT_SUPPLIED" if candidate is None else candidate["experiment_id"], cutoff,
    )
    counts = {attempt: sum(x["attempt_status"] == attempt for x in rows)
              for attempt in ATTEMPT_STATUSES + ("MISSING_DECISION_RECORD",)}
    forecast_candidates = counts["FORECAST"]
    missing_records = counts["MISSING_DECISION_RECORD"]
    blockers = [
        "NO_INDEPENDENT_PREDECISION_PROTOCOL_TIMESTAMP_WITNESS",
        "NO_INDEPENDENT_CRYPTOGRAPHIC_FORECAST_SIGNATURE_VERIFICATION",
        "NO_AUTHORIZED_EXTERNAL_TIMESTAMP_WITNESS_SERVICE",
        "THIRTEEN_SOURCE_FAMILIES_NOT_INDEPENDENTLY_ADMITTED",
        "STAGE7_EXTERNAL_GATES_NOT_INDEPENDENTLY_ACCEPTED",
        "NO_REAL_PREDICTION_REGISTRATION_OR_OPERATOR_APPROVAL",
    ]
    if candidate is None:
        blockers.append("NO_LOCAL_EXPERIMENT_PROTOCOL_SUPPLIED")
    if ledger_file is None:
        blockers.append("NO_SYNTHETIC_DECISION_LEDGER_SUPPLIED")
    if prior_ledger is None:
        blockers.append("NO_LOCAL_LEDGER_SNAPSHOT_SUPPLIED")
    if receipt_candidates is None:
        blockers.append("NO_RECEIPT_CANDIDATES_SUPPLIED")
    if missing_records:
        blockers.append("MISSING_M5_DECISION_RECORDS_QUARANTINED_IN_DENOMINATOR")
    if reconciliation["orphan_receipt_candidate_count"]:
        blockers.append("ORPHAN_RECEIPT_CANDIDATES_QUARANTINED")
    if reconciliation["duplicate_receipt_ids"]:
        blockers.append("DUPLICATE_RECEIPT_CANDIDATES_QUARANTINED")
    report = {
        "version": VERSION,
        "scope": "P9_05_TO_P9_08_OFFLINE_FORECAST_CUSTODY_RECONCILIATION",
        "historical_epoch_id": EPOCH,
        "as_of": cutoff.isoformat(),
        "historical_policy_sha256": policy_sha,
        "stage9_protocol_report_sha256": proto["report_sha256"],
        "protocol_candidate_sha256": None if candidate is None else candidate["candidate_protocol_sha256"],
        "mode": "READ_ONLY_SYNTHETIC_CUSTODY_NO_REGISTRATION",
        "ledger_status": "NOT_SUPPLIED" if ledger_file is None else "LOCAL_UNATTESTED_LEDGER_EXERCISE",
        "ledger_file_sha256": ledger_sha,
        "local_prior_ledger_sha256": prior_sha,
        "local_frozen_snapshot_bytes_match": local_same_bytes,
        "local_snapshot_proves_external_timestamp": False,
        "mock_clock_at": None if mock is None else mock.isoformat(),
        "witness_interface": {
            "contract_version": WITNESS_VERSION,
            "request_only": True, "requests_prepared_not_sent": forecast_candidates,
            "request_transmissions": 0, "service_connections": 0,
            "trusted_signer_keys_loaded": 0, "external_receipts_returned": 0,
            "independent_receipt_verified": False,
        },
        "coverage": {
            "contract_version": COVERAGE_VERSION,
            "expected_m5_decisions": slots,
            "recorded_decisions": slots - missing_records,
            "missing_decision_records": missing_records,
            "research_denominator_including_missing_and_holds": slots,
            "locally_claimed_predeclared_exclusions": sum(
                x["exclusion_claim_code"] is not None for x in rows
            ),
            "independently_verified_exclusions": 0,
            "forecast_candidates": forecast_candidates,
            "held_decisions": counts["HOLD"],
            "abstained_decisions": counts["ABSTAIN"],
            "failed_decisions": counts["FAILED"],
            "no_forecast_decisions": counts["NO_FORECAST"],
            "eligible_universe_independently_attested": False,
            "all_declared_m5_slots_accounted_for_locally": missing_records == 0,
        },
        "decisions": rows,
        "reconciliation": reconciliation,
        "real_registered_forecasts": 0,
        "independent_receipts_verified": 0,
        "forecast_outcomes_loaded": False,
        "test_holdout_opened": False,
        "statistical_edge_demonstrated": False,
        "read_only": True, "files_written": False,
        "network_accessed": False, "broker_connected": False,
        "collector_started": False, "council_invoked": False,
        "model_loaded": False, "training_authorized": False,
        "settlement_invoked": False, "paper_orders_authorized": False,
        "live_orders_authorized": False, "operator_approval_granted": False,
        "historical_epoch_rebound": False, "future_epoch_created": False,
        "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-custody", action="store_true", required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--protocol-file")
    parser.add_argument("--prior-protocol")
    parser.add_argument("--ledger-file")
    parser.add_argument("--prior-ledger")
    parser.add_argument("--receipt-candidates")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_stage9_forecast_custody(
            epoch_id=args.epoch_id, as_of=args.as_of,
            policy_file=args.policy_file,
            protocol_file=args.protocol_file, prior_protocol=args.prior_protocol,
            ledger_file=args.ledger_file, prior_ledger=args.prior_ledger,
            receipt_candidates=args.receipt_candidates,
        )
    except (ForecastCustodyError, ExperimentProtocolError, OfflineHeartbeatError,
            OSError, ValueError, TypeError, KeyError, OverflowError):
        print("STAGE 9 CUSTODY BLOCKED: Unsafe or unverified local identity/cutoff.")
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 9 P9-05..08 forecast custody rehearsal")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Ledger: " + report["ledger_status"])
        cover = report["coverage"]
        print("M5 denominator: %d | Missing rows: %d | Held: %d | Abstained: %d" % (
            cover["research_denominator_including_missing_and_holds"],
            cover["missing_decision_records"], cover["held_decisions"],
            cover["abstained_decisions"]))
        print("Unsent requests: %d | Real registered forecasts: 0" % (
            report["witness_interface"]["requests_prepared_not_sent"]))
        print("Receipt candidates: %d | Independent receipts verified: 0" % (
            report["reconciliation"]["receipt_candidate_count"]))
        print("Independent gates/families: 0/11 | 0/13")
        for code in report["blockers"]:
            print("  BLOCKER " + code)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO EXTERNAL WITNESS | NO REGISTRATION | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
