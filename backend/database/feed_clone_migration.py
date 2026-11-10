"""Apply additive feed columns to the disposable DP-M2D.8 archive clone only.

This is never a production/archive migration. It requires a separate clone URL,
an exact clone-name confirmation, known source row counts, a restricted role,
and no active sessions on the archive. No rows are relabelled or rewritten.
"""

import argparse
import json
import os
import shutil
import sys

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from backend.database.feed_schema_migration import (
    EXPECTED_UNIQUES,
    LOCAL_HOSTS,
    MIGRATION_SQL,
    _postflight,
    _unique_constraints,
)


CLONE_DATABASE_NAME = "eurusd_oracle_dp_m2d8_clone"
ARCHIVE_DATABASE_NAME = "eurusd_oracle_archive"
CLONE_URL_ENV = "ORACLE_DP_M2D8_CLONE_DATABASE_URL"
EXPECTED_ROW_COUNTS = {
    "ticks": 30_744_086,
    "broker_candles": 75_528,
    "candles": 2,
}


class FeedCloneMigrationError(RuntimeError):
    """The exact disposable clone did not meet its migration contract."""


def validate_clone_database_url(raw_url, primary_url=None):
    if not isinstance(raw_url, str) or not raw_url.strip():
        raise FeedCloneMigrationError(
            "A separate disposable-clone DATABASE_URL is required."
        )
    try:
        url = make_url(raw_url)
    except Exception as exc:
        raise FeedCloneMigrationError("Clone DATABASE_URL is invalid.") from exc
    if url.drivername != "postgresql+psycopg2":
        raise FeedCloneMigrationError("Clone migration requires psycopg2.")
    if url.database != CLONE_DATABASE_NAME:
        raise FeedCloneMigrationError("Refusing any database except the named clone.")
    if url.host not in LOCAL_HOSTS or url.port not in (None, 5432):
        raise FeedCloneMigrationError("Refusing a remote or nonstandard target.")
    if url.username not in (None, "Komarchy") or url.query:
        raise FeedCloneMigrationError("Refusing clone URL overrides or admin roles.")
    if primary_url:
        try:
            primary = make_url(primary_url)
        except Exception as exc:
            raise FeedCloneMigrationError("Primary URL cannot be validated.") from exc
        if primary.database in (CLONE_DATABASE_NAME, ARCHIVE_DATABASE_NAME):
            raise FeedCloneMigrationError("Primary application URL is misconfigured.")
        if primary == url:
            raise FeedCloneMigrationError("Clone URL overlaps the primary URL.")
    return url


def _row_counts(connection):
    return {
        table: connection.execute(
            text(f"SELECT count(*) FROM public.{table}")
        ).scalar_one()
        for table in EXPECTED_ROW_COUNTS
    }


def _preflight(connection):
    database = connection.execute(
        text("SELECT current_database()")
    ).scalar_one()
    if database != CLONE_DATABASE_NAME:
        raise FeedCloneMigrationError("Connected database is not the named clone.")

    role_name, role_is_superuser = connection.execute(
        text("SELECT current_user, rolsuper FROM pg_roles WHERE rolname = current_user")
    ).one()
    if role_name != "Komarchy" or role_is_superuser:
        raise FeedCloneMigrationError("Clone migration requires the restricted Komarchy role.")

    database_bytes = connection.execute(
        text("SELECT pg_database_size(current_database())")
    ).scalar_one()
    free_bytes = shutil.disk_usage("/").free
    if free_bytes < 2 * database_bytes:
        raise FeedCloneMigrationError("Insufficient disk space for clone indexes and rollback margin.")

    archive_sessions = connection.execute(
        text("SELECT count(*) FROM pg_stat_activity WHERE datname = :name"),
        {"name": ARCHIVE_DATABASE_NAME},
    ).scalar_one()
    if archive_sessions:
        raise FeedCloneMigrationError("Archive has active sessions; refusing clone migration.")

    inspector = inspect(connection)
    tables = set(inspector.get_table_names(schema="public"))
    if not set(EXPECTED_ROW_COUNTS).issubset(tables) or "data_quality_events" not in tables:
        raise FeedCloneMigrationError("Clone is missing canonical source tables.")

    for table, expected in EXPECTED_UNIQUES.items():
        if _unique_constraints(connection, table) != expected:
            raise FeedCloneMigrationError(
                "Legacy unique contract mismatch in clone: " + table
            )

    counts = _row_counts(connection)
    if counts != EXPECTED_ROW_COUNTS:
        raise FeedCloneMigrationError("Clone row counts differ from the verified archive baseline.")

    for table in ("ticks", "broker_candles", "data_quality_events"):
        if "feed_id" in {col["name"] for col in inspector.get_columns(table, schema="public")}:
            has_feed_rows = connection.execute(
                text(f"SELECT EXISTS (SELECT 1 FROM public.{table} WHERE feed_id IS NOT NULL LIMIT 1)")
            ).scalar_one()
            if has_feed_rows:
                raise FeedCloneMigrationError(
                    "Clone bootstrap refuses pre-existing feed-labelled rows: " + table
                )
    return counts, free_bytes


def migrate_disposable_clone(raw_url, *, confirm_database_name):
    if confirm_database_name != CLONE_DATABASE_NAME:
        raise FeedCloneMigrationError("Exact clone-name confirmation is required.")
    url = validate_clone_database_url(raw_url)
    engine = create_engine(
        url,
        connect_args={
            "options": (
                "-c lock_timeout=2000 "
                "-c statement_timeout=180000 "
                "-c application_name=ORACLE_DP_M2D8_CLONE_MIGRATION"
            )
        },
    )
    try:
        with engine.begin() as connection:
            counts_before, free_bytes_before = _preflight(connection)
            for statement in MIGRATION_SQL:
                connection.execute(text(statement))
            _postflight(connection)
            counts_after = _row_counts(connection)
            if counts_after != counts_before:
                raise FeedCloneMigrationError("Schema migration changed clone row counts.")
    finally:
        engine.dispose()

    return {
        "milestone": "DP-M2D.8",
        "mode": "DISPOSABLE_ARCHIVE_CLONE_ADDITIVE_SCHEMA",
        "database": CLONE_DATABASE_NAME,
        "row_counts_before": counts_before,
        "row_counts_after": counts_after,
        "free_bytes_before_migration": free_bytes_before,
        "legacy_rows_modified": 0,
        "feed_labelled_rows_written": 0,
        "legacy_unique_constraints_preserved": True,
        "archive_database_connected": False,
        "archive_database_modified": False,
        "feed_writes_authorized": False,
        "final_action": "HOLD",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--offline-only", action="store_true")
    modes.add_argument("--apply-disposable-clone", action="store_true")
    parser.add_argument("--confirm-database-name", default="")
    args = parser.parse_args(argv)

    if args.offline_only:
        print(json.dumps({
            "mode": "OFFLINE_NO_CONNECTION",
            "target_database": CLONE_DATABASE_NAME,
            "archive_database_connected": False,
            "database_changes": False,
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
    primary_url = os.environ.get("DATABASE_URL")
    try:
        validate_clone_database_url(raw_url, primary_url)
        report = migrate_disposable_clone(
            raw_url,
            confirm_database_name=args.confirm_database_name,
        )
    except Exception as exc:
        print("CLONE MIGRATION BLOCKED: " + type(exc).__name__, file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
