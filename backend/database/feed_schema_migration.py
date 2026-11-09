"""Apply DP-M2D's additive feed schema to the empty local dev database only.

This is a workstation bootstrap migration, not a production/archive migration.
It refuses every database except ``eurusd_oracle_local`` on a local host and
requires an explicit database-name confirmation. It preserves legacy rows and
the existing global tick/candle uniqueness rules. Feed-labelled writes remain
blocked by the storage layer until feed-aware readers and cutover are ready.
"""

import argparse
import json
import os
import re
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


LOCAL_DATABASE_NAME = "eurusd_oracle_local"
LOCAL_HOSTS = {None, "", "localhost", "127.0.0.1", "::1"}

MIGRATION_SQL = (
    """CREATE TABLE IF NOT EXISTS public.oracle_mt5_feed_registry (
        feed_id VARCHAR(78) PRIMARY KEY,
        profile_id VARCHAR(64) NOT NULL,
        capture_epoch VARCHAR(64) NOT NULL,
        identity_sha256 CHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_oracle_feed_identity CHECK (
            feed_id = 'mt5_eurusd_v1_' || identity_sha256
            AND identity_sha256 ~ '^[0-9a-f]{64}$'
        )
    )""",
    "ALTER TABLE public.ticks ADD COLUMN IF NOT EXISTS feed_id VARCHAR(78) NULL",
    "ALTER TABLE public.broker_candles ADD COLUMN IF NOT EXISTS feed_id VARCHAR(78) NULL",
    """DO $migration$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'public.ticks'::regclass
              AND conname = 'fk_ticks_feed_registry'
        ) THEN
            ALTER TABLE public.ticks
                ADD CONSTRAINT fk_ticks_feed_registry
                FOREIGN KEY (feed_id)
                REFERENCES public.oracle_mt5_feed_registry (feed_id)
                ON DELETE RESTRICT NOT VALID;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
            WHERE conrelid = 'public.broker_candles'::regclass
              AND conname = 'fk_broker_candles_feed_registry'
        ) THEN
            ALTER TABLE public.broker_candles
                ADD CONSTRAINT fk_broker_candles_feed_registry
                FOREIGN KEY (feed_id)
                REFERENCES public.oracle_mt5_feed_registry (feed_id)
                ON DELETE RESTRICT NOT VALID;
        END IF;
    END
    $migration$""",
    "ALTER TABLE public.ticks VALIDATE CONSTRAINT fk_ticks_feed_registry",
    "ALTER TABLE public.broker_candles VALIDATE CONSTRAINT fk_broker_candles_feed_registry",
    """CREATE INDEX IF NOT EXISTS ix_ticks_feed_timestamp_msc_v2
        ON public.ticks (feed_id, "timestamp", source_time_msc)
        WHERE feed_id IS NOT NULL""",
    """CREATE INDEX IF NOT EXISTS ix_broker_candles_feed_time_v2
        ON public.broker_candles (feed_id, timeframe, "timestamp")
        WHERE feed_id IS NOT NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_candle_feed_v2
        ON public.broker_candles (feed_id, symbol, timeframe, "timestamp")
        WHERE feed_id IS NOT NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS uq_broker_candle_legacy_null_v2
        ON public.broker_candles (symbol, timeframe, "timestamp", source)
        WHERE feed_id IS NULL""",
)

EXPECTED_UNIQUES = {
    "ticks": {
        "uq_tick_fingerprint": ("fingerprint",),
    },
    "broker_candles": {
        "uq_broker_candle": (
            "symbol", "timeframe", "timestamp", "source"
        ),
    },
}

EXPECTED_INDEXES = {
    "ticks": {
        "ix_ticks_feed_timestamp_msc_v2": False,
    },
    "broker_candles": {
        "ix_broker_candles_feed_time_v2": False,
        "uq_broker_candle_feed_v2": True,
        "uq_broker_candle_legacy_null_v2": True,
    },
}

EXPECTED_INDEX_SHAPES = {
    "ix_ticks_feed_timestamp_msc_v2": (
        "ticks", ["feed_id", "timestamp", "source_time_msc"], False,
        "feed_id is not null",
    ),
    "ix_broker_candles_feed_time_v2": (
        "broker_candles", ["feed_id", "timeframe", "timestamp"], False,
        "feed_id is not null",
    ),
    "uq_broker_candle_feed_v2": (
        "broker_candles", ["feed_id", "symbol", "timeframe", "timestamp"],
        True, "feed_id is not null",
    ),
    "uq_broker_candle_legacy_null_v2": (
        "broker_candles", ["symbol", "timeframe", "timestamp", "source"],
        True, "feed_id is null",
    ),
}


class FeedSchemaMigrationError(RuntimeError):
    """The local database did not satisfy the guarded migration contract."""


def validate_local_database_url(raw_url):
    """Parse a URL without connecting and refuse non-local/other databases."""
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise FeedSchemaMigrationError("DATABASE_URL is required.")

    try:
        url = make_url(raw_url)
    except Exception as exc:
        raise FeedSchemaMigrationError("DATABASE_URL is invalid.") from exc

    if url.drivername != "postgresql+psycopg2":
        raise FeedSchemaMigrationError(
            "Local feed migration requires postgresql+psycopg2."
        )
    if url.database != LOCAL_DATABASE_NAME:
        raise FeedSchemaMigrationError(
            "Refusing migration outside the dedicated local dev database."
        )
    if url.host not in LOCAL_HOSTS:
        raise FeedSchemaMigrationError(
            "Refusing migration to a non-local PostgreSQL host."
        )
    if url.username not in (None, "Komarchy"):
        raise FeedSchemaMigrationError(
            "Local schema migration requires the restricted Komarchy role."
        )
    if url.port not in (None, 5432):
        raise FeedSchemaMigrationError(
            "Refusing a nonstandard PostgreSQL port for local migration."
        )
    if url.query:
        raise FeedSchemaMigrationError(
            "DATABASE_URL query overrides are not permitted for migration."
        )
    return url


def _unique_constraints(connection, table_name):
    inspector = inspect(connection)
    return {
        item.get("name"): tuple(item.get("column_names") or ())
        for item in inspector.get_unique_constraints(
            table_name, schema="public"
        )
    }


def _preflight(connection):
    database_name = connection.execute(
        text("SELECT current_database()")
    ).scalar_one()
    if database_name != LOCAL_DATABASE_NAME:
        raise FeedSchemaMigrationError(
            "Connected database did not match the dedicated local dev name."
        )

    is_superuser = connection.execute(
        text(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        )
    ).scalar_one()
    if is_superuser:
        raise FeedSchemaMigrationError(
            "Refusing schema migration through a superuser role."
        )

    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema="public"))
    if not {"ticks", "broker_candles"}.issubset(tables):
        raise FeedSchemaMigrationError(
            "Canonical ticks and broker_candles tables are required."
        )

    for table_name, expected in EXPECTED_UNIQUES.items():
        observed = _unique_constraints(connection, table_name)
        for constraint_name, columns in expected.items():
            if observed.get(constraint_name) != columns:
                raise FeedSchemaMigrationError(
                    "Legacy uniqueness contract mismatch: " + table_name
                )

    for table_name in ("ticks", "broker_candles"):
        if connection.execute(
            text(f"SELECT EXISTS (SELECT 1 FROM public.{table_name} LIMIT 1)")
        ).scalar_one():
            raise FeedSchemaMigrationError(
                "Local bootstrap migration requires empty " + table_name
                + " tables; use a separately reviewed migration for data."
            )

    return database_name


def _postflight(connection):
    inspector = inspect(connection)
    for table_name in ("ticks", "broker_candles"):
        columns = {
            column["name"]: column
            for column in inspector.get_columns(table_name, schema="public")
        }
        feed_column = columns.get("feed_id")
        if feed_column is None or not feed_column["nullable"]:
            raise FeedSchemaMigrationError(
                "Feed ID column missing or not nullable: " + table_name
            )
        if getattr(feed_column["type"], "length", None) != 78:
            raise FeedSchemaMigrationError(
                "Feed ID column must be VARCHAR(78): " + table_name
            )

        foreign_keys = {
            item.get("name")
            for item in inspector.get_foreign_keys(
                table_name, schema="public"
            )
        }
        expected_fk = (
            "fk_ticks_feed_registry"
            if table_name == "ticks"
            else "fk_broker_candles_feed_registry"
        )
        if expected_fk not in foreign_keys:
            raise FeedSchemaMigrationError(
                "Feed registry foreign key missing: " + table_name
            )

    registry_columns = {
        column["name"]
        for column in inspector.get_columns(
            "oracle_mt5_feed_registry", schema="public"
        )
    }
    if not {
        "feed_id", "profile_id", "capture_epoch", "identity_sha256",
        "created_at",
    }.issubset(registry_columns):
        raise FeedSchemaMigrationError("Feed registry shape is incomplete.")

    registry_pk = inspector.get_pk_constraint(
        "oracle_mt5_feed_registry", schema="public"
    )
    if registry_pk.get("constrained_columns") != ["feed_id"]:
        raise FeedSchemaMigrationError("Feed registry primary key is invalid.")

    registry_checks = {
        item.get("name")
        for item in inspector.get_check_constraints(
            "oracle_mt5_feed_registry", schema="public"
        )
    }
    if "ck_oracle_feed_identity" not in registry_checks:
        raise FeedSchemaMigrationError(
            "Feed registry identity check constraint is missing."
        )

    indexes = {
        table_name: {
            item["name"]: bool(item.get("unique"))
            for item in inspector.get_indexes(table_name, schema="public")
        }
        for table_name in EXPECTED_INDEXES
    }
    for table_name, expected in EXPECTED_INDEXES.items():
        for name, unique in expected.items():
            if indexes[table_name].get(name) is not unique:
                raise FeedSchemaMigrationError(
                    "Feed index missing or has wrong uniqueness: " + name
                )

    for index_name, expected in EXPECTED_INDEX_SHAPES.items():
        row = connection.execute(
            text(
                """SELECT tablename AS table_name, indexdef
                   FROM pg_indexes
                   WHERE schemaname = 'public' AND indexname = :index_name"""
            ),
            {"index_name": index_name},
        ).mappings().one_or_none()
        if row is None:
            raise FeedSchemaMigrationError("Feed index is missing: " + index_name)

        table_name, columns, unique, predicate = expected
        index_def = re.sub(r"\s+", " ", row["indexdef"]).lower()
        normalized_def = index_def.replace('"timestamp"', "timestamp")
        normalized_predicate = re.sub(r"[()]", "", predicate.lower())
        normalized_predicate = re.sub(r"\s+", " ", normalized_predicate)
        actual_predicate = (
            index_def.split(" where ", 1)[1].strip()
            if " where " in index_def else ""
        )
        actual_predicate = re.sub(r"[()]", "", actual_predicate)
        actual_predicate = re.sub(r"\s+", " ", actual_predicate).strip()
        expected_columns = "(" + ", ".join(columns) + ")"
        unique_clause = "create unique index " if unique else "create index "
        if (not normalized_def.startswith(unique_clause)
                or f" on public.{table_name} using btree "
                + expected_columns not in normalized_def
                or actual_predicate != normalized_predicate):
            raise FeedSchemaMigrationError(
                "Feed index definition mismatch: " + index_name
            )

    for table_name, expected in EXPECTED_UNIQUES.items():
        observed = _unique_constraints(connection, table_name)
        for constraint_name, columns in expected.items():
            if observed.get(constraint_name) != columns:
                raise FeedSchemaMigrationError(
                    "Legacy uniqueness contract changed: " + table_name
                )


def migrate_empty_local_dev_database(raw_url):
    """Apply idempotent nullable schema and partial indexes to local dev only."""
    url = validate_local_database_url(raw_url)
    engine = create_engine(
        url,
        connect_args={
            "options": (
                "-c lock_timeout=2000 "
                "-c statement_timeout=15000 "
                "-c application_name=ORACLE_DP_M2D_LOCAL_SCHEMA"
            )
        },
    )
    try:
        with engine.begin() as connection:
            database_name = _preflight(connection)
            for statement in MIGRATION_SQL:
                connection.execute(text(statement))
            _postflight(connection)
    finally:
        engine.dispose()

    return {
        "milestone": "DP-M2D.1",
        "mode": "EMPTY_LOCAL_DEV_ADDITIVE_SCHEMA",
        "database": database_name,
        "registry_created_or_verified": True,
        "nullable_feed_columns_created_or_verified": [
            "ticks.feed_id", "broker_candles.feed_id"
        ],
        "partial_feed_indexes_created_or_verified": True,
        "legacy_unique_constraints_preserved": True,
        "legacy_rows_modified": 0,
        "feed_labelled_rows_written": 0,
        "archive_database_connected": False,
        "feed_writes_authorized": False,
        "final_action": "HOLD",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--offline-only", action="store_true")
    modes.add_argument("--apply-local-dev", action="store_true")
    parser.add_argument("--confirm-database-name", default="")
    args = parser.parse_args(argv)

    if args.offline_only:
        print(json.dumps({
            "milestone": "DP-M2D.1",
            "mode": "OFFLINE_NO_CONNECTION",
            "database_changes": False,
            "target_database": LOCAL_DATABASE_NAME,
            "feed_writes_authorized": False,
            "final_action": "HOLD",
        }, indent=2, sort_keys=True))
        return 0

    if args.confirm_database_name != LOCAL_DATABASE_NAME:
        parser.error(
            "Exact --confirm-database-name eurusd_oracle_local is required."
        )

    from dotenv import load_dotenv

    load_dotenv()
    raw_url = os.environ.get("DATABASE_URL")
    try:
        report = migrate_empty_local_dev_database(raw_url)
    except Exception as exc:
        # Do not print a connection URL, password, or driver exception detail.
        print(
            "LOCAL DEV MIGRATION BLOCKED: " + type(exc).__name__,
            file=sys.stderr,
        )
        return 1

    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
