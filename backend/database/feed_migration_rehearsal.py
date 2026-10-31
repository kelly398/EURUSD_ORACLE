
"""DP-M2B.3: synthetic, rollback-only uniqueness rehearsal in a disposable DB.

Never uses the primary DATABASE_URL for a connection. It cannot perform the
proposed migration on the real ticks or broker_candles tables. PostgreSQL DDL
is executed only against TEMP tables inside a transaction that is rolled back.

This tests the constraint cutover, not production lock/index sizing or writers.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import sys

from sqlalchemy.engine import make_url


LAB_NAME = "eurusd_oracle_dp_m2b_lab"
LAB_URL_ENV = "ORACLE_DP_M2B_LAB_DATABASE_URL"
TEST_TIME = datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc)
FEED_A = "mt5_eurusd_v1_" + "a" * 64  # Synthetic; not an admitted feed
FEED_B = "mt5_eurusd_v1_" + "b" * 64

CREATE_TEMP_TABLES = (
    """CREATE TEMP TABLE dp_m2b_ticks (
        id BIGSERIAL PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        "timestamp" TIMESTAMPTZ NOT NULL,
        source_time_msc BIGINT NOT NULL,
        bid DOUBLE PRECISION NOT NULL,
        ask DOUBLE PRECISION NOT NULL,
        last DOUBLE PRECISION NOT NULL DEFAULT 0,
        volume DOUBLE PRECISION NOT NULL DEFAULT 0,
        flags INTEGER NOT NULL DEFAULT 0,
        volume_real DOUBLE PRECISION NOT NULL DEFAULT 0,
        spread DOUBLE PRECISION NOT NULL,
        spread_pips DOUBLE PRECISION NOT NULL,
        fingerprint VARCHAR(64) NOT NULL,
        CONSTRAINT uq_tick_fingerprint UNIQUE (fingerprint)
    ) ON COMMIT DROP""",
    """CREATE TEMP TABLE dp_m2b_broker_candles (
        id BIGSERIAL PRIMARY KEY,
        symbol VARCHAR NOT NULL,
        timeframe VARCHAR NOT NULL,
        "timestamp" TIMESTAMPTZ NOT NULL,
        open DOUBLE PRECISION NOT NULL,
        high DOUBLE PRECISION NOT NULL,
        low DOUBLE PRECISION NOT NULL,
        close DOUBLE PRECISION NOT NULL,
        tick_volume BIGINT NOT NULL,
        spread_points INTEGER NOT NULL,
        real_volume DOUBLE PRECISION NOT NULL,
        is_complete BOOLEAN NOT NULL,
        source VARCHAR NOT NULL,
        CONSTRAINT uq_broker_candle UNIQUE (
            symbol, timeframe, "timestamp", source
        )
    ) ON COMMIT DROP""",
    """CREATE TEMP TABLE dp_m2b_feed_registry (
        feed_id VARCHAR(78) PRIMARY KEY,
        profile_id VARCHAR(64) NOT NULL,
        capture_epoch VARCHAR(64) NOT NULL,
        identity_sha256 CHAR(64) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_dp_m2b_feed_identity CHECK (
            feed_id = 'mt5_eurusd_v1_' || identity_sha256
            AND identity_sha256 ~ '^[0-9a-f]{64}$'
        )
    ) ON COMMIT DROP""",
)

ADD_COLUMNS_AND_INDEXES = (
    "ALTER TABLE dp_m2b_ticks ADD COLUMN feed_id VARCHAR(78) NULL",
    "ALTER TABLE dp_m2b_broker_candles ADD COLUMN feed_id VARCHAR(78) NULL",
    """CREATE INDEX dp_m2b_tick_feed_time_v2 ON dp_m2b_ticks
        (feed_id, "timestamp", source_time_msc)
        WHERE feed_id IS NOT NULL""",
    """CREATE UNIQUE INDEX dp_m2b_feed_candle_v2 ON dp_m2b_broker_candles
        (feed_id, symbol, timeframe, "timestamp")
        WHERE feed_id IS NOT NULL""",
    """CREATE UNIQUE INDEX dp_m2b_legacy_candle_v2 ON dp_m2b_broker_candles
        (symbol, timeframe, "timestamp", source)
        WHERE feed_id IS NULL""",
)

CANDLE_INSERT = """INSERT INTO dp_m2b_broker_candles
    (symbol, timeframe, "timestamp", open, high, low, close,
     tick_volume, spread_points, real_volume, is_complete, source, feed_id)
    VALUES ('EURUSD', 'M5', %s, %s, %s, %s, %s, 10, 5, 0, TRUE, 'MT5', %s)
    {on_conflict} RETURNING id"""

TICK_INSERT = """INSERT INTO dp_m2b_ticks
    (symbol, "timestamp", source_time_msc, bid, ask, last,
     volume, flags, volume_real, spread, spread_pips, fingerprint, feed_id)
    VALUES ('EURUSD', %s, %s, 1.1, 1.1002, 0,
            1, 6, 0, 0.0002, 2, %s, %s)
    ON CONFLICT DO NOTHING RETURNING id"""


def validate_lab_url(raw, primary_raw=None):
    """Validate locally without opening any network connection."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("A separate disposable PostgreSQL lab URL is required.")
    try:
        lab = make_url(raw)
    except Exception as exc:
        raise ValueError("Invalid lab database URL.") from exc
    if lab.drivername not in ("postgresql", "postgresql+psycopg2"):
        raise ValueError("Lab connection must use psycopg2/PostgreSQL.")
    if lab.database != LAB_NAME:
        raise ValueError("Lab must use exactly the disposable database: " + LAB_NAME)
    if primary_raw:
        try:
            primary = make_url(primary_raw)
        except Exception as exc:
            raise ValueError("Primary DATABASE_URL cannot be validated.") from exc
        if primary.database == LAB_NAME or primary == lab:
            raise ValueError("Refusing a lab URL which overlaps the primary DB.")
    return lab


def _insert_candle(cursor, *, feed_id, price, legacy_writer=False):
    clause = (
        "ON CONFLICT ON CONSTRAINT uq_broker_candle DO NOTHING"
        if legacy_writer else "ON CONFLICT DO NOTHING"
    )
    cursor.execute(
        CANDLE_INSERT.format(on_conflict=clause),
        (TEST_TIME, price, price + 0.001, price - 0.001, price + 0.0002, feed_id),
    )
    return cursor.fetchone() is not None


def _insert_tick(cursor, *, feed_id, fingerprint):
    cursor.execute(
        TICK_INSERT,
        (TEST_TIME, int(TEST_TIME.timestamp() * 1000) + 112, fingerprint, feed_id),
    )
    return cursor.fetchone() is not None


def rehearse(cursor):
    """Run only on a previously confirmed disposable lab transaction."""
    from backend.data_engine.tick_identity import (
        create_tick_fingerprint,
        create_tick_fingerprint_v2,
    )

    for statement in CREATE_TEMP_TABLES:
        cursor.execute(statement)
    for statement in ADD_COLUMNS_AND_INDEXES:
        cursor.execute(statement)

    for feed, suffix in ((FEED_A, "a"), (FEED_B, "b")):
        cursor.execute(
            """INSERT INTO dp_m2b_feed_registry
                 (feed_id, profile_id, capture_epoch, identity_sha256)
                 VALUES (%s, 'SYNTHETIC_TEST', 'LAB_ONLY_20261102', %s)""",
            (feed, suffix * 64),
        )

    if not _insert_candle(cursor, feed_id=None, price=1.1, legacy_writer=True):
        raise RuntimeError("Legacy candle insert failed.")
    if _insert_candle(cursor, feed_id=FEED_A, price=1.2, legacy_writer=True):
        raise RuntimeError("Old uniqueness unexpectedly permits broker overlap.")

    # On a TEMP table only: model the coordinated replacement of the old key.
    cursor.execute(
        "ALTER TABLE dp_m2b_broker_candles DROP CONSTRAINT uq_broker_candle"
    )
    cursor.execute("SAVEPOINT dp_m2b_old_writer")
    try:
        _insert_candle(cursor, feed_id=FEED_A, price=1.2, legacy_writer=True)
    except Exception as exc:
        # PostgreSQL: undefined_object (missing ON CONSTRAINT name).
        if getattr(exc, "pgcode", None) != "42704":
            raise
        old_writer_broken = True
        cursor.execute("ROLLBACK TO SAVEPOINT dp_m2b_old_writer")
    else:
        old_writer_broken = False
    finally:
        cursor.execute("RELEASE SAVEPOINT dp_m2b_old_writer")

    if not old_writer_broken:
        raise RuntimeError("Old candle writer unexpectedly survived constraint removal.")

    assert _insert_candle(cursor, feed_id=FEED_A, price=1.2)
    assert _insert_candle(cursor, feed_id=FEED_B, price=1.3)
    assert not _insert_candle(cursor, feed_id=FEED_A, price=1.4)
    assert not _insert_candle(cursor, feed_id=None, price=1.4)

    tick_tuple = dict(
        symbol="EURUSD", time_msc=int(TEST_TIME.timestamp() * 1000) + 112,
        bid=1.1, ask=1.1002, last=0, volume=1, flags=6, volume_real=0,
    )
    legacy_fp = create_tick_fingerprint(**tick_tuple)
    first_fp = create_tick_fingerprint_v2(feed_id=FEED_A, **tick_tuple)
    second_fp = create_tick_fingerprint_v2(feed_id=FEED_B, **tick_tuple)
    if len({legacy_fp, first_fp, second_fp}) != 3:
        raise RuntimeError("Feed-specific fingerprints are not distinct.")

    assert _insert_tick(cursor, feed_id=None, fingerprint=legacy_fp)
    assert _insert_tick(cursor, feed_id=FEED_A, fingerprint=first_fp)
    assert _insert_tick(cursor, feed_id=FEED_B, fingerprint=second_fp)
    assert not _insert_tick(cursor, feed_id=FEED_A, fingerprint=first_fp)

    cursor.execute(
        """SELECT feed_id, COUNT(*) FROM dp_m2b_broker_candles
            GROUP BY feed_id ORDER BY feed_id NULLS FIRST"""
    )
    candles = cursor.fetchall()
    cursor.execute(
        """SELECT feed_id, COUNT(*) FROM dp_m2b_ticks
            GROUP BY feed_id ORDER BY feed_id NULLS FIRST"""
    )
    ticks = cursor.fetchall()
    if [row[1] for row in candles] != [1, 1, 1]:
        raise RuntimeError("Candle fixture isolation failed.")
    if [row[1] for row in ticks] != [1, 1, 1]:
        raise RuntimeError("Tick fixture isolation failed.")

    return {
        "milestone": "DP-M2B.3",
        "mode": "DISPOSABLE_LAB_TEMP_TABLE_REHEARSAL",
        "legacy_candle_rows": 1,
        "distinct_broker_candle_rows": 2,
        "distinct_broker_tick_rows": 2,
        "legacy_tick_rows": 1,
        "old_candle_writer_must_change_at_cutover": old_writer_broken,
        "all_lab_ddl_and_rows_rolled_back": True,  # issued in finally by run_lab
        "production_database_connected": False,
        "production_database_modified": False,
        "live_schema_migration_approved": False,
        "final_action": "HOLD",
    }


def run_lab():
    raw = os.environ.get(LAB_URL_ENV)
    lab = validate_lab_url(raw, os.environ.get("DATABASE_URL"))

    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError("Install psycopg2 in the active virtual environment.") from exc

    connection = None
    try:
        connection = psycopg2.connect(
            lab.set(drivername="postgresql").render_as_string(hide_password=False),
            connect_timeout=8,
            options=(
                "-c statement_timeout=15000 "
                "-c lock_timeout=1000 "
                "-c application_name=ORACLE_DP_M2B_LAB_ONLY"
            ),
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT current_database(), current_setting('transaction_read_only'),
                          pg_is_in_recovery(),
                          (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"""
            )
            db_name, read_only, replica, superuser = cursor.fetchone()
            if db_name != LAB_NAME or read_only != "off" or replica or superuser:
                raise RuntimeError("Disposable non-superuser writable lab not verified.")
            result = rehearse(cursor)
        return result
    finally:
        if connection is not None:
            try:
                connection.rollback()  # ALWAYS: no commit operation exists.
            finally:
                connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--offline-only", action="store_true")
    modes.add_argument("--run-disposable-lab", action="store_true")
    parser.add_argument("--confirm-lab-name", default="")
    args = parser.parse_args(argv)

    if args.offline_only:
        print(json.dumps({
            "milestone": "DP-M2B.3",
            "mode": "OFFLINE_NO_CONNECTION",
            "lab_database_required": LAB_NAME,
            "lab_url_env_required_for_lab_run": LAB_URL_ENV,
            "database_changes": False,
            "final_action": "HOLD",
        }, indent=2, sort_keys=True))
        return 0

    if args.confirm_lab_name != LAB_NAME:
        parser.error("Exact --confirm-lab-name is required for a lab run.")
    try:
        report = run_lab()
    except Exception as exc:
        # Never print URLs or credential-bearing exception messages.
        print("LAB REHEARSAL BLOCKED:", type(exc).__name__, file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
