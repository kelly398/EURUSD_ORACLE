
"""P7-09..12: offline capture ancestry, future epoch proposal, recovery preflight.

Strict local artifact checking is NOT independent capture/source attestation.
Never repairs or rebinds 2026-10-27 history. Never approves a future epoch.
No network, broker, collector, Council, training, settlement or orders.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

VERSION = "EURUSD_STAGE7_CAPTURE_EPOCH_READINESS_P7_09_12_V1"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
POLICY_PATH = "docs/clock_profiles/hfm_forward_20261027.json"
POLICY_SHA256 = "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088"
UTC = timezone.utc
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
SENSITIVE = re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|private[_-]?key|account[_-]?(id|number)|bearer|authorization|login)")
KIND_PARENTS = {
    "PROVIDER_PAYLOAD": set(),
    "CAPTURED_TICK": {"PROVIDER_PAYLOAD"},
    "M5_CANDLE": {"CAPTURED_TICK", "PROVIDER_PAYLOAD"},
    "STORAGE_RECORD": {"CAPTURED_TICK", "M5_CANDLE"},
    "DERIVED_CONTEXT": {"STORAGE_RECORD"},
    "CAPTURE_RECEIPT": {"PROVIDER_PAYLOAD", "CAPTURED_TICK", "M5_CANDLE"},
    "ARCHIVED_REPORT": {"CAPTURE_RECEIPT", "STORAGE_RECORD", "DERIVED_CONTEXT"},
}
INDEX_FIELDS = {"schema_version", "historical_epoch_id", "packet_id", "as_of", "records"}
RECORD_FIELDS = {
    "record_id", "kind", "provider_claim", "source_sequence", "raw_timestamp_claim",
    "observed_at_claim", "first_available_at_claim", "captured_at_claim",
    "claimed_receipt_at", "relative_path", "file_sha256", "transform_code_path",
    "transform_code_sha256", "upstream_ids", "report_parent_sha256",
}
CHARTER_FIELDS = {
    "schema_version", "proposal_id", "historical_epoch_id", "proposed_epoch_id",
    "created_at", "proposed_start_at", "proposed_end_at", "research_scope",
    "isolation_mode", "provider_claims", "max_clock_uncertainty_ms",
    "maximum_capture_gap_seconds", "retention_days", "required_stop_conditions",
    "required_external_gates", "required_approval_roles", "expected_evidence_packages",
    "approval_signature_claims",
}
STOPS = (
    "MISSING_INDEPENDENT_CLOCK", "UNADMITTED_SOURCE", "INVALID_PROVIDER_METADATA",
    "SEQUENCE_GAP", "MISSING_RECEIPT", "INTERRUPTED_OR_PARTIAL_WRITE",
    "STORAGE_UNAVAILABLE", "OPERATOR_STOP",
)
ROLES = ("INDEPENDENT_SOURCE_REVIEWER", "INDEPENDENT_TIME_REVIEWER", "RISK_OWNER", "SYSTEM_OPERATOR")
PACKAGES = ("BROKER_IDENTITY", "UTC_REFERENCE", "ORIGINAL_CAPTURE", "SOURCE_CHAIN", "RISK_APPROVAL", "RECOVERY_PLAN")
GATES = tuple("G%02d" % i for i in range(1, 8))
FAULTS = (
    "MALFORMED_PROVIDER_METADATA", "INTERRUPTED_WRITE", "MISSING_CLOCK_PROOF",
    "STORAGE_FAILURE", "DUPLICATE_SEQUENCE", "MISSING_OR_REVOKED_RECEIPT",
)
MAX_INDEX_BYTES = 262144
MAX_CHARTER_BYTES = 32768
MAX_ARTIFACT_BYTES = 1048576
MAX_CODE_BYTES = 1048576
MAX_TOTAL_BYTES = 12582912
MAX_RECORDS = 128


class CaptureEpochError(ValueError):
    """The candidate cannot be safely evaluated."""


def _utc(value):
    if not isinstance(value, str):
        raise CaptureEpochError("Explicit UTC timestamp required.")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        raise CaptureEpochError("Invalid UTC timestamp.") from None
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise CaptureEpochError("UTC timestamp must have an explicit zero offset.")
    return result.astimezone(UTC)


def _id(value, name):
    if not isinstance(value, str) or not IDENT.fullmatch(value) or SENSITIVE.search(value):
        raise CaptureEpochError("Invalid or sensitive identifier: " + name)
    return value


def _hex(value, name):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise CaptureEpochError("SHA-256 expected: " + name)
    return value


def _strict(value, fields, name):
    if not isinstance(value, dict) or set(value) != fields:
        raise CaptureEpochError("Strict schema mismatch: " + name)


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise CaptureEpochError("Duplicate JSON key refused.")
        obj[key] = value
    return obj


def _decode(raw):
    try:
        data = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(CaptureEpochError("Nonfinite JSON refused.")),
        )
    except CaptureEpochError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise CaptureEpochError("Invalid UTF-8 JSON object.") from None
    if not isinstance(data, dict):
        raise CaptureEpochError("JSON object expected.")
    return data


def _digest(obj):
    return sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _without_links(path):
    p = Path(path).absolute()
    for part in (p, *p.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise CaptureEpochError("Symlink or junction refused.")
    return p


def _read(path, max_bytes):
    p = _without_links(path)
    if not p.is_file():
        raise CaptureEpochError("Existing regular evidence file required.")
    try:
        with p.open("rb") as stream:
            content = stream.read(max_bytes + 1)
    except OSError:
        raise CaptureEpochError("Cannot read local evidence.") from None
    if len(content) > max_bytes:
        raise CaptureEpochError("Evidence exceeds size bound.")
    return content


def _child(root, name):
    if (not isinstance(name, str) or not 1 <= len(name) <= 180
            or "\\" in name or ":" in name or "\x00" in name or "@" in name
            or SENSITIVE.search(name) or PurePosixPath(name).is_absolute()
            or any(p in ("", ".", "..") for p in name.split("/"))):
        raise CaptureEpochError("Unsafe or sensitive relative evidence path.")
    base = _without_links(root)
    if not base.is_dir():
        raise CaptureEpochError("Evidence root directory required.")
    candidate = _without_links(base.joinpath(*name.split("/")))
    if not candidate.resolve().is_relative_to(base.resolve()):
        raise CaptureEpochError("Evidence path escapes root.")
    return candidate


def _timestamp_label(raw):
    if (not isinstance(raw, str) or not 1 <= len(raw) <= 60
            or "\n" in raw or "\r" in raw or "\x00" in raw
            or any(ord(c) < 32 or ord(c) > 126 for c in raw)
            or SENSITIVE.search(raw)):
        raise CaptureEpochError("Invalid raw timestamp label.")
    return sha256(raw.encode("ascii")).hexdigest()


def _load_capture(index_file, root, cutoff):
    if root is None:
        raise CaptureEpochError("Capture root required with index.")
    raw = _read(index_file, MAX_INDEX_BYTES)
    doc = _decode(raw)
    _strict(doc, INDEX_FIELDS, "capture_index")
    if (type(doc["schema_version"]) is not int or doc["schema_version"] != 1
            or doc["historical_epoch_id"] != EPOCH):
        raise CaptureEpochError("Historical epoch or capture schema mismatch.")
    _id(doc["packet_id"], "packet_id")
    as_of = _utc(doc["as_of"])
    if as_of > cutoff:
        raise CaptureEpochError("Capture index claims future availability.")
    records = doc["records"]
    if not isinstance(records, list) or len(records) > MAX_RECORDS:
        raise CaptureEpochError("Capture record count invalid.")
    seen_ids, seen_paths, code_cache = set(), set(), {}
    total_bytes = 0
    entries = []
    for r in records:
        _strict(r, RECORD_FIELDS, "capture_record")
        rid = _id(r["record_id"], "record_id")
        if rid in seen_ids:
            raise CaptureEpochError("Duplicate capture record identifier.")
        seen_ids.add(rid)
        if not isinstance(r["kind"], str) or r["kind"] not in KIND_PARENTS:
            raise CaptureEpochError("Unsupported capture artifact kind.")
        _id(r["provider_claim"], "provider_claim")
        seq = r["source_sequence"]
        if seq is not None and (type(seq) is not int or not 0 <= seq <= 1000000000):
            raise CaptureEpochError("Invalid source sequence.")
        raw_timestamp_sha = _timestamp_label(r["raw_timestamp_claim"])
        observed = _utc(r["observed_at_claim"])
        available = _utc(r["first_available_at_claim"])
        captured = _utc(r["captured_at_claim"])
        receipt = _utc(r["claimed_receipt_at"])
        if receipt > as_of or available > as_of:
            raise CaptureEpochError("Claimed evidence unavailable at index cutoff.")
        path = r["relative_path"]
        if path in seen_paths:
            raise CaptureEpochError("Local artifact path reused.")
        seen_paths.add(path)
        artifact = _read(_child(root, path), MAX_ARTIFACT_BYTES)
        expected = _hex(r["file_sha256"], "file_sha256")
        if sha256(artifact).hexdigest() != expected:
            raise CaptureEpochError("Capture artifact byte hash mismatch.")
        total_bytes += len(artifact)
        codepath, codehash = r["transform_code_path"], r["transform_code_sha256"]
        if (codepath is None) != (codehash is None):
            raise CaptureEpochError("Transformation code path/hash must appear together.")
        if codepath is not None:
            expected_code = _hex(codehash, "transform_code_sha256")
            if codepath not in code_cache:
                src = _read(_child(root, codepath), MAX_CODE_BYTES)
                if codepath.endswith(".py"):
                    try:
                        ast.parse(src.decode("utf-8-sig"))
                    except (UnicodeError, SyntaxError, RecursionError):
                        raise CaptureEpochError("Transformation Python AST invalid.") from None
                code_cache[codepath] = sha256(src).hexdigest()
                total_bytes += len(src)
            if code_cache[codepath] != expected_code:
                raise CaptureEpochError("Transformation code byte hash mismatch.")
        parents = r["upstream_ids"]
        if (not isinstance(parents, list) or len(parents) > 16
                or any(_id(x, "upstream_id") != x for x in parents)
                or len(set(parents)) != len(parents) or rid in parents):
            raise CaptureEpochError("Invalid upstream record links.")
        prior = r["report_parent_sha256"]
        if prior is not None:
            _hex(prior, "report_parent_sha256")
            if r["kind"] != "ARCHIVED_REPORT":
                raise CaptureEpochError("Report ancestry allowed only for archived reports.")
        entries.append({
            "record_id": rid, "kind": r["kind"], "source": r["provider_claim"],
            "sequence": seq, "raw_timestamp_sha256": raw_timestamp_sha,
            "observed": observed, "available": available, "captured": captured,
            "receipt": receipt, "sha256": expected, "code_matched": codepath is not None,
            "parents": parents, "prior_report_sha256": prior,
        })
        if total_bytes > MAX_TOTAL_BYTES:
            raise CaptureEpochError("Total artifact/code byte bound exceeded.")
    return {"raw_index_sha256": sha256(raw).hexdigest(), "as_of": as_of.isoformat(),
            "records": records, "entries": entries, "locally_hashed_bytes": total_bytes,
            "matched_code_versions": len(code_cache)}


def _inspect_capture(capture, previous):
    if capture is None:
        return {
            "status": "NOT_SUPPLIED", "record_count": 0, "record_kinds": {},
            "local_hash_matched": 0, "transformation_files_hash_matched": 0,
            "timestamp_discrepancies": [], "sequence_findings": [],
            "archived_reports": 0, "append_only_prefix": "NOT_SUPPLIED",
            "original_provider_authenticated": False,
            "historical_broker_utc_attested": False,
        }
    entries = capture["entries"]
    counts = {kind: sum(r["kind"] == kind for r in entries) for kind in KIND_PARENTS}
    timestamp_discrepancies = []
    sequence_findings = []
    seq_last, fingerprints = {}, set()
    for entry in entries:
        rid = entry["record_id"]
        if entry["observed"] > entry["captured"]:
            timestamp_discrepancies.append({"record_id": rid, "finding": "OBSERVED_AFTER_CAPTURE_CLAIM"})
        if entry["available"] > entry["captured"]:
            timestamp_discrepancies.append({"record_id": rid, "finding": "FIRST_AVAILABLE_AFTER_CAPTURE_CLAIM"})
        if entry["captured"] > entry["receipt"]:
            timestamp_discrepancies.append({"record_id": rid, "finding": "CAPTURE_AFTER_CLAIMED_RECEIPT"})
        if entry["receipt"] < entry["available"]:
            timestamp_discrepancies.append({"record_id": rid, "finding": "RECEIPT_BEFORE_FIRST_AVAILABILITY"})
        sequence = entry["sequence"]
        if sequence is not None:
            key = (entry["kind"], entry["source"])
            previous_seq = seq_last.get(key)
            if previous_seq is not None:
                if sequence == previous_seq:
                    sequence_findings.append({"record_id": rid, "finding": "DUPLICATE_SOURCE_SEQUENCE"})
                elif sequence < previous_seq:
                    sequence_findings.append({"record_id": rid, "finding": "REGRESSED_SOURCE_SEQUENCE"})
                elif sequence > previous_seq + 1:
                    sequence_findings.append({"record_id": rid, "finding": "SOURCE_SEQUENCE_GAP",
                                              "missing_count": sequence - previous_seq - 1})
            seq_last[key] = sequence
        fingerprint = (entry["kind"], entry["source"], entry["sha256"])
        if fingerprint in fingerprints:
            sequence_findings.append({"record_id": rid, "finding": "DUPLICATED_OBSERVATION_BYTES"})
        fingerprints.add(fingerprint)
    quarantined = bool(timestamp_discrepancies or sequence_findings)
    return {
        "status": "LOCAL_CAPTURE_FINDINGS_QUARANTINED" if quarantined else "LOCAL_CAPTURE_CANDIDATE_UNATTESTED",
        "record_count": len(entries), "record_kinds": counts,
        "local_hash_matched": len(entries),
        "transformation_files_hash_matched": capture["matched_code_versions"],
        "timestamp_discrepancies": timestamp_discrepancies,
        "sequence_findings": sequence_findings,
        "archived_reports": counts["ARCHIVED_REPORT"],
        "append_only_prefix": previous,
        "original_provider_authenticated": False, "historical_broker_utc_attested": False,
    }


def _inspect_chain(capture):
    if capture is None:
        return {"status": "NOT_SUPPLIED", "checked_links": 0, "findings": [],
                "council_source_chain_admitted": False}
    entries = capture["entries"]
    lookup = {r["record_id"]: r for r in entries}
    seen = set()
    past_reports = set()
    findings = []
    checked = 0
    for entry in entries:
        kind = entry["kind"]
        parents = entry["parents"]
        rid = entry["record_id"]
        if not parents and kind not in ("PROVIDER_PAYLOAD", "ARCHIVED_REPORT"):
            findings.append({"record_id": rid, "finding": "MISSING_UPSTREAM_CUSTODY_LINK"})
        if parents and kind == "PROVIDER_PAYLOAD":
            findings.append({"record_id": rid, "finding": "UNEXPECTED_UPSTREAM_PROVIDER_LINK"})
        for pid in parents:
            checked += 1
            if pid not in lookup:
                findings.append({"record_id": rid, "finding": "UNRESOLVED_UPSTREAM_RECORD"})
                continue
            if pid not in seen:
                findings.append({"record_id": rid, "finding": "FORWARD_OR_NONCAUSAL_RECORD_LINK"})
                continue
            parent = lookup[pid]
            if parent["kind"] not in KIND_PARENTS[kind]:
                findings.append({"record_id": rid, "finding": "UNSUPPORTED_SOURCE_CHAIN_EDGE"})
            if parent["available"] > entry["available"]:
                findings.append({"record_id": rid, "finding": "PARENT_AVAILABLE_AFTER_DERIVED_CONTEXT"})
        if kind in ("M5_CANDLE", "STORAGE_RECORD", "DERIVED_CONTEXT") and not entry["code_matched"]:
            findings.append({"record_id": rid, "finding": "TRANSFORMATION_CODE_NOT_SUPPLIED"})
        if kind == "ARCHIVED_REPORT":
            prior = entry["prior_report_sha256"]
            if past_reports and prior is None:
                findings.append({"record_id": rid, "finding": "ARCHIVED_REPORT_PARENT_NOT_DECLARED"})
            elif prior is not None and prior not in past_reports:
                findings.append({"record_id": rid, "finding": "ARCHIVED_REPORT_PARENT_HASH_NOT_FOUND"})
            past_reports.add(entry["sha256"])
        seen.add(rid)
    return {
        "status": "LOCAL_CHAIN_INCOMPLETE_OR_CONFLICTING" if findings else "LOCAL_CHAIN_CONSISTENT_UNATTESTED",
        "checked_links": checked, "findings": findings,
        "locally_hashed_bytes": capture["locally_hashed_bytes"],
        "independently_verified_custody_links": 0,
        "council_source_chain_admitted": False,
    }


def inspect_capture_custody(*, index_file=None, root=None, previous_index=None,
                            previous_root=None, as_of):
    cutoff = _utc(as_of)
    if index_file is None:
        if root is not None or previous_index is not None or previous_root is not None:
            raise CaptureEpochError("Capture evidence root/snapshot requires current index.")
        return _inspect_capture(None, "NOT_SUPPLIED"), _inspect_chain(None), None
    current = _load_capture(index_file, root, cutoff)
    previous = "NOT_SUPPLIED"
    if previous_index is None:
        if previous_root is not None:
            raise CaptureEpochError("Previous root without snapshot index.")
    else:
        if previous_root is None:
            raise CaptureEpochError("Previous snapshot requires its evidence root.")
        old = _load_capture(previous_index, previous_root, cutoff)
        if (old["as_of"] > current["as_of"]
                or old["records"] != current["records"][:len(old["records"])]):
            raise CaptureEpochError("Previous custody prefix removed or rewritten.")
        previous = "LOCAL_APPEND_ONLY_PREFIX_MATCH_UNATTESTED"
    return _inspect_capture(current, previous), _inspect_chain(current), current["raw_index_sha256"]


def inspect_future_epoch_charter(*, charter_file=None, as_of):
    cutoff = _utc(as_of)
    if charter_file is None:
        return {"status": "NOT_SUPPLIED", "proposal_digest": None,
                "future_epoch_distinct": False, "approval_signature_count": 0,
                "operational_approval": False, "collector_start_permitted": False}
    raw = _read(charter_file, MAX_CHARTER_BYTES)
    doc = _decode(raw)
    _strict(doc, CHARTER_FIELDS, "prospective_epoch_charter")
    if type(doc["schema_version"]) is not int or doc["schema_version"] != 1:
        raise CaptureEpochError("Unsupported epoch charter schema.")
    _id(doc["proposal_id"], "proposal_id")
    proposed = _hex(doc["proposed_epoch_id"], "proposed_epoch_id")
    if doc["historical_epoch_id"] != EPOCH or proposed == EPOCH or proposed == "0" * 64:
        raise CaptureEpochError("Prospective identity must differ from immutable historical epoch.")
    created = _utc(doc["created_at"])
    start = _utc(doc["proposed_start_at"])
    end = _utc(doc["proposed_end_at"])
    if not created <= cutoff < start < end or end - start > timedelta(days=14):
        raise CaptureEpochError("Proposal not future-only or interval too large.")
    if doc["research_scope"] != "RESEARCH_PREPARATION_NO_EXECUTION":
        raise CaptureEpochError("Charter requests unsupported activity.")
    if doc["isolation_mode"] != "NO_BROKER_CONNECTIONS_NO_COLLECTOR_START":
        raise CaptureEpochError("Charter not isolated from live operations.")
    providers = doc["provider_claims"]
    if (not isinstance(providers, list) or len(providers) > 13
            or any(not isinstance(x, str) for x in providers)
            or len(set(providers)) != len(providers)
            or any(_id(x, "provider_claim") != x for x in providers)):
        raise CaptureEpochError("Invalid proposed provider claims.")
    value = doc["max_clock_uncertainty_ms"]
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9]\d{0,3})(?:\.\d{1,3})?", value):
        raise CaptureEpochError("Invalid proposed clock uncertainty string.")
    try:
        uncertainty = Decimal(value)
    except InvalidOperation:
        raise CaptureEpochError("Invalid proposed clock uncertainty.") from None
    if uncertainty > Decimal("1000"):
        raise CaptureEpochError("Unacceptable proposed uncertainty ceiling.")
    gap = doc["maximum_capture_gap_seconds"]
    retention = doc["retention_days"]
    if type(gap) is not int or not 0 <= gap <= 300:
        raise CaptureEpochError("Invalid maximum capture gap.")
    if type(retention) is not int or not 7 <= retention <= 3650:
        raise CaptureEpochError("Invalid retention plan.")
    for key, required in (
        ("required_stop_conditions", STOPS),
        ("required_external_gates", GATES),
        ("required_approval_roles", ROLES),
        ("expected_evidence_packages", PACKAGES),
    ):
        value = doc[key]
        if (not isinstance(value, list) or len(value) != len(required)
                or any(not isinstance(x, str) for x in value)
                or set(value) != set(required)):
            raise CaptureEpochError("Required charter safety contract missing: " + key)
    if doc["approval_signature_claims"] != []:
        raise CaptureEpochError("Unverified signature claims cannot authorize a future epoch.")
    return {
        "status": "LOCAL_FUTURE_EPOCH_PROPOSAL_UNAPPROVED",
        "proposal_digest": sha256(raw).hexdigest(), "proposal_id": doc["proposal_id"],
        "future_epoch_distinct": True, "proposed_epoch_id": proposed,
        "proposed_start_at": start.isoformat(), "proposed_end_at": end.isoformat(),
        "proposed_provider_claims_count": len(providers),
        "required_independent_gates": list(GATES), "required_approval_roles": list(ROLES),
        "required_stop_conditions": list(STOPS),
        "expected_evidence_packages": list(PACKAGES),
        "retention_days": retention, "max_clock_uncertainty_ms_proposed": str(uncertainty),
        "maximum_capture_gap_seconds": gap,
        "approval_signature_count": 0, "operational_approval": False,
        "collector_start_permitted": False, "historical_epoch_rebound": False,
    }


def offline_recovery_matrix():
    """Constant fail-closed design scenarios, not executed recovery acceptance tests."""
    return [
        {"fault": name, "expected_response": "QUARANTINE_AND_HOLD",
         "simulated_only": True, "disk_fault_injected": False,
         "recovery_tested_on_real_collector": False, "collector_restart_authorized": False}
        for name in FAULTS
    ]


def assess_stage7_capture_epoch(*, epoch_id, as_of, policy_file=POLICY_PATH,
                               capture_index=None, capture_root=None,
                               previous_index=None, previous_root=None,
                               charter_file=None):
    if epoch_id != EPOCH:
        raise CaptureEpochError("Historical epoch mismatch: rebinding refused.")
    cutoff = _utc(as_of)
    if cutoff > datetime.now(UTC):
        raise CaptureEpochError("Future assessment cutoff refused.")
    policy = sha256(_read(policy_file, 16384)).hexdigest()
    if policy != POLICY_SHA256:
        raise CaptureEpochError("Immutable historical policy SHA-256 mismatch.")
    capture, chain, index_sha = inspect_capture_custody(
        index_file=capture_index, root=capture_root,
        previous_index=previous_index, previous_root=previous_root, as_of=as_of)
    charter = inspect_future_epoch_charter(charter_file=charter_file, as_of=as_of)
    faults = offline_recovery_matrix()
    blockers = [
        "G03_CAPTURE_ORIGINALS_NOT_INDEPENDENTLY_ATTESTED",
        "G04_FUTURE_EPOCH_NOT_SEPARATELY_OPERATOR_APPROVED",
        "HISTORICAL_BROKER_UTC_NOT_INDEPENDENTLY_ATTESTED",
        "NO_AUTHENTICATED_PROVIDER_TO_CONTEXT_SOURCE_CHAIN",
        "NO_REAL_ATOMIC_WRITE_AND_STORAGE_RECOVERY_ACCEPTANCE",
        "NO_OPERATOR_AUTHORIZATION_FOR_COLLECTOR_OR_COUNCIL",
    ]
    if capture["status"] == "NOT_SUPPLIED":
        blockers.append("NO_HISTORICAL_CAPTURE_INDEX_SUPPLIED")
    if chain["status"] == "LOCAL_CHAIN_INCOMPLETE_OR_CONFLICTING":
        blockers.append("LOCAL_SOURCE_CHAIN_FINDINGS_QUARANTINED")
    if capture["status"] == "LOCAL_CAPTURE_FINDINGS_QUARANTINED":
        blockers.append("LOCAL_CAPTURE_SEQUENCE_OR_TIMESTAMP_FINDINGS_QUARANTINED")
    if charter["status"] == "NOT_SUPPLIED":
        blockers.append("NO_FUTURE_EPOCH_PROPOSAL_SUPPLIED")
    report = {
        "version": VERSION, "scope": "P7_09_TO_P7_12_READ_ONLY_CAPTURE_EPOCH_READINESS",
        "as_of": cutoff.isoformat(), "historical_epoch_id": EPOCH,
        "original_historical_policy": {"path": POLICY_PATH, "sha256": policy,
                                       "raw_bytes_match_original_pin": True,
                                       "broker_utc_independently_verified": False,
                                       "historical_epoch_rebound": False},
        "capture_inventory": capture, "capture_index_sha256": index_sha,
        "source_chain_reconciliation": chain, "future_epoch_charter": charter,
        "offline_recovery_preflight": {
            "status": "DESIGN_ONLY_NOT_TESTED_ON_COLLECTOR",
            "fault_matrix": faults,
            "actual_collector_recovery_verified": False,
            "atomic_write_behavior_verified": False,
            "external_storage_recovery_verified": False,
            "independent_clock_proof_present": False,
            "prospective_session_authorized": False,
        },
        "G03_independently_verified": False, "G04_independently_verified": False,
        "external_gates_independently_verified": 0,
        "source_families_independently_verified": 0,
        "engineering_tests_self_certified": False,
        "files_written": False, "historical_files_modified": False,
        "remote_queried": False, "network_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "training_authorized": False,
        "settlement_invoked": False, "paper_orders_authorized": False,
        "live_orders_authorized": False, "forward_epoch_created": False,
        "operator_approval_granted": False, "read_only": True,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-capture", action="store_true", required=True)
    parser.add_argument("--read-only-proposal", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--capture-index")
    parser.add_argument("--capture-root")
    parser.add_argument("--previous-index")
    parser.add_argument("--previous-root")
    parser.add_argument("--charter-file")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = assess_stage7_capture_epoch(
            epoch_id=args.epoch_id, as_of=args.as_of,
            policy_file=args.policy_file, capture_index=args.capture_index,
            capture_root=args.capture_root, previous_index=args.previous_index,
            previous_root=args.previous_root, charter_file=args.charter_file,
        )
    except (CaptureEpochError, OSError, TypeError, ValueError):
        print("STAGE 7 CAPTURE/EPOCH REVIEW BLOCKED: Invalid or unsafe local evidence.")
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 7 P7-09..12 capture and epoch review")
        print("Historical clock policy: ORIGINAL_BYTES_MATCH_PIN")
        print("Capture inventory: " + report["capture_inventory"]["status"])
        print("Source chain: " + report["source_chain_reconciliation"]["status"])
        print("Future epoch proposal: " + report["future_epoch_charter"]["status"])
        print("Recovery preflight: DESIGN_ONLY_NOT_TESTED_ON_COLLECTOR")
        print("Independent G03/G04: 0/2 | External gates/families: 0/11 | 0/13")
        for item in report["blockers"]:
            print("  BLOCKER " + item)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO REBIND | NO COLLECTOR | NO COUNCIL | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
