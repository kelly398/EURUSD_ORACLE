
"""Milestone 09: source-backed, read-only feature QA tests."""

import json
from pathlib import Path

import pytest

from backend.models.test_audit_historical_utc_research_export import (
    make_export,
)

from backend.models.historical_utc_research_features import (
    build_clock_aware_research_features,
)

from backend.models.historical_utc_research_feature_export import (
    export_research_features,
)

from backend.models.historical_utc_research_quality import (
    ResearchQualityError,
    _summary,
    assess_research_feature_quality,
    main,
)


def sample(tmp_path, minutes=range(0, 120, 5)):
    candle_parent = tmp_path / "candles"
    candle_parent.mkdir()

    source, _, _ = make_export(
        candle_parent,
        minutes=minutes,
    )

    project = tmp_path / "EURUSD_ORACLE"

    (
        project / "backend" / "models"
    ).mkdir(parents=True)

    features = build_clock_aware_research_features(
        source
    )

    export = export_research_features(
        features,
        source,
        project,
    )

    return source, export, project


def test_short_sample_is_audited_but_not_adequate(
    tmp_path,
):
    source, export, project = sample(tmp_path)

    before = {
        p.name: p.read_bytes()
        for p in export.iterdir()
    }

    report = assess_research_feature_quality(
        export, source
    )

    assert report["row_count"] == 24

    assert report["distinct_candidate_dates"] == 1

    assert (
        report["minimum_exploratory_coverage_met"]
        is False
    )

    assert report[
        "candidate_offset_hours_observed"
    ] == [2]

    assert report["fully_warmed_rows"] == 11

    availability = report["indicator_availability"]

    assert availability["atr_14_pips"]["rows"] == 11
    assert availability["sma_close_12"]["rows"] == 13

    assert availability[
        "volatility_log_return_12"
    ]["rows"] == 12

    assert report["training_authorized"] is False
    assert report["execution_authorized"] is False

    assert before == {
        p.name: p.read_bytes()
        for p in export.iterdir()
    }

    assert sorted(
        p.name
        for p in project.rglob("*")
        if p.is_file()
    ) == [
        "checksums.sha256",
        "features.jsonl",
        "manifest.json",
    ]


def test_distribution_consistency_and_zero_counts(
    tmp_path,
):
    source, export, _ = sample(tmp_path)

    report = assess_research_feature_quality(
        export, source
    )

    assert report["zero_tick_volume"] == 0
    assert report["zero_spread_points"] == 0

    assert report[
        "distributions"
    ]["spread_points"]["median"] == 8

    assert report[
        "distributions"
    ]["range_pips"]["p95"] == 300

    assert report[
        "distributions"
    ]["log_return_1"]["count"] == 23


def test_gaps_dont_count_as_usable_m5(tmp_path):
    source, export, _ = sample(
        tmp_path,
        minutes=[0, 5, 20, 25, 30],
    )

    report = assess_research_feature_quality(
        export, source
    )

    assert report["gap_count"] == 1
    assert report["segment_count"] == 2

    assert report[
        "unobserved_m5_slots_including_market_closures"
    ] == 2

    assert report["fully_warmed_rows"] == 0

    assert any(
        "Gaps" in issue
        for issue in report["review_findings"]
    )


def test_short_window_cannot_pass_without_summer(
    tmp_path,
):
    source, export, _ = sample(tmp_path)

    report = assess_research_feature_quality(
        export,
        source,
        min_rows=1,
        min_candidate_days=1,
    )

    assert (
        report["minimum_exploratory_coverage_met"]
        is False
    )

    assert any(
        "winter and summer" in issue
        for issue in report["review_findings"]
    )


def test_tampered_export_fails_source_check(tmp_path):
    source, export, _ = sample(tmp_path)

    with (
        export / "features.jsonl"
    ).open("ab") as f:
        f.write(b"corruption\n")

    with pytest.raises(
        ResearchQualityError,
        match="verification",
    ):
        assess_research_feature_quality(
            export, source
        )


def test_tampered_source_fails_source_check(tmp_path):
    source, export, _ = sample(tmp_path)

    with (
        source / "candles.jsonl"
    ).open("ab") as f:
        f.write(b"corruption\n")

    with pytest.raises(
        ResearchQualityError,
        match="verification",
    ):
        assess_research_feature_quality(
            export, source
        )


def test_changed_bytes_after_verify_fails(
    tmp_path,
    monkeypatch,
):
    source, export, _ = sample(tmp_path)

    from backend.models import (
        historical_utc_research_quality as q,
    )

    original_verify = q.verify_feature_export

    def change_after_verify(f, src):
        report = original_verify(f, src)

        with (
            Path(f) / "features.jsonl"
        ).open("ab") as stream:
            stream.write(b"\n")

        return report

    monkeypatch.setattr(
        q,
        "verify_feature_export",
        change_after_verify,
    )

    with pytest.raises(ResearchQualityError):
        assess_research_feature_quality(
            export, source
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("min_rows", True),
        ("min_rows", 0),
        ("min_rows", 500001),
        ("min_candidate_days", 0),
        ("min_candidate_days", 3661),
        ("min_candidate_days", 1.5),
    ],
)
def test_invalid_thresholds_rejected(
    tmp_path,
    field,
    value,
):
    with pytest.raises(ResearchQualityError):
        assess_research_feature_quality(
            tmp_path,
            tmp_path,
            **{field: value},
        )


def test_missing_source_or_export_rejected(tmp_path):
    source, export, _ = sample(tmp_path)

    with pytest.raises(ResearchQualityError):
        assess_research_feature_quality(
            export,
            tmp_path / "missing",
        )

    with pytest.raises(ResearchQualityError):
        assess_research_feature_quality(
            tmp_path / "missing",
            source,
        )


def test_cli_prints_safe_diagnostics(
    tmp_path,
    capsys,
):
    source, export, _ = sample(tmp_path)

    assert main([
        "--features-directory",
        str(export),
        "--source-directory",
        str(source),
    ]) == 0

    printed = capsys.readouterr().out

    assert "Rows: 24" in printed
    assert "TRAINING BLOCKED" in printed
    assert "NO FILES WRITTEN" in printed

    assert (
        "Minimum exploratory coverage met: False"
        in printed
    )


def test_cli_json_report(tmp_path, capsys):
    source, export, _ = sample(tmp_path)

    assert main([
        "--features-directory",
        str(export),
        "--source-directory",
        str(source),
        "--json",
    ]) == 0

    result = json.loads(
        capsys.readouterr().out
    )

    assert result["row_count"] == 24
    assert result["source_receipt_certified"] is False
    assert result["research_only"] is True


def test_cli_rejects_corrupt_source(
    tmp_path,
    capsys,
):
    source, export, _ = sample(tmp_path)

    (source / "manifest.json").unlink()

    assert main([
        "--features-directory",
        str(export),
        "--source-directory",
        str(source),
    ]) == 1

    assert "BLOCKED" in capsys.readouterr().out


def test_nearest_rank_p95_and_empty_series():
    assert _summary([])["p95"] is None

    assert _summary([
        1, 2, 3, 4, 5
    ])["median"] == 3

    assert _summary([
        1, 2, 3, 4, 5
    ])["p95"] == 5
