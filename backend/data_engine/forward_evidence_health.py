
"""Read-only, bounded inspection of locally recorded forward UTC evidence.

IMPORTANT: ForwardFeedStore intentionally allows repeated/overlapping requests
and stores deduplicated rows under their FIRST capture only. Those rows cannot
reconstruct every original, ordered capture payload or its manifest digest.

Neither local receipt times nor broker wall labels are independent attestation.
No schema creation, broker access, writes, real admission, training or trading.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re

from sqlalchemy import select, text

from backend.data_engine.clock_policy import (
    ClockPolicy, ClockPolicyError, aware_utc, implementation_sha256, instant,
)
from backend.data_engine.forward_store import (
    bars, captures, encoded, epochs, hashed, schema, ticks,
)

VERSION = "EURUSD_FORWARD_EVIDENCE_HEALTH_V2"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ForwardEvidenceError(RuntimeError):
    """Inspection cannot safely complete; no admission follows."""


def _require_epoch_id(value):
    if not isinstance(value, str) or not HEX64.fullmatch(value):
        raise ForwardEvidenceError(
            "Explicit lowercase 64-character epoch ID required."
        )
    return value


def _bounded(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ForwardEvidenceError(
            f"{label} must be an integer in [{low},{high}]."
        )
    return value


def _sha(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def _transaction(connection):
    if connection.dialect.name == "postgresql":
        connection.execute(text(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        ))
        connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        connection.execute(text("SET LOCAL statement_timeout = '15s'"))


def _dialect(engine):
    if engine.dialect.name not in ("postgresql", "sqlite"):
        raise ForwardEvidenceError(
            "Unsupported inspection database dialect."
        )


def _schema(connection):
    if connection.execute(
        select(schema.c.version)
    ).scalars().all() != [1]:
        raise ForwardEvidenceError(
            "Unsupported forward-feed schema version."
        )


def list_forward_epochs(engine, *, limit=20):
    _bounded(limit, 1, 100, "limit")
    _dialect(engine)

    with engine.connect() as connection, connection.begin():
        _transaction(connection)
        _schema(connection)

        found = connection.execute(
            select(
                epochs.c.epoch_id,
                epochs.c.valid_from,
                epochs.c.valid_until,
                epochs.c.recorded_at,
            )
            .order_by(
                epochs.c.recorded_at.desc(),
                epochs.c.epoch_id,
            )
            .limit(limit + 1)
        ).mappings().all()

    return {
        "epochs": [
            {
                key: value.isoformat()
                if isinstance(value, datetime) else value
                for key, value in item.items()
            }
            for item in found[:limit]
        ],
        "truncated": len(found) > limit,
        "read_only": True,
    }


def _capture_manifest(cap, policy, cutoff):
    issues = set()

    try:
        manifest = json.loads(cap["manifest_json"])

        if (
            not isinstance(manifest, dict)
            or hashed(manifest) != cap["capture_id"]
        ):
            return None, {"CAPTURE_MANIFEST_HASH_MISMATCH"}

        required = dict(
            schema_version=1,
            epoch_id=policy.epoch_id,
            received_at=cap["received_at"].isoformat(),
            tick_start=cap["tick_start"].isoformat(),
            tick_end=cap["tick_end"].isoformat(),
        )

        if any(
            manifest.get(key) != value
            for key, value in required.items()
        ):
            issues.add("CAPTURE_MANIFEST_FIELDS_MISMATCH")

        start = datetime.fromisoformat(manifest["bar_start"])
        end = datetime.fromisoformat(manifest["bar_end"])

        if not (
            policy.valid_from
            <= start
            <= end
            <= cap["received_at"]
        ):
            issues.add("BAR_REQUEST_INTERVAL_INVALID")

        if not (
            policy.valid_from
            <= cap["tick_start"]
            < cap["tick_end"]
            <= cap["received_at"]
            <= cap["ingested_at"]
            <= cutoff
        ):
            issues.add("CAPTURE_CHRONOLOGY_INVALID")

        if not (
            policy.valid_from
            <= cap["received_at"]
            < policy.valid_until
        ):
            issues.add("CAPTURE_OUTSIDE_EPOCH")

        for kind in ("tick", "bar"):
            count = manifest.get(kind + "_count")
            digest = manifest.get(kind + "s_sha256")

            if type(count) is not int or count < 0:
                issues.add("MANIFEST_COUNT_INVALID")

            if (
                not isinstance(digest, str)
                or not HEX64.fullmatch(digest)
            ):
                issues.add("MANIFEST_DIGEST_INVALID")

    except (
        ValueError,
        TypeError,
        KeyError,
        OverflowError,
        ClockPolicyError,
    ):
        return None, {"CAPTURE_MANIFEST_INVALID"}

    return manifest, issues


def _first_owned(connection, table, epoch_id, capture_id, cutoff, limit):
    """Read first-owned source rows without claiming full batch recovery."""
    key = (
        table.c.fingerprint
        if table is ticks
        else table.c.bar_id
    )

    return connection.execute(
        select(table)
        .where(
            table.c.epoch_id == epoch_id,
            table.c.first_capture_id == capture_id,
            table.c.received_at <= cutoff,
            table.c.ingested_at <= cutoff,
        )
        .order_by(table.c.timestamp, key)
        .limit(limit + 1)
    ).mappings().all()


def _source_integrity(records, policy, cap, kind, cutoff, manifest):
    errors = set()

    for row in records:
        try:
            raw = json.loads(row["raw_json"])

            item = (
                policy.tick(raw)
                if kind == "tick"
                else policy.rate(raw)
            )

            utc_time = instant(
                item["time_msc"]
                if kind == "tick"
                else item["time"] * 1000
            )

            if utc_time != row["timestamp"]:
                errors.add(
                    kind.upper() + "_UTC_TIMESTAMP_MISMATCH"
                )

            if not (
                utc_time
                <= row["received_at"]
                <= row["ingested_at"]
                <= cutoff
            ) or (
                row["received_at"] != cap["received_at"]
                or row["ingested_at"] != cap["ingested_at"]
            ):
                errors.add(
                    kind.upper() + "_RECEIPT_CHRONOLOGY_INVALID"
                )

            if kind == "tick":
                if not (
                    cap["tick_start"]
                    <= utc_time
                    < cap["tick_end"]
                ):
                    errors.add(
                        "TICK_OUTSIDE_FIRST_CAPTURE_WINDOW"
                    )

                if (
                    row["raw_time_msc"] != raw["time_msc"]
                    or row["fingerprint"] != hashed(dict(
                        epoch_id=policy.epoch_id,
                        raw=item["source_record"],
                    ))
                    or (row["bid"], row["ask"])
                    != (item["bid"], item["ask"])
                ):
                    errors.add("TICK_RAW_PROVENANCE_INVALID")

            else:
                if (
                    row["raw_open_time"] != raw["time"]
                    or row["bar_id"] != hashed(dict(
                        epoch_id=policy.epoch_id,
                        time=item["time"],
                    ))
                    or any(
                        row[key] != item[key]
                        for key in ("open", "high", "low", "close")
                    )
                ):
                    errors.add("BAR_RAW_PROVENANCE_INVALID")

                if (
                    utc_time.minute % 5
                    or utc_time.second
                    or utc_time.microsecond
                    or utc_time + timedelta(minutes=5)
                    > row["received_at"]
                ):
                    errors.add("BAR_NOT_CLOSED_AT_RECEIPT")

                if manifest is not None:
                    start = datetime.fromisoformat(
                        manifest["bar_start"]
                    )
                    end = datetime.fromisoformat(
                        manifest["bar_end"]
                    )

                    if not start <= utc_time < end:
                        errors.add(
                            "BAR_OUTSIDE_FIRST_CAPTURE_REQUEST"
                        )

        except (
            ValueError,
            TypeError,
            KeyError,
            OverflowError,
            ClockPolicyError,
        ):
            errors.add(
                kind.upper() + "_RAW_RECORD_INVALID"
            )

    return errors


def inspect_forward_evidence(
    engine,
    *,
    epoch_id,
    as_of,
    max_captures=10,
    max_source_rows=10000,
):
    """Inspect point-in-time local evidence without certifying its source."""
    epoch_id = _require_epoch_id(epoch_id)
    cutoff = aware_utc(as_of)

    if cutoff > datetime.now(timezone.utc):
        raise ForwardEvidenceError(
            "Future inspection cutoff not permitted."
        )

    _bounded(max_captures, 1, 32, "max_captures")
    _bounded(max_source_rows, 1, 50000, "max_source_rows")
    _dialect(engine)

    blockers = set()
    observations = set()
    limitations = set()
    summaries = []

    count_rows = 0
    latest_tick = None
    latest_bar = None

    last_query_end = None
    last_received = None
    overlap_count = 0
    gap_count = 0
    truncated = False

    with engine.connect() as connection, connection.begin():
        _transaction(connection)
        _schema(connection)

        record = connection.execute(
            select(epochs)
            .where(
                epochs.c.epoch_id == epoch_id,
                epochs.c.recorded_at <= cutoff,
            )
        ).mappings().first()

        if record is None:
            raise ForwardEvidenceError(
                "Epoch absent or not recorded by cutoff."
            )

        try:
            policy = ClockPolicy.model_validate_json(
                record["policy_json"]
            )
        except (ValueError, TypeError) as exc:
            raise ForwardEvidenceError(
                "Stored epoch policy invalid."
            ) from exc

        if (
            policy.epoch_id != epoch_id
            or record["valid_from"] != policy.valid_from
            or record["valid_until"] != policy.valid_until
            or record["policy_json"] != encoded(
                policy.model_dump(mode="json")
            )
        ):
            blockers.add("STORED_EPOCH_POLICY_MISMATCH")

        clock_matches = (
            policy.implementation_sha256
            == implementation_sha256()
        )

        if not clock_matches:
            blockers.add(
                "LOCAL_CLOCK_IMPLEMENTATION_UNMATCHED"
            )
            limitations.add(
                "STORED_CLOCK_TRANSFORM_CANNOT_BE_REPRODUCED_WITH_CURRENT_CODE"
            )

        if not (
            policy.valid_from
            <= cutoff
            < policy.valid_until
        ):
            blockers.add("EPOCH_NOT_ACTIVE_AT_CUTOFF")

        found = connection.execute(
            select(captures)
            .where(
                captures.c.epoch_id == epoch_id,
                captures.c.received_at <= cutoff,
                captures.c.ingested_at <= cutoff,
                captures.c.tick_end <= cutoff,
            )
            .order_by(
                captures.c.tick_end.desc(),
                captures.c.received_at.desc(),
                captures.c.capture_id,
            )
            .limit(max_captures + 1)
        ).mappings().all()

        truncated = len(found) > max_captures

        if truncated:
            blockers.add("CAPTURE_SAMPLE_TRUNCATED")
            limitations.add(
                "EARLIER_CAPTURE_HISTORY_NOT_EXAMINED"
            )

        for cap in reversed(found[:max_captures]):
            issues = set()

            manifest, errors = _capture_manifest(
                cap, policy, cutoff
            )
            issues.update(errors)

            if last_query_end is not None:
                if cap["tick_start"] > last_query_end:
                    gap_count += 1
                    issues.add(
                        "CAPTURE_UNQUERIED_TICK_GAP"
                    )

                elif cap["tick_start"] < last_query_end:
                    overlap_count += 1
                    observations.add(
                        "OVERLAPPING_QUERY_WINDOWS_ALLOWED_BY_STORE"
                    )

                if cap["received_at"] < last_received:
                    issues.add(
                        "CAPTURE_RECEIPT_REGRESSION"
                    )

            if last_query_end is None:
                last_query_end = cap["tick_end"]
            else:
                last_query_end = max(
                    last_query_end,
                    cap["tick_end"],
                )

            last_received = cap["received_at"]

            owned_ticks = []
            owned_bars = []

            for kind, table in (
                ("tick", ticks),
                ("bar", bars),
            ):
                remaining = max_source_rows - count_rows

                if remaining <= 0:
                    issues.add("SOURCE_SCAN_TRUNCATED")
                    truncated = True
                    continue

                rows = _first_owned(
                    connection,
                    table,
                    epoch_id,
                    cap["capture_id"],
                    cutoff,
                    remaining,
                )

                if len(rows) > remaining:
                    issues.add("SOURCE_SCAN_TRUNCATED")
                    truncated = True
                    continue

                count_rows += len(rows)

                if kind == "tick":
                    owned_ticks = rows
                else:
                    owned_bars = rows

                if manifest is not None:
                    count = manifest.get(
                        kind + "_count"
                    )

                    if type(count) is int:
                        if len(rows) > count:
                            issues.add(
                                kind.upper()
                                + "_OWNED_ROWS_EXCEED_CAPTURE_COUNT"
                            )

                        if len(rows) != count:
                            limitations.add(
                                "ORIGINAL_CAPTURE_PAYLOAD_NOT_RECONSTRUCTABLE"
                            )

                # Never reinterpret an old epoch using changed
                # clock-conversion code.
                if clock_matches:
                    issues.update(
                        _source_integrity(
                            rows,
                            policy,
                            cap,
                            kind,
                            cutoff,
                            manifest,
                        )
                    )
                else:
                    limitations.add(
                        "RAW_UTC_REPLAY_SKIPPED_CLOCK_VERSION_MISMATCH"
                    )

            issues = sorted(issues)
            blockers.update(issues)

            summaries.append({
                "capture_id": cap["capture_id"],
                "tick_start": cap["tick_start"].isoformat(),
                "tick_end": cap["tick_end"].isoformat(),
                "received_at": cap["received_at"].isoformat(),
                "ingested_at": cap["ingested_at"].isoformat(),
                "first_owned_ticks": len(owned_ticks),
                "first_owned_bars": len(owned_bars),
                "declared_tick_count": (
                    manifest.get("tick_count")
                    if manifest else None
                ),
                "declared_bar_count": (
                    manifest.get("bar_count")
                    if manifest else None
                ),
                "reason_codes": issues,
            })

        # A recent source row may belong to an older first capture.
        # Determine freshness from all PIT-visible source rows,
        # not only the bounded capture ownership sample.
        latest = {}

        for kind, table in (
            ("tick", ticks),
            ("bar", bars),
        ):
            key = (
                table.c.fingerprint
                if kind == "tick"
                else table.c.bar_id
            )

            row = connection.execute(
                select(table)
                .where(
                    table.c.epoch_id == epoch_id,
                    table.c.received_at <= cutoff,
                    table.c.ingested_at <= cutoff,
                    table.c.timestamp <= cutoff,
                )
                .order_by(
                    table.c.timestamp.desc(),
                    key,
                )
                .limit(1)
            ).mappings().first()

            latest[kind] = row

            if (
                row is not None
                and row["first_capture_id"] not in {
                    cap["capture_id"]
                    for cap in summaries
                }
            ):
                limitations.add(
                    "LATEST_"
                    + kind.upper()
                    + "_FIRST_CAPTURE_OUTSIDE_SAMPLE"
                )

        latest_tick = (
            latest["tick"]["timestamp"]
            if latest["tick"] else None
        )

        latest_bar = (
            latest["bar"]["timestamp"]
            if latest["bar"] else None
        )

    if not summaries:
        blockers.add("NO_CAPTURE_BY_CUTOFF")

    if latest_tick is None:
        blockers.add("NO_VISIBLE_TICK_IN_SAMPLE")
    elif not (
        timedelta(0)
        <= cutoff - latest_tick
        <= timedelta(seconds=120)
    ):
        blockers.add("LATEST_TICK_STALE_OR_FUTURE")

    expected_bar = cutoff.replace(
        minute=(cutoff.minute // 5) * 5,
        second=0,
        microsecond=0,
    ) - timedelta(minutes=5)

    if latest_bar != expected_bar:
        blockers.add("LATEST_CLOSED_M5_MISSING")

    if truncated:
        blockers.add("INSPECTION_INCOMPLETE")

    limitations.add(
        "ORDERED_ORIGINAL_API_BATCH_BYTES_NOT_RETAINED"
    )
    limitations.add(
        "LOCAL_RECEIPTS_NOT_EXTERNALLY_ATTESTED"
    )

    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id,
        "as_of": cutoff.isoformat(),
        "captures_examined": len(summaries),
        "source_rows_examined": count_rows,
        "sample_truncated": truncated,
        "captures": summaries,
        "overlap_query_count": overlap_count,
        "unqueried_gap_count": gap_count,
        "observations": sorted(observations),
        "evidence_limitations": sorted(limitations),
        "latest_tick": (
            latest_tick.isoformat()
            if latest_tick else None
        ),
        "latest_bar": (
            latest_bar.isoformat()
            if latest_bar else None
        ),
        "expected_bar": expected_bar.isoformat(),
        "bound_clock_implementation_sha256":
            policy.implementation_sha256,
        "local_clock_implementation_sha256":
            implementation_sha256(),
        "local_evidence_status": (
            "LOCAL_STRUCTURAL_CHECKS_PASS_UNATTESTED"
            if not blockers else "BLOCKED"
        ),
        "blockers": sorted(blockers),
        "scope": "BOUNDED_EXISTING_FORWARD_EPOCH_ONLY",
        "independent_broker_clock_attestation": False,
        "independent_source_receipt_attestation": False,
        "historical_utc_certified": False,
        "real_data_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }

    result["report_sha256"] = _sha(result)
    return result


def _parse_as_of(value):
    try:
        parsed = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
        if parsed.utcoffset() != timedelta(0):
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise ForwardEvidenceError(
            "--as-of must be an explicit UTC ISO-8601 instant."
        ) from None


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only-db",
        action="store_true",
        required=True,
    )
    parser.add_argument("--list-epochs", action="store_true")
    parser.add_argument("--epoch-id")
    parser.add_argument("--as-of")
    parser.add_argument("--captures", type=int, default=10)
    parser.add_argument("--source-limit", type=int, default=10000)
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)

    try:
        if not args.list_epochs and (
            not args.epoch_id or not args.as_of
        ):
            raise ForwardEvidenceError(
                "Supply --epoch-id and --as-of for inspection."
            )

        from backend.database.connection import engine

        try:
            if args.list_epochs:
                report = list_forward_epochs(engine)
            else:
                report = inspect_forward_evidence(
                    engine,
                    epoch_id=args.epoch_id,
                    as_of=_parse_as_of(args.as_of),
                    max_captures=args.captures,
                    max_source_rows=args.source_limit,
                )
        finally:
            engine.dispose()

    except Exception as exc:
        print(
            "FORWARD EVIDENCE INSPECTION BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json or args.list_epochs:
        print(json.dumps(
            report,
            sort_keys=True,
            indent=2,
        ))
    else:
        print(
            "EURUSD ORACLE | Read-only forward evidence health | "
            + VERSION
        )
        print(
            "Epoch: " + report["epoch_id"]
            + " | as_of=" + report["as_of"]
        )
        print(
            f"Local status: {report['local_evidence_status']} | "
            f"captures={report['captures_examined']} | "
            f"source_rows={report['source_rows_examined']} | FINAL=HOLD"
        )
        print(
            f"Latest tick: {report['latest_tick']} | "
            f"latest M5: {report['latest_bar']}"
        )
        print(
            f"Expected last closed M5: {report['expected_bar']}"
        )
        print(
            f"Repeated query overlaps: {report['overlap_query_count']} | "
            f"unqueried gaps: {report['unqueried_gap_count']}"
        )
        print(
            "Bound clock hash: "
            + report["bound_clock_implementation_sha256"]
        )
        print(
            "Local clock hash: "
            + report["local_clock_implementation_sha256"]
        )

        for reason in report["blockers"]:
            print("  BLOCKER " + reason)

        for reason in report["evidence_limitations"]:
            print("  LIMITATION " + reason)

        print("SHA-256: " + report["report_sha256"])
        print(
            "LOCAL RECEIPTS UNATTESTED | NO WRITES | "
            "REAL ADMISSION/TRAINING/EXECUTION BLOCKED"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
