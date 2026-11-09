
"""DP-M2B offline source-schema audit and PostgreSQL DDL proposals.

No database driver, connection, execution, writes, or side effects.
The proposals are NOT approved to run against the restored archive.
DP-M2D adds nullable feed identity to the ORM and a guarded local-dev
bootstrap migration; these proposals remain the separate large-database plan.
"""

from hashlib import sha256
import json

from sqlalchemy import String, UniqueConstraint

from backend.database.models import (
    Base,
    BrokerCandle,
    MT5FeedRegistry,
    Tick,
)


class FeedSchemaPlanError(ValueError):
    """A source-model assumption needed for the migration was not established."""


# Templates only. The production/archive path still requires catalog
# verification, a recoverable backup, capacity review and separate approval.
PHASE_A_PROPOSAL = """-- DP-M2B PHASE A PROPOSAL ONLY: DO NOT EXECUTE.
-- Evaluate locks, maintenance window and rollback before approval.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';
CREATE TABLE oracle_mt5_feed_registry (
    feed_id VARCHAR(78) PRIMARY KEY,
    profile_id VARCHAR(64) NOT NULL,
    capture_epoch VARCHAR(64) NOT NULL,
    identity_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_oracle_feed_identity CHECK (
        feed_id = 'mt5_eurusd_v1_' || identity_sha256
        AND identity_sha256 ~ '^[0-9a-f]{64}$'
    )
);
ALTER TABLE ticks ADD COLUMN feed_id VARCHAR(78) NULL;
ALTER TABLE broker_candles ADD COLUMN feed_id VARCHAR(78) NULL;
ALTER TABLE ticks ADD CONSTRAINT fk_ticks_feed_registry
    FOREIGN KEY (feed_id) REFERENCES oracle_mt5_feed_registry(feed_id)
    ON DELETE RESTRICT NOT VALID;
ALTER TABLE broker_candles ADD CONSTRAINT fk_broker_candles_feed_registry
    FOREIGN KEY (feed_id) REFERENCES oracle_mt5_feed_registry(feed_id)
    ON DELETE RESTRICT NOT VALID;
COMMIT;
"""

PHASE_B_PROPOSAL = """-- DP-M2B PHASE B PROPOSAL ONLY: DO NOT EXECUTE.
-- CREATE INDEX CONCURRENTLY must run outside a transaction and after
-- verifying PostgreSQL catalog, permissions, available space and locks.
CREATE INDEX CONCURRENTLY ix_ticks_feed_timestamp_msc_v2
    ON ticks (feed_id, timestamp, source_time_msc)
    WHERE feed_id IS NOT NULL;
CREATE INDEX CONCURRENTLY ix_broker_candles_feed_time_v2
    ON broker_candles (feed_id, timeframe, timestamp)
    WHERE feed_id IS NOT NULL;
CREATE UNIQUE INDEX CONCURRENTLY uq_broker_candle_feed_v2
    ON broker_candles (feed_id, symbol, timeframe, timestamp)
    WHERE feed_id IS NOT NULL;
CREATE UNIQUE INDEX CONCURRENTLY uq_broker_candle_legacy_null_v2
    ON broker_candles (symbol, timeframe, timestamp, source)
    WHERE feed_id IS NULL;
"""

PHASE_C_PROPOSAL = """-- DP-M2B PHASE C PROPOSAL ONLY: DO NOT EXECUTE.
-- Run only after every candle writer uses untargeted ON CONFLICT and
-- backup/recovery is approved. Index state and existing feed rows are checked
-- under a bounded table lock immediately before removing the old constraint.
-- Feed-labelled writes remain disabled until readers and ORM are migrated.
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '15s';
LOCK TABLE broker_candles IN ACCESS EXCLUSIVE MODE;
DO $preflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_class idx ON idx.oid = i.indexrelid
        WHERE i.indrelid = 'broker_candles'::regclass
          AND idx.relname = 'uq_broker_candle_feed_v2'
          AND i.indisunique AND i.indisready AND i.indisvalid
    ) THEN
        RAISE EXCEPTION 'Feed-specific candle index is not valid and ready';
    END IF;
    IF NOT EXISTS (
        SELECT 1
        FROM pg_index i
        JOIN pg_class idx ON idx.oid = i.indexrelid
        WHERE i.indrelid = 'broker_candles'::regclass
          AND idx.relname = 'uq_broker_candle_legacy_null_v2'
          AND i.indisunique AND i.indisready AND i.indisvalid
    ) THEN
        RAISE EXCEPTION 'Legacy candle index is not valid and ready';
    END IF;
    IF EXISTS (
        SELECT 1 FROM broker_candles WHERE feed_id IS NOT NULL LIMIT 1
    ) THEN
        RAISE EXCEPTION 'Feed-labelled candle rows exist before writer cutover';
    END IF;
END
$preflight$;
ALTER TABLE broker_candles DROP CONSTRAINT uq_broker_candle;
COMMIT;
-- Recreating the old global constraint may be impossible after feed-specific
-- duplicate keys exist; define and approve rollback before any feed write.
"""


EXPECTED = {
    'ticks': {
        'required_columns': (
            'id', 'symbol', 'timestamp', 'source_time_msc', 'fingerprint',
            'feed_id',
        ),
        'unique_name': 'uq_tick_fingerprint',
        'unique_columns': ('fingerprint',),
    },
    'broker_candles': {
        'required_columns': (
            'id', 'symbol', 'timeframe', 'timestamp',
            'source', 'is_complete', 'feed_id'
        ),
        'unique_name': 'uq_broker_candle',
        'unique_columns': (
            'symbol', 'timeframe', 'timestamp', 'source'
        ),
    },
}


def audit_source_models():
    """Inspect additive ORM declarations without contacting PostgreSQL."""
    result = {}

    foreign_keys = {
        'ticks': 'fk_ticks_feed_registry',
        'broker_candles': 'fk_broker_candles_feed_registry',
    }

    for model in (Tick, BrokerCandle):
        table = model.__table__
        contract = EXPECTED[table.name]
        columns = set(table.columns.keys())

        if not set(contract['required_columns']).issubset(columns):
            raise FeedSchemaPlanError(
                'Expected source columns missing: ' + table.name
            )

        if 'feed_id' not in columns:
            raise FeedSchemaPlanError(
                'ORM feed_id column is missing: '
                + table.name
            )

        feed_column = table.columns['feed_id']
        if (not feed_column.nullable
                or not isinstance(feed_column.type, String)
                or feed_column.type.length != 78):
            raise FeedSchemaPlanError(
                'feed_id must be nullable VARCHAR(78): ' + table.name
            )

        fk_names = {item.name for item in table.foreign_keys}
        if foreign_keys[table.name] not in fk_names:
            raise FeedSchemaPlanError(
                'Feed registry foreign key is missing: ' + table.name
            )

        uniques = {
            item.name: tuple(col.name for col in item.columns)
            for item in table.constraints
            if isinstance(item, UniqueConstraint)
        }

        if uniques.get(contract['unique_name']) != contract['unique_columns']:
            raise FeedSchemaPlanError(
                'Current uniqueness contract differs: ' + table.name
            )

        result[table.name] = {
            'expected_old_constraint': contract['unique_name'],
            'unique_columns': list(contract['unique_columns']),
            'feed_id_present_in_source_model': True,
            'feed_id_nullable': feed_column.nullable,
            'feed_id_length': feed_column.type.length,
            'feed_registry_foreign_key': foreign_keys[table.name],
        }

    registry_table = MT5FeedRegistry.__table__
    registry_columns = set(registry_table.columns.keys())
    if not {
        'feed_id', 'profile_id', 'capture_epoch', 'identity_sha256',
        'created_at',
    }.issubset(registry_columns):
        raise FeedSchemaPlanError('Feed registry ORM contract is incomplete.')

    if registry_table.primary_key.columns.keys() != ['feed_id']:
        raise FeedSchemaPlanError('Feed registry primary key changed.')

    if Base.metadata.tables.get('ticks') is not Tick.__table__:
        raise FeedSchemaPlanError('Tick metadata is not canonical.')

    if Base.metadata.tables.get('broker_candles') is not BrokerCandle.__table__:
        raise FeedSchemaPlanError('Broker-candle metadata is not canonical.')

    if (Base.metadata.tables.get('oracle_mt5_feed_registry')
            is not MT5FeedRegistry.__table__):
        raise FeedSchemaPlanError('Feed registry metadata is not canonical.')

    result['oracle_mt5_feed_registry'] = {
        'primary_key': ['feed_id'],
        'columns': sorted(registry_columns),
        'identity_check_constraint': 'ck_oracle_feed_identity',
    }

    return result


def plan_summary():
    """Return deterministic offline plan evidence, not migration approval."""
    source = audit_source_models()

    result = {
        'milestone': 'DP-M2B',
        'mode': 'ORM_AND_PROPOSAL_NO_DATABASE_CONNECTION',
        'source_schema': source,
        'phase_a_sha256': sha256(
            PHASE_A_PROPOSAL.encode('utf-8')
        ).hexdigest(),
        'phase_b_sha256': sha256(
            PHASE_B_PROPOSAL.encode('utf-8')
        ).hexdigest(),
        'phase_c_sha256': sha256(
            PHASE_C_PROPOSAL.encode('utf-8')
        ).hexdigest(),
        'live_postgres_catalog_verified': False,
        'database_migration_applied': False,
        'legacy_constraint_cutover_applied': False,
        'cutover_preconditions_verified': False,
        'historical_rows_modified': False,
        'legacy_fingerprints_changed': False,
        'old_source_uniques_preserved': True,
        'orm_feed_schema_present': True,
        'local_dev_migration_applied': False,
        'new_feed_writes_authorized': False,
        'external_gates_verified': 0,
        'source_families_admitted': 0,
        'final_action': 'HOLD',
    }

    canonical = json.dumps(
        result,
        sort_keys=True,
        separators=(',', ':'),
        ensure_ascii=True,
    ).encode('utf-8')

    result['summary_sha256'] = sha256(canonical).hexdigest()
    return result


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--offline-only', action='store_true', required=True
    )
    parser.add_argument(
        '--show-proposed-sql', action='store_true'
    )

    args = parser.parse_args(argv)
    result = plan_summary()

    print(json.dumps(result, sort_keys=True, indent=2))

    if args.show_proposed_sql:
        print(
            '\n' + PHASE_A_PROPOSAL + '\n' + PHASE_B_PROPOSAL
            + '\n' + PHASE_C_PROPOSAL
        )

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
