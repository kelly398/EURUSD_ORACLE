
"""Offline schema-migration preflight. No PostgreSQL connection."""

from hashlib import sha256
import inspect
import json
import re

import pytest

from backend.database import feed_schema_plan as p


def test_current_tick_model_contract_matches_audit():
    result = p.audit_source_models()

    assert result['ticks']['unique_columns'] == ['fingerprint']
    assert result['ticks']['expected_old_constraint'] == 'uq_tick_fingerprint'
    assert result['ticks']['feed_id_present_in_source_model'] is True
    assert result['ticks']['feed_id_nullable'] is True
    assert result['ticks']['feed_id_length'] == 78
    assert result['ticks']['feed_registry_foreign_key'] == (
        'fk_ticks_feed_registry'
    )


def test_current_broker_candle_model_contract_matches_audit():
    result = p.audit_source_models()

    assert result['broker_candles']['unique_columns'] == [
        'symbol', 'timeframe', 'timestamp', 'source'
    ]
    assert result['broker_candles']['expected_old_constraint'] == (
        'uq_broker_candle'
    )
    assert result['broker_candles']['feed_id_present_in_source_model'] is True
    assert result['broker_candles']['feed_id_nullable'] is True
    assert result['broker_candles']['feed_id_length'] == 78


def test_additive_phase_contains_only_nullable_feed_columns_and_registry():
    sql = p.PHASE_A_PROPOSAL

    assert 'CREATE TABLE oracle_mt5_feed_registry' in sql
    assert 'ALTER TABLE ticks ADD COLUMN feed_id VARCHAR(78) NULL;' in sql
    assert (
        'ALTER TABLE broker_candles ADD COLUMN feed_id VARCHAR(78) NULL;'
    ) in sql
    assert 'identity_sha256 ~' in sql
    assert 'fk_ticks_feed_registry' in sql
    assert 'fk_broker_candles_feed_registry' in sql
    assert "lock_timeout = '2s'" in sql
    assert "statement_timeout = '15s'" in sql
    assert sql.count('COMMIT;') == 1

    for forbidden in (
        'DROP TABLE',
        'DROP CONSTRAINT',
        'TRUNCATE',
        'DELETE FROM',
        'UPDATE ticks',
        'UPDATE broker_candles',
    ):
        assert forbidden not in sql


def test_stage_b_indexes_are_partial_and_postgres_concurrent():
    sql = p.PHASE_B_PROPOSAL

    assert len(re.findall(
        r'^CREATE INDEX CONCURRENTLY', sql, re.M
    )) == 2

    assert len(re.findall(
        r'^CREATE UNIQUE INDEX CONCURRENTLY', sql, re.M
    )) == 2

    assert sql.count('WHERE feed_id IS NOT NULL') == 3
    assert sql.count('WHERE feed_id IS NULL') == 1
    assert 'DROP CONSTRAINT' not in sql
    assert 'BEGIN;' not in sql
    assert 'COMMIT;' not in sql


def test_stage_c_cutover_is_separate_bounded_and_not_applied():
    sql = p.PHASE_C_PROPOSAL

    assert 'DROP CONSTRAINT uq_broker_candle' in sql
    assert "lock_timeout = '2s'" in sql
    assert "statement_timeout = '15s'" in sql
    assert 'LOCK TABLE broker_candles IN ACCESS EXCLUSIVE MODE' in sql
    assert "uq_broker_candle_feed_v2" in sql
    assert "uq_broker_candle_legacy_null_v2" in sql
    assert 'i.indisunique AND i.indisready AND i.indisvalid' in sql
    assert 'WHERE feed_id IS NOT NULL LIMIT 1' in sql
    assert sql.count('DROP CONSTRAINT') == 1
    assert 'CREATE INDEX' not in sql
    assert 'Feed-labelled writes remain disabled' in sql
    assert 'DO NOT EXECUTE' in sql

    report = p.plan_summary()
    assert report['legacy_constraint_cutover_applied'] is False
    assert report['cutover_preconditions_verified'] is False
    assert report['new_feed_writes_authorized'] is False


def test_orm_columns_are_nullable_and_plan_has_no_database_side_effects():
    assert p.Tick.__table__.columns['feed_id'].nullable is True
    assert p.BrokerCandle.__table__.columns['feed_id'].nullable is True
    assert 'oracle_mt5_feed_registry' in p.Base.metadata.tables

    source = inspect.getsource(p)

    for forbidden in (
        'create_engine(',
        'engine.connect(',
        'SessionLocal(',
        '.execute(',
        'metadata.create_all(',
        'import MetaTrader5',
        'order_send(',
        'mt5.initialize(',
    ):
        assert forbidden not in source


def test_no_market_data_or_trading_authorization():
    report = p.plan_summary()

    assert report['live_postgres_catalog_verified'] is False
    assert report['database_migration_applied'] is False
    assert report['legacy_constraint_cutover_applied'] is False
    assert report['cutover_preconditions_verified'] is False
    assert report['historical_rows_modified'] is False
    assert report['legacy_fingerprints_changed'] is False
    assert report['old_source_uniques_preserved'] is True
    assert report['orm_feed_schema_present'] is True
    assert report['local_dev_migration_applied'] is False
    assert report['new_feed_writes_authorized'] is False
    assert report['external_gates_verified'] == 0
    assert report['source_families_admitted'] == 0
    assert report['final_action'] == 'HOLD'


def test_plan_reproducible_digest():
    x = p.plan_summary()
    y = p.plan_summary()

    assert x == y

    digest = x.pop('summary_sha256')

    canonical = json.dumps(
        x,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
    ).encode()

    assert digest == sha256(canonical).hexdigest()


def test_detect_nonnullable_feed_col(monkeypatch):
    class FakeFeedColumn:
        nullable = False
        type = p.Tick.__table__.columns['feed_id'].type

    class FakeTable:
        name = 'ticks'
        columns = {
            'id': 1,
            'symbol': 1,
            'timestamp': 1,
            'source_time_msc': 1,
            'fingerprint': 1,
            'feed_id': FakeFeedColumn(),
        }
        constraints = (
            type(
                'FakeUnique',
                (),
                {
                    'name': 'uq_tick_fingerprint',
                    'columns': [
                        type('FakeColumn', (), {'name': 'fingerprint'})()
                    ],
                },
            )(),
        )

    class FakeTick:
        __table__ = FakeTable()

    monkeypatch.setattr(p, 'Tick', FakeTick)

    with pytest.raises(p.FeedSchemaPlanError, match='nullable VARCHAR'):
        p.audit_source_models()


def test_detect_missing_unique_constraint(monkeypatch):
    class FakeTable:
        name = 'ticks'
        columns = {
            'id': 1,
            'symbol': 1,
            'timestamp': 1,
            'source_time_msc': 1,
            'fingerprint': 1,
            'feed_id': type(
                'FakeFeedColumn',
                (),
                {
                    'nullable': True,
                    'type': p.Tick.__table__.columns['feed_id'].type,
                },
            )(),
        }
        constraints = ()
        foreign_keys = [
            type(
                'FakeForeignKey',
                (),
                {'name': 'fk_ticks_feed_registry'},
            )(),
        ]

    class FakeTick:
        __table__ = FakeTable()

    monkeypatch.setattr(p, 'Tick', FakeTick)

    with pytest.raises(p.FeedSchemaPlanError, match='uniqueness'):
        p.audit_source_models()


def test_cli_offline_report(capsys):
    assert p.main(['--offline-only']) == 0

    parsed = json.loads(capsys.readouterr().out)

    assert parsed['mode'] == 'ORM_AND_PROPOSAL_NO_DATABASE_CONNECTION'
    assert parsed['final_action'] == 'HOLD'


def test_cli_sql_requires_explicit_flag(capsys):
    assert p.main([
        '--offline-only',
        '--show-proposed-sql',
    ]) == 0

    output = capsys.readouterr().out

    assert '-- DP-M2B PHASE A PROPOSAL ONLY: DO NOT EXECUTE.' in output
    assert 'CREATE INDEX CONCURRENTLY' in output
    assert '-- DP-M2B PHASE C PROPOSAL ONLY: DO NOT EXECUTE.' in output
    assert 'ALTER TABLE broker_candles DROP CONSTRAINT uq_broker_candle;' in output


def test_cli_requires_offline_flag():
    with pytest.raises(SystemExit) as error:
        p.main([])

    assert error.value.code == 2
