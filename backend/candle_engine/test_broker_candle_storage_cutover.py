
"""Offline DP-M2C.1 checks for the existing broker candle writer."""

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql

from backend.candle_engine import broker_candle_storage as storage


TIME = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)


def candle(**override):
    result = dict(
        symbol="EURUSD",
        timeframe="M5",
        timestamp=TIME,
        open=1.1000,
        high=1.1010,
        low=1.0990,
        close=1.1005,
        tick_volume=15,
        spread_points=12,
        real_volume=0.0,
        is_complete=True,
        source="MT5",
    )
    result.update(override)
    return result


def session_stub(monkeypatch, *, rowcount=1, failure=None):
    session = Mock()
    if failure is None:
        session.execute.return_value = SimpleNamespace(rowcount=rowcount)
    else:
        session.execute.side_effect = failure
    factory = Mock(return_value=session)
    monkeypatch.setattr(storage, "SessionLocal", factory)
    return factory, session


def test_legacy_insert_uses_no_named_conflict_target(monkeypatch):
    _, session = session_stub(monkeypatch)
    assert storage.BrokerCandleStorage().save_batch([candle()]) == 1
    statement = session.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT DO NOTHING" in sql
    assert "ON CONFLICT ON CONSTRAINT" not in sql
    assert "uq_broker_candle" not in sql
    assert "INSERT INTO broker_candles" in sql
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()


def test_new_duplicate_result_returns_zero(monkeypatch):
    _, session = session_stub(monkeypatch, rowcount=0)
    assert storage.BrokerCandleStorage().save_batch([candle()]) == 0
    session.commit.assert_called_once_with()
    session.close.assert_called_once_with()


def test_rowcount_none_preserves_legacy_result(monkeypatch):
    _, session = session_stub(monkeypatch, rowcount=None)
    assert storage.BrokerCandleStorage().save_batch([candle()]) == 0
    session.commit.assert_called_once_with()


def test_two_legacy_candles_are_batched_together(monkeypatch):
    _, session = session_stub(monkeypatch, rowcount=2)
    assert storage.BrokerCandleStorage().save_batch([
        candle(), candle(timestamp=TIME.replace(minute=5))
    ]) == 2
    session.execute.assert_called_once()


def test_empty_rows_do_not_open_session(monkeypatch):
    factory = Mock(side_effect=AssertionError("opened"))
    monkeypatch.setattr(storage, "SessionLocal", factory)
    assert storage.BrokerCandleStorage().save_batch([]) == 0
    factory.assert_not_called()


@pytest.mark.parametrize("value", [
    None, "mt5_eurusd_v1_" + "a" * 64, "", 17,
])
def test_feed_labeled_rows_refused_before_db_connection(
    monkeypatch, value
):
    factory = Mock(side_effect=AssertionError("opened"))
    monkeypatch.setattr(storage, "SessionLocal", factory)
    with pytest.raises(RuntimeError, match="blocked pending approved"):
        storage.BrokerCandleStorage().save_batch([
            candle(feed_id=value)
        ])
    factory.assert_not_called()


def test_mixed_legacy_and_feed_batch_is_atomic_fail_closed(monkeypatch):
    factory = Mock(side_effect=AssertionError("opened"))
    monkeypatch.setattr(storage, "SessionLocal", factory)
    with pytest.raises(RuntimeError, match="Feed-labelled"):
        storage.BrokerCandleStorage().save_batch([
            candle(),
            candle(feed_id="mt5_eurusd_v1_" + "b" * 64),
        ])
    factory.assert_not_called()


def test_session_failure_rolls_back_and_closes(monkeypatch):
    _, session = session_stub(
        monkeypatch,
        failure=RuntimeError("synthetic failure")
    )
    with pytest.raises(RuntimeError, match="synthetic failure"):
        storage.BrokerCandleStorage().save_batch([candle()])
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()
    session.close.assert_called_once_with()


def test_legacy_latest_cursor_retains_source_filter(monkeypatch):
    session = Mock()
    query = session.query.return_value
    query.filter.return_value = query
    query.scalar.return_value = TIME
    factory = Mock(return_value=session)
    monkeypatch.setattr(storage, "SessionLocal", factory)

    assert (
        storage.BrokerCandleStorage()
        .get_latest_timestamp("EURUSD", "M5")
    ) == TIME

    query.filter.assert_called_once()

    compiled = [
        str(
            item.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True}
            )
        )
        for item in query.filter.call_args.args
    ]

    assert any(
        "broker_candles.source = 'MT5'" in item
        for item in compiled
    )
    assert any(
        "broker_candles.symbol = 'EURUSD'" in item
        for item in compiled
    )
    assert any(
        "broker_candles.timeframe = 'M5'" in item
        for item in compiled
    )
    session.close.assert_called_once_with()


def test_no_new_models_or_mt5_calls_are_added():
    source = inspect.getsource(storage)
    assert "import MetaTrader5" not in source
    assert "order_send(" not in source
    assert "CREATE TABLE" not in source
    assert "ALTER TABLE" not in source
    assert "DROP CONSTRAINT" not in source
    assert "feed_id" in source
