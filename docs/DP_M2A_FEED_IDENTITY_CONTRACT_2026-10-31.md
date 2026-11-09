
# DP-M2A: Versioned MT5 Feed Identity Contract

Baseline commit:
df80babcf8206fd3f09521df9aaf61c89b30dfe1

## Status

DP-M1 was committed and pushed after Windows regression.

DP-M2A introduces an offline local feed identifier, versioned tick
fingerprint and proposed M5 candle identity. DP-M2B adds a source-only
migration preflight, three-phase non-executing DDL proposal and a rollback-only
rehearsal against synthetic temporary tables. Its final proposed phase checks
the replacement indexes under a bounded table lock, refuses preexisting
feed-labeled rows, then proposes dropping the old candle uniqueness constraint.
That phase remains unapplied. DP-M2C.1 prepares the existing candle
writer to reject feed-labeled rows before database sessions are opened. The
local uncommitted continuation adds fail-closed guards to the tick batch and
historical backfill writers, and removes the backfill's dependency on the
legacy named candle constraint. DP-M2D.1 adds nullable feed columns, a feed
registry and partial indexes to the ORM, then applies an explicit guarded
bootstrap migration only to the empty Linux development database.

The original create_tick_fingerprint() function is preserved.

The restored `eurusd_oracle_archive` database retains the Windows archive
schema and rows unchanged. Only the empty `eurusd_oracle_local` database has
the additive DP-M2D.1 schema; the migration refuses the archive and lab names,
remote hosts, superuser sessions and populated tick/candle tables. Feed-labeled
writes remain blocked; feed-aware readers are not implemented.

Independent gates: 0/11.
Admitted source families: 0/13.
FINAL=HOLD.

## Existing database findings

The ticks table has a globally unique fingerprint without feed_id.

The broker_candles table has the uq_broker_candle constraint on:

(symbol, timeframe, timestamp, source)

Neither constraint safely partitions multiple MT5 brokers.

Existing tick/candle storage, market-state repositories, Data Quality
and historical dataset builders still lack consistent feed identity.

## DP-M2A: Feed identity contract

Each future selected feed receives a deterministic routing identifier
derived from its strict MT5 profile and an explicit capture-epoch label.

The ID covers the claimed broker, server, environment, account alias,
broker-specific symbol and timestamp-contract label.

A local alias is not independent account authentication.

The contract cannot approve market-data writing or trading.

Tick fingerprint V2 adds a feed-aware versioned domain separator.
Existing V1 fingerprints remain unchanged.

The M5 candle key is a proposed future per-feed uniqueness identifier.
It is not yet implemented as a database constraint.

## DP-M2B: PostgreSQL migration

The archive/production migration remains unimplemented and unapproved. The
DP-M2D.1 workstation bootstrap is not a production migration.

Before any populated-database migration:

1. Inspect the current PostgreSQL schema and indexes.
2. Verify backup, available disk space and rollback procedures.
3. Test on a disposable PostgreSQL database.
4. Create a versioned feed registry.
5. Add nullable feed_id fields through an additive migration.
6. Preserve legacy rows with unknown feed ancestry.
7. Preserve existing tick fingerprints.
8. Coordinate replacement of the old global candle uniqueness rule.
9. Add feed-aware indexes and writer validation.
10. Verify rollback and mixed-broker data isolation.

The existing candle uniqueness constraint must not be dropped before
the new schema, writers and indexes have been validated together.

## DP-M2C: Ingestion and reader integration

Fail-closed writer guards are in place, and nullable ORM columns now exist.
Full ingestion and reader integration is not yet implemented.

Integrate feed identity into:

- MT5TickIngestor and BatchTickStorage
- MT5BarIngestor and BrokerCandleStorage
- TickRepository and CandleRepository
- DataQualityEngine
- MarketStateEngine
- Historical dataset and feature builders

All multi-broker reads must explicitly select a feed.

Legacy data must not be silently relabeled as authenticated HFM data.

## Acceptance conditions

DP-M2A requires Windows compilation, focused tests, preservation of
legacy fingerprint behavior and successful Stage 7/11 regression. The focused
DP-M2 regression passed 150 tests on Linux. The ignored historical reports
have been recovered under `logs/`; the earlier broad Stage 7/11 run has not
been repeated since. The pinned historical policy remains unchanged because
this checkout has LF bytes while the recovered original has the expected CRLF
hash. Windows acceptance remains pending.

Do not commit before reviewing the Windows results.

DP-M2 overall remains incomplete until database, writer, reader and
quality integration have been verified.

No collector, broker connection, migration or orders are authorized.

FINAL=HOLD.
