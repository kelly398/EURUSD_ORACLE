
"""Milestone 10: planner and safety tests. No database required."""

from datetime import date, datetime, timedelta, timezone

import pytest

from backend.models.run_multiwindow_utc_research import (
    WindowPlanError,
    plan_windows,
    run,
)


BASE = [
    "--start-date", "2026-01-12",
    "--end-date", "2026-01-20",
    "--window-days", "3",
    "--research-only",
]


def test_plan_is_exclusive_nonoverlapping_and_complete():
    windows = plan_windows(
        date(2026, 1, 12),
        date(2026, 1, 20),
        window_days=3,
    )

    assert len(windows) == 3

    assert [
        w.end_label.date().isoformat()
        for w in windows
    ] == [
        "2026-01-15",
        "2026-01-18",
        "2026-01-20",
    ]

    assert all(
        a.end_label == b.start_label
        for a, b in zip(windows, windows[1:])
    )

    assert windows[0].start_label.utcoffset() == (
        timedelta(0)
    )

    assert windows[-1].end_label.date() == (
        date(2026, 1, 20)
    )


def test_plan_does_not_access_database(capsys):
    assert run(BASE) == 0

    out = capsys.readouterr().out

    assert "PLAN ONLY" in out
    assert "Windows planned: 3" in out


def test_preview_calls_existing_single_window_runner():
    seen = []

    def fake(argv):
        seen.append(argv)
        return 0

    assert run(
        BASE + ["--execute"],
        single_window_runner=fake,
    ) == 0

    assert len(seen) == 3
    assert all("--research-only" in a for a in seen)
    assert all("--export" not in a for a in seen)

    assert seen[0][
        seen[0].index("--start-label") + 1
    ].startswith("2026-01-12T")

    assert seen[-1][
        seen[-1].index("--end-label") + 1
    ].startswith("2026-01-20T")


def test_export_is_explicit_and_forwarded():
    seen = []

    def fake(argv):
        seen.append(argv)
        return 0

    assert run(
        BASE + ["--execute", "--export"],
        single_window_runner=fake,
    ) == 0

    assert len(seen) == 3
    assert all("--export" in a for a in seen)


def test_export_without_execute_rejected():
    assert run(BASE + ["--export"]) == 2


def test_failed_window_stops_later_windows(capsys):
    seen = []

    def fake(argv):
        seen.append(argv)
        return 0 if len(seen) == 1 else 1

    assert run(
        BASE + ["--execute", "--export"],
        single_window_runner=fake,
    ) == 1

    assert len(seen) == 2
    assert "NOT fully complete" in (
        capsys.readouterr().out
    )


def test_interruption_is_not_success(capsys):
    def interrupted(_):
        raise KeyboardInterrupt

    assert run(
        BASE + ["--execute"],
        single_window_runner=interrupted,
    ) == 130

    assert "interrupted" in capsys.readouterr().out


def test_exception_does_not_leak_connection_secret(capsys):
    def crash(_):
        raise RuntimeError(
            "postgresql://user:secret@host"
        )

    assert run(
        BASE + ["--execute"],
        single_window_runner=crash,
    ) == 1

    assert "secret" not in (
        capsys.readouterr().out
    )


@pytest.mark.parametrize("bad", [
    (date(2026, 1, 12), date(2026, 1, 12)),
    (date(2026, 1, 20), date(2026, 1, 12)),
    (date(2026, 1, 12), date(2027, 1, 14)),
])
def test_bad_date_span_rejected(bad):
    with pytest.raises(WindowPlanError):
        plan_windows(*bad)


@pytest.mark.parametrize(
    "value",
    [0, 91, True, 1.2, "3"],
)
def test_invalid_window_days_rejected(value):
    with pytest.raises(WindowPlanError):
        plan_windows(
            date(2026, 1, 12),
            date(2026, 1, 20),
            window_days=value,
        )


@pytest.mark.parametrize(
    "value",
    [0, 5001, True, "10"],
)
def test_invalid_page_size_rejected(value):
    with pytest.raises(WindowPlanError):
        plan_windows(
            date(2026, 1, 12),
            date(2026, 1, 20),
            page_size=value,
        )


@pytest.mark.parametrize(
    "value",
    [0, 500001, True, "10"],
)
def test_invalid_max_rows_rejected(value):
    with pytest.raises(WindowPlanError):
        plan_windows(
            date(2026, 1, 12),
            date(2026, 1, 20),
            max_rows=value,
        )


def test_too_many_windows_rejected():
    with pytest.raises(
        WindowPlanError,
        match="Too many",
    ):
        plan_windows(
            date(2026, 1, 1),
            date(2026, 5, 1),
            window_days=1,
        )


def test_datetime_not_accepted_as_date():
    with pytest.raises(WindowPlanError):
        plan_windows(
            datetime(
                2026, 1, 1,
                tzinfo=timezone.utc,
            ),
            date(2026, 1, 2),
        )


@pytest.mark.parametrize("value", [
    "2026-1-01",
    "2026-02-30",
    "01-01-2026",
])
def test_invalid_cli_date_rejected(value):
    with pytest.raises(SystemExit) as err:
        run([
            "--start-date", value,
            "--end-date", "2026-01-20",
            "--research-only",
        ])

    assert err.value.code == 2


def test_missing_acknowledgement_rejected():
    with pytest.raises(SystemExit) as err:
        run([
            "--start-date", "2026-01-12",
            "--end-date", "2026-01-20",
        ])

    assert err.value.code == 2


def test_adapter_validation_still_enforced():
    assert run(
        BASE + ["--page-size", "5001"]
    ) == 2

    assert run(
        BASE + ["--max-rows", "500001"]
    ) == 2
