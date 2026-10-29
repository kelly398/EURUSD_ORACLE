
"""Stage 4 S4-09..12: bounded, read-only forward receipt/recovery review.

Capture manifests are self-hashed but do not retain their original source lists.
Rows are deduplicated and record only the first owning capture. Consequently,
manifest tick/bar payload digests CANNOT be independently recomputed here.
Local database integrity does not authenticate broker receipts or allow a restart.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import re

from sqlalchemy import select

from backend.data_engine.clock_policy import ClockPolicy, ClockPolicyError, decode_json, implementation_sha256, instant
from backend.data_engine.forward_store import epochs, captures, ticks, bars, hashed

VERSION = "EURUSD_STAGE4_FORWARD_RECEIPT_RECOVERY_S4_09_12_V1"
SHA = re.compile(r"^[0-9a-f]{64}$")
MAX_POLICY_BYTES = 16384
MAX_MANIFEST_BYTES = 8192
MAX_RAW_BYTES = 8192
MANIFEST_KEYS = frozenset({
    "schema_version", "epoch_id", "received_at", "tick_start", "tick_end",
    "bar_start", "bar_end", "ticks_sha256", "bars_sha256", "tick_count", "bar_count",
})


class ForwardReceiptReviewError(RuntimeError):
    """Inspected local evidence violates the bounded capture contract."""


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ForwardReceiptReviewError("Explicit UTC datetime required.")
    return value.astimezone(timezone.utc)


def _stamp(value):
    if not isinstance(value, str):
        raise ForwardReceiptReviewError("UTC timestamp text required.")
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except (ValueError, OverflowError, TypeError):
        raise ForwardReceiptReviewError("Invalid UTC timestamp.") from None


def _sha(value):
    return isinstance(value, str) and SHA.fullmatch(value) is not None


def _decoded(raw, limit):
    if not isinstance(raw, str):
        raise ForwardReceiptReviewError("Expected stored JSON text.")
    data = raw.encode("utf-8")
    try:
        return decode_json(data, limit=limit)
    except (ClockPolicyError, ValueError, UnicodeError, RecursionError):
        raise ForwardReceiptReviewError("Stored JSON evidence invalid.") from None


def _policy(engine, epoch_id, as_of, policy_file, expected_policy_sha256):
    if not _sha(epoch_id) or not _sha(expected_policy_sha256):
        raise ForwardReceiptReviewError("Invalid epoch or policy SHA-256.")
    path = Path(policy_file)
    if path.is_symlink():
        raise ForwardReceiptReviewError("Symlinked policy evidence refused.")
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_POLICY_BYTES + 1)
    except OSError:
        raise ForwardReceiptReviewError("Policy file not readable.") from None
    if len(raw) > MAX_POLICY_BYTES or sha256(raw).hexdigest() != expected_policy_sha256:
        raise ForwardReceiptReviewError("Local policy file SHA-256 differs.")
    try:
        local_policy = ClockPolicy.model_validate(decode_json(raw, limit=MAX_POLICY_BYTES))
    except (ValueError, ClockPolicyError):
        raise ForwardReceiptReviewError("Local policy format invalid.") from None
    with engine.connect() as connection:
        record = connection.execute(
            select(epochs).where(epochs.c.epoch_id == epoch_id)
        ).mappings().one_or_none()
    if record is None:
        raise ForwardReceiptReviewError("Stored epoch not found.")
    try:
        stored_policy = ClockPolicy.model_validate(_decoded(record["policy_json"], MAX_POLICY_BYTES))
    except (ValueError, ClockPolicyError):
        raise ForwardReceiptReviewError("Stored policy invalid.") from None
    if (
        stored_policy.epoch_id != epoch_id
        or local_policy.model_dump(mode="json") != stored_policy.model_dump(mode="json")
        or _utc(record["valid_from"]) != stored_policy.valid_from
        or _utc(record["valid_until"]) != stored_policy.valid_until
        or _utc(record["recorded_at"]) > as_of
        or local_policy.historical_admission is not False
    ):
        raise ForwardReceiptReviewError("Epoch/policy evidence does not agree.")
    return stored_policy


def _limited(connection, table, epoch_id, as_of, limit, order):
    rows = connection.execute(
        select(table).where(
            table.c.epoch_id == epoch_id,
            table.c.ingested_at <= as_of,
        ).order_by(*order).limit(limit + 1)
    ).mappings().all()
    return rows[:limit], len(rows) > limit


def _verify_capture(row, policy, as_of):
    manifest = _decoded(row["manifest_json"], MAX_MANIFEST_BYTES)
    if set(manifest) != MANIFEST_KEYS or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ForwardReceiptReviewError("Capture manifest schema invalid.")
    if not _sha(row["capture_id"]) or hashed(manifest) != row["capture_id"]:
        raise ForwardReceiptReviewError("Capture manifest integrity mismatch.")
    if manifest["epoch_id"] != policy.epoch_id:
        raise ForwardReceiptReviewError("Capture epoch differs from policy.")
    if not all(_sha(manifest[k]) for k in ("ticks_sha256", "bars_sha256")):
        raise ForwardReceiptReviewError("Malformed capture source-list SHA-256.")
    if any(type(manifest[k]) is not int or manifest[k] < 0 for k in ("tick_count", "bar_count")):
        raise ForwardReceiptReviewError("Malformed capture source counts.")
    received, ingested = _utc(row["received_at"]), _utc(row["ingested_at"])
    start, end = _utc(row["tick_start"]), _utc(row["tick_end"])
    bar_start, bar_end = _stamp(manifest["bar_start"]), _stamp(manifest["bar_end"])
    if (
        _stamp(manifest["received_at"]) != received
        or _stamp(manifest["tick_start"]) != start
        or _stamp(manifest["tick_end"]) != end
        or not (policy.valid_from <= start < end <= policy.valid_until)
        or not (policy.valid_from <= bar_start <= bar_end <= policy.valid_until)
        or not (end <= received <= ingested <= as_of)
        or bar_end > received
    ):
        raise ForwardReceiptReviewError("Capture interval/receipt chronology invalid.")
    return {"capture_id": row["capture_id"], "start": start, "end": end,
            "received": received, "ingested": ingested,
            "declared_ticks": manifest["tick_count"],
            "declared_bars": manifest["bar_count"]}


def _verify_first_owned(row, owner, policy, as_of, kind):
    raw = _decoded(row["raw_json"], MAX_RAW_BYTES)
    if row["epoch_id"] != policy.epoch_id:
        raise ForwardReceiptReviewError("Source-row epoch differs.")
    if (
        _utc(row["received_at"]) != owner["received"]
        or _utc(row["ingested_at"]) != owner["ingested"]
        or not owner["ingested"] <= as_of
    ):
        raise ForwardReceiptReviewError("First-owned source receipt does not match capture.")
    try:
        if kind == "tick":
            derived = policy.tick(raw)
            if (
                row["fingerprint"] != hashed({"epoch_id": policy.epoch_id, "raw": derived["source_record"]})
                or _utc(row["timestamp"]) != instant(derived["time_msc"])
                or row["raw_time_msc"] != raw["time_msc"]
                or (row["bid"], row["ask"]) != (derived["bid"], derived["ask"])
                or not owner["start"] <= _utc(row["timestamp"]) < owner["end"]
                or _utc(row["timestamp"]) > owner["received"]
            ):
                raise ForwardReceiptReviewError("Tick first-owned record inconsistent.")
        else:
            derived = policy.rate(raw)
            bar_time = _utc(row["timestamp"])
            if (
                row["bar_id"] != hashed({"epoch_id": policy.epoch_id, "time": derived["time"]})
                or bar_time != instant(derived["time"] * 1000)
                or row["raw_open_time"] != raw["time"]
                or any(row[k] != derived[k] for k in ("open", "high", "low", "close"))
                or not bar_time + timedelta(minutes=5) <= owner["received"]
            ):
                raise ForwardReceiptReviewError("Bar first-owned record inconsistent.")
    except (ClockPolicyError, ValueError, TypeError, KeyError, OverflowError):
        raise ForwardReceiptReviewError("Source normalization check failed.") from None


def inspect_stage4_forward_receipts(
    engine, *, epoch_id, as_of, policy_file, expected_policy_sha256,
    max_captures=100, max_source_rows=5000,
):
    """Read only SELECTs. Local structure is assessed; external gates stay HOLD."""
    at = _utc(as_of)
    if type(max_captures) is not int or not 1 <= max_captures <= 1000:
        raise ForwardReceiptReviewError("Capture bound must be 1..1000.")
    if type(max_source_rows) is not int or not 1 <= max_source_rows <= 10000:
        raise ForwardReceiptReviewError("Source-row bound must be 1..10000.")
    policy = _policy(engine, epoch_id, at, policy_file, expected_policy_sha256)

    with engine.connect() as connection:
        capture_rows, capture_truncated = _limited(
            connection, captures, epoch_id, at, max_captures,
            (captures.c.ingested_at, captures.c.capture_id),
        )
        tick_rows, ticks_truncated = _limited(
            connection, ticks, epoch_id, at, max_source_rows,
            (ticks.c.ingested_at, ticks.c.fingerprint),
        )
        bar_rows, bars_truncated = _limited(
            connection, bars, epoch_id, at, max_source_rows,
            (bars.c.ingested_at, bars.c.bar_id),
        )

    verified = [_verify_capture(row, policy, at) for row in capture_rows]
    owners = {row["capture_id"]: row for row in verified}
    if len(owners) != len(verified):
        raise ForwardReceiptReviewError("Duplicate capture ID in scope.")

    outside = {"ticks": 0, "bars": 0}
    for kind, rows in (("tick", tick_rows), ("bar", bar_rows)):
        for row in rows:
            owner = owners.get(row["first_capture_id"])
            if owner is None:
                outside[kind + "s"] += 1
                continue
            _verify_first_owned(row, owner, policy, at, kind)

    by_interval = sorted(verified, key=lambda r: (r["end"], r["start"], r["capture_id"]))
    gaps = []
    cursor = None
    for row in by_interval:
        if cursor is not None and row["start"] > cursor:
            gaps.append({"from": cursor.isoformat(), "to": row["start"].isoformat()})
        cursor = max(cursor, row["end"]) if cursor is not None else row["end"]

    last_ingested = max((r["ingested"] for r in verified), default=None)
    last_end = max((r["end"] for r in verified), default=None)
    sample_truncated = {
        "captures": capture_truncated,
        "ticks": ticks_truncated,
        "bars": bars_truncated,
    }
    blockers = set()
    if any(sample_truncated.values()):
        blockers.add("BOUNDED_INSPECTION_TRUNCATED")
    if not verified:
        blockers.add("NO_CAPTURES_IN_INSPECTED_SCOPE")
    if outside["ticks"] or outside["bars"]:
        blockers.add("FIRST_OWNING_CAPTURE_NOT_IN_INSPECTED_SCOPE")
    if gaps:
        blockers.add("UNQUERIED_TICK_INTERVALS_IN_INSPECTED_SCOPE")
    if policy.implementation_sha256 != implementation_sha256():
        blockers.add("BOUND_CLOCK_CODE_DIFFERS_FROM_CURRENT")
    if not policy.valid_from <= at < policy.valid_until:
        blockers.add("EPOCH_NOT_ACTIVE_AT_INSPECTION")
    if last_ingested is None or (at - last_ingested).total_seconds() > 900:
        blockers.add("NO_CURRENT_RECEIPT_IN_INSPECTED_SCOPE")
    blockers.update({
        "ORIGINAL_PER_CAPTURE_SOURCE_LISTS_UNAVAILABLE",
        "INDEPENDENT_BROKER_RECEIPT_ATTESTATION_MISSING",
        "OPERATOR_APPROVED_RECOVERY_PLAN_MISSING",
    })

    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id,
        "as_of": at.isoformat(),
        "policy_file_sha256": expected_policy_sha256,
        "local_policy_matches_stored_epoch": True,
        "bound_clock_code_matches_current": policy.implementation_sha256 == implementation_sha256(),
        "inspected_scope": {
            "captures": len(verified), "first_owned_ticks": len(tick_rows),
            "first_owned_bars": len(bar_rows), "truncated": sample_truncated,
        },
        "manifest_self_hashes_valid_for_inspected_captures": bool(verified),
        "first_owned_source_rows_locally_consistent_when_owner_in_scope": bool(tick_rows or bar_rows) and not any(outside.values()),
        "first_owned_rows_outside_capture_scope": outside,
        "local_tick_query_interval_gaps": gaps,
        "local_query_coverage_is_not_broker_completeness": True,
        "last_capture_ingested_at": last_ingested.isoformat() if last_ingested else None,
        "last_completed_tick_query_end": last_end.isoformat() if last_end else None,
        "original_per_capture_source_lists_available": False,
        "original_capture_tick_bar_sha256_recomputed": False,
        "deduplicated_rows_cannot_reconstruct_original_capture_payloads": True,
        "broker_receipt_time_independently_attested": False,
        "broker_source_identity_independently_attested": False,
        "broker_query_completeness_independently_attested": False,
        "local_evidence_only": True,
        "recovery_prerequisites": [
            "INDEPENDENT_BROKER_IDENTITY_CLOCK_AND_RECEIPT_ATTESTATION",
            "ORIGINAL_CAPTURE_PAYLOAD_OR_EXTERNAL_ORDERED_RECEIPT_EVIDENCE",
            "OPERATOR_REVIEW_OF_EPOCH_BOUND_CLOCK_CODE_DIFFERENCE",
            "SEPARATELY_APPROVED_NEW_SESSION_AND_EPOCH_PLAN",
            "DOCUMENTED_FIRST_RECEIPT_AND_GAP_RECONCILIATION",
            "EXPLICIT_PRE_EXECUTION_RISK_AND_COUNCIL_GATE_REVIEW",
        ],
        "blockers": sorted(blockers),
        "existing_epoch_rebound": False,
        "collector_restart_authorized": False,
        "new_epoch_created": False,
        "database_modified": False,
        "broker_connected": False,
        "council_invoked": False,
        "training_authorized": False,
        "execution_authorized": False,
        "read_only": True,
        "final_action": "HOLD",
    }
    result["report_sha256"] = hashed(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-db", action="store_true", required=True)
    parser.add_argument("--read-only-evidence", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--policy-file", required=True)
    parser.add_argument("--expected-policy-sha256", required=True)
    parser.add_argument("--captures", type=int, default=100)
    parser.add_argument("--source-rows", type=int, default=5000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        at = _stamp(args.as_of)
        if at > datetime.now(timezone.utc):
            raise ForwardReceiptReviewError("Future inspection cutoff refused.")
        if not _sha(args.epoch_id) or not _sha(args.expected_policy_sha256):
            raise ForwardReceiptReviewError("Invalid epoch or policy SHA-256.")
        from backend.database.connection import engine
        try:
            report = inspect_stage4_forward_receipts(
                engine, epoch_id=args.epoch_id, as_of=at,
                policy_file=args.policy_file,
                expected_policy_sha256=args.expected_policy_sha256,
                max_captures=args.captures,
                max_source_rows=args.source_rows,
            )
        finally:
            engine.dispose()
    except Exception as exc:
        print("STAGE 4 RECEIPT RECOVERY REVIEW BLOCKED: " + type(exc).__name__)
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    else:
        counts = report["inspected_scope"]
        print("EURUSD ORACLE | Stage 4 S4-09..12 receipt recovery review")
        print("Epoch: " + report["epoch_id"] + " | cutoff=" + report["as_of"])
        print("Captured manifests: " + str(counts["captures"]))
        print("First-owned ticks/bars: %s/%s" % (counts["first_owned_ticks"], counts["first_owned_bars"]))
        print("Source sample truncated: " + str(counts["truncated"]))
        print("Last stored ingestion: " + str(report["last_capture_ingested_at"]))
        print("Local interval gaps: " + str(len(report["local_tick_query_interval_gaps"])))
        print("Original capture payload hashes: NOT RECOMPUTABLE FROM DEDUPLICATED ROWS")
        print("Independent broker receipts: NOT ATTESTED")
        for reason in report["blockers"]:
            print("  BLOCKER " + reason)
        print("SHA-256: " + report["report_sha256"])
        print("READ-ONLY | NO RESTART | NO EPOCH REBIND | NO COUNCIL | FINAL=HOLD")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
