"""DP-M2D.8 clone-migration guards; never connect to PostgreSQL in tests."""

import inspect
import json

import pytest

from backend.database import feed_clone_migration as migration


CLONE_URL = "postgresql+psycopg2:///eurusd_oracle_dp_m2d8_clone"


def test_clone_url_accepts_only_exact_local_clone():
    url = migration.validate_clone_database_url(CLONE_URL)
    assert url.database == migration.CLONE_DATABASE_NAME
    assert url.host is None


@pytest.mark.parametrize("url", [
    "postgresql+psycopg2:///eurusd_oracle_archive",
    "postgresql+psycopg2:///eurusd_oracle_local",
    "postgresql+psycopg2://db.example/eurusd_oracle_dp_m2d8_clone",
    "postgresql+psycopg2://Komarchy@localhost:5433/eurusd_oracle_dp_m2d8_clone",
    "postgresql+psycopg2:///eurusd_oracle_dp_m2d8_clone?host=db.example",
    "sqlite:///eurusd_oracle_dp_m2d8_clone.db",
    "",
])
def test_clone_url_rejects_archive_primary_remote_and_malformed_targets(url):
    with pytest.raises(migration.FeedCloneMigrationError):
        migration.validate_clone_database_url(url)


def test_clone_url_rejects_overlap_with_primary_url():
    with pytest.raises(migration.FeedCloneMigrationError):
        migration.validate_clone_database_url(CLONE_URL, CLONE_URL)


def test_preflight_pins_clone_counts_and_legacy_constraints():
    assert migration.EXPECTED_ROW_COUNTS == {
        "ticks": 30_744_086,
        "broker_candles": 75_528,
        "candles": 2,
    }
    source = inspect.getsource(migration._preflight)
    assert "archive_sessions" in source
    assert "role_name != \"Komarchy\"" in source
    assert "EXPECTED_UNIQUES" in source
    assert "has_feed_rows" in source
    assert "free_bytes < 2 * database_bytes" in source


def test_clone_migration_reuses_only_additive_feed_schema():
    sql = "\n".join(migration.MIGRATION_SQL).upper()
    assert "ADD COLUMN IF NOT EXISTS FEED_ID VARCHAR(78) NULL" in sql
    assert "FOREIGN KEY (FEED_ID)" in sql
    for forbidden in (
        "DROP TABLE",
        "DROP CONSTRAINT",
        "TRUNCATE",
        "DELETE FROM",
        "UPDATE TICKS",
        "UPDATE BROKER_CANDLES",
    ):
        assert forbidden not in sql


def test_offline_cli_never_connects_or_changes_database(monkeypatch, capsys):
    def fail_if_connected(*args, **kwargs):
        raise AssertionError("offline mode must not connect")

    monkeypatch.setattr(migration, "create_engine", fail_if_connected)
    assert migration.main(["--offline-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["target_database"] == migration.CLONE_DATABASE_NAME
    assert report["archive_database_connected"] is False
    assert report["database_changes"] is False
    assert report["final_action"] == "HOLD"


def test_clone_apply_requires_exact_name_before_url_access():
    with pytest.raises(SystemExit) as error:
        migration.main([
            "--apply-disposable-clone",
            "--confirm-database-name",
            "eurusd_oracle_archive",
        ])
    assert error.value.code == 2
