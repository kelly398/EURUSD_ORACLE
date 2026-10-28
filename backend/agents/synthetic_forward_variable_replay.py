
"""M45-48: bounded variable-length synthetic causal replay and Council shadow.

M45: validate 2..8 contiguous first-capture batches using existing M41 proof.
M46: re-evaluate every decision-time prefix without future receipts or backfill.
M47: reject missing, delayed, overlapping, reordered, and tampered evidence.
M48: run the real Council at the FINAL cutoff for each independent scenario.

All assessment operations are SELECT-only. The fixture writer alone creates new
in-memory SQLite rows. No PostgreSQL, broker access, historical admission,
model training, persistence, or executable decision. Every final action is HOLD.
"""

import argparse
from datetime import timedelta
from hashlib import sha256
import json

from sqlalchemy import create_engine, select

from backend.agents.synthetic_forward_capture_replay import (
    CaptureReplayError, FIVE_MINUTES, _eligible, _read_snapshot, _receipt, need,
)
from backend.agents.synthetic_council_shadow import run_shadow_council
from backend.agents.synthetic_forward_causal_bridge import (
    TEST_COMPANY, TEST_SERVER, TEST_EVIDENCE,
)
from backend.data_engine.clock_policy import (
    ClockPolicy, ClockPolicyError, hfm_utc_to_wall,
    implementation_sha256, utc_msc,
)
from backend.data_engine.forward_store import (
    ForwardFeedStore, captures, ticks, bars, encoded,
)
from backend.agents.contracts import AgentContext

VERSION = "EURUSD_SYNTHETIC_VARIABLE_CAPTURE_REPLAY_V1"
MIN_CAPTURES = 2
MAX_CAPTURES = 8  # M41's source verifier has the same bounded limit.


def _count(value):
    need(type(value) is int and MIN_CAPTURES <= value <= MAX_CAPTURES,
         "Capture count must be an integer from 2 to 8.")
    return value


def _match_market(row, context, cutoff, index, total):
    """Check each source observation against the pinned synthetic fixture."""
    expected_bar = context.candles[-total + index]
    expected_tick = next(
        (tick for tick in context.ticks
         if tick.timestamp == cutoff - timedelta(seconds=10)), None
    )
    need(row["bar"] == expected_bar,
         "Replay candle differs from pinned market-state history.")
    need(expected_tick is not None, "Missing pinned synthetic tick.")
    actual = row["tick"]
    need(actual.timestamp == expected_tick.timestamp and
         actual.bid == expected_tick.bid and
         actual.ask == expected_tick.ask and
         abs(actual.spread_pips - expected_tick.spread_pips) < 1e-7,
         "Replay quote differs from pinned market-state history.")
    need(row["tick_received_at"] <= cutoff and
         row["bar_received_at"] <= cutoff and
         row["tick_ingested_at"] <= cutoff and
         row["bar_ingested_at"] <= cutoff and
         row["capture_ingested_at"] <= cutoff,
         "Market observation was not available by decision cutoff.")


def replay_variable_captures(store, policy, context, *, capture_count,
                             synthetic_only=False):
    """Verify every prefix before ONE final-cutoff synthetic Council dispatch.

    A Council run at an earlier cutoff would require rebuilding every other
    macro/risk/market-state input at that earlier cutoff. This module does not
    pretend the existing final-time fixture provides such prior-time proof.
    """
    total = _count(capture_count)
    _eligible(store, policy, context, synthetic_only)
    need(len(context.candles) >= total and len(context.ticks) >= total,
         "Synthetic fixture lacks market history.")
    cutoffs = tuple(context.as_of - FIVE_MINUTES * (total - 1 - i)
                    for i in range(total))
    receipts = []
    previous_ids = ()
    final_rows = None
    failures = []

    try:
        for i, cutoff in enumerate(cutoffs):
            rows = _read_snapshot(store, policy, cutoff)
            receipt = _receipt(cutoff, rows)
            ids = tuple(row["capture_id"] for row in rows)
            if len(rows) != i + 1:
                failures.append(f"SNAPSHOT_{i + 1}:EXPECTED_{i + 1}_VISIBLE_CAPTURES")
            if ids[:len(previous_ids)] != previous_ids:
                failures.append(f"SNAPSHOT_{i + 1}:PRIOR_EVIDENCE_CHANGED")
            if len(ids) != len(set(ids)):
                failures.append(f"SNAPSHOT_{i + 1}:DUPLICATE_CAPTURE")
            for reason in receipt["blockers"]:
                failures.append(f"SNAPSHOT_{i + 1}:{reason}")
            if rows and len(rows) == i + 1 and not receipt["blockers"]:
                _match_market(rows[-1], context, cutoff, i, total)
            receipt["expected_visible_captures"] = i + 1
            receipt["verified_prefix_sha256"] = sha256(encoded(ids).encode()).hexdigest()
            receipts.append(receipt)
            previous_ids = ids
            final_rows = rows
    except (CaptureReplayError, ClockPolicyError):
        raise
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise CaptureReplayError("Variable capture source verification failed.") from exc

    council = None
    if not failures:
        need(final_rows is not None and len(final_rows) == total,
             "Final synthetic snapshot has incomplete capture history.")
        for i, row in enumerate(final_rows):
            _match_market(row, context, cutoffs[i], i, total)
        # Only replace the final tick/bar. Other Council context remains pinned
        # to the original synthetic fixture at the one final decision cutoff.
        payload = context.model_dump()
        payload["candles"] = (*context.candles[:-1], final_rows[-1]["bar"])
        payload["ticks"] = (*context.ticks[:-1], final_rows[-1]["tick"])
        rebuilt = AgentContext.model_validate(payload)
        council = run_shadow_council(rebuilt)
        need(council["council_seat_count"] == 31 and
             council["final_action"] == "HOLD" and
             council["execution_authorized"] is False,
             "Synthetic Council violated HOLD boundary.")

    report = {
        "assessment_version": VERSION,
        "capture_count_requested": total,
        "epoch_id": policy.epoch_id,
        "status": "SYNTHETIC_VARIABLE_REPLAY_HOLD" if council else "BLOCKED",
        "snapshot_receipts": receipts,
        "blockers": failures,
        "council_runs": 1 if council else 0,
        "council_seats": council["council_seat_count"] if council else 0,
        "council_sha256": council["report_sha256"] if council else None,
        "early_cutoff_council_runs": 0,
        "future_receipts_used": False,
        "last_market_inputs_source_mapped": council is not None,
        "other_council_inputs_synthetic": True,
        "independent_broker_attestation": False,
        "source_admitted_to_production": False,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    report["report_sha256"] = sha256(encoded(report).encode()).hexdigest()
    return report


def make_variable_demo(*, capture_count=5, fault="none"):
    """Create a newly isolated memory-only fixture; faults are for tests."""
    total = _count(capture_count)
    need(fault in ("none", "delayed", "missing", "overlap", "conflict", "reordered"),
         "Unsupported synthetic replay fault.")
    from backend.agents.test_full_research_council import synthetic_full_context
    context = synthetic_full_context()
    now = context.as_of
    first = now - FIVE_MINUTES * (total - 1)
    clock = [first]
    engine = create_engine("sqlite://")
    store = ForwardFeedStore(engine, clock=lambda: clock[0])
    policy = ClockPolicy(
        company_sha256=TEST_COMPANY,
        server_sha256=TEST_SERVER,
        evidence_sha256=TEST_EVIDENCE,
        implementation_sha256=implementation_sha256(),
        valid_from=first - timedelta(hours=1),
        valid_until=now + timedelta(days=1),
    )
    try:
        store.initialize()
        first_append = None
        for index in range(total):
            cutoff = first + FIVE_MINUTES * index
            receipt = (cutoff + timedelta(minutes=2)
                       if fault == "delayed" and index == total - 1
                       else cutoff)
            clock[0] = receipt
            candle = context.candles[-total + index]
            quote = next(t for t in context.ticks
                         if t.timestamp == cutoff - timedelta(seconds=10))
            need(candle.timestamp + FIVE_MINUTES == cutoff,
                 "Synthetic candle schedule changed.")
            wall_tick = utc_msc(hfm_utc_to_wall(quote.timestamp))
            wall_bar = utc_msc(hfm_utc_to_wall(candle.timestamp)) // 1000
            raw_tick = dict(time=wall_tick // 1000, time_msc=wall_tick,
                            bid=quote.bid, ask=quote.ask, last=0.0,
                            volume=0, flags=6, volume_real=0.0)
            raw_bar = dict(time=wall_bar, open=candle.open, high=candle.high,
                           low=candle.low, close=candle.close,
                           tick_volume=candle.tick_volume,
                           spread=candle.spread_points,
                           real_volume=int(candle.real_volume))
            kwargs = dict(tick_rows=[policy.tick(raw_tick)],
                          bar_rows=[policy.rate(raw_bar)], received_at=receipt,
                          tick_start=cutoff - FIVE_MINUTES, tick_end=cutoff,
                          bar_start=cutoff - timedelta(minutes=10), bar_end=cutoff)
            store.append(policy, **kwargs)
            if index == 0:
                first_append = kwargs
            if fault == "overlap" and index == 1:
                clock[0] = cutoff + timedelta(minutes=1)
                store.append(policy, **{**first_append, "received_at": clock[0]})
        if fault in ("missing", "conflict", "reordered"):
            with engine.begin() as conn:
                if fault == "missing":
                    middle = first + FIVE_MINUTES * (total // 2)
                    conn.execute(bars.delete().where(
                        bars.c.timestamp == middle - FIVE_MINUTES))
                elif fault == "conflict":
                    conn.execute(ticks.update().values(bid=2.0))
                else:
                    last_capture = conn.execute(
                        select(captures.c.capture_id).order_by(
                            captures.c.tick_end.desc()).limit(1)
                    ).scalar_one()
                    conn.execute(captures.update().where(
                        captures.c.capture_id == last_capture).values(
                            tick_start=now - timedelta(minutes=4)))
        clock[0] = now + (timedelta(minutes=2) if fault == "delayed"
                          else timedelta())
        return store, policy, context, engine
    except Exception:
        engine.dispose()
        raise


def _integrity_blocked(exc):
    report = {
        "status": "BLOCKED_INTEGRITY",
        "blockers": [str(exc)],
        "council_runs": 0,
        "council_seats": 0,
        "final_action": "HOLD",
        "training_authorized": False,
        "execution_authorized": False,
        "independent_broker_attestation": False,
    }
    report["report_sha256"] = sha256(encoded(report).encode()).hexdigest()
    return report


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--scenario", choices=("complete", "delayed", "missing",
                                               "overlap", "conflict", "reordered", "all"),
                        default="all")
    parser.add_argument("--captures", type=int, default=5)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        total = _count(args.captures)
    except CaptureReplayError as exc:
        print("VARIABLE REPLAY BLOCKED: " + str(exc))
        return 1
    names = (("complete", "delayed", "missing", "overlap", "conflict", "reordered")
             if args.scenario == "all" else (args.scenario,))
    records = []
    for name in names:
        engine = None
        try:
            fault = "none" if name == "complete" else name
            store, policy, context, engine = make_variable_demo(
                capture_count=total, fault=fault)
            try:
                report = replay_variable_captures(
                    store, policy, context, capture_count=total, synthetic_only=True)
            except (CaptureReplayError, ClockPolicyError) as exc:
                if name == "complete":
                    raise
                report = _integrity_blocked(exc)
            records.append({"scenario": name, "result": report})
        except (CaptureReplayError, ClockPolicyError, TypeError, ValueError) as exc:
            print("VARIABLE REPLAY BLOCKED: " + type(exc).__name__)
            return 1
        finally:
            if engine is not None:
                engine.dispose()
    if args.json:
        print(json.dumps(records, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Variable-length causal replay M45-48")
        for item in records:
            report = item["result"]
            print(f"SCENARIO {item['scenario']} | {report['status']} | "
                  f"snapshots={len(report.get('snapshot_receipts', []))} | "
                  f"council_runs={report['council_runs']} | "
                  f"seats={report['council_seats']} | FINAL=HOLD | "
                  f"digest={report['report_sha256']}")
            if report["blockers"]:
                print("  blockers=" + ",".join(report["blockers"]))
        print("IN-MEMORY SYNTHETIC ONLY | NO BROKER | NO FILES WRITTEN | "
              "REAL ADMISSION/TRAINING/EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
