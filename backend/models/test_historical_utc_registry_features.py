
"""Milestone 17: offline integration with actual research-export formats."""

from dataclasses import replace
import json
import shutil

import pytest

import backend.models.historical_utc_registry_features as module
from backend.models.historical_utc_registry_features import (
    RegistryFeatureError,
    assess_registry_features,
    run,
)
from backend.models.test_historical_utc_gap_evidence import setup


def sample(tmp_path, **kwargs):
    registry, indexes, data = setup(tmp_path)
    return assess_registry_features(registry, indexes, data, **kwargs)


def test_recomputes_every_source_without_merging(tmp_path):
    report = sample(tmp_path)
    assert report["row_count"] == 9
    assert report["source_export_count"] == 3
    assert report["winter_rows"] == 6
    assert report["summer_rows"] == 3
    assert report["fully_warmed_rows"] == 0
    assert report["indicator_available_rows"]["log_return_1"] == 5
    assert report["indicator_available_rows"]["sma_close_12"] == 0
    assert report["source_isolated_feature_segments"] == 4
    assert report["within_export_gap_count"] == 1
    assert report["within_export_missing_m5_slots"] == 1
    assert report["across_export_unverified_gap_count"] == 1
    assert report["across_export_missing_m5_slots"] == 576
    assert report["between_index_calendar_days_excluded"] > 100
    assert len(report["parent_features"]) == 3
    assert all(p["reset_at_start_of_export"] for p in report["parent_features"])
    assert report["historical_utc_certified"] is False
    assert report["actual_first_available_at_verified"] is False
    assert report["training_authorized"] is False
    assert report["execution_authorized"] is False


def test_heuristics_are_not_permissions(tmp_path):
    report = sample(tmp_path, min_rows=1, min_candidate_dates=1)
    assert report["coverage_heuristics"]["all_three_coverage_heuristics_met"] is True
    assert report["production_model_readiness_established"] is False
    assert report["historical_admission"] is False
    assert report["training_authorized"] is False


def test_default_heuristics_are_not_satisfied_in_tiny_fixture(tmp_path):
    report = sample(tmp_path)
    assert report["coverage_heuristics"]["all_three_coverage_heuristics_met"] is False
    assert report["coverage_heuristics"]["row_count_met"] is False
    assert report["coverage_heuristics"]["candidate_date_count_met"] is False


def test_empty_indicators_stay_null_after_gap_and_source_boundary(tmp_path):
    report = sample(tmp_path)
    assert report["indicator_available_rows"]["volatility_log_return_12"] == 0
    assert report["indicator_available_rows"]["atr_14_pips"] == 0
    assert report["parent_features"][2]["feature_segment_count"] == 2


def test_no_mutation_and_deterministic(tmp_path):
    registry, indexes, data = setup(tmp_path)
    paths = [p for root in (registry, *indexes) for p in root.iterdir()]
    paths += [p for root in data.iterdir() for p in root.iterdir()]
    before = {str(p): p.read_bytes() for p in paths}
    a = assess_registry_features(registry, indexes, data)
    b = assess_registry_features(registry, indexes, data)
    assert a == b
    assert {str(p): p.read_bytes() for p in paths} == before


def test_tampered_export_fails(tmp_path):
    registry, indexes, data = setup(tmp_path)
    f = next(data.iterdir()) / "candles.jsonl"
    f.write_bytes(f.read_bytes() + b"changed\n")
    with pytest.raises(RegistryFeatureError, match="verification failed"):
        assess_registry_features(registry, indexes, data)


def test_missing_source_fails(tmp_path):
    registry, indexes, data = setup(tmp_path)
    shutil.rmtree(next(data.iterdir()))
    with pytest.raises(RegistryFeatureError, match="verification failed"):
        assess_registry_features(registry, indexes, data)


def test_wrong_index_order_rejected(tmp_path):
    registry, indexes, data = setup(tmp_path)
    with pytest.raises(RegistryFeatureError, match="verification failed"):
        assess_registry_features(registry, indexes[::-1], data)


def test_builder_source_digest_mismatch_rejected(tmp_path, monkeypatch):
    registry, indexes, data = setup(tmp_path)
    original = module.build_clock_aware_research_features

    def forged(directory, *, max_rows):
        result = original(directory, max_rows=max_rows)
        return replace(result, source_rows_sha256="0" * 64)

    monkeypatch.setattr(module, "build_clock_aware_research_features", forged)
    with pytest.raises(RegistryFeatureError, match="source or safety"):
        assess_registry_features(registry, indexes, data)


@pytest.mark.parametrize("bad", [0, True, 500001, "10000", 1.5, -1])
def test_invalid_min_rows_fails_before_io(bad):
    with pytest.raises(RegistryFeatureError, match="min_rows"):
        assess_registry_features("x", (), "x", min_rows=bad)


@pytest.mark.parametrize("bad", [0, True, 3661, "30", 1.5, -1])
def test_invalid_min_dates_fails_before_io(bad):
    with pytest.raises(RegistryFeatureError, match="min_candidate_dates"):
        assess_registry_features("x", (), "x", min_candidate_dates=bad)


def test_cli_preview_and_json(tmp_path, capsys):
    registry, indexes, data = setup(tmp_path)
    args = ["--registry", str(registry), "--dataset-root", str(data), "--research-only"]
    for index in indexes:
        args.extend(["--index", str(index)])
    assert run(args) == 0
    output = capsys.readouterr().out
    assert "OBSERVATIONS: 9" in output
    assert "SOURCE EXPORTS: 3" in output
    assert "NO FILES WRITTEN" in output
    assert run(args + ["--json"]) == 0
    data_json = json.loads(capsys.readouterr().out)
    assert data_json["training_authorized"] is False
    assert data_json["source_export_count"] == 3


def test_cli_fail_closed(tmp_path, capsys):
    registry, indexes, data = setup(tmp_path)
    assert run([
        "--registry", str(registry),
        "--index", str(indexes[0]),
        "--index", str(indexes[0]),
        "--dataset-root", str(data),
        "--research-only",
    ]) == 1
    assert "BLOCKED" in capsys.readouterr().out


def test_cli_requires_research_only(tmp_path):
    registry, indexes, data = setup(tmp_path)
    with pytest.raises(SystemExit) as exc:
        run([
            "--registry", str(registry),
            "--index", str(indexes[0]),
            "--index", str(indexes[1]),
            "--dataset-root", str(data),
        ])
    assert exc.value.code == 2
