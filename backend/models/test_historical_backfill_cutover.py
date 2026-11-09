"""Offline DP-M2C historical candle writer compatibility checks."""

from contextlib import contextmanager
import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql


@pytest.fixture
def backfill_module(monkeypatch):
    """Import the writer without requiring MT5 or a configured database."""
    connection_name = "backend.database.connection"
    module_name = "backend.models.historical_backfill"
    old_backfill = sys.modules.get(module_name)
    created_backfill = old_backfill is None

    connection_stub = types.ModuleType(connection_name)
    connection_stub.engine = Mock()
    monkeypatch.setitem(sys.modules, connection_name, connection_stub)
    monkeypatch.setitem(
        sys.modules,
        "MetaTrader5",
        types.ModuleType("MetaTrader5"),
    )

    try:
        module = importlib.import_module(module_name)
        yield module
    finally:
        if created_backfill:
            sys.modules.pop(module_name, None)


def candle(**overrides):
    row = {
        "symbol": "EURUSD",
        "timeframe": "M5",
        "timestamp": "2026-11-02T14:00:00+00:00",
        "open": 1.1000,
        "high": 1.1010,
        "low": 1.0990,
        "close": 1.1005,
        "tick_volume": 15,
        "spread_points": 12,
        "real_volume": 0.0,
        "is_complete": True,
        "source": "MT5",
    }
    row.update(overrides)
    return row


def stub_engine(monkeypatch, module, *, rowcounts=(1,), failure=None):
    connection = Mock()
    if failure is not None:
        connection.execute.side_effect = failure
    else:
        connection.execute.side_effect = [
            SimpleNamespace(rowcount=count) for count in rowcounts
        ]

    @contextmanager
    def transaction():
        yield connection

    engine = Mock()
    engine.begin.side_effect = transaction
    monkeypatch.setattr(module, "engine", engine)
    return engine, connection


def test_empty_batch_does_not_open_transaction(monkeypatch, backfill_module):
    engine, _ = stub_engine(monkeypatch, backfill_module)

    assert backfill_module.insert_bars([]) == 0
    engine.begin.assert_not_called()


@pytest.mark.parametrize("feed_id", [None, "mt5_eurusd_v1_" + "a" * 64])
def test_feed_labelled_batch_is_rejected_before_transaction(
    monkeypatch, backfill_module, feed_id
):
    engine, connection = stub_engine(monkeypatch, backfill_module)

    with pytest.raises(RuntimeError, match="blocked pending approved"):
        backfill_module.insert_bars([candle(feed_id=feed_id)])

    engine.begin.assert_not_called()
    connection.execute.assert_not_called()


def test_mixed_legacy_and_feed_batch_is_rejected_atomically(
    monkeypatch, backfill_module
):
    engine, connection = stub_engine(monkeypatch, backfill_module)

    with pytest.raises(RuntimeError, match="Feed-labelled historical candle"):
        backfill_module.insert_bars([
            candle(),
            candle(timestamp="2026-11-02T14:05:00+00:00", feed_id=None),
        ])

    engine.begin.assert_not_called()
    connection.execute.assert_not_called()


def test_legacy_insert_uses_untargeted_conflict_handling(
    monkeypatch, backfill_module
):
    _, connection = stub_engine(monkeypatch, backfill_module)

    assert backfill_module.insert_bars([candle()]) == 1

    sql = str(
        connection.execute.call_args.args[0].compile(
            dialect=postgresql.dialect()
        )
    )
    assert "ON CONFLICT DO NOTHING" in sql
    assert "ON CONFLICT ON CONSTRAINT" not in sql
    assert "uq_broker_candle" not in sql
    assert "INSERT INTO broker_candles" in sql


def test_batch_size_and_inserted_row_count_are_preserved(
    monkeypatch, backfill_module
):
    monkeypatch.setattr(backfill_module, "BAR_BATCH_SIZE", 2)
    engine, connection = stub_engine(
        monkeypatch,
        backfill_module,
        rowcounts=(2, 1),
    )

    rows = [candle(timestamp=f"2026-11-02T14:{minute:02d}:00+00:00")
            for minute in (0, 5, 10)]
    assert backfill_module.insert_bars(rows) == 3
    assert engine.begin.call_count == 2
    assert connection.execute.call_count == 2


def test_duplicate_only_batch_reports_zero(monkeypatch, backfill_module):
    engine, _ = stub_engine(
        monkeypatch,
        backfill_module,
        rowcounts=(0,),
    )

    assert backfill_module.insert_bars([candle()]) == 0
    assert engine.begin.call_count == 1


def test_none_rowcount_does_not_increase_inserted_total(
    monkeypatch, backfill_module
):
    stub_engine(
        monkeypatch,
        backfill_module,
        rowcounts=(None,),
    )

    assert backfill_module.insert_bars([candle()]) == 0


def test_database_error_propagates(monkeypatch, backfill_module):
    engine, _ = stub_engine(
        monkeypatch,
        backfill_module,
        failure=RuntimeError("synthetic database error"),
    )

    with pytest.raises(RuntimeError, match="synthetic database error"):
        backfill_module.insert_bars([candle()])

    assert engine.begin.call_count == 1


def test_insert_ticks_keeps_legacy_fingerprint_conflict_target(
    monkeypatch, backfill_module
):
    _, connection = stub_engine(monkeypatch, backfill_module)

    assert backfill_module.insert_ticks([{"fingerprint": "a" * 64}]) == 1

    sql = str(
        connection.execute.call_args.args[0].compile(
            dialect=postgresql.dialect()
        )
    )
    assert "ON CONFLICT (fingerprint) DO NOTHING" in sql
