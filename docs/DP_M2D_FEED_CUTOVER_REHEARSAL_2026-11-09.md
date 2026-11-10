# DP-M2D.6: Feed writer and candle-key cutover rehearsal

## Objective

Reconfirm the writer and uniqueness boundary using only temporary PostgreSQL
tables in the named disposable lab database. Production and archive schemas
must remain untouched.

## Implemented and rehearsed

- Fixed `feed_migration_rehearsal.run_lab()` to load project `.env` before
  reading its dedicated lab URL. The first CLI attempt had no loaded lab URL
  and safely refused to connect; the loader fix is covered by a unit test.
- Ran:
  `python -m backend.database.feed_migration_rehearsal --run-disposable-lab --confirm-lab-name eurusd_oracle_dp_m2b_lab`.
- The lab verified a non-superuser writable connection to the exact lab DB,
  created only temporary tables, and exercised legacy and feed-specific
  candle/tick uniqueness. Its report confirmed the old named-constraint
  candle writer breaks after removal of `uq_broker_candle`; untargeted conflict
  handling supports the replacement indexes.
- Result: one legacy candle and tick row; two distinct feed candle and tick
  rows; duplicates were suppressed. The runner rolled back all temporary DDL
  and rows in `finally`.
- It reported `production_database_connected=false`,
  `production_database_modified=false`,
  `live_schema_migration_approved=false`, and `final_action=HOLD`.

## Verification and limits

- Focused feed, registry, migration, storage, repository, profile-clock,
  factory, Council, and context suite: **202 passed**, 15 subtests passed,
  with 4 existing pytest collection warnings.
- `git diff --check` passed.
- The rehearsal is synthetic and rollback-only. It does not measure production
  lock duration, table size, index build capacity, live writer deployment
  ordering, source admission, or broker-clock evidence.
- `eurusd_oracle_archive` was not connected to or modified.

## Next

1. Design an operational deployment and rollback sequence for writer
   replacement and candle-key cutover, with lock, capacity, and backup gates.
2. Keep both feed-labelled writers blocked until that sequence is reviewed and
   Windows regression passes.
3. Do not cut over or alter the archive as part of this rehearsal.

