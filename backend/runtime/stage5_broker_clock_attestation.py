
"""S5-05..08: read-only broker identity/clock evidence reconciliation.

Original policy/probe are historical local evidence. Optional candidate files
may corroborate hashes and chronology, but do NOT establish independent trust.
No broker connection, new epoch, recorder restart, or Council admission.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path, PurePosixPath
import re

from backend.data_engine.clock_policy import (
    ClockPolicy,
    ClockPolicyError,
    hfm_wall_to_utc_msc,
    implementation_sha256,
    instant,
)

VERSION = "EURUSD_STAGE5_BROKER_CLOCK_S5_05_08_V1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
LABEL = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
MAX_POLICY = 16384
MAX_PROBE = 65536
MAX_BUNDLE = 65536
MAX_DOCUMENT = 4194304
UTC = timezone.utc


class Stage5ClockError(ValueError):
    """Malformed or misleading supplied local evidence."""


def _utc(value):
    if not isinstance(value, str):
        raise Stage5ClockError("UTC timestamp text required.")
    try:
        when = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except (ValueError, OverflowError):
        raise Stage5ClockError("UTC timestamp invalid.") from None
    if when.tzinfo is None or when.utcoffset() != timedelta(0):
        raise Stage5ClockError(
            "Explicit UTC offset zero required."
        )
    return when.astimezone(UTC)


def _hash(value):
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Stage5ClockError("Duplicate JSON field.")
        result[key] = value
    return result


def _read(path, limit):
    file = Path(path)
    if file.is_symlink() or not file.is_file():
        raise Stage5ClockError(
            "Existing regular evidence file required."
        )
    try:
        with file.open("rb") as stream:
            contents = stream.read(limit + 1)
    except OSError:
        raise Stage5ClockError(
            "Evidence file unavailable."
        ) from None
    if len(contents) > limit:
        raise Stage5ClockError(
            "Evidence exceeds bounded size."
        )
    return contents


def _json(raw):
    try:
        data = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique,
            parse_constant=lambda _: (
                _ for _ in ()
            ).throw(
                Stage5ClockError(
                    "Nonfinite JSON refused."
                )
            ),
        )
    except Stage5ClockError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise Stage5ClockError(
            "Evidence JSON invalid."
        ) from None
    if not isinstance(data, dict):
        raise Stage5ClockError(
            "JSON evidence must be an object."
        )
    return data


def _ms(delta):
    return round(delta.total_seconds() * 1000)


def _positive_int(value):
    return type(value) is int and value > 0


def inspect_original_broker_probe(
    *,
    policy_file,
    probe_file,
    epoch_id,
    expected_policy_sha256,
):
    """Verify historical policy/probe binding and local semantics."""
    if (
        not isinstance(epoch_id, str)
        or not HEX64.fullmatch(epoch_id)
    ):
        raise Stage5ClockError(
            "Invalid bound epoch ID."
        )

    if (
        not isinstance(expected_policy_sha256, str)
        or not HEX64.fullmatch(expected_policy_sha256)
    ):
        raise Stage5ClockError(
            "Invalid expected policy digest."
        )

    original_policy = _read(
        policy_file, MAX_POLICY
    )

    if sha256(original_policy).hexdigest() != expected_policy_sha256:
        raise Stage5ClockError(
            "Original policy bytes differ from expected digest."
        )

    try:
        policy = ClockPolicy.model_validate(
            _json(original_policy)
        )
    except (ClockPolicyError, ValueError, TypeError):
        raise Stage5ClockError(
            "Original policy contract invalid."
        ) from None

    if (
        policy.epoch_id != epoch_id
        or policy.historical_admission is not False
    ):
        raise Stage5ClockError(
            "Original epoch identity or admission mismatched."
        )

    original_probe = _read(
        probe_file, MAX_PROBE
    )
    probe_sha = sha256(
        original_probe
    ).hexdigest()

    if probe_sha != policy.evidence_sha256:
        raise Stage5ClockError(
            "Original probe does not match bound SHA-256."
        )

    probe = _json(original_probe)

    if (
        probe.get("schema_version") != 1
        or type(probe.get("schema_version")) is not int
        or probe.get("initialized") is not True
        or probe.get("read_only") is not True
        or probe.get("historical_utc_certified") is not False
        or probe.get("hfm_company_match") is not True
        or probe.get("company_sha256") != policy.company_sha256
        or probe.get("server_sha256") != policy.server_sha256
        or probe.get("error_type")
        or probe.get("blocker")
    ):
        raise Stage5ClockError(
            "Original probe contract or identity inconsistent."
        )

    observations = probe.get("observations")
    if (
        not isinstance(observations, list)
        or not 3 <= len(observations) <= 32
    ):
        raise Stage5ClockError(
            "Original probe observation count invalid."
        )

    receipts = []
    residual_ms = []
    wall_offsets = []

    for item in observations:
        if not isinstance(item, dict):
            raise Stage5ClockError(
                "Original probe observation invalid."
            )

        try:
            before = _utc(item["received_before"])
            after = _utc(item["received_after"])
            raw_ms = item["raw_time_msc"]
            raw_sec = item["raw_time"]

            if (
                not _positive_int(raw_ms)
                or not _positive_int(raw_sec)
                or raw_ms // 1000 != raw_sec
                or not before <= after <= policy.valid_from
            ):
                raise Stage5ClockError(
                    "Original probe receipt chronology invalid."
                )

            wall = instant(raw_ms)
            converted = instant(
                hfm_wall_to_utc_msc(raw_ms)
            )

            residual = _ms(converted - after)
            offset = _ms(wall - converted) // 1000

        except (
            KeyError,
            ClockPolicyError,
            OverflowError,
            TypeError,
        ):
            raise Stage5ClockError(
                "Original probe timestamp invalid."
            ) from None

        if (
            not -180000 <= residual <= 5000
            or offset not in (7200, 10800)
        ):
            raise Stage5ClockError(
                "Original probe clock convention inconsistent."
            )

        if (
            "raw_rendered_as_utc" in item
            and _utc(item["raw_rendered_as_utc"]) != wall
        ):
            raise Stage5ClockError(
                "Original raw rendered clock inconsistent."
            )

        if "skew_seconds" in item:
            skew = item["skew_seconds"]

            if (
                type(skew) not in (int, float)
                or not isfinite(skew)
                or abs(
                    skew - (wall - after).total_seconds()
                ) > 0.02
            ):
                raise Stage5ClockError(
                    "Original reported clock skew inconsistent."
                )

        receipts.append(after)
        residual_ms.append(residual)
        wall_offsets.append(offset)

    if (
        receipts != sorted(receipts)
        or len(set(receipts)) != len(receipts)
        or receipts[-1] - receipts[0] < timedelta(seconds=1)
        or receipts[-1] != policy.valid_from
        or len(set(wall_offsets)) != 1
    ):
        raise Stage5ClockError(
            "Original repeated probe receipts inconsistent."
        )

    query = probe.get("query_evidence")

    if (
        not isinstance(query, list)
        or len(query) != 2
        or not all(
            isinstance(q, dict)
            and isinstance(q.get("kind"), str)
            for q in query
        )
        or {
            q["kind"]
            for q in query
        } != {"utc_request", "raw_clock_request"}
    ):
        raise Stage5ClockError(
            "Original probe query kinds incomplete."
        )

    try:
        for q in query:
            if not _positive_int(q["row_count"]):
                raise Stage5ClockError(
                    "Original query row count invalid."
                )

            first = instant(q["first_raw_msc"])
            last = instant(q["last_raw_msc"])
            start = _utc(q["requested_start"])
            end = _utc(q["requested_end"])

            if not start <= first <= last < end + timedelta(seconds=1):
                raise Stage5ClockError(
                    "Original query raw clock bounds inconsistent."
                )

    except (
        KeyError,
        ClockPolicyError,
        OverflowError,
        TypeError,
    ):
        raise Stage5ClockError(
            "Original query observation invalid."
        ) from None

    return policy, {
        "epoch_id": epoch_id,
        "policy_file_sha256": expected_policy_sha256,
        "probe_file_sha256": probe_sha,
        "bound_clock_code_sha256": policy.implementation_sha256,
        "current_clock_code_sha256": implementation_sha256(),
        "current_code_byte_exact_to_epoch": (
            policy.implementation_sha256 == implementation_sha256()
        ),
        "policy_and_original_probe_locally_bound": True,
        "company_sha256": policy.company_sha256,
        "server_sha256": policy.server_sha256,
        "historical_observation_count": len(observations),
        "historical_query_kind_count": len(query),
        "original_probe_last_receipt": receipts[-1].isoformat(),
        "bound_broker_wall_offset_seconds": wall_offsets[0],
        "local_quote_residual_ms_min": min(residual_ms),
        "local_quote_residual_ms_max": max(residual_ms),
        "offset_is_policy_mapping_not_independent_clock_measurement": True,
        "original_broker_or_utc_evidence_independently_attested": False,
        "historical_utc_certified": False,
    }


def _contained(root, name):
    if (
        not isinstance(name, str)
        or not 1 <= len(name) <= 240
        or "\\" in name
        or ":" in name
        or "\x00" in name
    ):
        raise Stage5ClockError(
            "Unsafe document relative path."
        )

    segments = name.split("/")

    if (
        PurePosixPath(name).is_absolute()
        or any(
            segment in ("", ".", "..")
            for segment in segments
        )
    ):
        raise Stage5ClockError(
            "Document path traversal refused."
        )

    directory = Path(root)

    if (
        not directory.is_dir()
        or directory.is_symlink()
    ):
        raise Stage5ClockError(
            "Existing non-symlink document root required."
        )

    target = directory

    for segment in segments:
        target = target / segment

        if target.is_symlink():
            raise Stage5ClockError(
                "Symlinked evidence document refused."
            )

    return target


def _document(entry, root, as_of):
    if (
        not isinstance(entry, dict)
        or set(entry) != {
            "relative_path",
            "file_sha256",
            "claimed_issuer",
            "claimed_issued_at",
            "claimed_received_at",
        }
    ):
        raise Stage5ClockError(
            "Document metadata contract invalid."
        )

    digest = entry["file_sha256"]
    issuer = entry["claimed_issuer"]

    if (
        not isinstance(digest, str)
        or not HEX64.fullmatch(digest)
        or not isinstance(issuer, str)
        or not LABEL.fullmatch(issuer)
    ):
        raise Stage5ClockError(
            "Document hash/claimed issuer invalid."
        )

    if not (
        _utc(entry["claimed_issued_at"])
        <= _utc(entry["claimed_received_at"])
        <= as_of
    ):
        raise Stage5ClockError(
            "Document claimed issue/receipt order invalid."
        )

    file = _contained(
        root, entry["relative_path"]
    )

    if sha256(
        _read(file, MAX_DOCUMENT)
    ).hexdigest() != digest:
        raise Stage5ClockError(
            "Document bytes do not match claimed SHA-256."
        )

    return {
        "file_sha256": digest,
        "local_bytes_match": True,
        "independent_issuer_verified": False,
    }


def inspect_candidate_bundle(
    bundle_path,
    *,
    root,
    policy,
    epoch_id,
    as_of,
):
    """Reconcile optional broker, terminal and reference claims."""
    empty = {
        "status": "NOT_SUPPLIED",
        "bundle_sha256": None,
        "documents_locally_hash_matched": 0,
        "identity_hashes_match_bound_policy": None,
        "reference_kind_claimed": None,
        "claimed_reference_uncertainty_ms": None,
        "time_samples_consistent": None,
        "time_sample_count": 0,
        "sample_quote_age_residual_ms_min": None,
        "sample_quote_age_residual_ms_max": None,
        "provider_origin_independently_verified": False,
        "utc_reference_independently_verified": False,
        "broker_clock_offset_independently_verified": False,
    }

    if bundle_path is None:
        if root is not None:
            raise Stage5ClockError(
                "Document root without bundle refused."
            )
        return empty

    if root is None:
        raise Stage5ClockError(
            "Document root required with candidate bundle."
        )

    raw = _read(bundle_path, MAX_BUNDLE)
    bundle = _json(raw)

    if (
        set(bundle) != {
            "schema_version",
            "epoch_id",
            "as_of",
            "broker_identity",
            "utc_reference",
        }
        or type(bundle["schema_version"]) is not int
        or bundle["schema_version"] != 1
        or bundle["epoch_id"] != epoch_id
        or _utc(bundle["as_of"]) != as_of
    ):
        raise Stage5ClockError(
            "Candidate bundle identity/review scope mismatch."
        )

    identity = bundle["broker_identity"]
    reference = bundle["utc_reference"]

    if (
        not isinstance(identity, dict)
        or set(identity) != {
            "claimed_company",
            "claimed_server",
            "provider_document",
            "terminal_document",
        }
    ):
        raise Stage5ClockError(
            "Broker identity contract invalid."
        )

    if (
        not isinstance(reference, dict)
        or set(reference) != {
            "reference_document",
            "reference_kind",
            "claimed_uncertainty_ms",
            "samples",
        }
    ):
        raise Stage5ClockError(
            "Clock reference contract invalid."
        )

    company = identity["claimed_company"]
    server = identity["claimed_server"]

    if not all(
        isinstance(x, str) and 1 <= len(x) <= 256
        for x in (company, server)
    ):
        raise Stage5ClockError(
            "Broker identity labels invalid."
        )

    if (
        sha256(company.encode("utf-8")).hexdigest()
            != policy.company_sha256
        or sha256(server.encode("utf-8")).hexdigest()
            != policy.server_sha256
    ):
        raise Stage5ClockError(
            "Candidate broker identity differs from bound epoch."
        )

    docs = [
        _document(
            identity["provider_document"], root, as_of
        ),
        _document(
            identity["terminal_document"], root, as_of
        ),
        _document(
            reference["reference_document"], root, as_of
        ),
    ]

    if reference["reference_kind"] not in (
        "ntp",
        "ptp",
        "gps",
        "time_authority",
        "other",
    ):
        raise Stage5ClockError(
            "Unknown claimed UTC reference kind."
        )

    uncertainty = reference["claimed_uncertainty_ms"]

    if (
        type(uncertainty) is not int
        or not 0 <= uncertainty <= 2000
    ):
        raise Stage5ClockError(
            "Claimed UTC uncertainty out of range."
        )

    samples = reference["samples"]

    if (
        not isinstance(samples, list)
        or not 3 <= len(samples) <= 32
    ):
        raise Stage5ClockError(
            "Clock sample count must be 3..32."
        )

    times = []
    residuals = []

    for sample in samples:
        if (
            not isinstance(sample, dict)
            or set(sample) != {
                "broker_raw_time_msc",
                "received_before",
                "received_after",
                "reference_utc",
            }
        ):
            raise Stage5ClockError(
                "Clock sample fields invalid."
            )

        raw_time = sample["broker_raw_time_msc"]

        if not _positive_int(raw_time):
            raise Stage5ClockError(
                "Broker raw clock value invalid."
            )

        before = _utc(sample["received_before"])
        after = _utc(sample["received_after"])
        referenced = _utc(sample["reference_utc"])

        if (
            not policy.valid_from
                <= before
                <= referenced
                <= after
                <= as_of
            or after >= policy.valid_until
            or after - before > timedelta(seconds=5)
        ):
            raise Stage5ClockError(
                "Clock sample receipt/reference ordering invalid."
            )

        try:
            converted = instant(
                hfm_wall_to_utc_msc(raw_time)
            )
        except (
            ClockPolicyError,
            ValueError,
            OverflowError,
        ):
            raise Stage5ClockError(
                "Broker wall-clock conversion unsupported."
            ) from None

        residual = _ms(
            converted - referenced
        )

        if not -180000 <= residual <= 5000:
            raise Stage5ClockError(
                "Sample quote/reference residual exceeds bounded convention."
            )

        times.append(referenced)
        residuals.append(residual)

    if (
        times != sorted(times)
        or len(set(times)) != len(times)
        or times[-1] - times[0] < timedelta(seconds=1)
    ):
        raise Stage5ClockError(
            "Repeated clock observations not causally ordered."
        )

    return {
        "status": "LOCAL_CANDIDATE_TRIANGULATION_UNATTESTED",
        "bundle_sha256": sha256(raw).hexdigest(),
        "documents_locally_hash_matched": len(docs),
        "identity_hashes_match_bound_policy": True,
        "reference_kind_claimed": reference["reference_kind"],
        "claimed_reference_uncertainty_ms": uncertainty,
        "time_samples_consistent": True,
        "time_sample_count": len(times),
        "sample_quote_age_residual_ms_min": min(residuals),
        "sample_quote_age_residual_ms_max": max(residuals),
        "provider_origin_independently_verified": False,
        "utc_reference_independently_verified": False,
        "broker_clock_offset_independently_verified": False,
    }


def assess_stage5_broker_clock(
    *,
    policy_file,
    probe_file,
    epoch_id,
    expected_policy_sha256,
    as_of,
    candidate_bundle=None,
    evidence_root=None,
):
    at = _utc(as_of)

    policy, baseline = inspect_original_broker_probe(
        policy_file=policy_file,
        probe_file=probe_file,
        epoch_id=epoch_id,
        expected_policy_sha256=expected_policy_sha256,
    )

    if at < policy.valid_from:
        raise Stage5ClockError(
            "Review cutoff precedes bound epoch."
        )

    candidate = inspect_candidate_bundle(
        candidate_bundle,
        root=evidence_root,
        policy=policy,
        epoch_id=epoch_id,
        as_of=at,
    )

    blockers = [
        "BROKER_PROVIDER_IDENTITY_NOT_INDEPENDENTLY_AUTHENTICATED",
        "UTC_REFERENCE_ORIGIN_AND_UNCERTAINTY_NOT_INDEPENDENTLY_ATTESTED",
        "BROKER_CLOCK_OFFSET_NOT_INDEPENDENTLY_ATTESTED",
        "EXTERNAL_CUSTODY_AND_OPERATOR_REVIEW_NOT_COMPLETED",
    ]

    if not baseline["current_code_byte_exact_to_epoch"]:
        blockers.append(
            "CURRENT_CLOCK_IMPLEMENTATION_DIFFERS_FROM_BOUND_EPOCH"
        )

    if candidate["status"] == "NOT_SUPPLIED":
        blockers.append(
            "NO_BROKER_IDENTITY_AND_CLOCK_CANDIDATE_BUNDLE"
        )

    if not policy.valid_from <= at < policy.valid_until:
        blockers.append(
            "BOUND_EPOCH_NOT_ACTIVE_AT_REVIEW_CUTOFF"
        )

    report = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id,
        "as_of": at.isoformat(),
        "original_baseline": baseline,
        "candidate_triangulation": candidate,
        "local_verification_level": (
            "ORIGINAL_PROBE_PLUS_LOCAL_CANDIDATES"
            if candidate["status"] != "NOT_SUPPLIED"
            else "ORIGINAL_POLICY_AND_PROBE_ONLY"
        ),
        "independently_verified_broker_identity": False,
        "independently_verified_utc_reference": False,
        "independently_verified_broker_clock_offset": False,
        "new_epoch_approved": False,
        "required_next_evidence": [
            "OBTAIN_GENUINE_BROKER_ACCOUNT_SERVER_CONFIRMATION_FROM_PROVIDER",
            "CAPTURE_ORIGINAL_TERMINAL_IDENTITY_RECEIPT_WITH_TRUE_CAPTURE_TIME",
            "OBTAIN_UTC_SOURCE_AUTHENTICATION_AND_DOCUMENT_MEASUREMENT_UNCERTAINTY",
            "OBTAIN_THIRD_PARTY_OR_BROKER_SIGNED_RECEIPT_CUSTODY",
            "INDEPENDENTLY_REVIEW_TRUST_ANCHORS_AND_OPERATOR_RECOVERY_PLAN",
            "DO_NOT_REUSE_OR_REBIND_OCTOBER_27_EPOCH_FOR_NEW_RECORDER",
        ],
        "blockers": sorted(blockers),
        "historical_epoch_mutated": False,
        "files_written": False,
        "database_accessed": False,
        "network_accessed": False,
        "broker_connected": False,
        "collector_started": False,
        "council_invoked": False,
        "training_authorized": False,
        "execution_authorized": False,
        "real_forward_admission": False,
        "read_only": True,
        "final_action": "HOLD",
    }

    report["report_sha256"] = _hash(report)
    return report


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
        "--read-only-clock",
        action="store_true",
        required=True,
    )
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", required=True)
    parser.add_argument("--probe-file", required=True)
    parser.add_argument(
        "--expected-policy-sha256",
        required=True,
    )
    parser.add_argument("--candidate-bundle")
    parser.add_argument("--evidence-root")
    parser.add_argument(
        "--json",
        action="store_true",
    )

    args = parser.parse_args(argv)

    try:
        if _utc(args.as_of) > datetime.now(UTC):
            raise Stage5ClockError(
                "Future review cutoff refused."
            )

        report = assess_stage5_broker_clock(
            policy_file=args.policy_file,
            probe_file=args.probe_file,
            epoch_id=args.epoch_id,
            expected_policy_sha256=args.expected_policy_sha256,
            as_of=args.as_of,
            candidate_bundle=args.candidate_bundle,
            evidence_root=args.evidence_root,
        )

    except (
        Stage5ClockError,
        ClockPolicyError,
        ValueError,
        OSError,
    ) as exc:
        print(
            "STAGE 5 BROKER CLOCK REVIEW BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
        )
    else:
        historical = report["original_baseline"]
        candidate = report["candidate_triangulation"]

        print(
            "EURUSD ORACLE | Stage 5 S5-05..08 broker clock evidence"
        )
        print(
            "Epoch: " + report["epoch_id"]
            + " | cutoff=" + report["as_of"]
        )
        print(
            "Original policy and probe locally bound: "
            + str(
                historical[
                    "policy_and_original_probe_locally_bound"
                ]
            )
        )
        print(
            "Original observations: "
            + str(historical["historical_observation_count"])
        )
        print(
            "Current clock implementation byte-exact: "
            + str(
                historical["current_code_byte_exact_to_epoch"]
            )
        )
        print(
            "Bound broker wall-label convention: "
            + str(historical["bound_broker_wall_offset_seconds"])
            + " seconds (NOT an independent offset measurement)"
        )
        print(
            "Candidate bundle: "
            + candidate["status"]
        )
        print(
            "Candidate documents / samples: %s / %s"
            % (
                candidate["documents_locally_hash_matched"],
                candidate["time_sample_count"],
            )
        )
        print(
            "Broker identity and independent UTC: "
            "NOT INDEPENDENTLY VERIFIED"
        )

        for blocker in report["blockers"]:
            print("  BLOCKER " + blocker)

        print(
            "SHA-256: " + report["report_sha256"]
        )
        print(
            "READ-ONLY | NO MT5 | NO REBIND | "
            "NO COUNCIL | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
