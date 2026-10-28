
"""Milestone 16 offline contract tests using Milestone 15 real-format fixtures."""

import json
from pathlib import Path

import pytest

from backend.models.historical_utc_research_readiness import (
    ResearchReadinessError,
    assess_research_readiness,
    run,
)
from backend.models.test_historical_utc_gap_evidence import setup


def assess(tmp_path, **options):
    registry, indexes, data = setup(tmp_path)
    return assess_research_readiness(registry, indexes, data, **options)


def test_genuine_source_backed_evidence(tmp_path):
    report = assess(tmp_path)

    assert report["row_count"] == 9
    assert report["index_count"] == 2
    assert report["winter_rows"] == 6
    assert report["summer_rows"] == 3
    assert report["observed_gap_count"] == 2
    assert report["unclassified_missing_m5_slots"] == 577

    assert (
        report["gap_classification_counts"]["SAME_BROKER_DAY_UNVERIFIED"]
        == 1
    )
    assert (
        report["gap_classification_counts"]["WEEKEND_SHAPED_UNVERIFIED"]
        == 1
    )
    assert report["excluded_between_index_calendar_days"] > 100

    assert report["evidence_verified"]["registry_index_and_export_chain"] is True
    assert report["evidence_verified"]["cross_index_separation"] is True
    assert report["coverage_heuristics"]["row_count_threshold_met"] is False
    assert report["coverage_heuristics"]["both_clock_regimes_observed"] is True


def test_heuristic_achieved_does_not_authorize_anything(tmp_path):
    report = assess(tmp_path, min_rows=1)

    assert report["coverage_heuristics"]["row_count_threshold_met"] is True
    assert (
        report["coverage_heuristics"]["overall_exploratory_coverage_established"]
        is False
    )
    assert report["training_authorized"] is False
    assert report["execution_authorized"] is False
    assert report["historical_admission"] is False
    assert report["evidence_verified"]["full_registry_feature_set_audited"] is False
    assert report["evidence_verified"]["historical_event_utc_certified"] is False


def test_deterministic_and_no_files_written(tmp_path):
    registry, indexes, data = setup(tmp_path)

    files = [
        p
        for root in (registry, *indexes)
        for p in root.iterdir()
    ]
    files += [
        p
        for source in data.iterdir()
        for p in source.iterdir()
    ]

    before = {p: p.read_bytes() for p in files}

    one = assess_research_readiness(registry, indexes, data)
    two = assess_research_readiness(registry, indexes, data)

    assert one == two
    assert {p: p.read_bytes() for p in files} == before


def test_tampered_registry_fails_closed(tmp_path):
    registry, indexes, data = setup(tmp_path)
    (registry / "manifest.json").write_bytes(b"forged")

    with pytest.raises(
        ResearchReadinessError,
        match="verification failed",
    ):
        assess_research_readiness(registry, indexes, data)


def test_tampered_export_fails_closed(tmp_path):
    registry, indexes, data = setup(tmp_path)
    export = next(data.iterdir()) / "candles.jsonl"
    export.write_bytes(export.read_bytes() + b"damage\n")

    with pytest.raises(
        ResearchReadinessError,
        match="verification failed",
    ):
        assess_research_readiness(registry, indexes, data)


def test_wrong_index_order_fails_closed(tmp_path):
    registry, indexes, data = setup(tmp_path)

    with pytest.raises(
        ResearchReadinessError,
        match="verification failed",
    ):
        assess_research_readiness(registry, indexes[::-1], data)


@pytest.mark.parametrize(
    "bad",
    [0, -1, True, 500001, "10000", 1.5],
)
def test_invalid_min_rows_fails_closed(tmp_path, bad):
    with pytest.raises(
        ResearchReadinessError,
        match="min_rows",
    ):
        assess_research_readiness("x", (), "x", min_rows=bad)


def test_cli_preview(tmp_path, capsys):
    registry, indexes, data = setup(tmp_path)

    args = [
        "--registry", str(registry),
        "--dataset-root", str(data),
        "--research-only",
    ]

    for index in indexes:
        args += ["--index", str(index)]

    assert run(args) == 0

    out = capsys.readouterr().out
    assert "ROWS: 9" in out
    assert "ROW-COUNT HEURISTIC MET: False" in out
    assert "TRAINING BLOCKED" in out
    assert "NO FILES WRITTEN" in out


def test_cli_json_is_explicitly_not_authorization(tmp_path, capsys):
    registry, indexes, data = setup(tmp_path)

    args = [
        "--registry", str(registry),
        "--dataset-root", str(data),
        "--research-only",
        "--json",
        "--min-rows", "1",
    ]

    for index in indexes:
        args += ["--index", str(index)]

    assert run(args) == 0

    data = json.loads(capsys.readouterr().out)

    assert data["coverage_heuristics"]["row_count_threshold_met"] is True
    assert data["training_authorized"] is False
    assert data["execution_authorized"] is False


def test_cli_missing_research_only(tmp_path):
    registry, indexes, data = setup(tmp_path)

    with pytest.raises(SystemExit) as exc:
        run([
            "--registry", str(registry),
            "--index", str(indexes[0]),
            "--index", str(indexes[1]),
            "--dataset-root", str(data),
        ])

    assert exc.value.code == 2


def test_cli_failure_does_not_appear_successful(tmp_path, capsys):
    registry, indexes, data = setup(tmp_path)

    assert run([
        "--registry", str(registry),
        "--index", str(indexes[0]),
        "--index", str(indexes[0]),
        "--dataset-root", str(data),
        "--research-only",
    ]) == 1

    assert "BLOCKED" in capsys.readouterr().out


def test_excluded_day_field_matches_gap_evidence_contract(tmp_path):
    """Prevent a mismatch between Milestone 15 and Milestone 16 field names."""
    from backend.models.historical_utc_gap_evidence import inspect_gap_evidence

    registry, indexes, data = setup(tmp_path)
    evidence = inspect_gap_evidence(registry, indexes, data)
    report = assess_research_readiness(registry, indexes, data)

    assert report["excluded_between_index_calendar_days"] == (
        evidence["excluded_calendar_days_between_indexes"]
    )


def test_missing_excluded_day_evidence_fails_closed(tmp_path, monkeypatch):
    """Missing source metadata must not default to zero excluded days."""
    import backend.models.historical_utc_research_readiness as readiness
    from backend.models.historical_utc_gap_evidence import inspect_gap_evidence

    registry, indexes, data = setup(tmp_path)
    evidence = inspect_gap_evidence(registry, indexes, data)
    evidence.pop("excluded_calendar_days_between_indexes")

    monkeypatch.setattr(
        readiness,
        "inspect_gap_evidence",
        lambda *args: evidence,
    )

    with pytest.raises(ResearchReadinessError, match="Inconsistent"):
        assess_research_readiness(registry, indexes, data)
