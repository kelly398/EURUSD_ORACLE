
"""P3-13..16: read-only, point-in-time candidates from stored forward receipts.

Only stored prices are exposed. No broker attestation, prospective signal,
Council dispatch, training, policy rewrite, collector restart or execution.
A retrospective decision_at is a research reconstruction, not a logged decision.
"""

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
import re

from sqlalchemy import select, text

from backend.data_engine.clock_policy import (
    ClockPolicy, implementation_sha256, utc_msc,
)
from backend.data_engine.forward_store import (
    bars, captures, encoded, epochs, hashed, schema, ticks,
)
from backend.runtime.forward_source_provenance import (
    _capture_issue, _policy_copy, _source_issue, _utc, _bound,
)

VERSION = "EURUSD_P3_MARKET_INPUT_LINEAGE_V1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class MarketInputLineageError(RuntimeError):
    """Fail closed if the source or requested point in time is invalid."""


def _visible_rows(connection, table, epoch, decision, limit):
    """Only first-owned rows already ingested before the decision instant."""
    key = table.c.fingerprint if table is ticks else table.c.bar_id
    query = (select(
        table,
        captures.c.capture_id.label("owner_id"),
        captures.c.epoch_id.label("owner_epoch_id"),
        captures.c.received_at.label("capture_received_at"),
        captures.c.ingested_at.label("capture_ingested_at"),
        captures.c.tick_start.label("capture_tick_start"),
        captures.c.tick_end.label("capture_tick_end"),
        captures.c.manifest_json.label("capture_manifest_json"),
    ).join(
        captures,
        table.c.first_capture_id == captures.c.capture_id
    ).where(
        table.c.epoch_id == epoch,
        captures.c.epoch_id == epoch,
        table.c.timestamp < decision,
        table.c.received_at < decision,
        table.c.ingested_at < decision,
        captures.c.received_at < decision,
        captures.c.ingested_at < decision,
    ))
    if table is bars:
        query = query.where(
            table.c.timestamp <= decision - timedelta(minutes=5)
        )
    return connection.execute(
        query.order_by(table.c.timestamp.desc(), key).limit(limit + 1)
    ).mappings().all()


def _owned_issues(row, policy, kind, decision):
    """Check persisted owner receipt and source fields without clock replay."""
    owner = {
        "capture_id": row["owner_id"],
        "epoch_id": row["owner_epoch_id"],
        "received_at": row["capture_received_at"],
        "ingested_at": row["capture_ingested_at"],
        "tick_start": row["capture_tick_start"],
        "tick_end": row["capture_tick_end"],
        "manifest_json": row["capture_manifest_json"],
    }
    issues = set(_capture_issue(owner, policy, decision))
    issues.update(_source_issue(row, policy, kind, decision))
    if row["first_capture_id"] != row["owner_id"]:
        issues.add("SOURCE_OWNER_ID_MISMATCH")
    if row["epoch_id"] != policy.epoch_id:
        issues.add("SOURCE_EPOCH_MISMATCH")
    if row["owner_epoch_id"] != policy.epoch_id:
        issues.add("OWNER_EPOCH_MISMATCH")
    if kind == "bar":
        expected_id = hashed({
            "epoch_id": policy.epoch_id,
            "time": utc_msc(row["timestamp"]) // 1000,
        })
        if row["bar_id"] != expected_id:
            issues.add("BAR_ID_NOT_DERIVED_FROM_STORED_UTC_OPEN")
    return sorted(issues)


def _receipt_reference(row, kind):
    if row is None:
        return None
    available = max(
        row["received_at"], row["ingested_at"],
        row["capture_received_at"], row["capture_ingested_at"]
    )
    return {
        "source_id": (
            row["fingerprint"] if kind == "tick" else row["bar_id"]
        ),
        "first_capture_id": row["first_capture_id"],
        "event_time": row["timestamp"].isoformat(),
        "first_local_availability": available.isoformat(),
        "availability_basis":
            "LOCAL_RECEIVED_AND_INGESTED_TIMESTAMPS_UNATTESTED",
    }


def inspect_market_input_lineage(
    engine,
    *,
    epoch_id,
    as_of,
    decision_at=None,
    policy_file=None,
    expected_policy_sha256=None,
    max_rows=5000,
    max_captures=32,
):
    """Bounded query of a single epoch: no writes, normalization or execution."""
    if not isinstance(epoch_id, str) or HEX64.fullmatch(epoch_id) is None:
        raise MarketInputLineageError(
            "Lowercase SHA-256 epoch ID required."
        )
    try:
        cutoff = _utc(as_of)
        decision = _utc(decision_at) if decision_at is not None else cutoff
        _bound(max_rows, 1, 5000, "max_rows")
        _bound(max_captures, 1, 100, "max_captures")
    except (ValueError, TypeError, RuntimeError) as exc:
        raise MarketInputLineageError(
            "Invalid UTC cutoff or inspection bounds."
        ) from exc

    if decision > cutoff or cutoff > datetime.now(timezone.utc):
        raise MarketInputLineageError(
            "Decision/cutoff must not reference future data."
        )
    if engine.dialect.name not in ("sqlite", "postgresql"):
        raise MarketInputLineageError(
            "Unsupported database dialect."
        )

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
            raise MarketInputLineageError(
                "Forward schema version not recognized."
            )

        epoch = connection.execute(
            select(epochs).where(
                epochs.c.epoch_id == epoch_id,
                epochs.c.recorded_at < decision,
            )
        ).mappings().first()

        if epoch is None:
            raise MarketInputLineageError(
                "Epoch was not available at decision time."
            )

        try:
            policy = ClockPolicy.model_validate_json(
                epoch["policy_json"]
            )
        except (TypeError, ValueError) as exc:
            raise MarketInputLineageError(
                "Stored policy invalid."
            ) from exc

        if (
            policy.epoch_id != epoch_id
            or epoch["valid_from"] != policy.valid_from
            or epoch["valid_until"] != policy.valid_until
            or epoch["policy_json"] != encoded(
                policy.model_dump(mode="json")
            )
        ):
            raise MarketInputLineageError(
                "Stored policy/epoch mismatch."
            )

        capture_rows = connection.execute(
            select(captures)
            .where(
                captures.c.epoch_id == epoch_id,
                captures.c.received_at < decision,
                captures.c.ingested_at < decision,
                captures.c.tick_end <= decision,
            )
            .order_by(
                captures.c.ingested_at.desc(),
                captures.c.capture_id,
            )
            .limit(max_captures + 1)
        ).mappings().all()

        tick_rows = _visible_rows(
            connection, ticks, epoch_id, decision, max_rows
        )
        bar_rows = _visible_rows(
            connection, bars, epoch_id, decision, max_rows
        )

    sample_captures = capture_rows[:max_captures]
    sample_ticks = tick_rows[:max_rows]
    sample_bars = bar_rows[:max_rows]

    limited = {
        "captures": len(capture_rows) > max_captures,
        "ticks": len(tick_rows) > max_rows,
        "bars": len(bar_rows) > max_rows,
    }

    problems = set()
    for row in sample_captures:
        problems.update(_capture_issue(row, policy, decision))
    for row in sample_ticks:
        problems.update(_owned_issues(row, policy, "tick", decision))
    for row in sample_bars:
        problems.update(_owned_issues(row, policy, "bar", decision))

    if any(limited.values()):
        problems.add("BOUNDED_SAMPLE_INCOMPLETE")
    if not sample_ticks:
        problems.add("NO_TICK_FIRST_AVAILABLE_BEFORE_DECISION")
    if not sample_bars:
        problems.add("NO_CLOSED_M5_FIRST_AVAILABLE_BEFORE_DECISION")
    if not sample_captures:
        problems.add("NO_CAPTURE_FIRST_AVAILABLE_BEFORE_DECISION")

    tick = sample_ticks[0] if sample_ticks else None
    bar = sample_bars[0] if sample_bars else None
    latest_capture = sample_captures[0] if sample_captures else None

    expected_bar = decision.replace(
        minute=decision.minute // 5 * 5,
        second=0, microsecond=0
    ) - timedelta(minutes=5)

    if (
        tick is not None
        and decision - tick["timestamp"] > timedelta(seconds=120)
    ):
        problems.add("TICK_NOT_CURRENT_AT_DECISION")

    if bar is not None and bar["timestamp"] != expected_bar:
        problems.add("M5_NOT_LAST_CLOSED_AT_DECISION")

    if (
        latest_capture is not None
        and decision - latest_capture["ingested_at"]
        > timedelta(minutes=15)
    ):
        problems.add("CAPTURE_NOT_CURRENT_AT_DECISION")

    if not policy.valid_from <= decision < policy.valid_until:
        problems.add("EPOCH_NOT_ACTIVE_AT_DECISION")

    if policy.implementation_sha256 != implementation_sha256():
        problems.add("CURRENT_CLOCK_CODE_DIFFERS_FROM_BOUND_EPOCH")

    policy_copy = _policy_copy(
        policy, policy_file, expected_policy_sha256
    )

    if not policy_copy["canonical_epoch_match"]:
        problems.add("MATCHING_EPOCH_POLICY_COPY_NOT_ESTABLISHED")

    if policy_copy["observed_digest_matches_expected"] is False:
        problems.add("POLICY_COPY_CHANGED_FROM_OBSERVED_SHA256")

    # These are candidate stored prices, never an admitted Council payload.
    # Source integrity faults withhold all values. Timing and policy
    # blockers are reported separately, never silently cleared.
    data_ok = bool(
        tick and bar and not any(limited.values())
        and not any(code for code in problems if code in {
            "CAPTURE_MANIFEST_DIGEST_INVALID",
            "CAPTURE_MANIFEST_UNREADABLE",
            "CAPTURE_LOCAL_CHRONOLOGY_INVALID",
            "CAPTURE_MANIFEST_METADATA_INVALID",
            "TICK_RAW_PROVENANCE_MISMATCH",
            "BAR_RAW_PROVENANCE_MISMATCH",
            "SOURCE_RAW_RECORD_UNREADABLE",
            "RAW_RECORD_JSON_INVALID",
            "SOURCE_LOCAL_CHRONOLOGY_INVALID",
            "SOURCE_FIRST_CAPTURE_RECEIPT_MISMATCH",
            "TICK_FIRST_CAPTURE_INTERVAL_INVALID",
            "BAR_FIRST_CAPTURE_INTERVAL_INVALID",
            "BAR_ID_NOT_DERIVED_FROM_STORED_UTC_OPEN",
            "SOURCE_OWNER_ID_MISMATCH",
            "OWNER_EPOCH_MISMATCH",
            "SOURCE_EPOCH_MISMATCH",
        })
    )

    allowed_nonintegrity = {
        "TICK_NOT_CURRENT_AT_DECISION",
        "M5_NOT_LAST_CLOSED_AT_DECISION",
        "CAPTURE_NOT_CURRENT_AT_DECISION",
        "EPOCH_NOT_ACTIVE_AT_DECISION",
        "CURRENT_CLOCK_CODE_DIFFERS_FROM_BOUND_EPOCH",
        "MATCHING_EPOCH_POLICY_COPY_NOT_ESTABLISHED",
        "POLICY_COPY_CHANGED_FROM_OBSERVED_SHA256",
    }

    if any(problem not in allowed_nonintegrity for problem in problems):
        data_ok = False

    values = None
    if data_ok:
        bid, ask = float(tick["bid"]), float(tick["ask"])
        op, hi, lo, cl = (
            float(bar[k])
            for k in ("open", "high", "low", "close")
        )
        if (
            all(
                math.isfinite(x)
                for x in (bid, ask, op, hi, lo, cl)
            )
            and 0 < bid <= ask
            and 0 < lo <= min(op, cl) <= max(op, cl) <= hi
        ):
            values = {
                "bid": bid,
                "ask": ask,
                "spread_price_units": ask - bid,
                "last_closed_m5_open": op,
                "last_closed_m5_high": hi,
                "last_closed_m5_low": lo,
                "last_closed_m5_close": cl,
                "last_closed_m5_range_price_units": hi - lo,
            }
        else:
            problems.add("NONFINITE_OR_INVALID_STORED_PRICES")

    result = {
        "assessment_version": VERSION,
        "epoch_id": epoch_id,
        "as_of": cutoff.isoformat(),
        "decision_at": decision.isoformat(),
        "decision_time_mode":
            "RETROSPECTIVE_RECONSTRUCTION_NOT_REGISTERED_SIGNAL",
        "policy_copy": policy_copy,
        "first_owned_receipts": {
            "captures_inspected": len(sample_captures),
            "ticks_inspected": len(sample_ticks),
            "bars_inspected": len(sample_bars),
            "sample_truncated": limited,
            "original_api_order_recoverable": False,
            "broker_receipt_signature_verified": False,
            "latest_tick": _receipt_reference(tick, "tick"),
            "latest_closed_m5": _receipt_reference(bar, "bar"),
            "expected_last_closed_m5_open": expected_bar.isoformat(),
            "latest_capture_ingested_at": (
                latest_capture["ingested_at"].isoformat()
                if latest_capture else None
            ),
        },
        "locally_derived_market_values": values,
        "derived_values_status": (
            "LOCAL_STORED_VALUES_UNATTESTED"
            if values is not None
            else "WITHHELD_INVALID_OR_INCOMPLETE"
        ),
        "local_blockers": sorted(problems),
        "council_input_families_considered": [
            "ticks", "candles", "market_state"
        ],
        "council_input_families_independently_attested": 0,
        "prospective_prediction_registered": False,
        "independent_broker_source_attested": False,
        "independent_first_availability_attested": False,
        "historical_utc_certified": False,
        "real_forward_council_admission": False,
        "read_only": True,
        "database_modified": False,
        "council_invoked": False,
        "council_seats_run": 0,
        "training_authorized": False,
        "execution_authorized": False,
        "collector_started": False,
        "final_action": "HOLD",
    }

    result["report_sha256"] = hashed(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--read-only-db", action="store_true", required=True
    )
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--decision-at")
    parser.add_argument("--policy-file")
    parser.add_argument("--expected-policy-sha256")
    parser.add_argument("--captures", type=int, default=32)
    parser.add_argument("--source-rows", type=int, default=5000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if not HEX64.fullmatch(args.epoch_id):
            raise MarketInputLineageError("Invalid epoch ID.")
        if (
            args.expected_policy_sha256 is not None
            and not HEX64.fullmatch(args.expected_policy_sha256)
        ):
            raise MarketInputLineageError(
                "Invalid expected policy file SHA-256."
            )

        cutoff = _utc(
            datetime.fromisoformat(
                args.as_of.replace("Z", "+00:00")
            )
        )
        decision = (
            _utc(
                datetime.fromisoformat(
                    args.decision_at.replace("Z", "+00:00")
                )
            )
            if args.decision_at else cutoff
        )

        _bound(args.captures, 1, 100, "captures")
        _bound(args.source_rows, 1, 5000, "source_rows")

        if (
            decision > cutoff
            or cutoff > datetime.now(timezone.utc)
        ):
            raise MarketInputLineageError(
                "Future cutoff or decision."
            )

        from backend.database.connection import engine
        try:
            result = inspect_market_input_lineage(
                engine,
                epoch_id=args.epoch_id,
                as_of=cutoff,
                decision_at=decision,
                policy_file=args.policy_file,
                expected_policy_sha256=args.expected_policy_sha256,
                max_rows=args.source_rows,
                max_captures=args.captures,
            )
        finally:
            engine.dispose()

    except Exception as exc:
        print(
            "MARKET INPUT LINEAGE BLOCKED: "
            + type(exc).__name__
        )
        return 1

    if args.json:
        print(json.dumps(
            result, sort_keys=True, indent=2, allow_nan=False
        ))
    else:
        print(
            "EURUSD ORACLE | P3-13..16 market-input lineage (read-only)"
        )
        print(
            "Decision: " + result["decision_at"]
            + " | snapshot: " + result["as_of"]
        )
        refs = result["first_owned_receipts"]
        print(
            "Inspected: %s captures; %s ticks; %s M5 bars" % (
                refs["captures_inspected"],
                refs["ticks_inspected"],
                refs["bars_inspected"],
            )
        )
        print(
            "Stored market values: "
            + result["derived_values_status"]
        )
        for item in result["local_blockers"]:
            print("  LOCAL BLOCKER " + item)

        print(
            "Council source attestation: 0/3; "
            "original ordered batches: NOT RECOVERABLE"
        )
        print("SHA-256: " + result["report_sha256"])
        print(
            "READ-ONLY | NOT A PREDICTION | NO COUNCIL | FINAL=HOLD"
        )
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
