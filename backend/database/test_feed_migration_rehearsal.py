
"""DP-M2B.3 offline guards; no PostgreSQL server or credentials needed."""

import inspect
import json

import pytest

from backend.database import feed_migration_rehearsal as lab


def test_offline_cli_cannot_connect(capsys, monkeypatch):
    monkeypatch.setattr(lab, "run_lab", lambda: (_ for _ in ()).throw(AssertionError("connected")))
    assert lab.main(["--offline-only"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "OFFLINE_NO_CONNECTION"
    assert report["final_action"] == "HOLD"


def test_lab_cli_requires_exact_explicit_acknowledgement(capsys, monkeypatch):
    monkeypatch.setattr(lab, "run_lab", lambda: (_ for _ in ()).throw(AssertionError("connected")))
    with pytest.raises(SystemExit) as e:
        lab.main(["--run-disposable-lab"])
    assert e.value.code == 2


def test_no_implicit_operation(capsys):
    with pytest.raises(SystemExit) as e:
        lab.main([])
    assert e.value.code == 2


@pytest.mark.parametrize("url", [
    "", None, "postgresql://user:pass@localhost/eurusd_oracle",
    "sqlite:///eurusd_oracle_dp_m2b_lab",
    "postgresql://user:pass@localhost/postgres",
    "postgresql://user:pass@localhost/eurusd_oracle_dp_m2b_lab_wrong",
    "malformed database URL ://",
])
def test_lab_url_rejects_wrong_target(url):
    with pytest.raises(ValueError):
        lab.validate_lab_url(url)


def test_lab_url_accepts_only_named_postgres_lab():
    url = "postgresql+psycopg2://lab:dummy@localhost/eurusd_oracle_dp_m2b_lab"
    assert lab.validate_lab_url(url).database == lab.LAB_NAME


def test_lab_url_rejects_production_same_db():
    url = "postgresql://lab:dummy@localhost/eurusd_oracle_dp_m2b_lab"
    with pytest.raises(ValueError):
        lab.validate_lab_url(url, url)


def test_lab_url_rejects_primary_db_name_overlap_even_if_creds_differ():
    lab_url = "postgresql://lab:lab@localhost/eurusd_oracle_dp_m2b_lab"
    primary_url = "postgresql://prod:prod@localhost/eurusd_oracle_dp_m2b_lab"
    with pytest.raises(ValueError):
        lab.validate_lab_url(lab_url, primary_url)


def test_lab_url_cannot_be_implicit_production_url():
    assert lab.LAB_URL_ENV != "DATABASE_URL"
    assert lab.LAB_NAME != "eurusd_oracle"
    source = inspect.getsource(lab.run_lab)
    assert 'os.environ.get(LAB_URL_ENV)' in source
    assert 'os.environ.get("DATABASE_URL")' in source
    assert 'connection.rollback()' in source
    assert 'connection.commit(' not in source


def test_all_ddl_targets_temp_lab_tables_only():
    assert len(lab.CREATE_TEMP_TABLES) == 3
    assert all("CREATE TEMP TABLE dp_m2b_" in s for s in lab.CREATE_TEMP_TABLES)
    assert all("ON COMMIT DROP" in s for s in lab.CREATE_TEMP_TABLES)
    for sql in lab.CREATE_TEMP_TABLES + lab.ADD_COLUMNS_AND_INDEXES:
        assert "ALTER TABLE ticks " not in sql
        assert "ALTER TABLE broker_candles " not in sql
        assert "public." not in sql
        assert "CONCURRENTLY" not in sql
    assert "DROP CONSTRAINT uq_broker_candle" in inspect.getsource(lab.rehearse)
    assert "ALTER TABLE dp_m2b_broker_candles" in inspect.getsource(lab.rehearse)


def test_fixture_covers_old_and_new_uniqueness_contracts():
    source = inspect.getsource(lab.rehearse)
    assert 'legacy_writer=True' in source
    assert 'old_writer_broken' in source
    assert 'create_tick_fingerprint_v2' in source
    assert 'create_tick_fingerprint(' in source
    assert 'feed_id IS NULL' in " ".join(lab.ADD_COLUMNS_AND_INDEXES)
    assert 'feed_id IS NOT NULL' in " ".join(lab.ADD_COLUMNS_AND_INDEXES)


def test_no_mt5_or_trades_or_live_database_connection():
    source = inspect.getsource(lab)
    assert "import MetaTrader5" not in source
    assert "order_send(" not in source
    assert "backend.database.connection" not in source
    assert "connect(DATABASE_URL" not in source


def test_label_ids_are_synthetic_and_distinct():
    from backend.data_engine.feed_identity import require_feed_id
    assert require_feed_id(lab.FEED_A)
    assert require_feed_id(lab.FEED_B)
    assert lab.FEED_A != lab.FEED_B
