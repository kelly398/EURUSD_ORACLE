
"""Milestone 12: offline consolidation tests with audited mock exports."""

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from backend.models.historical_utc_reconstruction import (
    LegacyM5Bar,
    reconstruct_historical_m5_bars,
)

from backend.models.test_audit_historical_utc_research_export import (
    write,
)

from backend.models.consolidate_historical_utc_research import (
    ConsolidationError,
    ResearchWindow,
    inspect_windows,
    publish_index,
    run,
)

UTC = timezone.utc


def one_export(tmp_path, name, starting, identifiers):
    folder = tmp_path / name
    folder.mkdir()

    bars = [
        LegacyM5Bar(
            row_id=ident,
            broker_label_time=(
                starting + timedelta(minutes=5 * index)
            ),
            open=1.10,
            high=1.12,
            low=1.09,
            close=1.11,
            tick_volume=100,
            spread_points=8,
            real_volume=0,
        )
        for index, ident in enumerate(identifiers)
    ]

    batch = reconstruct_historical_m5_bars(
        bars
    )

    write(
        folder,
        [r.record() for r in batch.records],
        batch.evidence_sha256,
        batch.mapping_implementation_sha256,
    )

    return folder


def sample(tmp_path):
    # Friday to Monday creates an unclassified gap.

    friday = one_export(
        tmp_path,
        "week_a",
        datetime(
            2026, 1, 16, 23, 45,
            tzinfo=UTC,
        ),
        (1, 2, 3),
    )

    monday = one_export(
        tmp_path,
        "week_b",
        datetime(
            2026, 1, 19, 0, 0,
            tzinfo=UTC,
        ),
        (4, 5, 6),
    )

    return (
        ResearchWindow(
            date(2026, 1, 12),
            date(2026, 1, 19),
            friday,
        ),
        ResearchWindow(
            date(2026, 1, 19),
            date(2026, 1, 26),
            monday,
        ),
    )


def project(tmp_path):
    root = tmp_path / "EURUSD_ORACLE"

    (
        root / "backend" / "models"
    ).mkdir(parents=True)

    return root


def test_cross_window_gap_and_portable_fingerprint(
    tmp_path,
):
    windows = sample(tmp_path)

    first = inspect_windows(windows)
    second = inspect_windows(windows)

    assert first == second

    assert first["row_count"] == 6
    assert first["window_count"] == 2

    assert first["gap_count"] == 1

    assert (
        first["unclassified_missing_m5_slots"]
        == 576
    )

    assert (
        first["source_windows"][1][
            "unclassified_boundary_missing_m5_slots"
        ] == 576
    )

    assert first["winter_count"] == 6
    assert first["summer_count"] == 0

    assert first["research_only"] is True
    assert first["historical_utc_certified"] is False

    assert first["training_authorized"] is False
    assert first["execution_authorized"] is False

    assert (
        "directory"
        not in first["source_windows"][0]
    )


def test_input_audit_is_read_only(tmp_path):
    windows = sample(tmp_path)

    before = {
        str(p): p.read_bytes()
        for w in windows
        for p in w.directory.iterdir()
    }

    inspect_windows(windows)

    after = {
        str(p): p.read_bytes()
        for w in windows
        for p in w.directory.iterdir()
    }

    assert before == after


def test_duplicate_id_rejected_across_windows(
    tmp_path,
):
    windows = sample(tmp_path)

    copied = one_export(
        tmp_path,
        "duplicate_ids",
        datetime(
            2026, 1, 19,
            tzinfo=UTC,
        ),
        (1, 7),
    )

    with pytest.raises(
        ConsolidationError,
        match="row ID",
    ):
        inspect_windows((
            windows[0],
            ResearchWindow(
                date(2026, 1, 19),
                date(2026, 1, 26),
                copied,
            ),
        ))


def test_reversed_windows_rejected(tmp_path):
    windows = sample(tmp_path)

    with pytest.raises(
        ConsolidationError,
        match="not contiguous",
    ):
        inspect_windows(
            windows[::-1]
        )


def test_skipped_window_rejected(tmp_path):
    windows = sample(tmp_path)

    with pytest.raises(
        ConsolidationError,
        match="not contiguous",
    ):
        inspect_windows((
            windows[0],
            ResearchWindow(
                date(2026, 1, 20),
                date(2026, 1, 26),
                windows[1].directory,
            ),
        ))


def test_mislabeled_window_rejected(tmp_path):
    windows = sample(tmp_path)

    wrong = ResearchWindow(
        date(2026, 1, 15),
        date(2026, 1, 16),
        windows[0].directory,
    )

    with pytest.raises(
        ConsolidationError,
        match="outside",
    ):
        inspect_windows((wrong,))


def test_duplicate_parent_rejected(tmp_path):
    windows = sample(tmp_path)

    with pytest.raises(
        ConsolidationError,
        match="Duplicate source export",
    ):
        inspect_windows((
            windows[0],
            ResearchWindow(
                date(2026, 1, 19),
                date(2026, 1, 26),
                windows[0].directory,
            ),
        ))


def test_corrupt_child_rejected(tmp_path):
    windows = sample(tmp_path)

    with (
        windows[1].directory / "candles.jsonl"
    ).open("ab") as fp:
        fp.write(b"broken\n")

    with pytest.raises(
        ConsolidationError,
        match="audit",
    ):
        inspect_windows(windows)


def test_max_rows_enforced(tmp_path):
    with pytest.raises(
        ConsolidationError,
        match="limit",
    ):
        inspect_windows(
            sample(tmp_path),
            max_rows=5,
        )


@pytest.mark.parametrize(
    "bad",
    [0, -1, True, 500001, 1.5],
)
def test_invalid_max_rows(bad):
    with pytest.raises(ConsolidationError):
        inspect_windows(
            [],
            max_rows=bad,
        )


def test_index_publish_no_overwrite(tmp_path):
    windows = sample(tmp_path)

    index = inspect_windows(windows)

    root = project(tmp_path)

    path = publish_index(
        index,
        root,
    )

    saved = json.loads(
        (path / "manifest.json").read_text()
    )

    assert saved == index

    assert {
        p.name
        for p in path.iterdir()
    } == {
        "manifest.json",
        "checksums.sha256",
    }

    with pytest.raises(
        ConsolidationError,
        match="already exists",
    ):
        publish_index(index, root)

    assert saved == json.loads(
        (path / "manifest.json").read_text()
    )

    assert not (
        path.parent / ".index_export.lock"
    ).exists()


def test_modified_manifest_rejected(tmp_path):
    index = inspect_windows(
        sample(tmp_path)
    )

    index["training_authorized"] = True

    with pytest.raises(
        ConsolidationError,
        match="Unsafe",
    ):
        publish_index(
            index,
            project(tmp_path),
        )


def test_cli_preview_and_publication(
    tmp_path,
    capsys,
):
    windows = sample(tmp_path)

    opts = ["--research-only"]

    for w in windows:
        opts += [
            "--window",
            f"{w.start_date}:{w.end_date}:{w.directory}",
        ]

    assert run(opts) == 0

    assert "PREVIEW ONLY" in (
        capsys.readouterr().out
    )

    root = project(tmp_path)

    assert run(
        opts + ["--publish-index"],
        project_root=root,
    ) == 0

    assert "INDEX PUBLISHED" in (
        capsys.readouterr().out
    )

    assert run(
        opts + ["--publish-index"],
        project_root=root,
    ) == 1

    assert "already exists" in (
        capsys.readouterr().out
    )


def test_invalid_dates_and_window_length_rejected():
    with pytest.raises(ConsolidationError):
        ResearchWindow(
            date(2026, 1, 16),
            date(2026, 1, 16),
            Path("x"),
        )

    with pytest.raises(ConsolidationError):
        ResearchWindow(
            date(2026, 1, 16),
            date(2026, 1, 15),
            Path("x"),
        )
