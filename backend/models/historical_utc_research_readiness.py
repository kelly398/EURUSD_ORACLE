
"""Milestone 16: read-only provenance and readiness scorecard for ORACLE.

Reuses Milestone 15 to re-audit registry, indexes, and candle exports.
The results are diagnostic only. Meeting a sample-size threshold does not
certify event timing, approve features, admit models, or authorize trading.
"""

import argparse
import json
from pathlib import Path

from backend.models.historical_utc_gap_evidence import (
    GapEvidenceError,
    inspect_gap_evidence,
)
from backend.models.register_historical_utc_seasons import (
    SeasonRegistryError,
    read_index,
)

VERSION = "EURUSD_HISTORICAL_RESEARCH_READINESS_V1"
MAX_ROWS = 500_000
GAP_CLASSES = (
    "SAME_BROKER_DAY_UNVERIFIED",
    "WEEKEND_SHAPED_UNVERIFIED",
    "CROSS_BROKER_DAY_UNVERIFIED",
)


class ResearchReadinessError(RuntimeError):
    """A source-backed readiness assessment was not safely completed."""


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def assess_research_readiness(
    registry_directory,
    index_directories,
    dataset_root,
    *,
    min_rows=10_000,
):
    """Assess audited research provenance without granting admission.

    Only row count is tested against the optional exploratory heuristic.
    The number of distinct candidate-UTC dates and feature coverage across
    the registry are NOT verified by the existing registry metadata.
    """
    if type(min_rows) is not int or not 1 <= min_rows <= MAX_ROWS:
        raise ResearchReadinessError("min_rows must be 1..500000.")

    indexes = tuple(index_directories)
    try:
        # Milestone 15 verifies the registry, all indexes, original exports,
        # source rows, content hashes, and observed gaps. Do not duplicate it.
        evidence = inspect_gap_evidence(
            registry_directory, indexes, dataset_root
        )
        verified_indexes = [read_index(path) for path in indexes]
    except (GapEvidenceError, SeasonRegistryError, OSError, ValueError,
            TypeError) as exc:
        raise ResearchReadinessError(
            "Registry, index, or source export verification failed."
        ) from exc

    count = evidence.get("row_count")
    gap_count = evidence.get("observed_gap_count")
    missing = evidence.get("unclassified_missing_m5_slots")

    # Match the exact field returned by Milestone 15.
    excluded_days = evidence.get("excluded_calendar_days_between_indexes")

    classifications = evidence.get("classification_counts")

    if (not all(_nonnegative_int(x) for x in (
            count, gap_count, missing, excluded_days))
            or count == 0
            or evidence.get("index_count") != len(indexes)
            or not isinstance(classifications, dict)
            or set(classifications) != set(GAP_CLASSES)
            or any(not _nonnegative_int(v) for v in classifications.values())
            or sum(classifications.values()) != gap_count
            or evidence.get("research_only") is not True
            or evidence.get("historical_utc_certified") is not False
            or evidence.get("training_authorized") is not False
            or evidence.get("execution_authorized") is not False
            or evidence.get("excluded_periods_are_observed_gaps") is not False):
        raise ResearchReadinessError("Inconsistent audited research evidence.")

    gaps = evidence.get("gaps")
    if (not isinstance(gaps, list) or len(gaps) != gap_count
            or sum(item.get("unobserved_m5_slots", 0) for item in gaps
                   if isinstance(item, dict)) != missing
            or any(not isinstance(item, dict)
                   or item.get("classification") not in GAP_CLASSES
                   or not _nonnegative_int(item.get("unobserved_m5_slots"))
                   or item["unobserved_m5_slots"] == 0
                   or item.get("market_closure_certified") is not False
                   or item.get("tick_archive_proves_cause") is not False
                   for item in gaps)):
        raise ResearchReadinessError("Unverified gap evidence is inconsistent.")

    winter = sum(item["winter_count"] for item in verified_indexes)
    summer = sum(item["summer_count"] for item in verified_indexes)

    if (winter + summer != count
            or not _nonnegative_int(winter)
            or not _nonnegative_int(summer)):
        raise ResearchReadinessError("Season counts disagree with audited rows.")

    row_threshold_met = count >= min_rows
    both_seasons_observed = winter > 0 and summer > 0

    # There is deliberately no approval flag that can be set to True.
    findings = [
        "Historical event UTC and causal first-availability remain uncertified.",
        "Original broker/source receipt timing has not been independently verified.",
        "Observed missing M5 slots remain unclassified as to cause.",
        "Whole-registry feature availability and leakage controls are not verified.",
        "Distinct candidate UTC calendar dates are not assessed by this report.",
        "Out-of-sample trading performance and transaction costs are not evaluated.",
    ]

    if not row_threshold_met:
        findings.append(
            f"Only {count} rows; exploratory count heuristic is {min_rows}."
        )

    if not both_seasons_observed:
        findings.append("Both clock-offset regimes are not represented.")

    return {
        "assessment_version": VERSION,
        "status": "SOURCE_AUDITED_RESEARCH_ONLY_NOT_MODEL_READY",
        "registry_sha256": evidence["registry_sha256"],
        "row_count": count,
        "index_count": len(indexes),
        "winter_rows": winter,
        "summer_rows": summer,
        "observed_gap_count": gap_count,
        "unclassified_missing_m5_slots": missing,
        "gap_classification_counts": {
            kind: classifications[kind] for kind in GAP_CLASSES
        },
        "excluded_between_index_calendar_days": excluded_days,
        "excluded_periods_joined": False,
        "coverage_heuristics": {
            "minimum_rows": min_rows,
            "row_count_threshold_met": row_threshold_met,
            "both_clock_regimes_observed": both_seasons_observed,
            "minimum_distinct_candidate_dates_verified": False,
            "overall_exploratory_coverage_established": False,
        },
        "evidence_verified": {
            "registry_index_and_export_chain": True,
            "cross_index_separation": True,
            "all_observed_gap_boundaries": True,
            "gap_causes_independently_verified": False,
            "historical_event_utc_certified": False,
            "first_availability_verified": False,
            "full_registry_feature_set_audited": False,
            "out_of_sample_strategy_validation": False,
        },
        "blocking_findings": findings,
        "research_only": True,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
    }


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Read-only EURUSD historical research readiness assessment"
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--index", action="append", type=Path, required=True,
                        help="Repeat in chronological registry order")
    parser.add_argument("--dataset-root", type=Path,
                        default=Path("data/research/historical_utc_v2"))
    parser.add_argument("--min-rows", type=int, default=10_000)
    parser.add_argument("--research-only", action="store_true", required=True)
    parser.add_argument("--json", action="store_true",
                        help="Print diagnostics to stdout only; no files written")
    args = parser.parse_args(argv)

    try:
        report = assess_research_readiness(
            args.registry, args.index, args.dataset_root,
            min_rows=args.min_rows,
        )
    except ResearchReadinessError as exc:
        print(f"READINESS ASSESSMENT BLOCKED: {exc}")
        return 1

    if args.json:
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print("EURUSD ORACLE | Historical research readiness (read only)")
        print(f"REGISTRY VERIFIED: {report['registry_sha256']}")
        print(f"ROWS: {report['row_count']} | INDEXES: {report['index_count']}")
        print(f"WINTER: {report['winter_rows']} | SUMMER: {report['summer_rows']}")
        print(f"GAPS UNVERIFIED: {report['observed_gap_count']} | "
              f"M5 SLOTS: {report['unclassified_missing_m5_slots']}")
        print("BETWEEN-INDEX DAYS EXCLUDED: "
              f"{report['excluded_between_index_calendar_days']}")
        print("ROW-COUNT HEURISTIC MET: "
              f"{report['coverage_heuristics']['row_count_threshold_met']}")
        print(f"STATUS: {report['status']}")

        for finding in report["blocking_findings"]:
            print("REVIEW:", finding)

        print(
            "NO FILES WRITTEN | UTC UNCERTIFIED | "
            "TRAINING BLOCKED | EXECUTION BLOCKED"
        )

    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
