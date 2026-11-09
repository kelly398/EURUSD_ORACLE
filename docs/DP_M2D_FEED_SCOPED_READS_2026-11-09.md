# DP-M2D.2: Feed-scoped reads and cursors

## Objective

Make database reads select one explicit MT5 feed or the legacy unknown-feed
partition. A missing `feed_id` must never mean “read every feed”.

## Changes

- `TickRepository` filters its range reads by `feed_id`; its default selects
  only legacy rows where `feed_id IS NULL`.
- `CandleRepository` applies the same scope to latest, as-of, and range reads.
- `BatchTickStorage` and `BrokerCandleStorage` now calculate latest-row
  cursors within the selected feed. Invalid feed IDs fail before a DB session
  opens.
- `TickSample` and `CandleBar` retain the selected `feed_id` for downstream
  lineage.
- Existing storage writers still reject feed-labelled rows. No new feed data
  can be written by these read/cursor changes.

## Verification

- Synthetic SQLite isolation tests cover legacy, feed A, and feed B reads;
  as-of/range queries; and per-feed tick/candle cursors.
- The combined DP-M2 focused suite passed 156 tests on Linux after these
  changes.
- A wider market-state test collection was attempted. Two modules could not be
  collected on native Linux because they import the Windows-only
  `MetaTrader5` package. Those tests need a Windows run or an explicit MT5 stub.
- No query was run against `eurusd_oracle_archive`; the archive remains
  unchanged.

## Remaining work

1. Thread feed selection through `DataQualityEngine`, `MarketStateEngine`, and
   `AgentContextFactory`.
2. Put `feed_id` into the immutable market-state and agent-context contracts,
   and reject mixed-feed contexts.
3. Validate against a disposable clone of the archive; preserve the archive
   and its null/unknown legacy ancestry.
4. Keep feed writes disabled until readers and writers are coordinated with
   the uniqueness cutover.

Independent gates remain 0/11, source-family admissions 0/13, and
`FINAL=HOLD`.
