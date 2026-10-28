
"""Milestone 09: read-only quality assessment of EURUSD features.

Requires the original audited research candles.
Neither checksums nor this assessment certify actual event time,
causal availability, or trading fitness.
"""

import argparse
from datetime import datetime
from hashlib import sha256
import json
from math import ceil, isfinite
from pathlib import Path

from backend.models.historical_utc_research_feature_export import (
    ResearchFeatureExportError,
    verify_feature_export,
)
from backend.models.historical_utc_research_features import (
    ResearchFeatureError,
)


class ResearchQualityError(RuntimeError):
    """Feature quality assessment cannot be trusted."""


NUMERIC_FIELDS = (
    "close",
    "log_return_1",
    "range_pips",
    "body_pips",
    "sma_close_12",
    "volatility_log_return_12",
    "atr_14_pips",
    "tick_volume",
    "spread_points",
)

ROLLING_FIELDS = (
    "log_return_1",
    "sma_close_12",
    "volatility_log_return_12",
    "atr_14_pips",
)


def _summary(values):
    """Deterministic nearest-rank 95th percentile."""
    if not values:
        return {
            "count": 0,
            "min": None,
            "median": None,
            "p95": None,
            "max": None,
        }

    ordered = sorted(values)
    n = len(ordered)

    middle = (
        ordered[(n - 1) // 2]
        + ordered[n // 2]
    ) / 2

    return {
        "count": n,
        "min": round(ordered[0], 12),
        "median": round(middle, 12),
        "p95": round(
            ordered[ceil(0.95 * n) - 1],
            12,
        ),
        "max": round(ordered[-1], 12),
    }


def assess_research_feature_quality(
    features_directory: Path,
    source_directory: Path,
    *,
    min_rows: int = 10_000,
    min_candidate_days: int = 30,
) -> dict:
    """Assess features without changing or admitting any data.

    Coverage thresholds are exploratory heuristics, not proof
    of valid backtests or production readiness.
    """

    if (
        type(min_rows) is not int
        or not 1 <= min_rows <= 500_000
    ):
        raise ResearchQualityError(
            "min_rows must be 1..500000."
        )

    if (
        type(min_candidate_days) is not int
        or not 1 <= min_candidate_days <= 3660
    ):
        raise ResearchQualityError(
            "min_candidate_days must be 1..3660."
        )

    feature_path = Path(features_directory)

    try:
        # Require source-backed reconstruction verification,
        # not merely checksums from the feature export.

        meta = verify_feature_export(
            feature_path,
            Path(source_directory),
        )

    except (
        ResearchFeatureExportError,
        ResearchFeatureError,
        OSError,
        ValueError,
        TypeError,
    ) as exc:
        raise ResearchQualityError(
            "Source-backed feature verification failed."
        ) from exc

    series = {
        field: []
        for field in NUMERIC_FIELDS
    }

    dates = set()
    offsets = set()

    first_time = None
    last_time = None

    fully_warmed = 0
    zero_tick_volume = 0
    zero_spread_points = 0
    zero_range_pips = 0

    digest = sha256()
    n = 0

    try:
        with (
            feature_path / "features.jsonl"
        ).open("rb") as stream:

            for raw in stream:
                digest.update(raw)

                row = json.loads(raw)
                n += 1

                if n > meta["feature_count"]:
                    raise ResearchQualityError(
                        "Feature row limit changed."
                    )

                # Recheck numeric fields before performing
                # calculations and distribution analysis.

                for field in NUMERIC_FIELDS:
                    value = row[field]

                    if value is None:
                        if field not in ROLLING_FIELDS:
                            raise ResearchQualityError(
                                f"Unexpected null {field}."
                            )
                        continue

                    if (
                        type(value) not in (int, float)
                        or not isfinite(value)
                    ):
                        raise ResearchQualityError(
                            f"Invalid numeric {field}."
                        )

                    series[field].append(value)

                if all(
                    row[key] is not None
                    for key in ROLLING_FIELDS
                ):
                    fully_warmed += 1

                zero_tick_volume += (
                    row["tick_volume"] == 0
                )

                zero_spread_points += (
                    row["spread_points"] == 0
                )

                zero_range_pips += (
                    row["range_pips"] == 0
                )

                t = datetime.fromisoformat(
                    row["candidate_utc_open"]
                )

                dates.add(t.date().isoformat())

                offsets.add(
                    row["candidate_offset_hours"]
                )

                if first_time is None:
                    first_time = t

                last_time = t

    except (
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        OverflowError,
    ) as exc:
        raise ResearchQualityError(
            "Feature file changed or is unreadable."
        ) from exc

    if (
        n != meta["feature_count"]
        or digest.hexdigest()
        != meta["feature_rows_sha256"]
    ):
        raise ResearchQualityError(
            "Feature file changed during assessment."
        )

    available = {
        name: {
            "rows": len(series[name]),
            "percent": round(
                100 * len(series[name]) / n,
                2,
            ),
        }
        for name in ROLLING_FIELDS
    }

    # These thresholds establish minimum exploratory
    # coverage only. They do not approve model training.

    coverage_met = (
        n >= min_rows
        and len(dates) >= min_candidate_days
        and offsets == {2, 3}
    )

    issues = []

    if n < min_rows:
        issues.append(
            f"Only {n} candles; "
            f"heuristic minimum is {min_rows}."
        )

    if len(dates) < min_candidate_days:
        issues.append(
            "Insufficient distinct candidate calendar dates."
        )

    if offsets != {2, 3}:
        issues.append(
            "Both winter and summer clock regimes "
            "are not represented."
        )

    if meta["gap_count"]:
        issues.append(
            "Gaps need market-closure and "
            "data-outage classification."
        )

    if zero_tick_volume:
        issues.append(
            "Some candles have zero recorded tick volume."
        )

    if zero_spread_points:
        issues.append(
            "Some candles have zero recorded spread points."
        )

    if zero_range_pips:
        issues.append(
            "Some candles have zero high-low range."
        )

    if fully_warmed == 0:
        issues.append(
            "No rows contain all rolling indicators."
        )

    return {
        "assessment_version":
            "EURUSD_RESEARCH_FEATURE_QA_V1",

        "source_lineage_sha256":
            meta["source_lineage_sha256"],

        "feature_rows_sha256":
            meta["feature_rows_sha256"],

        "row_count": n,

        "distinct_candidate_dates": len(dates),

        "first_candidate_utc":
            first_time.isoformat(),

        "last_candidate_utc":
            last_time.isoformat(),

        "candidate_offset_hours_observed":
            sorted(offsets),

        "segment_count": meta["segment_count"],

        "gap_count": meta["gap_count"],

        "unobserved_m5_slots_including_market_closures":
            meta["unobserved_intervals"],

        "fully_warmed_rows": fully_warmed,

        "indicator_availability": available,

        "distributions": {
            field: _summary(values)
            for field, values in series.items()
        },

        "zero_tick_volume": zero_tick_volume,

        "zero_spread_points": zero_spread_points,

        "zero_range_pips": zero_range_pips,

        "minimum_exploratory_coverage_met":
            coverage_met,

        "coverage_heuristics": {
            "minimum_rows": min_rows,
            "minimum_candidate_dates":
                min_candidate_days,
            "both_clock_regimes_required": True,
        },

        "review_findings": issues,

        "historical_utc_certified": False,
        "source_receipt_certified": False,
        "research_only": True,
        "training_authorized": False,
        "execution_authorized": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Read-only, source-backed EURUSD "
            "research feature quality assessment"
        )
    )

    parser.add_argument(
        "--features-directory",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--source-directory",
        type=Path,
        required=True,
    )

    parser.add_argument(
        "--min-rows",
        type=int,
        default=10_000,
    )

    parser.add_argument(
        "--min-candidate-days",
        type=int,
        default=30,
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full diagnostics",
    )

    args = parser.parse_args(argv)

    try:
        report = assess_research_feature_quality(
            args.features_directory,
            args.source_directory,
            min_rows=args.min_rows,
            min_candidate_days=args.min_candidate_days,
        )

    except ResearchQualityError as exc:
        print(
            f"QUALITY ASSESSMENT BLOCKED: {exc}"
        )
        return 1

    if args.json:
        print(
            json.dumps(
                report,
                indent=2,
                sort_keys=True,
            )
        )

    else:
        print(
            "EURUSD ORACLE | "
            "Research feature quality assessment"
        )

        print(
            f"Rows: {report['row_count']} | "
            "Distinct candidate dates: "
            f"{report['distinct_candidate_dates']}"
        )

        print(
            f"Segments: {report['segment_count']} | "
            f"Gaps: {report['gap_count']}"
        )

        print(
            "Fully warmed rows: "
            f"{report['fully_warmed_rows']}"
        )

        for name, item in report[
            "indicator_availability"
        ].items():

            print(
                f"{name}: {item['rows']} available "
                f"({item['percent']}%)"
            )

        for name in (
            "range_pips",
            "spread_points",
            "tick_volume",
        ):
            print(
                f"{name}: "
                f"{report['distributions'][name]}"
            )

        print(
            "Minimum exploratory coverage met:",
            report[
                "minimum_exploratory_coverage_met"
            ],
        )

        for finding in report["review_findings"]:
            print("REVIEW:", finding)

        print(
            "NO FILES WRITTEN | UTC UNCERTIFIED | "
            "TRAINING BLOCKED | EXECUTION BLOCKED"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
