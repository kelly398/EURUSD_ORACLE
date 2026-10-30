
"""P8-17..20: read-only historical research replay and blocked shadow handoff.

A fixture manifest identifies *already frozen* Stage 8 inputs and reports.
Reconstructs the M5 -> Market State -> 31-seat declarative Council -> memory
chain at an explicit mock clock, without writing intermediary files. In shadow
scope NO simulation or live process starts. A witness handoff is an UNISSUED
request *shape*, not an independent receipt, nor permission to register.

Never imports order adapters, agents, models, collectors or networking clients.
No training, registration, settlement, broker activity, or persistent journal.
All scenarios, including an apparently unanimous synthetic Council, HOLD.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, OfflineHeartbeatError, _lineage_key,
    _child, _read, _decode, _ident, _utc, assess_stage8_heartbeat,
)
from backend.runtime.stage8_point_in_time_market_state import (
    MarketStateError, assess_stage8_market_state,
)
from backend.runtime.stage8_offline_council_sandbox import (
    CouncilSandboxError, assess_stage8_council,
)
from backend.runtime.stage8_explainable_research_memory import (
    ResearchMemoryError, assess_stage8_explainable_memory,
)

VERSION = "EURUSD_STAGE8_SHADOW_REHEARSAL_P8_17_20_V1"
GATE_VERSION = "UNATTESTED_RESEARCH_SHADOW_GATE_V1"
WITNESS_VERSION = "UNISSUED_TIMESTAMP_WITNESS_REQUEST_V1"
UTC = timezone.utc
MAX_MANIFEST_BYTES = 65536
MAX_SNAPSHOT_BYTES = 524288
MAX_CASES = 8
CASE_KEYS = {
    "case_id", "bar_close_at", "decision_at", "heartbeat_index",
    "heartbeat_root", "context_index", "context_root", "context_report",
    "seat_schedule", "council_report", "forecast_proposal", "prior_journal",
    "memory_report",
}
MANIFEST_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode", "mock_clock_at",
    "requested_scope", "shadow_gate", "cycles",
}
GATE_KEYS = {
    "version", "operator_scope_claim", "source_admission_claim",
    "witness_service_claim", "stop_switch_claim",
}
GATE_CLAIMS = ("NOT_SUPPLIED", "LOCAL_UNATTESTED_CLAIM")
REPORT_FLAGS = (
    "read_only", "files_written", "network_accessed", "broker_connected",
    "collector_started", "council_invoked", "model_loaded",
    "training_authorized", "settlement_invoked", "paper_orders_authorized",
    "live_orders_authorized", "operator_approval_granted",
)


class ShadowRehearsalError(ValueError):
    """Malformed, forged, unsafe or incompatible local research rehearsal."""


def _strict(obj, required, label):
    if not isinstance(obj, dict) or set(obj) != required:
        raise ShadowRehearsalError("Strict schema mismatch: " + label)


def _hash(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _clock(value):
    try:
        return _utc(value)
    except (OfflineHeartbeatError, ValueError, TypeError):
        raise ShadowRehearsalError("Explicit, valid UTC mock time required.") from None


def _identifier(value, label):
    try:
        return _ident(value, label)
    except OfflineHeartbeatError:
        raise ShadowRehearsalError("Unsafe fixture identifier: " + label) from None


def _existing(root, relative, *, directory=False):
    try:
        target = _child(root, relative)
    except (OfflineHeartbeatError, OSError, ValueError, TypeError):
        raise ShadowRehearsalError("Unsafe local fixture relative path.") from None
    if directory and not target.is_dir():
        raise ShadowRehearsalError("Fixture directory does not exist.")
    if not directory and not target.is_file():
        raise ShadowRehearsalError("Fixture file does not exist.")
    return target


def _json_file(path, limit):
    try:
        data = _read(path, limit)
        return _decode(data), sha256(data).hexdigest(), len(data)
    except (OfflineHeartbeatError, OSError, ValueError, TypeError):
        raise ShadowRehearsalError("Unsafe, oversized or malformed local fixture JSON.") from None


def _manifest(path, root, cutoff):
    doc, digest, size = _json_file(path, MAX_MANIFEST_BYTES)
    _strict(doc, MANIFEST_KEYS, "stage8_offline_shadow_manifest")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH
            or doc["mode"] != "SYNTHETIC_OFFLINE"
            or doc["requested_scope"] not in ("OFFLINE_REPLAY", "RESEARCH_SHADOW_ONLY")):
        raise ShadowRehearsalError("Historical epoch or offline/shadow scope mismatch.")
    _identifier(doc["packet_id"], "packet_id")
    mock = _clock(doc["mock_clock_at"])
    if mock > cutoff:
        raise ShadowRehearsalError("Mock clock occurs after review cutoff.")
    gate = doc["shadow_gate"]
    if gate is not None:
        _strict(gate, GATE_KEYS, "read_only_shadow_gate_claim")
        if gate["version"] != GATE_VERSION or any(
            gate[field] not in GATE_CLAIMS for field in GATE_KEYS if field != "version"
        ):
            raise ShadowRehearsalError("No external authorization may be asserted through a local gate.")
    cycles = doc["cycles"]
    if not isinstance(cycles, list) or len(cycles) > MAX_CASES:
        raise ShadowRehearsalError("Bounded replay cycle list required.")
    seen_ids = set()
    resolved = []
    for item in cycles:
        _strict(item, CASE_KEYS, "replay_cycle")
        case_id = _identifier(item["case_id"], "case_id")
        if case_id in seen_ids:
            raise ShadowRehearsalError("Duplicate synthetic case ID.")
        seen_ids.add(case_id)
        bar = _clock(item["bar_close_at"])
        decision = _clock(item["decision_at"])
        if (bar.second or bar.microsecond or bar.minute % 5 or
                bar > decision or decision > mock):
            raise ShadowRehearsalError("Completed M5 close/decision/mock clock chronology invalid.")
        if item["heartbeat_index"] is None or item["heartbeat_root"] is None:
            raise ShadowRehearsalError("Offline replay requires M5 index and local root.")
        has_context = [item[k] is not None for k in (
            "context_index", "context_root", "context_report")]
        if any(has_context) and not all(has_context):
            raise ShadowRehearsalError("Context index, root and frozen report must be paired.")
        if (item["seat_schedule"] is None) != (item["council_report"] is None):
            raise ShadowRehearsalError("Council schedule and frozen report must be paired.")
        if item["council_report"] is not None and not all(has_context):
            raise ShadowRehearsalError("Council requires frozen point-in-time context.")
        if item["memory_report"] is not None and item["council_report"] is None:
            raise ShadowRehearsalError("Memory report requires frozen Council.")
        if (item["forecast_proposal"] is not None or item["prior_journal"] is not None) and item["memory_report"] is None:
            raise ShadowRehearsalError("Forecast/journal requires frozen memory report.")
        paths = {"case_id": case_id, "bar_close_at": bar.isoformat(),
                 "decision_at": decision.isoformat(), "decision_key": _lineage_key(bar)}
        for key in CASE_KEYS - {"case_id", "bar_close_at", "decision_at"}:
            relative = item[key]
            if relative is None:
                paths[key] = None
            else:
                paths[key] = _existing(root, relative, directory=key.endswith("_root"))
        resolved.append((item, paths))
    resolved.sort(key=lambda pair: (pair[1]["bar_close_at"], pair[1]["case_id"]))
    return doc, digest, size, mock, resolved


def _verify_snapshot(path, reconstructed, expected_scope):
    frozen, byte_digest, byte_size = _json_file(path, MAX_SNAPSHOT_BYTES)
    if (frozen.get("scope") != expected_scope or frozen.get("final_action") != "HOLD"
            or frozen.get("historical_epoch_id") != EPOCH):
        raise ShadowRehearsalError("Frozen report has invalid scope, epoch or final action.")
    if frozen != reconstructed:
        raise ShadowRehearsalError("Frozen report changed when replayed at the mock clock.")
    return byte_digest, byte_size


def _step(case, stage, status, digest=None, reasons=()):
    prior = case["evidence_events"][-1]["event_sha256"] if case["evidence_events"] else "0" * 64
    material = {
        "ordinal": len(case["evidence_events"]) + 1,
        "stage": stage, "state": status,
        "input_report_sha256": digest,
        "previous_sha256": prior,
        "reason_codes": sorted(set(reasons)),
        "terminal_permission": "HOLD",
    }
    case["evidence_events"].append({**material, "event_sha256": _hash(material)})


def _blank(item):
    return {
        "case_id": item["case_id"], "bar_close_at": item["bar_close_at"],
        "decision_at": item["decision_at"], "decision_key": item["decision_key"],
        "replay_label": "SYNTHETIC_OFFLINE_REPLAY",
        "state": "HELD_NOT_REPLAYED", "completed_frozen_stages": 0,
        "heartbeat_report_sha256": None, "context_report_sha256": None,
        "council_report_sha256": None, "memory_report_sha256": None,
        "heartbeat_candidate": False, "context_quarantined": None,
        "submitted_seats": 0, "missing_seat_count": 31,
        "risk_veto": None, "synthetic_proposed_direction": "HOLD",
        "synthetic_prediction_id": None, "forecast_registered": False,
        "postdecision_source_records_withheld": 0,
        "evidence_events": [], "reason_codes": [], "audit_notes": [],
        "witness_handoff": {
            "interface_version": WITNESS_VERSION,
            "status": "NOT_PREPARED", "request_sha256": None,
            "receipt_id": None, "witness_signature": None,
            "submitted_to_service": False, "independent_receipt_verified": False,
        },
        "council_agents_executed": 0,
    }


def _held(case, reason, stage, *, code="BLOCKED"):
    case["state"] = "HELD_" + code
    case["reason_codes"] = sorted(set(case["reason_codes"] + [reason]))
    _step(case, stage, "HELD", reasons=[reason])
    return case


def _handoff(memory, case):
    forecast = memory["forecast_envelope"]
    if forecast["status"] != "LOCAL_SYNTHETIC_FORECAST_PROPOSAL_UNREGISTERED":
        return
    request = {
        "interface_version": WITNESS_VERSION,
        "historical_epoch_id": EPOCH,
        "decision_key": case["decision_key"],
        "context_sha256": memory["council_input"]["context_sha256"],
        "council_report_sha256": memory["council_input"]["council_report_sha256"],
        "forecast_envelope_sha256": forecast["forecast_envelope_sha256"],
        "proposal_target_at": forecast["target_at"],
        "proposal_claim_time": forecast["proposed_at"],
        "requested_scope": "RESEARCH_RECEIPT_INTERFACE_ONLY",
        "no_network_submission": True,
    }
    # Distinguishes a deterministic request identifier from any third-party
    # signed receipt. Never populate receipt_id or witness_signature locally.
    case["witness_handoff"] = {
        "interface_version": WITNESS_VERSION,
        "status": "UNSENT_REQUEST_SHAPE_ONLY",
        "request_sha256": _hash(request),
        "receipt_id": None, "witness_signature": None,
        "submitted_to_service": False, "independent_receipt_verified": False,
    }
    case["audit_notes"].append("PRE_TARGET_CLAIM_LOCALLY_CHECKED_NOT_INDEPENDENTLY_WITNESSED")


def _replay_one(paths, mock, policy_file, contracts_file, manifest_file):
    case = _blank(paths)
    try:
        heartbeat = assess_stage8_heartbeat(
            epoch_id=EPOCH, as_of=mock.isoformat(), policy_file=policy_file,
            fixture_index=paths["heartbeat_index"], fixture_root=paths["heartbeat_root"],
        )
    except (OfflineHeartbeatError, OSError, ValueError, TypeError):
        return _held(case, "M5_HEARTBEAT_REPLAY_REJECTED", "M5_HEARTBEAT")
    case["heartbeat_report_sha256"] = heartbeat["report_sha256"]
    _step(case, "M5_HEARTBEAT", "LOCALLY_REPLAYED_UNATTESTED", heartbeat["report_sha256"])
    matches = [c for c in heartbeat["cycle_records"]
               if c["decision_key"] == case["decision_key"]]
    if len(matches) != 1:
        return _held(case, "M5_DECISION_KEY_NOT_UNIQUE_OR_NOT_SUPPLIED", "M5_ADMISSION")
    hb = matches[0]
    case["heartbeat_candidate"] = hb["locally_validated_synthetic_candidate"]
    if not case["heartbeat_candidate"]:
        case["reason_codes"].extend(hb["terminal_reasons"])
        return _held(case, "M5_BAR_NOT_LOCALLY_ELIGIBLE", "M5_ADMISSION")
    if paths["context_index"] is None:
        return _held(case, "POINT_IN_TIME_CONTEXT_NOT_SUPPLIED", "POINT_IN_TIME_CONTEXT")
    try:
        context = assess_stage8_market_state(
            epoch_id=EPOCH, as_of=mock.isoformat(),
            decision_at=case["decision_at"], bar_close_at=case["bar_close_at"],
            policy_file=policy_file, context_index=paths["context_index"],
            evidence_root=paths["context_root"],
            heartbeat_index=paths["heartbeat_index"],
            heartbeat_root=paths["heartbeat_root"],
            contracts_file=contracts_file, manifest_file=manifest_file,
        )
        ctx_file_sha, size = _verify_snapshot(
            paths["context_report"], context,
            "P8_05_TO_P8_08_POINT_IN_TIME_MARKET_STATE")
    except (MarketStateError, ShadowRehearsalError, OfflineHeartbeatError,
            OSError, ValueError, TypeError):
        return _held(case, "POINT_IN_TIME_CONTEXT_REPLAY_MISMATCH", "POINT_IN_TIME_CONTEXT")
    case["completed_frozen_stages"] = 1
    case["context_report_sha256"] = context["report_sha256"]
    case["context_quarantined"] = context["context"]["status"] == "LOCAL_CONTEXT_QUARANTINED"
    case["postdecision_source_records_withheld"] = context["evidence"]["postdecision_records_withheld"]
    if case["postdecision_source_records_withheld"]:
        case["audit_notes"].append("POSTDECISION_RECORDS_WITHHELD_FROM_CAUSAL_CONTEXT")
    if case["context_quarantined"]:
        case["reason_codes"].append("POINT_IN_TIME_INPUT_QUARANTINED")
        for item in context["context"]["context_fields"]:
            case["reason_codes"].extend(item["issues"])
    _step(case, "POINT_IN_TIME_CONTEXT", "LOCAL_REPLAY_HASH_MATCH_UNATTESTED", ctx_file_sha)
    if paths["council_report"] is None:
        return _held(case, "DECLARATIVE_COUNCIL_SCHEDULE_NOT_SUPPLIED", "COUNCIL_REHEARSAL")
    try:
        council = assess_stage8_council(
            epoch_id=EPOCH, as_of=mock.isoformat(), policy_file=policy_file,
            context_report=paths["context_report"],
            seat_schedule=paths["seat_schedule"],
            contracts_file=contracts_file, manifest_file=manifest_file,
        )
        council_file_sha, size = _verify_snapshot(
            paths["council_report"], council,
            "P8_09_TO_P8_12_SYNTHETIC_COUNCIL_SANDBOX")
    except (CouncilSandboxError, ShadowRehearsalError, OSError, ValueError, TypeError):
        return _held(case, "DECLARATIVE_COUNCIL_REPLAY_MISMATCH", "COUNCIL_REHEARSAL")
    case["completed_frozen_stages"] = 2
    case["council_report_sha256"] = council["report_sha256"]
    case["submitted_seats"] = council["rehearsal"]["submitted_seat_count"]
    case["missing_seat_count"] = len(council["rehearsal"]["missing_seat_ids"])
    case["risk_veto"] = council["rehearsal"]["declared_risk_veto"]
    case["synthetic_proposed_direction"] = council["rehearsal"]["synthetic_only_proposed_direction"]
    case["reason_codes"].extend(council["rehearsal"]["reason_codes"])
    _step(case, "COUNCIL_REHEARSAL", "LOCAL_REPLAY_HASH_MATCH_UNATTESTED", council_file_sha)
    if paths["memory_report"] is None:
        return _held(case, "EXPLAINABLE_MEMORY_SNAPSHOT_NOT_SUPPLIED", "EXPLAINABLE_MEMORY")
    try:
        memory = assess_stage8_explainable_memory(
            epoch_id=EPOCH, as_of=mock.isoformat(), policy_file=policy_file,
            council_report=paths["council_report"],
            context_report=paths["context_report"],
            seat_schedule=paths["seat_schedule"],
            forecast_proposal=paths["forecast_proposal"],
            prior_journal=paths["prior_journal"],
            contracts_file=contracts_file, manifest_file=manifest_file,
        )
        memory_file_sha, size = _verify_snapshot(
            paths["memory_report"], memory,
            "P8_13_TO_P8_16_EXPLAINABLE_RESEARCH_MEMORY")
    except (ResearchMemoryError, CouncilSandboxError, ShadowRehearsalError,
            OSError, ValueError, TypeError):
        return _held(case, "EXPLAINABLE_MEMORY_REPLAY_MISMATCH", "EXPLAINABLE_MEMORY")
    case["completed_frozen_stages"] = 3
    case["memory_report_sha256"] = memory["report_sha256"]
    case["synthetic_prediction_id"] = memory["forecast_envelope"]["prediction_id"]
    _step(case, "EXPLAINABLE_MEMORY", "LOCAL_REPLAY_HASH_MATCH_UNATTESTED", memory_file_sha)
    _handoff(memory, case)
    if case["witness_handoff"]["status"] == "UNSENT_REQUEST_SHAPE_ONLY":
        _step(case, "WITNESS_HANDOFF", "UNSENT_NOT_A_RECEIPT",
              case["witness_handoff"]["request_sha256"])
    else:
        _step(case, "WITNESS_HANDOFF", "NOT_PREPARED_NO_FORECAST")
    case["state"] = "FULL_FROZEN_OFFLINE_CHAIN_HELD"
    case["reason_codes"].append("NO_INDEPENDENT_WITNESS_OR_SHADOW_AUTHORIZATION")
    case["reason_codes"] = sorted(set(case["reason_codes"]))
    _step(case, "FINAL_RESEARCH_CYCLE", "HELD", reasons=case["reason_codes"])
    return case


def assess_stage8_shadow_rehearsal(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                    fixture_manifest=None, evidence_root=None,
                                    contracts_file="backend/agents/contracts.py",
                                    manifest_file="backend/agents/council_manifest.py"):
    if epoch_id != EPOCH:
        raise ShadowRehearsalError("Historical epoch mismatch; rebinding refused.")
    cutoff = _clock(as_of)
    if cutoff > datetime.now(UTC):
        raise ShadowRehearsalError("Future review time refused.")
    try:
        policy_sha = sha256(_read(policy_file, 16384)).hexdigest()
    except (OfflineHeartbeatError, OSError):
        raise ShadowRehearsalError("Immutable clock policy file unavailable.") from None
    if policy_sha != POLICY_SHA256:
        raise ShadowRehearsalError("Immutable historical policy SHA-256 mismatch.")
    if (fixture_manifest is None) != (evidence_root is None):
        raise ShadowRehearsalError("Fixture manifest and evidence root must be paired.")
    doc, index_sha, index_bytes, mock, resolved = None, None, 0, None, []
    if fixture_manifest is not None:
        doc, index_sha, index_bytes, mock, resolved = _manifest(fixture_manifest, evidence_root, cutoff)
    scope = "OFFLINE_REPLAY" if doc is None else doc["requested_scope"]
    gate = None if doc is None else doc["shadow_gate"]
    shadow_blockers = [
        "STAGE7_GATES_NOT_INDEPENDENTLY_ADMITTED",
        "THIRTEEN_SOURCE_FAMILIES_UNATTESTED",
        "RESEARCH_SHADOW_SCOPE_NOT_SEPARATELY_AUTHORIZED",
        "NO_INDEPENDENT_TIMESTAMP_WITNESS_OR_RECEIPT_SERVICE",
        "NO_REAL_COUNCIL_PROCESS_OR_OPERATOR_RISK_CLEARANCE",
    ]
    out, seen, duplicates, conflicts = [], {}, 0, 0
    fingerprints = {}
    for _, paths in resolved:
        normalized = {k: v for k, v in paths.items() if k != "case_id"}
        fingerprint = _hash({k: str(v) for k, v in normalized.items()})
        fingerprints.setdefault(paths["decision_key"], set()).add(fingerprint)
    conflicting_keys = {key for key, versions in fingerprints.items() if len(versions) > 1}
    conflicts = len(conflicting_keys)
    for _, paths in resolved:
        key = paths["decision_key"]
        case = _blank(paths)
        # Preflight every duplicate identity BEFORE replay. In particular,
        # never construct an unsent witness request for a conflicted M5 key.
        if key in conflicting_keys:
            seen[key] = None
            out.append(_held(case, "CONFLICTING_BAR_DECISION_QUARANTINED",
                             "IDEMPOTENCY", code="CONFLICT"))
            continue
        if key in seen:
            duplicates += 1
            out.append(_held(case, "DUPLICATE_BAR_DECISION_SUPPRESSED",
                             "IDEMPOTENCY", code="DUPLICATE"))
            continue
        seen[key] = next(iter(fingerprints[key]))
        if scope == "RESEARCH_SHADOW_ONLY":
            out.append(_held(case, "UNAPPROVED_RESEARCH_SHADOW_SCOPE_NOT_RUN", "SHADOW_GATE"))
            continue
        out.append(_replay_one(paths, mock, policy_file, contracts_file, manifest_file))
    if not resolved:
        shadow_blockers.append("NO_OFFLINE_REPLAY_FIXTURE_MANIFEST_OR_CYCLES")
    if scope == "RESEARCH_SHADOW_ONLY":
        shadow_blockers.append("LOCAL_SHADOW_GATE_CANNOT_CONFER_INDEPENDENT_APPROVAL")
    if conflicts:
        shadow_blockers.append("CONFLICTING_DECISION_KEYS_QUARANTINED")
    if any(x["state"] != "FULL_FROZEN_OFFLINE_CHAIN_HELD" for x in out):
        shadow_blockers.append("INCOMPLETE_OR_HELD_OFFLINE_REPLAY_CASES")
    report = {
        "version": VERSION, "scope": "P8_17_TO_P8_20_OFFLINE_SHADOW_CYCLE_REHEARSAL",
        "historical_epoch_id": EPOCH, "as_of": cutoff.isoformat(),
        "historical_policy_sha256": policy_sha,
        "replay_mode": "SYNTHETIC" if doc is None else "OFFLINE_REPLAY_SYNTHETIC_INPUTS",
        "mock_clock_at": None if mock is None else mock.isoformat(),
        "manifest_sha256": index_sha, "manifest_bytes_locally_hashed": index_bytes,
        "requested_scope": scope,
        "shadow_gate": {
            "contract_version": GATE_VERSION,
            "local_claims_supplied": gate is not None,
            "local_claims_independently_verified": False,
            "shadow_scope_authorized": False,
            "shadow_runner_started": False,
            "operator_permission_granted": False,
        },
        "witness_interface": {
            "contract_version": WITNESS_VERSION,
            "request_only": True, "third_party_receipt_id": None,
            "independent_timestamp_witness_connected": False,
            "independent_predecision_receipts_verified": 0,
        },
        "cycles": out,
        "unique_decision_keys": len(seen),
        "duplicate_decisions_suppressed": duplicates,
        "conflicting_decision_keys_quarantined": conflicts,
        "full_frozen_offline_chains_reconstructed": sum(
            x["state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD" for x in out),
        "candidate_shadow_cycles_started": 0, "real_council_seats_dispatched": 0,
        "registered_predictions": 0, "independently_attested_source_families": 0,
        "independent_external_gates_verified": 0,
        "read_only": True, "files_written": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_loaded": False,
        "training_authorized": False, "settlement_invoked": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "operator_approval_granted": False, "historical_epoch_rebound": False,
        "future_epoch_created": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(set(shadow_blockers)), "final_action": "HOLD",
    }
    report["report_sha256"] = _hash(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-shadow-rehearsal", action="store_true", required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--fixture-manifest")
    parser.add_argument("--evidence-root")
    parser.add_argument("--contracts-file", default="backend/agents/contracts.py")
    parser.add_argument("--manifest-file", default="backend/agents/council_manifest.py")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_stage8_shadow_rehearsal(
            epoch_id=args.epoch_id, as_of=args.as_of, policy_file=args.policy_file,
            fixture_manifest=args.fixture_manifest, evidence_root=args.evidence_root,
            contracts_file=args.contracts_file, manifest_file=args.manifest_file,
        )
    except (ShadowRehearsalError, OfflineHeartbeatError, CouncilSandboxError,
            MarketStateError, ResearchMemoryError, ValueError, TypeError, OSError):
        print("STAGE 8 SHADOW REHEARSAL BLOCKED: Invalid, unsafe or noncausal local inputs.")
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 8 P8-17..20 offline shadow-cycle rehearsal")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Requested scope: " + report["requested_scope"])
        print("Mock clock: " + str(report["mock_clock_at"]))
        print("Synthetic cases: %d | Full frozen chains: %d" % (
            len(report["cycles"]), report["full_frozen_offline_chains_reconstructed"]))
        print("Duplicates: %d | Conflicts: %d" % (
            report["duplicate_decisions_suppressed"],
            report["conflicting_decision_keys_quarantined"]))
        print("Shadow cycles: 0 | Agent dispatches: 0 | Predictions registered: 0")
        print("Independent gates/families: 0/11 | 0/13 | Witness receipts: 0")
        for reason in report["blockers"]:
            print("  BLOCKER " + reason)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | OFFLINE REPLAY | NO WITNESS ISSUED | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
