
"""Milestones 25-28 offline research-only contracts and source integration."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import shutil

import pytest

import backend.models.historical_research_agent_preview as module

from backend.models.historical_research_agent_preview import (
    ResearchPreviewError,
    ResearchSnapshot,
    assess_historical_agent_previews,
    preview_snapshot,
    run,
)

from backend.models.test_historical_utc_gap_evidence import setup


def fake_snapshot(**changes):
    base = ResearchSnapshot(
        source_lineage_sha256="a" * 64,
        source_rows_sha256="b" * 64,
        feature_rows_sha256="c" * 64,
        source_row_id=44,
        broker_label_time="2026-01-19T00:00:00+00:00",
        candidate_utc_open="2026-01-18T22:00:00+00:00",
        candidate_offset_hours=2,
        segment_id=1,
        close=1.1000,
        sma_close_12=1.0998,
        log_return_1=0.0002,
        body_pips=2.0,
        atr_14_pips=4.0,
        spread_points=16,
    )

    return replace(base, **changes)


def real_fixture(tmp_path):
    registry, indexes, root = setup(tmp_path)
    return registry, indexes, root


def cli(tmp_path):
    registry, indexes, root = real_fixture(tmp_path)

    args = [
        "--registry",
        str(registry),
        "--dataset-root",
        str(root),
        "--research-only",
    ]

    for index in indexes:
        args.extend([
            "--index",
            str(index),
        ])

    return args


def test_m25_frozen_contract_and_hypothetical_clock():
    x = fake_snapshot()

    with pytest.raises(Exception):
        x.close = 1.2

    assert x.first_available_at is None
    assert x.historical_utc_certified is False
    assert x.execution_authorized is False

    with pytest.raises(
        ResearchPreviewError,
        match="clock",
    ):
        preview_snapshot(
            replace(x, candidate_offset_hours=3)
        )

    with pytest.raises(
        ResearchPreviewError,
        match="identity",
    ):
        preview_snapshot(
            replace(x, source_lineage_sha256="bad")
        )


def test_m26_three_independent_agreeing_probes_still_hold():
    item = preview_snapshot(fake_snapshot())

    assert [
        p["probe"] for p in item["diagnostic_probes"]
    ] == list(module.PROBES)

    assert [
        p["direction"] for p in item["diagnostic_probes"]
    ] == ["UP"] * 3

    assert item["research_consensus_status"] == (
        "DIRECTIONAL_ALIGNMENT_RESEARCH_ONLY"
    )

    assert item["action"] == "HOLD"
    assert item["prediction_probability"] is None
    assert item["training_authorized"] is False
    assert item["execution_authorized"] is False


def test_m27_conflicting_directions_are_visible_and_hold():
    item = preview_snapshot(
        fake_snapshot(log_return_1=-0.0002)
    )

    assert item["research_consensus_status"] == (
        "DIRECTIONAL_DISAGREEMENT_RESEARCH_ONLY"
    )

    assert [
        p["direction"] for p in item["diagnostic_probes"]
    ] == ["UP", "DOWN", "UP"]

    assert item["action"] == "HOLD"


def test_m27_neutral_is_not_elected_by_other_votes():
    item = preview_snapshot(
        fake_snapshot(body_pips=0.1)
    )

    assert item["research_consensus_status"] == (
        "INSUFFICIENT_DIRECTIONAL_AGREEMENT"
    )

    assert item["action"] == "HOLD"


def test_spread_review_suppresses_alignment_even_if_all_agree():
    item = preview_snapshot(
        fake_snapshot(spread_points=75)
    )

    assert item["spread_review_flag"] is True

    assert item["research_consensus_status"] == (
        "SPREAD_REVIEW_REQUIRED"
    )

    assert item["action"] == "HOLD"


def test_insufficient_warmup_abstains_without_earlier_backfill():
    item = preview_snapshot(
        fake_snapshot(sma_close_12=None)
    )

    assert item["diagnostic_probes"] == []

    assert item["missing_indicators"] == [
        "sma_close_12"
    ]

    assert item["research_consensus_status"] == (
        "INSUFFICIENT_RESEARCH_FEATURES"
    )


def test_zero_atr_abstains_without_dividing_by_zero():
    item = preview_snapshot(
        fake_snapshot(atr_14_pips=0.0)
    )

    assert item["missing_indicators"] == [
        "invalid_indicator_scale"
    ]


@pytest.mark.parametrize(
    "bad",
    [0, -1, True, 100001, "50", 4.5],
)
def test_spread_threshold_guard_before_io(bad):
    with pytest.raises(
        ResearchPreviewError,
        match="spread_review_points",
    ):
        assess_historical_agent_previews(
            "x",
            (),
            "x",
            spread_review_points=bad,
        )


@pytest.mark.parametrize(
    "change",
    [
        {"historical_utc_certified": True},
        {"training_authorized": True},
        {"execution_authorized": True},
        {
            "first_available_at": datetime(
                2026, 1, 19, tzinfo=timezone.utc
            )
        },
    ],
)
def test_no_admission_flag_can_be_injected(change):
    with pytest.raises(
        ResearchPreviewError,
        match="admission",
    ):
        preview_snapshot(
            fake_snapshot(**change)
        )


def test_nonfinite_indicator_rejected():
    with pytest.raises(
        ResearchPreviewError,
        match="Nonfinite",
    ):
        preview_snapshot(
            fake_snapshot(
                log_return_1=float("nan")
            )
        )


def test_m28_real_exports_reaudited_in_isolation_and_no_writes(
    tmp_path,
):
    registry, indexes, root = real_fixture(tmp_path)

    sources = [
        p
        for folder in (registry, *indexes)
        for p in folder.iterdir()
    ]

    sources += [
        p
        for folder in root.iterdir()
        for p in folder.iterdir()
    ]

    original = {
        str(p): p.read_bytes()
        for p in sources
    }

    first = assess_historical_agent_previews(
        registry,
        indexes,
        root,
    )

    second = assess_historical_agent_previews(
        registry,
        indexes,
        root,
    )

    assert first == second
    assert first["source_export_count"] == 3
    assert first["underlying_audited_rows"] == 9
    assert len(first["previews"]) == 3

    assert all(
        p["action"] == "HOLD"
        for p in first["previews"]
    )

    assert all(
        p["diagnostic_probes"] == []
        for p in first["previews"]
    )

    assert first[
        "canonical_31_seat_council_replaced"
    ] is False

    assert first["real_council_invoked"] is False
    assert first["execution_authorized"] is False
    assert first["training_authorized"] is False

    assert original == {
        str(p): p.read_bytes()
        for p in sources
    }


def test_reordered_indexes_fail_closed(tmp_path):
    registry, indexes, root = real_fixture(tmp_path)

    with pytest.raises(
        ResearchPreviewError,
        match="verification failed",
    ):
        assess_historical_agent_previews(
            registry,
            indexes[::-1],
            root,
        )


def test_tampered_source_fails_closed(tmp_path):
    registry, indexes, root = real_fixture(tmp_path)

    p = next(root.iterdir()) / "candles.jsonl"

    p.write_bytes(
        p.read_bytes() + b"BAD\n"
    )

    with pytest.raises(
        ResearchPreviewError,
        match="verification failed",
    ):
        assess_historical_agent_previews(
            registry,
            indexes,
            root,
        )


def test_forged_feature_digest_fails_closed(
    tmp_path,
    monkeypatch,
):
    registry, indexes, root = real_fixture(tmp_path)

    original = (
        module.build_clock_aware_research_features
    )

    def forged(folder, *, max_rows):
        result = original(
            folder,
            max_rows=max_rows,
        )

        return replace(
            result,
            feature_rows_sha256="0" * 64,
        )

    monkeypatch.setattr(
        module,
        "build_clock_aware_research_features",
        forged,
    )

    with pytest.raises(
        ResearchPreviewError,
        match="identity mismatch",
    ):
        assess_historical_agent_previews(
            registry,
            indexes,
            root,
        )


def test_cli_and_json_are_preview_only(
    tmp_path,
    capsys,
):
    args = cli(tmp_path)

    assert run(args) == 0

    output = capsys.readouterr().out

    assert (
        "AUDITED ROWS: 9 | SEPARATE EXPORTS: 3"
        in output
    )

    assert "ACTION=HOLD" in output
    assert "CANONICAL COUNCIL NOT INVOKED" in output
    assert "NO FILES WRITTEN" in output

    assert run(args + ["--json"]) == 0

    report = json.loads(
        capsys.readouterr().out
    )

    assert report["action"] == "HOLD"
    assert report["historical_utc_certified"] is False


def test_cli_invalid_limit_fails_closed(
    tmp_path,
    capsys,
):
    assert run(
        cli(tmp_path)
        + ["--spread-review-points", "0"]
    ) == 1

    assert "BLOCKED" in capsys.readouterr().out


def test_cli_requires_explicit_research_only(tmp_path):
    args = cli(tmp_path)

    args.remove("--research-only")

    with pytest.raises(SystemExit) as exc:
        run(args)

    assert exc.value.code == 2


def test_m28_warmed_export_runs_all_three_probes_end_to_end(
    tmp_path,
):
    """Source-backed warmed candles exercise the full preview, not just abstain."""

    from datetime import date

    from backend.models.historical_utc_reconstruction import (
        LegacyM5Bar,
        reconstruct_historical_m5_bars,
    )

    from backend.models.test_audit_historical_utc_research_export import (
        write,
    )

    from backend.models.test_consolidate_historical_utc_research import (
        project,
    )

    from backend.models.consolidate_historical_utc_research import (
        ResearchWindow,
        inspect_windows,
        publish_index,
    )

    from backend.models.register_historical_utc_seasons import (
        build_registry,
        publish_registry,
    )

    from backend.models.audit_historical_utc_research_export import (
        audit_research_export,
    )

    project_root = project(tmp_path)
    index_dirs = []

    research_root = (
        project_root
        / "data/research/historical_utc_v2"
    )

    research_root.mkdir(parents=True)

    for season, day, row_start in (
        ("winter", date(2026, 1, 19), 100),
        ("summer", date(2026, 6, 15), 200),
    ):
        folder = tmp_path / ("source_" + season)
        folder.mkdir()

        start = datetime(
            day.year,
            day.month,
            day.day,
            tzinfo=timezone.utc,
        )

        bars = [
            LegacyM5Bar(
                row_id=row_start + i,
                broker_label_time=(
                    start + timedelta(minutes=5 * i)
                ),
                open=1.10000 + i * 0.00005,
                high=1.10030 + i * 0.00005,
                low=1.09990 + i * 0.00005,
                close=1.10020 + i * 0.00005,
                tick_volume=100,
                spread_points=16,
                real_volume=0,
            )
            for i in range(20)
        ]

        batch = reconstruct_historical_m5_bars(bars)

        write(
            folder,
            [
                r.record()
                for r in batch.records
            ],
            batch.evidence_sha256,
            batch.mapping_implementation_sha256,
        )

        index_dirs.append(
            publish_index(
                inspect_windows((
                    ResearchWindow(
                        day,
                        day + timedelta(days=7),
                        folder,
                    ),
                )),
                project_root,
            )
        )

        lineage = audit_research_export(
            folder
        ).lineage_sha256

        shutil.copytree(
            folder,
            research_root / ("dataset_" + lineage),
        )

    registry = publish_registry(
        build_registry(tuple(index_dirs)),
        project_root,
    )

    report = assess_historical_agent_previews(
        registry,
        index_dirs,
        research_root,
    )

    assert report["underlying_audited_rows"] == 40
    assert report["source_export_count"] == 2

    assert all(
        len(p["diagnostic_probes"]) == 3
        for p in report["previews"]
    )

    assert all(
        p["action"] == "HOLD"
        for p in report["previews"]
    )

    assert all(
        p["spread_review_flag"] is False
        for p in report["previews"]
    )

    assert report[
        "canonical_31_seat_council_replaced"
    ] is False
