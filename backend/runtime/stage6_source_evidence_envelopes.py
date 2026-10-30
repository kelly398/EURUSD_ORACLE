
"""P6-05..08: offline market/macro evidence envelope and quality review.

Only supplied existing files and self-declared timestamps are evaluated. Payload
bytes are hashed but NOT decoded as provider originals, cryptographically
authenticated or admitted to Council. No network, SQL, broker or writes.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from backend.runtime.stage6_research_governance import (
    HISTORICAL_EPOCH, ResearchGovernanceError, _decode, _hash, _read,
    _utc, _within, inspect_historical_anchor,
)

VERSION = "EURUSD_STAGE6_SOURCE_ENVELOPES_P6_05_08_V1"
KINDS = {
    "tick": "OBSERVED",
    "m5_bar": "OBSERVED",
    "cross_asset_quote": "OBSERVED",
    "cot_release": "OBSERVED",
    "ecb_release": "EFFECTIVE",
    "fed_release": "EFFECTIVE",
    "economic_schedule": "SCHEDULED",
    "economic_actual": "OBSERVED",
}
TRUST = frozenset(("LOCAL_ONLY", "EXTERNAL_CLAIM_UNVERIFIED", "QUARANTINED"))
CLOCKS = frozenset(("UNKNOWN", "LOCAL_UTC_CLAIM", "UNVERIFIED_PROVIDER_WALL"))
FIELDS = frozenset(("schema_version", "epoch_id", "decision_at", "as_of", "records"))
RECORD_FIELDS = frozenset((
    "record_id", "source_kind", "provider_id", "source_record_id", "stream_id",
    "revision", "revision_of", "event_at", "source_published_at",
    "source_available_at", "local_received_at", "native_timestamp",
    "clock_reference", "clock_uncertainty_ms", "sequence", "interval_seconds",
    "relative_path", "file_sha256", "custody_receipt_path", "custody_receipt_sha256",
    "trust_class",
))
HEX = re.compile(r"^[0-9a-f]{64}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
MAX_INDEX = 131072
MAX_FILE = 2097152
MAX_TOTAL = 16777216
MAX_RECORDS = 96
QUALITY_CLOCK_LIMIT_MS = 2000
MARKET_FRESHNESS_SECONDS = {"tick": 120, "m5_bar": 900, "cross_asset_quote": 1800}
UTC = timezone.utc


class SourceEnvelopeError(ValueError):
    """Invalid candidate source metadata, content integrity or causal order."""


def _label(text):
    return isinstance(text, str) and bool(LABEL.fullmatch(text))


def _sha(text):
    return isinstance(text, str) and bool(HEX.fullmatch(text))


def _empty():
    return {
        "status": "NOT_SUPPLIED", "index_sha256": None,
        "record_counts_by_kind": {kind: 0 for kind in KINDS},
        "locally_hash_matched_payloads": 0, "locally_hash_matched_custody_claims": 0,
        "revision_links_checked": 0, "quality_findings": [],
        "clock_claims_above_2000ms": 0, "clock_reference_unknown": 0,
        "sequence_gaps_detected": 0, "m5_time_gaps_detected": 0,
        "stale_market_observations_detected": 0,
        "quality_clear_for_independent_admission": False,
        "provider_origins_independently_verified": 0,
        "publication_times_independently_verified": 0,
        "clock_offsets_independently_verified": 0,
        "custody_signatures_cryptographically_verified": 0,
        "provider_payloads_semantically_decoded": False,
    }


def inspect_existing_source_index(index_file, *, evidence_root, epoch_id, decision_at, as_of):
    """Validate metadata and local bytes; independently verify NOTHING."""
    result = _empty()
    if index_file is None:
        if evidence_root is not None:
            raise SourceEnvelopeError("Evidence root without index refused.")
        return result
    if evidence_root is None:
        raise SourceEnvelopeError("Evidence index requires root.")
    if epoch_id != HISTORICAL_EPOCH:
        raise SourceEnvelopeError("Historical epoch scope mismatch.")
    decision, cutoff = _utc(decision_at), _utc(as_of)
    if decision > cutoff:
        raise SourceEnvelopeError("Decision after review cutoff.")
    raw_index = _read(index_file, MAX_INDEX)
    data = _decode(raw_index)
    if (
        set(data) != FIELDS or type(data.get("schema_version")) is not int
        or data["schema_version"] != 1 or data["epoch_id"] != epoch_id
        or _utc(data["decision_at"]) != decision or _utc(data["as_of"]) != cutoff
    ):
        raise SourceEnvelopeError("Source index schema/epoch/time scope invalid.")
    rows = data["records"]
    if not isinstance(rows, list) or len(rows) > MAX_RECORDS:
        raise SourceEnvelopeError("Evidence record bound exceeded.")

    records, versions = {}, {}
    total_bytes = 0
    counts = result["record_counts_by_kind"]
    market_streams = defaultdict(list)
    findings = []
    custody_count = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != RECORD_FIELDS:
            raise SourceEnvelopeError("Invalid source record field contract.")
        rid, kind = row["record_id"], row["source_kind"]
        if not _label(rid) or rid in records or not isinstance(kind, str) or kind not in KINDS:
            raise SourceEnvelopeError("Duplicate record or unknown source kind.")
        if any(not _label(row[field]) for field in (
            "provider_id", "source_record_id", "stream_id",
        )):
            raise SourceEnvelopeError("Provider/source/stream identity invalid.")
        if not isinstance(row["trust_class"], str) or row["trust_class"] not in TRUST:
            raise SourceEnvelopeError("Self-certifying trust class forbidden.")
        if not isinstance(row["clock_reference"], str) or row["clock_reference"] not in CLOCKS:
            raise SourceEnvelopeError("Self-certified clock reference forbidden.")
        if (
            type(row["revision"]) is not int or row["revision"] < 1
            or row["revision"] > 10000
        ):
            raise SourceEnvelopeError("Source revision number invalid.")
        prev = row["revision_of"]
        if prev is not None and not _label(prev):
            raise SourceEnvelopeError("Source revision pointer invalid.")
        if (
            not isinstance(row["native_timestamp"], str)
            or not 1 <= len(row["native_timestamp"]) <= 128
            or any(ord(ch) < 32 for ch in row["native_timestamp"])
        ):
            raise SourceEnvelopeError("Native provider timestamp string invalid.")
        if (
            type(row["clock_uncertainty_ms"]) is not int
            or not 0 <= row["clock_uncertainty_ms"] <= 86400000
        ):
            raise SourceEnvelopeError("Clock uncertainty claim invalid.")
        sequence, interval = row["sequence"], row["interval_seconds"]
        if kind in {"tick", "m5_bar", "cross_asset_quote"}:
            if type(sequence) is not int or sequence < 0:
                raise SourceEnvelopeError("Market observation sequence missing/invalid.")
        elif sequence is not None:
            raise SourceEnvelopeError("Nonmarket sequence must be null.")
        if kind == "m5_bar":
            if type(interval) is not int or interval != 300:
                raise SourceEnvelopeError("M5 interval must be exactly 300 seconds.")
        elif interval is not None:
            raise SourceEnvelopeError("Unexpected interval on non-M5 observation.")
        if not _sha(row["file_sha256"]):
            raise SourceEnvelopeError("Source payload digest invalid.")
        if (row["custody_receipt_path"] is None) != (row["custody_receipt_sha256"] is None):
            raise SourceEnvelopeError("Custody receipt path and digest must travel together.")
        if row["custody_receipt_sha256"] is not None and not _sha(row["custody_receipt_sha256"]):
            raise SourceEnvelopeError("Custody receipt digest invalid.")

        event = _utc(row["event_at"])
        published = _utc(row["source_published_at"])
        available = _utc(row["source_available_at"])
        received = _utc(row["local_received_at"])
        if not published <= available <= received <= decision:
            raise SourceEnvelopeError("Source not available and received before decision.")
        if KINDS[kind] == "OBSERVED" and event > published:
            raise SourceEnvelopeError("Unreleased observation or future actual refused.")
        # SCHEDULED future event and EFFECTIVE future implementation are allowed
        # only when their *publication and receipt* precede the decision.

        payload = _read(_within(evidence_root, row["relative_path"]), MAX_FILE)
        total_bytes += len(payload)
        if total_bytes > MAX_TOTAL:
            raise SourceEnvelopeError("Combined source bytes exceed bound.")
        if sha256(payload).hexdigest() != row["file_sha256"]:
            raise SourceEnvelopeError("Original candidate payload SHA-256 mismatch.")
        if row["custody_receipt_path"] is not None:
            receipt = _read(_within(evidence_root, row["custody_receipt_path"]), MAX_FILE)
            total_bytes += len(receipt)
            if total_bytes > MAX_TOTAL:
                raise SourceEnvelopeError("Combined custody bytes exceed bound.")
            if sha256(receipt).hexdigest() != row["custody_receipt_sha256"]:
                raise SourceEnvelopeError("Local custody receipt SHA-256 mismatch.")
            custody_count += 1
        vkey = (kind, row["provider_id"], row["source_record_id"], row["revision"])
        if vkey in versions:
            raise SourceEnvelopeError("Duplicate provider source revision.")
        versions[vkey] = rid
        records[rid] = (row, event, published, available, received)
        counts[kind] += 1
        if row["clock_uncertainty_ms"] > QUALITY_CLOCK_LIMIT_MS:
            findings.append(("CLOCK_UNCERTAINTY_HIGH", rid))
        if row["clock_reference"] == "UNKNOWN":
            findings.append(("CLOCK_REFERENCE_UNKNOWN", rid))
        if kind in MARKET_FRESHNESS_SECONDS and (decision - event).total_seconds() > MARKET_FRESHNESS_SECONDS[kind]:
            findings.append(("MARKET_OBSERVATION_STALE", rid))
        if kind in {"tick", "m5_bar", "cross_asset_quote"} and row["revision"] == 1:
            key = (kind, row["provider_id"], row["stream_id"])
            market_streams[key].append((event, sequence, rid))

    linked = 0
    for rid, (row, event, published, available, received) in records.items():
        if row["revision"] == 1:
            if row["revision_of"] is not None:
                raise SourceEnvelopeError("First revision must not have predecessor.")
            continue
        predecessor = versions.get((
            row["source_kind"], row["provider_id"], row["source_record_id"], row["revision"] - 1,
        ))
        if predecessor is None or predecessor != row["revision_of"]:
            raise SourceEnvelopeError("Revision chain lacks exact predecessor.")
        earlier = records[predecessor]
        if not (earlier[2] < published and earlier[3] < available and earlier[4] < received):
            raise SourceEnvelopeError("Source revision publication/receipt backdated.")
        if event != earlier[1] or row["stream_id"] != earlier[0]["stream_id"]:
            raise SourceEnvelopeError("Revision changed event/stream identity.")
        linked += 1

    for key, stream in sorted(market_streams.items()):
        stream.sort(key=lambda x: (x[0], x[1], x[2]))
        for older, newer in zip(stream, stream[1:]):
            at0, seq0, _ = older
            at1, seq1, rid = newer
            if seq1 <= seq0:
                findings.append(("SEQUENCE_NOT_INCREASING", rid))
            elif seq1 > seq0 + 1:
                findings.append(("SEQUENCE_GAP", rid))
            if key[0] == "m5_bar" and (at1 - at0).total_seconds() != 300:
                findings.append(("M5_INTERVAL_GAP", rid))

    findings.sort()
    return {
        "status": (
            "LOCAL_SOURCE_CANDIDATES_UNATTESTED" if records
            else "LOCAL_EMPTY_SOURCE_INDEX_UNATTESTED"
        ),
        "index_sha256": sha256(raw_index).hexdigest(),
        "record_counts_by_kind": counts,
        "locally_hash_matched_payloads": len(records),
        "locally_hash_matched_custody_claims": custody_count,
        "revision_links_checked": linked,
        "quality_findings": [
            {"code": code, "record_id": rid} for code, rid in findings
        ],
        "clock_claims_above_2000ms": sum(x[0] == "CLOCK_UNCERTAINTY_HIGH" for x in findings),
        "clock_reference_unknown": sum(x[0] == "CLOCK_REFERENCE_UNKNOWN" for x in findings),
        "sequence_gaps_detected": sum(x[0] == "SEQUENCE_GAP" for x in findings),
        "m5_time_gaps_detected": sum(x[0] == "M5_INTERVAL_GAP" for x in findings),
        "stale_market_observations_detected": sum(x[0] == "MARKET_OBSERVATION_STALE" for x in findings),
        "quality_clear_for_independent_admission": False,
        "provider_origins_independently_verified": 0,
        "publication_times_independently_verified": 0,
        "clock_offsets_independently_verified": 0,
        "custody_signatures_cryptographically_verified": 0,
        "provider_payloads_semantically_decoded": False,
    }


def assess_stage6_source_envelopes(*, policy_file, epoch_id, decision_at, as_of,
                                   index_file=None, evidence_root=None):
    if epoch_id != HISTORICAL_EPOCH:
        raise SourceEnvelopeError("Original historical epoch must remain bound.")
    decision, cutoff = _utc(decision_at), _utc(as_of)
    if decision > cutoff:
        raise SourceEnvelopeError("Decision after review cutoff.")
    anchor = inspect_historical_anchor(policy_file)
    candidates = inspect_existing_source_index(
        index_file, evidence_root=evidence_root, epoch_id=epoch_id,
        decision_at=decision.isoformat(), as_of=cutoff.isoformat(),
    )
    blockers = [
        "INDEPENDENT_PROVIDER_ORIGIN_AND_FIRST_AVAILABILITY_UNVERIFIED",
        "INDEPENDENT_BROKER_CLOCK_OFFSET_AND_UNCERTAINTY_UNVERIFIED",
        "INDEPENDENT_MARKET_MACRO_CUSTODY_FOR_13_FAMILIES_UNVERIFIED",
        "NO_AUTHORIZED_NEW_FORWARD_EPOCH_OR_SOURCE_ADMISSION",
        "CANDIDATE_METADATA_NOT_CRYPTOGRAPHIC_PROVIDER_ATTESTATION",
    ]
    if candidates["status"] == "NOT_SUPPLIED":
        blockers.append("NO_OPTIONAL_EXISTING_SOURCE_INDEX_SUPPLIED")
    if candidates["quality_findings"]:
        blockers.append("LOCAL_SOURCE_QUALITY_FINDINGS_REQUIRE_REVIEW")
    result = {
        "assessment_version": VERSION,
        "historical_epoch_id": HISTORICAL_EPOCH,
        "decision_at": decision.isoformat(), "as_of": cutoff.isoformat(),
        "historical_policy_anchor": anchor,
        "candidate_evidence": candidates,
        "evidence_contract": {
            "source_kinds": KINDS,
            "market_age_warning_seconds": MARKET_FRESHNESS_SECONDS,
            "clock_uncertainty_warning_ms": QUALITY_CLOCK_LIMIT_MS,
            "custody_receipts_require_independent_verification": True,
            "original_source_bytes_semantically_parsed": False,
            "historical_session_reopened": False,
            "clock_claims_are_self_declared": True,
            "economic_schedules_are_not_actuals": True,
            "revision_claims_are_not_external_vintage_proof": True,
        },
        "independent_source_families_verified": 0,
        "external_provenance_gates_verified": 0,
        "research_ready": False,
        "required_next_evidence": [
            "ORIGIN_AUTHENTICATED_PROVIDER_MARKET_AND_MACRO_RESPONSES",
            "FIRST_PUBLICATION_AVAILABILITY_AND_RECEIPT_RECEIPTS",
            "OFFICIAL_ECB_COT_FED_VINTAGES_AND_REVISION_CUSTODY",
            "INDEPENDENT_UTC_REFERENCE_OFFSET_UNCERTAINTY_AND_NATIVE_TIMES",
            "ORIGINAL_ORDERED_MARKET_CAPTURES_WITH_GAP_EXPLANATIONS",
            "INDEPENDENT_SIGNATURE_OR_CUSTODY_VERIFIER_BEFORE_TRUST_PROMOTION",
            "SEPARATE_APPROVED_NEW_EPOCH_PLAN_BEFORE_ANY_FORWARD_CAPTURE",
        ],
        "blockers": sorted(blockers),
        "read_only": True, "files_written": False,
        "network_accessed": False, "database_accessed": False,
        "broker_connected": False, "collector_started": False,
        "council_invoked": False, "training_authorized": False,
        "paper_execution_authorized": False, "live_execution_authorized": False,
        "historical_epoch_rebound": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _hash(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-source", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--policy-file", default="docs/clock_profiles/hfm_forward_20261027.json")
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--index-file")
    parser.add_argument("--evidence-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise SourceEnvelopeError("Future cutoff refused.")
        report = assess_stage6_source_envelopes(
            policy_file=args.policy_file, epoch_id=args.epoch_id,
            decision_at=args.decision_at, as_of=args.as_of,
            index_file=args.index_file, evidence_root=args.evidence_root,
        )
    except (SourceEnvelopeError, ResearchGovernanceError, ValueError, OSError) as exc:
        print("STAGE 6 SOURCE ENVELOPES BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 6 P6-05..08 source envelopes")
        print("Historical policy: " + report["historical_policy_anchor"]["status"])
        print("Source evidence index: " + report["candidate_evidence"]["status"])
        print("Local candidate payloads: " + str(report["candidate_evidence"]["locally_hash_matched_payloads"]))
        print("Local quality findings: " + str(len(report["candidate_evidence"]["quality_findings"])))
        print("Independently verified source families: 0/13")
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO MT5 | NO COUNCIL | NO SOURCE PROMOTION | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
