"""Milestone 18 isolated-source and spread review contract tests."""

from dataclasses import replace
import json
import shutil

import pytest

import backend.models.historical_utc_feature_integrity as module
from backend.models.historical_utc_feature_integrity import (
    FeatureIntegrityError,
    assess_feature_integrity,
    run,
)
from backend.models.test_historical_utc_gap_evidence import setup


def assess(tmp_path, **kwargs):
    registry, indexes, data = setup(tmp_path)
    return assess_feature_integrity(registry, indexes, data, **kwargs)


def cli_args(tmp_path):
    registry, indexes, data = setup(tmp_path)
    args = ["--registry", str(registry), "--dataset-root", str(data),
            "--research-only"]
    for index in indexes:
        args.extend(["--index", str(index)])
    return args


def test_validated_all_rows_and_reset_contract(tmp_path):
    report = assess(tmp_path)
    assert report["row_count"] == 9
    assert report["source_export_count"] == 3
    assert report["segment_start_count"] == 4
    assert report["internal_gap_reset_count"] == 1
    assert report["rolling_availability_verified_on_every_row"] is True
    assert report["rolling_feature_value_causality_certified"] is False
    assert report["spread_alert_count"] == 0
    assert report["largest_spread_alerts"] == []
    assert report["reset_examples"][0]["reason"] == "EXPORT_START"
    assert [r["reason"] for r in report["reset_examples"]].count(
        "INTERNAL_M5_GAP") == 1
    assert report["training_authorized"] is False
    assert report["execution_authorized"] is False


def test_spread_alerts_near_resets_and_season_bins(tmp_path):
    report = assess(tmp_path, spread_alert_points=8, near_reset_bars=1)
    assert report["spread_alert_count"] == 9
    assert report["spread_alerts_near_reset"] == 4
    assert report["spread_alerts_by_season"] == {"winter": 6, "summer": 3}
    for hours in report["spread_alerts_by_hour"].values():
        assert len(hours) == 24
        assert sum(item["rows"] for item in hours) == 9
        assert sum(item["alerts"] for item in hours) == 9
    assert report["spread_alert_percent"] == 100.0


def test_top_example_count_and_stable_order(tmp_path):
    registry, indexes, data = setup(tmp_path)
    a = assess_feature_integrity(registry, indexes, data,
                                 spread_alert_points=8, top_examples=2)
    b = assess_feature_integrity(registry, indexes, data,
                                 spread_alert_points=8, top_examples=2)
    assert a == b
    assert len(a["largest_spread_alerts"]) == 2
    assert a["largest_spread_alerts"][0]["spread_points"] == 8


def test_mid_threshold_has_no_alerts(tmp_path):
    result = assess(tmp_path, spread_alert_points=9)
    assert result["spread_alert_count"] == 0


def test_sources_not_mutated(tmp_path):
    registry, indexes, data = setup(tmp_path)
    files = [p for folder in (registry, *indexes) for p in folder.iterdir()]
    files += [p for folder in data.iterdir() for p in folder.iterdir()]
    before = {p: p.read_bytes() for p in files}
    assess_feature_integrity(registry, indexes, data)
    assert before == {p: p.read_bytes() for p in files}


def test_corrupt_source_fails_closed(tmp_path):
    registry, indexes, data = setup(tmp_path)
    candle = next(data.iterdir()) / "candles.jsonl"
    candle.write_bytes(candle.read_bytes() + b"tampered\n")
    with pytest.raises(FeatureIntegrityError, match="verification failed"):
        assess_feature_integrity(registry, indexes, data)


def test_missing_source_fails_closed(tmp_path):
    registry, indexes, data = setup(tmp_path)
    shutil.rmtree(next(data.iterdir()))
    with pytest.raises(FeatureIntegrityError, match="verification failed"):
        assess_feature_integrity(registry, indexes, data)


def test_reverse_indexes_fails_closed(tmp_path):
    registry, indexes, data = setup(tmp_path)
    with pytest.raises(FeatureIntegrityError, match="verification failed"):
        assess_feature_integrity(registry, indexes[::-1], data)


def test_forged_first_candle_roll_fails_closed(tmp_path, monkeypatch):
    registry, indexes, data = setup(tmp_path)
    original = module.build_clock_aware_research_features

    def modified(folder, *, max_rows):
        result = original(folder, max_rows=max_rows)
        first = replace(result.rows[0], log_return_1=0.0)
        return replace(result, rows=(first,) + result.rows[1:])

    monkeypatch.setattr(module, "build_clock_aware_research_features", modified)
    with pytest.raises(FeatureIntegrityError, match="Rolling feature availability"):
        assess_feature_integrity(registry, indexes, data)


def test_forged_spread_candle_detected_by_digest(tmp_path, monkeypatch):
    registry, indexes, data = setup(tmp_path)
    original = module.build_clock_aware_research_features

    def modified(folder, *, max_rows):
        result = original(folder, max_rows=max_rows)
        first = replace(result.rows[0], spread_points=100)
        return replace(result, rows=(first,) + result.rows[1:])

    monkeypatch.setattr(module, "build_clock_aware_research_features", modified)
    with pytest.raises(FeatureIntegrityError, match="feature proof"):
        assess_feature_integrity(registry, indexes, data)


@pytest.mark.parametrize("name,value", [
    ("spread_alert_points", 0),
    ("spread_alert_points", 100001),
    ("spread_alert_points", True),
    ("top_examples", 0),
    ("top_examples", 26),
    ("top_examples", "3"),
    ("near_reset_bars", 0),
    ("near_reset_bars", 101),
    ("near_reset_bars", True),
])
def test_invalid_limits_before_io(name, value):
    with pytest.raises(FeatureIntegrityError):
        assess_feature_integrity("x", (), "x", **{name: value})


def test_cli_preview(tmp_path, capsys):
    args = cli_args(tmp_path)
    assert run(args + ["--spread-alert-points", "8"]) == 0
    output = capsys.readouterr().out
    assert "ROWS: 9 | EXPORTS: 3" in output
    assert "SPREAD ALERT >= 8 broker points: 9" in output
    assert "TRAINING BLOCKED" in output
    assert "NO FILES WRITTEN" in output


def test_cli_json_flags(tmp_path, capsys):
    args = cli_args(tmp_path)
    assert run(args + ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["training_authorized"] is False
    assert data["execution_authorized"] is False
    assert data["rolling_feature_value_causality_certified"] is False
    assert data["segment_start_count"] == 4


def test_cli_fail_closed(tmp_path, capsys):
    args = cli_args(tmp_path)
    assert run(args + ["--spread-alert-points", "0"]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_cli_requires_research_only(tmp_path):
    args = cli_args(tmp_path)
    args.remove("--research-only")
    with pytest.raises(SystemExit) as err:
        run(args)
    assert err.value.code == 2


# Milestone 19: statistically meaningful rates need the row denominators.
def test_spread_exposure_joint_denominators(tmp_path):
    report = assess(tmp_path, spread_alert_points=8, near_reset_bars=1)
    assert report["spread_exposure_by_reset"] == {
        "near_reset": {"rows": 4, "alerts": 4, "alert_rate_percent": 100.0},
        "outside_reset": {"rows": 5, "alerts": 5, "alert_rate_percent": 100.0},
    }
    assert report["spread_exposure_by_broker_hour_group"] == {
        "broker_hour_00": {"rows": 6, "alerts": 6, "alert_rate_percent": 100.0},
        "other_broker_hours": {"rows": 3, "alerts": 3, "alert_rate_percent": 100.0},
    }
    assert report["spread_exposure_joint_reset_and_hour"] == {
        "broker_00_near_reset": {"rows": 3, "alerts": 3, "alert_rate_percent": 100.0},
        "broker_00_outside_reset": {"rows": 3, "alerts": 3, "alert_rate_percent": 100.0},
        "other_hours_near_reset": {"rows": 1, "alerts": 1, "alert_rate_percent": 100.0},
        "other_hours_outside_reset": {"rows": 2, "alerts": 2, "alert_rate_percent": 100.0},
    }
    assert report["spread_exposure_by_season"]["winter"]["rows"] == 6
    assert report["spread_exposure_by_season"]["summer"]["rows"] == 3
    assert report["spread_association_is_causal"] is False
    assert report["broker_hour_grouping_is_event_time_certified"] is False


def test_zero_exposure_stratum_is_not_marked_zero_rate(tmp_path):
    report = assess(tmp_path, spread_alert_points=8, near_reset_bars=3)
    assert report["spread_exposure_by_reset"]["near_reset"]["rows"] == 9
    assert report["spread_exposure_by_reset"]["outside_reset"] == {
        "rows": 0, "alerts": 0, "alert_rate_percent": None,
    }
    assert report["spread_exposure_joint_reset_and_hour"][
        "other_hours_outside_reset"
    ]["alert_rate_percent"] is None


def test_no_alert_rate_is_zero_when_exposure_exists(tmp_path):
    report = assess(tmp_path, spread_alert_points=9, near_reset_bars=1)
    assert report["spread_exposure_by_reset"]["near_reset"] == {
        "rows": 4, "alerts": 0, "alert_rate_percent": 0.0,
    }
    assert report["spread_exposure_by_broker_hour_group"][
        "broker_hour_00"
    ]["alert_rate_percent"] == 0.0


def test_hour_rate_denominators_agree_with_totals(tmp_path):
    report = assess(tmp_path, spread_alert_points=8, near_reset_bars=1)
    for hour_group in report["spread_alerts_by_hour"].values():
        assert len(hour_group) == 24
        assert sum(p["rows"] for p in hour_group) == report["row_count"]
        assert sum(p["alerts"] for p in hour_group) == report["spread_alert_count"]
        assert all(
            p["alert_rate_percent"] is None
            if p["rows"] == 0
            else p["alert_rate_percent"] == 100.0
            for p in hour_group
        )


def test_cli_reports_denominator_aware_spread_exposure(tmp_path, capsys):
    assert run(cli_args(tmp_path) + ["--spread-alert-points", "8",
                                      "--near-reset-bars", "1"]) == 0
    output = capsys.readouterr().out
    assert "RESET EXPOSURE near_reset: 4/4 (100.0%)" in output
    assert "BROKER HOUR EXPOSURE broker_hour_00: 6/6 (100.0%)" in output
    assert "JOINT EXPOSURE other_hours_outside_reset: 2/2 (100.0%)" in output
    assert "TRAINING BLOCKED" in output


def test_heuristic_threshold_changes_alerts_not_denominators(tmp_path):
    registry, indexes, data = setup(tmp_path)
    report_a = assess_feature_integrity(
        registry, indexes, data, spread_alert_points=8, near_reset_bars=1
    )
    report_b = assess_feature_integrity(
        registry, indexes, data, spread_alert_points=9, near_reset_bars=1
    )
    assert report_a["spread_exposure_by_reset"]["near_reset"]["rows"] == (
        report_b["spread_exposure_by_reset"]["near_reset"]["rows"]
    )
    assert report_a["spread_exposure_by_reset"]["near_reset"]["alerts"] == 4
    assert report_b["spread_exposure_by_reset"]["near_reset"]["alerts"] == 0


# Milestone 20: broker-label date-level, within-hour and seasonal attribution.
def test_broker_date_exposure_keeps_partial_days(tmp_path):
    report = assess(tmp_path, spread_alert_points=8, top_days=2)
    days = report["broker_date_exposure"]
    assert report["broker_date_count"] == 3
    assert len(days) == 3
    assert [x["broker_label_date"] for x in days] == [
        "2026-01-16", "2026-01-19", "2026-06-10",
    ]
    friday, monday, summer = days
    assert friday["season"] == "winter"
    assert friday["broker_hour_00"] == {
        "rows": 0, "alerts": 0, "alert_rate_percent": None,
    }
    assert friday["other_hours"] == {
        "rows": 3, "alerts": 3, "alert_rate_percent": 100.0,
    }
    assert monday["broker_hour_00"]["rows"] == 3
    assert summer["broker_hour_00"]["rows"] == 3
    assert [d["broker_label_date"] for d in report["top_broker_hour00_days"]] == [
        "2026-01-19", "2026-06-10",
    ]
    assert report["broker_date_coverage_certified"] is False
    assert report["spread_concentration_stability_established"] is False


def test_hour00_minute_buckets_are_complete_and_grounded(tmp_path):
    report = assess(tmp_path, spread_alert_points=8)
    bins = report["broker_hour00_by_minute"]
    assert len(bins) == 12
    assert [item["minute"] for item in bins] == list(range(0, 60, 5))
    assert [item["rows"] for item in bins[:4]] == [2, 2, 1, 1]
    assert sum(item["rows"] for item in bins) == 6
    assert sum(item["alerts"] for item in bins) == 6
    assert bins[4]["alert_rate_percent"] is None
    assert bins[0]["alert_rate_percent"] == 100.0


def test_hour00_by_season_does_not_mix_clocks(tmp_path):
    report = assess(tmp_path, spread_alert_points=8)
    season = report["broker_hour_group_by_season"]
    assert season["winter"]["broker_hour_00"]["rows"] == 3
    assert season["winter"]["other_hours"]["rows"] == 3
    assert season["summer"]["broker_hour_00"]["rows"] == 3
    assert season["summer"]["other_hours"]["rows"] == 0
    assert season["summer"]["other_hours"]["alert_rate_percent"] is None
    for group in ("broker_hour_00", "other_hours"):
        assert sum(season[s][group]["alerts"] for s in season) == (
            report["spread_exposure_by_broker_hour_group"][
                group if group == "broker_hour_00" else "other_broker_hours"
            ]["alerts"]
        )


def test_outside_hour00_alert_examples_have_full_provenance(tmp_path):
    report = assess(tmp_path, spread_alert_points=8, top_examples=2)
    assert report["outside_broker_hour00_alert_count"] == 3
    assert len(report["outside_broker_hour00_alert_examples"]) == 2
    assert all(x["broker_label"].startswith("2026-01-16T23:")
               for x in report["outside_broker_hour00_alert_examples"])
    assert all(x["source_lineage_sha256"] and x["source_row_id"] is not None
               for x in report["outside_broker_hour00_alert_examples"])
    assert all(x["spread_points"] == 8
               for x in report["outside_broker_hour00_alert_examples"])


def test_outside_hour00_absence_when_threshold_exceeds_spread(tmp_path):
    report = assess(tmp_path, spread_alert_points=9)
    assert report["outside_broker_hour00_alert_count"] == 0
    assert report["outside_broker_hour00_alert_examples"] == []
    assert all(x["alerts"] == 0 for x in report["broker_hour00_by_minute"])
    assert all(x["all_hours"]["alerts"] == 0
               for x in report["broker_date_exposure"])


@pytest.mark.parametrize("bad", [0, -1, 21, True, "8", 1.5])
def test_invalid_top_days_fails_before_io(bad):
    with pytest.raises(FeatureIntegrityError, match="top_days"):
        assess_feature_integrity("x", (), "x", top_days=bad)


def test_m20_cli_and_json_include_broker_date_evidence(tmp_path, capsys):
    args = cli_args(tmp_path) + ["--spread-alert-points", "8", "--top-days", "2"]
    assert run(args) == 0
    output = capsys.readouterr().out
    assert "BROKER-LABEL DATES OBSERVED: 3" in output
    assert "SEASON winter hour00=3/3" in output
    assert "BROKER HOUR00 MINUTE 00: 2/2" in output
    assert "OUTSIDE BROKER HOUR00 ALERTS: 3" in output
    assert "OUTSIDE HOUR00 REVIEW spread=8" in output
    assert "NO FILES WRITTEN" in output
    assert run(args + ["--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["broker_date_count"] == 3
    assert len(data["top_broker_hour00_days"]) == 2
    assert data["training_authorized"] is False
    assert data["execution_authorized"] is False


def test_two_non_midnight_alerts_with_distinct_source_rows(tmp_path):
    """Two high Friday spreads survive provenance checks and attribution."""
    from datetime import date, datetime, timedelta, timezone
    from backend.models.historical_utc_reconstruction import (
        LegacyM5Bar, reconstruct_historical_m5_bars,
    )
    from backend.models.test_audit_historical_utc_research_export import write
    from backend.models.test_consolidate_historical_utc_research import (
        one_export, project,
    )
    from backend.models.test_historical_utc_gap_evidence import (
        summer_export_with_internal_gap,
    )
    from backend.models.consolidate_historical_utc_research import (
        ResearchWindow, inspect_windows, publish_index,
    )
    from backend.models.register_historical_utc_seasons import (
        build_registry, publish_registry,
    )
    from backend.models.audit_historical_utc_research_export import (
        audit_research_export,
    )

    root = project(tmp_path)
    friday = tmp_path / "friday_spread"
    friday.mkdir()
    start = datetime(2026, 1, 16, 23, 45, tzinfo=timezone.utc)
    batch = reconstruct_historical_m5_bars([
        LegacyM5Bar(
            row_id=i + 1,
            broker_label_time=start + timedelta(minutes=5 * i),
            open=1.10, high=1.12, low=1.09, close=1.11,
            tick_volume=100, spread_points=spread, real_volume=0,
        )
        for i, spread in enumerate((70, 8, 55))
    ])
    write(friday, [r.record() for r in batch.records],
          batch.evidence_sha256, batch.mapping_implementation_sha256)
    monday = one_export(tmp_path, "monday", datetime(
        2026, 1, 19, tzinfo=timezone.utc), (4, 5, 6))
    summer = summer_export_with_internal_gap(tmp_path)
    winter_idx = publish_index(inspect_windows((
        ResearchWindow(date(2026, 1, 12), date(2026, 1, 19), friday),
        ResearchWindow(date(2026, 1, 19), date(2026, 1, 26), monday),
    )), root)
    summer_idx = publish_index(inspect_windows((ResearchWindow(
        date(2026, 6, 8), date(2026, 6, 15), summer,
    ),)), root)
    indexes = (winter_idx, summer_idx)
    registry = publish_registry(build_registry(indexes), root)
    data = root / "data/research/historical_utc_v2"
    data.mkdir()
    for source in (friday, monday, summer):
        lineage = audit_research_export(source).lineage_sha256
        shutil.copytree(source, data / ("dataset_" + lineage))

    report = assess_feature_integrity(
        registry, indexes, data, spread_alert_points=50,
    )
    assert report["spread_alert_count"] == 2
    assert report["outside_broker_hour00_alert_count"] == 2
    assert [x["spread_points"] for x in report[
        "outside_broker_hour00_alert_examples"]] == [70, 55]
    assert [x["source_row_id"] for x in report[
        "outside_broker_hour00_alert_examples"]] == [1, 3]
    assert report["spread_exposure_by_broker_hour_group"][
        "broker_hour_00"]["alerts"] == 0
    assert report["broker_date_exposure"][0]["other_hours"]["alerts"] == 2
