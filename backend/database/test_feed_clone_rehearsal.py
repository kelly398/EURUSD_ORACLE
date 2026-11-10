"""Offline safety guards for the DP-M2D.8 clone writer rehearsal."""

import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from backend.database import feed_clone_rehearsal as rehearsal
from backend.data_engine.clock_policy import (
    ClockPolicy, hfm_utc_to_wall, implementation_sha256, utc_msc,
)
from backend.data_engine.forward_store import ForwardFeedStore


def test_synthetic_rows_are_legacy_only_and_do_not_use_sequences():
    tick = rehearsal._tick_row()
    candle = rehearsal._candle_row()
    assert tick["id"] < 0 and candle["id"] < 0
    assert "feed_id" not in tick and "feed_id" not in candle
    assert tick["symbol"] == candle["symbol"] == rehearsal.TEST_SYMBOL


def test_feed_labelled_probes_fail_before_opening_sessions(monkeypatch):
    import backend.candle_engine.broker_candle_storage as candle_storage
    import backend.data_engine.batch_tick_storage as tick_storage

    class NoSession:
        def __call__(self, *args, **kwargs):
            pytest.fail("rejected feed row opened a database session")

    monkeypatch.setattr(tick_storage, "SessionLocal", NoSession())
    monkeypatch.setattr(candle_storage, "SessionLocal", NoSession())
    rehearsal._probe_feed_write_blocks(tick_storage, candle_storage)


def test_exact_clone_confirmation_is_required_before_engine_creation(monkeypatch):
    def must_not_connect(*args, **kwargs):
        pytest.fail("invalid confirmation reached database engine creation")

    monkeypatch.setattr(rehearsal, "create_engine", must_not_connect)
    with pytest.raises(rehearsal.FeedCloneMigrationError):
        rehearsal.rehearse_disposable_clone(
            "postgresql+psycopg2:///eurusd_oracle_archive",
            confirm_database_name="eurusd_oracle_archive",
        )


def test_offline_cli_never_connects(monkeypatch, capsys):
    def must_not_connect(*args, **kwargs):
        pytest.fail("offline mode attempted a connection")

    monkeypatch.setattr(rehearsal, "create_engine", must_not_connect)
    assert rehearsal.main(["--offline-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "OFFLINE_NO_CONNECTION"
    assert report["target_database"] == rehearsal.CLONE_DATABASE_NAME
    assert report["persistent_clone_rows_written"] is False
    assert report["final_action"] == "HOLD"


def test_runner_uses_savepoint_sessions_and_outer_rollback():
    source = Path(rehearsal.__file__).read_text(encoding="utf-8")
    assert 'join_transaction_mode="create_savepoint"' in source
    assert "transaction.rollback()" in source
    assert '"persistent_clone_rows_written": 0' in source
    assert '"sequence_values_advanced": False' in source


def test_durable_resume_cursor_survives_store_reopen():
    now = datetime(2026, 10, 27, 14, 0, tzinfo=timezone.utc)
    policy = ClockPolicy.model_validate({
        "company_sha256": sha256(b"SYNTHETIC_TEST").hexdigest(),
        "server_sha256": sha256(b"SYNTHETIC_SERVER").hexdigest(),
        "evidence_sha256": "a" * 64,
        "implementation_sha256": implementation_sha256(),
        "valid_from": now - timedelta(hours=1),
        "valid_until": now + timedelta(days=1),
    })
    tick_at = now - timedelta(seconds=2)
    raw_tick = {
        "time": utc_msc(hfm_utc_to_wall(tick_at)) // 1000,
        "time_msc": utc_msc(hfm_utc_to_wall(tick_at)),
        "bid": 1.1,
        "ask": 1.1001,
        "last": 0.0,
        "volume": 0,
        "flags": 6,
        "volume_real": 0.0,
    }
    bar_at = now - timedelta(minutes=5)
    raw_bar = {
        "time": utc_msc(hfm_utc_to_wall(bar_at)) // 1000,
        "open": 1.1,
        "high": 1.101,
        "low": 1.099,
        "close": 1.1005,
        "tick_volume": 10,
        "spread": 2,
        "real_volume": 0,
    }
    engine = create_engine("sqlite://")
    try:
        first = ForwardFeedStore(engine, clock=lambda: now)
        first.initialize()
        first.append(
            policy,
            tick_rows=[policy.tick(raw_tick)],
            bar_rows=[policy.rate(raw_bar)],
            received_at=now,
            tick_start=now - timedelta(minutes=1),
            tick_end=now,
            bar_start=now - timedelta(minutes=10),
            bar_end=now,
        )
        reopened = ForwardFeedStore(engine, clock=lambda: now)
        assert reopened.resume_cursor(policy) == now
    finally:
        engine.dispose()

