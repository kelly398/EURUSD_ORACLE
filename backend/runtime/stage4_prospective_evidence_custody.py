
"""S4-17..20: read-only custody check for existing prospective evidence.

Validates optional file bytes and claimed chronology, never external origin.
No registration, outcome, fill, profit, or independent timestamp is created.
All files are treated as untrusted, self-declared local records. FINAL=HOLD.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re

VERSION = "EURUSD_STAGE4_PROSPECTIVE_EVIDENCE_CUSTODY_S4_17_20_V1"
P3_VERSION = "EURUSD_P3_PROSPECTIVE_EVALUATION_V1"

SHA = re.compile(r"^[0-9a-f]{64}$")
IDENT = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")

MAX_MANIFEST = 131072
MAX_REPORT = 131072
MAX_ARTIFACT = 1048576
UTC = timezone.utc

GATES = (
    (
        "prediction_registration",
        "EXTERNAL_APPEND_ONLY_PREDICTION_RECORD_AND_TRUSTED_PREDECISION_TIMESTAMP",
    ),
    (
        "post_horizon_outcome",
        "INDEPENDENT_BROKER_OR_REFERENCE_OUTCOME_AFTER_FROZEN_HORIZON",
    ),
    (
        "broker_cost_fills",
        "BROKER_CONFIRMED_BID_ASK_SPREAD_COMMISSION_SLIPPAGE_AND_FILL_LEDGER",
    ),
    (
        "holdout_freeze",
        "INDEPENDENT_PRE_EVALUATION_SPLIT_FREEZE_AND_DATA_EXCLUSION_PROOF",
    ),
)

GATE_NAMES = {name for name, _ in GATES}

ROW_KEYS = {
    "gate",
    "artifact_id",
    "relative_path",
    "file_sha256",
    "claimed_issuer",
    "claimed_event_at",
    "subject_sha256",
}

MANIFEST_KEYS = {
    "schema_version",
    "epoch_id",
    "decision_at",
    "as_of",
    "horizon_seconds",
    "heldout_start",
    "heldout_end",
    "artifacts",
}


class EvidenceCustodyError(ValueError):
    """Reject unsafe, malformed, or inconsistent supplied local records."""


def _digest(value):
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _utc(value):
    if not isinstance(value, str):
        raise EvidenceCustodyError(
            "Explicit UTC timestamp text required."
        )

    try:
        timestamp = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (ValueError, OverflowError):
        raise EvidenceCustodyError(
            "Invalid UTC timestamp."
        ) from None

    if (
        timestamp.tzinfo is None
        or timestamp.utcoffset() != timedelta(0)
    ):
        raise EvidenceCustodyError(
            "Timestamp must include UTC offset zero."
        )

    return timestamp.astimezone(UTC)


def _read(path, limit):
    file = Path(path)

    if file.is_symlink() or not file.is_file():
        raise EvidenceCustodyError(
            "Existing regular evidence file required."
        )

    try:
        with file.open("rb") as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise EvidenceCustodyError(
            "Cannot read existing evidence file."
        ) from None

    if len(raw) > limit:
        raise EvidenceCustodyError(
            "Existing evidence file exceeds size bound."
        )

    return raw


def _unique(pairs):
    obj = {}

    for key, value in pairs:
        if key in obj:
            raise EvidenceCustodyError(
                "Duplicate JSON key."
            )
        obj[key] = value

    return obj


def _json_file(path, limit):
    raw = _read(path, limit)

    try:
        obj = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=lambda _: (
                _ for _ in ()
            ).throw(
                EvidenceCustodyError(
                    "Nonfinite JSON value."
                )
            ),
        )
    except EvidenceCustodyError:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise EvidenceCustodyError(
            "Invalid JSON evidence document."
        ) from exc

    return obj, sha256(raw).hexdigest()


def _artifact_path(root, relative):
    """Reject absolute paths, traversal and symlink components."""
    if (
        not isinstance(relative, str)
        or not 1 <= len(relative) <= 240
    ):
        raise EvidenceCustodyError(
            "Invalid evidence relative path."
        )

    if (
        "\\" in relative
        or ":" in relative
        or "\x00" in relative
    ):
        raise EvidenceCustodyError(
            "Unsafe evidence path syntax."
        )

    parts = PurePosixPath(relative)

    if (
        parts.is_absolute()
        or any(
            part in (".", "..", "")
            for part in relative.split("/")
        )
    ):
        raise EvidenceCustodyError(
            "Evidence path traversal refused."
        )

    root = Path(root)

    if not root.is_dir() or root.is_symlink():
        raise EvidenceCustodyError(
            "Existing non-symlink evidence root required."
        )

    current = root

    for component in parts.parts:
        current = current / component

        if current.is_symlink():
            raise EvidenceCustodyError(
                "Symlinked evidence path refused."
            )

    if not current.is_file():
        raise EvidenceCustodyError(
            "Evidence artifact is missing."
        )

    return current


def inspect_phase3_report(
    path,
    *,
    epoch_id,
    decision_at,
    as_of,
):
    """Inspect an existing Phase 3 report; never grant attestation."""
    empty = {
        "status": "NOT_SUPPLIED",
        "file_sha256": None,
        "report_sha256": None,
        "market_lineage_sha256": None,
        "registration_count_in_scope": None,
        "post_horizon_outcomes_in_scope": None,
        "cost_records_in_scope": None,
        "independent_attestation": False,
    }

    if path is None:
        return empty

    obj, file_sha = _json_file(
        path, MAX_REPORT
    )

    if (
        not isinstance(obj, dict)
        or obj.get("assessment_version") != P3_VERSION
    ):
        raise EvidenceCustodyError(
            "Unexpected Phase 3 evaluation version."
        )

    declared = obj.get("report_sha256")

    if (
        not isinstance(declared, str)
        or not SHA.fullmatch(declared)
        or declared != _digest({
            k: v
            for k, v in obj.items()
            if k != "report_sha256"
        })
    ):
        raise EvidenceCustodyError(
            "Phase 3 report hash mismatch."
        )

    if (
        obj.get("epoch_id") != epoch_id
        or _utc(obj.get("decision_at")) != decision_at
        or _utc(obj.get("as_of")) != as_of
        or obj.get("final_action") != "HOLD"
        or obj.get("read_only") is not True
    ):
        raise EvidenceCustodyError(
            "Phase 3 report scope/HOLD mismatch."
        )

    for flag in (
        "independent_prediction_registration_attested",
        "independent_outcome_source_attested",
        "broker_fill_and_costs_attested",
        "heldout_split_independently_frozen",
        "external_timestamp_anchor_verified",
        "performance_measured",
        "transaction_costs_measured",
        "training_authorized",
        "execution_authorized",
        "council_invoked",
    ):
        if obj.get(flag) is not False:
            raise EvidenceCustodyError(
                "Phase 3 report claimed unauthorized evidence/action."
            )

    if obj.get("out_of_sample_trades_measured") is not None:
        raise EvidenceCustodyError(
            "Phase 3 measured-trades claim refused."
        )

    lineage = obj.get("market_lineage_sha256")

    if (
        not isinstance(lineage, str)
        or not SHA.fullmatch(lineage)
    ):
        raise EvidenceCustodyError(
            "Phase 3 market lineage link missing."
        )

    counters = (
        "registration_count_in_scope",
        "post_horizon_outcomes_in_scope",
        "cost_records_in_scope",
    )

    if any(
        type(obj.get(key)) is not int
        or obj[key] < 0
        for key in counters
    ):
        raise EvidenceCustodyError(
            "Phase 3 evidence counters invalid."
        )

    return {
        "status": "LOCAL_PHASE3_REPORT_LINKED_UNATTESTED",
        "file_sha256": file_sha,
        "report_sha256": declared,
        "market_lineage_sha256": lineage,
        **{
            key: obj[key]
            for key in counters
        },
        "independent_attestation": False,
    }


def inspect_evidence_manifest(
    path,
    *,
    root=None,
    epoch_id,
    decision_at,
    as_of,
):
    """Read-only artifact hashing and claimed causal ordering."""
    empty = {
        "status": "NOT_SUPPLIED",
        "manifest_file_sha256": None,
        "files_locally_hash_matched": 0,
        "artifact_count": 0,
        "gate_names_present": [],
        "gate_names_missing": sorted(GATE_NAMES),
        "subject_links_locally_consistent": None,
        "claimed_horizon_end": None,
        "claimed_holdout_start": None,
        "claimed_holdout_end": None,
        "independently_attested_gates": 0,
    }

    if path is None:
        if root is not None:
            raise EvidenceCustodyError(
                "Evidence root without manifest refused."
            )
        return empty

    if root is None:
        raise EvidenceCustodyError(
            "Evidence root required for manifest artifacts."
        )

    manifest, file_sha = _json_file(
        path, MAX_MANIFEST
    )

    if (
        not isinstance(manifest, dict)
        or set(manifest) != MANIFEST_KEYS
        or type(manifest["schema_version"]) is not int
        or manifest["schema_version"] != 1
        or manifest["epoch_id"] != epoch_id
        or _utc(manifest["decision_at"]) != decision_at
        or _utc(manifest["as_of"]) != as_of
    ):
        raise EvidenceCustodyError(
            "Evidence manifest version/scope mismatch."
        )

    horizon = manifest["horizon_seconds"]

    if (
        type(horizon) is not int
        or not 60 <= horizon <= 86400
    ):
        raise EvidenceCustodyError(
            "Invalid prospective horizon."
        )

    horizon_end = decision_at + timedelta(
        seconds=horizon
    )

    start = _utc(manifest["heldout_start"])
    end = _utc(manifest["heldout_end"])

    if (
        not start <= decision_at < end <= as_of
        or horizon_end > as_of
    ):
        raise EvidenceCustodyError(
            "Held-out interval/horizon is outside inspection."
        )

    items = manifest["artifacts"]

    if (
        not isinstance(items, list)
        or len(items) > 4
    ):
        raise EvidenceCustodyError(
            "Artifact inventory must contain at most four gates."
        )

    seen_ids = set()
    subjects = {}
    seen_gates = set()

    for item in items:
        if (
            not isinstance(item, dict)
            or set(item) != ROW_KEYS
        ):
            raise EvidenceCustodyError(
                "Artifact fields differ from contract."
            )

        gate = item["gate"]

        if (
            not isinstance(gate, str)
            or gate not in GATE_NAMES
            or gate in seen_gates
        ):
            raise EvidenceCustodyError(
                "Unrecognized/duplicate evidence gate."
            )

        ident = item["artifact_id"]
        issuer = item["claimed_issuer"]

        if (
            not isinstance(ident, str)
            or not IDENT.fullmatch(ident)
            or ident in seen_ids
            or not isinstance(issuer, str)
            or not IDENT.fullmatch(issuer)
        ):
            raise EvidenceCustodyError(
                "Invalid issuer or artifact identity."
            )

        if (
            not isinstance(item["file_sha256"], str)
            or not SHA.fullmatch(item["file_sha256"])
        ):
            raise EvidenceCustodyError(
                "Artifact SHA-256 invalid."
            )

        if (
            not isinstance(item["subject_sha256"], str)
            or not SHA.fullmatch(item["subject_sha256"])
        ):
            raise EvidenceCustodyError(
                "Artifact subject digest invalid."
            )

        claimed = _utc(
            item["claimed_event_at"]
        )

        if claimed > as_of:
            raise EvidenceCustodyError(
                "Claimed event after review cutoff."
            )

        if (
            gate == "prediction_registration"
            and claimed > decision_at
        ):
            raise EvidenceCustodyError(
                "Claimed registration after decision."
            )

        if (
            gate == "holdout_freeze"
            and claimed >= start
        ):
            raise EvidenceCustodyError(
                "Claimed split freeze not before evaluation start."
            )

        if (
            gate in (
                "post_horizon_outcome",
                "broker_cost_fills",
            )
            and claimed < horizon_end
        ):
            raise EvidenceCustodyError(
                "Claimed outcome/cost event before horizon."
            )

        artifact = _artifact_path(
            root,
            item["relative_path"],
        )

        if (
            sha256(
                _read(artifact, MAX_ARTIFACT)
            ).hexdigest()
            != item["file_sha256"]
        ):
            raise EvidenceCustodyError(
                "Local artifact file hash mismatch."
            )

        seen_gates.add(gate)
        seen_ids.add(ident)
        subjects[gate] = item["subject_sha256"]

    linked_subjects = [
        subjects[name]
        for name in (
            "prediction_registration",
            "post_horizon_outcome",
            "broker_cost_fills",
        )
        if name in subjects
    ]

    linked = None

    if linked_subjects:
        linked = len(set(linked_subjects)) == 1

        if not linked:
            raise EvidenceCustodyError(
                "Cross-artifact prediction digest mismatch."
            )

    return {
        "status": "LOCAL_ARTIFACT_BYTES_LINKED_UNATTESTED",
        "manifest_file_sha256": file_sha,
        "files_locally_hash_matched": len(items),
        "artifact_count": len(items),
        "gate_names_present": sorted(seen_gates),
        "gate_names_missing": sorted(
            GATE_NAMES - seen_gates
        ),
        "subject_links_locally_consistent": linked,
        "claimed_horizon_end": horizon_end.isoformat(),
        "claimed_holdout_start": start.isoformat(),
        "claimed_holdout_end": end.isoformat(),
        "independently_attested_gates": 0,
    }


def assess_stage4_prospective_custody(
    *,
    epoch_id,
    decision_at,
    as_of,
    evidence_manifest=None,
    evidence_root=None,
    phase3_report=None,
):
    """Evaluate optional local evidence without external attestation."""
    decision = _utc(decision_at)
    cutoff = _utc(as_of)

    if (
        not isinstance(epoch_id, str)
        or not SHA.fullmatch(epoch_id)
        or decision > cutoff
    ):
        raise EvidenceCustodyError(
            "Epoch or decision/cutoff invalid."
        )

    p3 = inspect_phase3_report(
        phase3_report,
        epoch_id=epoch_id,
        decision_at=decision,
        as_of=cutoff,
    )

    docs = inspect_evidence_manifest(
        evidence_manifest,
        root=evidence_root,
        epoch_id=epoch_id,
        decision_at=decision,
        as_of=cutoff,
    )

    blockers = []

    if p3["status"] == "NOT_SUPPLIED":
        blockers.append(
            "NO_PHASE3_EVALUATION_REPORT_SUPPLIED"
        )

    if docs["status"] == "NOT_SUPPLIED":
        blockers.append(
            "NO_EXISTING_EVIDENCE_MANIFEST_SUPPLIED"
        )
    else:
        blockers.extend(
            "LOCAL_DOC_MISSING:" + gate
            for gate in docs["gate_names_missing"]
        )

    if (
        docs["gate_names_present"]
        and p3["status"] == "NOT_SUPPLIED"
    ):
        blockers.append(
            "NO_PHASE3_SCOPE_CROSSCHECK"
        )

    if p3["status"] != "NOT_SUPPLIED":
        for gate, count_key in (
            (
                "prediction_registration",
                "registration_count_in_scope",
            ),
            (
                "post_horizon_outcome",
                "post_horizon_outcomes_in_scope",
            ),
            (
                "broker_cost_fills",
                "cost_records_in_scope",
            ),
        ):
            if (
                gate in docs["gate_names_present"]
                and p3[count_key] == 0
            ):
                blockers.append(
                    "ARTIFACT_NOT_RECONCILED_TO_PHASE3_SCOPE:"
                    + gate
                )

    blockers.extend([
        "NO_INDEPENDENT_EXTERNAL_TIMESTAMP_ANCHOR_VERIFIED",
        "NO_INDEPENDENT_OUTCOME_SOURCE_VERIFIED",
        "NO_BROKER_CONFIRMED_FILL_COSTS_VERIFIED",
        "NO_INDEPENDENTLY_FROZEN_HELDOUT_SPLIT_VERIFIED",
    ])

    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id,
        "decision_at": decision.isoformat(),
        "as_of": cutoff.isoformat(),
        "phase3_evaluation": p3,
        "existing_artifact_custody": docs,
        "external_gates": [
            {
                "gate": gate,
                "evidence_required": requirement,
                "independently_verified": False,
            }
            for gate, requirement in GATES
        ],
        "external_gates_independently_verified": 0,
        "prediction_records_independently_registered": False,
        "post_horizon_outcomes_independently_attested": False,
        "broker_executions_or_costs_independently_attested": False,
        "holdout_independently_frozen": False,
        "performance_measured": False,
        "transaction_costs_measured": False,
        "out_of_sample_trades_measured": None,
        "interpretation": (
            "LOCAL_HASH_AND_CLAIM_ORDER_CHECKS_ONLY_"
            "NOT_PROVIDER_ATTESTATION_OR_PROSPECTIVE_CERTIFICATION"
        ),
        "blockers": sorted(
            set(blockers)
        ),
        "operator_next_actions": [
            "LOCATE_EXISTING_EXTERNALLY_TIMESTAMPED_PREDECISION_PREDICTION_LEDGER",
            "OBTAIN_INDEPENDENT_POST_HORIZON_BROKER_OR_REFERENCE_QUOTE_RECEIPTS",
            "OBTAIN_BROKER_FILL_SPREAD_COMMISSION_AND_SLIPPAGE_RECORDS_IF_TRADES_EXIST",
            "VERIFY_PRIOR_HELDOUT_SPLIT_FREEZE_USING_EXTERNAL_TIMESTAMP_AND_CUSTODY",
            "RECONCILE_EVIDENCE_WITH_PHASE3_REPORT_WITHOUT_BACKFILLING_OR_RETROACTIVE_REGISTRATION",
        ],
        "files_created": False,
        "files_modified": False,
        "database_accessed": False,
        "network_accessed": False,
        "broker_connected": False,
        "collector_started": False,
        "council_invoked": False,
        "council_seats_run": 0,
        "training_authorized": False,
        "execution_authorized": False,
        "real_forward_council_admission": False,
        "read_only": True,
        "final_action": "HOLD",
    }

    result["report_sha256"] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--read-only-evidence",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--read-only-phase3",
        action="store_true",
        required=True,
    )
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--evidence-manifest")
    parser.add_argument("--evidence-root")
    parser.add_argument("--phase3-report")
    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args(argv)

    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise EvidenceCustodyError(
                "Future inspection cutoff refused."
            )

        result = assess_stage4_prospective_custody(
            epoch_id=args.epoch_id,
            decision_at=args.decision_at,
            as_of=args.as_of,
            evidence_manifest=args.evidence_manifest,
            evidence_root=args.evidence_root,
            phase3_report=args.phase3_report,
        )

    except (EvidenceCustodyError, OSError, ValueError) as exc:
        print(
            "STAGE 4 PROSPECTIVE CUSTODY BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(
            json.dumps(
                result,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        print(
            "EURUSD ORACLE | Stage 4 S4-17..20 prospective evidence custody"
        )
        print(
            "Phase 3 report: "
            + result["phase3_evaluation"]["status"]
        )
        print(
            "Existing evidence manifest: "
            + result["existing_artifact_custody"]["status"]
        )
        print(
            "Locally hash-matched files: "
            + str(
                result[
                    "existing_artifact_custody"
                ]["files_locally_hash_matched"]
            )
        )
        print(
            "Independently verified external gates: 0/4"
        )
        print(
            "Performance and costs: NOT MEASURED"
        )

        for blocker in result["blockers"]:
            print("  BLOCKER " + blocker)

        print(
            "SHA-256: "
            + result["report_sha256"]
        )
        print(
            "READ-ONLY | NO REGISTRATION | NO COUNCIL "
            "| NO TRAINING | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
