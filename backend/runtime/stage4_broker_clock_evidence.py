
"""Stage 4 S4-05..08: read-only broker identity and clock evidence reconciliation.

Links the stored forward epoch, unmodified local policy, original clock probe,
and an optional historical Git blob. These are *local provenance checks*, not
independent broker attestation. Never contacts MT5, edits data, or starts jobs.
"""

import argparse
import ast
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess

from sqlalchemy import select

from backend.data_engine.clock_policy import (
    ClockPolicy, ClockPolicyError, decode_json, hfm_wall_to_utc_msc,
    implementation_sha256, instant,
)
from backend.data_engine.forward_store import epochs, hashed

VERSION = "EURUSD_STAGE4_BROKER_CLOCK_EVIDENCE_S4_05_08_V1"
SHA = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
CANDIDATE_PATH = "backend/data_engine/clock_policy.py"
MAX_POLICY_BYTES = 16384
MAX_PROBE_BYTES = 65536
MAX_SOURCE_BYTES = 131072
UTC = timezone.utc


class ClockEvidenceReviewError(RuntimeError):
    """Malformed input or contradictory local clock evidence."""


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ClockEvidenceReviewError("Explicit UTC timestamp required.")
    if value.utcoffset() != timedelta(0):
        raise ClockEvidenceReviewError("UTC offset must be zero.")
    return value.astimezone(UTC)


def _parse_utc(value):
    if not isinstance(value, str):
        raise ClockEvidenceReviewError("UTC timestamp must be text.")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, TypeError):
        raise ClockEvidenceReviewError("Invalid UTC timestamp.") from None


def _read_file(path, limit):
    file = Path(path)
    if file.is_symlink():
        raise ClockEvidenceReviewError("Symlink evidence refused.")
    try:
        with file.open("rb") as stream:
            data = stream.read(limit + 1)
    except OSError:
        raise ClockEvidenceReviewError("Evidence file unavailable.") from None
    if len(data) > limit:
        raise ClockEvidenceReviewError("Evidence file exceeds safe size.")
    return data


def _sha256(data):
    return sha256(data).hexdigest()


def _policy_from_bytes(raw):
    try:
        return ClockPolicy.model_validate(
            decode_json(raw, limit=MAX_POLICY_BYTES)
        )
    except (ClockPolicyError, ValueError, KeyError, TypeError):
        raise ClockEvidenceReviewError("Invalid clock policy data.") from None


def inspect_epoch_policy(
    engine, *, epoch_id, policy_file, expected_policy_sha256, as_of
):
    """SELECT-only epoch lookup plus exact local policy byte comparison."""
    at = _utc(as_of)
    if not isinstance(epoch_id, str) or not SHA.fullmatch(epoch_id):
        raise ClockEvidenceReviewError("Invalid epoch ID.")
    if (
        not isinstance(expected_policy_sha256, str)
        or not SHA.fullmatch(expected_policy_sha256)
    ):
        raise ClockEvidenceReviewError("Invalid expected file digest.")

    with engine.connect() as connection:
        row = connection.execute(
            select(
                epochs.c.epoch_id,
                epochs.c.policy_json,
                epochs.c.valid_from,
                epochs.c.valid_until,
                epochs.c.recorded_at,
            ).where(epochs.c.epoch_id == epoch_id)
        ).mappings().one_or_none()

    if row is None:
        raise ClockEvidenceReviewError("Stored epoch was not found.")
    if not isinstance(row["policy_json"], str):
        raise ClockEvidenceReviewError("Stored policy is not JSON text.")

    stored = _policy_from_bytes(row["policy_json"].encode("utf-8"))
    if (
        stored.epoch_id != epoch_id
        or _utc(row["valid_from"]) != stored.valid_from
        or _utc(row["valid_until"]) != stored.valid_until
        or _utc(row["recorded_at"]) > at
    ):
        raise ClockEvidenceReviewError(
            "Stored epoch identity or boundaries mismatch."
        )

    raw = _read_file(policy_file, MAX_POLICY_BYTES)
    disk = _policy_from_bytes(raw)

    if disk.model_dump(mode="json") != stored.model_dump(mode="json"):
        raise ClockEvidenceReviewError(
            "Local policy differs from stored epoch."
        )

    digest = _sha256(raw)
    if digest != expected_policy_sha256:
        raise ClockEvidenceReviewError(
            "Original policy file digest mismatch."
        )

    if stored.historical_admission is not False:
        raise ClockEvidenceReviewError(
            "Policy historical admission must be false."
        )

    return stored, {
        "epoch_id": epoch_id,
        "policy_file_sha256": digest,
        "stored_policy_matches_local_copy": True,
        "local_policy_file_matches_expected_digest": True,
        "stored_epoch_canonical_identity_matches": True,
        "bound_code_sha256": stored.implementation_sha256,
        "current_code_sha256": implementation_sha256(),
        "current_code_byte_exact": (
            stored.implementation_sha256 == implementation_sha256()
        ),
        "company_sha256_matches_stored_policy": True,
        "server_sha256_matches_stored_policy": True,
        "epoch_recorded_at": _utc(row["recorded_at"]).isoformat(),
        "epoch_valid_from": stored.valid_from.isoformat(),
        "epoch_valid_until": stored.valid_until.isoformat(),
        "epoch_active_at_inspection": (
            stored.valid_from <= at < stored.valid_until
        ),
        "historical_utc_admission": False,
        "original_policy_file_location_independently_attested": False,
    }


def inspect_original_probe(policy, path=None):
    """Inspect existing original probe bytes; do not re-create a clock policy."""
    result = {
        "status": "NOT_SUPPLIED",
        "probe_sha256": None,
        "probe_hash_matches_bound_policy": False,
        "probe_identity_hashes_match_policy": False,
        "repeated_probe_receipts_locally_consistent": False,
        "history_query_convention_locally_consistent": False,
        "observation_count": 0,
        "query_kind_count": 0,
        "independent_broker_probe_attested": False,
        "independent_broker_clock_attested": False,
        "source_file_location_independently_attested": False,
    }

    if path is None:
        return result

    try:
        raw = _read_file(path, MAX_PROBE_BYTES)
        digest = _sha256(raw)
        result["probe_sha256"] = digest
        result["probe_hash_matches_bound_policy"] = (
            digest == policy.evidence_sha256
        )

        if digest != policy.evidence_sha256:
            result["status"] = "PROBE_DIGEST_MISMATCH"
            return result

        probe = decode_json(raw, limit=MAX_PROBE_BYTES)

        if (
            probe.get("schema_version") != 1
            or probe.get("read_only") is not True
            or probe.get("initialized") is not True
            or probe.get("historical_utc_certified") is not False
            or probe.get("error_type")
            or probe.get("blocker")
        ):
            result["status"] = "PROBE_CONTRACT_INVALID"
            return result

        identity = (
            probe.get("company_sha256") == policy.company_sha256
            and probe.get("server_sha256") == policy.server_sha256
            and probe.get("hfm_company_match") is True
        )
        result["probe_identity_hashes_match_policy"] = identity

        observations = probe.get("observations")
        queries = probe.get("query_evidence")

        if (
            not isinstance(observations, list)
            or not 3 <= len(observations) <= 64
            or not isinstance(queries, list)
            or len(queries) != 2
        ):
            result["status"] = "PROBE_CONTRACT_INVALID"
            return result

        result["observation_count"] = len(observations)
        result["query_kind_count"] = len(queries)
        receipts = []

        for item in observations:
            if not isinstance(item, dict):
                raise ClockEvidenceReviewError(
                    "Invalid probe observation."
                )

            before = _parse_utc(item["received_before"])
            after = _parse_utc(item["received_after"])
            raw_sec = item["raw_time"]
            raw_ms = item["raw_time_msc"]

            if (
                type(raw_sec) is not int
                or type(raw_ms) is not int
                or raw_ms // 1000 != raw_sec
                or not before <= after <= policy.valid_from
            ):
                raise ClockEvidenceReviewError(
                    "Probe receipt or raw timestamps invalid."
                )

            converted = instant(hfm_wall_to_utc_msc(raw_ms))
            if not -180 <= (converted - after).total_seconds() <= 5:
                raise ClockEvidenceReviewError(
                    "Probe conversion exceeds clock bounds."
                )

            receipts.append(after)

        result["repeated_probe_receipts_locally_consistent"] = (
            receipts == sorted(receipts)
            and receipts[-1] - receipts[0] >= timedelta(seconds=1)
            and receipts[-1] == policy.valid_from
        )

        qmap = {}

        for item in queries:
            if (
                not isinstance(item, dict)
                or item.get("kind") in qmap
            ):
                raise ClockEvidenceReviewError(
                    "Duplicate/invalid query evidence."
                )

            kind = item.get("kind")
            if kind not in ("utc_request", "raw_clock_request"):
                raise ClockEvidenceReviewError(
                    "Invalid query clock kind."
                )

            if (
                type(item.get("row_count")) is not int
                or item["row_count"] <= 0
            ):
                raise ClockEvidenceReviewError(
                    "Nonpositive query evidence count."
                )

            first = item.get("first_raw_msc")
            last = item.get("last_raw_msc")
            start = _parse_utc(item["requested_start"])
            end = _parse_utc(item["requested_end"])

            if (
                type(first) is not int
                or type(last) is not int
                or not start <= instant(first) <= instant(last)
                < end + timedelta(seconds=1)
            ):
                raise ClockEvidenceReviewError(
                    "Query raw clock bounds invalid."
                )

            qmap[kind] = item

        result["history_query_convention_locally_consistent"] = (
            len(qmap) == 2
            and -90 <= (
                instant(
                    hfm_wall_to_utc_msc(
                        qmap["raw_clock_request"]["last_raw_msc"]
                    )
                ) - receipts[-1]
            ).total_seconds() <= 5
        )

        result["status"] = (
            "LOCAL_PROBE_LINKED_UNATTESTED"
            if (
                identity
                and result["repeated_probe_receipts_locally_consistent"]
                and result["history_query_convention_locally_consistent"]
            )
            else "LOCAL_PROBE_CHECKS_INCOMPLETE"
        )

    except (
        ClockEvidenceReviewError,
        ClockPolicyError,
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        OSError,
    ):
        result["status"] = "PROBE_UNREADABLE_OR_INCONSISTENT"

    return result


def inspect_historical_clock_blob(
    repo, *, ref=None, bound_sha256, current_file=None, runner=None
):
    """Read a Git blob without checkout/import. Git SHA is local history only."""
    result = {
        "status": "NOT_SUPPLIED",
        "candidate_commit": ref,
        "candidate_blob_sha256": None,
        "candidate_matches_bound_code": False,
        "candidate_ast_matches_current": None,
        "git_blob_independently_attested": False,
        "old_code_restored_or_executed": False,
    }

    if ref is None:
        return result

    if not isinstance(ref, str) or not COMMIT.fullmatch(ref):
        raise ClockEvidenceReviewError(
            "Invalid Git candidate commit."
        )

    if not SHA.fullmatch(bound_sha256):
        raise ClockEvidenceReviewError(
            "Invalid bound implementation SHA-256."
        )

    runner = runner or subprocess.run

    try:
        completed = runner(
            [
                "git", "show",
                f"{ref}:{CANDIDATE_PATH}",
            ],
            cwd=Path(repo),
            capture_output=True,
            check=False,
            timeout=12,
        )

        raw = completed.stdout

        if (
            completed.returncode != 0
            or len(raw) > MAX_SOURCE_BYTES
            or not raw
        ):
            result["status"] = "GIT_CANDIDATE_UNAVAILABLE"
            return result

        digest = _sha256(raw)
        result["candidate_blob_sha256"] = digest
        result["candidate_matches_bound_code"] = (
            digest == bound_sha256
        )

        if current_file is None:
            from backend.data_engine import clock_policy
            current_file = clock_policy.__file__

        current_bytes = _read_file(
            current_file, MAX_SOURCE_BYTES
        )

        result["candidate_ast_matches_current"] = (
            ast.dump(
                ast.parse(raw),
                include_attributes=False,
            )
            == ast.dump(
                ast.parse(current_bytes),
                include_attributes=False,
            )
        )

        result["status"] = (
            "LOCAL_BOUND_BLOB_FOUND_UNATTESTED"
            if result["candidate_matches_bound_code"]
            else "GIT_CANDIDATE_NOT_BOUND_IMPLEMENTATION"
        )

    except (
        OSError,
        ValueError,
        SyntaxError,
        subprocess.TimeoutExpired,
        ClockEvidenceReviewError,
    ):
        result["status"] = "GIT_CANDIDATE_UNAVAILABLE"

    return result


def assess_clock_gate(
    policy_info, probe_info, git_info, *, as_of
):
    """All externally dependent admission gates remain blocked."""
    at = _utc(as_of)
    blockers = set()

    if not policy_info["current_code_byte_exact"]:
        blockers.add("BOUND_CLOCK_IMPLEMENTATION_NOT_CURRENT")

    if not policy_info["epoch_active_at_inspection"]:
        blockers.add("EPOCH_NOT_ACTIVE_AT_INSPECTION")

    if probe_info["status"] != "LOCAL_PROBE_LINKED_UNATTESTED":
        blockers.add("ORIGINAL_PROBE_LOCAL_CONTRACT_NOT_ESTABLISHED")

    if git_info["status"] != "LOCAL_BOUND_BLOB_FOUND_UNATTESTED":
        blockers.add("BOUND_HISTORICAL_GIT_BLOB_NOT_ESTABLISHED")

    blockers.update({
        "INDEPENDENT_BROKER_IDENTITY_NOT_ATTESTED",
        "INDEPENDENT_BROKER_CLOCK_OFFSET_NOT_ATTESTED",
        "ORIGINAL_RECEIPT_TIMESTAMPS_NOT_INDEPENDENTLY_ATTESTED",
    })

    report = {
        "assessment_version": VERSION,
        "epoch_id": policy_info["epoch_id"],
        "as_of": at.isoformat(),
        "epoch_policy": policy_info,
        "original_probe": probe_info,
        "historical_git_code": git_info,
        "local_artifact_integrity": (
            "POLICY_PROBE_AND_GIT_LOCALLY_LINKED"
            if (
                probe_info["status"] == "LOCAL_PROBE_LINKED_UNATTESTED"
                and git_info["status"] ==
                    "LOCAL_BOUND_BLOB_FOUND_UNATTESTED"
            )
            else "LOCAL_LINKAGE_INCOMPLETE"
        ),
        "external_broker_identity_attested": False,
        "external_broker_clock_offset_attested": False,
        "original_broker_probe_independently_attested": False,
        "historical_utc_certified": False,
        "clock_policy_rebound": False,
        "historical_code_restored": False,
        "collector_started": False,
        "database_modified": False,
        "files_created_by_inspector": False,
        "real_forward_council_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "next_evidence_actions": [
            "OBTAIN_BROKER_OR_TERMINAL_ORIGIN_DOCUMENT_WITH_SERVER_IDENTITY_AND_TIME_REFERENCE",
            "VERIFY_PROBE_RECEIPT_ORIGIN_AND_CLOCK_AGAINST_INDEPENDENT_UTC_SOURCE",
            "DOCUMENT_EXACT_HISTORICAL_CLOCK_SOURCE_WITH_REVIEWED_GIT_COMMIT",
            "DESIGN_SEPARATE_NEW_EPOCH_PLAN_IF_CURRENT_CLOCK_CODE_DIFFERS",
            "DO_NOT_RESTART_RECORDER_OR_REBIND_ORIGINAL_EPOCH",
        ],
        "blockers": sorted(blockers),
        "final_action": "HOLD",
        "read_only": True,
    }

    report["report_sha256"] = hashed(report)
    return report


def inspect_stage4_broker_clock(
    engine,
    *,
    epoch_id,
    as_of,
    policy_file,
    expected_policy_sha256,
    probe_file=None,
    candidate_commit=None,
    repo=".",
):
    policy, local = inspect_epoch_policy(
        engine,
        epoch_id=epoch_id,
        policy_file=policy_file,
        expected_policy_sha256=expected_policy_sha256,
        as_of=as_of,
    )

    probe = inspect_original_probe(
        policy, probe_file
    )

    git = inspect_historical_clock_blob(
        repo,
        ref=candidate_commit,
        bound_sha256=policy.implementation_sha256,
    )

    return assess_clock_gate(
        local, probe, git, as_of=as_of
    )


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--read-only-db",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--git-read-only",
        action="store_true",
        required=True,
    )
    parser.add_argument(
        "--read-only-evidence",
        action="store_true",
        required=True,
    )
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", required=True)
    parser.add_argument(
        "--expected-policy-sha256",
        required=True,
    )
    parser.add_argument("--probe-file")
    parser.add_argument("--candidate-commit")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    try:
        cutoff = _parse_utc(args.as_of)

        if cutoff > datetime.now(UTC):
            raise ClockEvidenceReviewError(
                "Inspection cutoff is in the future."
            )

        if (
            not SHA.fullmatch(args.epoch_id)
            or not SHA.fullmatch(args.expected_policy_sha256)
        ):
            raise ClockEvidenceReviewError(
                "Invalid epoch/file digest."
            )

        if (
            args.candidate_commit is not None
            and not COMMIT.fullmatch(args.candidate_commit)
        ):
            raise ClockEvidenceReviewError(
                "Invalid Git candidate commit."
            )

        from backend.database.connection import engine

        try:
            report = inspect_stage4_broker_clock(
                engine,
                epoch_id=args.epoch_id,
                as_of=cutoff,
                policy_file=args.policy_file,
                expected_policy_sha256=args.expected_policy_sha256,
                probe_file=args.probe_file,
                candidate_commit=args.candidate_commit,
                repo=Path.cwd(),
            )
        finally:
            engine.dispose()

    except Exception as exc:
        print(
            "STAGE 4 BROKER CLOCK REVIEW BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(json.dumps(
            report,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ))
    else:
        policy = report["epoch_policy"]
        probe = report["original_probe"]
        git = report["historical_git_code"]

        print(
            "EURUSD ORACLE | Stage 4 S4-05..08 broker clock evidence"
        )
        print(
            "Epoch: " + report["epoch_id"]
            + " | cutoff=" + report["as_of"]
        )
        print(
            "Local epoch/policy: CONSISTENT "
            "(NOT INDEPENDENTLY ATTESTED)"
        )
        print(
            "Current bound-code exact match: "
            + str(policy["current_code_byte_exact"])
        )
        print("Original probe: " + probe["status"])
        print("Historical Git candidate: " + git["status"])
        print(
            "Historical Git AST matches current: "
            + str(git["candidate_ast_matches_current"])
        )
        print(
            "Broker identity and offset: NOT INDEPENDENTLY ATTESTED"
        )

        for item in report["blockers"]:
            print("  BLOCKER " + item)

        print("SHA-256: " + report["report_sha256"])
        print(
            "READ-ONLY | NO BROKER CONNECTION | "
            "NO REBIND | NO COUNCIL | FINAL=HOLD"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
