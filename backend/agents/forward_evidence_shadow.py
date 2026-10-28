
"""Milestones 33-36: read-only forward-feed evidence gate and inert shadow.

M33 revalidates a forward feed epoch, raw rows and first-capture lineage.
M34 checks candidate event time, recorded receipt and freshness.
M35 fails closed on invalid, stale, incomplete or tampered evidence.
M36 may exercise the existing 31-seat Council on a SEPARATE synthetic fixture.

A locally stored receipt is NOT independent broker/source attestation. Forward
observations never enter AgentContext here. No history admission or execution.
"""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from backend.data_engine.clock_policy import ClockPolicy, ClockPolicyError, aware_utc, instant
from backend.data_engine.forward_store import (
    ForwardFeedStore, epochs, captures, ticks, bars, encoded, hashed,
)

VERSION = "EURUSD_FORWARD_EVIDENCE_SHADOW_V1"


class ForwardShadowError(RuntimeError):
    """The evidence cannot be reliably inspected; never admit it."""


def _require(condition, message):
    if not condition:
        raise ForwardShadowError(message)


def _limit(value, name, maximum):
    _require(type(value) is int and 1 <= value <= maximum,
             f"{name} must be an integer in 1..{maximum}.")


def _raw_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ForwardShadowError("Duplicate stored raw-source field.")
            result[key] = value
        return result

    if not isinstance(raw, str) or len(raw) > 20_000:
        raise ForwardShadowError("Unbounded stored source record.")
    try:
        record = json.loads(raw, object_pairs_hook=unique,
                            parse_constant=lambda _: _require(False, "Nonfinite JSON."))
    except (ValueError, TypeError, RecursionError) as exc:
        raise ForwardShadowError("Invalid stored source JSON.") from exc
    _require(isinstance(record, dict) and encoded(record) == raw,
             "Stored source JSON is not canonical.")
    return record


def inspect_forward_evidence(store, policy, as_of, *, max_tick_age_seconds=120,
                             max_bar_age_seconds=360):
    """Check source-normalization, capture identity and freshness without writes.

    Existing ForwardFeedStore.status performs primary clock/raw validation.
    Here we additionally verify each row's first capture and epoch linkage.
    Neither local checks nor stored receipt fields prove external identity.
    """
    _limit(max_tick_age_seconds, "max_tick_age_seconds", 3600)
    _limit(max_bar_age_seconds, "max_bar_age_seconds", 3600)
    _require(isinstance(store, ForwardFeedStore) and isinstance(policy, ClockPolicy),
             "ForwardFeedStore and ClockPolicy are required.")
    try:
        _require(as_of.utcoffset() == timedelta(0) and
                 aware_utc(as_of) == as_of, "Analysis time must be UTC.")
        policy.check_active(as_of)
        initial = store.status(policy, as_of=as_of)
        _require(initial["epoch_id"] == policy.epoch_id,
                 "Forward epoch identity mismatch.")
        blockers = list(initial["blockers"])
        _require(len(blockers) == len(set(blockers)), "Duplicate status blocker.")
        checked = {}
        # Only SELECTs. PostgreSQL transaction is explicitly read-only.
        with store.engine.connect() as conn:
            with conn.begin():
                if conn.dialect.name == "postgresql":
                    conn.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
                    conn.execute(text("SET LOCAL statement_timeout = '15s'"))
                _require(conn.dialect.name in ("sqlite", "postgresql"),
                         "Unsupported forward database backend.")
                epoch = conn.execute(select(epochs).where(
                    epochs.c.epoch_id == policy.epoch_id)).mappings().one_or_none()
                _require(epoch is not None and
                         epoch["policy_json"] == encoded(policy.model_dump(mode="json")) and
                         epoch["valid_from"] == policy.valid_from and
                         epoch["valid_until"] == policy.valid_until,
                         "Forward epoch policy is absent or altered.")
                for name, table in (("tick", ticks), ("bar", bars)):
                    row = conn.execute(select(table).where(
                        table.c.epoch_id == policy.epoch_id,
                        table.c.ingested_at <= as_of,
                    ).order_by(table.c.timestamp.desc()).limit(1)).mappings().first()
                    if row is None:
                        blockers.append("FORWARD_" + name.upper() + "_MISSING")
                        checked[name] = None
                        continue
                    cap = conn.execute(select(captures).where(
                        captures.c.capture_id == row["first_capture_id"]
                    )).mappings().one_or_none()
                    _require(cap is not None and cap["epoch_id"] == policy.epoch_id,
                             "First-capture lineage is missing.")
                    manifest = _raw_json(cap["manifest_json"])
                    _require(hashed(manifest) == cap["capture_id"] and
                             manifest["epoch_id"] == policy.epoch_id and
                             manifest["received_at"] == cap["received_at"].isoformat() and
                             manifest["tick_start"] == cap["tick_start"].isoformat() and
                             manifest["tick_end"] == cap["tick_end"].isoformat() and
                             row["received_at"] == cap["received_at"] and
                             cap["received_at"] <= cap["ingested_at"] <= as_of and
                             row["received_at"] <= row["ingested_at"] <= as_of,
                             "First-capture receipt or manifest changed.")
                    raw = _raw_json(row["raw_json"])
                    if name == "tick":
                        normalized = policy.tick(raw)
                        _require(instant(normalized["time_msc"]) == row["timestamp"] and
                            hashed({"epoch_id": policy.epoch_id, "raw": raw}) == row["fingerprint"] and
                            (normalized["bid"], normalized["ask"]) == (row["bid"], row["ask"]) and
                            cap["tick_start"] <= row["timestamp"] < cap["tick_end"] and
                            row["timestamp"] <= row["received_at"],
                            "Stored tick normalization or capture interval changed.")
                        # Raw timestamp is broker wall-clock, not UTC; keep it verbatim.
                        _require(row["raw_time_msc"] == raw["time_msc"],
                                 "Original tick timestamp changed.")
                        age = (as_of - row["timestamp"]).total_seconds()
                        if age > max_tick_age_seconds:
                            blockers.append("FORWARD_TICK_STALE_BY_POLICY")
                    else:
                        normalized = policy.rate(raw)
                        _require(instant(normalized["time"] * 1000) == row["timestamp"] and
                                 row["raw_open_time"] == raw["time"] and
                                 hashed({"epoch_id": policy.epoch_id,
                                         "time": normalized["time"]}) == row["bar_id"] and
                                 all(normalized[key] == row[key] for key in
                                     ("open", "high", "low", "close")) and
                                 row["timestamp"] >= cap["received_at"] - timedelta(days=1) and
                                 row["timestamp"] + timedelta(minutes=5) <= row["received_at"] and
                                 row["timestamp"] + timedelta(minutes=5) <= as_of,
                                 "Stored closed bar or receipt changed.")
                        _require(manifest["bar_start"] <= row["timestamp"].isoformat() and
                                 row["timestamp"].isoformat() < manifest["bar_end"],
                                 "Bar lies outside its recorded capture window.")
                        age = (as_of - (row["timestamp"] + timedelta(minutes=5))).total_seconds()
                        if age > max_bar_age_seconds:
                            blockers.append("FORWARD_BAR_STALE_BY_POLICY")
                    _require(0 <= age, "Observed event is in the future.")
                    checked[name] = {
                        "first_capture_id": row["first_capture_id"],
                        "event_time_utc": row["timestamp"].isoformat(),
                        "recorded_received_at": row["received_at"].isoformat(),
                        "recorded_ingested_at": row["ingested_at"].isoformat(),
                        "age_seconds": round(age, 3),
                        "raw_normalization_and_local_capture_verified": True,
                    }
    except ForwardShadowError:
        raise
    except (ClockPolicyError, SQLAlchemyError, OSError, ValueError, TypeError, KeyError,
            OverflowError, AttributeError) as exc:
        raise ForwardShadowError("Forward source or time evidence failed verification.") from exc

    blockers = sorted(set(blockers))
    report = {
        "assessment_version": VERSION,
        "epoch_id": policy.epoch_id,
        "as_of": as_of.isoformat(),
        "status": "BLOCKED" if blockers else "LOCAL_EVIDENCE_CHECKS_PASS_RESEARCH_ONLY",
        "blockers": blockers,
        "latest_records": checked,
        "local_receipts_checked": True,
        "independent_external_receipt_attestation": False,
        "full_council_context_causality_verified": False,
        "atomic_cross_query_snapshot_verified": False,
        "source_admitted_to_council": False,
        "research_only": True,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    report["evidence_sha256"] = sha256(encoded(report).encode()).hexdigest()
    return report


def shadow_forward_gate(store, policy, as_of, *, synthetic_council_context=None,
                        synthetic_only=False, max_tick_age_seconds=120,
                        max_bar_age_seconds=360):
    """Gate local evidence and optionally run an unrelated synthetic Council test.

    The synthetic context is NEVER populated from forward evidence. No caller
    can obtain admitted, executable or historical Council data via this API.
    """
    if synthetic_council_context is not None and synthetic_only is not True:
        raise ForwardShadowError("Synthetic Council testing requires explicit opt-in.")
    evidence = inspect_forward_evidence(
        store, policy, as_of, max_tick_age_seconds=max_tick_age_seconds,
        max_bar_age_seconds=max_bar_age_seconds,
    )
    council = None
    if not evidence["blockers"] and synthetic_only is True:
        _require(synthetic_council_context is not None,
                 "Synthetic testing needs an explicit test context.")
        from backend.agents.synthetic_council_shadow import run_shadow_council
        council = run_shadow_council(synthetic_council_context)
        _require(council["final_action"] == "HOLD" and
                 council["execution_authorized"] is False and
                 council["synthetic_only"] is True,
                 "Synthetic Council did not fail closed.")
    result = {
        "assessment_version": VERSION,
        "forward_evidence": evidence,
        "synthetic_council_test": council,
        "forward_rows_supplied_to_council": False,
        "source_admitted_to_council": False,
        "final_action": "HOLD",
        "training_authorized": False,
        "execution_authorized": False,
    }
    result["report_sha256"] = sha256(encoded(result).encode()).hexdigest()
    return result


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--scenario", choices=("complete", "stale", "both"), default="both")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    # CLI intentionally has no live database, broker, policy-path or context input.
    # Temporary SQLite is purely in-memory and uses existing project fixtures.
    from sqlalchemy import create_engine
    from backend.data_engine.clock_policy import (
        implementation_sha256, utc_msc, hfm_utc_to_wall,
    )
    from backend.agents.test_full_research_council import synthetic_full_context
    now = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)
    clock_at = [now]
    engine = create_engine("sqlite://")
    lease = ClockPolicy(
        company_sha256=sha256(b"SYNTHETIC_COMPANY").hexdigest(),
        server_sha256=sha256(b"SYNTHETIC_SERVER").hexdigest(),
        evidence_sha256="a" * 64,
        implementation_sha256=implementation_sha256(),
        valid_from=now - timedelta(hours=1),
        valid_until=now + timedelta(days=1),
    )
    tick_ms = utc_msc(hfm_utc_to_wall(now - timedelta(seconds=2)))
    bar_s = utc_msc(hfm_utc_to_wall(now - timedelta(minutes=5))) // 1000
    tick = dict(time=tick_ms // 1000, time_msc=tick_ms, bid=1.1, ask=1.1001,
                last=0.0, volume=0, flags=6, volume_real=0.0)
    bar = dict(time=bar_s, open=1.1, high=1.101, low=1.099,
               close=1.1005, tick_volume=100, spread=10, real_volume=0)
    results = []
    try:
        store = ForwardFeedStore(engine, clock=lambda: clock_at[0])
        store.initialize()
        store.append(lease,
                     tick_rows=[lease.tick(tick)], bar_rows=[lease.rate(bar)],
                     received_at=now, tick_start=now - timedelta(minutes=1),
                     tick_end=now, bar_start=now - timedelta(minutes=10),
                     bar_end=now)
        for name in (("complete", "stale") if args.scenario == "both" else (args.scenario,)):
            clock_at[0] = now if name == "complete" else now + timedelta(minutes=3)
            result = shadow_forward_gate(
                store, lease, clock_at[0],
                synthetic_council_context=synthetic_full_context(), synthetic_only=True,
            )
            results.append({"scenario": name, "result": result})
    except (ForwardShadowError, SQLAlchemyError, ValueError, TypeError) as exc:
        print("FORWARD SHADOW BLOCKED: " + type(exc).__name__)
        return 1
    finally:
        engine.dispose()
    if args.json:
        print(json.dumps(results, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Forward evidence and Council shadow M33-36")
        for item in results:
            report = item["result"]
            evidence = report["forward_evidence"]
            council = report["synthetic_council_test"]
            print(f"SCENARIO {item['scenario']} | {evidence['status']} | "
                  f"blockers={','.join(evidence['blockers']) or 'none'} | "
                  f"synthetic_seats={council['council_seat_count'] if council else 0} "
                  "| FINAL=HOLD")
        print("IN-MEMORY SYNTHETIC SOURCE ONLY | FORWARD DATA NOT COUNCIL-ADMITTED | "
              "NO BROKER | NO FILES WRITTEN | TRAINING/EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
