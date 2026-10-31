
"""Offline candidate evidence intake for EURUSD ORACLE.

Processes actual locally saved provider documents and research receipts.

A matching SHA-256 proves local byte consistency against a declared hash.
It does NOT prove source authenticity, independent clock custody,
independent reviewer approval, market-data validity, or gate admission.

No network, broker, trading, collector, Council, training, or promotion.
The historical epoch and original clock-policy bytes remain pinned.
FINAL=HOLD always.

A template may be created only by explicit --create-template.
Evidence source files are never modified by this module.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit


EPOCH = (
    "75311e239cd5c295d3e08e03c358488fb123a46bfc6d342dd31493d95710aa33"
)

POLICY_SHA256 = (
    "e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088"
)

VERSION = "EURUSD_STAGE7_EXTERNAL_EVIDENCE_CANDIDATE_INTAKE_V1"

MAX_MANIFEST_BYTES = 131072
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_ARTIFACTS = 64

HEX = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
SAFE_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}$")
SAFE_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]{0,125}[a-z0-9])?$"
)

ALLOWED_EXTENSIONS = {
    ".pdf",
    ".csv",
    ".txt",
    ".json",
    ".jsonl",
    ".xml",
    ".html",
}

UTC = timezone.utc

# G01-G07 match the established Stage 7 handover requirements.
# G08-G11 are the independent evaluation evidence requirements.
# These definitions organize candidates; they cannot authorize gates.

GATES = {
    "G01": (
        "BROKER_SERVER_IDENTITY",
        "BROKER_IDENTITY_RECORD",
    ),
    "G02": (
        "INDEPENDENT_BROKER_UTC",
        "INDEPENDENT_CLOCK_RECORD",
    ),
    "G03": (
        "ORIGINAL_CAPTURE_CUSTODY",
        "RAW_CAPTURE_RECEIPT",
    ),
    "G04": (
        "PROSPECTIVE_EPOCH_APPROVAL",
        "EPOCH_APPROVAL_DOCUMENT",
    ),
    "G05": (
        "THIRTEEN_FAMILY_SOURCE_ADMISSION",
        "SOURCE_FAMILY_PUBLICATION",
    ),
    "G06": (
        "FEATURE_MODEL_POLICY_ANCESTRY",
        "ANCESTRY_RECORD",
    ),
    "G07": (
        "RISK_RECOVERY_OPERATOR_HANDOVER",
        "RISK_REVIEW_RECORD",
    ),
    "G08": (
        "INDEPENDENT_PREDECISION_FORECAST_RECEIPT",
        "FORECAST_RECEIPT",
    ),
    "G09": (
        "INDEPENDENT_POSTHORIZON_OUTCOME",
        "OUTCOME_RECEIPT",
    ),
    "G10": (
        "AUTHENTIC_COST_AND_NO_TRADE_EVIDENCE",
        "COST_AND_ABSTENTION_RECORD",
    ),
    "G11": (
        "INDEPENDENT_FROZEN_EVALUATION_HOLDOUT",
        "HOLDOUT_FREEZE_RECORD",
    ),
}

ARTIFACT_KEYS = {
    "artifact_id",
    "gate_id",
    "evidence_kind",
    "file_name",
    "expected_sha256",
    "origin_url",
    "published_at_utc",
    "acquired_at_utc",
    "family_slot",
}

MANIFEST_KEYS = {
    "schema_version",
    "historical_epoch_id",
    "created_at_utc",
    "artifacts",
}


class CandidateIntakeError(ValueError):
    """Candidate rejected without changing existing gate admission."""


def _timestamp(value):
    if (
        type(value) is not str
        or not (
            value.endswith("Z")
            or value.endswith("+00:00")
        )
    ):
        raise CandidateIntakeError(
            "Canonical UTC timestamp required."
        )

    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (ValueError, OverflowError):
        raise CandidateIntakeError(
            "Invalid UTC timestamp."
        ) from None

    if (
        parsed.utcoffset() is None
        or parsed.utcoffset().total_seconds() != 0
    ):
        raise CandidateIntakeError(
            "UTC offset must equal zero."
        )

    return parsed.astimezone(UTC)


def _unique_pairs(pairs):
    result = {}

    for key, value in pairs:
        if key in result:
            raise CandidateIntakeError(
                "Duplicate JSON field refused."
            )

        result[key] = value

    return result


def _reject_nonfinite(value):
    raise CandidateIntakeError(
        "Nonfinite JSON token refused."
    )


def _parse_json(raw):
    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_nonfinite,
        )
    except CandidateIntakeError:
        raise
    except (
        ValueError,
        TypeError,
        UnicodeError,
        json.JSONDecodeError,
    ):
        raise CandidateIntakeError(
            "Malformed evidence manifest."
        ) from None


def _ensure_directory(path):
    root = Path(path)

    if not root.is_absolute() or not root.is_dir():
        raise CandidateIntakeError(
            "Evidence root must be an existing absolute directory."
        )

    if any(
        part.is_symlink()
        for part in (root, *root.parents)
    ):
        raise CandidateIntakeError(
            "Symlinked evidence root refused."
        )

    return root


def _name(value, *, suffixes=None):
    if (
        type(value) is not str
        or SAFE_NAME.fullmatch(value) is None
        or ".." in value
        or value in (".", "..")
        or (
            suffixes is not None
            and Path(value).suffix.lower() not in suffixes
        )
    ):
        raise CandidateIntakeError(
            "Only safe flat evidence filenames are allowed."
        )

    return value


def _read_regular(root, filename, max_bytes):
    """Read ordinary local bytes without treating them as trusted custody."""

    path = root / filename

    if path.is_symlink():
        raise CandidateIntakeError(
            "Symlink evidence file refused."
        )

    try:
        before = path.stat(follow_symlinks=False)

        if (
            not stat.S_ISREG(before.st_mode)
            or not 0 < before.st_size <= max_bytes
        ):
            raise CandidateIntakeError(
                "Evidence file size/type rejected."
            )

        chunks = []
        count = 0

        with path.open("rb") as handle:
            first = os.fstat(handle.fileno())

            if not stat.S_ISREG(first.st_mode):
                raise CandidateIntakeError(
                    "Evidence handle not an ordinary file."
                )

            while True:
                chunk = handle.read(65536)

                if not chunk:
                    break

                chunks.append(chunk)
                count += len(chunk)

                if count > max_bytes:
                    raise CandidateIntakeError(
                        "Evidence read exceeded bound."
                    )

            last = os.fstat(handle.fileno())

        after = path.stat(follow_symlinks=False)

    except CandidateIntakeError:
        raise

    except (OSError, ValueError):
        raise CandidateIntakeError(
            "Cannot safely read local evidence bytes."
        ) from None

    def signature(item):
        return (
            item.st_dev,
            item.st_ino,
            item.st_size,
            item.st_mtime_ns,
        )

    if (
        signature(before) != signature(first)
        or signature(first) != signature(last)
        or signature(last) != signature(after)
        or count != before.st_size
        or path.is_symlink()
    ):
        raise CandidateIntakeError(
            "Unstable file identity or changed local bytes."
        )

    return b"".join(chunks)


def _read_manifest(root, filename):
    try:
        raw = _read_regular(
            root,
            _name(filename, suffixes={".json"}),
            MAX_MANIFEST_BYTES,
        )
    except CandidateIntakeError as exc:
        raise CandidateIntakeError(
            "Unsafe local manifest: " + str(exc)
        ) from exc

    return _parse_json(raw)


def _origin_host(value):
    if type(value) is not str or len(value) > 512:
        raise CandidateIntakeError(
            "Origin URL must be a bounded HTTPS reference."
        )

    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port

    except ValueError:
        raise CandidateIntakeError(
            "Invalid origin URL."
        ) from None

    if (
        parsed.scheme != "https"
        or not host
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.netloc != host
        or not SAFE_HOST.fullmatch(host)
        or ".." in host
        or "." not in host
    ):
        raise CandidateIntakeError(
            "Origin URL must be HTTPS with no credentials, "
            "query or fragment."
        )

    return host


def _read_policy_pin(path):
    policy = Path(path).absolute()

    if not policy.is_file() or policy.is_symlink():
        raise CandidateIntakeError(
            "Historical policy file unavailable."
        )

    root = _ensure_directory(policy.parent)

    original = _read_regular(
        root,
        _name(policy.name, suffixes={".json"}),
        MAX_MANIFEST_BYTES,
    )

    if sha256(original).hexdigest() != POLICY_SHA256:
        raise CandidateIntakeError(
            "Historical policy bytes do not match original pin."
        )

    return POLICY_SHA256


def _canonical_hash(body):
    raw = json.dumps(
        body,
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")

    return sha256(raw).hexdigest()


def make_empty_template(*, epoch_id, as_of, root, name):
    """Create a new editable local candidate manifest, never overwrite."""

    if epoch_id != EPOCH:
        raise CandidateIntakeError(
            "Historical epoch rebinding refused."
        )

    cutoff = _timestamp(as_of)

    if cutoff > datetime.now(UTC):
        raise CandidateIntakeError(
            "Future template cutoff refused."
        )

    folder = _ensure_directory(root)

    target = folder / _name(
        name,
        suffixes={".json"},
    )

    if target.exists() or target.is_symlink():
        raise CandidateIntakeError(
            "Existing manifest is never overwritten."
        )

    template = {
        "schema_version": 1,
        "historical_epoch_id": EPOCH,
        "created_at_utc": cutoff.isoformat(),
        "artifacts": [],
    }

    raw = (
        json.dumps(
            template,
            indent=2,
            sort_keys=True,
        ) + "\n"
    ).encode("utf-8")

    try:
        with target.open("xb") as handle:
            handle.write(raw)

    except OSError:
        raise CandidateIntakeError(
            "Cannot create new local candidate template."
        ) from None

    return _canonical_hash(template)


def assess_evidence_candidates(
    *,
    epoch_id,
    as_of,
    evidence_root,
    manifest=None,
    policy_file,
):
    """Evaluate genuine local candidate files without admitting evidence."""

    if epoch_id != EPOCH:
        raise CandidateIntakeError(
            "Historical epoch rebinding refused."
        )

    cutoff = _timestamp(as_of)

    if cutoff > datetime.now(UTC):
        raise CandidateIntakeError(
            "Future evidence cutoff refused."
        )

    pinned = _read_policy_pin(policy_file)
    root = _ensure_directory(evidence_root)

    document = (
        None
        if manifest is None
        else _read_manifest(root, manifest)
    )

    artifacts = []

    if document is not None:
        if (
            type(document) is not dict
            or set(document) != MANIFEST_KEYS
            or type(document["schema_version"]) is not int
            or document["schema_version"] != 1
            or document["historical_epoch_id"] != EPOCH
            or _timestamp(document["created_at_utc"]) > cutoff
            or type(document["artifacts"]) is not list
            or len(document["artifacts"]) > MAX_ARTIFACTS
        ):
            raise CandidateIntakeError(
                "Manifest schema, epoch or chronology invalid."
            )

        artifacts = document["artifacts"]

    rows = []
    used_ids = set()
    used_files = set()

    locally_matched = 0
    total_bytes = 0

    for item in artifacts:
        if (
            type(item) is not dict
            or set(item) != ARTIFACT_KEYS
        ):
            raise CandidateIntakeError(
                "Artifact schema refuses extra or missing fields."
            )

        identifier = item["artifact_id"]

        if (
            type(identifier) is not str
            or SAFE_ID.fullmatch(identifier) is None
        ):
            raise CandidateIntakeError(
                "Invalid artifact ID."
            )

        gate = item["gate_id"]

        if (
            type(gate) is not str
            or gate not in GATES
            or item["evidence_kind"] != GATES[gate][1]
        ):
            raise CandidateIntakeError(
                "Wrong gate or mismatched document kind."
            )

        filename = _name(
            item["file_name"],
            suffixes=ALLOWED_EXTENSIONS,
        )

        if (
            identifier in used_ids
            or filename.lower() in used_files
        ):
            raise CandidateIntakeError(
                "Duplicate ID or file reuse refused."
            )

        used_ids.add(identifier)
        used_files.add(filename.lower())

        expected = item["expected_sha256"]

        if (
            type(expected) is not str
            or not HEX.fullmatch(expected)
        ):
            raise CandidateIntakeError(
                "Expected lowercase SHA-256 required."
            )

        host = _origin_host(item["origin_url"])

        slot = item["family_slot"]

        if (
            slot is not None
            and (
                gate != "G05"
                or type(slot) is not int
                or not 1 <= slot <= 13
            )
        ):
            raise CandidateIntakeError(
                "Family planning slot must belong to G05."
            )

        published = (
            None
            if item["published_at_utc"] is None
            else _timestamp(item["published_at_utc"])
        )

        acquired = _timestamp(
            item["acquired_at_utc"]
        )

        if (
            acquired > cutoff
            or (
                published is not None
                and (
                    published > acquired
                    or published > cutoff
                )
            )
        ):
            raise CandidateIntakeError(
                "Future or noncausal publication/acquisition."
            )

        file_path = root / filename

        if file_path.is_symlink():
            raise CandidateIntakeError(
                "Symlink evidence candidate refused."
            )

        if not file_path.exists():
            status = "LOCAL_FILE_MISSING"
            actual = None
            size = None

        else:
            raw = _read_regular(
                root,
                filename,
                MAX_FILE_BYTES,
            )

            size = len(raw)
            total_bytes += size

            if total_bytes > MAX_TOTAL_BYTES:
                raise CandidateIntakeError(
                    "Cumulative candidate bytes exceeded budget."
                )

            actual = sha256(raw).hexdigest()

            if not hmac.compare_digest(
                actual,
                expected,
            ):
                status = "HASH_MISMATCH_QUARANTINED"

            elif published is None:
                status = (
                    "LOCAL_BYTES_MATCH_PUBLICATION_TIME_UNKNOWN"
                )

            else:
                status = "LOCAL_BYTES_MATCH_UNATTESTED"

            if actual == expected:
                locally_matched += 1

        rows.append({
            "artifact_id": identifier,
            "gate_id": gate,
            "family_planning_slot": slot,
            "claimed_origin_host_unverified": host,
            "status": status,
            "observed_sha256": actual,
            "local_file_bytes": size,
            "publication_time_known_only_by_manifest": (
                published is not None
            ),
            "independently_authenticated": False,
            "independently_admitted": False,
            "runtime_permissions_granted": False,
        })

    counts = {
        gate: 0
        for gate in GATES
    }

    family_candidate_slots = set()

    for row in rows:
        if row["status"] == "LOCAL_BYTES_MATCH_UNATTESTED":
            counts[row["gate_id"]] += 1

            if row["family_planning_slot"] is not None:
                family_candidate_slots.add(
                    row["family_planning_slot"]
                )

    board = []

    for gate, definition in GATES.items():
        board.append({
            "gate_id": gate,
            "requirement": definition[0],
            "local_candidates_with_time_and_hash": counts[gate],
            "independently_verified": False,
            "admission_status": "NOT_ADMITTED",
        })

    families = []

    for index in range(1, 14):
        families.append({
            "planning_slot": index,
            "local_candidate_present": (
                index in family_candidate_slots
            ),
            "canonical_stage7_family_mapping_verified": False,
            "independently_admitted": False,
        })

    result = {
        "scope": VERSION,
        "historical_epoch_id": EPOCH,
        "as_of_utc": cutoff.isoformat(),
        "pinned_policy_sha256": pinned,
        "manifest_status": (
            "NOT_SUPPLIED"
            if document is None
            else "LOCAL_MANIFEST_UNATTESTED"
        ),
        "candidate_count": len(rows),
        "locally_hash_matching_candidates": locally_matched,
        "candidates": rows,
        "external_gate_board": board,
        "family_planning_board": families,
        "independent_external_gates_verified": 0,
        "independent_source_families_admitted": 0,
        "independent_clock_verified": False,
        "operator_signed_authorization_verified": False,
        "admission_or_execution_permission": False,
        "upstream_stage7_admission_registry_modified": False,
        "network_accessed": False,
        "collector_started": False,
        "council_invoked": False,
        "broker_connected": False,
        "paper_orders_authorized": False,
        "live_orders_authorized": False,
        "research_operational_readiness": (
            "BLOCKED_EXTERNAL_EVIDENCE"
        ),
        "next_step": (
            "INDEPENDENT_SOURCE_AND_OPERATOR_REVIEW_OUTSIDE_THIS_TOOL"
        ),
        "final_action": "HOLD",
    }

    result["report_sha256"] = _canonical_hash(result)

    return result


def run(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--read-only-evidence",
        required=True,
        action="store_true",
    )

    parser.add_argument(
        "--offline-only",
        required=True,
        action="store_true",
    )

    parser.add_argument(
        "--epoch-id",
        required=True,
    )

    parser.add_argument(
        "--as-of",
        required=True,
    )

    parser.add_argument(
        "--policy-file",
        required=True,
    )

    parser.add_argument(
        "--evidence-root",
        required=True,
    )

    action = parser.add_mutually_exclusive_group()

    action.add_argument("--manifest")
    action.add_argument("--create-template")

    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args(argv)

    try:
        if args.create_template is not None:
            _read_policy_pin(args.policy_file)

            digest = make_empty_template(
                epoch_id=args.epoch_id,
                as_of=args.as_of,
                root=args.evidence_root,
                name=args.create_template,
            )

            print(
                "LOCAL CANDIDATE TEMPLATE CREATED; "
                "no gate admission; SHA-256: " + digest
            )

            print(
                "NO NETWORK | NO BROKER | NO ORDERS | FINAL=HOLD"
            )

            return 0

        result = assess_evidence_candidates(
            epoch_id=args.epoch_id,
            as_of=args.as_of,
            evidence_root=args.evidence_root,
            manifest=args.manifest,
            policy_file=args.policy_file,
        )

    except (
        CandidateIntakeError,
        OSError,
        TypeError,
        ValueError,
        OverflowError,
    ):
        print(
            "EXTERNAL EVIDENCE INTAKE BLOCKED: "
            "unsafe input or pinned policy mismatch."
        )

        return 1

    if args.json:
        print(json.dumps(
            result,
            sort_keys=True,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        ))

    else:
        print(
            "EURUSD ORACLE | Stage 7 external "
            "evidence candidate intake"
        )

        print(
            "Manifest: " + result["manifest_status"]
        )

        print(
            "Candidate files: %d | Local hash matches: %d"
            % (
                result["candidate_count"],
                result["locally_hash_matching_candidates"],
            )
        )

        print(
            "Independent gates: 0/11 | "
            "Independently admitted families: 0/13"
        )

        print(
            "Historical policy: ORIGINAL_BYTES_MATCH_PIN"
        )

        print(
            "Report SHA-256: " + result["report_sha256"]
        )

        print(
            "READ ONLY | NO NETWORK | NO BROKER | "
            "NO COUNCIL | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
