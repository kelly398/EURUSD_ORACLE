"""DP-M2D local schema migration guards; no live DB writes in unit tests."""

import json
import inspect

import pytest

from backend.database import feed_schema_migration as migration


def test_only_local_development_database_url_is_accepted():
    url = migration.validate_local_database_url(
        "postgresql+psycopg2:///eurusd_oracle_local"
    )

    assert url.database == "eurusd_oracle_local"
    assert url.host is None


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+psycopg2:///eurusd_oracle_archive",
        "postgresql+psycopg2:///eurusd_oracle_dp_m2b_lab",
        "postgresql+psycopg2://db.example/eurusd_oracle_local",
        "postgresql+psycopg2://Komarchy@localhost:5433/eurusd_oracle_local",
        "postgresql+psycopg2:///eurusd_oracle_local?host=db.example",
        "postgresql+psycopg2://postgres@localhost/eurusd_oracle_local",
        "sqlite:///eurusd_oracle_local.db",
        "",
    ],
)
def test_refuses_other_or_invalid_targets(url):
    with pytest.raises(migration.FeedSchemaMigrationError):
        migration.validate_local_database_url(url)


def test_migration_sql_is_additive_and_preserves_old_uniqueness():
    sql = "\n".join(migration.MIGRATION_SQL).upper()

    assert "CREATE TABLE IF NOT EXISTS PUBLIC.ORACLE_MT5_FEED_REGISTRY" in sql
    assert "ADD COLUMN IF NOT EXISTS FEED_ID VARCHAR(78) NULL" in sql
    assert "FOREIGN KEY (FEED_ID)" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS UQ_BROKER_CANDLE_FEED_V2" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS UQ_BROKER_CANDLE_LEGACY_NULL_V2" in sql
    assert "DROP CONSTRAINT" not in sql
    assert "DROP TABLE" not in sql
    assert "TRUNCATE" not in sql
    assert "DELETE FROM" not in sql
    assert "UPDATE TICKS" not in sql
    assert "UPDATE BROKER_CANDLES" not in sql


def test_preflight_refuses_populated_tick_or_candle_tables():
    source = inspect.getsource(migration._preflight)

    assert "SELECT EXISTS (SELECT 1 FROM public." in source
    assert "requires empty" in source


def test_postflight_requires_legacy_constraints_and_feed_indexes():
    assert migration.EXPECTED_UNIQUES["ticks"]["uq_tick_fingerprint"] == (
        "fingerprint",
    )
    assert migration.EXPECTED_UNIQUES["broker_candles"]["uq_broker_candle"] == (
        "symbol", "timeframe", "timestamp", "source"
    )
    assert migration.EXPECTED_INDEXES["broker_candles"][
        "uq_broker_candle_feed_v2"
    ] is True


def test_offline_cli_report_never_applies_database_changes(monkeypatch, capsys):
    def fail_if_connected(*args, **kwargs):
        raise AssertionError("offline mode must not connect")

    monkeypatch.setattr(migration, "create_engine", fail_if_connected)

    assert migration.main(["--offline-only"]) == 0
    report = json.loads(capsys.readouterr().out)

    assert report["mode"] == "OFFLINE_NO_CONNECTION"
    assert report["database_changes"] is False
    assert report["target_database"] == "eurusd_oracle_local"
    assert report["feed_writes_authorized"] is False
    assert report["final_action"] == "HOLD"


def test_live_local_cli_requires_exact_database_confirmation():
    with pytest.raises(SystemExit) as error:
        migration.main(["--apply-local-dev"])

    assert error.value.code == 2
