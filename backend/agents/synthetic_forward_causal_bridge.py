
"""M37-40: verified synthetic forward-row mapping into the existing 31-seat Council.

Only a new in-memory SQLite fixture is supported. One closed bar and one tick
are reconstructed as actual Council types, compared to the synthetic market
state, and substituted before research-only shadow dispatch. Remaining context
is synthetic. No broker admission, production, model fitting, persistence, or
execution. Stored local receipts are not independent provenance attestation.
"""

import argparse
from datetime import timedelta
from hashlib import sha256
import json

from sqlalchemy import select

from backend.agents.contracts import AgentContext
from backend.agents.synthetic_council_shadow import run_shadow_council
from backend.agents.forward_evidence_shadow import inspect_forward_evidence, _raw_json
from backend.data_engine.clock_policy import ClockPolicy, hfm_utc_to_wall, utc_msc
from backend.data_engine.forward_store import (
    ForwardFeedStore, bars, captures, encoded, hashed, ticks,
)
from backend.market_state.schemas import CandleBar, TickSample

VERSION = "EURUSD_SYNTHETIC_FORWARD_CAUSAL_BRIDGE_V1"
PIP = 0.0001
TEST_COMPANY = sha256(b"SYNTHETIC_COMPANY").hexdigest()
TEST_SERVER = sha256(b"SYNTHETIC_SERVER").hexdigest()
TEST_EVIDENCE = sha256(b"M37-40 TEST FIXTURE ONLY").hexdigest()


class CausalBridgeError(RuntimeError):
    """An input or source-to-Council invariant failed closed."""


def require(condition, message):
    if not condition:
        raise CausalBridgeError(message)


def source_rows(store, policy, as_of, evidence):
    """Read exactly one first-captured tick and bar, with full manifest proof."""
    mapped = {}
    with store.engine.connect() as conn:
        with conn.begin():
            for name, table, primary in (
                ("tick", ticks, "fingerprint"), ("bar", bars, "bar_id")
            ):
                rows = conn.execute(select(table).where(
                    table.c.epoch_id == policy.epoch_id,
                    table.c.ingested_at <= as_of,
                ).limit(2)).mappings().all()
                require(len(rows) == 1, "Expected one synthetic " + name)
                row = rows[0]
                proof = evidence["latest_records"][name]
                require(proof is not None and
                        row["timestamp"].isoformat() == proof["event_time_utc"] and
                        row["first_capture_id"] == proof["first_capture_id"] and
                        row["received_at"].isoformat() == proof["recorded_received_at"] and
                        row["ingested_at"].isoformat() == proof["recorded_ingested_at"],
                        "Source record changed after evidence gate")
                cap = conn.execute(select(captures).where(
                    captures.c.capture_id == row["first_capture_id"]
                )).mappings().one_or_none()
                require(cap is not None and cap["epoch_id"] == policy.epoch_id,
                        "First capture missing")
                manifest = _raw_json(cap["manifest_json"])
                require(hashed(manifest) == cap["capture_id"] and
                        manifest["epoch_id"] == policy.epoch_id and
                        manifest["schema_version"] == 1 and
                        manifest["tick_count"] == manifest["bar_count"] == 1 and
                        manifest["received_at"] == cap["received_at"].isoformat() and
                        cap["received_at"] <= cap["ingested_at"] <= as_of and
                        row["received_at"] == cap["received_at"] and
                        row["received_at"] <= row["ingested_at"] <= as_of,
                        "Capture receipt or manifest invalid")
                raw = _raw_json(row["raw_json"])
                normalized = policy.tick(raw) if name == "tick" else policy.rate(raw)
                digest_field = "ticks_sha256" if name == "tick" else "bars_sha256"
                require(manifest[digest_field] == hashed([normalized]),
                        "Capture content digest mismatch")
                if name == "tick":
                    require(hashed({"epoch_id": policy.epoch_id, "raw": raw}) ==
                            row["fingerprint"] and
                            row["raw_time_msc"] == raw["time_msc"] and
                            row["timestamp"] == as_of - timedelta(seconds=10) and
                            row["bid"] == normalized["bid"] and
                            row["ask"] == normalized["ask"] and
                            cap["tick_start"] <= row["timestamp"] < cap["tick_end"],
                            "Mapped tick not source-equivalent")
                    mapped[name] = TickSample(
                        symbol="EURUSD", timestamp=row["timestamp"],
                        source_time_msc=row["raw_time_msc"],
                        bid=row["bid"], ask=row["ask"],
                        spread_pips=(row["ask"]-row["bid"])/PIP,
                    )
                else:
                    require(hashed({"epoch_id": policy.epoch_id,
                                    "time": normalized["time"]}) == row["bar_id"] and
                            row["raw_open_time"] == raw["time"] and
                            row["timestamp"] == as_of - timedelta(minutes=5) and
                            row["timestamp"] + timedelta(minutes=5) <= row["received_at"] and
                            cap["received_at"] >= row["timestamp"] + timedelta(minutes=5) and
                            cap["received_at"].isoformat() >= manifest["bar_end"] and
                            manifest["bar_start"] <= row["timestamp"].isoformat() < manifest["bar_end"] and
                            all(row[key] == normalized[key] for key in
                                ("open", "high", "low", "close")),
                            "Mapped candle not source-equivalent or not closed")
                    mapped[name] = CandleBar(
                        symbol="EURUSD", timeframe="M5", timestamp=row["timestamp"],
                        open=row["open"], high=row["high"], low=row["low"],
                        close=row["close"], tick_volume=raw["tick_volume"],
                        spread_points=raw["spread"],
                        real_volume=raw["real_volume"], source="MT5",
                    )
                mapped[name + "_capture"] = cap["capture_id"]
                mapped[name + "_source"] = row[primary]
    return mapped


def report_for(evidence, status, council=None, mapped=None):
    result = {
        "assessment_version": VERSION,
        "status": status,
        "blockers": list(evidence["blockers"]),
        "forward_evidence_sha256": evidence["evidence_sha256"],
        "mapped_tick_id": mapped["tick_source"] if mapped else None,
        "mapped_candle_id": mapped["bar_source"] if mapped else None,
        "first_capture_ids": (
            [mapped["tick_capture"], mapped["bar_capture"]] if mapped else []
        ),
        "mapped_synthetic_records_in_council": council is not None,
        "remaining_context_is_synthetic": True,
        "canonical_council_invoked": council is not None,
        "council_seats": council["council_seat_count"] if council else 0,
        "council_sha256": council["report_sha256"] if council else None,
        "independent_broker_attestation": False,
        "historical_admission": False,
        "production_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = sha256(encoded(result).encode()).hexdigest()
    return result


def run_causal_synthetic_bridge(store, policy, context, *, synthetic_only=False):
    """Bridge 2 source rows only after checking against exact synthetic fixture.

    Never accepts a disk file or PostgreSQL store; no real source can be
    admitted through this interface. A caller declaration alone is insufficient.
    """
    require(synthetic_only is True, "Synthetic-only opt-in required")
    require(isinstance(store, ForwardFeedStore) and isinstance(policy, ClockPolicy),
            "Typed synthetic forward store and policy required")
    url = store.engine.url
    require(url.get_backend_name() == "sqlite" and
            url.database in (None, "", ":memory:") and url.host is None,
            "Only in-memory synthetic stores are supported")
    require(policy.company_sha256 == TEST_COMPANY and
            policy.server_sha256 == TEST_SERVER and
            policy.evidence_sha256 == TEST_EVIDENCE,
            "Synthetic clock identity not pinned")
    require(isinstance(context, AgentContext), "Typed synthetic Council context required")
    from backend.agents.test_full_research_council import synthetic_full_context
    expected = synthetic_full_context()
    require(context.model_dump(mode="json") == expected.model_dump(mode="json"),
            "Context differs from canonical synthetic fixture")
    require(context.as_of.utcoffset() == timedelta(0) and
            len(context.candles) >= 140 and len(context.ticks) > 0,
            "Invalid synthetic market inputs")

    evidence = inspect_forward_evidence(store, policy, context.as_of)
    if evidence["blockers"]:
        return report_for(evidence, "BLOCKED")
    try:
        mapped = source_rows(store, policy, context.as_of, evidence)
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CausalBridgeError("Synthetic source mapping failed") from exc
    require(mapped["bar"] == context.candles[-1],
            "Mapped bar differs from market-state anchor")
    expected_tick = context.ticks[-1]
    actual_tick = mapped["tick"]
    require(actual_tick.timestamp == expected_tick.timestamp and
            actual_tick.bid == expected_tick.bid and
            actual_tick.ask == expected_tick.ask and
            abs(actual_tick.spread_pips - expected_tick.spread_pips) < 1e-7,
            "Mapped tick differs from market-state quote")
    # The source_time_msc field is the original broker-wall label, not the
    # normalized timestamp used by the synthetic historical replay fixture.
    payload = context.model_dump()
    payload["candles"] = (*context.candles[:-1], mapped["bar"])
    payload["ticks"] = (*context.ticks[:-1], actual_tick)
    try:
        updated = AgentContext.model_validate(payload)
    except ValueError as exc:
        raise CausalBridgeError("Mapped Council context invalid") from exc
    result = run_shadow_council(updated)
    require(result["final_action"] == "HOLD" and
            result["execution_authorized"] is False and
            result["council_seat_count"] == 31,
            "Canonical Council must remain non-executing")
    return report_for(evidence, "SYNTHETIC_MATCHED_SHADOW_ONLY", result, mapped)


def make_synthetic_demo(*, stale=False):
    """Create a fully separate, deterministic, never-persisted test store."""
    from sqlalchemy import create_engine
    from backend.data_engine.clock_policy import implementation_sha256
    from backend.agents.test_full_research_council import synthetic_full_context
    ctx = synthetic_full_context()
    now = ctx.as_of
    observation_time = now + timedelta(minutes=3) if stale else now
    engine = create_engine("sqlite://")
    store = ForwardFeedStore(engine, clock=lambda: observation_time)
    policy = ClockPolicy(
        company_sha256=TEST_COMPANY, server_sha256=TEST_SERVER,
        evidence_sha256=TEST_EVIDENCE,
        implementation_sha256=implementation_sha256(),
        valid_from=now - timedelta(hours=2),
        valid_until=now + timedelta(days=1),
    )
    store.initialize()
    tick, candle = ctx.ticks[-1], ctx.candles[-1]
    require(tick.timestamp == now - timedelta(seconds=10) and
            candle.timestamp == now - timedelta(minutes=5),
            "Synthetic fixture source anchors changed")
    tick_wall = utc_msc(hfm_utc_to_wall(tick.timestamp))
    candle_wall = utc_msc(hfm_utc_to_wall(candle.timestamp)) // 1000
    raw_tick = dict(time=tick_wall // 1000, time_msc=tick_wall,
                    bid=tick.bid, ask=tick.ask, last=0.0,
                    volume=0, flags=6, volume_real=0.0)
    raw_bar = dict(time=candle_wall, open=candle.open, high=candle.high,
                   low=candle.low, close=candle.close,
                   tick_volume=candle.tick_volume, spread=candle.spread_points,
                   real_volume=int(candle.real_volume))
    store.append(policy, tick_rows=[policy.tick(raw_tick)],
                 bar_rows=[policy.rate(raw_bar)], received_at=now,
                 tick_start=now-timedelta(minutes=1), tick_end=now,
                 bar_start=now-timedelta(minutes=10), bar_end=now)
    return store, policy, ctx, engine


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--scenario", choices=("matched", "stale", "both"),
                        default="both")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    names = ("matched", "stale") if args.scenario == "both" else (args.scenario,)
    rows = []
    for name in names:
        engine = None
        try:
            store, policy, ctx, engine = make_synthetic_demo(stale=name == "stale")
            if name == "stale":
                # The later as_of is not the synthetic fixture's Council as_of.
                # Check freshness only, and never dispatch across this mismatch.
                evidence = inspect_forward_evidence(
                    store, policy, ctx.as_of + timedelta(minutes=3))
                require(bool(evidence["blockers"]), "Stale feed was not rejected")
                result = report_for(evidence, "BLOCKED")
            else:
                result = run_causal_synthetic_bridge(
                    store, policy, ctx, synthetic_only=True)
            rows.append({"scenario": name, "result": result})
        except (CausalBridgeError, ValueError, TypeError) as exc:
            print("SYNTHETIC CAUSAL BRIDGE BLOCKED: " + type(exc).__name__)
            return 1
        finally:
            if engine is not None:
                engine.dispose()
    if args.json:
        print(json.dumps(rows, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Synthetic forward causal bridge M37-40")
        for entry in rows:
            result = entry["result"]
            print(f"SCENARIO {entry['scenario']} | {result['status']} | "
                  f"mapped={result['mapped_synthetic_records_in_council']} | "
                  f"seats={result['council_seats']} | FINAL=HOLD | "
                  f"digest={result['report_sha256']}")
            if result["blockers"]:
                print("  blockers=" + ",".join(result["blockers"]))
        print("IN-MEMORY SYNTHETIC ONLY | NO BROKER | NO FILES WRITTEN | "
              "REAL ADMISSION/TRAINING/EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
