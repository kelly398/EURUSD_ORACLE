# DP-M2D.7: Feed-aware candle-key cutover sequence

## Status

Design only. No production/archive schema or writer cutover has been
authorized or executed. `FINAL=HOLD`.

## Current constraints

- `eurusd_oracle_archive` remains on its original schema with 30,744,086
  ticks and 75,528 broker candles. Its rows retain unknown feed provenance.
- The local development database has additive nullable feed columns and
  indexes. Feed-labelled tick and candle writes still fail before opening a
  database session.
- `uq_broker_candle` is still present. The rollback-only lab verified that the
  old writer's `ON CONFLICT ON CONSTRAINT uq_broker_candle` fails after that
  constraint is removed.
- `uq_tick_fingerprint` remains global. New tick fingerprints include the
  feed ID, so cross-feed quote events receive different keys; historic
  fingerprint bytes are not rewritten.
- Restoring the old global candle constraint after feed-labelled rows overlap
  by timestamp may be impossible. That point is a rollback boundary.

## Required gates before any cutover

1. **Target and backup:** identify the exact target explicitly; produce a
   checksum-verified backup and complete a restore drill on an isolated clone.
   Confirm recovery time, disk headroom, WAL headroom, and row counts.
2. **Writer inventory:** identify every tick, candle, backfill, repair, and
   maintenance writer. Demonstrate that all candle inserts use untargeted
   conflict handling and that no path names `uq_broker_candle`.
3. **Reader readiness:** confirm all latest-row cursors, quality checks,
   market-state builders, and Council contexts use the same explicit feed or
   legacy-null scope. Verify mixed-feed contexts fail closed.
4. **Index readiness:** on the clone, build and validate the feed-specific
   unique candle index and legacy-null partial unique index. Inspect
   `pg_index.indisready` and `indisvalid`; collect build duration and space.
5. **Writer readiness:** exercise actual writer code on clone-only rows for
   legacy, feed A, and feed B; prove retries, duplicate suppression, and
   distinct same-time broker candles. Current production writers remain
   blocked until this gate passes.
6. **Platform acceptance:** run Windows MT5 regression with a verified
   profile/epoch and complete the Stage 7/11 acceptance suite. A Linux
   synthetic rehearsal cannot substitute for these checks.
7. **Independent review:** source admission and operational change approval
   require evidence outside the local feed registry. A registry row or local
   profile is not an approval.

## Proposed order after all gates pass

1. Freeze the exact application and migration revisions; capture database
   identity, schema catalog, row counts, index sizes, and active writer list.
2. Pause all candle writers and confirm no active transactions can insert.
3. On the target, create the feed and legacy partial unique indexes
   concurrently, outside a transaction. Validate definitions and planner
   usability. Do not remove `uq_broker_candle` during this step.
4. Deploy the compatible writer release that uses untargeted
   `ON CONFLICT DO NOTHING`; keep its feed-labelled row guard enabled.
5. Verify every writer process is on the compatible release, remains healthy,
   uses no named legacy constraint, and still rejects feed-labelled batches.
6. With a bounded lock timeout, acquire the candle table lock and recheck both
   replacement indexes are unique, ready, and valid. Recheck feed-labelled
   rows are absent. If any check fails, abort and retain the old constraint.
7. Drop `uq_broker_candle` in the short transaction. Verify the legacy-null
   unique index, feed-specific unique index, and all reader contracts. Resume
   legacy-only writes and observe before considering any feed-labelled write.
8. Keep feed-labelled writes disabled until a separate explicit admission and
   write authorization exists. Removing the old constraint alone grants no
   such authorization.

## Rollback boundary

- Before the old constraint is removed: aborting the transaction leaves the
  existing uniqueness rule intact; invalid concurrent indexes can be dropped
  and rebuilt under a separate reviewed operation.
- After the old constraint is removed but before feed-labelled writes: pause
  writers, verify the legacy partial unique index, and retain the replacement
  indexes. Restoring the old constraint is possible only after proving no
  duplicate `(symbol, timeframe, timestamp, source)` values exist.
- After overlapping feed-labelled rows are written: do not promise rollback
  to the old global constraint. The recovery path must preserve rows, isolate
  feeds, and use the backup/restore drill or a separately designed forward
  repair. Never delete or relabel historical rows to force the old constraint.

## Rehearsal evidence

The disposable PostgreSQL lab uses temporary tables and always rolls back.
It verified legacy/feed tick and candle separation, duplicate suppression,
and old-writer breakage. It does not measure archive-scale locks, index build
time, WAL, disk requirements, or recovery. The next milestone must establish
those measurements on an isolated archive clone; never rehearse against
`eurusd_oracle_archive` itself.

