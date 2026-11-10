"""Rollback-only legacy candle writer rehearsal after clone constraint removal.

This exercises the planned short cutover transaction on the disposable clone:
verify replacement indexes, remove the old global constraint, run the actual
legacy broker-candle writer and a duplicate retry, then roll back all DDL and
rows. No feed-labelled rows are inserted and no archive target is accepted.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import sys
from time import perf_counter

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.database.feed_clone_migration import (
    CLONE_DATABASE_NAME,
    CLONE_URL_ENV,
    EXPECTED_ROW_COUNTS,
    FeedCloneMigrationError,
    _preflight,
    _row_counts,
    validate_clone_database_url,
)


TEST_SYMBOL = "ORACLE_DP_M2D9_CUTOVER_REHEARSAL"
CANDLE_ID = -202611090901
CANDLE_TIME = datetime(2026, 11, 9, 12, 5, tzinfo=timezone.utc)
REQUIRED_INDEXES = {
    "uq_broker_candle_feed_v2",
    "uq_broker_candle_legacy_null_v2",
}


def _replacement_indexes(connection):
    rows = connection.execute(text("""
        SELECT idx.relname, i.indisunique, i.indisready, i.indisvalid
        FROM pg_index AS i
        JOIN pg_class AS tbl ON tbl.oid = i.indrelid
        JOIN pg_class AS idx ON idx.oid = i.indexrelid
        JOIN pg_namespace AS n ON n.oid = tbl.relnamespace
        WHERE n.nspname = 'public'
          AND tbl.relname = 'broker_candles'
          AND idx.relname = ANY(:names)
    """), {"names": sorted(REQUIRED_INDEXES)}).mappings().all()
    found = {row["relname"]: row for row in rows}
    if set(found) != REQUIRED_INDEXES:
        raise FeedCloneMigrationError("Replacement candle indexes are missing.")
    if any(
        not row["indisunique"] or not row["indisready"] or not row["indisvalid"]
        for row in found.values()
    ):
        raise FeedCloneMigrationError("Replacement candle indexes are not unique, ready, and valid.")
    return found


def _ensure_fixture_clear(connection):
    existing = connection.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM public.broker_candles
            WHERE id = :row_id OR symbol = :symbol
        )
    """), {"row_id": CANDLE_ID, "symbol": TEST_SYMBOL}).scalar_one()
    if existing:
        raise FeedCloneMigrationError("Cutover rehearsal fixture already exists.")


def _run_writer_after_constraint_drop(connection):
    import backend.candle_engine.broker_candle_storage as candle_storage

    original_factory = candle_storage.SessionLocal
    candle_storage.SessionLocal = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )
    try:
        row = {
            "id": CANDLE_ID,
            "symbol": TEST_SYMBOL,
            "timeframe": "M5",
            "timestamp": CANDLE_TIME,
            "open": 1.1000,
            "high": 1.1005,
            "low": 1.0998,
            "close": 1.1002,
            "tick_volume": 19,
            "spread_points": 2,
            "real_volume": 0.0,
            "is_complete": True,
            "source": "MT5",
        }
        writer = candle_storage.BrokerCandleStorage()
        start = perf_counter()
        inserted_first = writer.save_batch([row])
        first_ms = round((perf_counter() - start) * 1000, 3)
        start = perf_counter()
        inserted_retry = writer.save_batch([row])
        retry_ms = round((perf_counter() - start) * 1000, 3)
        visible_rows = connection.execute(text(
            "SELECT count(*) FROM public.broker_candles WHERE id = :id"
        ), {"id": CANDLE_ID}).scalar_one()
        if not connection.in_transaction():
            raise FeedCloneMigrationError("Writer commit escaped the outer transaction.")
        if (inserted_first, inserted_retry, visible_rows) != (1, 0, 1):
            raise FeedCloneMigrationError("Legacy candle insert/retry contract failed after constraint removal.")
        return {
            "legacy_candle_inserted_inside_transaction": inserted_first,
            "duplicate_retry_inserted": inserted_retry,
            "synthetic_rows_visible_before_rollback": visible_rows,
            "writer_elapsed_ms": {
                "first_insert": first_ms,
                "duplicate_retry": retry_ms,
            },
            "outer_transaction_still_open_after_writer_commits": True,
        }
    finally:
        candle_storage.SessionLocal = original_factory


def rehearse_clone_cutover(raw_url, *, confirm_database_name):
    if confirm_database_name != CLONE_DATABASE_NAME:
        raise FeedCloneMigrationError("Exact clone-name confirmation is required.")
    url = validate_clone_database_url(raw_url)
    engine = create_engine(
        url,
        connect_args={
            "options": (
                "-c lock_timeout=2000 "
                "-c statement_timeout=30000 "
                "-c application_name=ORACLE_DP_M2D9_CUTOVER_REHEARSAL"
            )
        },
    )
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                counts_before, free_bytes = _preflight(connection)
                indexes = _replacement_indexes(connection)
                _ensure_fixture_clear(connection)
                feed_rows = connection.execute(text("""
                    SELECT count(*) FROM public.broker_candles
                    WHERE feed_id IS NOT NULL
                """)).scalar_one()
                if feed_rows:
                    raise FeedCloneMigrationError("Feed-labelled candle rows exist; refusing cutover rehearsal.")

                connection.execute(text(
                    "ALTER TABLE public.broker_candles DROP CONSTRAINT uq_broker_candle"
                ))
                old_constraint_after_drop = connection.execute(text("""
                    SELECT EXISTS (
                        SELECT 1 FROM pg_constraint
                        WHERE conrelid = 'public.broker_candles'::regclass
                          AND conname = 'uq_broker_candle'
                    )
                """)).scalar_one()
                if old_constraint_after_drop:
                    raise FeedCloneMigrationError("Old candle constraint remained after transactional drop.")
                writer_result = _run_writer_after_constraint_drop(connection)
            finally:
                transaction.rollback()

        with engine.connect() as connection:
            counts_after = _row_counts(connection)
            old_constraint_restored = connection.execute(text("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conrelid = 'public.broker_candles'::regclass
                      AND conname = 'uq_broker_candle'
                )
            """)).scalar_one()
            fixture_rows = connection.execute(text(
                "SELECT count(*) FROM public.broker_candles WHERE id = :id"
            ), {"id": CANDLE_ID}).scalar_one()
            indexes_after = _replacement_indexes(connection)
        if counts_after != counts_before or counts_after != EXPECTED_ROW_COUNTS:
            raise FeedCloneMigrationError("Rollback changed canonical clone row counts.")
        if not old_constraint_restored or fixture_rows:
            raise FeedCloneMigrationError("Rollback did not restore constraint and remove fixture row.")
        if set(indexes_after) != set(indexes):
            raise FeedCloneMigrationError("Replacement indexes changed during rehearsal.")

        return {
            "milestone": "DP-M2D.9_CLONE_CUTOVER_ROLLBACK_REHEARSAL",
            "mode": "DISPOSABLE_CLONE_CONSTRAINT_DROP_AND_ROLLBACK",
            "database": CLONE_DATABASE_NAME,
            "row_counts_before": counts_before,
            "row_counts_after_rollback": counts_after,
            "feed_indexes_unique_ready_valid_before": True,
            "feed_indexes_unique_ready_valid_after": True,
            "old_constraint_absent_inside_transaction": True,
            "old_constraint_restored_after_rollback": old_constraint_restored,
            "synthetic_fixture_rows_after_rollback": fixture_rows,
            "persistent_clone_rows_written": 0,
            "sequence_values_advanced": False,
            "free_bytes_before": free_bytes,
            "feed_labelled_candles_written": 0,
            "feed_writes_authorized": False,
            "archive_database_connected": False,
            "archive_database_modified": False,
            "rollback_verified": True,
            "final_action": "HOLD",
            **writer_result,
        }
    finally:
        engine.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--offline-only", action="store_true")
    modes.add_argument("--run-disposable-clone", action="store_true")
    parser.add_argument("--confirm-database-name", default="")
    args = parser.parse_args(argv)

    if args.offline_only:
        print(json.dumps({
            "mode": "OFFLINE_NO_CONNECTION",
            "target_database": CLONE_DATABASE_NAME,
            "persistent_clone_rows_written": False,
            "constraint_change_persistent": False,
            "feed_writes_authorized": False,
            "final_action": "HOLD",
        }, indent=2, sort_keys=True))
        return 0
    if args.confirm_database_name != CLONE_DATABASE_NAME:
        parser.error(
            "Exact --confirm-database-name eurusd_oracle_dp_m2d8_clone is required."
        )

    from dotenv import load_dotenv

    load_dotenv()
    raw_url = os.environ.get(CLONE_URL_ENV)
    try:
        validate_clone_database_url(raw_url, os.environ.get("DATABASE_URL"))
        report = rehearse_clone_cutover(
            raw_url,
            confirm_database_name=args.confirm_database_name,
        )
    except Exception as exc:
        print("CLONE CUTOVER REHEARSAL BLOCKED: " + type(exc).__name__, file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
