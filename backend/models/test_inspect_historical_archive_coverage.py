
"""Offline Milestone 11 tests: no real PostgreSQL or files."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from backend.models.inspect_historical_archive_coverage import (
    CoverageInspectionError,
    inspect_coverage,
    main,
)

UTC = timezone.utc


def record(day=15, complete=288, incomplete=0, **changes):
    row = {
        "broker_day": date(2026, 1, day),
        "stored_rows": complete + incomplete,
        "complete_rows": complete,
        "incomplete_rows": incomplete,
        "distinct_complete_labels": complete,
        "nonpositive_tick_volume": 0,
        "nonpositive_spread": 0,
        "first_complete_label": (
            datetime(2026, 1, day, 0, tzinfo=UTC)
            if complete else None
        ),
        "last_complete_label": (
            datetime(2026, 1, day, 23, 55, tzinfo=UTC)
            if complete else None
        ),
    }

    row.update(changes)
    return row


class FakeConn:

    def __init__(self, rows):
        self.rows = rows
        self.sql = []
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def begin(self):
        return self

    def execute(self, statement, params=None):
        query = str(statement).strip()
        self.sql.append(query)

        if params is not None:
            self.params = params
            return SimpleNamespace(
                mappings=lambda: iter(self.rows)
            )

        return None


class FakeEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, rows):
        self.conn = FakeConn(rows)

    def connect(self):
        return self.conn


def inspect(
    rows,
    start=date(2026, 1, 15),
    end=date(2026, 1, 18),
):
    engine = FakeEngine(rows)
    return inspect_coverage(engine, start, end), engine


def test_reports_missing_days_without_claiming_outage():
    report, engine = inspect([record()])

    assert report.total_complete == 288
    assert report.observed_days == 1

    assert [d.label for d in report.days] == [
        "288_COMPLETE_LABELS",
        "NO_STORED_ROWS",
        "NO_STORED_ROWS",
    ]

    assert (
        "REPEATABLE READ READ ONLY"
        in engine.conn.sql[0]
    )

    assert "FROM broker_candles" in engine.conn.sql[2]

    assert (
        engine.conn.params["end_label"].date()
        == date(2026, 1, 18)
    )

    assert all(
        "INSERT " not in statement
        for statement in engine.conn.sql
    )


def test_partial_and_incomplete_rows_are_not_hidden():
    report, _ = inspect([
        record(complete=80, incomplete=4)
    ])

    assert report.days[0].complete == 80
    assert report.days[0].incomplete == 4

    assert (
        report.days[0].label
        == "PARTIAL_OR_SESSION_LIMITED"
    )


def test_all_incomplete_day():
    report, _ = inspect([
        record(complete=0, incomplete=2)
    ])

    assert (
        report.days[0].label
        == "NO_COMPLETED_CANDLES"
    )


def test_empty_archive_is_valid_inventory():
    report, _ = inspect([])

    assert report.total_complete == 0
    assert len(report.days) == 3


@pytest.mark.parametrize("changes", [
    {"distinct_complete_labels": 1},
    {"stored_rows": 1},
    {"nonpositive_spread": 999},
    {"complete_rows": 289},
    {"first_complete_label": None},
    {"broker_day": date(2026, 1, 25)},
])
def test_inconsistent_database_output_fails_closed(changes):
    with pytest.raises(CoverageInspectionError):
        inspect([record(**changes)])


def test_duplicate_day_fails_closed():
    with pytest.raises(CoverageInspectionError):
        inspect([record(), record()])


@pytest.mark.parametrize("start,end", [
    (date(2026, 1, 15), date(2026, 1, 15)),
    (date(2026, 1, 16), date(2026, 1, 15)),
    (date(2026, 1, 1), date(2026, 5, 1)),
])
def test_invalid_date_ranges_rejected(start, end):
    with pytest.raises(CoverageInspectionError):
        inspect_coverage(
            FakeEngine([]), start, end
        )


def test_postgres_only():
    engine = FakeEngine([])
    engine.dialect = SimpleNamespace(name="sqlite")

    with pytest.raises(CoverageInspectionError):
        inspect_coverage(
            engine,
            date(2026, 1, 15),
            date(2026, 1, 16),
        )


def test_cli_requires_acknowledgement():
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--start-date", "2026-01-15",
                "--end-date", "2026-01-18",
            ],
            engine=FakeEngine([]),
        )

    assert error.value.code == 2


def test_cli_reads_and_never_writes(capsys):
    result = main(
        [
            "--start-date", "2026-01-15",
            "--end-date", "2026-01-18",
            "--research-only",
        ],
        engine=FakeEngine([record()]),
    )

    assert result == 0

    output = capsys.readouterr().out

    assert "288 completed candles" in output
    assert "No files written" in output


def test_bad_cli_dates_rejected():
    with pytest.raises(SystemExit) as error:
        main(
            [
                "--start-date", "2026-2-01",
                "--end-date", "2026-02-03",
                "--research-only",
            ],
            engine=FakeEngine([]),
        )

    assert error.value.code == 2
