
# EURUSD_ORACLE data-pipeline modernization milestones

## Baseline and scope

- Repository source archive created from commit `6e007810c3d84fee7320be44bf1f6ead06290e99` by the operator.
- Source archive SHA-256 `2e95f89b22ca1ab1de773bf0d5fccfd7ea6b113ddecba613ff8b8e03523f4726` verified locally.
- 595 tracked files in the archive, including source, tests, historical experiment manifests and model artifacts.
- This is a static-source and fake-terminal review. The archived source has no live database or independently authenticated MT5 session attached. No collector, training, Council, execution, or broker connection was started.
- Original HFM clock-policy implementation, pinned epoch artifacts, and historical trading records are not modified by the first milestone.
- Independent external gates remain 0/11, source-family admissions remain 0/13, and FINAL=HOLD remains mandatory.

## Confirmed current dependency graph

1. `backend/data_engine/mt5_provider.py` owns the MT5 Python API calls (except historical offline/probe utilities with direct MT5 initialization).
2. `backend/data_engine/mt5_tick_ingestor.py` and `backend/candle_engine/mt5_bar_ingestor.py` each instantiate an MT5 provider and persist to distinct stores.
3. `backend/data_engine/batch_tick_storage.py` keys tick deduplication on a global `fingerprint`, and `backend/candle_engine/broker_candle_storage.py` keys M5 bars on `(symbol,timeframe,timestamp,source)`.
4. `backend/database/models.py` defines the `ticks` and `broker_candles` entities without a dedicated broker-profile/feed ID.
5. `backend/data_quality/quality_engine.py`, `backend/market_state/tick_repository.py` and `backend/market_state/candle_repository.py` query by symbol/timeframe and sometimes `source='MT5'` but not broker-feed identity.
6. `backend/models/feature_builder.py`, historical dataset contracts, `backend/agents/context_factory.py`, and market-state schemas consume these stored rows through typed research contexts; their principal data contracts lack a broker-feed ID.
7. `backend/macro` (ECB, Fed, FRED cross-asset) and `backend/institutional` (CFTC) acquire separate types of evidence and must NOT be routed through MT5. Their publication-time and admission requirements remain distinct.
8. `backend/runtime/stage7_universal_mt5_adapter.py` and `stage7_mt5_read_only_export_bridge.py` implement strict offline profile/export contracts. They do not initialize MT5 or admit unverified rows.
9. `backend/runtime/market_data_supervisor.py` can spawn legacy tick/bar workers or a bounded forward clock recorder when explicitly run. This audit does not run it.
10. `backend/data_engine/clock_policy.py` is HFM epoch-specific, independent of the broker-neutral normalization contract. Do not generalize its UTC+2/+3 mapping.

## Key confirmed gaps

- **A. Connection identity:** The existing provider's plain connect path checks terminal connectivity without validating the expected broker company, server, account environment or instrument characteristics.
- **B. Global database identity:** Original tick and candle uniqueness keys cannot reliably partition data from different MT5 brokers, even when they share `EURUSD` and `source='MT5'`.
- **C. Writer duplication:** The legacy `TickCollector`, canonical `MT5TickIngestor`, alternative candle builder and `LiveCandleEngine` have overlapping collection/storage roles. Their activation must be inventoried before allowing a new feed.
- **D. Timestamp provenance:** MT5 Python UTC fields and the separate historically pinned HFM wall-clock correction must remain distinct. An unverified raw terminal timestamp is not independent clock attestation.
- **E. Quality and model lineage:** Per-feed freshness, candle gaps, deduplication, models, replay, forecasts and agent context need a broker/feed identity and source-admission state.
- **F. Connected-tool bypasses:** `backend/models/historical_backfill.py`, `audit_historical_backfill.py` and `probe_mt5_history.py` directly call `mt5.initialize`; these diagnostic/historical entry points require the same selected-feed guard when approved for integration.
- **G. Account specificity:** A human-readable local account alias is NOT a cryptographic binding to an MT5 account number. A connected metadata match must not be misrepresented as independent G01 approval.
- **H. Database migration:** `Base.metadata.create_all()` does not migrate existing Postgres tables. A feed-ID schema change requires a planned, reversible, tested migration and a rule for legacy rows with unknown provenance.

## Milestone DP-M1 — Existing MT5 provider broker binding

**Change now prepared, pending Windows verification.**

- Update the existing `MT5Provider`, adding an optional validated `broker_profile`.
- Reuse the previously committed offline Stage 7 broker-profile schema. Do not duplicate the entire profile contract.
- Validate company, server, DEMO/LIVE, NETTING/EXCHANGE/HEDGING, expected symbol, digit precision, point, contract size, currencies, spread mode, chart mode, trade mode and calc mode before accepting data.
- Revalidate the bound terminal on each `get_price`, `get_ticks_range` and `get_candles` entry, so switching the active MT5 account after initialization fails closed.
- Preserve all existing method signatures and legacy unbound behavior. Existing collectors/ingestors are not yet migrated to profile-bound mode.
- Keep `clock_policy` and `broker_profile` mutually exclusive. Preserve the historical HFM policy and original epoch unchanged.
- No account-number storage or logging, no terminal path leakage, no new trade calls or data admission.
- Add fake-MT5 tests for two broker profiles and adverse identity/specification changes.

**Local outcome:** 248 passed and 34 subtests passed for the focused combined test set with an MT5 stub and an isolated in-memory SQLite configuration. This is NOT a full Windows integration test and NOT a verified live-market connection.

**Exit criteria:** User confirms Python compilation, new provider tests, relevant existing timestamp/clock tests and Stage 7/11 regression on Windows. Review diff for exactly the intended files, commit/push only after checks.

## Milestone DP-M2 — Feed identity and additive Postgres schema migration

**In progress.** The additive ORM contract and guarded empty-local-database
bootstrap are implemented. The restored historical archive is not migrated.

- Add a stable, validated `feed_id` (broker profile/server/epoch combination) to *new* ticks, candles, quality-event receipts, and research queries.
- Design revised uniqueness and indexes without breaking the existing global fingerprint/`uq_broker_candle` constraints until the migration is validated on a database copy.
- Choose new identity versioning rather than rewriting existing fingerprint bytes. Treat legacy rows as `LEGACY_FEED_UNKNOWN` unless independently evidenced; do not silently claim historical HFM provenance.
- Test mixed-broker inserts, deduplication, latest-row cursors, query isolation and resumption on realistic-volume fixtures.
- Provide an explicit rollback plan before any populated-database migration.
  The local bootstrap is restricted to the empty `eurusd_oracle_local` database.

### Local progress on `feature/utc-research-m01-m08` at `002cbcf`

- DP-M2A defines a local feed identity and versioned tick fingerprint while preserving V1.
- DP-M2B contains a source-model preflight, non-executing PostgreSQL DDL proposal, and a rollback-only rehearsal for synthetic temporary tables. The proposal separates nullable columns, concurrent partial-index creation, and a bounded legacy candle-constraint cutover that checks both replacement indexes and refuses existing feed-labeled rows. These do not verify or modify a real database.
- DP-M2C.1 updates the existing candle writer to reject `feed_id` rows before opening a session. Local uncommitted changes apply the same guard to the tick batch writer and historical candle backfill; the backfill also removes its named `uq_broker_candle` conflict-target dependency.
- DP-M2D.1 adds nullable feed IDs, registry foreign keys, and partial feed indexes to the ORM while preserving the existing `uq_tick_fingerprint` and `uq_broker_candle` constraints. A guarded idempotent local-only migration adds this schema to the empty `eurusd_oracle_local` database; it refuses other database names, remote hosts, superuser sessions, or populated tick/candle tables.
- DP-M2D.2 scopes TickRepository, CandleRepository, and latest-row cursors to one selected feed. Omitting `feed_id` now means legacy rows with `feed_id IS NULL`; it never means “all feeds”. TickSample and CandleBar preserve the selected feed ID. Feed IDs are syntax-validated before queries; writer guards remain active.
- DP-M2D.3 threads one selected feed through the data-quality gate, immutable market-state snapshot, and immutable agent context; it records feed-labelled quality events and rejects mixed-feed snapshots/candles/ticks. Its additive event-column migration completed twice with strict postflight on `eurusd_oracle_local`; zero legacy rows changed and zero feed-labelled writes occurred. Feed-labelled market writes remain blocked.
- DP-M2D.4 binds feed-scoped quality checks to a broker profile plus capture epoch whose deterministic identity must equal the selected feed ID. Without the binding there is no generic terminal-clock fallback; the quality gate fails closed. Synthetic profile/provider checks pass; no live terminal was used.
- DP-M2D.5 adds a deterministic registration preparer and an explicit local-only registry insert guarded by exact database confirmation, DB/role rechecks, idempotent verification, and bounded timeouts. It stores only feed/profile/epoch/hash fields and does not admit the source or enable market-data writes. No sample profile was registered.
- DP-M2D.6 fixes the rollback-only lab runner to load `.env`, then reruns the temporary-table writer/uniqueness rehearsal in the exact disposable lab DB. It confirmed old named-constraint writer breakage, legacy/feed deduplication, and full rollback; production was neither connected nor modified.
- The Windows archive was restored separately as `eurusd_oracle_archive` (30,744,086 ticks and 75,528 broker candles). It remains on its original schema and was not connected to or modified by the DP-M2D migration. The rollback-only lab remains empty after rehearsal.
- Legacy writers still reject feed-labelled writes; feed-aware readers are not active. Existing archive rows retain unknown feed provenance; the old candle constraint is not dropped; `FINAL=HOLD` remains.
- Focused DP-M2 offline regression on Linux: 156 passed, including schema/writer contracts and feed-scoped repository/cursor isolation using synthetic SQLite rows. Two broader tests could not be collected on native Linux because importing them requires the Windows-only `MetaTrader5` package. The local PostgreSQL migration was applied repeatedly and its strict catalog postflight verified the partial index definitions. Windows regression/acceptance remains pending for milestone acceptance.
- DP-M2D.3 focused feed/storage/context/Council regression: 191 passed, 15 subtests passed, and 4 existing collection warnings. The Stage 8 point-in-time suite still fails its pinned HFM policy raw-byte SHA check because this checkout has a known line-ending mismatch. The policy file remains untouched. PostgreSQL was stopped during this session, so the event feed-column migration is not yet applied.
- The transferred ignored historical reports are now available under `logs/`. The tracked HFM policy remains unmodified; the original Windows-byte copy is preserved in Downloads because this Linux checkout has a different raw line-ending hash. The earlier broad Stage 7/11 run has not been repeated after report recovery.

## Milestone DP-M3 — One MT5 owner and canonical ingestion/quality

**Not yet implemented.**

- Route existing tick and M5 workers through a shared profile-bound MT5 connection strategy or isolated broker-specific processes; MT5's Python terminal state must not be shared casually between unrelated accounts.
- Preserve current `MT5TickIngestor` and `MT5BarIngestor`; ensure writes are impossible without approved feed-specific schema/provenance and point-in-time rules.
- Make tick validation, timestamp checks, rate completion, cursors and checkpointing consistent across the two ingestion paths.
- Make data-quality checks feed-specific and correctly distinguish closed markets, staleness, missing bars and provider errors.
- Inspect alternate collector and derived-candle writers to avoid two production writers for the same stream.
- No collector start/restart until separately authorized.

## Milestone DP-M4 — Model, macro and Council input lineage

**Not yet implemented.**

- Propagate `feed_id`, origin observation time, arrival time, raw timestamp domain, clock policy/epoch, source vintage and quality/admission status into market-state and model feature manifests.
- Preserve source-specific ECB/Fed/FRED/CFTC adapters and their own release-time guards; MT5 is only the broker market-data interface.
- Make model dataset and replay comparisons refuse incompatible mixed-feed ancestry.
- Make `AgentContextFactory` and Council inputs reject stale, ambiguous, mixed or unapproved sources. Keep agent calls disabled pending Stage 8 approvals.

## Milestone DP-M5 — Independent evidence and authorization review

**Not yet implemented.**

- Reconcile source receipt and broker UTC evidence (G01–G03), source-family admissions (G05–G06), model/forecast ancestry and post-horizon outcome controls.
- Obtain independent review of a bounded *read-only* connected acquisition plan separately from this offline integration.
- No G01–G11 promotion from self-reported data, local file hashes, synthetic tests or HFM screenshots alone.
- FINAL=HOLD remains effective throughout modernization, without broker order submission.

## Safety and review rules

- No modifying historical HFM policy JSON, historical datasets, pre-existing ticks/candles or frozen model artifacts.
- No activation of MT5, supervisor, Council, models, automation, paper trading or execution during offline milestones.
- All code changes target the *current* repository branch, not the obsolete October 27 snapshot.
- No hardcoded broker server in shared provider logic; HFM appears only in its local profile/tests.
- Every implementation milestone must have exact source diff, local offline tests, Windows regression, operator-reviewed Git status and push confirmation before marking complete.
