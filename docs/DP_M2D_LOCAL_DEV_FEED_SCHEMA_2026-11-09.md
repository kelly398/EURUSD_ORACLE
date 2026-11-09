# DP-M2D.1: Local development feed schema

## Objective

Advance the feed identity work from an offline DDL proposal to an ORM schema
and a narrowly guarded bootstrap migration for the empty Linux development
database. This milestone does not migrate the restored historical archive,
enable feed-labelled writes, or authorize a collector.

## Schema added

- `oracle_mt5_feed_registry` with a deterministic feed ID, profile ID, capture
  epoch, identity digest, creation time, and PostgreSQL identity check.
- Nullable `feed_id VARCHAR(78)` columns on `ticks` and `broker_candles`, each
  referencing the registry.
- A feed/time cursor index for ticks; feed/time and partial uniqueness indexes
  for broker candles.
- The existing `uq_tick_fingerprint` and `uq_broker_candle` constraints remain
  in place. Legacy records are not updated or assigned a feed ID.

## Migration safeguards

`backend/database/feed_schema_migration.py` is idempotent and accepts only
`eurusd_oracle_local` on a local PostgreSQL host. It requires an explicit
database-name confirmation, a non-superuser session, the expected legacy
constraints, and empty `ticks` and `broker_candles` tables. It refuses remote
hosts, the archive/lab databases, populated tables, and schema mismatches.
It uses bounded lock and statement timeouts and reports no connection details.

Run from the project root only for the local development database:

```bash
oracle_env/bin/python -m backend.database.feed_schema_migration \
  --apply-local-dev \
  --confirm-database-name eurusd_oracle_local
```

## Local outcome

- Applied twice to `eurusd_oracle_local`; both runs succeeded, confirming
  idempotency. Reports confirm zero legacy rows modified and zero
  feed-labelled rows written.
- The restored `eurusd_oracle_archive` remains separate and unchanged. It
  contains 30,744,086 ticks and 75,528 broker candles. The migration refuses
  to connect to that database.
- `eurusd_oracle_dp_m2b_lab` remains empty after the rollback-only rehearsal.
- Focused DP-M2 regression: 150 passed on Linux.
- Working tree remains uncommitted pending the required Windows regression.

## Remaining work

1. Add explicit feed-scoped repository queries and cursors while preserving
   legacy `feed_id IS NULL` behavior.
2. Test mixed-feed isolation, deduplication, and resumption against a
   disposable clone of the archive; never use the archive database itself.
3. Coordinate writers, readers, and partial indexes before any candle
   uniqueness cutover. Keep all feed-labelled writes blocked until then.
4. Run Windows compilation and Stage 7/11 regression before committing.

Independent evidence gates remain 0/11, source-family admissions 0/13, and
`FINAL=HOLD`.
