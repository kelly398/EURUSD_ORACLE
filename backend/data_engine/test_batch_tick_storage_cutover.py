"""Offline DP-M2C checks for the existing batch tick writer."""

import importlib
import sys
import types
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.dialects import postgresql


@pytest.fixture
def storage_module(monkeypatch):
    connection_name = "backend.database.connection"
    module_name = "backend.data_engine.batch_tick_storage"
    old_storage = sys.modules.get(module_name)
    created_storage = old_storage is None

    connection_stub = types.ModuleType(connection_name)
    connection_stub.SessionLocal = Mock()
    monkeypatch.setitem(sys.modules, connection_name, connection_stub)

    try:
        module = importlib.import_module(module_name)
        yield module
    finally:
        if created_storage:
            sys.modules.pop(module_name, None)


def session_stub(monkeypatch, module, *, rowcount=1):
    session = Mock()
    session.execute.return_value = SimpleNamespace(rowcount=rowcount)
    factory = Mock(return_value=session)
    monkeypatch.setattr(module, "SessionLocal", factory)
    return factory, session


def test_empty_batch_does_not_open_session(monkeypatch, storage_module):
    factory = Mock(side_effect=AssertionError("opened"))
    monkeypatch.setattr(storage_module, "SessionLocal", factory)

    assert storage_module.BatchTickStorage().save_batch([]) == 0
    factory.assert_not_called()


@pytest.mark.parametrize("feed_id", [None, "mt5_eurusd_v1_" + "a" * 64])
def test_feed_labelled_rows_are_rejected_before_session(
    monkeypatch, storage_module, feed_id
):
    factory = Mock(side_effect=AssertionError("opened"))
    monkeypatch.setattr(storage_module, "SessionLocal", factory)

    with pytest.raises(RuntimeError, match="blocked pending approved"):
        storage_module.BatchTickStorage().save_batch([
            {"fingerprint": "a" * 64, "feed_id": feed_id}
        ])

    factory.assert_not_called()


def test_mixed_legacy_and_feed_rows_are_rejected_atomically(
    monkeypatch, storage_module
):
    factory = Mock(side_effect=AssertionError("opened"))
    monkeypatch.setattr(storage_module, "SessionLocal", factory)

    with pytest.raises(RuntimeError, match="Feed-labelled tick writes"):
        storage_module.BatchTickStorage().save_batch([
            {"fingerprint": "a" * 64},
            {"fingerprint": "b" * 64, "feed_id": None},
        ])

    factory.assert_not_called()


def test_legacy_rows_keep_fingerprint_conflict_handling(
    monkeypatch, storage_module
):
    _, session = session_stub(monkeypatch, storage_module)

    assert storage_module.BatchTickStorage().save_batch([
        {"fingerprint": "a" * 64}
    ]) == 1

    sql = str(
        session.execute.call_args.args[0].compile(
            dialect=postgresql.dialect()
        )
    )
    assert "ON CONFLICT (fingerprint) DO NOTHING" in sql
    session.commit.assert_called_once_with()
    session.rollback.assert_not_called()
    session.close.assert_called_once_with()
