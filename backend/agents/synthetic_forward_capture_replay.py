
"""M41-44: multi-capture synthetic-only causal replay through the real Council.

M41: verify capture manifests, first-capture links, continuity and source hashes.
M42: reconstruct three as-known-at-time snapshots without future receipts.
M43: fail closed on missing, delayed, duplicated, reordered or altered records.
M44: replace the last synthetic Council tick/bar, run 31 seats, enforce HOLD.

Never operates on disk or PostgreSQL databases. The setup helper WRITES solely to
new in-memory SQLite. The assessment makes SELECTs only. No real broker source,
independent receipt attestation, historical admission, training or execution.
"""

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json

from sqlalchemy import create_engine, select

from backend.agents.contracts import AgentContext
from backend.agents.forward_evidence_shadow import _raw_json
from backend.agents.synthetic_council_shadow import run_shadow_council
from backend.agents.synthetic_forward_causal_bridge import (
    TEST_COMPANY, TEST_SERVER, TEST_EVIDENCE,
)
from backend.data_engine.clock_policy import (
    ClockPolicy, ClockPolicyError, aware_utc, hfm_utc_to_wall,
    implementation_sha256, instant, utc_msc,
)
from backend.data_engine.forward_store import (
    ForwardFeedStore, epochs, captures, ticks, bars, encoded, hashed,
)
from backend.market_state.schemas import CandleBar, TickSample

VERSION = "EURUSD_SYNTHETIC_FORWARD_CAPTURE_REPLAY_V1"
FIVE_MINUTES = timedelta(minutes=5)
POINT = 0.0001


class CaptureReplayError(RuntimeError):
    """The synthetic replay's source or causal invariant cannot be established."""


def need(condition, message):
    if not condition:
        raise CaptureReplayError(message)


def _eligible(store, policy, context, synthetic_only):
    need(synthetic_only is True, "Explicit synthetic-only opt-in required.")
    need(isinstance(store, ForwardFeedStore) and isinstance(policy, ClockPolicy),
         "Typed store and policy required.")
    url = store.engine.url
    need(url.get_backend_name() == "sqlite" and url.database in (None, "", ":memory:")
         and url.host is None, "Only in-memory synthetic SQLite is supported.")
    need((policy.company_sha256, policy.server_sha256, policy.evidence_sha256) ==
         (TEST_COMPANY, TEST_SERVER, TEST_EVIDENCE), "Synthetic epoch identity not pinned.")
    need(isinstance(context, AgentContext), "Typed synthetic context required.")
    from backend.agents.test_full_research_council import synthetic_full_context
    known = synthetic_full_context()
    need(context.model_dump(mode="json") == known.model_dump(mode="json"),
         "Context differs from pinned synthetic fixture.")
    need(context.as_of.utcoffset() == timedelta(0), "Decision clock must be UTC.")
    policy.check_active(context.as_of)
    return known


def _manifest(capture, policy):
    doc = _raw_json(capture["manifest_json"])
    need(hashed(doc) == capture["capture_id"], "Capture manifest digest mismatch.")
    need(doc.get("schema_version") == 1 and doc.get("epoch_id") == policy.epoch_id,
         "Capture manifest epoch or schema changed.")
    for key in ("received_at", "tick_start", "tick_end"):
        need(doc.get(key) == capture[key].isoformat(),
             "Capture manifest time differs from stored time.")
    need(doc.get("bar_start") is not None and doc.get("bar_end") is not None,
         "Missing bar request interval.")
    start, end = (datetime.fromisoformat(doc[key]) for key in ("bar_start", "bar_end"))
    need(start.utcoffset() == timedelta(0) and end.utcoffset() == timedelta(0) and
         policy.valid_from <= start <= end <= policy.valid_until and
         end <= capture["received_at"], "Invalid bar request interval.")
    need(doc.get("tick_count") == 1 and doc.get("bar_count") == 1,
         "Unreconstructable capture membership.")
    need(policy.valid_from <= capture["tick_start"] < capture["tick_end"] <=
         capture["received_at"] <= capture["ingested_at"] and
         capture["ingested_at"] < policy.valid_until,
         "Capture clock chronology invalid.")
    return doc, start, end


def _decode_row(row, capture, doc, policy, kind, bar_start, bar_end):
    need(row["epoch_id"] == policy.epoch_id and
         row["first_capture_id"] == capture["capture_id"] and
         row["received_at"] == capture["received_at"] and
         capture["received_at"] <= row["ingested_at"] and
         row["ingested_at"] >= capture["ingested_at"],
         "Source first-capture receipt linkage invalid.")
    raw = _raw_json(row["raw_json"])
    if kind == "tick":
        normalized = policy.tick(raw)
        need(hashed([normalized]) == doc["ticks_sha256"] and
             hashed({"epoch_id": policy.epoch_id, "raw": raw}) == row["fingerprint"] and
             row["raw_time_msc"] == raw["time_msc"] and
             instant(normalized["time_msc"]) == row["timestamp"] and
             (normalized["bid"], normalized["ask"]) == (row["bid"], row["ask"]) and
             capture["tick_start"] <= row["timestamp"] < capture["tick_end"] and
             row["timestamp"] <= row["received_at"],
             "Tick raw provenance or capture window changed.")
        item = TickSample(symbol="EURUSD", timestamp=row["timestamp"],
                          source_time_msc=row["raw_time_msc"], bid=row["bid"],
                          ask=row["ask"], spread_pips=(row["ask"]-row["bid"])/POINT)
        key = row["fingerprint"]
    else:
        normalized = policy.rate(raw)
        need(hashed([normalized]) == doc["bars_sha256"] and
             hashed({"epoch_id": policy.epoch_id, "time": normalized["time"]}) == row["bar_id"] and
             row["raw_open_time"] == raw["time"] and
             instant(normalized["time"] * 1000) == row["timestamp"] and
             all(row[k] == normalized[k] for k in ("open", "high", "low", "close")) and
             bar_start <= row["timestamp"] < bar_end and
             row["timestamp"] + FIVE_MINUTES <= row["received_at"],
             "Candle raw provenance, window or closure changed.")
        item = CandleBar(symbol="EURUSD", timeframe="M5", timestamp=row["timestamp"],
                         open=row["open"], high=row["high"], low=row["low"],
                         close=row["close"], tick_volume=raw["tick_volume"],
                         spread_points=raw["spread"], real_volume=raw["real_volume"],
                         source="MT5")
        key = row["bar_id"]
    return item, key


def _read_snapshot(store, policy, cutoff):
    """Single SQL transaction. Only evidence actually ingested by cutoff qualifies."""
    need(cutoff.utcoffset() == timedelta(0), "Replay cutoff must be UTC.")
    with store.engine.connect() as conn:
        with conn.begin():
            epoch = conn.execute(select(epochs).where(
                epochs.c.epoch_id == policy.epoch_id)).mappings().one_or_none()
            need(epoch is not None and
                 epoch["policy_json"] == encoded(policy.model_dump(mode="json")) and
                 epoch["valid_from"] == policy.valid_from and
                 epoch["valid_until"] == policy.valid_until and
                 epoch["recorded_at"] <= cutoff,
                 "Epoch provenance not known at decision time.")
            captured = conn.execute(select(captures).where(
                captures.c.epoch_id == policy.epoch_id,
                captures.c.ingested_at <= cutoff,
                captures.c.received_at <= cutoff,
            ).order_by(captures.c.tick_end, captures.c.capture_id)).mappings().all()
            tick_rows = conn.execute(select(ticks).where(
                ticks.c.epoch_id == policy.epoch_id,
                ticks.c.ingested_at <= cutoff,
                ticks.c.received_at <= cutoff,
            )).mappings().all()
            bar_rows = conn.execute(select(bars).where(
                bars.c.epoch_id == policy.epoch_id,
                bars.c.ingested_at <= cutoff,
                bars.c.received_at <= cutoff,
            )).mappings().all()
    need(len(captured) <= 8 and len(tick_rows) <= 8 and len(bar_rows) <= 8,
         "Synthetic capture replay exceeds bounded input.")
    by_type = {}
    for name, rows in (("tick", tick_rows), ("bar", bar_rows)):
        grouped = {}
        for row in rows:
            grouped.setdefault(row["first_capture_id"], []).append(row)
        by_type[name] = grouped
    ids = {c["capture_id"] for c in captured}
    need(all(set(grouped).issubset(ids) for grouped in by_type.values()),
         "Orphan first-capture source row.")
    outcomes, prior = [], None
    for cap in captured:
        doc, bar_start, bar_end = _manifest(cap, policy)
        if prior is not None:
            need(cap["tick_start"] == prior["tick_end"],
                 "Tick capture intervals contain a gap or overlap.")
            need(cap["received_at"] >= prior["received_at"] and
                 cap["ingested_at"] >= prior["ingested_at"],
                 "Capture clock regressed.")
        selected = {}
        for name in ("tick", "bar"):
            rows = by_type[name].get(cap["capture_id"], [])
            need(len(rows) == 1, "Duplicate or unprovable capture membership.")
            item, ident = _decode_row(rows[0], cap, doc, policy, name,
                                      bar_start, bar_end)
            selected[name] = item
            selected[name + "_source_id"] = ident
            selected[name + "_received_at"] = rows[0]["received_at"]
            selected[name + "_ingested_at"] = rows[0]["ingested_at"]
        if outcomes:
            before = outcomes[-1]
            need(selected["tick"].timestamp > before["tick"].timestamp,
                 "Tick event clock regressed.")
            need(selected["bar"].timestamp == before["bar"].timestamp + FIVE_MINUTES,
                 "M5 candle gap, duplication or clock discontinuity.")
        outcomes.append({"capture_id": cap["capture_id"],
                         "capture_ingested_at": cap["ingested_at"], **selected})
        prior = cap
    return outcomes


def _receipt(cutoff, outcomes):
    """Never backfill from a later receipt, even if its event time is earlier."""
    if not outcomes:
        return {"as_of": cutoff.isoformat(), "status": "BLOCKED",
                "blockers": ["CAPTURE_NOT_YET_INGESTED"], "visible_captures": 0,
                "tick_source_id": None, "bar_source_id": None}
    latest = outcomes[-1]
    tick = latest["tick"]
    bar = latest["bar"]
    blockers = []
    if (cutoff - tick.timestamp).total_seconds() > 120:
        blockers.append("TICK_STALE")
    if bar.timestamp != cutoff.replace(
        minute=(cutoff.minute // 5) * 5, second=0, microsecond=0
    ) - FIVE_MINUTES:
        blockers.append("M5_NOT_CURRENT")
    if any(latest[k] > cutoff for k in (
        "tick_received_at", "bar_received_at", "tick_ingested_at", "bar_ingested_at"
    )):
        blockers.append("FUTURE_RECEIPT")
    return {"as_of": cutoff.isoformat(), "status": "BLOCKED" if blockers else
            "LOCAL_SYNTHETIC_SNAPSHOT_ONLY", "blockers": blockers,
            "visible_captures": len(outcomes), "tick_source_id": latest["tick_source_id"],
            "bar_source_id": latest["bar_source_id"],
            "first_capture_id": latest["capture_id"]}


def replay_synthetic_captures(store, policy, context, *, synthetic_only=False):
    """Strictly source-backed three-cutoff replay. Council only at final cutoff."""
    _eligible(store, policy, context, synthetic_only)
    points = tuple(context.as_of - timedelta(minutes=i) for i in (10, 5, 0))
    receipts = []
    final_rows = None
    try:
        for cutoff in points:
            rows = _read_snapshot(store, policy, cutoff)
            item = _receipt(cutoff, rows)
            receipts.append(item)
            if cutoff == context.as_of:
                final_rows = rows
    except (CaptureReplayError, ClockPolicyError):
        raise
    except (ValueError, TypeError, KeyError, IndexError, OverflowError) as exc:
        raise CaptureReplayError("Synthetic source verification failed.") from exc

    failures = [f"SNAPSHOT_{i + 1}:{reason}" for i, r in enumerate(receipts)
                for reason in r["blockers"]]
    if final_rows is None or len(final_rows) != 3:
        failures.append("EXPECTED_THREE_FIRST_CAPTURE_BATCHES")
    council = None
    if not failures:
        for i, item in enumerate(final_rows):
            ref_bar = context.candles[-3 + i]
            ref_tick = next((t for t in context.ticks
                             if t.timestamp == points[i] - timedelta(seconds=10)), None)
            need(item["bar"] == ref_bar and ref_tick is not None,
                 "Replay candle differs from pinned synthetic context.")
            q = item["tick"]
            need(q.timestamp == ref_tick.timestamp and q.bid == ref_tick.bid and
                 q.ask == ref_tick.ask and
                 abs(q.spread_pips - ref_tick.spread_pips) < 1e-7,
                 "Replay quote differs from pinned synthetic context.")
        payload = context.model_dump()
        payload["candles"] = (*context.candles[:-1], final_rows[-1]["bar"])
        payload["ticks"] = (*context.ticks[:-1], final_rows[-1]["tick"])
        rebuilt = AgentContext.model_validate(payload)
        council = run_shadow_council(rebuilt)
        need(council["final_action"] == "HOLD" and
             council["execution_authorized"] is False and
             council["council_seat_count"] == 31,
             "Council did not enforce synthetic HOLD.")
    output = {
        "assessment_version": VERSION,
        "epoch_id": policy.epoch_id,
        "snapshot_receipts": receipts,
        "status": "SYNTHETIC_THREE_CAPTURE_SHADOW_ONLY" if council else "BLOCKED",
        "blockers": failures,
        "council_invoked": council is not None,
        "council_seats": council["council_seat_count"] if council else 0,
        "council_report_sha256": council["report_sha256"] if council else None,
        "market_inputs_from_verified_synthetic_captures": council is not None,
        "other_council_inputs_remain_synthetic": True,
        "source_provenance_independently_attested": False,
        "real_forward_admission": False,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    output["report_sha256"] = sha256(encoded(output).encode()).hexdigest()
    return output


def make_replay_demo(*, fault="none"):
    """Fixture writer only: 3 adjacent capture intervals in NEW memory SQLite."""
    need(fault in ("none", "delayed", "duplicate"), "Unsupported demo fault.")
    from backend.agents.test_full_research_council import synthetic_full_context
    ctx = synthetic_full_context()
    now = ctx.as_of
    clock = [now - timedelta(minutes=10)]
    engine = create_engine("sqlite://")
    store = ForwardFeedStore(engine, clock=lambda: clock[0])
    policy = ClockPolicy(company_sha256=TEST_COMPANY, server_sha256=TEST_SERVER,
                         evidence_sha256=TEST_EVIDENCE,
                         implementation_sha256=implementation_sha256(),
                         valid_from=now - timedelta(hours=1),
                         valid_until=now + timedelta(days=1))
    store.initialize()
    raw_first = None
    for i, back in enumerate((10, 5, 0)):
        end = now - timedelta(minutes=back)
        receipt = end + (timedelta(minutes=3) if fault == "delayed" and i == 2
                         else timedelta())
        clock[0] = receipt
        candle = ctx.candles[-3 + i]
        quote = next(t for t in ctx.ticks
                     if t.timestamp == end - timedelta(seconds=10))
        need(candle.timestamp == end - FIVE_MINUTES,
             "Synthetic candle fixture has changed.")
        raw_tick_ms = utc_msc(hfm_utc_to_wall(quote.timestamp))
        raw_bar_s = utc_msc(hfm_utc_to_wall(candle.timestamp)) // 1000
        raw_tick = dict(time=raw_tick_ms // 1000, time_msc=raw_tick_ms,
                        bid=quote.bid, ask=quote.ask, last=0.0, volume=0,
                        flags=6, volume_real=0.0)
        raw_bar = dict(time=raw_bar_s, open=candle.open, high=candle.high,
                       low=candle.low, close=candle.close,
                       tick_volume=candle.tick_volume, spread=candle.spread_points,
                       real_volume=int(candle.real_volume))
        kwargs = dict(tick_rows=[policy.tick(raw_tick)], bar_rows=[policy.rate(raw_bar)],
                      received_at=receipt, tick_start=end - FIVE_MINUTES,
                      tick_end=end, bar_start=end - timedelta(minutes=10), bar_end=end)
        store.append(policy, **kwargs)
        if i == 0:
            raw_first = kwargs
        if fault == "duplicate" and i == 1:
            clock[0] = end + timedelta(minutes=1)
            store.append(policy, **{**raw_first, "received_at": clock[0]})
    clock[0] = now + (timedelta(minutes=3) if fault == "delayed" else timedelta())
    return store, policy, ctx, engine


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--scenario", choices=("complete", "delayed", "duplicate", "all"),
                        default="all")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    names = ("complete", "delayed", "duplicate") if args.scenario == "all" else (args.scenario,)
    results = []
    for name in names:
        engine = None
        try:
            store, policy, ctx, engine = make_replay_demo(
                fault={"complete": "none"}.get(name, name))
            try:
                report = replay_synthetic_captures(store, policy, ctx, synthetic_only=True)
            except (CaptureReplayError, ClockPolicyError) as exc:
                # Integrity violations are terminal. This is a test-only
                # negative scenario; preserve the actual verification failure.
                if name != "duplicate":
                    raise
                report = {"status": "BLOCKED_INTEGRITY", "blockers": [str(exc)],
                          "council_invoked": False, "council_seats": 0,
                          "final_action": "HOLD", "execution_authorized": False,
                          "training_authorized": False}
                report["report_sha256"] = sha256(encoded(report).encode()).hexdigest()
            results.append({"scenario": name, "result": report})
        except (CaptureReplayError, ClockPolicyError, ValueError, TypeError) as exc:
            print("CAPTURE REPLAY BLOCKED: " + type(exc).__name__)
            return 1
        finally:
            if engine is not None:
                engine.dispose()
    if args.json:
        print(json.dumps(results, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Multi-capture synthetic replay M41-44")
        for item in results:
            result = item["result"]
            print(f"SCENARIO {item['scenario']} | {result['status']} | "
                  f"snapshots={len(result.get('snapshot_receipts', []))} | "
                  f"seats={result['council_seats']} | FINAL=HOLD | "
                  f"digest={result['report_sha256']}")
            if result["blockers"]:
                print("  blockers=" + ",".join(result["blockers"]))
        print("IN-MEMORY SYNTHETIC ONLY | SOURCE RECEIPTS NOT EXTERNALLY ATTESTED | "
              "NO BROKER | NO FILES WRITTEN | TRAINING/EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
