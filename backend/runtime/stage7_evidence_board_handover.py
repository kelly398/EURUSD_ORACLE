
"""Stage 7 P7-21..24: OFFLINE G01-G07 board, risk preflight, Stage 8 handover.

Local files, signatures, issuers, receipts, Git refs, and operator statements are
CANDIDATE claims, not independent attestations. This tool NEVER accepts any
external gate, authorizes a collector or Council, or updates an Atlas ledger.
The Oct 27 epoch and policy are immutable. No broker/network/agent/model I/O.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

from backend.runtime.stage6_council_context_readiness import FAMILIES
from backend.runtime.stage7_family_vintage_admission import family_contracts

VERSION = "EURUSD_STAGE7_EVIDENCE_BOARD_HANDOVER_P7_21_24_V1"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
POLICY_PATH = "docs/clock_profiles/hfm_forward_20261027.json"
POLICY_SHA256 = "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088"
BRANCH = "feature/utc-research-m01-m08"
GATES = {
    "G01": ("BROKER_SERVER_IDENTITY", "HISTORICAL_EPOCH"),
    "G02": ("INDEPENDENT_BROKER_UTC", "HISTORICAL_EPOCH"),
    "G03": ("ORIGINAL_CAPTURE_CUSTODY", "HISTORICAL_EPOCH"),
    "G04": ("PROSPECTIVE_EPOCH_APPROVAL", "FUTURE_PROPOSAL_ONLY"),
    "G05": ("THIRTEEN_FAMILY_SOURCE_ADMISSION", "RESEARCH_SOURCE_REVIEW"),
    "G06": ("FEATURE_MODEL_POLICY_ANCESTRY", "RESEARCH_SOURCE_REVIEW"),
    "G07": ("RISK_RECOVERY_OPERATOR_HANDOVER", "STAGE7_HANDOVER"),
}
FUTURE_GATES = ("G08", "G09", "G10", "G11")
STOPS = (
    "MISSING_INDEPENDENT_CLOCK", "UNADMITTED_OR_REVOKED_SOURCE",
    "CLOCK_UNCERTAINTY_BREACH", "MALFORMED_PROVIDER_METADATA",
    "SEQUENCE_GAP", "INTERRUPTED_OR_PARTIAL_WRITE", "STORAGE_UNAVAILABLE",
    "RISK_LIMIT_BREACH", "OPERATOR_STOP",
)
FAULTS = (
    "MISSING_CLOCK_PROOF", "REVOKED_SOURCE", "STORAGE_FAILURE",
    "PARTIAL_WRITE", "DUPLICATE_OR_MISSING_SEQUENCE",
    "CLOCK_UNCERTAINTY_BREACH", "UNAUTHORIZED_RESTART",
    "ESCALATION_UNAVAILABLE",
)
ISOLATION_FIELDS = (
    "broker_connected", "collector_running", "council_dispatched",
    "training_running", "paper_or_live_order_enabled",
)
RESTART_FIELDS = (
    "automatic_restart", "operator_approval_required",
    "independent_gate_recheck_required", "new_epoch_review_required",
    "historical_epoch_preserved", "starts_in_hold",
)
GATE_INDEX_KEYS = {"schema_version", "historical_epoch_id", "packet_id", "as_of", "records"}
GATE_KEYS = {
    "gate_id", "evidence_id", "issuer_claim", "reviewer_claim", "scope_claim",
    "claim_state", "issued_at", "first_available_at", "reviewed_at_claim",
    "revoked_at_claim", "signature_sha256_claim", "independent_receipt_claim",
    "artifact_relative_path", "artifact_sha256",
}
RISK_KEYS = {
    "schema_version", "historical_epoch_id", "plan_id", "as_of", "risk_policy_version_claim",
    "clock_uncertainty_limit_ms", "max_source_gap_seconds", "max_quote_age_seconds",
    "retention_days", "data_isolation", "stop_conditions", "recovery_scenarios",
    "restart_policy", "operator_approval_claim", "independent_risk_signoff_claim",
}
RECOVERY_KEYS = {
    "fault", "expected_action", "simulation_result", "escalation_role",
    "restart_authorized",
}
LEDGER_KEYS = {
    "schema_version", "historical_epoch_id", "package_id", "as_of", "branch",
    "batch_commits", "windows_suite", "report_receipts", "evidence_receipts",
    "atlas_append_only_ticket", "operator_signoff_claim", "stage8_authorization_claim",
}
COMMIT_KEYS = {"batch_id", "commit_id"}
WINDOWS_KEYS = {"reported_at", "passed", "skipped", "allowlist", "operator_reported"}
REPORT_KEYS = {"batch_id", "report_sha256", "recorded_at"}
RECEIPT_KEYS = {"gate_id", "evidence_id", "artifact_sha256"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
NUMBER = re.compile(r"^(?:0|[1-9][0-9]{0,5})(?:\.[0-9]{1,3})?$")
SENSITIVE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|private[_-]?key|"
    r"account[_-]?(id|number)|bearer|authorization|login)"
)
UTC = timezone.utc
MAX_JSON_BYTES = 262144
MAX_ARTIFACT_BYTES = 1048576
MAX_TOTAL_ARTIFACT_BYTES = 8388608
MAX_GATE_CLAIMS = 32
MAX_OUTPUT_RECORDS = 128
STAGE7_BATCHES = (
    ("B1", "7216d39", "stage7_postcommit_evidence_baseline"),
    ("B2", "b999750", "stage7_broker_clock_identity_review"),
    ("B3", "2e9c5f4", "stage7_capture_epoch_readiness"),
    ("B4", "5dfc203", "stage7_family_vintage_admission"),
    ("B5", "f6327c7", "stage7_feature_model_causal_snapshot"),
    ("B6", None, "stage7_evidence_board_handover"),
)
WINDOWS_TEST_ALLOWLIST = tuple(
    "backend/runtime/test_" + name + ".py" for _, _, name in STAGE7_BATCHES
)
PRIOR_WINDOWS_RESULT = {
    "source": "OPERATOR_TERMINAL_OUTPUT_NOT_INDEPENDENTLY_AUDITED",
    "through_batch": "B5", "passed": 407, "skipped": 5,
}
PRIOR_REPORT_DIGESTS = {
    "B2": "8893ea51838f3e9a961077abce217837b0bb72aeaa03d83577d9f9497245562a",
    "B3": "86ca9296e6386b6b26f2f01ac355bd3ea7f2e3882cd61079863eb26ddc0cfab3",
    "B4": "555fe9fd7ce641d0cc9f7a0f8ea66c21b19f825a85eb7a74e879b3dbe4a44883",
    "B5": "6a1e26154385ce695da71a054b44adefbe1938e86f24fe15b5a77b52e7bf9842",
}


class EvidenceBoardError(ValueError):
    """Local evidence is unsafe or cannot be assessed without ambiguity."""


def _utc(value):
    if not isinstance(value, str):
        raise EvidenceBoardError("Explicit UTC timestamp required.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise EvidenceBoardError("Invalid UTC timestamp.") from None
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise EvidenceBoardError("Explicit UTC zero offset required.")
    return result.astimezone(UTC)


def _maybe_utc(value):
    return None if value is None else _utc(value)


def _ident(value, name):
    if not isinstance(value, str) or not IDENT.fullmatch(value) or SENSITIVE.search(value):
        raise EvidenceBoardError("Invalid or sensitive identifier: " + name)
    return value


def _hex(value, name):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise EvidenceBoardError("SHA-256 required: " + name)
    return value


def _strict(value, fields, name):
    if not isinstance(value, dict) or set(value) != fields:
        raise EvidenceBoardError("Strict schema mismatch: " + name)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EvidenceBoardError("Duplicate JSON key refused.")
        result[key] = value
    return result


def _decode(raw):
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                            parse_constant=lambda _: (_ for _ in ()).throw(
                                EvidenceBoardError("Nonfinite JSON refused.")))
    except EvidenceBoardError:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise EvidenceBoardError("Invalid UTF-8 JSON evidence.") from None
    if not isinstance(result, dict):
        raise EvidenceBoardError("JSON object required.")
    return result


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _no_links(path):
    p = Path(path).absolute()
    for parent in (p, *p.parents):
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            raise EvidenceBoardError("Symlink or junction refused.")
    return p


def _read(path, maximum):
    p = _no_links(path)
    if not p.is_file():
        raise EvidenceBoardError("Regular local evidence file required.")
    try:
        with p.open("rb") as f:
            data = f.read(maximum + 1)
    except OSError:
        raise EvidenceBoardError("Cannot read local evidence file.") from None
    if len(data) > maximum:
        raise EvidenceBoardError("Evidence size bound exceeded.")
    return data


def _child(root, path):
    if (not isinstance(path, str) or not 1 <= len(path) <= 180
            or "\\" in path or ":" in path or "@" in path or "\x00" in path
            or PurePosixPath(path).is_absolute() or SENSITIVE.search(path)
            or any(p in ("", ".", "..") for p in path.split("/"))):
        raise EvidenceBoardError("Unsafe or sensitive relative evidence path.")
    parent = _no_links(root)
    if not parent.is_dir():
        raise EvidenceBoardError("Evidence root directory required.")
    result = _no_links(parent.joinpath(*path.split("/")))
    if not result.resolve().is_relative_to(parent.resolve()):
        raise EvidenceBoardError("Evidence path escapes root.")
    return result


def _bounded_decimal(value, field, maximum):
    if not isinstance(value, str) or not NUMBER.fullmatch(value):
        raise EvidenceBoardError("Bounded decimal string required: " + field)
    try:
        val = Decimal(value)
    except InvalidOperation:
        raise EvidenceBoardError("Invalid decimal value.") from None
    if not val.is_finite() or val > maximum:
        raise EvidenceBoardError("Decimal exceeds hard parsing bound.")
    return val


def _gate_board(index_path, root, cutoff):
    records = []
    packet_sha = None
    local_bytes = 0
    if index_path is not None:
        if root is None:
            raise EvidenceBoardError("Gate evidence root required with index.")
        raw = _read(index_path, MAX_JSON_BYTES)
        doc = _decode(raw)
        _strict(doc, GATE_INDEX_KEYS, "gate_index")
        if type(doc["schema_version"]) is not int or doc["schema_version"] != 1 or doc["historical_epoch_id"] != EPOCH:
            raise EvidenceBoardError("Gate index historical epoch or schema mismatch.")
        _ident(doc["packet_id"], "packet_id")
        packet_cutoff = _utc(doc["as_of"])
        if packet_cutoff > cutoff:
            raise EvidenceBoardError("Gate packet not available by assessment cutoff.")
        values = doc["records"]
        if not isinstance(values, list) or len(values) > MAX_GATE_CLAIMS:
            raise EvidenceBoardError("Gate claim count out of bounds.")
        record_ids, artifact_paths = set(), set()
        for row in values:
            _strict(row, GATE_KEYS, "gate_claim")
            gate = row["gate_id"]
            if not isinstance(gate, str) or gate not in GATES:
                raise EvidenceBoardError("Only G01-G07 are in Stage 7 review scope.")
            claim_id = _ident(row["evidence_id"], "evidence_id")
            _ident(row["scope_claim"], "scope_claim")
            issuer = _ident(row["issuer_claim"], "issuer_claim")
            reviewer = _ident(row["reviewer_claim"], "reviewer_claim") if row["reviewer_claim"] is not None else None
            receipt = (_ident(row["independent_receipt_claim"], "independent_receipt_claim")
                       if row["independent_receipt_claim"] is not None else None)
            sig = _hex(row["signature_sha256_claim"], "signature_sha256_claim") if row["signature_sha256_claim"] is not None else None
            if claim_id in record_ids:
                raise EvidenceBoardError("Duplicate gate evidence identifier.")
            record_ids.add(claim_id)
            path = row["artifact_relative_path"]
            if path in artifact_paths:
                raise EvidenceBoardError("Repeated gate artifact path.")
            artifact_paths.add(path)
            artifact = _read(_child(root, path), MAX_ARTIFACT_BYTES)
            if sha256(artifact).hexdigest() != _hex(row["artifact_sha256"], "artifact_sha256"):
                raise EvidenceBoardError("Gate artifact SHA-256 mismatch.")
            local_bytes += len(artifact)
            if local_bytes > MAX_TOTAL_ARTIFACT_BYTES:
                raise EvidenceBoardError("Cumulative gate artifact size exceeded.")
            issued = _utc(row["issued_at"])
            available = _utc(row["first_available_at"])
            reviewed = _maybe_utc(row["reviewed_at_claim"])
            revoked = _maybe_utc(row["revoked_at_claim"])
            if not issued <= available <= packet_cutoff:
                raise EvidenceBoardError("Gate issue/availability chronology invalid.")
            if reviewed is not None and not available <= reviewed <= packet_cutoff:
                raise EvidenceBoardError("Gate review date unavailable at cutoff.")
            if revoked is not None and not available <= revoked <= packet_cutoff:
                raise EvidenceBoardError("Gate revocation date unavailable at cutoff.")
            state = row["claim_state"]
            if state not in ("PENDING", "FAILED", "APPROVED", "REVOKED"):
                raise EvidenceBoardError("Unsupported gate claim state.")
            if state in ("FAILED", "APPROVED") and reviewed is None:
                raise EvidenceBoardError("Failed/approved claims require review date.")
            if state == "REVOKED" and revoked is None:
                raise EvidenceBoardError("Revocation claim requires revocation date.")
            issues = []
            if row["scope_claim"] != GATES[gate][1]:
                issues.append("GATE_SCOPE_MISMATCH")
            if reviewer is not None and reviewer == issuer:
                issues.append("ISSUER_AND_REVIEWER_NOT_INDEPENDENT")
            if state in ("FAILED", "APPROVED") and reviewer is None:
                issues.append("REVIEWER_NOT_DECLARED")
            if state == "APPROVED" and (sig is None or receipt is None):
                issues.append("APPROVAL_CLAIM_LACKS_SIGNED_RECEIPT_METADATA")
            if revoked is not None and reviewed is not None and revoked < reviewed:
                issues.append("REVOCATION_PRECEDES_REVIEW")
            if state != "REVOKED" and revoked is not None:
                issues.append("REVOCATION_WITHOUT_REVOKED_STATE")
            records.append({
                "gate_id": gate, "evidence_id": claim_id, "issuer_claim": issuer,
                "reviewer_claim": reviewer, "claimed_reviewed_at": None if reviewed is None else reviewed.isoformat(),
                "claimed_revoked_at": None if revoked is None else revoked.isoformat(),
                "claimed_scope": row["scope_claim"], "claim_state": state,
                "signed_receipt_metadata_present": sig is not None and receipt is not None,
                "issues": issues,
            })
        packet_sha = sha256(raw).hexdigest()
    elif root is not None:
        raise EvidenceBoardError("Gate evidence root without index refused.")

    board = []
    for gate, (name, scope) in GATES.items():
        entries = [r for r in records if r["gate_id"] == gate]
        findings = set(x for r in entries for x in r["issues"])
        states = {r["claim_state"] for r in entries}
        if len(states) > 1:
            findings.add("CONFLICTING_LOCAL_GATE_CLAIMS")
        if any(r["claim_state"] == "REVOKED" for r in entries):
            status = "LOCAL_REVOCATION_CLAIM_QUARANTINED"
        elif findings:
            status = "QUARANTINED_LOCAL_CLAIM"
        elif "FAILED" in states:
            status = "LOCAL_FAILED_CLAIM"
        elif "APPROVED" in states:
            status = "LOCAL_APPROVAL_CLAIM_UNATTESTED"
        elif entries:
            status = "PENDING_LOCAL_REVIEW_CLAIM"
        else:
            status = "NOT_SUPPLIED"
        board.append({
            "gate_id": gate, "requirement": name, "required_scope": scope,
            "local_status": status, "evidence_ids": sorted(r["evidence_id"] for r in entries),
            "issuers_claimed": sorted({r["issuer_claim"] for r in entries}),
            "reviewers_claimed": sorted({r["reviewer_claim"] for r in entries if r["reviewer_claim"]}),
            "claimed_review_dates": sorted({r["claimed_reviewed_at"] for r in entries if r["claimed_reviewed_at"]}),
            "claim_count": len(entries), "findings": sorted(findings),
            "revocation_claim_present": "REVOKED" in states,
            "independently_verified": False, "external_reviewer_decision": "NOT_PROVIDED",
            "authorized_to_admit_sources": False,
        })
    return {
        "status": "NOT_SUPPLIED" if index_path is None else "LOCAL_GATE_CLAIMS_REVIEWED_UNATTESTED",
        "packet_sha256": packet_sha, "locally_hashed_artifacts": len(records),
        "locally_hashed_bytes": local_bytes, "gates": board,
        "independently_verified_gates_G01_G07": 0,
        "independently_verified_gates_G01_G11": 0,
        "future_gates_G08_G11": {name: "NOT_EVALUATED_BY_STAGE7" for name in FUTURE_GATES},
        "gate_claims_do_not_grant_approval": True,
    }


def _risk_review(path, cutoff):
    if path is None:
        return {
            "status": "NOT_SUPPLIED", "plan_sha256": None, "issues": [],
            "design_scenarios_reported": 0, "actual_recovery_tests_verified": 0,
            "operator_approval": False, "independent_risk_signoff": False,
            "restart_authorized": False, "risk_limits_operator_approved": False,
        }
    raw = _read(path, MAX_JSON_BYTES)
    doc = _decode(raw)
    _strict(doc, RISK_KEYS, "risk_recovery_plan")
    if type(doc["schema_version"]) is not int or doc["schema_version"] != 1 or doc["historical_epoch_id"] != EPOCH:
        raise EvidenceBoardError("Risk plan historical epoch or schema mismatch.")
    _ident(doc["plan_id"], "plan_id")
    _ident(doc["risk_policy_version_claim"], "risk_policy_version_claim")
    if _utc(doc["as_of"]) > cutoff:
        raise EvidenceBoardError("Risk plan is later than review cutoff.")
    uncertainty = _bounded_decimal(doc["clock_uncertainty_limit_ms"], "clock_uncertainty_limit_ms", Decimal(100000))
    gap, quote, retention = (doc[k] for k in (
        "max_source_gap_seconds", "max_quote_age_seconds", "retention_days"
    ))
    for name, val, maximum in (("max_source_gap_seconds", gap, 86400),
                               ("max_quote_age_seconds", quote, 86400),
                               ("retention_days", retention, 3650)):
        if type(val) is not int or not 0 <= val <= maximum:
            raise EvidenceBoardError("Invalid hard risk bound: " + name)
    isolation = doc["data_isolation"]
    _strict(isolation, set(ISOLATION_FIELDS), "data_isolation")
    for val in isolation.values():
        if type(val) is not bool:
            raise EvidenceBoardError("Isolation control must be boolean.")
    restart = doc["restart_policy"]
    _strict(restart, set(RESTART_FIELDS), "restart_policy")
    for val in restart.values():
        if type(val) is not bool:
            raise EvidenceBoardError("Restart control must be boolean.")
    for key in ("operator_approval_claim", "independent_risk_signoff_claim"):
        if type(doc[key]) is not bool:
            raise EvidenceBoardError("Approval claim must be boolean.")
    stop = doc["stop_conditions"]
    if not isinstance(stop, list) or any(not isinstance(x, str) for x in stop) or len(stop) > 32:
        raise EvidenceBoardError("Invalid stop-condition list.")
    tests = doc["recovery_scenarios"]
    if not isinstance(tests, list) or len(tests) > 32:
        raise EvidenceBoardError("Recovery scenario count invalid.")
    found = set()
    issues = []
    if set(stop) != set(STOPS) or len(stop) != len(STOPS):
        issues.append("MANDATORY_SAFE_STOP_CONTRACT_INCOMPLETE")
    if uncertainty > Decimal("1000"):
        issues.append("PROPOSED_CLOCK_UNCERTAINTY_EXCEEDS_DESIGN_CEILING")
    if gap > 300 or quote > 300 or retention < 7:
        issues.append("PROPOSED_RISK_OR_RETENTION_LIMITS_OUTSIDE_DESIGN_BOUNDS")
    if any(isolation.values()):
        issues.append("NONISOLATED_RESEARCH_PLAN_REJECTED")
    if (restart["automatic_restart"] or not all(
        restart[key] for key in RESTART_FIELDS if key != "automatic_restart"
    )):
        issues.append("UNSAFE_AUTOMATIC_OR_UNAPPROVED_RESTART_PATH")
    if doc["operator_approval_claim"] or doc["independent_risk_signoff_claim"]:
        issues.append("SELF_ASSERTED_OPERATOR_OR_RISK_APPROVAL_NOT_ADMISSIBLE")
    for row in tests:
        _strict(row, RECOVERY_KEYS, "recovery_scenario")
        fault = row["fault"]
        if not isinstance(fault, str) or fault not in FAULTS:
            raise EvidenceBoardError("Unknown recovery fault.")
        if fault in found:
            raise EvidenceBoardError("Duplicate recovery fault scenario.")
        found.add(fault)
        _ident(row["escalation_role"], "escalation_role")
        if row["simulation_result"] not in ("DESIGN_ONLY", "SIMULATED_PASS", "SIMULATED_FAIL"):
            raise EvidenceBoardError("Invalid simulated recovery result.")
        if type(row["restart_authorized"]) is not bool:
            raise EvidenceBoardError("Recovery restart flag must be boolean.")
        if row["expected_action"] != "QUARANTINE_HOLD_ESCALATE" or row["restart_authorized"]:
            issues.append("RECOVERY_FAULT_NOT_FAIL_CLOSED")
        if row["simulation_result"] == "SIMULATED_FAIL":
            issues.append("SIMULATED_RECOVERY_FAULT_FAILED")
    if found != set(FAULTS):
        issues.append("RECOVERY_FAULT_MATRIX_INCOMPLETE")
    return {
        "status": "LOCAL_RISK_PLAN_QUARANTINED" if issues else "LOCAL_RISK_PLAN_CANDIDATE_UNATTESTED",
        "plan_sha256": sha256(raw).hexdigest(), "issues": sorted(set(issues)),
        "design_scenarios_reported": len(tests), "actual_recovery_tests_verified": 0,
        "clock_uncertainty_ceiling_ms_proposed": str(uncertainty),
        "max_source_gap_seconds_proposed": gap,
        "max_quote_age_seconds_proposed": quote,
        "data_isolated_by_local_claim": not any(isolation.values()),
        "safe_stop_conditions_proposed": sorted(set(stop)),
        "operator_approval": False, "independent_risk_signoff": False,
        "restart_authorized": False, "risk_limits_operator_approved": False,
    }


def _ledger_review(path, cutoff):
    if path is None:
        return {
            "status": "NOT_SUPPLIED", "ledger_sha256": None,
            "batch_commit_claims": [], "windows_test_claim": None,
            "report_receipts": [], "evidence_receipt_count": 0,
            "atlas_updated": False, "stage8_authorized": False,
        }
    raw = _read(path, MAX_JSON_BYTES)
    doc = _decode(raw)
    _strict(doc, LEDGER_KEYS, "stage7_handover_ledger")
    if type(doc["schema_version"]) is not int or doc["schema_version"] != 1 or doc["historical_epoch_id"] != EPOCH:
        raise EvidenceBoardError("Handover ledger historical epoch or schema mismatch.")
    _ident(doc["package_id"], "package_id")
    _ident(doc["atlas_append_only_ticket"], "atlas_append_only_ticket")
    if _utc(doc["as_of"]) > cutoff or doc["branch"] != BRANCH:
        raise EvidenceBoardError("Handover ledger as-of or branch mismatch.")
    for k in ("operator_signoff_claim", "stage8_authorization_claim"):
        if type(doc[k]) is not bool:
            raise EvidenceBoardError("Ledger approval field must be boolean.")
    if doc["operator_signoff_claim"] or doc["stage8_authorization_claim"]:
        raise EvidenceBoardError("Unverified ledger cannot assert operational approval.")
    commits = doc["batch_commits"]
    if not isinstance(commits, list) or len(commits) > 6:
        raise EvidenceBoardError("Invalid batch commit claim count.")
    known = {b: h for b, h, _ in STAGE7_BATCHES}
    seen = set()
    for c in commits:
        _strict(c, COMMIT_KEYS, "batch_commit")
        name = c["batch_id"]
        if not isinstance(name, str) or name not in known or name in seen:
            raise EvidenceBoardError("Invalid or duplicate batch identity.")
        seen.add(name)
        commit = c["commit_id"]
        if not isinstance(commit, str) or not HEX_COMMIT.fullmatch(commit):
            raise EvidenceBoardError("Invalid Git commit claim.")
        if known[name] is not None and not commit.startswith(known[name]):
            raise EvidenceBoardError("Claimed Stage 7 commit contradicts prior Windows anchor.")
    win = doc["windows_suite"]
    if win is not None:
        _strict(win, WINDOWS_KEYS, "windows_suite")
        _utc(win["reported_at"])
        if _utc(win["reported_at"]) > cutoff:
            raise EvidenceBoardError("Windows test claim after review cutoff.")
        if (type(win["passed"]) is not int or type(win["skipped"]) is not int
                or not 0 <= win["passed"] <= 100000 or not 0 <= win["skipped"] <= 10000
                or win["operator_reported"] is not True):
            raise EvidenceBoardError("Invalid operator-reported regression counts.")
        al = win["allowlist"]
        if (not isinstance(al, list) or len(al) > 6
                or any(not isinstance(x, str) or x not in WINDOWS_TEST_ALLOWLIST for x in al)
                or len(set(al)) != len(al)):
            raise EvidenceBoardError("Windows test allowlist mismatch.")
    reports = doc["report_receipts"]
    if not isinstance(reports, list) or len(reports) > 12:
        raise EvidenceBoardError("Invalid report receipt count.")
    report_out = []
    for record in reports:
        _strict(record, REPORT_KEYS, "report_receipt")
        batch = record["batch_id"]
        if not isinstance(batch, str) or batch not in known:
            raise EvidenceBoardError("Unknown report batch.")
        stamp = _utc(record["recorded_at"])
        if stamp > cutoff:
            raise EvidenceBoardError("Report receipt after cutoff.")
        h = _hex(record["report_sha256"], "report_sha256")
        report_out.append({"batch_id": batch, "report_sha256": h})
    receipts = doc["evidence_receipts"]
    if not isinstance(receipts, list) or len(receipts) > MAX_OUTPUT_RECORDS:
        raise EvidenceBoardError("Invalid evidence receipt count.")
    for r in receipts:
        _strict(r, RECEIPT_KEYS, "evidence_receipt")
        if r["gate_id"] not in GATES:
            raise EvidenceBoardError("Evidence receipt outside G01-G07.")
        _ident(r["evidence_id"], "evidence_id")
        _hex(r["artifact_sha256"], "artifact_sha256")
    return {
        "status": "LOCAL_ATLAS_UPDATE_CANDIDATE_NOT_WRITTEN",
        "ledger_sha256": sha256(raw).hexdigest(),
        "batch_commit_claims": [{"batch_id": c["batch_id"], "commit_id": c["commit_id"]} for c in commits],
        "windows_test_claim": None if win is None else {
            "passed": win["passed"], "skipped": win["skipped"],
            "allowlist": win["allowlist"],
            "status": "OPERATOR_REPORTED_NOT_INDEPENDENTLY_REPRODUCED",
        },
        "report_receipts": sorted(report_out, key=lambda x: (x["batch_id"], x["report_sha256"])),
        "evidence_receipt_count": len(receipts),
        "atlas_updated": False, "stage8_authorized": False,
    }


def _git_run(root, *args, allowed_exit=(0,)):
    """Read-only LOCAL Git commands; never fetch/push or ask network service."""
    env = dict(os.environ)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"})
    try:
        process = subprocess.run(["git", "-C", str(root), *args],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, timeout=12, check=False, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise EvidenceBoardError("Local Git inspection unavailable.") from None
    if process.returncode not in allowed_exit or len(process.stdout) > 32768:
        raise EvidenceBoardError("Local Git object inspection failed.")
    return process.stdout.strip(), process.returncode


def _git_review(git_root):
    empty = {
        "status": "NOT_REQUESTED", "local_head": None, "local_origin_tracking_head": None,
        "branch": None, "verified_batch_prefixes": [], "batch6_commit_present": False,
        "tracked_worktree_clean": None, "tracking_ref_matches_head": None,
        "remote_server_independently_queried": False, "remote_custody_attested": False,
    }
    if git_root is None:
        return empty
    root = _no_links(git_root)
    if not root.is_dir():
        raise EvidenceBoardError("Git root must be an existing directory.")
    branch, _ = _git_run(root, "rev-parse", "--abbrev-ref", "HEAD")
    head, _ = _git_run(root, "rev-parse", "HEAD")
    origin, _ = _git_run(root, "rev-parse", "refs/remotes/origin/" + BRANCH)
    dirty, _ = _git_run(root, "status", "--porcelain", "--untracked-files=no")
    if not FULL_COMMIT.fullmatch(head) or not FULL_COMMIT.fullmatch(origin):
        raise EvidenceBoardError("Git commit SHA malformed.")
    found = []
    for name, prefix, module in STAGE7_BATCHES[:5]:
        resolved, _ = _git_run(root, "rev-parse", "--verify", prefix + "^{commit}")
        if not FULL_COMMIT.fullmatch(resolved) or not resolved.startswith(prefix):
            raise EvidenceBoardError("Git historical Stage 7 commit anchor mismatch.")
        _, ancestor_code = _git_run(root, "merge-base", "--is-ancestor", resolved, head,
                                    allowed_exit=(0, 1))
        if ancestor_code != 0:
            raise EvidenceBoardError("Stage 7 checkpoint not ancestor of local HEAD.")
        names, _ = _git_run(root, "show", "--format=", "--name-only", "--no-renames", resolved)
        expected = {"backend/runtime/" + module + ".py", "backend/runtime/test_" + module + ".py"}
        if set(names.splitlines()) != expected:
            raise EvidenceBoardError("Stage 7 anchor commit contains unexpected files.")
        found.append(name)
    batch6_present = False
    if head != _git_run(root, "rev-parse", "--verify", "f6327c7^{commit}")[0]:
        names, _ = _git_run(root, "show", "--format=", "--name-only", "--no-renames", head)
        batch6_module = STAGE7_BATCHES[-1][2]
        expected = {"backend/runtime/" + batch6_module + ".py",
                    "backend/runtime/test_" + batch6_module + ".py"}
        batch6_present = set(names.splitlines()) == expected
    ready = (branch == BRANCH and head == origin and not dirty and batch6_present)
    return {
        "status": "SIX_BATCHES_LOCALLY_PRESENT_UNATTESTED" if ready else "LOCAL_GIT_HANDOVER_NOT_COMPLETE",
        "local_head": head, "local_origin_tracking_head": origin, "branch": branch,
        "verified_batch_prefixes": found, "batch6_commit_present": batch6_present,
        "tracked_worktree_clean": not bool(dirty),
        "tracking_ref_matches_head": head == origin,
        "remote_server_independently_queried": False, "remote_custody_attested": False,
    }


def assess_stage7_handover(*, epoch_id, as_of, policy_file=POLICY_PATH,
                            gate_index=None, evidence_root=None, risk_plan=None,
                            handover_ledger=None, git_root=None):
    if epoch_id != EPOCH:
        raise EvidenceBoardError("Historical epoch mismatch; rebinding refused.")
    cutoff = _utc(as_of)
    if cutoff > datetime.now(UTC):
        raise EvidenceBoardError("Future assessment cutoff refused.")
    policy_sha = sha256(_read(policy_file, 16384)).hexdigest()
    if policy_sha != POLICY_SHA256:
        raise EvidenceBoardError("Immutable October 27 policy SHA-256 mismatch.")
    if tuple(FAMILIES) != tuple(family_contracts()):
        raise EvidenceBoardError("Canonical Stage 6 and 7 family contracts changed.")
    gate = _gate_board(gate_index, evidence_root, cutoff)
    risk = _risk_review(risk_plan, cutoff)
    ledger = _ledger_review(handover_ledger, cutoff)
    git = _git_review(git_root)

    blockers = [
        "G01_G07_REQUIRE_INDEPENDENT_EVIDENCE_AND_SEPARATE_REVIEW",
        "THIRTEEN_COUNCIL_FAMILIES_NOT_INDEPENDENTLY_ADMITTED",
        "BROKER_SERVER_IDENTITY_AND_UTC_NOT_INDEPENDENTLY_ATTESTED",
        "HISTORICAL_CAPTURE_CUSTODY_UNATTESTED_NO_REBIND",
        "FUTURE_FORWARD_EPOCH_NOT_SEPARATELY_APPROVED",
        "FEATURE_MODEL_ANCESTRY_NOT_INDEPENDENTLY_APPROVED",
        "RISK_RECOVERY_REAL_FAILURE_TESTS_NOT_VERIFIED",
        "NO_OPERATOR_AUTHORIZATION_TO_CONNECT_COLLECT_OR_RUN_COUNCIL",
        "STAGE8_RESEARCH_CONTEXT_NOT_AUTHORIZED",
    ]
    if gate["status"] == "NOT_SUPPLIED":
        blockers.append("NO_LOCAL_INDEPENDENT_GATE_CLAIM_INDEX_SUPPLIED")
    if any(g["local_status"].startswith(("QUARANTINED", "LOCAL_REVOCATION"))
           for g in gate["gates"]):
        blockers.append("CONFLICTING_REVOKED_OR_UNSAFE_GATE_CLAIMS_QUARANTINED")
    if risk["status"] == "NOT_SUPPLIED":
        blockers.append("NO_LOCAL_RISK_RECOVERY_PLAN_SUPPLIED")
    if risk["status"] == "LOCAL_RISK_PLAN_QUARANTINED":
        blockers.append("LOCAL_RISK_RECOVERY_PLAN_UNSAFE")
    if ledger["status"] == "NOT_SUPPLIED":
        blockers.append("NO_APPEND_ONLY_ATLAS_HANDOVER_LEDGER_SUPPLIED")
    if git["status"] == "NOT_REQUESTED":
        blockers.append("CURRENT_LOCAL_GIT_COMMIT_PROOF_NOT_REQUESTED")
    elif git["status"] != "SIX_BATCHES_LOCALLY_PRESENT_UNATTESTED":
        blockers.append("FINAL_STAGE7_COMMIT_OR_TRACKING_CHECK_PENDING")

    paths = [
        {"batch_id": batch, "source_path": "backend/runtime/" + name + ".py",
         "test_path": "backend/runtime/test_" + name + ".py",
         "prior_operator_reported_commit_prefix": prefix}
        for batch, prefix, name in STAGE7_BATCHES
    ]
    report = {
        "version": VERSION, "scope": "P7_21_TO_P7_24_READ_ONLY_STAGE7_HANDOVER",
        "as_of": cutoff.isoformat(), "historical_epoch_id": EPOCH,
        "historical_clock_policy": {
            "path": POLICY_PATH, "sha256": policy_sha,
            "original_raw_bytes_matched": True, "broker_utc_independently_attested": False,
            "historical_epoch_rebound": False,
        },
        "independent_gate_board": gate,
        "risk_and_recovery_preflight": risk,
        "source_readiness_decision": {
            "code_integrity": git["status"],
            "independent_evidence": "BLOCKED_NOT_ATTESTED",
            "clock_confidence": "BLOCKED_NO_INDEPENDENT_UTC_PROOF",
            "source_family_coverage": {"contract_count": 13, "independently_admitted": 0},
            "model_and_feature_ancestry": "UNATTESTED_NO_COUNCIL_MODEL_ADMISSION",
            "risk_recovery_real_tests": "NOT_INDEPENDENTLY_VERIFIED",
            "operator_approval": "NOT_GRANTED",
            "stage7_engineering_completion": (
                "LOCAL_SIX_COMMIT_CANDIDATE_PENDING_WINDOWS_CONFIRMATION"
                if git["status"] == "SIX_BATCHES_LOCALLY_PRESENT_UNATTESTED"
                else "BATCH6_PENDING_WINDOWS_TESTS_AND_GIT_COMMIT"
            ),
            "source_ingestion_allowed": False,
            "research_council_dispatch_allowed": False,
            "stage8_external_readiness": "BLOCKED",
            "final_action": "HOLD",
        },
        "stage7_handover": {
            "planned_checkpoint_count": 24, "operator_reported_prior_committed": 20,
            "local_committed_checkpoint_candidate": (
                24 if git["status"] == "SIX_BATCHES_LOCALLY_PRESENT_UNATTESTED" else 20
            ),
            "batch6_engineering_checkpoints_pending": (
                [] if git["status"] == "SIX_BATCHES_LOCALLY_PRESENT_UNATTESTED"
                else ["P7-21", "P7-22", "P7-23", "P7-24"]
            ),
            "batch_file_allowlist": paths,
            "windows_test_allowlist": list(WINDOWS_TEST_ALLOWLIST),
            "prior_operator_reported_windows_regression": PRIOR_WINDOWS_RESULT,
            "previously_reported_snapshot_digests": PRIOR_REPORT_DIGESTS,
            "optional_local_atlas_ledger": ledger,
            "local_git_review": git,
            "atlas_written_by_this_tool": False,
            "operator_must_append_final_commit_test_and_report_receipt": True,
            "stage8_authorization": False,
            "stage8_requirements": [
                "Independent qualified review and nonrevoked evidence for G01-G07",
                "Provider-authenticated source, UTC and clock-uncertainty proof",
                "Separate prospective forward epoch charter and operator consent",
                "Independently admitted 13 Council input families or approved seat-specific abstention rules",
                "Reviewed feature/model/risk/evaluation ancestry with causal receipts",
                "Real storage, safe-stop, revocation, recovery and escalation acceptance",
                "Completed Stage 7 Windows allowlist, exact six-commit Git custody and append-only Atlas record",
                "Explicit separately scoped authorization for research-only Stage 8 context",
            ],
        },
        "independent_gates_verified": 0,
        "independent_source_families_verified": 0,
        "remote_proof_queried": False, "read_only": True, "files_written": False,
        "historical_files_modified": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "model_loaded": False,
        "training_authorized": False, "settlement_invoked": False,
        "paper_orders_authorized": False, "live_orders_authorized": False,
        "forward_epoch_created": False, "operator_approval_granted": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-gates", action="store_true", required=True)
    parser.add_argument("--read-only-handover", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--gate-index")
    parser.add_argument("--evidence-root")
    parser.add_argument("--risk-plan")
    parser.add_argument("--handover-ledger")
    parser.add_argument("--git-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_stage7_handover(
            epoch_id=args.epoch_id, as_of=args.as_of, policy_file=args.policy_file,
            gate_index=args.gate_index, evidence_root=args.evidence_root,
            risk_plan=args.risk_plan, handover_ledger=args.handover_ledger,
            git_root=args.git_root,
        )
    except (EvidenceBoardError, OSError, TypeError, ValueError):
        print("STAGE 7 EVIDENCE BOARD BLOCKED: Local input or Git evidence invalid.")
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 7 P7-21..24 independent gate board and handover")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Gate claims: " + report["independent_gate_board"]["status"])
        print("G01-G07 independent verified: 0/7 | G01-G11: 0/11")
        print("Council source families independently admitted: 0/13")
        print("Risk/recovery preflight: " + report["risk_and_recovery_preflight"]["status"])
        print("Local Git continuity: " + report["stage7_handover"]["local_git_review"]["status"])
        print("Atlas: NOT_WRITTEN_BY_THIS_TOOL | Stage 8: NOT_AUTHORIZED")
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO BROKER | NO COLLECTOR | NO COUNCIL | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
