# EURUSD_ORACLE continuation progress

Updated: 2026-11-09

This is the handoff note for the next development session. Read it together
with the architecture references and current DP-M2D records:

- `docs/ORACLE_Implementation_Atlas_2026-10-30.pdf`
- `docs/ORACLE_Future_Implementation_Masterplan_Stages_7_to_11_2026-10-30.pdf`
- `docs/DATA_PIPELINE_MODERNIZATION_MILESTONES_2026-10-31.md`
- `docs/DP_M2D_FEED_SCOPED_READS_2026-11-09.md`
- `docs/DP_M2D_FEED_PROVENANCE_2026-11-09.md`
- `docs/DP_M2D_PROFILE_BOUND_CLOCK_2026-11-09.md`
- `docs/DP_M2D_FEED_REGISTRATION_2026-11-09.md`
- `docs/DP_M2D_FEED_CUTOVER_REHEARSAL_2026-11-09.md`
- `docs/DP_M2D_CUTOVER_SEQUENCE_2026-11-09.md`

## Current position

- Branch: `feature/utc-research-m01-m08`.
- Latest implementation commit: `975096a fix(database): load env for
  rollback-only feed rehearsal`.
- Latest design milestone commit: `b906d73 docs: define feed candle cutover
  sequence`.
- DP-M2D.5 implementation commit: `8e3a253 feat(data): add guarded feed
  registry registration`.
- DP-M2D.4 implementation commit: `a30260c fix(data): bind quality clocks
  to selected feed`.
- DP-M2D.3 implementation commit: `ba6ce30 feat(data): propagate feed
  provenance to council context`; migration status update: `a73b493`.
- Handoff report was introduced in `49f6db9` and is updated after each
  implementation or design milestone commit.
- Previous commits: `bb9ca49 feat(database): add guarded local feed schema migration`;
  `95b742a fix(storage): block feed-labelled tick and backfill writes`.
- Current engineering milestone: DP-M2D.4 profile-bound broker-clock checks
  are implemented and covered by focused tests. The DP-M2D.3 event-column
  migration was applied twice and passed strict postflight on
  `eurusd_oracle_local`.

## Reference PDFs added

- The Implementation Atlas and Stages 7-11 Masterplan are dated 2026-10-30.
  Their baselines say Stage 5 / Stage 6 complete and propose P6 or P7 work as
  the next action at that time. They are now historical/design references, not
  an exact description of this later checkout.
- This repository has since advanced through Stage 7/8/9/10/11 engineering
  modules and the DP-M2D feed work listed below. Use this continuation report,
  current Git history, and current code to determine implementation state.
- Preserve the PDFs' useful boundaries: distinguish engineering completion
  from independent operational acceptance, do not infer source admission from
  local hashes or tests, keep the system `FINAL=HOLD`, and document tests,
  commit, evidence limits, and the next dependency after each batch.
- PDF SHA-256: `ORACLE_Implementation_Atlas_2026-10-30.pdf` is
  `219b1e65dad67d6417dcc46e1fd1ccc9b0ec879ee895ff3a2d30c0e2905c71ab`;
  `ORACLE_Future_Implementation_Masterplan_Stages_7_to_11_2026-10-30.pdf` is
  `16d6d45e62cf43ef9d735065cdbf9ad3d60d6b853ed353df93c97c56e8d1adc9`.

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

### Profile-bound clock — DP-M2D.4

- `DataQualityEngine` now requires a broker profile and capture epoch together
  and derives the local feed identity from them. A mismatch fails before a
  provider is constructed.
- A feed-scoped quality gate without its matching profile has no generic MT5
  clock fallback; it records a blocking quality issue. A matching profile is
  passed into `MT5Provider` for its existing terminal/account/instrument
  checks.
- `MarketStateEngine` forwards the optional profile and epoch without putting
  account-profile data in snapshots or Council contexts.
- Details: `docs/DP_M2D_PROFILE_BOUND_CLOCK_2026-11-09.md`.

### Feed registration — DP-M2D.5

- Added `prepare_feed_registration` to derive the minimal registry row from a
  validated profile and capture epoch. Preview reports omit account aliases
  and explicitly keep authentication, admission, market writes, and trading
  disabled.
- Added `register_local_feed` for explicit local development registration.
  It requires exact `eurusd_oracle_local` confirmation, validates the URL
  before connecting, verifies current database and role, and inserts only a
  registry row with bounded transaction timeouts.
- Same-record registration is idempotent; conflicting registry rows are never
  overwritten. Tests use synthetic SQLite data. The example profile was not
  registered and no MT5 terminal was used.
- Details: `docs/DP_M2D_FEED_REGISTRATION_2026-11-09.md`.

### Writer and candle-key cutover rehearsal — DP-M2D.6

- Fixed the rollback-only PostgreSQL lab runner to load `.env` before reading
  its dedicated disposable lab URL. Its first CLI attempt safely refused
  because the environment had not been loaded.
- Reran the exact lab command with explicit database-name confirmation. The
  temporary-table rehearsal verified distinct legacy/feed candle and tick
  rows, duplicate suppression, and that the old named-constraint candle
  writer breaks after `uq_broker_candle` removal.
- The rehearsal rolled back all temporary DDL and rows. It reported that the
  production DB was neither connected nor modified. The archive was not
  accessed; feed-labelled production writes remain blocked.
- Details: `docs/DP_M2D_FEED_CUTOVER_REHEARSAL_2026-11-09.md`.

### Candle-key cutover plan — DP-M2D.7

- Added the gated deployment sequence for writers, readers, concurrent
  indexes, bounded constraint removal, and post-cutover verification.
- Documented the rollback boundary: after overlapping feed-labelled candle
  rows exist, restoring the old global unique constraint may be impossible.
- No production or archive migration was run. Archive-scale build, lock,
  capacity, restore, Windows, and Stage 7/11 evidence remain outstanding.
- Details: `docs/DP_M2D_CUTOVER_SEQUENCE_2026-11-09.md`.

## Linux workstation and databases

- PostgreSQL is installed and running locally on port `5432`.
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
- DP-M2D.4 focused suite, recorded in `a30260c`: **194 passed**, 15
  subtests passed, with the same 4
  existing collection warnings. Synthetic profile/provider tests prove that
  missing or mismatched bindings fail closed; no terminal was contacted.
- DP-M2D.5 focused suite, recorded in `8e3a253`: **201 passed**, 15
  subtests passed, and 4 existing
  pytest collection warnings. It covers preparation, minimal fields,
  idempotency, collision refusal, and local-database guards. No real profile
  was registered.
- DP-M2D.6 suite, recorded in `975096a`: **202 passed**, 15 subtests passed,
  and 4 existing
  collection warnings. The dedicated PostgreSQL lab rehearsal completed and
  rolled back all its temporary schema and data.
- DP-M2D.7 is a documentation-only cutover design. It does not pass gates or
  authorize the constraint change, feed writes, or archive modification.
- `git diff --check` passed for the latest code commit.
- The Stage 8 point-in-time suite was also attempted; it fails at the known
  pinned HFM policy raw-byte SHA check caused by this checkout's line-ending
  mismatch. The tracked policy remains unchanged.
- The new `data_quality_events.feed_id` migration was applied twice to
  `eurusd_oracle_local`; strict postflight verified the column, foreign key,
  and index. Both runs reported zero legacy-row changes and zero feed-labelled
  writes. The archive was not accessed.
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

## Next implementation milestone: DP-M2D.8

1. Exercise real feed-aware writers, retries, and checkpoints only on a
   disposable archive clone; collect archive-scale index, lock, disk, WAL,
   and restore measurements.
2. Complete Windows MT5 and Stage 7/11 acceptance and independent source
   review before considering any operational write authorization.
3. Keep feed-labelled writers blocked; do not cut over or alter the archive.

## Commit and report routine

For each implementation commit:

1. Run the focused tests for the changed milestone and `git diff --check`.
2. Keep commits scoped and name the verification result accurately; do not
   claim Windows or live-market acceptance from Linux tests.
3. Update this report with the commit hash, implemented scope, test results,
   remaining blockers and next milestone in the same commit or an immediately
   following documentation commit.
4. Do not push unless the user asks.
