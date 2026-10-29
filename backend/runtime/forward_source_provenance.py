
"""Phase 3 P3-09..P3-12: bounded, read-only forward source provenance.

Inspects an already recorded epoch, an operator-selected policy file, and
first-owned capture/tick/bar evidence. Matching local hashes are NOT broker
attestation and this program never authorizes recorder restart or trading.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from sqlalchemy import select, text

from backend.data_engine.clock_policy import (
    ClockPolicy, aware_utc, decode_json, implementation_sha256,
)
from backend.data_engine.forward_store import (
    bars, captures, encoded, epochs, hashed, schema, ticks,
)

VERSION = "EURUSD_P3_FORWARD_SOURCE_PROVENANCE_V1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class SourceProvenanceError(RuntimeError):
    """The read-only source review is incomplete or inconsistent."""


def _bound(value, minimum, maximum, label):
    if type(value) is not int or not minimum <= value <= maximum:
        raise SourceProvenanceError(
            f"{label} must be an integer in [{minimum},{maximum}]."
        )
    return value


def _utc(value):
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise SourceProvenanceError("Explicit UTC datetime required.")
    return aware_utc(value)


def _file_bytes(path, *, limit=16384):
    with Path(path).open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File exceeds inspection bound.")
    return data


def _policy_copy(stored, path, expected_sha256=None):
    """Compare an existing local policy copy with the DB epoch; never export."""
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str)
        or HEX64.fullmatch(expected_sha256) is None
    ):
        raise SourceProvenanceError(
            "Expected policy file SHA-256 is invalid."
        )
    result = {
        "status": "NOT_PROVIDED",
        "file_sha256": None,
        "canonical_epoch_match": False,
        "clock_code_matches_worktree": False,
        "observed_digest_matches_expected": None,
        "original_creation_path_attested": False,
    }
    if path is None:
        return result
    try:
        payload = _file_bytes(path)
        other = ClockPolicy.model_validate(
            decode_json(payload, limit=16384)
        )
    except (OSError, ValueError, TypeError, RuntimeError):
        result["status"] = "UNAVAILABLE_OR_INVALID"
        return result

    same = (
        encoded(other.model_dump(mode="json"))
        == encoded(stored.model_dump(mode="json"))
    )
    file_hash = sha256(payload).hexdigest()

    result.update({
        "status": (
            "LOCAL_CANONICAL_COPY_MATCH_UNATTESTED"
            if same
            else "LOCAL_POLICY_DIFFERS_FROM_EPOCH"
        ),
        "file_sha256": file_hash,
        "observed_digest_matches_expected": (
            file_hash == expected_sha256
            if expected_sha256 is not None
            else None
        ),
        "canonical_epoch_match": (
            same and other.epoch_id == stored.epoch_id
        ),
        "clock_code_matches_worktree": (
            other.implementation_sha256 == implementation_sha256()
        ),
    })
    return result


def _identity_candidate(stored, path):
    """Inspect only company/server claims; never report identifying raw text."""
    result = {
        "status": "NOT_PROVIDED",
        "file_sha256": None,
        "company_hash_matches_epoch": None,
        "server_hash_matches_epoch": None,
        "independently_signed_or_attested": False,
    }
    if path is None:
        return result
    try:
        data = _file_bytes(path)
        item = decode_json(data, limit=16384)
        if set(item) != {"company", "server"} or any(
            not isinstance(item[key], str)
            or not 1 <= len(item[key]) <= 256
            for key in ("company", "server")
        ):
            raise ValueError("Identity candidate has invalid fields.")
    except (OSError, ValueError, TypeError, RuntimeError):
        result["status"] = "UNAVAILABLE_OR_INVALID"
        return result

    company = (
        sha256(item["company"].encode("utf-8")).hexdigest()
        == stored.company_sha256
    )
    server = (
        sha256(item["server"].encode("utf-8")).hexdigest()
        == stored.server_sha256
    )
    result.update({
        "status": (
            "LOCAL_IDENTITY_HASH_MATCH_UNATTESTED"
            if company and server
            else "LOCAL_IDENTITY_HASH_MISMATCH"
        ),
        "file_sha256": sha256(data).hexdigest(),
        "company_hash_matches_epoch": company,
        "server_hash_matches_epoch": server,
    })
    return result


def _capture_issue(row, policy, cutoff):
    issue = set()
    try:
        manifest = json.loads(row["manifest_json"])
        if (
            not isinstance(manifest, dict)
            or hashed(manifest) != row["capture_id"]
        ):
            return ["CAPTURE_MANIFEST_DIGEST_INVALID"]

        if (
            manifest.get("schema_version") != 1
            or manifest.get("epoch_id") != policy.epoch_id
            or any(
                manifest.get(key) != row[key].isoformat()
                for key in ("received_at", "tick_start", "tick_end")
            )
        ):
            issue.add("CAPTURE_MANIFEST_METADATA_INVALID")

        start = datetime.fromisoformat(manifest["bar_start"])
        end = datetime.fromisoformat(manifest["bar_end"])
        if not (
            policy.valid_from
            <= start
            <= end
            <= row["received_at"]
        ):
            issue.add("CAPTURE_BAR_REQUEST_INVALID")

        for prefix in ("tick", "bar"):
            count = manifest.get(prefix + "_count")
            if type(count) is not int or count < 0:
                issue.add("CAPTURE_MANIFEST_COUNT_INVALID")

            digest = manifest.get(prefix + "s_sha256")
            if (
                not isinstance(digest, str)
                or HEX64.fullmatch(digest) is None
            ):
                issue.add("CAPTURE_MANIFEST_SOURCE_HASH_INVALID")

    except (KeyError, TypeError, ValueError, OverflowError):
        return ["CAPTURE_MANIFEST_UNREADABLE"]

    if not (
        policy.valid_from
        <= row["tick_start"]
        < row["tick_end"]
        <= row["received_at"]
        <= row["ingested_at"]
        <= cutoff
    ):
        issue.add("CAPTURE_LOCAL_CHRONOLOGY_INVALID")

    return sorted(issue)


def _source_issue(row, policy, kind, cutoff):
    issue = set()
    try:
        raw = json.loads(row["raw_json"])
        if (
            not isinstance(raw, dict)
            or encoded(raw) != row["raw_json"]
        ):
            issue.add("RAW_RECORD_JSON_INVALID")
            return sorted(issue)

        if not (
            policy.valid_from
            <= row["timestamp"]
            <= row["received_at"]
            <= row["ingested_at"]
            <= cutoff
        ):
            issue.add("SOURCE_LOCAL_CHRONOLOGY_INVALID")

        if (
            row["received_at"] != row["capture_received_at"]
            or row["ingested_at"] != row["capture_ingested_at"]
        ):
            issue.add("SOURCE_FIRST_CAPTURE_RECEIPT_MISMATCH")

        if kind == "tick":
            if not (
                row["capture_tick_start"]
                <= row["timestamp"]
                < row["capture_tick_end"]
            ):
                issue.add("TICK_FIRST_CAPTURE_INTERVAL_INVALID")

            if (
                raw["time_msc"] != row["raw_time_msc"]
                or hashed({
                    "epoch_id": policy.epoch_id,
                    "raw": raw,
                }) != row["fingerprint"]
                or float(raw["bid"]) != row["bid"]
                or float(raw["ask"]) != row["ask"]
                or not (0 < row["bid"] <= row["ask"])
            ):
                issue.add("TICK_RAW_PROVENANCE_MISMATCH")

        else:
            manifest = json.loads(row["capture_manifest_json"])
            start = datetime.fromisoformat(manifest["bar_start"])
            end = datetime.fromisoformat(manifest["bar_end"])

            if not (
                start <= row["timestamp"] < end
                and row["timestamp"] + timedelta(minutes=5)
                <= row["received_at"]
            ):
                issue.add("BAR_FIRST_CAPTURE_INTERVAL_INVALID")

            if (
                raw["time"] != row["raw_open_time"]
                or any(
                    float(raw[key]) != row[key]
                    for key in ("open", "high", "low", "close")
                )
            ):
                issue.add("BAR_RAW_PROVENANCE_MISMATCH")

    except (KeyError, ValueError, TypeError, OverflowError):
        issue.add("SOURCE_RAW_RECORD_UNREADABLE")

    return sorted(issue)


def _select_sources(connection, table, epoch_id, cutoff, limit):
    key = (
        table.c.fingerprint
        if table is ticks
        else table.c.bar_id
    )

    query = (
        select(
            table,
            captures.c.received_at.label("capture_received_at"),
            captures.c.ingested_at.label("capture_ingested_at"),
            captures.c.tick_start.label("capture_tick_start"),
            captures.c.tick_end.label("capture_tick_end"),
            captures.c.manifest_json.label("capture_manifest_json"),
        )
        .join(
            captures,
            table.c.first_capture_id == captures.c.capture_id,
        )
        .where(
            table.c.epoch_id == epoch_id,
            captures.c.epoch_id == epoch_id,
            table.c.timestamp <= cutoff,
            table.c.received_at <= cutoff,
            table.c.ingested_at <= cutoff,
            captures.c.received_at <= cutoff,
            captures.c.ingested_at <= cutoff,
        )
        .order_by(
            table.c.timestamp.desc(),
            key,
        )
        .limit(limit + 1)
    )

    return connection.execute(query).mappings().all()


def inspect_forward_source_provenance(
    engine,
    *,
    epoch_id,
    as_of,
    policy_file=None,
    identity_file=None,
    expected_policy_sha256=None,
    max_captures=32,
    max_rows=500,
):
    """One read-only transaction; local integrity is not broker certification."""
    if not isinstance(epoch_id, str) or HEX64.fullmatch(epoch_id) is None:
        raise SourceProvenanceError("Lowercase SHA-256 epoch ID required.")

    cutoff = _utc(as_of)
    if cutoff > datetime.now(timezone.utc):
        raise SourceProvenanceError("Future cutoff forbidden.")

    _bound(max_captures, 1, 100, "max_captures")
    _bound(max_rows, 1, 5000, "max_rows")

    if engine.dialect.name not in ("postgresql", "sqlite"):
        raise SourceProvenanceError("Unsupported database dialect.")

    with engine.connect() as connection, connection.begin():
        if engine.dialect.name == "postgresql":
            connection.execute(text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
            ))
            connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
            connection.execute(text(
                "SET LOCAL statement_timeout = '15s'"
            ))

        if connection.execute(
            select(schema.c.version)
        ).scalars().all() != [1]:
            raise SourceProvenanceError(
                "Unsupported forward-feed schema."
            )

        record = connection.execute(
            select(epochs)
            .where(
                epochs.c.epoch_id == epoch_id,
                epochs.c.recorded_at <= cutoff,
            )
        ).mappings().first()

        if record is None:
            raise SourceProvenanceError(
                "Epoch not visible at cutoff."
            )

        try:
            stored = ClockPolicy.model_validate_json(
                record["policy_json"]
            )
        except (TypeError, ValueError) as exc:
            raise SourceProvenanceError(
                "Stored policy JSON invalid."
            ) from exc

        if (
            stored.epoch_id != epoch_id
            or record["valid_from"] != stored.valid_from
            or record["valid_until"] != stored.valid_until
            or record["policy_json"] != encoded(
                stored.model_dump(mode="json")
            )
        ):
            raise SourceProvenanceError(
                "Stored policy metadata mismatch."
            )

        cap_rows = connection.execute(
            select(captures)
            .where(
                captures.c.epoch_id == epoch_id,
                captures.c.received_at <= cutoff,
                captures.c.ingested_at <= cutoff,
                captures.c.tick_end <= cutoff,
            )
            .order_by(
                captures.c.ingested_at.desc(),
                captures.c.capture_id,
            )
            .limit(max_captures + 1)
        ).mappings().all()

        tick_rows = _select_sources(
            connection, ticks, epoch_id, cutoff, max_rows
        )
        bar_rows = _select_sources(
            connection, bars, epoch_id, cutoff, max_rows
        )

    cap_sample = cap_rows[:max_captures]
    tick_sample = tick_rows[:max_rows]
    bar_sample = bar_rows[:max_rows]
    problems = set()

    for row in cap_sample:
        problems.update(_capture_issue(row, stored, cutoff))

    for row in tick_sample:
        problems.update(
            _source_issue(row, stored, "tick", cutoff)
        )

    for row in bar_sample:
        problems.update(
            _source_issue(row, stored, "bar", cutoff)
        )

    truncated = {
        "captures": len(cap_rows) > max_captures,
        "ticks": len(tick_rows) > max_rows,
        "bars": len(bar_rows) > max_rows,
    }

    if any(truncated.values()):
        problems.add("BOUNDED_EVIDENCE_SAMPLE_INCOMPLETE")

    if not cap_sample:
        problems.add("NO_VISIBLE_CAPTURE")
    if not tick_sample:
        problems.add("NO_VISIBLE_TICK")
    if not bar_sample:
        problems.add("NO_VISIBLE_BAR")

    last_ingested = max(
        (row["ingested_at"] for row in cap_sample),
        default=None,
    )

    if (
        last_ingested is None
        or cutoff - last_ingested > timedelta(minutes=15)
    ):
        problems.add("CAPTURE_NOT_CURRENT_AT_CUTOFF")

    if stored.implementation_sha256 != implementation_sha256():
        problems.add("BOUND_CLOCK_IMPLEMENTATION_MISMATCH")

    if not stored.valid_from <= cutoff < stored.valid_until:
        problems.add("EPOCH_NOT_ACTIVE_AT_CUTOFF")

    policy_result = _policy_copy(
        stored, policy_file, expected_policy_sha256
    )
    identity_result = _identity_candidate(
        stored, identity_file
    )

    if not policy_result["canonical_epoch_match"]:
        problems.add(
            "EXACT_STORED_POLICY_COPY_NOT_ESTABLISHED"
        )

    if policy_result["observed_digest_matches_expected"] is False:
        problems.add(
            "POLICY_FILE_CHANGED_FROM_PREVIOUSLY_OBSERVED_SHA256"
        )

    if identity_result["status"] == "LOCAL_IDENTITY_HASH_MISMATCH":
        problems.add(
            "IDENTITY_CANDIDATE_DIFFERS_FROM_POLICY"
        )

    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id,
        "as_of": cutoff.isoformat(),
        "stored_policy_canonical_sha256": sha256(
            record["policy_json"].encode("utf-8")
        ).hexdigest(),
        "epoch_bound_clock_sha256":
            stored.implementation_sha256,
        "current_clock_sha256":
            implementation_sha256(),
        "broker_identity_fields": {
            "company_hash_present": bool(stored.company_sha256),
            "server_hash_present": bool(stored.server_sha256),
            "evidence_hash_present": bool(stored.evidence_sha256),
            "independent_broker_identity_attested": False,
            "independent_clock_offset_attested": False,
        },
        "policy_file": policy_result,
        "optional_identity_candidate": identity_result,
        "capture_sample": {
            "captures_examined": len(cap_sample),
            "ticks_examined": len(tick_sample),
            "bars_examined": len(bar_sample),
            "sample_truncated": truncated,
            "last_capture_ingested_at": (
                last_ingested.isoformat()
                if last_ingested else None
            ),
            "local_manifest_checks_performed": len(cap_sample),
            "raw_rows_checked_without_clock_reinterpretation":
                len(tick_sample) + len(bar_sample),
            "original_ordered_api_batches_reconstructable": False,
            "independent_first_receipt_attested": False,
        },
        "local_structural_blockers": sorted(problems),
        "external_certification_gaps": [
            "NO_INDEPENDENT_BROKER_SERVER_IDENTITY_ATTESTATION",
            "NO_INDEPENDENT_BROKER_CLOCK_OFFSET_ATTESTATION",
            "NO_EXTERNAL_SOURCE_RECEIPT_SIGNATURES_OR_TIMESTAMPS",
            "NO_ORIGINAL_ORDERED_API_BATCH_BYTES",
            "NO_REAL_COUNCIL_INPUT_CAUSAL_CERTIFICATION",
        ],
        "historic_collector_stoppage_cause": "UNKNOWN",
        "read_only": True,
        "database_modified": False,
        "policy_modified": False,
        "collector_started": False,
        "real_data_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }

    result["report_sha256"] = hashed(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only-db",
        action="store_true",
        required=True,
    )
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file")
    parser.add_argument("--identity-file")
    parser.add_argument("--expected-policy-sha256")
    parser.add_argument("--captures", type=int, default=32)
    parser.add_argument("--source-rows", type=int, default=500)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if not HEX64.fullmatch(args.epoch_id):
            raise SourceProvenanceError("Invalid epoch ID.")

        cutoff = _utc(
            datetime.fromisoformat(
                args.as_of.replace("Z", "+00:00")
            )
        )

        _bound(args.captures, 1, 100, "captures")
        _bound(args.source_rows, 1, 5000, "source_rows")

        from backend.database.connection import engine

        try:
            report = inspect_forward_source_provenance(
                engine,
                epoch_id=args.epoch_id,
                as_of=cutoff,
                policy_file=args.policy_file,
                identity_file=args.identity_file,
                expected_policy_sha256=args.expected_policy_sha256,
                max_captures=args.captures,
                max_rows=args.source_rows,
            )
        finally:
            engine.dispose()

    except Exception as exc:
        print(
            "FORWARD SOURCE PROVENANCE BLOCKED: "
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
        print(
            "EURUSD ORACLE | P3-09..12 forward provenance inspection"
        )
        print(
            "Epoch: " + report["epoch_id"]
            + " | cutoff=" + report["as_of"]
        )
        print(
            "Policy copy: "
            + report["policy_file"]["status"]
        )
        print(
            "Identity candidate: "
            + report["optional_identity_candidate"]["status"]
        )

        counts = report["capture_sample"]
        print(
            "Inspected: %d captures, %d ticks, %d bars" % (
                counts["captures_examined"],
                counts["ticks_examined"],
                counts["bars_examined"],
            )
        )

        for code in report["local_structural_blockers"]:
            print("  LOCAL BLOCKER " + code)

        print(
            "Independent broker/receipt attestation: NOT ESTABLISHED"
        )
        print("SHA-256: " + report["report_sha256"])
        print(
            "SELECT-ONLY | NO RESTART | NO COUNCIL | FINAL=HOLD"
        )
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
