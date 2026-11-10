# DP-M2D.3: Feed provenance through quality and Council context

## Objective

Carry one explicit MT5 feed identity from database reads through the quality
gate, immutable market-state snapshot, and immutable agent context. Reject any
context that combines feed identities.

## Implemented

- `DataQualityEngine` validates the optional `feed_id`, scopes latest tick,
  latest candle, recent-bar, and missing-bar tick queries to that feed, and
  includes the selected ID in its report and quality-event receipts.
- `data_quality_events.feed_id` is nullable and references the feed registry.
  The local-only idempotent schema migration adds the column, foreign key, and
  index without rewriting existing quality events.
- `MarketStateEngine` passes one selected feed to its quality gate and both
  repositories. Its frozen `MarketStateSnapshot` records that feed ID.
- `AgentContextFactory` requires its selection to match the snapshot, scopes
  both repositories, and includes `feed_id` in the immutable context.
- `AgentContext` rejects invalid IDs and any snapshot, candle, or tick whose
  feed ID differs from the context. Legacy contexts remain explicitly
  `feed_id=None` and only accept legacy rows.
- Feed-labelled writers remain blocked. This milestone grants no acquisition,
  source-admission, Council-cycle, promotion, or trading authorization.

## Verification and operational status

- Focused feed, migration, storage, repository, factory, Council and context
  tests: **191 passed**, with 15 subtests and 4 existing pytest collection
  warnings.
- `git diff --check` passed.
- The expanded Stage 8 point-in-time test file still fails because the
  checkout's tracked HFM policy bytes do not match the pinned raw-byte SHA-256
  (the known Linux line-ending mismatch). The tracked policy was not changed.
- PostgreSQL was stopped during this session (`localhost:5432` returned no
  response). The additive migration code is covered offline, but the new
  `data_quality_events.feed_id` column and index have not yet been applied to
  `eurusd_oracle_local`.
- `eurusd_oracle_archive` was not accessed or modified. No MT5 terminal or
  live data source was used. `FINAL=HOLD`; independent gates remain 0/11 and
  source-family admissions remain 0/13.

## Next

1. Start local PostgreSQL and apply the guarded, idempotent migration to
   `eurusd_oracle_local`; verify its postflight without touching the archive.
2. Bind the quality gate's broker-clock source to the selected feed/profile;
   the existing MT5 clock provider is not yet profile-bound by this milestone.
3. Add a feed-aware capture registry workflow and a separately reviewed
   writer/uniqueness cutover plan. Keep writes blocked until both are proven.
4. Run the relevant Windows MT5 and Stage 7/11 acceptance checks.

