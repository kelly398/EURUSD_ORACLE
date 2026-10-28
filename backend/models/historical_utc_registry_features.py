
"""Milestone 17: source-backed, read-only feature coverage across ORACLE seasons.

Reuses Milestone 15's registry-to-source verification and Milestone 07's
past-only indicator calculator. Features are recomputed INDEPENDENTLY for
EVERY immutable source export. No rolling state may cross an export or an
unobserved M5 interval. No feature files, DB changes, training or trades.
"""

import argparse
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

from backend.models.consolidate_historical_utc_research import _canonical
from backend.models.historical_utc_gap_evidence import (
    GapEvidenceError,
    inspect_gap_evidence,
)
from backend.models.historical_utc_research_features import (
    ResearchFeatureError,
    build_clock_aware_research_features,
)
from backend.models.historical_utc_research_quality import _summary
from backend.models.register_historical_utc_seasons import (
    SeasonRegistryError,
    read_index,
)

VERSION = "EURUSD_M5_MULTISEASON_RESEARCH_FEATURE_COVERAGE_V1"
MAX_ROWS = 500_000
ROLLING = (
    "log_return_1",
    "sma_close_12",
    "volatility_log_return_12",
    "atr_14_pips",
)


class RegistryFeatureError(RuntimeError):
    """Source-backed research feature coverage could not be verified."""


def assess_registry_features(
    registry_directory,
    index_directories,
    dataset_root,
    *,
    min_rows=10_000,
    min_candidate_dates=30,
):
    """Audit sources and recompute isolated, past-only research features.

    Heuristics are descriptive ONLY. Not historical UTC, first availability,
    feature leakage, economic value, or model-readiness certification.
    """
    if type(min_rows) is not int or not 1 <= min_rows <= MAX_ROWS:
        raise RegistryFeatureError("min_rows must be 1..500000.")
    if (type(min_candidate_dates) is not int
            or not 1 <= min_candidate_dates <= 3660):
        raise RegistryFeatureError("min_candidate_dates must be 1..3660.")

    index_directories = tuple(index_directories)
    root = Path(dataset_root)
    if root.is_symlink() or not root.is_dir():
        raise RegistryFeatureError("Unsafe or missing dataset root.")

    try:
        # Full independent audit: registry -> indexes -> source candle rows.
        evidence = inspect_gap_evidence(
            registry_directory, index_directories, root
        )
        indexes = [read_index(path) for path in index_directories]
    except (GapEvidenceError, SeasonRegistryError, OSError) as exc:
        raise RegistryFeatureError(
            "Registry, index or source data verification failed."
        ) from exc

    candidate_dates = set()
    offset_counts = {2: 0, 3: 0}
    available = {name: 0 for name in ROLLING}
    spread_values = []
    range_values = []
    total_rows = total_segments = total_gaps = total_missing = 0
    total_warmed = 0
    zero_spread = zero_range = 0
    parents_report = []

    try:
        for index in indexes:
            for parent in index["source_windows"]:
                source_lineage = parent["source_lineage_sha256"]
                folder = root / ("dataset_" + source_lineage)
                if folder.is_symlink() or not folder.is_dir():
                    raise RegistryFeatureError("Source folder missing or unsafe.")

                # Never write feature exports. Reuse Milestone 07 verbatim.
                features = build_clock_aware_research_features(
                    folder, max_rows=MAX_ROWS
                )
                manifest = features.manifest()
                if (
                    manifest["source_lineage_sha256"] != source_lineage
                    or manifest["source_rows_sha256"] != parent["source_rows_sha256"]
                    or manifest["feature_count"] != parent["source_row_count"]
                    or manifest["training_authorized"] is not False
                    or manifest["execution_authorized"] is not False
                    or manifest["first_available_at"] is not None
                    or manifest["rolling_state_reset_on_gap"] is not True
                ):
                    raise RegistryFeatureError("Feature source or safety mismatch.")

                rows = features.rows
                if (not rows or rows[0].segment_id != 1
                        or rows[0].gap_before_m5 != 0
                        or any(getattr(rows[0], key) is not None for key in ROLLING)):
                    raise RegistryFeatureError("Source boundary did not reset state.")

                feature_warmed = 0
                for row in rows:
                    # The candidate bar end is hypothetical, NOT availability.
                    start = datetime.fromisoformat(row.candidate_utc_open)
                    end = datetime.fromisoformat(row.candidate_bar_end_utc)
                    if (start.utcoffset() != timedelta(0)
                            or end - start != timedelta(minutes=5)
                            or row.candidate_offset_hours not in (2, 3)):
                        raise RegistryFeatureError("Invalid candidate time in features.")
                    safety = row.record()
                    if (safety["first_available_at"] is not None
                            or safety["research_only"] is not True
                            or safety["training_authorized"] is not False
                            or safety["execution_authorized"] is not False):
                        raise RegistryFeatureError("Unsafe feature row.")

                    candidate_dates.add(start.date().isoformat())
                    offset_counts[row.candidate_offset_hours] += 1
                    spread_values.append(row.spread_points)
                    range_values.append(row.range_pips)
                    zero_spread += row.spread_points == 0
                    zero_range += row.range_pips == 0
                    for key in ROLLING:
                        available[key] += getattr(row, key) is not None
                    feature_warmed += all(getattr(row, key) is not None for key in ROLLING)

                total_warmed += feature_warmed
                total_rows += len(rows)
                total_segments += features.segment_count
                total_gaps += features.gap_count
                total_missing += features.unobserved_intervals

                parents_report.append({
                    "index_sha256": index["index_sha256"],
                    "source_lineage_sha256": source_lineage,
                    "source_rows_sha256": features.source_rows_sha256,
                    "feature_rows_sha256": features.feature_rows_sha256,
                    "row_count": len(rows),
                    "feature_segment_count": features.segment_count,
                    "internal_gap_count": features.gap_count,
                    "internal_missing_m5_slots": features.unobserved_intervals,
                    "fully_warmed_rows": feature_warmed,
                    "reset_at_start_of_export": True,
                })
    except RegistryFeatureError:
        raise
    except (ResearchFeatureError, OSError, ValueError, TypeError,
            KeyError, OverflowError, UnicodeError) as exc:
        raise RegistryFeatureError(
            "An audited source could not produce verified features."
        ) from exc

    if (total_rows != evidence["row_count"]
            or offset_counts[2] + offset_counts[3] != total_rows
            or offset_counts[2] != sum(i["winter_count"] for i in indexes)
            or offset_counts[3] != sum(i["summer_count"] for i in indexes)
            or total_segments != len(parents_report) + total_gaps
            or total_gaps > evidence["observed_gap_count"]
            or total_missing > evidence["unclassified_missing_m5_slots"]):
        raise RegistryFeatureError("Aggregated feature and registry totals disagree.")

    boundary_gaps = evidence["observed_gap_count"] - total_gaps
    boundary_missing = evidence["unclassified_missing_m5_slots"] - total_missing
    if (boundary_gaps < 0 or boundary_missing < boundary_gaps):
        raise RegistryFeatureError("Inconsistent cross-export gap totals.")

    heuristics = {
        "minimum_rows": min_rows,
        "minimum_candidate_dates": min_candidate_dates,
        "row_count_met": total_rows >= min_rows,
        "candidate_date_count_met": len(candidate_dates) >= min_candidate_dates,
        "both_seasonal_offsets_observed": all(offset_counts.values()),
    }
    heuristics["all_three_coverage_heuristics_met"] = all([
        heuristics["row_count_met"],
        heuristics["candidate_date_count_met"],
        bool(heuristics["both_seasonal_offsets_observed"]),
    ])

    # Digest binds this reproducible feature inventory to its audited registry.
    proof = sha256(_canonical({
        "version": VERSION,
        "registry_sha256": evidence["registry_sha256"],
        "parent_features": parents_report,
    })).hexdigest()
    return {
        "assessment_version": VERSION,
        "registry_sha256": evidence["registry_sha256"],
        "feature_inventory_sha256": proof,
        "index_count": len(indexes),
        "source_export_count": len(parents_report),
        "row_count": total_rows,
        "distinct_candidate_dates": len(candidate_dates),
        "winter_rows": offset_counts[2],
        "summer_rows": offset_counts[3],
        "fully_warmed_rows": total_warmed,
        "indicator_available_rows": available,
        "source_isolated_feature_segments": total_segments,
        "within_export_gap_count": total_gaps,
        "within_export_missing_m5_slots": total_missing,
        "across_export_unverified_gap_count": boundary_gaps,
        "across_export_missing_m5_slots": boundary_missing,
        "between_index_calendar_days_excluded": evidence[
            "excluded_calendar_days_between_indexes"],
        "spread_points_distribution": _summary(spread_values),
        "range_pips_distribution": _summary(range_values),
        "zero_spread_rows": zero_spread,
        "zero_range_rows": zero_range,
        "coverage_heuristics": heuristics,
        "parent_features": parents_report,
        "boundary_state_reset_at_each_export": True,
        "internal_gap_state_reset": True,
        "source_receipt_certified": False,
        "historical_utc_certified": False,
        "actual_first_available_at_verified": False,
        "training_authorized": False,
        "execution_authorized": False,
        "historical_admission": False,
        "research_only": True,
        "whole_registry_feature_coverage_assessed": True,
        "production_model_readiness_established": False,
    }


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit and recompute features for each registered research export"
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--index", action="append", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path,
                        default=Path("data/research/historical_utc_v2"))
    parser.add_argument("--min-rows", type=int, default=10_000)
    parser.add_argument("--min-candidate-dates", type=int, default=30)
    parser.add_argument("--research-only", required=True, action="store_true")
    parser.add_argument("--json", action="store_true",
                        help="Print diagnostics to stdout; write no files")
    args = parser.parse_args(argv)
    try:
        report = assess_registry_features(
            args.registry, args.index, args.dataset_root,
            min_rows=args.min_rows, min_candidate_dates=args.min_candidate_dates,
        )
    except RegistryFeatureError as exc:
        print(f"REGISTRY FEATURE ASSESSMENT BLOCKED: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("EURUSD ORACLE | Multi-season research features (read only)")
        print(f"REGISTRY VERIFIED: {report['registry_sha256']}")
        print(f"OBSERVATIONS: {report['row_count']} | "
              f"SOURCE EXPORTS: {report['source_export_count']}")
        print(f"WINTER: {report['winter_rows']} | SUMMER: {report['summer_rows']}")
        print(f"DISTINCT CANDIDATE DATES: {report['distinct_candidate_dates']}")
        print(f"FULLY WARMED: {report['fully_warmed_rows']}")
        for name, count in report["indicator_available_rows"].items():
            print(f"{name}: {count} available")
        print(f"FEATURE SEGMENTS: {report['source_isolated_feature_segments']}")
        print(f"INTERNAL GAPS: {report['within_export_gap_count']} | "
              f"ACROSS EXPORT GAPS: {report['across_export_unverified_gap_count']}")
        print(f"SPREAD POINTS: {report['spread_points_distribution']}")
        print(f"FEATURE INVENTORY SHA-256: {report['feature_inventory_sha256']}")
        print("COVERAGE HEURISTICS MET: "
              f"{report['coverage_heuristics']['all_three_coverage_heuristics_met']}")
        print("NO FILES WRITTEN | UTC UNCERTIFIED | "
              "TRAINING BLOCKED | EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
