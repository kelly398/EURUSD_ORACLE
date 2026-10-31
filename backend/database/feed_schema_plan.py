
"""DP-M2B offline PostgreSQL feed-schema review and NON-EXECUTING DDL proposal.

No database driver, connection, execution, writes, or side effects.
The proposed DDL is NOT approved to run against the production archive.
The existing Tick and BrokerCandle ORM schemas remain unchanged for now.
"""

from hashlib import sha256
import json

from sqlalchemy import UniqueConstraint

from backend.database.models import Base, BrokerCandle, Tick


class FeedSchemaPlanError(ValueError):
    """A source-model assumption needed for the migration was not established."""


# Templates only. Production catalog verification, a backup, capacity review,
# and separate authorization are mandatory before implementing any migration.
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


EXPECTED = {
    'ticks': {
        'required_columns': (
            'id', 'symbol', 'timestamp', 'source_time_msc', 'fingerprint'
        ),
        'unique_name': 'uq_tick_fingerprint',
        'unique_columns': ('fingerprint',),
    },
    'broker_candles': {
        'required_columns': (
            'id', 'symbol', 'timeframe', 'timestamp',
            'source', 'is_complete'
        ),
        'unique_name': 'uq_broker_candle',
        'unique_columns': (
            'symbol', 'timeframe', 'timestamp', 'source'
        ),
    },
}


def audit_source_models():
    """Inspect SQLAlchemy declarations without contacting PostgreSQL."""
    result = {}

    for model in (Tick, BrokerCandle):
        table = model.__table__
        contract = EXPECTED[table.name]
        columns = set(table.columns.keys())

        if not set(contract['required_columns']).issubset(columns):
            raise FeedSchemaPlanError(
                'Expected source columns missing: ' + table.name
            )

        if 'feed_id' in columns:
            raise FeedSchemaPlanError(
                'ORM already has feed_id; migration plan needs revision: '
                + table.name
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
            'feed_id_present_in_source_model': False,
        }

    if Base.metadata.tables.get('ticks') is not Tick.__table__:
        raise FeedSchemaPlanError('Tick metadata is not canonical.')

    if Base.metadata.tables.get('broker_candles') is not BrokerCandle.__table__:
        raise FeedSchemaPlanError('Broker-candle metadata is not canonical.')

    return result


def plan_summary():
    """Return deterministic offline plan evidence, not migration approval."""
    source = audit_source_models()

    result = {
        'milestone': 'DP-M2B',
        'mode': 'SOURCE_ONLY_NO_DATABASE_CONNECTION',
        'source_schema': source,
        'phase_a_sha256': sha256(
            PHASE_A_PROPOSAL.encode('utf-8')
        ).hexdigest(),
        'phase_b_sha256': sha256(
            PHASE_B_PROPOSAL.encode('utf-8')
        ).hexdigest(),
        'live_postgres_catalog_verified': False,
        'database_migration_applied': False,
        'historical_rows_modified': False,
        'legacy_fingerprints_changed': False,
        'old_source_uniques_preserved': True,
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
        print('\n' + PHASE_A_PROPOSAL + '\n' + PHASE_B_PROPOSAL)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
