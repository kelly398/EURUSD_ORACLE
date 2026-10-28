
"""Milestones 21-24: one read-only historical clock and availability assessment.

21 Inventory the verified candle clock evidence and explicit missing fields.
22 Optionally sample archived tick and candle clock fields in a READ ONLY DB transaction.
23 Separate archived insertion time from original causal first availability.
24 Return one fail-closed certification-readiness verdict.

No clock certification, inferred trading sessions, mutation, model training or execution.
"""

import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path

from backend.models.historical_utc_gap_evidence import (
    GapEvidenceError, inspect_gap_evidence,
)
from backend.models.register_historical_utc_seasons import (
    SeasonRegistryError, read_index,
)

VERSION = "EURUSD_HISTORICAL_CLOCK_EVIDENCE_BATCH_V1"
M5 = timedelta(minutes=5)
MAX_SAMPLES = 16


class ClockEvidenceError(RuntimeError):
    """Cannot make a trustworthy, source-backed research assessment."""


def _require_utc(value):
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ClockEvidenceError("Database timestamp is not UTC-tagged.")
    return value


def _nonnegative_int(value, name):
    if type(value) is not int or value < 0:
        raise ClockEvidenceError("Invalid archive count: " + name)
    return value


def _archive_sample(engine, candidates):
    """Bounded archive cross-check; two non-equivalent clock domains per slot.

    Counts are deliberately compared, NOT joined or reinterpreted as UTC proof.
    Database insertion timestamps are *not* original tick/candle receipts.
    """
    if getattr(getattr(engine, "dialect", None), "name", None) != "postgresql":
        raise ClockEvidenceError("Optional archive check requires PostgreSQL.")
    from sqlalchemy import text

    ticks_sql = text("""
        SELECT COUNT(*) AS n,
          COUNT(*) FILTER (WHERE source_time_msc <> CAST(
            EXTRACT(EPOCH FROM timestamp) * 1000 AS BIGINT
          )) AS raw_timestamp_mismatch
        FROM ticks
        WHERE symbol = 'EURUSD'
          AND timestamp >= :start_ts AND timestamp < :end_ts
    """)
    candle_sql = text("""
        SELECT id, timestamp, ingested_at
        FROM broker_candles
        WHERE id = :row_id AND symbol = 'EURUSD'
          AND timeframe = 'M5' AND source = 'MT5'
          AND is_complete IS TRUE
        LIMIT 1
    """)
    samples = []
    try:
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                ))
                conn.execute(text("SET LOCAL statement_timeout = '15s'"))
                for candidate in candidates:
                    # Explicitly different domains. Never substitute one for the other.
                    counts = {}
                    for clock_name, start_iso in (
                        ("broker_wall_label_domain", candidate["broker_missing_label"]),
                        ("utc_candidate_domain", candidate["missing_utc_candidate"]),
                    ):
                        start = datetime.fromisoformat(start_iso)
                        _require_utc(start)
                        row = conn.execute(ticks_sql, {
                            "start_ts": start, "end_ts": start + M5,
                        }).mappings().one()
                        n = _nonnegative_int(row["n"], "tick n")
                        mismatch = _nonnegative_int(
                            row["raw_timestamp_mismatch"], "raw mismatch",
                        )
                        if mismatch > n:
                            raise ClockEvidenceError("Invalid tick timestamp count.")
                        counts[clock_name] = {
                            "archived_tick_rows": n,
                            "raw_timestamp_mismatch_rows": mismatch,
                        }

                    anchors = []
                    for position in ("before", "after"):
                        ident = candidate[position + "_row_id"]
                        expected_label = candidate[position + "_broker_label"]
                        row = conn.execute(candle_sql, {"row_id": ident}).mappings().first()
                        if row is None:
                            anchors.append({
                                "row_id": ident, "source_record_found": False,
                                "insertion_timestamp_present": False,
                            })
                            continue
                        if (type(row["id"]) is not int or row["id"] != ident
                                or _require_utc(row["timestamp"]).isoformat()
                                != expected_label):
                            raise ClockEvidenceError("Archive candle identity mismatch.")
                        inserted = _require_utc(row["ingested_at"])
                        anchors.append({
                            "row_id": ident, "source_record_found": True,
                            "insertion_timestamp_present": True,
                            # Preserve as a DB observation, not an availability proof.
                            "db_ingested_at": inserted.isoformat(),
                        })
                    samples.append({
                        "gap_number": candidate["gap_number"],
                        "broker_missing_label": candidate["broker_missing_label"],
                        "missing_utc_candidate": candidate["missing_utc_candidate"],
                        "tick_counts_by_unaligned_domain": counts,
                        "source_candle_anchors": anchors,
                        "clock_domains_proven_equivalent": False,
                        "first_available_at_proven": False,
                    })
    except ClockEvidenceError:
        raise
    except Exception as exc:
        # Never echo DB exception details, which can contain connection secrets.
        raise ClockEvidenceError(
            "Read-only archive inspection failed (" + type(exc).__name__ + ")."
        ) from None
    return samples


def assess_historical_clock_evidence(
    registry_directory, index_directories, dataset_root, *, archive_engine=None,
):
    """One verified evidence chain, four diagnostics, one blocked verdict.

    By default this does NO SQL. With a supplied engine, perform bounded,
    read-only archive checks only around missing same-broker-day M5 slots.
    """
    indexes = tuple(index_directories)
    try:
        evidence = inspect_gap_evidence(registry_directory, indexes, dataset_root)
        manifests = [read_index(p) for p in indexes]
    except (GapEvidenceError, SeasonRegistryError, OSError, ValueError) as exc:
        raise ClockEvidenceError("Source/registry verification failed.") from exc

    winters = sum(i["winter_count"] for i in manifests)
    summers = sum(i["summer_count"] for i in manifests)
    source_count = sum(len(i["source_windows"]) for i in manifests)
    rows = evidence["row_count"]
    gaps = evidence["gaps"]
    if (rows != winters + summers or winters == 0 or summers == 0
            or len(gaps) != evidence["observed_gap_count"]
            or source_count < len(indexes)
            or evidence.get("research_only") is not True
            or evidence.get("historical_utc_certified") is not False
            or evidence.get("training_authorized") is not False
            or evidence.get("execution_authorized") is not False):
        raise ClockEvidenceError("Inconsistent audited registry evidence.")

    # The M5 export audit already strictly validates each original source row,
    # the candidate mapping, and that first_available_at is literally null.
    inventory = {
        "source_export_count": source_count,
        "index_count": len(indexes),
        "verified_candle_rows": rows,
        "winter_candidate_offset_hours": 2,
        "winter_candidate_rows": winters,
        "summer_candidate_offset_hours": 3,
        "summer_candidate_rows": summers,
        "broker_wall_label_retained": True,
        "hypothesized_candidate_utc_retained": True,
        "candidate_mapping_recomputed_from_original_rows": True,
        "candidate_utc_independently_certified": False,
        "export_contains_original_tick_source_time_msc": False,
        "export_contains_candle_db_ingested_at": False,
        "export_contains_original_receipt_timestamp": False,
        "export_first_available_at_is_null": True,
    }

    sample_candidates = []
    for gap_number, gap in enumerate(gaps, start=1):
        if gap["classification"] != "SAME_BROKER_DAY_UNVERIFIED":
            continue
        # We sample one missing M5 slot per observed same-day gap, not an
        # entire missing session. Larger gaps are not silently truncated.
        if gap["unobserved_m5_slots"] != 1:
            continue
        missing_label = (
            datetime.fromisoformat(gap["previous_broker_label"]) + M5
        )
        missing_candidate = datetime.fromisoformat(
            gap["first_unobserved_candidate_utc"]
        )
        if (missing_label.utcoffset() != timedelta(0)
                or missing_candidate.utcoffset() != timedelta(0)
                or type(gap["previous_source_row_id"]) is not int
                or type(gap["next_source_row_id"]) is not int):
            raise ClockEvidenceError("Invalid missing-slot source evidence.")
        sample_candidates.append({
            "gap_number": gap_number,
            "broker_missing_label": missing_label.isoformat(),
            "missing_utc_candidate": missing_candidate.isoformat(),
            "before_row_id": gap["previous_source_row_id"],
            "after_row_id": gap["next_source_row_id"],
            "before_broker_label": gap["previous_broker_label"],
            "after_broker_label": gap["next_broker_label"],
        })
    if len(sample_candidates) > MAX_SAMPLES:
        raise ClockEvidenceError("Too many archive sample intervals.")

    archive_samples = (
        _archive_sample(archive_engine, sample_candidates)
        if archive_engine is not None else []
    )
    archive_mode = (
        "BOUNDED_ARCHIVE_SAMPLES_UNCERTIFIED" if archive_engine is not None
        else "NOT_QUERIED"
    )
    blockers = [
        "Independent historical broker-clock evidence is not established.",
        "Tick raw timestamps and broker-candle labels may use different clocks.",
        "Database insertion time is not original observation receipt time.",
        "Historical first_available_at is unknown for every exported candle.",
        "Unverified gaps and excluded periods cannot be treated as sessions.",
        "No causal model-training or trading admission can be inferred.",
    ]
    return {
        "assessment_version": VERSION,
        "registry_sha256": evidence["registry_sha256"],
        "milestone_21_clock_inventory": inventory,
        "milestone_22_tick_candle_domain_review": {
            "archive_mode": archive_mode,
            "eligible_single_slot_same_day_gaps": len(sample_candidates),
            "sampled_gap_candidates": sample_candidates,
            "archive_samples": archive_samples,
            "clock_domains_proven_equivalent": False,
            "archive_covers_all_source_rows": False,
        },
        "milestone_23_causal_availability": {
            "export_rows_without_first_available_at": rows,
            "first_available_at_verified_rows": 0,
            "db_ingested_at_is_original_receipt": False,
            "candidate_bar_end_is_actual_availability": False,
            "future_information_excluded_from_training": "NOT_EVALUATED",
        },
        "milestone_24_integrated_readiness": {
            "status": "HISTORICAL_CLOCK_UNCERTIFIED_RESEARCH_ONLY",
            "excluded_calendar_days": evidence["excluded_calendar_days_between_indexes"],
            "observed_gap_count": evidence["observed_gap_count"],
            "unclassified_missing_m5_slots": evidence["unclassified_missing_m5_slots"],
            "blockers": blockers,
            "required_future_evidence": [
                "Timestamped independent broker clock/MT5 quote observations for historical periods.",
                "A verified bridge between tick and candle timestamp domains.",
                "Authentic original receipt/first-availability records with clock provenance.",
                "Gap-cause and session-calendar evidence for historical trading intervals.",
                "Independent leakage, replay, and out-of-sample validation.",
            ],
        },
        "research_only": True,
        "historical_utc_certified": False,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
    }


def run(argv=None, *, engine=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--index", action="append", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path,
                        default=Path("data/research/historical_utc_v2"))
    parser.add_argument("--inspect-archive", action="store_true",
                        help="Optional bounded read-only PostgreSQL samples")
    parser.add_argument("--research-only", action="store_true", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        archive_engine = None
        if args.inspect_archive:
            if engine is None:
                from backend.database.connection import engine as configured_engine
                engine = configured_engine
            archive_engine = engine
        elif engine is not None:
            raise ClockEvidenceError("Archive engine requires --inspect-archive.")
        report = assess_historical_clock_evidence(
            args.registry, args.index, args.dataset_root,
            archive_engine=archive_engine,
        )
    except ClockEvidenceError as exc:
        print("CLOCK EVIDENCE BLOCKED: " + str(exc))
        return 1
    except Exception as exc:
        print("CLOCK EVIDENCE BLOCKED: " + type(exc).__name__)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        m21 = report["milestone_21_clock_inventory"]
        m22 = report["milestone_22_tick_candle_domain_review"]
        m23 = report["milestone_23_causal_availability"]
        m24 = report["milestone_24_integrated_readiness"]
        print("EURUSD ORACLE | Milestones 21-24 clock evidence (read only)")
        print(f"REGISTRY VERIFIED: {report['registry_sha256']}")
        print(f"M21 ROWS: {m21['verified_candle_rows']} | EXPORTS: "
              f"{m21['source_export_count']} | "
              f"WINTER: {m21['winter_candidate_rows']} | "
              f"SUMMER: {m21['summer_candidate_rows']}")
        print(f"M22 ARCHIVE: {m22['archive_mode']} | "
              f"ELIGIBLE SINGLE-SLOT GAPS: {m22['eligible_single_slot_same_day_gaps']}")
        for item in m22["archive_samples"]:
            counts = item["tick_counts_by_unaligned_domain"]
            raw = counts["broker_wall_label_domain"]["archived_tick_rows"]
            candidate = counts["utc_candidate_domain"]["archived_tick_rows"]
            anchors = sum(x["source_record_found"] for x in item["source_candle_anchors"])
            print(f"M22 GAP {item['gap_number']} | broker-label-slot ticks={raw} | "
                  f"candidate-UTC-slot ticks={candidate} | "
                  f"stored candle anchors={anchors}/2 | NOT CLOCK-COMPARABLE")
        print("M23 UNKNOWN FIRST AVAILABILITY: "
              f"{m23['export_rows_without_first_available_at']}")
        print(f"M24 STATUS: {m24['status']} | "
              f"GAPS: {m24['observed_gap_count']} | "
              f"EXCLUDED DAYS: {m24['excluded_calendar_days']}")
        print("NO FILES WRITTEN | UTC UNCERTIFIED | "
              "TRAINING BLOCKED | EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
