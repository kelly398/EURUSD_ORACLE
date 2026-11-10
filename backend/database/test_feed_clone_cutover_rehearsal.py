"""Offline guards for the DP-M2D.9 clone-only cutover rehearsal."""

import json
from pathlib import Path

import pytest

from backend.database import feed_clone_cutover_rehearsal as rehearsal


def test_only_exact_disposable_clone_can_be_targeted(monkeypatch):
    def must_not_connect(*args, **kwargs):
        pytest.fail("invalid confirmation reached database engine creation")

    monkeypatch.setattr(rehearsal, "create_engine", must_not_connect)
    with pytest.raises(rehearsal.FeedCloneMigrationError):
        rehearsal.rehearse_clone_cutover(
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
    assert report["constraint_change_persistent"] is False
    assert report["final_action"] == "HOLD"


def test_constraint_drop_is_bounded_and_clone_table_specific():
    source = Path(rehearsal.__file__).read_text(encoding="utf-8")
    assert "lock_timeout=2000" in source
    assert "statement_timeout=30000" in source
    assert "ALTER TABLE public.broker_candles DROP CONSTRAINT uq_broker_candle" in source
    assert "transaction.rollback()" in source
    assert "join_transaction_mode=\"create_savepoint\"" in source
    assert "feed_labelled_candles_written\": 0" in source


def test_index_gate_requires_both_unique_ready_and_valid():
    source = Path(rehearsal.__file__).read_text(encoding="utf-8")
    assert "uq_broker_candle_feed_v2" in source
    assert "uq_broker_candle_legacy_null_v2" in source
    assert "indisunique" in source
    assert "indisready" in source
    assert "indisvalid" in source
