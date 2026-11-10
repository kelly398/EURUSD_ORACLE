"""Checks for the minimal legacy single-tick database writer."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from sqlalchemy.exc import IntegrityError

from backend.data_engine import storage as storage_module
from backend.data_engine.tick_identity import create_tick_fingerprint


def tick():
    return SimpleNamespace(
        symbol="EURUSD",
        bid=1.10001,
        ask=1.10021,
        timestamp=datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc),
        source_time_msc=1794225600000,
        spread=0.0002,
        spread_pips=2.0,
    )


def test_single_tick_writer_supplies_legacy_fingerprint_and_default_fields(monkeypatch):
    session = Mock()
    monkeypatch.setattr(storage_module, "SessionLocal", Mock(return_value=session))

    assert storage_module.TickStorage().save(tick()) is True

    record = session.add.call_args.args[0]
    assert record.fingerprint == create_tick_fingerprint(
        symbol="EURUSD",
        time_msc=1794225600000,
        bid=1.10001,
        ask=1.10021,
        last=0.0,
        volume=0.0,
        flags=0,
        volume_real=0.0,
    )
    assert (record.last, record.volume, record.flags, record.volume_real) == (
        0.0, 0.0, 0, 0.0
    )
    assert record.feed_id is None
    session.commit.assert_called_once_with()
    session.close.assert_called_once_with()


def test_single_tick_writer_treats_fingerprint_retry_as_duplicate(monkeypatch):
    session = Mock()
    session.commit.side_effect = IntegrityError(
        "insert", {}, RuntimeError("duplicate fingerprint")
    )
    monkeypatch.setattr(storage_module, "SessionLocal", Mock(return_value=session))

    assert storage_module.TickStorage().save(tick()) is False
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()
