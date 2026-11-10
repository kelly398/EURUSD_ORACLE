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
- Latest implementation commit: `7aa900d feat(database): rehearse clone
  candle cutover rollback`.
- Previous implementation commit: `1dc8e30 fix(storage): guard feed ticks
  and fingerprint legacy writes`.
- Previous implementation commit: `3936bea feat(database): rehearse clone
  writer retries safely`.
- Previous implementation commit: `60e49e4 feat(database): guard feed
  migration on archive clone`.
- Latest reference-document commit: `e854875 docs: add project atlas and
  future masterplan`.
- Previous DP-M2D.6 implementation commit: `975096a fix(database): load env
  for rollback-only feed rehearsal`.
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
- Current engineering milestone: DP-M2D.8 clone migration, rollback-safe
  writer retry rehearsal, and backup/restore check are complete. DP-M2D.9
  writer inventory and transactional old-constraint removal rehearsal are
  complete on the disposable archive clone. Feed-labelled production writes
  remain blocked. Earlier milestones and local migration history are recorded
  below.

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

### Clone-only additive migration — DP-M2D.8 (commit `60e49e4`)

- Created the disposable PostgreSQL clone `eurusd_oracle_dp_m2d8_clone` from
  `eurusd_oracle_archive`. The archive itself was not connected to by the
  migration and remains unchanged. The clone is owned by restricted role
  `Komarchy`.
- Added `backend/database/feed_clone_migration.py`, a separate clone-only
  command. It requires a dedicated clone URL and exact database-name
  confirmation; checks the target, role, archive sessions, disk margin,
  source row counts, legacy uniqueness, and absence of pre-existing
  feed-labelled rows; then applies only the additive feed schema and runs
  postflight verification. It does not rewrite rows, remove constraints, or
  authorize feed writes.
- Verified the migration/idempotence path on the clone. Counts before and
  after were unchanged: `ticks=30,744,086`, `broker_candles=75,528`,
  `candles=2`. Legacy `uq_tick_fingerprint` and `uq_broker_candle` remain.
  No feed-labelled rows were written; archive sessions at measurement time: 0.
- Clone database size: `12,752,918,207` bytes (about 12.75 GB). `ticks` total
  relation size was `12,614,270,976` bytes (about 12.6 GB), including indexes;
  current feed indexes are empty (8 KB apiece). PostgreSQL reported roughly
  124 GB free before migration. This is a size baseline only: no lock-duration,
  WAL-volume, backup/restore, or performance acceptance measurement is claimed.
- Feed-aware production writers remain blocked. The clone schema migration
  does not authorize feed-labelled production writes, remove the legacy
  constraint, or enable collection. The later clone-only rehearsal below
  checks legacy-writer retry behavior inside a rollback boundary; operational
  gates remain at HOLD.

### Clone writer/recovery rehearsal — DP-M2D.8 continuation (commit `3936bea`)

- Added `backend/database/feed_clone_rehearsal.py`. It runs the existing
  `BatchTickStorage` and `BrokerCandleStorage` classes on the clone using
  savepoint-joined sessions within one outer transaction. Exact database-name
  confirmation and the clone migration preflight are required. Synthetic
  negative IDs avoid advancing sequences; the outer transaction always rolls
  back and postflight checks counts and synthetic IDs.
- The clone run inserted one legacy tick and one legacy broker candle inside
  the transaction. Retrying each inserted zero duplicates. The tick query
  cursor advanced inside the transaction; this cursor is not durable. A
  separate test confirms `ForwardFeedStore.resume_cursor()` survives store
  reopen in its SQLite fixture. Feed-labelled writes were verified to fail
  before opening a database session.
- Before rollback, both table locks were granted as `RowExclusiveLock`.
  The four writer calls took 10.236 ms (tick insert), 2.221 ms (tick retry),
  6.696 ms (candle insert), and 2.247 ms (candle retry) in this single local
  rehearsal. PostgreSQL reported an 84,248-byte cluster-wide WAL LSN delta;
  this is an observed rehearsal value, not an isolated benchmark. After
  rollback, canonical counts matched the baseline and no synthetic rows
  remained. Database size was unchanged; sequences did not advance.
- Completed a full logical backup and restore verification. A 1,963,154,319
  byte custom-format dump restored into a temporary database in about 8.5
  minutes using four workers. Restored counts matched (`ticks=30,744,086`,
  `broker_candles=75,528`, `candles=2`); all five feed indexes were valid and
  legacy `uq_broker_candle` remained. The restored database measured
  12,905,494,207 bytes. The temporary restore database and dump were removed;
  the working clone remains. The archive was not modified.
- These results validate rollback, writer duplicate behavior, and logical
  recoverability on this workstation. They do not authorize feed-labelled
  writes or claim production lock/WAL performance, Windows acceptance, or
  independent source review.

### Writer inventory corrections — DP-M2D.9 readiness review (commit `1dc8e30`)

- Mapped the in-repository writers that can touch legacy ticks and broker
  candles. `BatchTickStorage` is used by `MT5TickIngestor`; `BrokerCandleStorage`
  is used by `MT5BarIngestor`; historical backfill has `insert_ticks` and
  `insert_bars`. Broker-candle paths use untargeted `ON CONFLICT DO NOTHING`.
  The named `uq_broker_candle` conflict target remains only in the isolated
  old-writer regression rehearsal, where its breakage after constraint removal
  is intentional.
- Found that `historical_backfill.insert_ticks` lacked the feed-label guard.
  It now rejects any row containing `feed_id`, including `feed_id=None`, before
  opening a transaction, matching the other batch writers.
- Found that the older single-row `TickStorage` used by `TickCollector` omitted
  the required `fingerprint`. It now computes the legacy fingerprint from the
  minimal `MarketTick` fields and the model's zero defaults. No in-repository
  caller of `TickCollector` was found. `CandleStorage` writes the separate
  derived `candles` table, not `broker_candles`; `ForwardFeedStore` writes its
  own `oracle_utc_*` evidence tables.
- Focused DP-M2D.9 regression: **85 passed** across historical backfill,
  single-tick storage, clone safeguards, schema, and cutover writer tests;
  `git diff --check` passed. No database writes were part of this code change.
- The inventory is a repository search, not proof that no external script or
  operator process writes these tables. Live MT5 terminal/profile acceptance
  and independent source review remain open gates.

### Transactional candle cutover rehearsal — DP-M2D.9 (commit `7aa900d`)

- Added `backend/database/feed_clone_cutover_rehearsal.py`. It checks both
  replacement candle indexes are unique, ready, and valid; verifies no
  feed-labelled candle rows; drops `uq_broker_candle` only inside a bounded
  transaction on `eurusd_oracle_dp_m2d8_clone`; exercises the real
  `BrokerCandleStorage` legacy insert and duplicate retry; then rolls back.
- The clone run confirmed the old constraint was absent inside the
  transaction, one legacy candle inserted, and the retry inserted zero. The
  writer's internal commits remained inside the outer transaction. Rollback
  restored `uq_broker_candle`, kept replacement indexes valid, removed the
  synthetic row, preserved all three baseline counts, and did not advance the
  sequence. No feed-labelled row was inserted; the archive was not modified.
- Focused DP-M2D.9 clone/cutover/schema suite: **50 passed**;
  `git diff --check` passed. This validates only the disposable-clone legacy
  writer path; it does not authorize production cutover or feed writes.

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
- DP-M2D.8 focused clone-migration/schema/rehearsal/writer-compatibility suite:
  **70 passed**. `git diff --check` passed. The actual clone rehearsal and
  logical backup/restore were run separately and recorded above.
- An attempt to collect the existing `backend/data_engine/test_forward_store.py`
  module was blocked because its test fixture imports `MetaTrader5`, which is
  unavailable on Linux. A new SQLite test directly exercises the durable
  `ForwardFeedStore` resume cursor across store reopen. No MT5 terminal was
  started.
- Most recent Linux Stage 7/11 module run: **627 passed, 454 failed** across
  the selected 14 Stage 7/11 test modules. Representative Stage 7 baseline
  and Stage 11 read-model failures hit the known pinned historical-policy
  raw-byte SHA mismatch in this LF checkout. This run is not an acceptance
  pass; the other failures have not all been individually classified.
- Windows full-suite result reported after pulling `feature/utc-research-m01-m08`:
  **5,712 passed, 73 skipped, 617 subtests passed, 5 collection warnings** in
  3,410.88 seconds. `$tests.Count` was `0`, so the pytest command received no
  paths and performed default full-project discovery; this includes the
  Stage 7/11 modules rather than being limited to them. The pasted output did
  not include `git rev-parse --short HEAD`; expected tested commit is `a3ad331`
  based on the preceding pull. No live-terminal run is claimed.
- The Windows full-suite run covers the Stage 7/11 test modules, but it does
  not establish live MT5 terminal/profile acceptance or independent source
  review. Verify the exact tested HEAD if this run will be used as a formal
  platform acceptance anchor.
- `git diff --check` passed for the latest code commit.
- The Stage 8 point-in-time suite was also attempted; it fails at the known
  pinned HFM policy raw-byte SHA check caused by this checkout's line-ending
  mismatch. The tracked policy remains unchanged.
- The new `data_quality_events.feed_id` migration was applied twice to
  `eurusd_oracle_local`; strict postflight verified the column, foreign key,
  and index. Both runs reported zero legacy-row changes and zero feed-labelled
  writes. That local migration did not access the archive.
- A wider market-state collection attempt could not collect two modules
  because they import `MetaTrader5` on Linux. No live MT5 terminal was started.
- The earlier broader Stage 7/11 run was 616 passed, 427 failed, and 38
  errors before missing ignored reports were restored. The selected module
  run above is the latest validation; the pinned policy line-ending mismatch
  remains in this checkout.
- Windows full-suite regression has passed as reported above. Live MT5
  terminal/profile acceptance and independent source review remain
  outstanding. The user explicitly authorized commits after focused tests.
- Independent gates remain 0/11 and source-family admissions 0/13.
  `FINAL=HOLD`. No collector, Council cycle, model promotion, paper/live
  trading or broker order submission is authorized.

## Next implementation milestone: DP-M2D.10 feed-writer authorization gate

1. Verify the exact Windows test commit and skipped-test reasons; do not infer
   live terminal acceptance from pytest alone.
2. Obtain independent review of feed identity/source evidence and the proposed
   operational change before considering feed-labelled writes.
3. Keep feed-labelled writers blocked and the archive unchanged until those
   external gates are independently satisfied.

## Commit and report routine

For each implementation commit:

1. Run the focused tests for the changed milestone and `git diff --check`.
2. Keep commits scoped and name the verification result accurately; do not
   claim Windows or live-market acceptance from Linux tests.
3. Update this report with the commit hash, implemented scope, test results,
   remaining blockers and next milestone in the same commit or an immediately
   following documentation commit.
4. Do not push unless the user asks.
