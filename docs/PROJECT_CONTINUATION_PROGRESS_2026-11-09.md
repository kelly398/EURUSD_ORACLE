# EURUSD_ORACLE continuation progress

Updated: 2026-11-09

This is the handoff note for the next development session. Read it together
with `docs/DATA_PIPELINE_MODERNIZATION_MILESTONES_2026-10-31.md`,
`docs/DP_M2D_FEED_SCOPED_READS_2026-11-09.md`, and
`docs/DP_M2D_FEED_PROVENANCE_2026-11-09.md`.

## Current position

- Branch: `feature/utc-research-m01-m08`.
- Latest implementation commit: `ba6ce30 feat(data): propagate feed
  provenance to council context`.
- Handoff report commit: `49f6db9 docs: add continuation progress handoff`.
- Previous commits: `bb9ca49 feat(database): add guarded local feed schema migration`;
  `95b742a fix(storage): block feed-labelled tick and backfill writes`.
- Current engineering milestone: DP-M2D.3 feed provenance is implemented in
  code and covered by focused tests. Its event-column migration is not yet
  applied because PostgreSQL was stopped during this session.

## Implemented

### Writer safety — commit `95b742a`

- Tick batch and historical candle backfill writers reject feed-labelled
  batches before opening a database session.
- Historical candle backfill no longer names the legacy
  `uq_broker_candle` constraint in its conflict target.
- Added regression coverage for legacy compatibility and fail-closed behavior.

### Additive local schema — commit `bb9ca49`

- Added `MT5FeedRegistry`, nullable `feed_id` columns on `ticks` and
  `broker_candles`, registry foreign keys and partial feed indexes to the ORM.
- Preserved `uq_tick_fingerprint` and `uq_broker_candle`.
- Added `backend/database/feed_schema_migration.py`. It only accepts local
  `eurusd_oracle_local`, requires explicit confirmation and a non-superuser,
  verifies the old uniqueness contracts, and refuses populated tick/candle
  tables. It has bounded timeouts and postflight checks for index definitions.
- Applied the idempotent migration twice to the empty development database.
  It did not connect to or modify the archive.

### Feed-scoped reads and cursors — commit `f57d876`

- `TickRepository` and `CandleRepository` take an optional validated feed ID.
- With no feed ID, reads select only legacy rows where `feed_id IS NULL`;
  they never combine all feeds. With a feed ID, reads filter to that feed.
- Tick and candle latest-row cursors use the same feed scope.
- `TickSample` and `CandleBar` carry `feed_id` downstream.
- Feed-labelled writes are still blocked. Reader APIs do not authorize a
  collector or new data admission.

### Feed provenance — DP-M2D.3

- `DataQualityEngine` now scopes tick and candle availability, freshness,
  recent-bar, and gap queries to one selected feed. Reports and quality-event
  receipts retain that feed ID.
- `MarketStateEngine` carries the selection into both repositories and its
  frozen `MarketStateSnapshot`.
- `AgentContextFactory` binds its selection to the snapshot and creates an
  immutable `AgentContext` that rejects mismatched snapshot, candle, or tick
  feed IDs. Legacy contexts remain scoped to `feed_id IS NULL`.
- Added a nullable `data_quality_events.feed_id`, registry foreign key, and
  index to the ORM and guarded local-only migration. Existing events stay
  null; no historic events or archive rows were rewritten.
- Details: `docs/DP_M2D_FEED_PROVENANCE_2026-11-09.md`.

## Linux workstation and databases

- PostgreSQL is installed locally on port `5432`, but was stopped during this
  session and did not answer at `localhost:5432`.
- `oracle_env` is the project-local Python 3.14 environment. The pinned Linux
  dependencies are installed; `MetaTrader5` is absent because the native
  Python package is distributed for Windows.
- The Git-ignored `.env` points the application to `eurusd_oracle_local`.
- `eurusd_oracle_local`: development database, now has the additive DP-M2D.1
  schema; `ticks` and `broker_candles` are empty.
- `eurusd_oracle_dp_m2b_lab`: disposable rehearsal database; the rollback-only
  rehearsal left zero persistent public tables.
- `eurusd_oracle_archive`: separate restore of the Windows PostgreSQL 17.11
  dump on local PostgreSQL 18.6. Verified counts: 30,744,086 ticks and 75,528
  broker candles. It remains on the original schema and was not migrated.
- Original dump remains at
  `/home/Komarchy/Downloads/ORACLE_transfer/eurusd_oracle.backup`.
  SHA-256: `7dcb5d4d1a2dc5c9ee563a79b8941dc5c79a6b5dc85c749a18533c425564a339`.
  Do not restore over the development DB or modify the archive.

## Historical artifacts

- Recovered ignored reports are in `logs/`:
  `archive_clock_candidate_audit_20261027.json` and
  `clock_epoch_final_verification.json`.
- The Windows copy of `hfm_forward_20261027.json` matched the pinned SHA-256
  `e34b7a29f78f5865f4b0fe7c4ad778e4e8b650d9943792eee019a48bdf2fe088` and is
  preserved under `/home/Komarchy/Downloads/ORACLE_transfer/`.
- Do not replace the tracked policy file. This Linux checkout has LF bytes and
  a different raw-byte hash. The recovered `hfm_probe_evidence` has the same
  parsed JSON content as the tracked copy; the tracked copy was retained.
- The files under `logs/` are Git-ignored and will not travel in commits.

## Verification and limits

- DP-M2D.3 focused feed/storage/context/Council regression, recorded in
  `ba6ce30`: **191 passed**,
  15 subtests passed, with 4 existing pytest collection warnings.
- `git diff --check` passed for the latest code commit.
- The Stage 8 point-in-time suite was also attempted; it fails at the known
  pinned HFM policy raw-byte SHA check caused by this checkout's line-ending
  mismatch. The tracked policy remains unchanged.
- The new `data_quality_events.feed_id` migration is prepared and covered by
  offline tests, but has not been applied to `eurusd_oracle_local` because
  PostgreSQL is stopped. The archive was not accessed.
- A wider market-state collection attempt could not collect two modules
  because they import `MetaTrader5` on Linux. No live MT5 terminal was started.
- The earlier broad Stage 7/11 run was 616 passed, 427 failed, 38 errors
  before the missing ignored reports were restored. It has not been rerun
  since; the pinned policy line-ending mismatch remains in this checkout.
- Windows compilation and Stage 7/11 regression remain outstanding for
  acceptance. The user explicitly authorized commits after focused tests; no
  Windows result has been claimed.
- Independent gates remain 0/11 and source-family admissions 0/13.
  `FINAL=HOLD`. No collector, Council cycle, model promotion, paper/live
  trading or broker order submission is authorized.

## Next implementation milestone: DP-M2D.4

1. Start PostgreSQL and apply the guarded migration to `eurusd_oracle_local`,
   then verify catalog postflight. Never connect to `eurusd_oracle_archive`.
2. Bind the quality gate's broker clock to the selected feed/profile; the
   current MT5 clock provider is not profile-bound.
3. Design a feed-aware capture registry workflow and coordinated writer plus
   uniqueness cutover, keeping feed-labelled writes blocked until reviewed.
4. Run the relevant Windows MT5 and Stage 7/11 acceptance checks.

## Commit and report routine

For each implementation commit:

1. Run the focused tests for the changed milestone and `git diff --check`.
2. Keep commits scoped and name the verification result accurately; do not
   claim Windows or live-market acceptance from Linux tests.
3. Update this report with the commit hash, implemented scope, test results,
   remaining blockers and next milestone in the same commit or an immediately
   following documentation commit.
4. Do not push unless the user asks.
