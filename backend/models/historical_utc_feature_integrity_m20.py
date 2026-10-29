"""Milestones 18-20: read-only spread and rolling-boundary integrity review.

Reuses the Milestone 17 source-backed inventory and Milestone 07 indicator
builder. Broker hours and UTC *candidate* hours are diagnostic labels only,
not independently certified event time or source availability.
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta
from hashlib import sha256
import json
from pathlib import Path

from backend.models.historical_utc_registry_features import (
    RegistryFeatureError,
    assess_registry_features,
)
from backend.models.historical_utc_research_features import (
    ResearchFeatureError,
    _json,
    build_clock_aware_research_features,
)
from backend.models.register_historical_utc_seasons import (
    SeasonRegistryError,
    read_index,
)

VERSION = "EURUSD_M5_RESEARCH_FEATURE_INTEGRITY_V1"
# Keep the established assessment version for backward-compatible consumers.
FIELDS = (
    "log_return_1",
    "sma_close_12",
    "volatility_log_return_12",
    "atr_14_pips",
)
WARMUP = {
    "log_return_1": 1,
    "sma_close_12": 11,
    "volatility_log_return_12": 12,
    "atr_14_pips": 13,
}
MAX_ROWS = 500_000


class FeatureIntegrityError(RuntimeError):
    """Research evidence or indicator reset checks did not verify."""


def _percent(value):
    return "N/A" if value is None else f"{value}%"


def _int_in_range(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise FeatureIntegrityError(f"{name} must be {low}..{high}.")
    return value


def assess_feature_integrity(
    registry_directory,
    index_directories,
    dataset_root,
    *,
    spread_alert_points=50,
    top_examples=10,
    near_reset_bars=3,
    top_days=8,
):
    """Inspect recomputed, source-verified features without writing outputs.

    Spread alert thresholds are user-defined descriptive filters, not a
    statistically calibrated outlier definition or execution cost estimate.
    """
    _int_in_range(spread_alert_points, "spread_alert_points", 1, 100_000)
    _int_in_range(top_examples, "top_examples", 1, 25)
    _int_in_range(near_reset_bars, "near_reset_bars", 1, 100)
    _int_in_range(top_days, "top_days", 1, 20)
    indexes = tuple(index_directories)
    root = Path(dataset_root)

    try:
        # Milestone 17 checks registry -> indexes -> all source candles,
        # including content fingerprints and all observed gap boundaries.
        coverage = assess_registry_features(
            registry_directory, indexes, root,
        )
        source_indexes = [read_index(path) for path in indexes]
    except (RegistryFeatureError, SeasonRegistryError, OSError) as exc:
        raise FeatureIntegrityError(
            "Source-backed registry feature verification failed."
        ) from exc

    parent_evidence = coverage["parent_features"]
    parents = [
        (index["index_sha256"], parent)
        for index in source_indexes
        for parent in index["source_windows"]
    ]
    if len(parents) != len(parent_evidence):
        raise FeatureIntegrityError("Source export inventory changed.")

    total_rows = 0
    reset_count = 0
    internal_reset_count = 0
    alert_count = 0
    alert_near_reset = 0
    # Milestone 19 additive attribution: denominator-aware spread exposure.
    # This is descriptive only; broker-label hour 00 is not a certified
    # market event-time session, and segment starts can coincide with it.
    exposure = {
        "broker_00_near_reset": [0, 0],
        "broker_00_outside_reset": [0, 0],
        "other_hours_near_reset": [0, 0],
        "other_hours_outside_reset": [0, 0],
    }
    season_exposure = {
        "winter": [0, 0],
        "summer": [0, 0],
    }
    checked_availability = Counter()
    alerts_by_season = {"winter": 0, "summer": 0}
    # Broker-label hours are NOT certified event-time hours.
    hours = {
        "broker_label": {hour: [0, 0] for hour in range(24)},
        "candidate_utc": {hour: [0, 0] for hour in range(24)},
    }
    leading_alerts = []
    reset_examples = []
    # Milestone 20: per-broker-label date and hour-00 five-minute exposure.
    # All counts are observations; missing trading time is NOT inferred.
    date_exposure = {}
    hour00_minute_exposure = {minute: [0, 0] for minute in range(0, 60, 5)}
    hour00_season_exposure = {
        season: {"broker_hour_00": [0, 0], "other_hours": [0, 0]}
        for season in ("winter", "summer")
    }
    outside_hour_alerts = []

    try:
        for (index_digest, parent), reference in zip(parents, parent_evidence):
            lineage = parent["source_lineage_sha256"]
            if (
                reference["index_sha256"] != index_digest
                or reference["source_lineage_sha256"] != lineage
            ):
                raise FeatureIntegrityError("Parent lineage/order mismatch.")

            folder = root / ("dataset_" + lineage)
            if folder.is_symlink() or not folder.is_dir():
                raise FeatureIntegrityError("Unsafe or missing source folder.")

            # Milestone 07 audits the source and calculates the indicators.
            result = build_clock_aware_research_features(
                folder, max_rows=MAX_ROWS
            )
            if (
                result.feature_rows_sha256 != reference["feature_rows_sha256"]
                or result.source_rows_sha256 != reference["source_rows_sha256"]
                or result.source_lineage_sha256 != lineage
                or len(result.rows) != reference["row_count"]
                or result.segment_count != reference["feature_segment_count"]
                or result.gap_count != reference["internal_gap_count"]
            ):
                raise FeatureIntegrityError("Rebuilt feature proof mismatch.")

            feature_digest = sha256()
            segment = 0
            position = 0
            previous_time = None
            for row in result.rows:
                feature_digest.update((_json(row.record()) + "\n").encode("utf-8"))
                current_time = datetime.fromisoformat(row.candidate_utc_open)
                broker_time = datetime.fromisoformat(row.broker_label_time)
                if (
                    current_time.utcoffset() != timedelta(0)
                    or broker_time.utcoffset() != timedelta(0)
                ):
                    raise FeatureIntegrityError("Invalid UTC-tagged label.")
                if row.spread_points < 0 or row.range_pips < 0:
                    raise FeatureIntegrityError("Invalid numeric observations.")

                delta_slots = 0
                if previous_time is not None:
                    delta = current_time - previous_time
                    if delta < timedelta(minutes=5) or delta % timedelta(minutes=5):
                        raise FeatureIntegrityError("Non-M5 feature ordering.")
                    delta_slots = int(delta // timedelta(minutes=5)) - 1

                begins_segment = previous_time is None or delta_slots > 0
                if begins_segment:
                    segment += 1
                    position = 0
                    reset_count += 1
                    if previous_time is not None:
                        internal_reset_count += 1
                    if len(reset_examples) < 16:
                        reset_examples.append({
                            "reason": (
                                "EXPORT_START" if previous_time is None
                                else "INTERNAL_M5_GAP"
                            ),
                            "source_lineage_sha256": lineage,
                            "broker_label": row.broker_label_time,
                            "candidate_utc": row.candidate_utc_open,
                            "unobserved_slots_before": delta_slots,
                            "source_row_id": row.source_row_id,
                        })
                else:
                    position += 1

                if (
                    row.segment_id != segment
                    or row.gap_before_m5 != delta_slots
                ):
                    raise FeatureIntegrityError("Segment boundary mismatch.")

                # Exact rolling availability contracts from Milestone 07:
                # no return on the first candle, and windowed indicators
                # appear only when the trailing window is fully populated.
                for name in FIELDS:
                    should_exist = position >= WARMUP[name]
                    is_present = getattr(row, name) is not None
                    if should_exist != is_present:
                        raise FeatureIntegrityError(
                            "Rolling feature availability violates reset contract."
                        )
                    checked_availability[name] += is_present

                safety = row.record()
                if (
                    safety["first_available_at"] is not None
                    or safety["research_only"] is not True
                    or safety["training_authorized"] is not False
                    or safety["execution_authorized"] is not False
                ):
                    raise FeatureIntegrityError("Unsafe feature row flags.")

                is_alert = row.spread_points >= spread_alert_points
                near_reset = position < near_reset_bars
                at_broker_00 = broker_time.hour == 0
                exposure_key = (
                    ("broker_00" if at_broker_00 else "other_hours")
                    + ("_near_reset" if near_reset else "_outside_reset")
                )
                exposure[exposure_key][0] += 1
                exposure[exposure_key][1] += int(is_alert)
                season = (
                    "winter" if row.candidate_offset_hours == 2
                    else "summer" if row.candidate_offset_hours == 3
                    else None
                )
                if season is None:
                    raise FeatureIntegrityError("Invalid seasonal offset.")
                season_exposure[season][0] += 1
                season_exposure[season][1] += int(is_alert)
                date_key = broker_time.date().isoformat()
                daily = date_exposure.setdefault(date_key, {
                    "season": season,
                    "all_hours": [0, 0],
                    "broker_hour_00": [0, 0],
                    "other_hours": [0, 0],
                })
                if daily["season"] != season:
                    raise FeatureIntegrityError(
                        "Broker date crossed inconsistent seasonal offsets."
                    )
                day_bucket = "broker_hour_00" if at_broker_00 else "other_hours"
                for pair in (daily["all_hours"], daily[day_bucket],
                             hour00_season_exposure[season][day_bucket]):
                    pair[0] += 1
                    pair[1] += int(is_alert)
                if at_broker_00:
                    if broker_time.minute not in hour00_minute_exposure:
                        raise FeatureIntegrityError("Invalid broker M5 minute label.")
                    pair = hour00_minute_exposure[broker_time.minute]
                    pair[0] += 1
                    pair[1] += int(is_alert)
                for kind, hour in (
                    ("broker_label", broker_time.hour),
                    ("candidate_utc", current_time.hour),
                ):
                    hours[kind][hour][0] += 1
                    hours[kind][hour][1] += is_alert

                if is_alert:
                    alert_count += 1
                    if near_reset:
                        alert_near_reset += 1
                    alerts_by_season[season] += 1
                    candidate = {
                        "spread_points": row.spread_points,
                        "broker_label": row.broker_label_time,
                        "candidate_utc": row.candidate_utc_open,
                        "source_row_id": row.source_row_id,
                        "source_lineage_sha256": lineage,
                        "bars_since_reset": position,
                        "near_reset": near_reset,
                    }
                    leading_alerts.append(candidate)
                    if not at_broker_00:
                        outside_hour_alerts.append(candidate)
                        outside_hour_alerts.sort(key=lambda x: (
                            -x["spread_points"], x["broker_label"],
                            x["source_row_id"],
                        ))
                        if len(outside_hour_alerts) > top_examples:
                            outside_hour_alerts.pop()
                    # Bounded selection of the largest, deterministic on ties.
                    leading_alerts.sort(key=lambda x: (
                        -x["spread_points"],
                        x["candidate_utc"],
                        x["source_row_id"],
                    ))
                    if len(leading_alerts) > top_examples:
                        leading_alerts.pop()

                total_rows += 1
                previous_time = current_time

            if (segment != result.segment_count
                    or feature_digest.hexdigest() != result.feature_rows_sha256):
                raise FeatureIntegrityError("Recomputed feature proof or segments changed.")
    except FeatureIntegrityError:
        raise
    except (ResearchFeatureError, OSError, ValueError, TypeError, KeyError,
            OverflowError, UnicodeError) as exc:
        raise FeatureIntegrityError("Feature source changed during review.") from exc

    if (
        total_rows != coverage["row_count"]
        or reset_count != coverage["source_isolated_feature_segments"]
        or internal_reset_count != coverage["within_export_gap_count"]
        or any(
            checked_availability[field]
            != coverage["indicator_available_rows"][field]
            for field in FIELDS
        )
    ):
        raise FeatureIntegrityError("Integrity totals disagree with Milestone 17.")

    if (
        sum(pair[0] for pair in exposure.values()) != total_rows
        or sum(pair[1] for pair in exposure.values()) != alert_count
        or sum(pair[0] for pair in season_exposure.values()) != total_rows
        or sum(pair[1] for pair in season_exposure.values()) != alert_count
        or sum(pair[1] for key, pair in exposure.items()
               if key.endswith("near_reset")) != alert_near_reset
        or sum(alerts_by_season.values()) != alert_count
        or any(sum(v[0] for v in bins.values()) != total_rows
               or sum(v[1] for v in bins.values()) != alert_count
               for bins in hours.values())
    ):
        raise FeatureIntegrityError("Spread exposure denominators disagree.")

    # Independent reconciliation with Milestone 19 group exposures.
    if (sum(v["all_hours"][0] for v in date_exposure.values()) != total_rows
            or sum(v["all_hours"][1] for v in date_exposure.values()) != alert_count
            or any(v["all_hours"][i] != (
                    v["broker_hour_00"][i] + v["other_hours"][i])
                   for v in date_exposure.values() for i in (0, 1))
            or any(sum(v[group][i] for v in date_exposure.values()
                       if v["season"] == season)
                   != hour00_season_exposure[season][group][i]
                   for season in hour00_season_exposure
                   for group in ("broker_hour_00", "other_hours")
                   for i in (0, 1))
            or any(sum(v[group][i] for v in date_exposure.values()) !=
                   sum(exposure[key][i] for key in exposure
                       if key.startswith("broker_00" if group == "broker_hour_00"
                                         else "other_hours"))
                   for group in ("broker_hour_00", "other_hours")
                   for i in (0, 1))
            or any(sum(pair[i] for pair in hour00_minute_exposure.values())
                   != sum(v["broker_hour_00"][i]
                          for v in date_exposure.values())
                   for i in (0, 1))
            or len(outside_hour_alerts) != min(
                sum(v["other_hours"][1] for v in date_exposure.values()),
                top_examples,
            )
            or any(example["spread_points"] < spread_alert_points
                   or datetime.fromisoformat(example["broker_label"]).hour == 0
                   for example in outside_hour_alerts)):
        raise FeatureIntegrityError("Broker-date spread attribution disagrees.")

    def rate(pair):
        rows, alerts = pair
        return {
            "rows": rows,
            "alerts": alerts,
            "alert_rate_percent": (
                round(100 * alerts / rows, 4) if rows else None
            ),
        }

    near = [
        sum(pair[i] for key, pair in exposure.items()
            if key.endswith("near_reset"))
        for i in (0, 1)
    ]
    far = [total_rows - near[0], alert_count - near[1]]
    broker_00 = [
        sum(pair[i] for key, pair in exposure.items()
            if key.startswith("broker_00"))
        for i in (0, 1)
    ]
    other = [total_rows - broker_00[0], alert_count - broker_00[1]]
    broker_dates = [
        {
            "broker_label_date": date_key,
            "season": item["season"],
            "all_hours": rate(item["all_hours"]),
            "broker_hour_00": rate(item["broker_hour_00"]),
            "other_hours": rate(item["other_hours"]),
        }
        for date_key, item in sorted(date_exposure.items())
    ]
    # Ranking by count, with explicit exposure and rate to show partial days.
    top_hour00_days = sorted(
        (day for day in broker_dates if day["broker_hour_00"]["rows"] > 0),
        key=lambda day: (
            -day["broker_hour_00"]["alerts"],
            -day["broker_hour_00"]["alert_rate_percent"],
            day["broker_label_date"],
        ),
    )[:top_days]
    by_hour = {
        name: [
            {
                "hour": hour,
                "rows": counts[0],
                "alerts": counts[1],
                "alert_rate_percent": rate(counts)["alert_rate_percent"],
            }
            for hour, counts in sorted(bins.items())
        ]
        for name, bins in hours.items()
    }
    return {
        "assessment_version": VERSION,
        "registry_sha256": coverage["registry_sha256"],
        "feature_inventory_sha256": coverage["feature_inventory_sha256"],
        "row_count": total_rows,
        "source_export_count": coverage["source_export_count"],
        "segment_start_count": reset_count,
        "internal_gap_reset_count": internal_reset_count,
        "rolling_availability_verified_on_every_row": True,
        "rolling_feature_value_causality_certified": False,
        "source_export_boundaries_reset": True,
        "gaps_reset_rolling_state": True,
        "spread_alert_threshold_points": spread_alert_points,
        "spread_alert_count": alert_count,
        "spread_alert_percent": round(100 * alert_count / total_rows, 4),
        "spread_alerts_near_reset": alert_near_reset,
        "spread_near_reset_bars": near_reset_bars,
        "spread_exposure_by_reset": {
            "near_reset": rate(near),
            "outside_reset": rate(far),
        },
        "spread_exposure_by_broker_hour_group": {
            "broker_hour_00": rate(broker_00),
            "other_broker_hours": rate(other),
        },
        "spread_exposure_joint_reset_and_hour": {
            key: rate(pair) for key, pair in exposure.items()
        },
        "spread_exposure_by_season": {
            key: rate(pair) for key, pair in season_exposure.items()
        },
        "broker_hour_grouping_is_event_time_certified": False,
        "spread_association_is_causal": False,
        "spread_alerts_by_season": alerts_by_season,
        "spread_alerts_by_hour": by_hour,
        "largest_spread_alerts": leading_alerts,
        # Milestone 20 additive diagnostics. All dates/hours are broker labels.
        "broker_date_exposure": broker_dates,
        "broker_date_count": len(broker_dates),
        "top_broker_hour00_days": top_hour00_days,
        "broker_hour00_by_minute": [
            {"minute": minute, **rate(pair)}
            for minute, pair in sorted(hour00_minute_exposure.items())
        ],
        "broker_hour_group_by_season": {
            season: {group: rate(pair) for group, pair in bins.items()}
            for season, bins in hour00_season_exposure.items()
        },
        "outside_broker_hour00_alert_count": other[1],
        "outside_broker_hour00_alert_examples": outside_hour_alerts,
        "broker_date_coverage_certified": False,
        "spread_concentration_stability_established": False,
        "reset_examples": reset_examples,
        "historical_event_utc_certified": False,
        "first_available_at_verified": False,
        "research_only": True,
        "historical_admission": False,
        "training_authorized": False,
        "execution_authorized": False,
    }


def run(argv=None):
    parser = argparse.ArgumentParser(
        description="Research-only spread timing and feature integrity review"
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--index", action="append", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path,
                        default=Path("data/research/historical_utc_v2"))
    parser.add_argument("--spread-alert-points", type=int, default=50)
    parser.add_argument("--top-examples", type=int, default=10)
    parser.add_argument("--near-reset-bars", type=int, default=3)
    parser.add_argument("--top-days", type=int, default=8)
    parser.add_argument("--research-only", required=True, action="store_true")
    parser.add_argument("--json", action="store_true", help="Print diagnostics only")
    args = parser.parse_args(argv)
    try:
        report = assess_feature_integrity(
            args.registry, args.index, args.dataset_root,
            spread_alert_points=args.spread_alert_points,
            top_examples=args.top_examples,
            near_reset_bars=args.near_reset_bars,
            top_days=args.top_days,
        )
    except FeatureIntegrityError as exc:
        print(f"FEATURE INTEGRITY BLOCKED: {exc}")
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print("EURUSD ORACLE | Research feature integrity (read only)")
        print(f"ROWS: {report['row_count']} | EXPORTS: {report['source_export_count']}")
        print(f"RESET STARTS: {report['segment_start_count']} | "
              f"INTERNAL GAP RESETS: {report['internal_gap_reset_count']}")
        print(f"ROLLING AVAILABILITY CHECKED: "
              f"{report['rolling_availability_verified_on_every_row']}")
        print(f"SPREAD ALERT >= {report['spread_alert_threshold_points']} "
              f"broker points: {report['spread_alert_count']} "
              f"({report['spread_alert_percent']}%)")
        print(f"ALERTS NEAR SEGMENT START: {report['spread_alerts_near_reset']}")
        for label, values in report["spread_exposure_by_reset"].items():
            print(f"RESET EXPOSURE {label}: {values['alerts']}/{values['rows']} "
                  f"({values['alert_rate_percent']}%)")
        for label, values in report["spread_exposure_by_broker_hour_group"].items():
            print(f"BROKER HOUR EXPOSURE {label}: "
                  f"{values['alerts']}/{values['rows']} "
                  f"({values['alert_rate_percent']}%)")
        for label, values in report["spread_exposure_joint_reset_and_hour"].items():
            print(f"JOINT EXPOSURE {label}: {values['alerts']}/{values['rows']} "
                  f"({values['alert_rate_percent']}%)")
        print(f"BROKER-LABEL DATES OBSERVED: {report['broker_date_count']}")
        for season, groups in report["broker_hour_group_by_season"].items():
            h00 = groups["broker_hour_00"]
            other = groups["other_hours"]
            print(f"SEASON {season} hour00={h00['alerts']}/{h00['rows']} "
                  f"({_percent(h00['alert_rate_percent'])}) | "
                  f"other={other['alerts']}/{other['rows']} "
                  f"({_percent(other['alert_rate_percent'])})")
        for day in report["top_broker_hour00_days"]:
            h00 = day["broker_hour_00"]
            print(f"BROKER DAY {day['broker_label_date']} [{day['season']}] "
                  f"hour00={h00['alerts']}/{h00['rows']} "
                  f"({_percent(h00['alert_rate_percent'])})")
        for item in report["broker_hour00_by_minute"]:
            print(f"BROKER HOUR00 MINUTE {item['minute']:02d}: "
                  f"{item['alerts']}/{item['rows']} "
                  f"({_percent(item['alert_rate_percent'])})")
        print("OUTSIDE BROKER HOUR00 ALERTS: "
              f"{report['outside_broker_hour00_alert_count']}")
        for item in report["outside_broker_hour00_alert_examples"]:
            print(f"OUTSIDE HOUR00 REVIEW spread={item['spread_points']} "
                  f"broker_points | broker={item['broker_label']} | "
                  f"UTC_candidate={item['candidate_utc']} | "
                  f"source_row_id={item['source_row_id']}")
        print(f"WINTER ALERTS: {report['spread_alerts_by_season']['winter']} | "
              f"SUMMER ALERTS: {report['spread_alerts_by_season']['summer']}")
        for example in report["largest_spread_alerts"]:
            print(f"REVIEW spread={example['spread_points']} "
                  f"broker_points | broker={example['broker_label']} | "
                  f"UTC_candidate={example['candidate_utc']} | "
                  f"bars_since_reset={example['bars_since_reset']}")
        print("NO FILES WRITTEN | UTC UNCERTIFIED | "
              "TRAINING BLOCKED | EXECUTION BLOCKED")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
