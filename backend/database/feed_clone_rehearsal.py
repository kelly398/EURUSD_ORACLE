"""Rollback-only rehearsal of legacy writers on the disposable archive clone.

The production storage classes call ``Session.commit`` internally. This
runner binds them to savepoint-joined sessions inside one outer transaction,
so their commits cannot persist. It uses the clone's real indexes and tables,
but synthetic rows with explicit negative IDs avoid advancing its sequences.
Feed-labelled writes are probed for refusal and never reach a database session.
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


TEST_SYMBOL = "ORACLE_DP_M2D8_REHEARSAL"
TICK_ID = -202611090801
CANDLE_ID = -202611090802
TICK_TIME = datetime(2026, 11, 9, 12, 0, tzinfo=timezone.utc)
CANDLE_TIME = datetime(2026, 11, 9, 12, 5, tzinfo=timezone.utc)
SYNTHETIC_FEED_ID = "mt5_eurusd_v1_" + "f" * 64


def _tick_row():
    from backend.data_engine.tick_identity import create_tick_fingerprint

    source_time_msc = int(TICK_TIME.timestamp() * 1000)
    bid, ask = 1.10001, 1.10021
    last, volume, flags, volume_real = 0.0, 1.0, 6, 0.0
    return {
        "id": TICK_ID,
        "symbol": TEST_SYMBOL,
        "timestamp": TICK_TIME,
        "source_time_msc": source_time_msc,
        "bid": bid,
        "ask": ask,
        "last": last,
        "volume": volume,
        "flags": flags,
        "volume_real": volume_real,
        "spread": ask - bid,
        "spread_pips": (ask - bid) * 10_000,
        "fingerprint": create_tick_fingerprint(
            symbol=TEST_SYMBOL,
            time_msc=source_time_msc,
            bid=bid,
            ask=ask,
            last=last,
            volume=volume,
            flags=flags,
            volume_real=volume_real,
        ),
    }


def _candle_row():
    return {
        "id": CANDLE_ID,
        "symbol": TEST_SYMBOL,
        "timeframe": "M5",
        "timestamp": CANDLE_TIME,
        "open": 1.1,
        "high": 1.1005,
        "low": 1.0998,
        "close": 1.1002,
        "tick_volume": 17,
        "spread_points": 2,
        "real_volume": 0.0,
        "is_complete": True,
        "source": "MT5",
    }


def _wal_lsn(connection):
    return connection.execute(text("SELECT pg_current_wal_insert_lsn()::text")).scalar_one()


def _wal_bytes(connection, start_lsn, end_lsn):
    return int(connection.execute(
        text("SELECT pg_wal_lsn_diff(:end_lsn, :start_lsn)::bigint"),
        {"start_lsn": start_lsn, "end_lsn": end_lsn},
    ).scalar_one())


def _check_synthetic_ids_available(connection):
    for table, row_id in (("ticks", TICK_ID), ("broker_candles", CANDLE_ID)):
        existing = connection.execute(
            text(f"SELECT EXISTS (SELECT 1 FROM public.{table} WHERE id = :id)"),
            {"id": row_id},
        ).scalar_one()
        if existing:
            raise FeedCloneMigrationError("Synthetic rehearsal ID is already present.")
    for table in ("ticks", "broker_candles"):
        existing = connection.execute(
            text(f"SELECT EXISTS (SELECT 1 FROM public.{table} WHERE symbol = :symbol)"),
            {"symbol": TEST_SYMBOL},
        ).scalar_one()
        if existing:
            raise FeedCloneMigrationError("Synthetic rehearsal symbol is already present.")


def _probe_feed_write_blocks(tick_storage, candle_storage):
    class SessionMustNotOpen:
        def __call__(self, *args, **kwargs):
            raise AssertionError("Feed-labelled refusal opened a database session.")

    original_tick_factory = tick_storage.SessionLocal
    original_candle_factory = candle_storage.SessionLocal
    tick_storage.SessionLocal = SessionMustNotOpen()
    candle_storage.SessionLocal = SessionMustNotOpen()
    try:
        for storage, rows in (
            (tick_storage.BatchTickStorage(), [{"feed_id": SYNTHETIC_FEED_ID}]),
            (candle_storage.BrokerCandleStorage(), [{"feed_id": SYNTHETIC_FEED_ID}]),
        ):
            try:
                storage.save_batch(rows)
            except RuntimeError as exc:
                if "writes are blocked" not in str(exc):
                    raise
            else:
                raise FeedCloneMigrationError("Feed-labelled writer did not fail closed.")
    finally:
        tick_storage.SessionLocal = original_tick_factory
        candle_storage.SessionLocal = original_candle_factory


def _rehearse_inside_transaction(connection):
    import backend.candle_engine.broker_candle_storage as candle_storage
    import backend.data_engine.batch_tick_storage as tick_storage

    _check_synthetic_ids_available(connection)
    _probe_feed_write_blocks(tick_storage, candle_storage)

    original_tick_factory = tick_storage.SessionLocal
    original_candle_factory = candle_storage.SessionLocal
    savepoint_sessions = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        join_transaction_mode="create_savepoint",
    )
    tick_storage.SessionLocal = savepoint_sessions
    candle_storage.SessionLocal = savepoint_sessions
    try:
        tick_row = _tick_row()
        candle_row = _candle_row()
        start_lsn = _wal_lsn(connection)

        tick_writer = tick_storage.BatchTickStorage()
        tick_started = perf_counter()
        ticks_first = tick_writer.save_batch([tick_row])
        tick_first_ms = round((perf_counter() - tick_started) * 1000, 3)
        tick_retry_started = perf_counter()
        ticks_retry = tick_writer.save_batch([tick_row])
        tick_retry_ms = round((perf_counter() - tick_retry_started) * 1000, 3)
        cursor_during = tick_writer.get_latest_time_msc(TEST_SYMBOL)

        candle_writer = candle_storage.BrokerCandleStorage()
        candle_started = perf_counter()
        candles_first = candle_writer.save_batch([candle_row])
        candle_first_ms = round((perf_counter() - candle_started) * 1000, 3)
        candle_retry_started = perf_counter()
        candles_retry = candle_writer.save_batch([candle_row])
        candle_retry_ms = round((perf_counter() - candle_retry_started) * 1000, 3)
        end_lsn = _wal_lsn(connection)

        counts_during = {
            "ticks": connection.execute(text(
                "SELECT count(*) FROM public.ticks WHERE id = :id"
            ), {"id": TICK_ID}).scalar_one(),
            "broker_candles": connection.execute(text(
                "SELECT count(*) FROM public.broker_candles WHERE id = :id"
            ), {"id": CANDLE_ID}).scalar_one(),
        }
        locks = connection.execute(text("""
            SELECT c.relname, l.mode, l.granted
            FROM pg_locks AS l
            JOIN pg_class AS c ON c.oid = l.relation
            JOIN pg_namespace AS n ON n.oid = c.relnamespace
            WHERE l.pid = pg_backend_pid()
              AND n.nspname = 'public'
              AND c.relname IN ('ticks', 'broker_candles')
            ORDER BY c.relname, l.mode
        """)).mappings().all()

        if not connection.in_transaction():
            raise FeedCloneMigrationError("Writer commit escaped the outer transaction.")
        if (ticks_first, ticks_retry, candles_first, candles_retry) != (1, 0, 1, 0):
            raise FeedCloneMigrationError("Writer first-attempt/retry idempotence mismatch.")
        if cursor_during != int(TICK_TIME.timestamp() * 1000):
            raise FeedCloneMigrationError("Tick cursor did not advance to the synthetic row.")
        if counts_during != {"ticks": 1, "broker_candles": 1}:
            raise FeedCloneMigrationError("Synthetic rows were not visible inside the transaction.")
        if not locks or any(not item["granted"] for item in locks):
            raise FeedCloneMigrationError("Expected relation locks were not held/granted.")

        return {
            "tick_first_inserted": ticks_first,
            "tick_retry_inserted": ticks_retry,
            "candle_first_inserted": candles_first,
            "candle_retry_inserted": candles_retry,
            "tick_cursor_during_transaction_msc": cursor_during,
            "legacy_query_cursor_is_durable": False,
            "writer_elapsed_ms": {
                "tick_first_insert": tick_first_ms,
                "tick_duplicate_retry": tick_retry_ms,
                "candle_first_insert": candle_first_ms,
                "candle_duplicate_retry": candle_retry_ms,
            },
            "wal_bytes_cluster_observed_during_transaction": _wal_bytes(
                connection, start_lsn, end_lsn
            ),
            "locks_held_before_rollback": [dict(item) for item in locks],
            "outer_transaction_still_open_after_writer_commits": True,
        }
    finally:
        tick_storage.SessionLocal = original_tick_factory
        candle_storage.SessionLocal = original_candle_factory


def rehearse_disposable_clone(raw_url, *, confirm_database_name):
    if confirm_database_name != CLONE_DATABASE_NAME:
        raise FeedCloneMigrationError("Exact clone-name confirmation is required.")
    url = validate_clone_database_url(raw_url)
    engine = create_engine(
        url,
        connect_args={
            "options": (
                "-c lock_timeout=2000 "
                "-c statement_timeout=30000 "
                "-c application_name=ORACLE_DP_M2D8_CLONE_REHEARSAL"
            )
        },
    )
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                counts_before, free_bytes = _preflight(connection)
                result = _rehearse_inside_transaction(connection)
            finally:
                transaction.rollback()

        with engine.connect() as connection:
            counts_after = _row_counts(connection)
            leftovers = {
                "ticks": connection.execute(text(
                    "SELECT count(*) FROM public.ticks WHERE id = :id"
                ), {"id": TICK_ID}).scalar_one(),
                "broker_candles": connection.execute(text(
                    "SELECT count(*) FROM public.broker_candles WHERE id = :id"
                ), {"id": CANDLE_ID}).scalar_one(),
            }
            database_bytes = connection.execute(
                text("SELECT pg_database_size(current_database())")
            ).scalar_one()
        if counts_after != counts_before or counts_after != EXPECTED_ROW_COUNTS:
            raise FeedCloneMigrationError("Rollback changed clone baseline row counts.")
        if leftovers != {"ticks": 0, "broker_candles": 0}:
            raise FeedCloneMigrationError("Rollback left synthetic test rows behind.")

        return {
            "milestone": "DP-M2D.8_CLONE_WRITER_REHEARSAL",
            "mode": "DISPOSABLE_CLONE_OUTER_TRANSACTION_ROLLBACK",
            "database": CLONE_DATABASE_NAME,
            "row_counts_before": counts_before,
            "row_counts_after_rollback": counts_after,
            "synthetic_rows_after_rollback": leftovers,
            "free_bytes_before": free_bytes,
            "database_bytes_after": database_bytes,
            "feed_labelled_writer_refusals_verified": True,
            "persistent_clone_rows_written": 0,
            "sequence_values_advanced": False,
            "rollback_verified": True,
            "checkpoint_scope": "query_cursor_only; durable recovery cursor is ForwardFeedStore",
            "archive_database_connected": False,
            "archive_database_modified": False,
            "feed_writes_authorized": False,
            "final_action": "HOLD",
            **result,
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
        report = rehearse_disposable_clone(
            raw_url,
            confirm_database_name=args.confirm_database_name,
        )
    except Exception as exc:
        print("CLONE WRITER REHEARSAL BLOCKED: " + type(exc).__name__, file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
