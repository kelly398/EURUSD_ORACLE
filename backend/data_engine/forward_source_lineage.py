
"""Phase 2 B4+C1-C3: read-only forward recency, clock and Council-source inventory.

Uses the V2 forward inspector as the ONLY database reader. All results use its
single, bounded read-only transaction. The typed Council schema is an interface,
NOT evidence of a live source, external first availability or real admission.

No recorder process is started; no policy epoch is amended; no training or
trading function is invoked. Final action is unconditionally HOLD.
"""

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json

from backend.agents.contracts import AgentContext
from backend.agents.council_inputs import (
    AgentReliability, CalendarCoverage, CrossAssetObservation,
    PortfolioRiskSnapshot, RiskLimits, SynthesisPolicy,
)
from backend.institutional.cot_schemas import COTPositioningSnapshot
from backend.macro.macro_schemas import (
    CentralBankRateSnapshot, EconomicCalendarEventSnapshot,
)
from backend.market_state.schemas import CandleBar, MarketStateSnapshot, TickSample
from backend.data_engine.forward_evidence_health import (
    VERSION as HEALTH_VERSION, _parse_as_of, _require_epoch_id,
    inspect_forward_evidence,
)
from backend.data_engine.forward_store import encoded

VERSION = "EURUSD_FORWARD_SOURCE_LINEAGE_PHASE2_V1"

# Candidate contract classes. They describe what the Council accepts, not
# where any production data actually came from.
FAMILY_MODELS = (
    ("market_state", MarketStateSnapshot, "DERIVED_MARKET_STATE"),
    ("candles", CandleBar, "FORWARD_UTC_BAR_CANDIDATE"),
    ("ticks", TickSample, "FORWARD_UTC_TICK_CANDIDATE"),
    ("cot_data", COTPositioningSnapshot, "EXTERNAL_COT_VERSION_REQUIRED"),
    ("ecb_data", CentralBankRateSnapshot, "EXTERNAL_ECB_VERSION_REQUIRED"),
    ("fed_data", CentralBankRateSnapshot, "EXTERNAL_FED_VERSION_REQUIRED"),
    ("economic_calendar", EconomicCalendarEventSnapshot, "EXTERNAL_CALENDAR_VERSION_REQUIRED"),
    ("cross_asset_data", CrossAssetObservation, "EXTERNAL_CROSS_ASSET_VERSION_REQUIRED"),
    ("calendar_coverage", CalendarCoverage, "CALENDAR_COMPLETENESS_REQUIRED"),
    ("risk_state", PortfolioRiskSnapshot, "RECONCILED_RISK_ORIGIN_REQUIRED"),
    ("risk_limits", RiskLimits, "OPERATOR_POLICY_VERSION_REQUIRED"),
    ("reliability", AgentReliability, "PROSPECTIVE_EVALUATION_VERSION_REQUIRED"),
    ("synthesis_policy", SynthesisPolicy, "OPERATOR_SYNTHESIS_VERSION_REQUIRED"),
)

TIME_FIELDS = (
    "as_of", "timestamp", "available_at", "ingested_at", "observation_at",
    "previous_observation_at", "scheduled_at", "start", "end", "report_date",
    "reference_date",
)
PROVENANCE_FIELDS = (
    "source", "content_hash", "source_record_id", "source_event_id",
    "evaluation_id", "source_time_msc",
)


class ForwardLineageError(RuntimeError):
    """Required interface or evidence invariant is not established."""


def _digest(value):
    return sha256(encoded(value).encode("utf-8")).hexdigest()


def inventory_council_sources():
    """Audit the ACTUAL typed field inventory without populating an AgentContext."""
    declared = set(AgentContext.model_fields)
    wanted = {name for name, _, _ in FAMILY_MODELS}
    if not wanted.issubset(declared) or len(wanted) != 13:
        raise ForwardLineageError("Council input family contract changed.")
    families = []
    for name, model, requirement in FAMILY_MODELS:
        fields = set(model.model_fields)
        families.append({
            "family": name,
            "typed_model": model.__name__,
            "timestamp_fields": sorted(fields.intersection(TIME_FIELDS)),
            "provenance_fields": sorted(fields.intersection(PROVENANCE_FIELDS)),
            "has_ingestion_field": "ingested_at" in fields,
            "has_public_availability_field": "available_at" in fields,
            "required_external_or_derived_evidence": requirement,
            "real_source_identity_verified": False,
            "real_first_receipt_attested": False,
            "real_point_in_time_lineage_proven": False,
            "status": "TYPED_CONTRACT_ONLY_UNVERIFIED",
        })
    return families


def _utc_age_seconds(cutoff, value):
    if value is None:
        return None
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None or stamp.utcoffset() != timedelta(0):
        raise ForwardLineageError("Source inspection produced a non-UTC instant.")
    delta = cutoff - stamp
    if delta < timedelta(0):
        raise ForwardLineageError("Source inspection included a future timestamp.")
    return round(delta.total_seconds(), 3)


def evaluate_source_lineage(health, *, max_capture_age_seconds=900):
    """Pure review of a V2 read-only result; never connects to the database."""
    if type(max_capture_age_seconds) is not int or not 60 <= max_capture_age_seconds <= 86400:
        raise ForwardLineageError("Capture age threshold must be 60..86400 seconds.")
    if not isinstance(health, dict) or health.get("assessment_version") != HEALTH_VERSION:
        raise ForwardLineageError("V2 health inspection receipt required.")
    original_hash = health.get("report_sha256")
    if not isinstance(original_hash, str) or original_hash != _digest({
        k: v for k, v in health.items() if k != "report_sha256"
    }):
        raise ForwardLineageError("Source health report integrity invalid.")
    if health.get("final_action") != "HOLD" or health.get("execution_authorized") is not False:
        raise ForwardLineageError("Source health receipt violated HOLD boundary.")

    cutoff = _parse_as_of(health["as_of"])
    captures = health.get("captures")
    if not isinstance(captures, list) or len(captures) != health.get("captures_examined"):
        raise ForwardLineageError("Capture summary count differs from health receipt.")
    for row in captures:
        if not isinstance(row, dict):
            raise ForwardLineageError("Invalid capture summary.")

    last = max(captures, key=lambda row: (row["tick_end"], row["received_at"])) if captures else None
    last_capture_end = last["tick_end"] if last else None
    last_received = max((row["received_at"] for row in captures), default=None)
    last_ingested = max((row["ingested_at"] for row in captures), default=None)
    lag = _utc_age_seconds(cutoff, last_ingested)

    # This is a local *observation*, not a claim the collector process crashed.
    observations = []
    if lag is None:
        observations.append("NO_VISIBLE_CAPTURE_AT_CUTOFF")
    elif lag > max_capture_age_seconds:
        observations.append("NO_RECENT_CAPTURE_INGESTION_AT_CUTOFF")
    if health.get("overlap_query_count", 0):
        observations.append("OVERLAPPING_QUERY_WINDOWS_NOT_A_SOURCE_FAILURE")
    if health.get("unqueried_gap_count", 0):
        observations.append("UNQUERIED_INTERVALS_REQUIRE_INVESTIGATION")

    bound = health.get("bound_clock_implementation_sha256")
    current = health.get("local_clock_implementation_sha256")
    if not isinstance(bound, str) or not isinstance(current, str):
        raise ForwardLineageError("Missing clock-version hashes.")
    clock_equal = bound == current
    clock = {
        "bound_implementation_sha256": bound,
        "local_implementation_sha256": current,
        "same_local_code_hash": clock_equal,
        "status": (
            "LOCAL_CODE_HASH_MATCH_UNATTESTED" if clock_equal
            else "BLOCKED_BOUND_IMPLEMENTATION_DIFFERS"
        ),
        "stored_source_bytes_verified_against_external_anchor": False,
        "historical_broker_utc_certified": False,
        "change_cause_determined": False,
        "remediation": (
            "VERIFY_ORIGINAL_CLOCK_CODE_FROM_TRUSTED_SOURCE_CONTROL_READ_ONLY"
            if not clock_equal else "NO_LOCAL_HASH_DRIFT_OBSERVED"
        ),
    }
    family_rows = inventory_council_sources()
    result = {
        "assessment_version": VERSION,
        "epoch_id": _require_epoch_id(health["epoch_id"]),
        "as_of": cutoff.isoformat(),
        "source_health_sha256": original_hash,
        "health_local_status": health["local_evidence_status"],
        "health_blockers": list(health["blockers"]),
        "health_limitations": list(health["evidence_limitations"]),
        "capture_count_in_bounded_sample": len(captures),
        "capture_sample_truncated": bool(health["sample_truncated"]),
        "last_visible_capture_query_end": last_capture_end,
        "last_visible_capture_received_at": last_received,
        "last_visible_capture_ingested_at": last_ingested,
        "seconds_since_capture_ingestion": lag,
        "capture_age_threshold_seconds": max_capture_age_seconds,
        "last_tick": health.get("latest_tick"),
        "seconds_since_tick": _utc_age_seconds(cutoff, health.get("latest_tick")),
        "last_m5_bar": health.get("latest_bar"),
        "seconds_since_m5_bar_open": _utc_age_seconds(cutoff, health.get("latest_bar")),
        "overlap_query_count": health["overlap_query_count"],
        "unqueried_gap_count": health["unqueried_gap_count"],
        "collection_observations": sorted(observations),
        "collector_process_was_running": "NOT_INSPECTED",
        "collection_stoppage_cause": "UNKNOWN_NOT_PROVABLE_FROM_FORWARD_TABLES",
        "clock_lineage": clock,
        "council_input_families": family_rows,
        "council_input_family_count": len(family_rows),
        "real_council_source_lineage_verified": False,
        "missing_external_attestations": len(family_rows),
        "status": "RESEARCH_ONLY_EVIDENCE_GAPS_HOLD",
        "read_only": True,
        "real_forward_admission": False,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
        "final_action": "HOLD",
    }
    result["report_sha256"] = _digest(result)
    return result


def inspect_forward_source_lineage(engine, *, epoch_id, as_of, max_captures=32,
                                  max_source_rows=50000, max_capture_age_seconds=900):
    """The existing V2 health inspector performs the sole read-only transaction."""
    health = inspect_forward_evidence(
        engine, epoch_id=epoch_id, as_of=as_of, max_captures=max_captures,
        max_source_rows=max_source_rows,
    )
    return evaluate_source_lineage(
        health, max_capture_age_seconds=max_capture_age_seconds,
    )


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--read-only-db", action="store_true", required=True)
    parser.add_argument("--epoch-id", required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--captures", type=int, default=32)
    parser.add_argument("--source-limit", type=int, default=50000)
    parser.add_argument("--capture-age-seconds", type=int, default=900)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        epoch = _require_epoch_id(args.epoch_id)
        cutoff = _parse_as_of(args.as_of)
        from backend.database.connection import engine
        try:
            result = inspect_forward_source_lineage(
                engine, epoch_id=epoch, as_of=cutoff,
                max_captures=args.captures,
                max_source_rows=args.source_limit,
                max_capture_age_seconds=args.capture_age_seconds,
            )
        finally:
            engine.dispose()
    except Exception as exc:
        print("FORWARD SOURCE LINEAGE BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))
    else:
        print("EURUSD ORACLE | Forward source lineage B4+C1-C3")
        print("Epoch: " + result["epoch_id"] + " | as_of=" + result["as_of"])
        print("Local health: " + result["health_local_status"] + " | FINAL=HOLD")
        print("Last capture ingested: " + str(result["last_visible_capture_ingested_at"]))
        print("Seconds since capture: " + str(result["seconds_since_capture_ingestion"]))
        print("Last tick: " + str(result["last_tick"]))
        print("Last M5 bar: " + str(result["last_m5_bar"]))
        print("Clock code hash match: " + str(result["clock_lineage"]["same_local_code_hash"]))
        print("Council input families with independent provenance: 0/13")
        for item in result["health_blockers"]:
            print("  HEALTH BLOCKER " + item)
        for item in result["collection_observations"]:
            print("  COLLECTION OBSERVATION " + item)
        print("Unknown collector-process cause | independent input provenance NOT certified")
        print("SHA-256: " + result["report_sha256"])
        print("SELECT-ONLY | NO BROKER | NO SOURCE REPAIR | NO TRAINING/EXECUTION")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
