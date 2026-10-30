
"""Stage 8 P8-09..12: deterministic OFFLINE 31-seat Council envelope rehearsal.

Only declarative, synthetic result envelopes are inspected. NO agent/model Python
is imported or executed, no worker/process is spawned, and claimed runtime or
memory numbers are NOT measured isolation. The Stage 8 Batch 2 context report
is locally hash-checked, NOT independently attested. All outcomes are research
rehearsal metadata, never executable instructions. FINAL=HOLD unconditionally.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path
import re

from backend.runtime.stage6_council_context_readiness import (
    CouncilContextError, DERIVED_INPUTS, FAMILIES, inspect_static_council,
)
from backend.runtime.stage8_m5_offline_heartbeat import EPOCH, POLICY_PATH, POLICY_SHA256

VERSION = "EURUSD_STAGE8_OFFLINE_COUNCIL_P8_09_12_V1"
POLICY_VERSION = "SYNTHETIC_STRICT_31_SEAT_HOLD_V1"
UTC = timezone.utc
HEX = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
CONF = re.compile(r"^(?:0(?:\.[0-9]{1,3})?|1(?:\.0{1,3})?)$")
SENSITIVE = re.compile(
    r"(?i)(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"account[_-]?(?:id|number)|bearer|authorization|login)"
)
MAX_CONTEXT_BYTES = 524288
MAX_SCHEDULE_BYTES = 131072
MAX_PAYLOAD_BYTES = 4096
MAX_WORKING_BYTES = 2097152
MAX_TIMESTAMP_MS = 60000
PRIMARY_BUDGET_MS = 250
GOVERNANCE_BUDGET_MS = 500
CYCLE_BUDGET_MS = 10000
MAX_DECLARED_MEMORY_BYTES = 65536
MIN_DIRECTIONAL = 24
MAX_EXPLICIT_ABSTAINS = 4
MIN_MAJORITY_SHARE = Decimal("0.67")
CONTEXT_SCOPE = "P8_05_TO_P8_08_POINT_IN_TIME_MARKET_STATE"
SIM_KEYS = {
    "schema_version", "historical_epoch_id", "packet_id", "as_of", "mode",
    "decision_key", "context_sha256", "synthetic_model_mock_sha256", "seat_results",
}
SEAT_KEYS = {
    "seat_number", "seat_id", "stage", "outcome", "started_ms", "elapsed_ms",
    "peak_memory_bytes", "payload", "exception_code",
}
PRIMARY_KEYS = {"direction", "confidence", "reason_code", "cited_labels"}
ADVOCATE_KEYS = {"challenge", "reason_code"}
RISK_KEYS = {"veto", "reason_code"}
STRATEGIST_KEYS = {"direction", "confidence", "reason_code"}
RESULT_OUTCOMES = ("RESULT", "ABSTAIN", "TIMEOUT", "EXCEPTION", "CANCELLED")
DIRECTION = ("BUY", "SELL", "HOLD")


class CouncilSandboxError(ValueError):
    """Untrusted, malformed or unsupported offline rehearsal metadata."""


def _utc(value):
    if not isinstance(value, str):
        raise CouncilSandboxError("Explicit UTC timestamp required.")
    try:
        time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise CouncilSandboxError("Invalid UTC timestamp.") from None
    if time.tzinfo is None or time.utcoffset() != timedelta(0):
        raise CouncilSandboxError("Explicit zero-offset UTC required.")
    return time.astimezone(UTC)


def _sha(value, name):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise CouncilSandboxError("SHA-256 required: " + name)
    return value


def _id(value, name):
    if not isinstance(value, str) or not IDENT.fullmatch(value) or SENSITIVE.search(value):
        raise CouncilSandboxError("Unsafe or invalid identifier: " + name)
    return value


def _int(value, name, maximum=MAX_TIMESTAMP_MS):
    if type(value) is not int or not 0 <= value <= maximum:
        raise CouncilSandboxError("Invalid bounded integer: " + name)
    return value


def _strict(value, fields, label):
    if not isinstance(value, dict) or set(value) != fields:
        raise CouncilSandboxError("Strict schema mismatch: " + label)


def _unique(pairs):
    found = {}
    for key, value in pairs:
        if key in found:
            raise CouncilSandboxError("Duplicate JSON key refused.")
        found[key] = value
    return found


def _decode(raw):
    try:
        doc = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                CouncilSandboxError("Nonfinite JSON refused.")),
        )
    except CouncilSandboxError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise CouncilSandboxError("Invalid UTF-8 JSON evidence.") from None
    if not isinstance(doc, dict):
        raise CouncilSandboxError("JSON object required.")
    return doc


def _digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _read(path, maximum):
    target = Path(path).absolute()
    for part in (target, *target.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise CouncilSandboxError("Symlink or junction refused.")
    if not target.is_file():
        raise CouncilSandboxError("Existing ordinary local evidence file required.")
    try:
        with target.open("rb") as stream:
            raw = stream.read(maximum + 1)
    except OSError:
        raise CouncilSandboxError("Evidence cannot be read.") from None
    if len(raw) > maximum:
        raise CouncilSandboxError("Evidence size limit exceeded.")
    return raw


def _manifest(contracts_file, manifest_file):
    try:
        manifest = inspect_static_council(contracts_file=contracts_file,
                                          manifest_file=manifest_file)
    except (CouncilContextError, OSError, ValueError):
        raise CouncilSandboxError("Canonical Council AST manifest invalid.") from None
    seats = manifest["seats"]
    if (manifest["static_seats"] != 31 or len(seats) != 31
            or [s["seat"] for s in seats] != list(range(1, 32))
            or [s["stage"] for s in seats] != ["PRIMARY"] * 28
            + ["GOVERNANCE"] * 2 + ["SYNTHESIS"]
            or [s["agent_id"] for s in seats[-3:]] != [
                "devils_advocate", "risk_officer", "chief_strategist"]):
        raise CouncilSandboxError("Council seat ordering/role manifest changed.")
    return manifest


def _context(path, cutoff, manifest):
    if path is None:
        return None, None
    raw = _read(path, MAX_CONTEXT_BYTES)
    doc = _decode(raw)
    expected = {
        "scope", "historical_epoch_id", "as_of", "report_sha256", "context",
        "final_action", "read_only", "council_invoked",
        "independent_external_gates_verified", "independent_source_families_verified",
    }
    if not expected.issubset(doc):
        raise CouncilSandboxError("Incomplete Stage 8 Batch 2 context report.")
    if (doc["scope"] != CONTEXT_SCOPE or doc["historical_epoch_id"] != EPOCH
            or doc["final_action"] != "HOLD" or doc["read_only"] is not True
            or doc["council_invoked"] is not False
            or doc["independent_external_gates_verified"] != 0
            or doc["independent_source_families_verified"] != 0
            or _utc(doc["as_of"]) > cutoff):
        raise CouncilSandboxError("Context report cannot grant Council authority.")
    expected_sha = _sha(doc["report_sha256"], "context_report_sha256")
    if _digest({key: val for key, val in doc.items() if key != "report_sha256"}) != expected_sha:
        raise CouncilSandboxError("Stage 8 context report digest mismatch.")
    ctx = doc["context"]
    if not isinstance(ctx, dict) or ctx.get("status") not in (
        "LOCAL_OFFLINE_CONTEXT_CANDIDATE_UNATTESTED", "LOCAL_CONTEXT_QUARANTINED"
    ):
        raise CouncilSandboxError("Synthetic point-in-time context not available.")
    if (ctx.get("council_context_admitted") is not False
            or ctx.get("research_cycle_dispatched") is not False
            or ctx.get("seat_count") != 31
            or ctx.get("manifest_sha256") != manifest["manifest_sha256"]
            or ctx.get("contracts_sha256") != manifest["contracts_sha256"]):
        raise CouncilSandboxError("Context/manifest binding or admission claim mismatch.")
    _sha(ctx.get("context_sha256"), "context_sha256")
    _sha(ctx.get("decision_key"), "decision_key")
    if _utc(ctx.get("decision_at")) > cutoff:
        raise CouncilSandboxError("Context decision after review cutoff.")
    fields = ctx.get("context_fields")
    labels = list(FAMILIES) + list(DERIVED_INPUTS)
    if not isinstance(fields, list) or len(fields) != len(labels):
        raise CouncilSandboxError("Context source family inventory incomplete.")
    seen = set()
    for item in fields:
        if not isinstance(item, dict) or item.get("label") not in labels:
            raise CouncilSandboxError("Noncanonical context input.")
        label = item["label"]
        if label in seen or item.get("category") != (
                "SOURCE" if label in FAMILIES else "FEATURE"):
            raise CouncilSandboxError("Duplicate or mismatched context family.")
        seen.add(label)
        if item.get("independently_attested") is not False:
            raise CouncilSandboxError("Local input cannot self-attest.")
        if item.get("status") not in (
            "NOT_SUPPLIED", "QUARANTINED", "LOCAL_CANDIDATE_UNATTESTED"
        ):
            raise CouncilSandboxError("Invalid input admission state.")
        if item["status"] == "LOCAL_CANDIDATE_UNATTESTED":
            if item.get("issues") != [] or item.get("selected_record_id") is None:
                raise CouncilSandboxError("Context claims locally usable inconsistent input.")
    supplied = ctx.get("seat_input_preflight")
    if not isinstance(supplied, list) or len(supplied) != 31:
        raise CouncilSandboxError("31 preflight seat records required.")
    by_seat = {r.get("seat_number"): r for r in supplied if isinstance(r, dict)}
    if len(by_seat) != 31:
        raise CouncilSandboxError("Preflight duplicate or missing seat.")
    for seat in manifest["seats"]:
        item = by_seat.get(seat["seat"])
        if (item is None or item.get("seat_id") != seat["agent_id"]
                or item.get("stage") != seat["stage"]
                or item.get("required_family_labels") != seat["required_source_families"]
                or item.get("required_derived_labels") != seat["derived_labels"]
                or item.get("seat_dispatched") is not False
                or item.get("decision") != "ABSTAIN"):
            raise CouncilSandboxError("Context seat/preflight identity mismatch.")
    return ctx, sha256(raw).hexdigest()


def _schedule(path, cutoff, ctx):
    if path is None:
        return {}, None, None, None
    if ctx is None:
        raise CouncilSandboxError("Seat simulation requires a prior synthetic context report.")
    raw = _read(path, MAX_SCHEDULE_BYTES)
    doc = _decode(raw)
    _strict(doc, SIM_KEYS, "synthetic_seat_schedule")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH or doc["mode"] != "SYNTHETIC_OFFLINE"
            or _utc(doc["as_of"]) > cutoff
            or doc["decision_key"] != ctx["decision_key"]
            or doc["context_sha256"] != ctx["context_sha256"]):
        raise CouncilSandboxError("Schedule epoch, mode or context binding mismatch.")
    _id(doc["packet_id"], "packet_id")
    mock = doc["synthetic_model_mock_sha256"]
    if mock is not None:
        _sha(mock, "synthetic_model_mock_sha256")
    rows = doc["seat_results"]
    if not isinstance(rows, list) or len(rows) > 31:
        raise CouncilSandboxError("Seat result envelope count exceeds 31.")
    by_seat = {}
    for row in rows:
        _strict(row, SEAT_KEYS, "seat_result")
        number = _int(row["seat_number"], "seat_number", 31)
        if number == 0 or number in by_seat:
            raise CouncilSandboxError("Duplicate or invalid Council seat number.")
        _id(row["seat_id"], "seat_id")
        if row["stage"] not in ("PRIMARY", "GOVERNANCE", "SYNTHESIS"):
            raise CouncilSandboxError("Unknown seat stage.")
        if row["outcome"] not in RESULT_OUTCOMES:
            raise CouncilSandboxError("Unknown simulated seat outcome.")
        _int(row["started_ms"], "started_ms")
        _int(row["elapsed_ms"], "elapsed_ms")
        _int(row["peak_memory_bytes"], "peak_memory_bytes", MAX_WORKING_BYTES)
        if row["outcome"] in ("RESULT", "ABSTAIN"):
            if row["exception_code"] is not None:
                raise CouncilSandboxError("Result cannot carry an exception code.")
        else:
            if row["payload"] is not None:
                raise CouncilSandboxError("Fault/cancel result cannot contain a vote.")
            _id(row["exception_code"], "exception_code")
        if row["payload"] is not None:
            if not isinstance(row["payload"], dict):
                raise CouncilSandboxError("Structured result payload required.")
            size = len(json.dumps(row["payload"], sort_keys=True,
                                  separators=(",", ":"), allow_nan=False).encode())
            if size > MAX_PAYLOAD_BYTES:
                raise CouncilSandboxError("Per-seat serialized result exceeds budget.")
        by_seat[number] = row
    return by_seat, sha256(raw).hexdigest(), mock, doc["as_of"]


def _validate_payload(row, seat):
    payload = row["payload"]
    number = seat["seat"]
    if row["outcome"] not in ("RESULT", "ABSTAIN"):
        return False, None
    if not isinstance(payload, dict):
        return False, None
    if number <= 28:
        if set(payload) != PRIMARY_KEYS:
            return False, None
        direction = payload.get("direction")
        if ((row["outcome"] == "ABSTAIN") != (direction == "ABSTAIN")
                or direction not in ("BUY", "SELL", "ABSTAIN")):
            return False, None
        cited = payload["cited_labels"]
        allowed = set(seat["required_source_families"]) | set(seat["derived_labels"])
        if (not isinstance(cited, list) or len(cited) > 12
                or any(not isinstance(x, str) or x not in allowed for x in cited)
                or len(set(cited)) != len(cited)
                or (row["outcome"] == "RESULT" and not cited)):
            return False, None
        if not isinstance(payload["confidence"], str) or not CONF.fullmatch(payload["confidence"]):
            return False, None
        _id(payload["reason_code"], "reason_code")
        return True, direction
    if number == 29:
        if set(payload) != ADVOCATE_KEYS or payload.get("challenge") not in ("PASS", "CHALLENGE"):
            return False, None
        _id(payload["reason_code"], "reason_code")
        return row["outcome"] == "RESULT", payload["challenge"]
    if number == 30:
        if set(payload) != RISK_KEYS or type(payload.get("veto")) is not bool:
            return False, None
        _id(payload["reason_code"], "reason_code")
        return row["outcome"] == "RESULT", payload["veto"]
    if set(payload) != STRATEGIST_KEYS or payload.get("direction") not in DIRECTION:
        return False, None
    if not isinstance(payload["confidence"], str) or not CONF.fullmatch(payload["confidence"]):
        return False, None
    _id(payload["reason_code"], "reason_code")
    return row["outcome"] == "RESULT", payload["direction"]


def _review_seats(manifest, ctx, envelopes, mock):
    available = {} if ctx is None else {x["label"]: x for x in ctx["context_fields"]}
    seats = manifest["seats"]
    finish = {}
    primary_finishes = []
    results = []
    direction_votes = []
    reasons = set()
    veto = None
    challenge = None
    strategist = None
    for seat in seats:
        num = seat["seat"]
        row = envelopes.get(num)
        why = []
        if ctx is None:
            why.append("POINT_IN_TIME_CONTEXT_NOT_SUPPLIED")
        else:
            for label in seat["required_source_families"] + seat["derived_labels"]:
                if available[label]["status"] != "LOCAL_CANDIDATE_UNATTESTED":
                    why.append("REQUIRED_INPUT_UNAVAILABLE:" + label)
        if seat["needs_model_artifact_lineage"] and mock is None:
            why.append("SYNTHETIC_MODEL_MOCK_NOT_SUPPLIED")
        if row is None:
            status = "NOT_SUBMITTED"
            why.append("SEAT_NOT_SUBMITTED_NOT_ABSTENTION")
        else:
            if row["seat_id"] != seat["agent_id"] or row["stage"] != seat["stage"]:
                raise CouncilSandboxError("Seat number/name/stage identity mismatch.")
            finish[num] = row["started_ms"] + row["elapsed_ms"]
            if num <= 28:
                primary_finishes.append(finish[num])
            if finish[num] > CYCLE_BUDGET_MS:
                why.append("DECLARED_CYCLE_DEADLINE_EXCEEDED")
            if num == 29:
                if len(primary_finishes) != 28 or row["started_ms"] < max(primary_finishes):
                    why.append("DEVILS_ADVOCATE_BEFORE_PRIMARY_BARRIER")
                if any(r["status"] not in (
                        "LOCAL_SYNTHETIC_RESULT_UNATTESTED", "EXPLICIT_ABSTENTION"
                ) for r in results[:28]):
                    why.append("PRIMARY_OPINION_BARRIER_NOT_READY")
            if num == 30:
                if 29 not in finish or row["started_ms"] < finish[29]:
                    why.append("RISK_OFFICER_BEFORE_DEVILS_ADVOCATE")
                if results[28]["status"] != "LOCAL_SYNTHETIC_RESULT_UNATTESTED":
                    why.append("DEVILS_ADVOCATE_NOT_VALIDATED_BEFORE_RISK")
            if num == 31:
                if 30 not in finish or row["started_ms"] < finish[30]:
                    why.append("STRATEGIST_BEFORE_RISK_OFFICER")
                if results[29]["status"] != "LOCAL_SYNTHETIC_RESULT_UNATTESTED":
                    why.append("RISK_OFFICER_NOT_VALIDATED_BEFORE_STRATEGY")
            budget = PRIMARY_BUDGET_MS if num <= 28 else GOVERNANCE_BUDGET_MS
            if row["elapsed_ms"] > budget or row["outcome"] == "TIMEOUT":
                status = "TIMEOUT_CANCEL_REQUESTED"
                why.append("SIMULATED_SEAT_TIMEOUT")
            elif row["peak_memory_bytes"] > MAX_DECLARED_MEMORY_BYTES:
                status = "RESOURCE_BUDGET_EXCEEDED"
                why.append("DECLARED_MEMORY_LIMIT_EXCEEDED")
            elif row["outcome"] in ("EXCEPTION", "CANCELLED"):
                status = "FAULT_OR_CANCELLATION"
                why.append("SIMULATED_" + row["outcome"])
            else:
                valid, value = _validate_payload(row, seat)
                if not valid:
                    status = "INVALID_RESULT"
                    why.append("MALFORMED_SEAT_RESULT")
                elif row["outcome"] == "ABSTAIN":
                    status = "EXPLICIT_ABSTENTION"
                else:
                    status = "LOCAL_SYNTHETIC_RESULT_UNATTESTED"
                if num == 30 and valid:
                    veto = value  # A declared veto blocks even when other inputs fail.
                if num == 29 and valid:
                    challenge = value
                if num == 31 and valid:
                    strategist = value
            if why and status in ("LOCAL_SYNTHETIC_RESULT_UNATTESTED", "EXPLICIT_ABSTENTION"):
                status = "INPUT_OR_ORDER_BLOCKED"
            if num <= 28 and status == "LOCAL_SYNTHETIC_RESULT_UNATTESTED":
                direction_votes.append((value, Decimal(row["payload"]["confidence"])))
        reasons.update(why)
        results.append({
            "seat_number": num, "seat_id": seat["agent_id"], "stage": seat["stage"],
            "status": status, "reason_codes": sorted(set(why)),
            "required_source_families": list(seat["required_source_families"]),
            "required_derived_labels": list(seat["derived_labels"]),
            "submission_present": row is not None,
            "explicit_abstention": status == "EXPLICIT_ABSTENTION",
            "timeout_cancellation_requested": status == "TIMEOUT_CANCEL_REQUESTED",
            "outcome_digest": None if row is None else _digest(row),
            "seat_executed": False, "model_executed": False,
        })
    if 30 in envelopes and veto is None:
        reasons.add("RISK_OFFICER_NO_VALID_VETO_CLEARANCE")
    if 30 not in envelopes:
        reasons.add("RISK_OFFICER_MISSING")
    missing = [x["seat_id"] for x in results if not x["submission_present"]]
    primary = results[:28]
    accepted = [r for r in primary if r["status"] == "LOCAL_SYNTHETIC_RESULT_UNATTESTED"]
    abstain = [r for r in primary if r["status"] == "EXPLICIT_ABSTENTION"]
    buys = sum(v == "BUY" for v, _ in direction_votes)
    sells = sum(v == "SELL" for v, _ in direction_votes)
    total = buys + sells
    share = (Decimal(max(buys, sells)) / Decimal(total) if total else None)
    majority = ("BUY" if buys > sells else "SELL" if sells > buys else None)
    if missing:
        reasons.add("MISSING_SEATS_ARE_NOT_IMPLICIT_ABSTENTIONS")
    if len(accepted) + len(abstain) != 28:
        reasons.add("PRIMARY_SEAT_PARTICIPATION_INCOMPLETE")
    if len(abstain) > MAX_EXPLICIT_ABSTAINS or total < MIN_DIRECTIONAL:
        reasons.add("PRIMARY_DIRECTIONAL_QUORUM_NOT_MET")
    if share is None or share < MIN_MAJORITY_SHARE:
        reasons.add("PRIMARY_DIRECTIONAL_DISAGREEMENT")
    if challenge is None:
        reasons.add("DEVILS_ADVOCATE_CHALLENGE_NOT_VALIDATED")
    if veto is None:
        reasons.add("RISK_CLEARANCE_NOT_SUPPLIED")
    if strategist is None:
        reasons.add("CHIEF_STRATEGIST_RESULT_NOT_VALIDATED")
    if veto is True:
        reasons.add("RISK_OFFICER_VETO_FORCES_HOLD")
    if strategist in ("BUY", "SELL") and majority is not None and strategist != majority:
        reasons.add("STRATEGIST_DIRECTION_CONTRADICTS_PRIMARY_MAJORITY")
    if strategist == "HOLD":
        reasons.add("STRATEGIST_EXPLICIT_HOLD")
    if challenge == "CHALLENGE":
        reasons.add("DEVILS_ADVOCATE_UNRESOLVED_CHALLENGE")
    governance_all_valid = all(
        r["status"] == "LOCAL_SYNTHETIC_RESULT_UNATTESTED" for r in results[28:]
    )
    if not governance_all_valid:
        reasons.add("GOVERNANCE_SEAT_NOT_VALIDATED")
    locally_consistent = bool(
        not reasons and len(results) == 31 and share is not None and
        strategist == majority and veto is False
    )
    proposal = majority if locally_consistent else "HOLD"
    return {
        "seat_records": results, "submitted_seat_count": len(envelopes),
        "missing_seat_ids": missing,
        "explicit_primary_abstention_count": len(abstain),
        "valid_primary_directional_count": len(direction_votes),
        "primary_buy_count": buys, "primary_sell_count": sells,
        "primary_majority_share": None if share is None else str(share.quantize(Decimal("0.001"))),
        "primary_majority_direction": majority,
        "declared_risk_veto": veto,
        "devils_advocate_challenge": challenge,
        "declared_strategist_direction": strategist,
        "locally_consistent_synthetic_governance": locally_consistent,
        "synthetic_only_proposed_direction": proposal,
        "logical_wall_clock_enforced": False,
        "real_memory_limit_enforced": False,
        "agent_processes_spawned": 0,
        "timeout_cancellation_requests_simulated": sum(
            r["timeout_cancellation_requested"] for r in results),
        "reason_codes": sorted(reasons),
    }


def assess_stage8_council(*, epoch_id, as_of, policy_file=POLICY_PATH,
                          context_report=None, seat_schedule=None,
                          contracts_file="backend/agents/contracts.py",
                          manifest_file="backend/agents/council_manifest.py"):
    if epoch_id != EPOCH:
        raise CouncilSandboxError("Historical epoch mismatch; rebinding refused.")
    cutoff = _utc(as_of)
    if cutoff > datetime.now(UTC):
        raise CouncilSandboxError("Future assessment cutoff refused.")
    policy = sha256(_read(policy_file, 16384)).hexdigest()
    if policy != POLICY_SHA256:
        raise CouncilSandboxError("Immutable Oct 27 policy SHA-256 mismatch.")
    manifest = _manifest(contracts_file, manifest_file)
    ctx, ctx_file_sha = _context(context_report, cutoff, manifest)
    envelopes, schedule_sha, mock, schedule_asof = _schedule(seat_schedule, cutoff, ctx)
    results = _review_seats(manifest, ctx, envelopes, mock)
    blockers = [
        "STAGE7_G01_G07_INDEPENDENT_EVIDENCE_NOT_ADMITTED",
        "THIRTEEN_SOURCE_FAMILIES_NOT_INDEPENDENTLY_ATTESTED",
        "NO_OPERATOR_APPROVED_STAGE8_SHADOW_OR_COUNCIL_AUTHORIZATION",
        "SYNTHETIC_ENVELOPES_ARE_NOT_REAL_AGENT_EXECUTION",
        "RUNTIME_MEMORY_TIMEOUT_AND_CANCELLATION_ARE_DECLARED_NOT_ENFORCED",
        "NO_REAL_RISK_CLEARANCE_OR_FORECAST_REGISTRATION",
    ]
    if ctx is None:
        blockers.append("NO_POINT_IN_TIME_CONTEXT_REPORT_SUPPLIED")
    if schedule_sha is None:
        blockers.append("NO_SYNTHETIC_SEAT_SCHEDULE_SUPPLIED")
    if results["reason_codes"]:
        blockers.append("COUNCIL_SIMULATION_HAS_INCOMPLETE_OR_CONFLICTING_SEATS")
    report = {
        "version": VERSION, "scope": "P8_09_TO_P8_12_SYNTHETIC_COUNCIL_SANDBOX",
        "historical_epoch_id": EPOCH, "historical_policy_sha256": policy,
        "as_of": cutoff.isoformat(),
        "mode": "SYNTHETIC_OFFLINE_NO_EXECUTION",
        "dispatch_contract": {
            "canonical_seats": 31, "primary": 28, "governance": 2, "synthesis": 1,
            "manifest_sha256": manifest["manifest_sha256"],
            "contracts_sha256": manifest["contracts_sha256"],
            "primary_budget_ms_proposed": PRIMARY_BUDGET_MS,
            "governance_budget_ms_proposed": GOVERNANCE_BUDGET_MS,
            "cycle_budget_ms_proposed": CYCLE_BUDGET_MS,
            "peak_memory_bytes_proposed": MAX_DECLARED_MEMORY_BYTES,
            "max_result_serialized_bytes": MAX_PAYLOAD_BYTES,
            "minimum_directional_primary": MIN_DIRECTIONAL,
            "maximum_explicit_primary_abstentions": MAX_EXPLICIT_ABSTAINS,
            "minimum_primary_majority_share": str(MIN_MAJORITY_SHARE),
            "policy_version": POLICY_VERSION,
        },
        "context": {
            "status": "NOT_SUPPLIED" if ctx is None else "LOCAL_CONTEXT_REPORT_UNATTESTED",
            "context_sha256": None if ctx is None else ctx["context_sha256"],
            "decision_key": None if ctx is None else ctx["decision_key"],
            "context_file_sha256": ctx_file_sha,
            "context_independently_attested": False,
        },
        "synthetic_schedule": {
            "status": "NOT_SUPPLIED" if schedule_sha is None else "DECLARED_RESULTS_NOT_EXECUTED",
            "index_sha256": schedule_sha,
            "as_of": schedule_asof,
            "model_mock_digest": mock, "model_independently_accepted": False,
        },
        "rehearsal": results,
        "cycle_result": "HELD_NO_DISPATCH",
        "prediction_registered": False, "council_seats_dispatched": 0,
        "independent_external_gates_verified": 0,
        "independent_source_families_verified": 0,
        "read_only": True, "files_written": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_loaded": False,
        "training_authorized": False, "settlement_invoked": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "historical_epoch_rebound": False, "forward_epoch_created": False,
        "operator_approval_granted": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-council", action="store_true", required=True)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--context-report")
    parser.add_argument("--seat-schedule")
    parser.add_argument("--contracts-file", default="backend/agents/contracts.py")
    parser.add_argument("--manifest-file", default="backend/agents/council_manifest.py")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args(argv)
    try:
        report = assess_stage8_council(
            epoch_id=opts.epoch_id, as_of=opts.as_of, policy_file=opts.policy_file,
            context_report=opts.context_report, seat_schedule=opts.seat_schedule,
            contracts_file=opts.contracts_file, manifest_file=opts.manifest_file,
        )
    except (CouncilSandboxError, OSError, ValueError, TypeError):
        print("STAGE 8 COUNCIL SANDBOX BLOCKED: Unsafe, invalid or unverified input.")
        return 1
    if opts.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        r = report["rehearsal"]
        print("EURUSD ORACLE | Stage 8 P8-09..12 synthetic Council envelope rehearsal")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Static Council: 28 PRIMARY | 2 GOVERNANCE | 1 STRATEGIST")
        print("Context: " + report["context"]["status"])
        print("Seat schedule: " + report["synthetic_schedule"]["status"])
        print("Submitted seats: %d | Missing: %d | Explicit primary abstentions: %d" % (
            r["submitted_seat_count"], len(r["missing_seat_ids"]),
            r["explicit_primary_abstention_count"]))
        print("Synthetic veto: %s | Synthetic proposal: %s" % (
            str(r["declared_risk_veto"]), r["synthetic_only_proposed_direction"]))
        print("Real Council seats dispatched: 0 | Forecasts registered: 0")
        print("Independent gates/families: 0/11 | 0/13")
        for item in report["blockers"]:
            print("  BLOCKER " + item)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO AGENT/MODEL EXECUTION | NO BROKER | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
