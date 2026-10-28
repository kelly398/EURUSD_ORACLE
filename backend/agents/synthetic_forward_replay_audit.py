
"""M49-52: source-fingerprinted audit of a synthetic-only causal Council replay.

M49: reason-coded failures and state-sensitive, deterministic evidence hashes.
M50: independently check each decision cutoff's visible capture membership.
M51: block overlaps, missing/late rows, and corrupted source or manifests.
M52: invoke the existing M45-48 Council replay only after all checks pass.

Assessment is SELECT-only. The CLI fixture writer creates new in-memory
SQLite records. This is not independent broker attestation, source admission,
backtesting, model training, or trading execution. Final action is always HOLD.
"""

import argparse
from datetime import timedelta
from hashlib import sha256
import json

from sqlalchemy import select

from backend.agents.synthetic_forward_capture_replay import (
    CaptureReplayError, FIVE_MINUTES, _eligible, _read_snapshot, _receipt,
)
from backend.agents.synthetic_forward_variable_replay import (
    _count, _match_market, make_variable_demo, replay_variable_captures,
)
from backend.data_engine.clock_policy import ClockPolicyError
from backend.data_engine.forward_store import (
    ForwardFeedStore, bars, captures, encoded, epochs, ticks,
)

VERSION = "EURUSD_SYNTHETIC_REPLAY_AUDIT_V1"


class ReplayAuditError(RuntimeError):
    """The audit cannot safely establish the declared synthetic evidence."""


def _digest(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def _stamp(value):
    """Stable record representation used ONLY for a digest, not an exposure."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _fingerprint(rows):
    """Hash full stored fields including raw JSON, but do not publish them."""
    return _digest([{
        k: _stamp(v) for k, v in sorted(row.items())
    } for row in rows])


def _observed_at(store, policy, cutoff):
    """One read-only SQL snapshot of data actually ingested by the cutoff."""
    with store.engine.connect() as conn:
        with conn.begin():
            rows = {}
            for name, table in (
                ("epochs", epochs),
                ("captures", captures),
                ("ticks", ticks),
                ("bars", bars),
            ):
                query = select(table).where(table.c.epoch_id == policy.epoch_id)
                if name == "epochs":
                    query = query.where(table.c.recorded_at <= cutoff)
                else:
                    query = query.where(table.c.received_at <= cutoff,
                                        table.c.ingested_at <= cutoff)
                rows[name] = conn.execute(query).mappings().all()
    return rows


def _structural_codes(observed, expected_count):
    """Explicitly distinguish gaps/overlaps from lost first-capture members."""
    codes = []
    caps = sorted(observed["captures"],
                  key=lambda row: (row["tick_start"], row["capture_id"]))
    if len(observed["epochs"]) != 1:
        codes.append("EPOCH_NOT_VISIBLE_BY_CUTOFF")
    if len(caps) != expected_count:
        codes.append("VISIBLE_CAPTURE_COUNT_MISMATCH")
    prior = None
    for cap in caps:
        if prior is not None:
            if cap["tick_start"] < prior["tick_end"]:
                codes.append("TICK_INTERVAL_OVERLAP")
            elif cap["tick_start"] > prior["tick_end"]:
                codes.append("TICK_INTERVAL_GAP")
            if (cap["received_at"] < prior["received_at"] or
                    cap["ingested_at"] < prior["ingested_at"]):
                codes.append("CAPTURE_RECEIPT_REGRESSION")
        prior = cap
        for kind, key in (("TICK", "ticks"), ("BAR", "bars")):
            members = [r for r in observed[key]
                       if r["first_capture_id"] == cap["capture_id"]]
            if len(members) != 1:
                codes.append("FIRST_CAPTURE_" + kind + "_MEMBERSHIP_INVALID")
    known = {row["capture_id"] for row in caps}
    for kind, key in (("TICK", "ticks"), ("BAR", "bars")):
        if any(row["first_capture_id"] not in known
               for row in observed[key]):
            codes.append("ORPHAN_" + kind + "_SOURCE_ROW")
    return sorted(set(codes))


def _source_failure_code(error):
    """Map only locally defined integrity messages, never expose arbitrary errors."""
    reason = str(error)
    if "manifest" in reason.lower():
        return "CAPTURE_MANIFEST_INTEGRITY_INVALID"
    if "Tick raw" in reason:
        return "TICK_SOURCE_INTEGRITY_INVALID"
    if "Candle raw" in reason:
        return "BAR_SOURCE_INTEGRITY_INVALID"
    if "Epoch provenance" in reason:
        return "EPOCH_PROVENANCE_INVALID"
    if "source" in reason.lower() or "capture" in reason.lower():
        return "SOURCE_PROVENANCE_INVALID"
    return "SNAPSHOT_INTEGRITY_INVALID"


def audit_variable_replay(store, policy, context, *, capture_count,
                          synthetic_only=False):
    """Revalidate every as-of prefix before permitting M45 to run the Council.

    A failed audit never invokes the Council. Successful audit delegates the
    final synthetic context validation and Council execution to M45, without
    recreating its agent logic or altering its eligibility rules.
    """
    count = _count(capture_count)
    _eligible(store, policy, context, synthetic_only)
    cutoffs = [context.as_of - FIVE_MINUTES * (count - 1 - i)
               for i in range(count)]
    snapshots = []
    failed = []
    prior_ids = ()

    for index, cutoff in enumerate(cutoffs, start=1):
        try:
            observed = _observed_at(store, policy, cutoff)
            state_sha = _digest({name: _fingerprint(observed[name])
                                 for name in ("epochs", "captures", "ticks", "bars")})
            codes = _structural_codes(observed, index)
            ids = tuple(row["capture_id"] for row in sorted(
                observed["captures"],
                key=lambda row: (row["tick_end"], row["capture_id"])))
            if ids[:len(prior_ids)] != prior_ids:
                codes.append("PRIOR_CAPTURE_PREFIX_CHANGED")
            prior_ids = ids
            if not codes:
                try:
                    verified = _read_snapshot(store, policy, cutoff)
                    receipt = _receipt(cutoff, verified)
                    if len(verified) != index:
                        codes.append("REVALIDATED_PREFIX_COUNT_MISMATCH")
                    for reason in receipt["blockers"]:
                        codes.append("RECEIPT_" + reason)
                    if not codes:
                        _match_market(verified[-1], context, cutoff,
                                      index - 1, count)
                except (CaptureReplayError, ClockPolicyError) as exc:
                    codes.append(_source_failure_code(exc))
            codes = sorted(set(codes))
            item = {
                "cutoff": cutoff.isoformat(),
                "expected_captures": index,
                "visible_captures": len(observed["captures"]),
                "observed_state_sha256": state_sha,
                "reason_codes": codes,
                "status": "BLOCKED" if codes else "POINT_IN_TIME_VERIFIED_SYNTHETIC",
            }
            item["receipt_sha256"] = _digest(item)
            snapshots.append(item)
            failed.extend(f"SNAPSHOT_{index}:{code}" for code in codes)
        except (CaptureReplayError, ClockPolicyError, ValueError, TypeError,
                KeyError, AttributeError, OverflowError) as exc:
            raise ReplayAuditError("Unable to safely audit the synthetic store.") from exc

    council = None
    if not failed:
        try:
            council = replay_variable_captures(
                store, policy, context, capture_count=count, synthetic_only=True)
        except (CaptureReplayError, ClockPolicyError, ValueError, TypeError) as exc:
            # Failure after dispatch cannot truthfully be called 'not invoked'.
            raise ReplayAuditError("Canonical replay failed after preflight.") from exc
        if (council["status"] != "SYNTHETIC_VARIABLE_REPLAY_HOLD" or
                council["council_runs"] != 1 or council["council_seats"] != 31 or
                council["final_action"] != "HOLD" or
                council["execution_authorized"] is not False):
            raise ReplayAuditError("Canonical replay did not meet HOLD contract.")

    result = {
        "assessment_version": VERSION,
        "epoch_id": policy.epoch_id,
        "capture_count_requested": count,
        "status": "AUDITED_SYNTHETIC_COUNCIL_HOLD" if council else "BLOCKED",
        "snapshots": snapshots,
        "reason_codes": sorted(set(failed)),
        "council_invoked": council is not None,
        "council_seats": council["council_seats"] if council else 0,
        "council_report_sha256": council["report_sha256"] if council else None,
        "independent_broker_attestation": False,
        "real_forward_admission": False,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["evidence_fingerprint_sha256"] = _digest({
        "epoch": result["epoch_id"],
        "snapshots": [s["receipt_sha256"] for s in snapshots],
        "failures": result["reason_codes"],
    })
    result["report_sha256"] = _digest(result)
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--captures", type=int, default=5)
    parser.add_argument("--scenario", choices=(
        "complete", "delayed", "missing", "overlap", "conflict", "reordered", "all"),
        default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    names = (("complete", "delayed", "missing", "overlap", "conflict", "reordered")
             if args.scenario == "all" else (args.scenario,))
    try:
        _count(args.captures)
    except CaptureReplayError as exc:
        print("AUDIT BLOCKED: " + str(exc))
        return 1
    outputs = []
    for name in names:
        engine = None
        try:
            store, policy, context, engine = make_variable_demo(
                capture_count=args.captures,
                fault="none" if name == "complete" else name,
            )
            report = audit_variable_replay(
                store, policy, context, capture_count=args.captures,
                synthetic_only=True,
            )
            outputs.append({"scenario": name, "result": report})
        except (ReplayAuditError, CaptureReplayError, ClockPolicyError,
                ValueError, TypeError) as exc:
            print("AUDIT BLOCKED: " + type(exc).__name__)
            return 1
        finally:
            if engine is not None:
                engine.dispose()
    if args.json:
        print(json.dumps(outputs, indent=2, sort_keys=True, allow_nan=False))
    else:
        print("EURUSD ORACLE | Causal replay evidence audit M49-52")
        for item in outputs:
            result = item["result"]
            print(f"SCENARIO {item['scenario']} | {result['status']} | "
                  f"cutoffs={len(result['snapshots'])} | "
                  f"seats={result['council_seats']} | FINAL=HOLD | "
                  f"digest={result['evidence_fingerprint_sha256']}")
            for reason in result["reason_codes"]:
                print("  " + reason)
        print("SYNTHETIC IN-MEMORY ONLY | NO BROKER | NO FILES WRITTEN | "
              "REAL ADMISSION/TRAINING/EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
