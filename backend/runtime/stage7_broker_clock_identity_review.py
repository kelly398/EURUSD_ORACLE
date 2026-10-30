
"""Stage 7 P7-05..08: offline broker-identity and UTC clock *claim* review.

All inputs are existing, locally stored, untrusted candidate evidence. Neither
an issuer string, a claimed third-party receipt, nor a matching SHA-256 proves
independent identity/clock authentication. This program can detect defects and
prepare reviewer requirements, but it CANNOT approve gates G01/G02.

No broker/API/network, collector, agent, training, settlement, disk writes,
or order execution. The Oct 27 historical epoch/policy are immutable.
"""

import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

VERSION = "EURUSD_STAGE7_BROKER_CLOCK_IDENTITY_P7_05_08_V1"
EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
POLICY_PATH = "docs/clock_profiles/hfm_forward_20261027.json"
POLICY_SHA256 = "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088"
INDEX_FIELDS = {"schema_version", "epoch_id", "packet_id", "as_of", "identity_claims", "utc_references", "clock_samples"}
IDENTITY_FIELDS = {
    "record_id", "broker_name", "server_identifier", "source_channel", "account_environment",
    "issuer_claim", "third_party_issuer_claim", "third_party_confirmation_ref",
    "claimed_issued_at", "claimed_received_at", "relative_path", "file_sha256",
}
REFERENCE_FIELDS = {
    "record_id", "source_issuer_claim", "method", "receipt_id_claim", "reference_utc_at",
    "device_utc_at", "first_available_at", "claimed_received_at",
    "stated_uncertainty_ms", "round_trip_ms", "relative_path", "file_sha256",
}
SAMPLE_FIELDS = {
    "record_id", "reference_record_id", "broker_identity_record_id", "broker_timestamp_raw", "broker_timezone_label_claim",
    "broker_utc_offset_claim_minutes", "broker_utc_at_claim", "feed_utc_at_claim",
    "device_utc_at_claim", "capture_arrival_utc_claim", "decision_cutoff_at",
    "first_available_at", "claimed_received_at", "relative_path", "file_sha256",
}
CHANNELS = ("MT5_TERMINAL", "BROKER_EXPORT", "READ_ONLY_FEED", "OTHER_RESEARCH_SOURCE")
ENVIRONMENTS = ("DEMO", "LIVE", "UNKNOWN")
METHODS = ("NTP_SERVER", "PTP_GRANDMASTER", "GNSS_RECEIVER", "EXTERNAL_TIME_SERVICE")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}$")
NAIVE_CLOCK = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d{1,6})?$")
NUM = re.compile(r"^(?:0|[1-9][0-9]{0,6})(?:\.[0-9]{1,6})?$")
SENSITIVE = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|account[_-]?(?:id|number)|\blogin\b|bearer|authorization)")
MAX_INDEX_BYTES = 262144
MAX_ARTIFACT_BYTES = 1048576
MAX_TOTAL_BYTES = 8388608
MAX_IDENTITIES = 12
MAX_REFERENCES = 32
MAX_SAMPLES = 128
MAX_REFERENCE_AGE_S = 300
MAX_DELAY_MS = Decimal("5000")
MAX_DISAGREEMENT_MS = Decimal("250")
MAX_CLOCK_SPREAD_MS = Decimal("2000")
MAX_ALLOWED_UNCERTAINTY_MS = Decimal("1000")
DRIFT_PPM_UPPER_BOUND = Decimal("50")  # Conservative *assumed* drift, not measured
MS = Decimal("1000")
UTC = timezone.utc


class BrokerClockReviewError(ValueError):
    """Invalid evidence input; no gate may be upgraded."""


def _utc(value):
    if not isinstance(value, str):
        raise BrokerClockReviewError("Explicit UTC timestamp required.")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (OverflowError, ValueError):
        raise BrokerClockReviewError("Invalid UTC timestamp, including leap-second notation.") from None
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise BrokerClockReviewError("Explicit zero-offset UTC timestamp required.")
    return dt.astimezone(UTC)


def _millis(a, b):
    delta = a - b
    return (Decimal(delta.days) * Decimal(86400000)
            + Decimal(delta.seconds) * Decimal(1000)
            + Decimal(delta.microseconds) / Decimal(1000))


def _fmt(value):
    return str(value.quantize(Decimal("0.001"), rounding=ROUND_HALF_EVEN))


def _sha(value, name):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise BrokerClockReviewError("Invalid SHA-256: " + name)
    return value


def _id(value, name):
    if not isinstance(value, str) or not IDENT.fullmatch(value) or SENSITIVE.search(value):
        raise BrokerClockReviewError("Invalid or sensitive identifier: " + name)
    return value


def _label(value, name):
    if (not isinstance(value, str) or not 1 <= len(value) <= 90
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or SENSITIVE.search(value) or "@" in value or "://" in value):
        raise BrokerClockReviewError("Invalid or sensitive public claim label: " + name)
    return value


def _decimal(value, name, ceiling):
    if not isinstance(value, str) or not NUM.fullmatch(value):
        raise BrokerClockReviewError("Decimal must be bounded nonnegative string: " + name)
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise BrokerClockReviewError("Invalid decimal: " + name) from None
    if not number.is_finite() or number > ceiling:
        raise BrokerClockReviewError("Decimal exceeds bounded limit: " + name)
    return number


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise BrokerClockReviewError("Duplicate JSON key.")
        result[key] = value
    return result


def _decode(raw):
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique,
                          parse_constant=lambda x: (_ for _ in ()).throw(
                              BrokerClockReviewError("Nonfinite JSON rejected.")))
    except BrokerClockReviewError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise BrokerClockReviewError("Invalid UTF-8 JSON.") from None
    if not isinstance(data, dict):
        raise BrokerClockReviewError("JSON object required.")
    return data


def _no_link_path(path):
    p = Path(path).absolute()
    for segment in (p, *p.parents):
        if segment.is_symlink() or (hasattr(segment, "is_junction") and segment.is_junction()):
            raise BrokerClockReviewError("Symlink or junction refused.")
    return p


def _read(path, limit):
    p = _no_link_path(path)
    if not p.is_file():
        raise BrokerClockReviewError("Existing regular file required.")
    try:
        with p.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        raise BrokerClockReviewError("File read failed.") from None
    if len(data) > limit:
        raise BrokerClockReviewError("Bounded file limit exceeded.")
    return data


def _child(root, relative):
    if (not isinstance(relative, str) or not 1 <= len(relative) <= 180
            or "\\" in relative or ":" in relative or "\x00" in relative
            or PurePosixPath(relative).is_absolute()
            or any(s in ("", ".", "..") for s in relative.split("/"))
            or SENSITIVE.search(relative) or "@" in relative):
        raise BrokerClockReviewError("Unsafe or sensitive evidence path.")
    base = _no_link_path(root)
    if not base.is_dir():
        raise BrokerClockReviewError("Existing evidence root required.")
    child = _no_link_path(base.joinpath(*relative.split("/")))
    if not child.resolve().is_relative_to(base.resolve()):
        raise BrokerClockReviewError("Evidence path escapes root.")
    return child


def _digest(report):
    return sha256(json.dumps(report, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode("utf-8")).hexdigest()


def _strict(doc, fields, name):
    if not isinstance(doc, dict) or set(doc) != fields:
        raise BrokerClockReviewError("Strict schema mismatch: " + name)


def _validate_artifact(entry, root, seen_paths):
    relative = entry["relative_path"]
    if relative in seen_paths:
        raise BrokerClockReviewError("Repeated local artifact path.")
    seen_paths.add(relative)
    expected = _sha(entry["file_sha256"], "file_sha256")
    body = _read(_child(root, relative), MAX_ARTIFACT_BYTES)
    if sha256(body).hexdigest() != expected:
        raise BrokerClockReviewError("Local artifact SHA-256 mismatch.")
    return len(body)


def _check_receipts(entry, document_cutoff, *, first, last, label):
    first_at = _utc(entry[first])
    received_at = _utc(entry[last])
    if first_at > received_at or received_at > document_cutoff:
        raise BrokerClockReviewError("Claimed " + label + " chronology invalid.")
    return first_at, received_at


def _time_wall(value):
    if not isinstance(value, str) or not NAIVE_CLOCK.fullmatch(value):
        raise BrokerClockReviewError("Broker raw timestamp must be timezone-free wall time.")
    try:
        parsed = datetime.fromisoformat(value.replace(" ", "T"))
    except ValueError:
        raise BrokerClockReviewError("Invalid broker wall time or leap second.") from None
    if parsed.tzinfo is not None:
        raise BrokerClockReviewError("Broker raw time must be timezone-free.")
    return parsed.replace(tzinfo=UTC)  # Numerical wall-time arithmetic ONLY, not trusted UTC.


def _read_packet(path, root, cutoff):
    if root is None:
        raise BrokerClockReviewError("Evidence root required with packet index.")
    raw = _read(path, MAX_INDEX_BYTES)
    doc = _decode(raw)
    _strict(doc, INDEX_FIELDS, "index")
    if type(doc["schema_version"]) is not int or doc["schema_version"] != 1 or doc["epoch_id"] != EPOCH:
        raise BrokerClockReviewError("Evidence schema/immutable epoch mismatch.")
    _id(doc["packet_id"], "packet_id")
    as_of = _utc(doc["as_of"])
    if as_of > cutoff:
        raise BrokerClockReviewError("Evidence index claims future review time.")
    collections = (
        ("identity_claims", MAX_IDENTITIES, IDENTITY_FIELDS),
        ("utc_references", MAX_REFERENCES, REFERENCE_FIELDS),
        ("clock_samples", MAX_SAMPLES, SAMPLE_FIELDS),
    )
    ids, paths, receipt_keys = set(), set(), set()
    total = 0
    for key, max_rows, expected in collections:
        rows = doc[key]
        if not isinstance(rows, list) or len(rows) > max_rows:
            raise BrokerClockReviewError("Evidence entry count invalid: " + key)
        for entry in rows:
            _strict(entry, expected, key)
            identifier = _id(entry["record_id"], "record_id")
            if identifier in ids:
                raise BrokerClockReviewError("Duplicate evidence record ID.")
            ids.add(identifier)
            total += _validate_artifact(entry, root, paths)
            if total > MAX_TOTAL_BYTES:
                raise BrokerClockReviewError("Total local evidence bytes exceeded.")
            if key == "identity_claims":
                for field in ("broker_name", "server_identifier", "issuer_claim", "third_party_issuer_claim"):
                    _label(entry[field], field)
                _id(entry["third_party_confirmation_ref"], "third_party_confirmation_ref")
                if entry["source_channel"] not in CHANNELS or entry["account_environment"] not in ENVIRONMENTS:
                    raise BrokerClockReviewError("Invalid channel or account environment label.")
                _check_receipts(entry, as_of, first="claimed_issued_at", last="claimed_received_at", label="identity")
            elif key == "utc_references":
                _label(entry["source_issuer_claim"], "source_issuer_claim")
                _id(entry["receipt_id_claim"], "receipt_id_claim")
                if entry["method"] not in METHODS:
                    raise BrokerClockReviewError("Clock reference method unsupported.")
                ref_at = _utc(entry["reference_utc_at"])
                first, _ = _check_receipts(entry, as_of, first="first_available_at", last="claimed_received_at", label="reference")
                if ref_at > first:
                    raise BrokerClockReviewError("Clock reference claimed available before sample time.")
                _utc(entry["device_utc_at"])
                _decimal(entry["stated_uncertainty_ms"], "stated_uncertainty_ms", Decimal("10000"))
                _decimal(entry["round_trip_ms"], "round_trip_ms", Decimal("60000"))
                receipt = (entry["source_issuer_claim"], entry["receipt_id_claim"])
                if receipt in receipt_keys:
                    raise BrokerClockReviewError("Duplicate claimed reference receipt.")
                receipt_keys.add(receipt)
            else:
                _time_wall(entry["broker_timestamp_raw"])
                _label(entry["broker_timezone_label_claim"], "broker_timezone_label_claim")
                ref_id = entry["reference_record_id"]
                identity_id = entry["broker_identity_record_id"]
                if ref_id is not None:
                    _id(ref_id, "reference_record_id")
                if identity_id is not None:
                    _id(identity_id, "broker_identity_record_id")
                offset = entry["broker_utc_offset_claim_minutes"]
                if offset is not None and (type(offset) is not int or not -840 <= offset <= 840):
                    raise BrokerClockReviewError("Broker offset claim must be integer minutes.")
                for field in ("broker_utc_at_claim", "feed_utc_at_claim"):
                    if entry[field] is not None:
                        _utc(entry[field])
                for field in ("device_utc_at_claim", "capture_arrival_utc_claim", "decision_cutoff_at"):
                    _utc(entry[field])
                _check_receipts(entry, as_of, first="first_available_at", last="claimed_received_at", label="sample")
    return doc, sha256(raw).hexdigest(), total


def _identity_analysis(claims):
    if not claims:
        return {"status": "NOT_SUPPLIED", "claims": 0, "locally_hash_matched_artifacts": 0,
                "conflict_found": False, "third_party_claims_distinct_from_issuer": 0,
                "findings": ["BROKER_IDENTITY_EVIDENCE_NOT_SUPPLIED"]}
    patterns = {
        (r["broker_name"].strip().casefold(), r["server_identifier"].strip().casefold(),
         r["account_environment"], r["source_channel"])
        for r in claims
    }
    issues = []
    if len(patterns) > 1:
        issues.append("BROKER_SERVER_CHANNEL_OR_ENVIRONMENT_CONFLICT")
    distinct = sum(r["issuer_claim"].strip().casefold() != r["third_party_issuer_claim"].strip().casefold()
                   for r in claims)
    if distinct < len(claims):
        issues.append("SELF_ASSERTED_THIRD_PARTY_CONFIRMATION")
    return {
        "status": "CONFLICTING_LOCAL_IDENTITY_CLAIMS_QUARANTINED" if issues else "LOCAL_IDENTITY_CLAIM_UNATTESTED",
        "claims": len(claims), "locally_hash_matched_artifacts": len(claims),
        "conflict_found": bool(issues), "third_party_claims_distinct_from_issuer": distinct,
        "findings": sorted(issues),
    }


def _reference_analysis(references, identity_claims):
    by_id, rows, issues = {}, [], set()
    previous_time = None
    broker_issuers = {r["issuer_claim"].casefold() for r in identity_claims}
    for reference in references:
        if reference["source_issuer_claim"].casefold() in broker_issuers:
            issues.add("REFERENCE_ISSUER_NOT_DISTINCT_FROM_BROKER_CLAIM")
        at = _utc(reference["reference_utc_at"])
        if previous_time is not None and at <= previous_time:
            issues.add("NON_MONOTONIC_REFERENCE_SAMPLE_SEQUENCE")
        previous_time = at
        offset = _millis(_utc(reference["device_utc_at"]), at)
        uncertainty = _decimal(reference["stated_uncertainty_ms"], "stated_uncertainty_ms", Decimal("10000"))
        latency = _decimal(reference["round_trip_ms"], "round_trip_ms", Decimal("60000"))
        bound = uncertainty + latency / 2
        rec = {"id": reference["record_id"], "issuer": reference["source_issuer_claim"].casefold(),
               "time": at, "available": _utc(reference["first_available_at"]),
               "received": _utc(reference["claimed_received_at"]),
               "offset_ms": offset, "base_bound_ms": bound}
        rows.append(rec)
        by_id[rec["id"]] = rec
    for left_index, left in enumerate(rows):
        for right in rows[left_index + 1:]:
            if abs(_millis(left["time"], right["time"])) <= Decimal(MAX_REFERENCE_AGE_S * 1000):
                allowable = left["base_bound_ms"] + right["base_bound_ms"] + MAX_DISAGREEMENT_MS
                if abs(left["offset_ms"] - right["offset_ms"]) > allowable:
                    issues.add("REFERENCE_OFFSET_CONFLICT" if left["issuer"] != right["issuer"]
                               else "INTRA_SOURCE_REFERENCE_OFFSET_JUMP")
    return by_id, {
        "status": "NOT_SUPPLIED" if not rows else (
            "LOCAL_REFERENCE_CONFLICT_QUARANTINED" if issues else "LOCAL_TIME_REFERENCE_CLAIMS_UNATTESTED"),
        "reference_count": len(rows), "distinct_claimed_issuers": len({x["issuer"] for x in rows}),
        "locally_hash_matched_artifacts": len(rows),
        "offset_range_ms_claimed": (
            {"minimum": _fmt(min(x["offset_ms"] for x in rows)),
             "maximum": _fmt(max(x["offset_ms"] for x in rows))}
            if rows else None),
        "findings": sorted(issues if rows else {"UTC_REFERENCE_EVIDENCE_NOT_SUPPLIED"}),
        "independently_authenticated_time_sources": 0,
    }


def _samples_analysis(samples, refs, reference_analysis, identity_ids, identity_analysis):
    previous_capture = None
    results = []
    combined = set()
    for number, entry in enumerate(samples, 1):
        findings = set()
        captured = _utc(entry["capture_arrival_utc_claim"])
        decision = _utc(entry["decision_cutoff_at"])
        first_available = _utc(entry["first_available_at"])
        claimed_received = _utc(entry["claimed_received_at"])
        identity_ref = entry["broker_identity_record_id"]
        if identity_ref is None:
            findings.add("BROKER_IDENTITY_CLAIM_NOT_LINKED")
        elif identity_ref not in identity_ids:
            raise BrokerClockReviewError("Clock sample references unknown broker identity claim.")
        if identity_analysis["conflict_found"]:
            findings.add("BROKER_IDENTITY_CLAIMS_CONFLICT")
        if previous_capture is not None and captured <= previous_capture:
            findings.add("NON_MONOTONIC_CAPTURE_SEQUENCE")
        previous_capture = captured
        if captured > decision:
            findings.add("POST_DECISION_CAPTURE")
        if first_available > decision or claimed_received > decision:
            findings.add("POST_DECISION_EVIDENCE_AVAILABILITY")
        if captured > claimed_received:
            findings.add("CAPTURE_LATER_THAN_CLAIMED_RECEIPT")
        device = _utc(entry["device_utc_at_claim"])
        if device > captured:
            findings.add("DEVICE_TIME_AHEAD_OF_CAPTURE")
        if abs(_millis(device, captured)) > MAX_CLOCK_SPREAD_MS:
            findings.add("DEVICE_CAPTURE_DOMAIN_DISAGREEMENT")
        feed = _utc(entry["feed_utc_at_claim"]) if entry["feed_utc_at_claim"] is not None else None
        if feed is None:
            findings.add("FEED_TIMESTAMP_NOT_SUPPLIED")
        else:
            delay = _millis(captured, feed)
            if delay < 0:
                findings.add("FUTURE_FEED_TIMESTAMP")
            elif delay > MAX_DELAY_MS:
                findings.add("CAPTURE_DELAY_EXCEEDS_POLICY")
        if entry["broker_utc_offset_claim_minutes"] is None:
            findings.add("BROKER_UTC_OFFSET_NOT_ESTABLISHED_BY_LABEL")
        else:
            wall = _time_wall(entry["broker_timestamp_raw"])
            candidate = wall - timedelta(minutes=entry["broker_utc_offset_claim_minutes"])
            broker_claim = entry["broker_utc_at_claim"]
            if broker_claim is None:
                findings.add("BROKER_UTC_CLAIM_NOT_SUPPLIED")
            else:
                utc_claim = _utc(broker_claim)
                if abs(_millis(candidate, utc_claim)) > MAX_DISAGREEMENT_MS:
                    findings.add("BROKER_WALL_OFFSET_CLAIM_CONFLICT")
                if feed is not None and abs(_millis(candidate, feed)) > MAX_CLOCK_SPREAD_MS:
                    findings.add("BROKER_FEED_CLOCK_DISAGREEMENT")
                if utc_claim > captured:
                    findings.add("BROKER_CLOCK_AHEAD_OF_CAPTURE")
        reference_id = entry["reference_record_id"]
        bound = None
        offset = None
        if reference_id is None:
            findings.add("REFERENCE_OBSERVATION_NOT_LINKED")
        elif reference_id not in refs:
            raise BrokerClockReviewError("Clock sample references unknown evidence record.")
        else:
            ref = refs[reference_id]
            for conflict in ("REFERENCE_OFFSET_CONFLICT", "INTRA_SOURCE_REFERENCE_OFFSET_JUMP",
                             "REFERENCE_ISSUER_NOT_DISTINCT_FROM_BROKER_CLAIM"):
                if conflict in reference_analysis["findings"]:
                    findings.add(conflict)
            if "NON_MONOTONIC_REFERENCE_SAMPLE_SEQUENCE" in reference_analysis["findings"]:
                findings.add("NON_MONOTONIC_REFERENCE_SAMPLE_SEQUENCE")
            age_ms = _millis(decision, ref["time"])
            if age_ms < 0:
                findings.add("REFERENCE_FROM_FUTURE")
            elif age_ms > Decimal(MAX_REFERENCE_AGE_S * 1000):
                findings.add("REFERENCE_STALE_FOR_DECISION")
            if ref["available"] > decision:
                findings.add("REFERENCE_UNAVAILABLE_AT_DECISION")
            if ref["received"] > decision:
                findings.add("REFERENCE_RECEIPT_AFTER_DECISION")
            if age_ms >= 0:
                drift = age_ms * DRIFT_PPM_UPPER_BOUND / Decimal(1000000)
                bound = ref["base_bound_ms"] + drift
                offset = ref["offset_ms"]
                if bound > MAX_ALLOWED_UNCERTAINTY_MS:
                    findings.add("CLOCK_UNCERTAINTY_EXCEEDS_POLICY")
        combined.update(findings)
        results.append({
            "sequence": number,
            "status": "LOCAL_CLOCK_CLAIM_QUARANTINED" if findings else "LOCAL_CLOCK_CANDIDATE_UNATTESTED",
            "findings": sorted(findings),
            "reference_device_minus_utc_offset_ms_claimed": _fmt(offset) if offset is not None else None,
            "effective_uncertainty_ms_claimed": _fmt(bound) if bound is not None else None,
            "broker_utc_mapping_independently_verified": False,
            "eligible_for_council": False,
        })
    return {
        "status": "NOT_SUPPLIED" if not samples else (
            "LOCAL_CLOCK_SAMPLES_QUARANTINED" if combined else "LOCAL_CLOCK_SAMPLES_UNATTESTED"),
        "samples": len(samples),
        "local_quarantined_samples": sum(x["status"] == "LOCAL_CLOCK_CLAIM_QUARANTINED" for x in results),
        "findings": sorted(combined if samples else {"CLOCK_DOMAIN_SAMPLES_NOT_SUPPLIED"}),
        "sample_results": results,
        "independently_accepted_council_samples": 0,
    }


def _reviewer_packet(identity, references, samples):
    """A checklist is not a signed review or a way to change the gate state."""
    return {
        "G01": {
            "gate": "INDEPENDENT_BROKER_SERVER_IDENTITY",
            "status": "NOT_INDEPENDENTLY_VERIFIED", "operator_decision": "NOT_PROVIDED",
            "external_reviewer_signature": None, "approved": False,
            "required_independent_proof": [
                "Broker-issued identity including exact server and source-channel scope",
                "Third-party confirmation verifiable directly with its issuer",
                "Unambiguous DEMO/LIVE environment without account secrets in exports",
                "Authenticated issuance and receipt chronology with custody",
                "Qualified human review of any conflicting server identity",
            ],
            "local_diagnostics": {"identity_status": identity["status"], "findings": identity["findings"]},
        },
        "G02": {
            "gate": "INDEPENDENT_BROKER_CLOCK_REFERENCE_AND_OFFSET",
            "status": "NOT_INDEPENDENTLY_VERIFIED", "operator_decision": "NOT_PROVIDED",
            "external_reviewer_signature": None, "approved": False,
            "required_independent_proof": [
                "Traceable independent UTC reference and independently verifiable original receipt",
                "Authenticated reference sampling window, uncertainty, and asymmetry assumptions",
                "Measured broker clock mapping, source timezone and DST handling; no label inference",
                "Independent comparison of reference offsets and monotonic capture arrivals",
                "Maximum age and uncertainty limits approved for the intended future epoch",
                "Qualified human review of conflicts and independent source identity",
            ],
            "local_diagnostics": {"reference_status": references["status"],
                                  "sample_status": samples["status"],
                                  "findings": sorted(set(references["findings"] + samples["findings"]))},
        },
    }


def assess_stage7_broker_clock(*, epoch_id, as_of, policy_file=POLICY_PATH,
                                packet_index=None, evidence_root=None):
    if epoch_id != EPOCH:
        raise BrokerClockReviewError("Historical epoch mismatch; no rebinding.")
    cutoff = _utc(as_of)
    if cutoff > datetime.now(UTC):
        raise BrokerClockReviewError("Future assessment cutoff refused.")
    original = sha256(_read(policy_file, 16384)).hexdigest()
    if original != POLICY_SHA256:
        raise BrokerClockReviewError("Original historical clock policy byte pin mismatch.")
    if packet_index is None:
        if evidence_root is not None:
            raise BrokerClockReviewError("Evidence root cannot be supplied without index.")
        identity_claims, utc_references, clock_samples = [], [], []
        index_sha, bytes_count, packet_status = None, 0, "NOT_SUPPLIED"
    else:
        packet, index_sha, bytes_count = _read_packet(packet_index, evidence_root, cutoff)
        identity_claims = packet["identity_claims"]
        utc_references = packet["utc_references"]
        clock_samples = packet["clock_samples"]
        packet_status = "LOCAL_PACKET_HASH_MATCHED_UNATTESTED"
    identity = _identity_analysis(identity_claims)
    refs, reference_review = _reference_analysis(utc_references, identity_claims)
    samples_review = _samples_analysis(
        clock_samples, refs, reference_review,
        {r["record_id"] for r in identity_claims}, identity,
    )
    reviewer = _reviewer_packet(identity, reference_review, samples_review)
    blockers = [
        "G01_BROKER_SERVER_IDENTITY_NOT_INDEPENDENTLY_VERIFIED",
        "G02_BROKER_UTC_CLOCK_REFERENCE_NOT_INDEPENDENTLY_VERIFIED",
        "NO_EXTERNAL_REVIEWER_OR_OPERATOR_ADMISSION",
        "NO_VERIFIED_UTC_MAPPING_FOR_HISTORICAL_OCT27_CAPTURE",
        "FUTURE_EPOCH_NOT_AUTHORIZED_OR_CREATED",
    ]
    if packet_status == "NOT_SUPPLIED":
        blockers.append("NO_LOCAL_BROKER_CLOCK_EVIDENCE_PACKET_SUPPLIED")
    if (identity["conflict_found"] or "CONFLICT" in reference_review["status"]
            or reference_review["status"] == "LOCAL_REFERENCE_CONFLICT_QUARANTINED"
            or samples_review["local_quarantined_samples"]):
        blockers.append("LOCAL_CLAIMS_REQUIRE_QUARANTINE_AND_REVIEW")
    report = {
        "assessment_version": VERSION, "scope": "P7_05_TO_P7_08_READ_ONLY_BROKER_CLOCK_REVIEW",
        "epoch_id": epoch_id, "as_of": cutoff.isoformat(),
        "original_historical_policy": {"path": POLICY_PATH, "sha256": original,
                                       "historical_epoch_rebound": False,
                                       "broker_utc_independently_attested": False},
        "candidate_packet": {"status": packet_status, "index_sha256": index_sha,
                             "local_artifact_bytes_hashed": bytes_count,
                             "personal_account_identifiers_exported": False,
                             "original_artifact_payloads_exported": False},
        "broker_identity": identity,
        "utc_reference_claims": reference_review,
        "clock_domain_review": samples_review,
        "external_reviewer_requirements": reviewer,
        "G01_independently_verified": False, "G02_independently_verified": False,
        "external_gates_independently_verified": 0,
        "source_families_independently_verified": 0,
        "council_context_admitted": False,
        "proposed_stage7_batch_checkpoints": ["P7-05", "P7-06", "P7-07", "P7-08"],
        "engineering_tests_self_certified": False,
        "read_only": True, "files_written": False, "broker_connected": False,
        "network_accessed": False, "collector_started": False,
        "council_invoked": False, "training_authorized": False,
        "settlement_invoked": False, "paper_orders_authorized": False,
        "live_orders_authorized": False, "forward_epoch_created": False,
        "operator_approval_granted": False,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "blockers": sorted(blockers), "final_action": "HOLD",
    }
    report["report_sha256"] = _digest(report)
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-source", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", default=POLICY_PATH)
    parser.add_argument("--packet-index")
    parser.add_argument("--evidence-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise BrokerClockReviewError("Future review cutoff refused.")
        report = assess_stage7_broker_clock(
            epoch_id=args.epoch_id, as_of=args.as_of,
            policy_file=args.policy_file,
            packet_index=args.packet_index, evidence_root=args.evidence_root,
        )
    except (BrokerClockReviewError, OSError, ValueError, TypeError):
        print("STAGE 7 BROKER/CLOCK REVIEW BLOCKED: Invalid or unsafe local evidence.")
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 7 P7-05..08 broker/UTC evidence review")
        print("Historical clock policy: RAW_SHA256_PIN_MATCHED")
        print("Optional evidence packet: " + report["candidate_packet"]["status"])
        print("Broker identity: " + report["broker_identity"]["status"])
        print("Reference clock: " + report["utc_reference_claims"]["status"])
        print("Clock-domain samples: " + report["clock_domain_review"]["status"])
        print("External G01/G02: 0/2 independently verified")
        print("External gates/families: 0/11 | 0/13")
        for item in report["blockers"]:
            print("  BLOCKER " + item)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO BROKER | NO COLLECTOR | NO COUNCIL | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
