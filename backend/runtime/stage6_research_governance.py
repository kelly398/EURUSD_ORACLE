
"""P6-01..04: offline research authority and independent-evidence gate matrix.

Only existing, bounded local files are read. This tool does NOT authenticate
external parties, create an epoch, promote evidence, access SQL/network/MT5,
run the Council, train models, settle outcomes, or place orders.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

VERSION = "EURUSD_STAGE6_RESEARCH_GOVERNANCE_P6_01_04_V1"
HISTORICAL_EPOCH = "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
HISTORICAL_POLICY_SHA256 = "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088"
HISTORICAL_CODE_SHA256 = "03a1811446de521f5481024df2ef8572b51a46746d61ec1135f9f26e6cf60645"
STAGE5_HANDOVER_COMMIT = "fb1c643"  # Operator-reported; not independently fetched.
EXPECTED_POLICY = {
    "schema_version": 1,
    "convention": "hfm_eu_wall_v1",
    "symbol": "EURUSD",
    "company_sha256": "05480005be67a8e868addd003bf98b4ef8204834653335d7782cd6805049658a",
    "server_sha256": "dcde2804e7ffca57ed5e4434558717b801a8549de02c2a389c2f94508bc99e4d",
    "evidence_sha256": "ec43a722243d192d112ea8ee2d45a0687af124041a0a833fe29cdf5a422832a4",
    "implementation_sha256": HISTORICAL_CODE_SHA256,
    "source_policy_url": "https://www.hfm.com/int/en/trading-instruments/trading-hours",
    "valid_from": "2026-10-27T14:27:44.221197Z",
    "valid_until": "2026-11-26T14:27:44.221197Z",
    "historical_admission": False,
}

# This is a *review-role requirements matrix*, not a personnel assignment.
# The proof descriptions are minimum requested evidence, not currently held.
GATES = (
    ("G01", "INDEPENDENT_BROKER_SERVER_IDENTITY", "broker_provider_reviewer",
     "Provider-origin account/server identity confirmation and custody trail"),
    ("G02", "INDEPENDENT_BROKER_CLOCK_REFERENCE_AND_OFFSET", "independent_time_reviewer",
     "Authenticated UTC reference, measurement method, uncertainty and broker clock observations"),
    ("G03", "INDEPENDENT_ORIGINAL_CAPTURE_RECEIPTS_AND_SOURCE_PAYLOADS", "source_custody_reviewer",
     "Genuine capture receipts and complete original ordered tick/bar payloads with chain of custody"),
    ("G04", "CURRENT_FORWARD_SESSION_WITH_APPROVED_NEW_EPOCH_PLAN", "authorized_operator",
     "Separately approved new epoch, first-receipt monitoring and recovery plan; no historical rebind"),
    ("G05", "INDEPENDENT_MARKET_AND_MACRO_SOURCE_PROVENANCE_FOR_13_FAMILIES", "market_macro_reviewer",
     "Versioned origin and first-availability receipts for all 13 Council input families"),
    ("G06", "INDEPENDENT_COUNCIL_DERIVED_INPUT_AND_MODEL_PROVENANCE", "model_governance_reviewer",
     "Immutable feature, model, data-version and decision-time lineage proof"),
    ("G07", "OPERATOR_APPROVED_RISK_AND_RECOVERY_CONTROLS", "risk_authority",
     "Authenticated risk limits, veto policy, kill switch and independent recovery approval"),
    ("G08", "EXTERNALLY_TIMESTAMPED_PREDECISION_PREDICTIONS", "registration_custodian",
     "Append-only third-party prediction registration receipt preceding each decision"),
    ("G09", "INDEPENDENT_POST_HORIZON_OUTCOME_RECEIPTS", "outcome_custodian",
     "Origin-authenticated quotes received after declared prediction horizon"),
    ("G10", "BROKER_CONFIRMED_EXECUTION_COSTS_IF_TRADES_EXIST", "execution_cost_reviewer",
     "Broker-original fills, spread, commission, slippage and financing; attest no-trade scope otherwise"),
    ("G11", "INDEPENDENT_PRE_EVALUATION_HOLDOUT_FREEZE", "evaluation_governance_reviewer",
     "Externally timestamped partition/evaluation-plan freeze before evaluation"),
)
TRUST_CLASSES = ("LOCAL_ONLY", "EXTERNAL_CLAIM_UNVERIFIED", "QUARANTINED")
UTC = timezone.utc
HEX64 = re.compile(r"^[0-9a-f]{64}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
INDEX_FIELDS = {"schema_version", "epoch_id", "as_of", "entries"}
ENTRY_FIELDS = {
    "gate_id", "record_id", "relative_path", "file_sha256",
    "claimed_issuer", "claimed_received_at", "trust_class",
}
MAX_POLICY = 16384
MAX_INDEX = 131072
MAX_FILE = 2097152
MAX_TOTAL = 8388608
MAX_ENTRIES = 64


class ResearchGovernanceError(ValueError):
    """Nonconforming policy, local claim or evidence contract."""


def _utc(text):
    if not isinstance(text, str):
        raise ResearchGovernanceError("Explicit UTC timestamp required.")
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        raise ResearchGovernanceError("Invalid UTC timestamp.") from None
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ResearchGovernanceError("Explicit zero-offset UTC required.")
    return result.astimezone(UTC)


def _hash(data):
    return sha256(json.dumps(
        data, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ResearchGovernanceError("Duplicate JSON key refused.")
        obj[key] = value
    return obj


def _decode(raw):
    try:
        obj = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(
                ResearchGovernanceError("Nonfinite JSON refused.")
            ),
        )
    except ResearchGovernanceError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise ResearchGovernanceError("Invalid evidence/policy JSON.") from None
    if not isinstance(obj, dict):
        raise ResearchGovernanceError("JSON object required.")
    return obj


def _read(path, maximum):
    p = Path(path)
    if p.is_symlink() or not p.is_file():
        raise ResearchGovernanceError("Existing regular file required.")
    try:
        with p.open("rb") as handle:
            data = handle.read(maximum + 1)
    except OSError:
        raise ResearchGovernanceError("Evidence file cannot be read.") from None
    if len(data) > maximum:
        raise ResearchGovernanceError("Evidence file size limit exceeded.")
    return data


def _within(root, relative):
    if (not isinstance(relative, str) or not 1 <= len(relative) <= 240
            or "\\" in relative or ":" in relative or "\x00" in relative
            or PurePosixPath(relative).is_absolute()
            or any(part in ("", ".", "..") for part in relative.split("/"))):
        raise ResearchGovernanceError("Unsafe relative evidence path.")
    target = Path(root)
    if target.is_symlink() or not target.is_dir():
        raise ResearchGovernanceError("Existing non-symlink evidence root required.")
    for part in relative.split("/"):
        target = target / part
        if target.is_symlink():
            raise ResearchGovernanceError("Symlinked evidence path refused.")
    return target


def inspect_historical_anchor(policy_file):
    """Verify byte-identical October 27 policy; epoch ID is a separate anchor."""
    raw = _read(policy_file, MAX_POLICY)
    digest = sha256(raw).hexdigest()
    if digest != HISTORICAL_POLICY_SHA256:
        raise ResearchGovernanceError("Historical policy SHA-256 mismatch; no rebind allowed.")
    data = _decode(raw)
    if data != EXPECTED_POLICY or set(data) != set(EXPECTED_POLICY):
        raise ResearchGovernanceError("Historical policy content differs from pinned anchor.")
    return {
        "status": "LOCAL_BYTES_MATCH_PINNED_HISTORICAL_POLICY",
        "policy_sha256": digest,
        "epoch_id_separately_bound": HISTORICAL_EPOCH,
        "epoch_id_derived_from_policy_json": False,
        "pinned_clock_implementation_sha256": HISTORICAL_CODE_SHA256,
        "historical_admission": False,
        "independent_broker_clock_attestation": False,
        "historical_policy_modified": False,
    }


def inspect_local_claims(index_file, *, evidence_root, as_of):
    """Compare local bytes to self-declared hashes; never establish trust."""
    counts = {gate_id: 0 for gate_id, *_ in GATES}
    classes = {name: 0 for name in TRUST_CLASSES}
    empty = {
        "status": "NOT_SUPPLIED", "index_sha256": None,
        "locally_hash_matched_documents": 0,
        "gate_claim_counts": counts, "trust_class_counts": classes,
        "independently_verified_documents": 0,
        "external_origins_authenticated": False,
        "receipt_times_independently_authenticated": False,
    }
    if index_file is None:
        if evidence_root is not None:
            raise ResearchGovernanceError("Evidence root supplied without index.")
        return empty
    if evidence_root is None:
        raise ResearchGovernanceError("Evidence index requires evidence root.")
    cutoff = _utc(as_of)
    raw = _read(index_file, MAX_INDEX)
    data = _decode(raw)
    if (set(data) != INDEX_FIELDS or type(data["schema_version"]) is not int
            or data["schema_version"] != 1
            or data["epoch_id"] != HISTORICAL_EPOCH
            or _utc(data["as_of"]) != cutoff):
        raise ResearchGovernanceError("Evidence index schema/epoch/cutoff mismatch.")
    entries = data["entries"]
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ResearchGovernanceError("Evidence index entry limit exceeded.")
    seen = set()
    total = 0
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
            raise ResearchGovernanceError("Evidence claim entry contract invalid.")
        gate_id, record_id = entry["gate_id"], entry["record_id"]
        if (not isinstance(gate_id, str) or gate_id not in counts or not isinstance(record_id, str)
                or not LABEL.fullmatch(record_id) or record_id in seen):
            raise ResearchGovernanceError("Unknown gate or duplicate record identifier.")
        if (not isinstance(entry["claimed_issuer"], str)
                or not LABEL.fullmatch(entry["claimed_issuer"])):
            raise ResearchGovernanceError("Claimed issuer identifier invalid.")
        if entry["trust_class"] not in TRUST_CLASSES:
            raise ResearchGovernanceError("Self-assigned independent trust is forbidden.")
        digest = entry["file_sha256"]
        if not isinstance(digest, str) or not HEX64.fullmatch(digest):
            raise ResearchGovernanceError("Evidence claim SHA-256 invalid.")
        if _utc(entry["claimed_received_at"]) > cutoff:
            raise ResearchGovernanceError("Evidence receipt claimed after review cutoff.")
        contents = _read(_within(evidence_root, entry["relative_path"]), MAX_FILE)
        total += len(contents)
        if total > MAX_TOTAL:
            raise ResearchGovernanceError("Total evidence byte limit exceeded.")
        if sha256(contents).hexdigest() != digest:
            raise ResearchGovernanceError("Evidence claim file SHA-256 mismatch.")
        seen.add(record_id)
        counts[gate_id] += 1
        classes[entry["trust_class"]] += 1
    return {
        "status": "LOCAL_CLAIMS_HASH_MATCHED_UNATTESTED",
        "index_sha256": sha256(raw).hexdigest(),
        "locally_hash_matched_documents": len(entries),
        "gate_claim_counts": counts, "trust_class_counts": classes,
        "independently_verified_documents": 0,
        "external_origins_authenticated": False,
        "receipt_times_independently_authenticated": False,
    }


def assess_stage6_governance(*, policy_file, epoch_id, as_of,
                             index_file=None, evidence_root=None):
    """Synthesize P6-01..04 research-only authority and open gate matrix."""
    if epoch_id != HISTORICAL_EPOCH:
        raise ResearchGovernanceError("Historical epoch mismatch; changing epoch refused.")
    cutoff = _utc(as_of)
    anchor = inspect_historical_anchor(policy_file)
    claims = inspect_local_claims(
        index_file, evidence_root=evidence_root, as_of=cutoff.isoformat(),
    )
    matrix = [
        {
            "gate_id": gate_id, "gate": name,
            "required_review_role": owner,
            "minimum_independent_proof": proof,
            "locally_hash_matched_candidate_count": claims["gate_claim_counts"][gate_id],
            "status": "NOT_INDEPENDENTLY_VERIFIED", "approved": False,
        }
        for gate_id, name, owner, proof in GATES
    ]
    blockers = [
        "EXTERNAL_GATE_NOT_INDEPENDENTLY_VERIFIED:" + gate["gate"]
        for gate in matrix
    ]
    if claims["status"] == "NOT_SUPPLIED":
        blockers.append("NO_OPTIONAL_LOCAL_EVIDENCE_INDEX_SUPPLIED")
    result = {
        "assessment_version": VERSION,
        "policy_contract": {
            "mode": "RESEARCH_OFFLINE_ONLY",
            "operational_action": "HOLD",
            "blueprint_version": "1.2_RESEARCH_FIRST",
            "stage5_handover_commit_operator_reported": STAGE5_HANDOVER_COMMIT,
            "stage5_commit_independently_verified_in_this_assessment": False,
            "historical_epoch_id": HISTORICAL_EPOCH,
            "historical_epoch_rebinding_allowed": False,
            "collector_restart_allowed": False,
            "council_dispatch_allowed": False,
            "model_training_allowed": False,
            "paper_orders_allowed": False,
            "live_orders_allowed": False,
            "policy_self_authorizes_operational_mode_changes": False,
        },
        "epoch_id": epoch_id,
        "as_of": cutoff.isoformat(),
        "historical_policy_anchor": anchor,
        "evidence_taxonomy": [
            {"trust_class": "NOT_SUPPLIED", "interpretation": "No candidate supplied; no inference about other repositories"},
            {"trust_class": "LOCAL_ONLY", "interpretation": "Byte hash matched locally; neither issuer nor time independently authenticated"},
            {"trust_class": "EXTERNAL_CLAIM_UNVERIFIED", "interpretation": "Issuer/time claimed; no independent verification of signature or custody"},
            {"trust_class": "QUARANTINED", "interpretation": "Candidate not admitted; may be incomplete or conflict with trusted inputs"},
        ],
        "candidate_evidence": claims,
        "acceptance_matrix": matrix,
        "external_gates_verified": 0,
        "external_gates_total": len(GATES),
        "independent_source_families_verified": 0,
        "research_operational_readiness": "BLOCKED_EXTERNAL_EVIDENCE",
        "engineering_scope": "P6_01_TO_P6_04_OFFLINE_POLICY_AND_GATE_MATRIX",
        "required_next_steps": [
            "KEEP_ORIGINAL_OCTOBER_27_EPOCH_AND_CLOCK_POLICY_IMMUTABLE",
            "OBTAIN_INDEPENDENTLY_AUTHENTICATED_PROVIDER_UTC_AND_CAPTURE_CUSTODY",
            "OBTAIN_VERSIONED_POINT_IN_TIME_PROVENANCE_FOR_13_COUNCIL_FAMILIES",
            "REGISTER_NEW_PREDICTIONS_EXTERNALLY_BEFORE_ANY_DECISIONS",
            "FREEZE_UNSEEN_HOLDOUT_WITH_INDEPENDENT_PRE_EVALUATION_TIMESTAMP",
            "SEPARATELY_REVIEW_OPERATOR_RISK_AND_NEW_EPOCH_APPROVAL",
            "DO_NOT_AUTO_START_COLLECTOR_COUNCIL_OR_EXECUTION",
        ],
        "blockers": sorted(blockers),
        "read_only": True,
        "files_written": False, "git_mutated": False, "database_accessed": False,
        "network_accessed": False, "broker_connected": False,
        "collector_started": False, "historical_epoch_rebound": False,
        "council_invoked": False, "training_authorized": False,
        "paper_execution_authorized": False, "live_execution_authorized": False,
        "research_admission_authorized": False,
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
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--index-file")
    parser.add_argument("--evidence-root")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise ResearchGovernanceError("Future review cutoff refused.")
        report = assess_stage6_governance(
            policy_file=args.policy_file, epoch_id=args.epoch_id, as_of=args.as_of,
            index_file=args.index_file, evidence_root=args.evidence_root,
        )
    except (ResearchGovernanceError, ValueError, OSError) as exc:
        print("STAGE 6 RESEARCH GOVERNANCE BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Stage 6 P6-01..04 research governance")
        print("Historical policy bytes: " + report["historical_policy_anchor"]["status"])
        print("Authority mode: " + report["policy_contract"]["mode"])
        print("Optional evidence index: " + report["candidate_evidence"]["status"])
        print("Locally hash-matched claims: " + str(report["candidate_evidence"]["locally_hash_matched_documents"]))
        print("Independent external gates: 0/11")
        print("Research operational readiness: " + report["research_operational_readiness"])
        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO REBIND | NO MT5 | NO COUNCIL | NO ORDERS | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
