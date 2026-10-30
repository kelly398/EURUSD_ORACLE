
"""P8-21..24: OFFLINE recovery design, telemetry and Stage 8 HOLD handover.

Every local artifact is untrusted. A supplied Batch 5 result is recomputed from
its original synthetic fixtures before use. The optional recovery journal is
only an already-existing local, hash-linked *claim*; this tool never appends to
it and does not prove disk durability or third-party custody. STOP/HOLD and
shadow release controls are interface previews, NOT actual process controls.

NO real workers, Council agents, model inference, market feeds, registration,
networking, broker, settlement or trading. FINAL=HOLD without exception.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path

from backend.runtime.stage6_council_context_readiness import FAMILIES, DERIVED_INPUTS
from backend.runtime.stage8_m5_offline_heartbeat import (
    EPOCH, POLICY_PATH, POLICY_SHA256, VERSION as HEARTBEAT_VERSION,
    OfflineHeartbeatError, _read, _child, _ident, _utc, _decode,
)
from backend.runtime.stage8_point_in_time_market_state import (
    VERSION as CONTEXT_VERSION,
)
from backend.runtime.stage8_offline_council_sandbox import VERSION as COUNCIL_VERSION
from backend.runtime.stage8_explainable_research_memory import VERSION as MEMORY_VERSION
from backend.runtime.stage8_offline_shadow_cycle_rehearsal import (
    VERSION as REPLAY_VERSION, ShadowRehearsalError,
    assess_stage8_shadow_rehearsal,
)

VERSION = "EURUSD_STAGE8_RECOVERY_OBSERVABILITY_HANDOVER_P8_21_24_V1"
RECOVERY_VERSION = "LOCAL_RECOVERY_JOURNAL_PREFIX_CLAIM_V1"
OBSERVABILITY_VERSION = "SYNTHETIC_OFFLINE_TELEMETRY_V1"
RELEASE_VERSION = "BLOCKED_RESEARCH_SHADOW_DRY_RUN_GATE_V1"
HANDOVER_VERSION = "STAGE8_LOCAL_ENGINEERING_HANDOVER_UNATTESTED_V1"
REPLAY_SCOPE = "P8_17_TO_P8_20_OFFLINE_SHADOW_CYCLE_REHEARSAL"
ZERO = "0" * 64
MAX_REPLAY_BYTES = 524288
MAX_JOURNAL_BYTES = 131072
MAX_GATE_BYTES = 32768
MAX_TEST_BYTES = 32768
MAX_STAGE_BYTES = 524288
MAX_ENTRIES = 64
MAX_CASES = 8
MAX_TESTS = 10000
JOURNAL_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode",
    "replay_report_sha256", "entries",
}
ENTRY_KEYS = {
    "offset", "decision_key", "case_id", "event_type", "effective_at",
    "checkpoint_sha256", "previous_entry_sha256", "entry_sha256",
}
EVENT_TYPES = (
    "STARTED", "CONTEXT_COMMITTED", "COUNCIL_COMMITTED",
    "MEMORY_COMMITTED", "CRASH_MARK", "HELD_TERMINAL",
)
STAGE_EVENTS = ("CONTEXT_COMMITTED", "COUNCIL_COMMITTED", "MEMORY_COMMITTED")
GATE_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode",
    "requested_scope", "operator_scope_claim", "source_family_claims",
    "forecast_witness_plan_sha256", "stop_hold_test_claim", "runner_code_sha256",
}
STOP_KEYS = {"test_id", "result", "test_artifact_sha256"}
TEST_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "mode",
    "suite", "collected", "passed", "skipped", "failed", "run_at",
    "result_digest", "selected_test_files",
}
TEST_FILES = (
    "backend/runtime/test_stage8_m5_offline_heartbeat.py",
    "backend/runtime/test_stage8_point_in_time_market_state.py",
    "backend/runtime/test_stage8_offline_council_sandbox.py",
    "backend/runtime/test_stage8_explainable_research_memory.py",
    "backend/runtime/test_stage8_offline_shadow_cycle_rehearsal.py",
    "backend/runtime/test_stage8_recovery_observability_handover.py",
)
MODULES = (
    ("m5_heartbeat", "stage8_m5_offline_heartbeat.py", HEARTBEAT_VERSION),
    ("point_in_time_market_state", "stage8_point_in_time_market_state.py", CONTEXT_VERSION),
    ("offline_council", "stage8_offline_council_sandbox.py", COUNCIL_VERSION),
    ("explainable_memory", "stage8_explainable_research_memory.py", MEMORY_VERSION),
    ("offline_replay", "stage8_offline_shadow_cycle_rehearsal.py", REPLAY_VERSION),
    ("recovery_handover", "stage8_recovery_observability_handover.py", VERSION),
)


class RecoveryHandoverError(ValueError):
    """Malformed, noncausal or self-authorizing local evidence was rejected."""


def _strict(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise RecoveryHandoverError("Strict schema mismatch: " + label)


def _hash(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _sha(value, label):
    if not isinstance(value, str) or len(value) != 64 or any(
        c not in "0123456789abcdef" for c in value
    ):
        raise RecoveryHandoverError("Lowercase SHA-256 required: " + label)
    return value


def _id(value, label):
    try:
        return _ident(value, label)
    except OfflineHeartbeatError:
        raise RecoveryHandoverError("Unsafe identifier: " + label) from None


def _time(value):
    try:
        return _utc(value)
    except (OfflineHeartbeatError, TypeError, ValueError):
        raise RecoveryHandoverError("Explicit zero-offset UTC time required.") from None


def _integer(value, label, maximum):
    if type(value) is not int or not 0 <= value <= maximum:
        raise RecoveryHandoverError("Bounded integer required: " + label)
    return value


def _file(path, maximum):
    try:
        raw = _read(path, maximum)
        return _decode(raw), sha256(raw).hexdigest()
    except (OfflineHeartbeatError, OSError, TypeError, ValueError):
        raise RecoveryHandoverError("Unsafe, oversized or invalid local JSON evidence.") from None


def _child_json(root, relative):
    try:
        return _file(_child(root, relative), MAX_STAGE_BYTES)[0]
    except (OfflineHeartbeatError, OSError, ValueError, TypeError):
        raise RecoveryHandoverError("Unsafe referenced frozen Stage 8 file.") from None


def _replay(path, manifest_path, root, cutoff, policy_file, contracts_file, manifest_file):
    present = [x is not None for x in (path, manifest_path, root)]
    if not any(present):
        return None, None, None
    if not all(present):
        raise RecoveryHandoverError(
            "Frozen replay report, fixture manifest and evidence root must be paired."
        )
    frozen, file_sha = _file(path, MAX_REPLAY_BYTES)
    if (frozen.get("scope") != REPLAY_SCOPE
            or frozen.get("historical_epoch_id") != EPOCH
            or frozen.get("final_action") != "HOLD"
            or frozen.get("read_only") is not True
            or frozen.get("candidate_shadow_cycles_started") != 0
            or frozen.get("real_council_seats_dispatched") != 0
            or frozen.get("registered_predictions") != 0
            or frozen.get("independent_external_gates_verified") != 0
            or frozen.get("independently_attested_source_families") != 0
            or _time(frozen.get("as_of")) > cutoff):
        raise RecoveryHandoverError("Frozen Batch 5 replay scope/authority mismatch.")
    if frozen.get("report_sha256") != _hash({
        k: v for k, v in frozen.items() if k != "report_sha256"
    }):
        raise RecoveryHandoverError("Frozen Batch 5 report SHA-256 mismatch.")
    try:
        reproduced = assess_stage8_shadow_rehearsal(
            epoch_id=EPOCH, as_of=frozen["as_of"], policy_file=policy_file,
            fixture_manifest=manifest_path, evidence_root=root,
            contracts_file=contracts_file, manifest_file=manifest_file,
        )
    except (ShadowRehearsalError, OfflineHeartbeatError, OSError, ValueError, TypeError):
        raise RecoveryHandoverError("Frozen Stage 8 replay cannot be reproduced.") from None
    if reproduced != frozen:
        raise RecoveryHandoverError("Frozen replay changed when recomputed from source bytes.")
    doc, _ = _file(manifest_path, 65536)
    if doc.get("requested_scope") != "OFFLINE_REPLAY":
        raise RecoveryHandoverError("Recovery only accepts the OFFLINE_REPLAY scope.")
    if len(frozen["cycles"]) > MAX_CASES:
        raise RecoveryHandoverError("Too many frozen Stage 8 cases.")
    return frozen, file_sha, doc


def _cases(report):
    if report is None:
        return {}, []
    by_key = {}
    for case in report["cycles"]:
        key = _sha(case["decision_key"], "decision_key")
        _id(case["case_id"], "case_id")
        by_key.setdefault(key, []).append(case)
    canonical = {k: v[0] for k, v in by_key.items()}
    return canonical, sorted(report["cycles"], key=lambda x: (x["bar_close_at"], x["case_id"]))


def _checkpoint(case, kind):
    if kind == "CONTEXT_COMMITTED":
        return case["context_report_sha256"], 1
    if kind == "COUNCIL_COMMITTED":
        return case["council_report_sha256"], 2
    if kind == "MEMORY_COMMITTED":
        return case["memory_report_sha256"], 3
    if kind == "HELD_TERMINAL":
        if not case["evidence_events"]:
            return None, 0
        return case["evidence_events"][-1]["event_sha256"], 0
    return None, 0


def _recovery(path, report, cutoff):
    canon, cases = _cases(report)
    if path is not None and report is None:
        raise RecoveryHandoverError("Recovery journal requires a verified frozen replay.")
    entries, journal_sha = [], None
    if path is not None:
        doc, journal_sha = _file(path, MAX_JOURNAL_BYTES)
        _strict(doc, JOURNAL_KEYS, "offline_recovery_journal")
        _id(doc["packet_id"], "journal_packet_id")
        if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
                or doc["historical_epoch_id"] != EPOCH
                or doc["mode"] != "LOCAL_UNATTESTED_CRASH_RECOVERY"
                or doc["replay_report_sha256"] != report["report_sha256"]):
            raise RecoveryHandoverError("Recovery journal historical replay binding mismatch.")
        entries = doc["entries"]
        if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
            raise RecoveryHandoverError("Recovery journal event count exceeds limit.")
    previous = ZERO
    per_key = {}
    key_case = {}
    last_time = None
    for offset, entry in enumerate(entries):
        _strict(entry, ENTRY_KEYS, "recovery_journal_event")
        if type(entry["offset"]) is not int or entry["offset"] != offset:
            raise RecoveryHandoverError("Noncontiguous or duplicate recovery journal offset.")
        key = _sha(entry["decision_key"], "decision_key")
        name = _id(entry["case_id"], "case_id")
        if key not in canon or canon[key]["case_id"] != name:
            raise RecoveryHandoverError("Recovery journal decision/canonical case mismatch.")
        if key in key_case and key_case[key] != name:
            raise RecoveryHandoverError("One logical decision cannot have two journal owners.")
        key_case[key] = name
        kind = entry["event_type"]
        if kind not in EVENT_TYPES:
            raise RecoveryHandoverError("Unknown or forbidden recovery event type.")
        when = _time(entry["effective_at"])
        if when > cutoff or when < _time(canon[key]["decision_at"]):
            raise RecoveryHandoverError("Recovery journal event outside causal review clock.")
        if last_time is not None and when < last_time:
            raise RecoveryHandoverError("Recovery journal offsets backdate an event.")
        last_time = when
        if entry["previous_entry_sha256"] != previous:
            raise RecoveryHandoverError("Recovery journal hash-link parent mismatch.")
        _sha(entry["entry_sha256"], "entry_sha256")
        if _hash({k: v for k, v in entry.items() if k != "entry_sha256"}) != entry["entry_sha256"]:
            raise RecoveryHandoverError("Recovery journal event content hash mismatch.")
        checkpoint, minimum = _checkpoint(canon[key], kind)
        if checkpoint is None:
            if entry["checkpoint_sha256"] is not None:
                raise RecoveryHandoverError("Checkpoint digest invented for uncommitted stage.")
        elif entry["checkpoint_sha256"] != checkpoint:
            raise RecoveryHandoverError("Recovery checkpoint disagrees with frozen causal report.")
        if minimum and canon[key]["completed_frozen_stages"] < minimum:
            raise RecoveryHandoverError("Recovery journal claims stage absent from frozen replay.")
        state = per_key.setdefault(key, {"milestone": None, "crashed": False, "terminal": False, "last_offset": None})
        if state["terminal"]:
            raise RecoveryHandoverError("Terminal recovery state cannot be appended or duplicated.")
        if kind == "STARTED":
            if state["milestone"] is not None:
                raise RecoveryHandoverError("Duplicate STARTED creates duplicate logical cycle.")
            state["milestone"] = "STARTED"
        elif state["milestone"] is None:
            raise RecoveryHandoverError("Recovery event without a STARTED decision.")
        elif kind in STAGE_EVENTS:
            required = {
                "CONTEXT_COMMITTED": "STARTED",
                "COUNCIL_COMMITTED": "CONTEXT_COMMITTED",
                "MEMORY_COMMITTED": "COUNCIL_COMMITTED",
            }[kind]
            if state["milestone"] != required:
                raise RecoveryHandoverError("Recovery milestone skipped or replayed twice.")
            state["milestone"] = kind
            state["crashed"] = False
        elif kind == "CRASH_MARK":
            if state["crashed"]:
                raise RecoveryHandoverError("Duplicate unresolved crash marker.")
            state["crashed"] = True
        else:  # HELD_TERMINAL
            state["terminal"] = True
            state["crashed"] = False
            state["milestone"] = "HELD_TERMINAL"
        state["last_offset"] = offset
        previous = entry["entry_sha256"]
    resume = []
    for key, case in sorted(canon.items(), key=lambda x: (x[1]["bar_close_at"], x[1]["case_id"])):
        state = per_key.get(key)
        terminal = state is not None and state["terminal"]
        crashed = state is not None and state["crashed"]
        resume.append({
            "decision_key": key, "canonical_case_id": case["case_id"],
            "source_case_count": sum(c["decision_key"] == key for c in cases),
            "source_case_state": case["state"],
            "recovery_state": (
                "NO_LOCAL_JOURNAL_EVENT" if state is None else
                "TERMINAL_HELD_ALREADY_RECORDED" if terminal else
                "CRASHED_PARTIAL_HELD" if crashed else "PARTIAL_HELD_NO_DISPATCH"
            ),
            "last_locally_claimed_offset": None if state is None else state["last_offset"],
            "last_locally_claimed_milestone": None if state is None else state["milestone"],
            "preview_resume_from": None if terminal or state is None else state["milestone"],
            "duplicate_forecast_registration_permitted": False,
            "logical_replay_decision_key_preserved": True,
            "real_recovery_process_started": False,
        })
    return {
        "contract_version": RECOVERY_VERSION,
        "status": "NOT_SUPPLIED" if path is None else "LOCAL_JOURNAL_PREFIX_RECONCILED_UNATTESTED",
        "local_journal_file_sha256": journal_sha,
        "locally_verified_entry_count": len(entries),
        "last_locally_claimed_offset": len(entries) - 1 if entries else None,
        "locally_verified_prefix_tip_sha256": previous if entries else None,
        "logical_decisions": resume,
        "logical_decision_count": len(canon),
        "terminal_held_count": sum(x["recovery_state"] == "TERMINAL_HELD_ALREADY_RECORDED" for x in resume),
        "partial_or_crashed_count": sum(x["recovery_state"] in (
            "PARTIAL_HELD_NO_DISPATCH", "CRASHED_PARTIAL_HELD") for x in resume),
        "local_exactly_once_design_checked": path is not None,
        "exactly_once_durable_delivery_proven": False,
        "independent_journal_custody_verified": False,
        "journal_fsync_verified": False,
        "journal_mutated": False,
        "recovery_process_started": False,
        "forecast_registration_attempted": False,
    }


def _metrics(report, manifest, root):
    cases = [] if report is None else report["cycles"]
    spec = {} if manifest is None else {x["case_id"]: x for x in manifest["cycles"]}
    observations = []
    for c in cases:
        source = spec[c["case_id"]]
        late, lateness_state = None, "NOT_MEASURED"
        try:
            hb = _child_json(root, source["heartbeat_index"])
            matching = [x for x in hb["envelopes"] if (
                _time(x["bar_close_at"]) == _time(c["bar_close_at"]))]
            if len(matching) == 1:
                late = str((_time(matching[0]["received_at"]) -
                            _time(matching[0]["bar_close_at"])).total_seconds())
                lateness_state = "SYNTHETIC_FIXTURE_CLAIM"
            elif len(matching) > 1:
                lateness_state = "AMBIGUOUS_MULTIPLE_BAR_ENVELOPES"
        except (RecoveryHandoverError, KeyError, ValueError, TypeError):
            lateness_state = "UNAVAILABLE_LOCAL_FIXTURE"
        missing_families = quarantined_families = None
        if c["context_report_sha256"] is not None:
            context = _child_json(root, source["context_report"])
            if context.get("report_sha256") != c["context_report_sha256"]:
                raise RecoveryHandoverError("Context observability input differs from verified frozen replay.")
            families = [x for x in context["context"]["context_fields"] if x["label"] in FAMILIES]
            missing_families = sum(x["status"] == "NOT_SUPPLIED" for x in families)
            quarantined_families = sum(x["status"] == "QUARANTINED" for x in families)
        timeout_count, disagreement = None, None
        if c["council_report_sha256"] is not None:
            council = _child_json(root, source["council_report"])
            if council.get("report_sha256") != c["council_report_sha256"]:
                raise RecoveryHandoverError("Council observability input differs from verified frozen replay.")
            seats = council["rehearsal"]["seat_records"]
            timeout_count = sum(x["status"] == "TIMEOUT_CANCEL_REQUESTED" for x in seats)
            disagreement = "PRIMARY_DIRECTIONAL_DISAGREEMENT" in council["rehearsal"]["reason_codes"]
        observations.append({
            "case_id": c["case_id"], "decision_key": c["decision_key"],
            "bar_lateness_seconds_claim": late,
            "bar_lateness_measurement": lateness_state,
            "bar_stale_or_expired": "STALE_M5_DELIVERY" in c["reason_codes"],
            "seat_timeout_count_claim": timeout_count,
            "missing_source_family_count_claim": missing_families,
            "quarantined_source_family_count_claim": quarantined_families,
            "primary_disagreement_claim": disagreement,
            "risk_veto_claim": c["risk_veto"],
            "missing_seat_count": c["missing_seat_count"] if c["council_report_sha256"] else None,
            "independent_prediction_receipt_lag_seconds": None,
            "independent_prediction_receipt_lag_state": "NOT_MEASURED_NO_INDEPENDENT_RECEIPT",
            "frozen_chain_complete": c["state"] == "FULL_FROZEN_OFFLINE_CHAIN_HELD",
            "terminal_cycle_held": True,
            "real_cycle_dispatched": False,
            "metrics_independently_witnessed": False,
        })
    return {
        "contract_version": OBSERVABILITY_VERSION,
        "status": "NOT_SUPPLIED" if report is None else "LOCAL_SYNTHETIC_REPLAY_OBSERVATIONS",
        "cases_observed": len(observations),
        "frozen_chain_reconstruction_count": sum(x["frozen_chain_complete"] for x in observations),
        "stale_bar_case_count": sum(x["bar_stale_or_expired"] for x in observations),
        "declared_seat_timeouts_in_observed_councils": sum(
            x["seat_timeout_count_claim"] for x in observations if x["seat_timeout_count_claim"] is not None),
        "councils_with_timeout_measurement": sum(x["seat_timeout_count_claim"] is not None for x in observations),
        "risk_veto_true_cases": sum(x["risk_veto_claim"] is True for x in observations),
        "primary_disagreement_true_cases": sum(x["primary_disagreement_claim"] is True for x in observations),
        "independent_prediction_receipt_measurements": 0,
        "case_observations": observations,
        "trading_quality_or_predictive_edge_proven": False,
        "actual_runtime_or_process_isolation_measured": False,
        "network_metrics_published": False,
    }


def _release(path, report, cutoff):
    doc, digest = None, None
    if path is not None:
        doc, digest = _file(path, MAX_GATE_BYTES)
        _strict(doc, GATE_KEYS, "shadow_dry_run_gate_packet")
        _id(doc["packet_id"], "gate_packet_id")
        if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
                or doc["historical_epoch_id"] != EPOCH
                or doc["mode"] != "LOCAL_UNATTESTED_RELEASE_CLAIMS"
                or doc["requested_scope"] != "RESEARCH_SHADOW_ONLY"
                or doc["operator_scope_claim"] not in ("NOT_SUPPLIED", "LOCAL_UNVERIFIED_CLAIM")):
            raise RecoveryHandoverError("External operator scope cannot be self-authorized.")
        families = doc["source_family_claims"]
        if (not isinstance(families, list) or len(families) > len(FAMILIES)
                or any(not isinstance(x, str) or x not in FAMILIES for x in families)
                or len(families) != len(set(families))):
            raise RecoveryHandoverError("Noncanonical/duplicate source-family admission claim.")
        for x in ("forecast_witness_plan_sha256", "runner_code_sha256"):
            if doc[x] is not None:
                _sha(doc[x], x)
        stop = doc["stop_hold_test_claim"]
        if stop is not None:
            _strict(stop, STOP_KEYS, "stop_hold_test_claim")
            _id(stop["test_id"], "stop_test_id")
            if stop["result"] not in ("LOCAL_PASS_CLAIM", "LOCAL_FAIL_CLAIM", "NOT_SUPPLIED"):
                raise RecoveryHandoverError("STOP/HOLD test cannot self-assert approval.")
            if (stop["test_artifact_sha256"] is None) != (stop["result"] == "NOT_SUPPLIED"):
                raise RecoveryHandoverError("STOP test claim/result digest mismatch.")
            if stop["test_artifact_sha256"] is not None:
                _sha(stop["test_artifact_sha256"], "stop_test_artifact_sha256")
    all_claims = False if doc is None else bool(
        doc["operator_scope_claim"] == "LOCAL_UNVERIFIED_CLAIM"
        and set(doc["source_family_claims"]) == set(FAMILIES)
        and doc["forecast_witness_plan_sha256"] is not None
        and doc["runner_code_sha256"] is not None
        and doc["stop_hold_test_claim"] is not None
        and doc["stop_hold_test_claim"]["result"] == "LOCAL_PASS_CLAIM"
    )
    blockers = [
        "NO_INDEPENDENT_OPERATOR_RESEARCH_SCOPE_APPROVAL",
        "G01_G07_EXTERNAL_EVIDENCE_NOT_INDEPENDENTLY_ACCEPTED",
        "THIRTEEN_CANONICAL_SOURCE_FAMILIES_NOT_INDEPENDENTLY_ADMITTED",
        "NO_INDEPENDENT_PREDECISION_FORECAST_WITNESS_PLAN_ACCEPTANCE",
        "NO_INDEPENDENT_STOP_HOLD_PROCESS_CONTROL_VERIFICATION",
        "NO_AUTHORIZED_SHADOW_PROCESS_OR_HARD_RUNTIME_ISOLATION",
    ]
    if report is None:
        blockers.append("NO_FROZEN_END_TO_END_OFFLINE_REPLAY_PROOF_SUPPLIED")
    if doc is None:
        blockers.append("NO_LOCAL_DRY_RUN_RELEASE_CLAIM_PACKET_SUPPLIED")
    if doc is not None and doc["stop_hold_test_claim"] is not None and doc["stop_hold_test_claim"]["result"] == "LOCAL_FAIL_CLAIM":
        blockers.append("LOCAL_STOP_HOLD_TEST_REPORTED_FAILED")
    return {
        "contract_version": RELEASE_VERSION,
        "status": "BLOCKED_EXTERNAL_EVIDENCE",
        "local_gate_packet_sha256": digest,
        "local_all_checklist_claims_present": all_claims,
        "local_source_family_claim_count": 0 if doc is None else len(doc["source_family_claims"]),
        "local_stop_hold_claim": None if doc is None else doc["stop_hold_test_claim"],
        "independent_operator_scope_approved": False,
        "independent_source_families_admitted": 0,
        "immutable_witness_plan_independently_approved": False,
        "real_stop_hold_mechanism_tested": False,
        "real_shadow_process_authorized": False,
        "shadow_process_started": False,
        "dry_run_release_decision": "BLOCKED_NO_SHADOW",
        "blockers": sorted(blockers),
    }


def _test_claim(path, cutoff):
    if path is None:
        return {"status": "NOT_SUPPLIED", "local_test_claim_sha256": None,
                "claimed_collected": None, "claimed_passed": None,
                "claimed_skipped": None, "claimed_failed": None,
                "selected_stage8_files": [], "independent_test_custody": False}
    doc, digest = _file(path, MAX_TEST_BYTES)
    _strict(doc, TEST_KEYS, "local_stage8_test_result_claim")
    _id(doc["packet_id"], "test_packet_id")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH
            or doc["mode"] != "LOCAL_TEST_RUN_UNATTESTED"
            or doc["suite"] != "STAGE8_ALLOWLIST_ONLY"
            or _time(doc["run_at"]) > cutoff):
        raise RecoveryHandoverError("Test claim not from bounded Stage 8-only scope.")
    for key in ("collected", "passed", "skipped", "failed"):
        _integer(doc[key], key, MAX_TESTS)
    if doc["collected"] != doc["passed"] + doc["skipped"] + doc["failed"]:
        raise RecoveryHandoverError("Collected test count does not equal outcomes.")
    listed = doc["selected_test_files"]
    if (not isinstance(listed, list) or not listed
            or any(not isinstance(x, str) or x not in TEST_FILES for x in listed)
            or len(listed) != len(set(listed))):
        raise RecoveryHandoverError("Only known Stage 8 test files may be claimed.")
    if doc["result_digest"] is not None:
        _sha(doc["result_digest"], "test_run_result_digest")
    return {
        "status": "LOCAL_UNATTESTED_TEST_CLAIM",
        "local_test_claim_sha256": digest,
        "claimed_collected": doc["collected"], "claimed_passed": doc["passed"],
        "claimed_skipped": doc["skipped"], "claimed_failed": doc["failed"],
        "selected_stage8_files": sorted(listed),
        "independent_test_custody": False,
    }


def _handover(report, recovery, metrics, gate, tests, policy_file):
    source = Path(__file__).resolve().parent
    modules = []
    for label, file_name, contract in MODULES:
        try:
            digest = sha256(_read(source / file_name, MAX_STAGE_BYTES)).hexdigest()
        except (OfflineHeartbeatError, OSError):
            raise RecoveryHandoverError("Stage 8 module bytes unavailable for handover.") from None
        modules.append({"module": label, "file": "backend/runtime/" + file_name,
                        "contract_version": contract, "local_code_sha256": digest,
                        "independently_reviewed": False})
    cases = [] if report is None else [{
        "case_id": c["case_id"], "decision_key": c["decision_key"],
        "terminal_research_state": c["state"],
        "source_reason_codes": list(c["reason_codes"]),
        "last_local_evidence_event_sha256": (
            c["evidence_events"][-1]["event_sha256"] if c["evidence_events"] else None),
        "context_sha256": c["context_report_sha256"],
        "council_sha256": c["council_report_sha256"],
        "memory_sha256": c["memory_report_sha256"],
        "witness_receipt_present": False,
    } for c in report["cycles"]]
    material = {
        "contract_version": HANDOVER_VERSION,
        "historical_epoch_id": EPOCH,
        "historical_policy_sha256": POLICY_SHA256,
        "code_inventory": modules,
        "canonical_family_count": len(FAMILIES),
        "canonical_derived_labels": len(DERIVED_INPUTS),
        "canonical_council_seats": 31,
        "frozen_replay_report_sha256": None if report is None else report["report_sha256"],
        "sample_decision_packets": cases,
        "recovery_local_prefix_verified": recovery["local_exactly_once_design_checked"],
        "recovery_independent_custody_verified": False,
        "observability_contract_version": metrics["contract_version"],
        "dry_run_release_decision": gate["dry_run_release_decision"],
        "test_artifact_claim": tests,
        "exit_mode": "OFFLINE_SYNTHETIC_ENGINEERING_ONLY_UNAPPROVED_SHADOW",
        "shadow_approved": False,
        "independent_source_admissions": 0,
        "independent_external_gates": 0,
        "real_prediction_registered": False,
        "broker_or_execution_capability_enabled": False,
        "historical_epoch_rebound": False,
        "final_action": "HOLD",
    }
    return {**material, "handover_sha256": _hash(material),
            "engineering_handover_written": False,
            "independent_external_handover_accepted": False}


def assess_stage8_recovery_handover(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                     replay_report=None, fixture_manifest=None,
                                     evidence_root=None, recovery_journal=None,
                                     shadow_gate_claim=None, test_result_claim=None,
                                     contracts_file="backend/agents/contracts.py",
                                     manifest_file="backend/agents/council_manifest.py"):
    if epoch_id != EPOCH:
        raise RecoveryHandoverError("Historical epoch mismatch; rebinding refused.")
    cutoff = _time(as_of)
    if cutoff > datetime.now(timezone.utc):
        raise RecoveryHandoverError("Future review cutoff refused.")
    try:
        pinned = sha256(_read(policy_file, 16384)).hexdigest()
    except (OfflineHeartbeatError, OSError):
        raise RecoveryHandoverError("Historical clock policy file unsafe/unavailable.") from None
    if pinned != POLICY_SHA256:
        raise RecoveryHandoverError("Immutable historical policy SHA-256 mismatch.")
    frozen, replay_file_sha, manifest = _replay(
        replay_report, fixture_manifest, evidence_root, cutoff, policy_file,
        contracts_file, manifest_file)
    recovery = _recovery(recovery_journal, frozen, cutoff)
    metrics = _metrics(frozen, manifest, evidence_root)
    gate = _release(shadow_gate_claim, frozen, cutoff)
    tests = _test_claim(test_result_claim, cutoff)
    handover = _handover(frozen, recovery, metrics, gate, tests, policy_file)
    blockers = [
        "STAGE7_GATES_G01_G07_NOT_INDEPENDENTLY_ACCEPTED",
        "THIRTEEN_COUNCIL_SOURCE_FAMILIES_NOT_INDEPENDENTLY_ADMITTED",
        "NO_APPROVED_RESEARCH_SHADOW_SCOPE_OR_OPERATOR_PERMISSION",
        "NO_INDEPENDENT_PREDECISION_FORECAST_WITNESS_RECEIPT",
        "STOP_HOLD_AND_RESOURCE_ISOLATION_ARE_NOT_REAL_RUNTIME_CONTROLS",
        "NO_DURABLE_INDEPENDENT_APPEND_ONLY_JOURNAL_CUSTODY",
        "NO_REAL_COUNCIL_DISPATCH_OR_PREDICTION_REGISTRATION",
    ]
    if frozen is None:
        blockers.append("NO_REPRODUCIBLE_FROZEN_STAGE8_REPLAY_SUPPLIED")
    if recovery_journal is None:
        blockers.append("NO_EXISTING_LOCAL_RECOVERY_JOURNAL_PREFIX_SUPPLIED")
    result = {
        "version": VERSION, "scope": "P8_21_TO_P8_24_OFFLINE_RECOVERY_HANDOVER",
        "historical_epoch_id": EPOCH, "historical_policy_sha256": pinned,
        "as_of": cutoff.isoformat(), "mode": "READ_ONLY_OFFLINE_SYNTHETIC_HOLD",
        "replay_source": {
            "status": "NOT_SUPPLIED" if frozen is None else "RECOMPUTED_LOCAL_FROZEN_REPLAY_UNATTESTED",
            "report_sha256": None if frozen is None else frozen["report_sha256"],
            "replay_file_sha256": replay_file_sha,
            "fixture_manifest_sha256": None if frozen is None else frozen["manifest_sha256"],
            "locally_reproduced": frozen is not None,
            "independently_attested": False,
        },
        "recovery": recovery, "observability": metrics,
        "release_gate": gate, "evidence_handover": handover,
        "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "independent_forecast_receipts_verified": 0,
        "real_shadow_cycles_started": 0, "council_seats_dispatched": 0,
        "predictions_registered": 0,
        "read_only": True, "files_written": False,
        "network_accessed": False, "broker_connected": False,
        "collector_started": False, "council_invoked": False,
        "model_loaded": False, "training_authorized": False,
        "settlement_invoked": False, "paper_orders_authorized": False,
        "live_orders_authorized": False, "operator_approval_granted": False,
        "historical_epoch_rebound": False, "future_epoch_created": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    result["report_sha256"] = _hash(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-recovery-handover", action="store_true", required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--replay-report")
    parser.add_argument("--fixture-manifest")
    parser.add_argument("--evidence-root")
    parser.add_argument("--recovery-journal")
    parser.add_argument("--shadow-gate-claim")
    parser.add_argument("--test-result-claim")
    parser.add_argument("--contracts-file", default="backend/agents/contracts.py")
    parser.add_argument("--manifest-file", default="backend/agents/council_manifest.py")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_stage8_recovery_handover(
            epoch_id=args.epoch_id, as_of=args.as_of, policy_file=args.policy_file,
            replay_report=args.replay_report, fixture_manifest=args.fixture_manifest,
            evidence_root=args.evidence_root, recovery_journal=args.recovery_journal,
            shadow_gate_claim=args.shadow_gate_claim,
            test_result_claim=args.test_result_claim,
            contracts_file=args.contracts_file, manifest_file=args.manifest_file,
        )
    except (RecoveryHandoverError, ShadowRehearsalError, OfflineHeartbeatError,
            OSError, TypeError, ValueError, KeyError):
        print("STAGE 8 RECOVERY HANDOVER BLOCKED: Unsafe or unauthorizable local evidence.")
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 8 P8-21..24 offline recovery handover")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Replay: " + report["replay_source"]["status"])
        print("Recovery: " + report["recovery"]["status"])
        print("Cases observed: %d | Reconstructed: %d" % (
            report["observability"]["cases_observed"],
            report["observability"]["frozen_chain_reconstruction_count"]))
        print("Shadow release: " + report["release_gate"]["dry_run_release_decision"])
        print("Real shadow cycles: 0 | Council dispatches: 0 | Predictions registered: 0")
        print("Independent gates/families: 0/11 | 0/13 | Forecast receipts: 0")
        for reason in report["blockers"]:
            print("  BLOCKER " + reason)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | RECOVERY DESIGN ONLY | NO SHADOW | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
