
"""Milestone 05: offline pipeline safety tests.

No real database, broker, or production artifacts are used.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.models import (
    run_historical_utc_research as cli,
)


START = "2026-01-15T00:00:00Z"
END = "2026-01-16T00:00:00Z"

ARGS = [
    "--start-label", START,
    "--end-label", END,
    "--research-only",
]


def fake_manifest(**changes):
    manifest = {
        "dataset_version":
            "EURUSD_M5_HISTORICAL_UTC_RESEARCH_V2",
        "symbol": "EURUSD",
        "timeframe": "M5",
        "research_only": True,
        "historical_admission": False,
        "execution_authorized": False,
        "training_authorized": False,
        "source_receipt_certified": False,
        "first_available_at": None,
        "record_count": 3,
        "unobserved_intervals": 0,
        "rows_jsonl_sha256": "a" * 64,
        "lineage_sha256": "b" * 64,
    }

    manifest.update(changes)

    return manifest


def bind(monkeypatch, *, manifest=None, failure=None):
    """Replace external dependencies with offline test doubles."""

    calls = []

    def reader(engine, window):
        calls.append(("read", window, engine))

        if failure == "read":
            raise RuntimeError(
                "database connection: password=SECRET"
            )

        return iter(["batch1", "batch2"])

    def builder(batches, *, max_rows):
        calls.append((
            "build",
            list(batches),
            max_rows,
        ))

        if failure == "build":
            raise RuntimeError(
                "reconstruction error"
            )

        return SimpleNamespace(
            manifest=lambda: fake_manifest(
                **(manifest or {})
            )
        )

    def exporter(dataset, root):
        calls.append(("export", Path(root)))
        return Path(root) / "exported"

    def verifier(path):
        calls.append(("verify", Path(path)))
        return fake_manifest()

    monkeypatch.setattr(
        cli,
        "iter_reconstructed_archive_batches",
        reader,
    )

    monkeypatch.setattr(
        cli,
        "build_historical_utc_research_dataset",
        builder,
    )

    monkeypatch.setattr(
        cli,
        "export_research_dataset",
        exporter,
    )

    monkeypatch.setattr(
        cli,
        "verify_research_export",
        verifier,
    )

    return calls


def test_preview_read_only_and_no_files(
    monkeypatch, tmp_path, capsys
):
    calls = bind(monkeypatch)

    result = cli.run(
        ARGS,
        engine=object(),
        project_root=tmp_path,
    )

    assert result == 0

    assert [c[0] for c in calls] == [
        "read", "build"
    ]

    assert calls[0][1].page_size == 500
    assert calls[0][1].max_rows == 100_000

    assert "No research dataset was written" in (
        capsys.readouterr().out
    )

    assert not list(tmp_path.iterdir())


def test_explicit_export_and_verification(
    monkeypatch, tmp_path
):
    calls = bind(monkeypatch)

    result = cli.run(
        ARGS + ["--export"],
        engine=object(),
        project_root=tmp_path,
    )

    assert result == 0

    assert [c[0] for c in calls] == [
        "read", "build", "export", "verify"
    ]

    assert calls[2][1] == tmp_path


def test_missing_explicit_research_flag_rejected():
    with pytest.raises(SystemExit) as info:
        cli.run(
            [
                "--start-label", START,
                "--end-label", END,
            ],
            engine=object(),
        )

    assert info.value.code == 2


@pytest.mark.parametrize("bad", [
    "2026-01-15T00:00:00",
    "2026-01-15",
    "2026-01-15T00:00:00+03:00",
    "2026-01-15T00:01:00Z",
    "2026-02-30T00:00:00Z",
    "2026-01-15T00:00:01Z",
])
def test_bad_start_labels_fail_before_database(bad):
    with pytest.raises(SystemExit) as info:
        cli.run(
            [
                "--start-label", bad,
                "--end-label", END,
                "--research-only",
            ],
            engine=object(),
        )

    assert info.value.code == 2


def test_reverse_window_rejected_without_database(
    monkeypatch,
):
    calls = bind(monkeypatch)

    result = cli.run(
        [
            "--start-label", END,
            "--end-label", START,
            "--research-only",
        ],
        engine=object(),
    )

    assert result == 2
    assert calls == []


def test_page_cap_rejected_without_database(
    monkeypatch,
):
    calls = bind(monkeypatch)

    result = cli.run(
        ARGS + ["--page-size", "5001"],
        engine=object(),
    )

    assert result == 2
    assert calls == []


def test_max_rows_rejected_without_database(
    monkeypatch,
):
    calls = bind(monkeypatch)

    result = cli.run(
        ARGS + ["--max-rows", "500001"],
        engine=object(),
    )

    assert result == 2
    assert calls == []


def test_missing_data_does_not_export(
    monkeypatch, tmp_path, capsys
):
    calls = bind(
        monkeypatch,
        failure="read",
    )

    result = cli.run(
        ARGS + ["--export"],
        engine=object(),
        project_root=tmp_path,
    )

    assert result == 1
    assert [c[0] for c in calls] == ["read"]

    assert "SECRET" not in (
        capsys.readouterr().out
    )


def test_failed_reconstruction_never_exports(
    monkeypatch, tmp_path
):
    calls = bind(
        monkeypatch,
        failure="build",
    )

    result = cli.run(
        ARGS + ["--export"],
        engine=object(),
        project_root=tmp_path,
    )

    assert result == 1

    assert [c[0] for c in calls] == [
        "read", "build"
    ]


@pytest.mark.parametrize("field, value", [
    ("research_only", False),
    ("historical_admission", True),
    ("execution_authorized", True),
    ("training_authorized", True),
    ("source_receipt_certified", True),
    (
        "first_available_at",
        "2026-01-15T00:00:00Z",
    ),
])
def test_unsafe_manifests_cannot_export(
    monkeypatch, tmp_path, field, value
):
    calls = bind(
        monkeypatch,
        manifest={field: value},
    )

    result = cli.run(
        ARGS + ["--export"],
        engine=object(),
        project_root=tmp_path,
    )

    assert result == 1

    assert [c[0] for c in calls] == [
        "read", "build"
    ]


def test_postexport_verification_failure_reports_path(
    monkeypatch, tmp_path, capsys
):
    calls = bind(monkeypatch)

    monkeypatch.setattr(
        cli,
        "verify_research_export",
        lambda _: {
            **fake_manifest(),
            "record_count": 4,
        },
    )

    result = cli.run(
        ARGS + ["--export"],
        engine=object(),
        project_root=tmp_path,
    )

    assert result == 1

    assert "Published export needs manual review" in (
        capsys.readouterr().out
    )

    assert [c[0] for c in calls] == [
        "read", "build", "export"
    ]


def test_postexport_verifier_exception_reports_path(
    monkeypatch, tmp_path, capsys
):
    calls = bind(monkeypatch)

    def broken(_):
        raise ValueError("verification failed")

    monkeypatch.setattr(
        cli,
        "verify_research_export",
        broken,
    )

    result = cli.run(
        ARGS + ["--export"],
        engine=object(),
        project_root=tmp_path,
    )

    assert result == 1

    assert "Published export needs manual review" in (
        capsys.readouterr().out
    )

    assert [c[0] for c in calls] == [
        "read", "build", "export"
    ]


def test_custom_page_limits_forwarded(monkeypatch):
    calls = bind(monkeypatch)

    result = cli.run(
        ARGS + [
            "--page-size", "10",
            "--max-rows", "100",
        ],
        engine=object(),
    )

    assert result == 0
    assert calls[0][1].page_size == 10
    assert calls[0][1].max_rows == 100
    assert calls[1][2] == 100
