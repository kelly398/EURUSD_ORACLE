
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

**Not yet implemented.** Design, dry-run and back up before any migration.

- Add a stable, validated `feed_id` (broker profile/server/epoch combination) to *new* ticks, candles, quality-event receipts, and research queries.
- Design revised uniqueness and indexes without breaking the existing global fingerprint/`uq_broker_candle` constraints until the migration is validated on a database copy.
- Choose new identity versioning rather than rewriting existing fingerprint bytes. Treat legacy rows as `LEGACY_FEED_UNKNOWN` unless independently evidenced; do not silently claim historical HFM provenance.
- Test mixed-broker inserts, deduplication, latest-row cursors, query isolation and resumption on realistic-volume fixtures.
- Provide an explicit rollback plan and perform no database changes without approval.

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
