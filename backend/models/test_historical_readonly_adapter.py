
"""Offline safety tests for the historical database adapter."""

from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from backend.models.historical_readonly_adapter import (
    HistoricalArchiveReadError,
    HistoricalArchiveWindow,
    iter_reconstructed_archive_batches,
)

UTC = timezone.utc


def make_row(row_id, minute=0, **changes):
    row = {
        "id": row_id,
        "broker_label_time": datetime(
            2026, 1, 15, 14, minute, tzinfo=UTC
        ),
        "open": 1.1000,
        "high": 1.1200,
        "low": 1.0900,
        "close": 1.1100,
        "tick_volume": 100,
        "spread_points": 8,
        "real_volume": 0.0,
        "symbol": "EURUSD",
        "timeframe": "M5",
        "source": "MT5",
        "is_complete": True,
    }
    row.update(changes)
    return row


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, engine):
        self.engine = engine
        self.commands = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def begin(self):
        return self

    def execute(self, statement, params=None):
        sql = str(statement).strip()
        self.commands.append(sql)

        if sql.startswith("SET "):
            return FakeResult([])

        assert sql.startswith("SELECT")
        assert "FROM broker_candles" in sql

        params = params or {}
        rows = [
            row for row in self.engine.rows
            if row["symbol"] == "EURUSD"
            and row["timeframe"] == "M5"
            and row["source"] == "MT5"
            and row["is_complete"] is True
            and params["start_label"]
            <= row["broker_label_time"]
            < params["end_label"]
        ]

        rows.sort(
            key=lambda row: (
                row["broker_label_time"],
                row["id"],
            )
        )

        if "last_label" in params:
            marker = (
                params["last_label"],
                params["last_id"],
            )
            rows = [
                row for row in rows
                if (
                    row["broker_label_time"],
                    row["id"],
                ) > marker
            ]

        return FakeResult(rows[:params["page_size"]])


class FakeEngine:
    dialect = SimpleNamespace(name="postgresql")

    def __init__(self, rows):
        self.rows = rows
        self.connection = None

    def connect(self):
        self.connection = FakeConnection(self)
        return self.connection


def make_window(**changes):
    values = {
        "start_label": datetime(
            2026, 1, 15, tzinfo=UTC
        ),
        "end_label": datetime(
            2026, 1, 16, tzinfo=UTC
        ),
        "page_size": 2,
    }
    values.update(changes)
    return HistoricalArchiveWindow(**values)


def test_readonly_reconstruction():
    engine = FakeEngine([
        make_row(1, 0),
        make_row(2, 5),
        make_row(3, 10),
    ])

    batches = list(
        iter_reconstructed_archive_batches(
            engine,
            make_window(),
        )
    )

    assert [len(b.records) for b in batches] == [2, 1]
    assert all(b.research_only for b in batches)
    assert all(not b.historical_admission for b in batches)
    assert engine.connection.closed

    commands = engine.connection.commands

    assert commands[0] == (
        "SET TRANSACTION ISOLATION LEVEL "
        "REPEATABLE READ READ ONLY"
    )

    assert all(
        command.startswith(("SELECT", "SET "))
        for command in commands
    )


def test_duplicate_labels_rejected():
    engine = FakeEngine([
        make_row(1, 0),
        make_row(2, 0),
    ])

    with pytest.raises(HistoricalArchiveReadError):
        list(
            iter_reconstructed_archive_batches(
                engine,
                make_window(),
            )
        )


def test_invalid_candle_rejected():
    engine = FakeEngine([
        make_row(1, 0, high=0.5),
    ])

    with pytest.raises(HistoricalArchiveReadError):
        list(
            iter_reconstructed_archive_batches(
                engine,
                make_window(),
            )
        )


def test_row_limit_enforced():
    engine = FakeEngine([
        make_row(1, 0),
        make_row(2, 5),
        make_row(3, 10),
    ])

    with pytest.raises(HistoricalArchiveReadError):
        list(
            iter_reconstructed_archive_batches(
                engine,
                make_window(max_rows=2),
            )
        )


def test_empty_archive_rejected():
    engine = FakeEngine([])

    with pytest.raises(HistoricalArchiveReadError):
        list(
            iter_reconstructed_archive_batches(
                engine,
                make_window(),
            )
        )


def test_invalid_window_rejected():
    with pytest.raises(HistoricalArchiveReadError):
        make_window(page_size=0)

    with pytest.raises(HistoricalArchiveReadError):
        make_window(
            start_label=datetime(2026, 1, 15)
        )

    with pytest.raises(HistoricalArchiveReadError):
        make_window(
            end_label=datetime(
                2026, 1, 15, tzinfo=UTC
            )
        )


def test_wrong_database_rejected():
    engine = FakeEngine([make_row(1)])
    engine.dialect = SimpleNamespace(name="sqlite")

    with pytest.raises(HistoricalArchiveReadError):
        list(
            iter_reconstructed_archive_batches(
                engine,
                make_window(),
            )
        )
