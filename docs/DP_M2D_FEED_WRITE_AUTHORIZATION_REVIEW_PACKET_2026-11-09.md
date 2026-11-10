# DP-M2D feed-write authorization review packet

**Status: draft for independent review; not an approval or write authorization.**

This packet gives an independent reviewer a bounded scope for assessing the
EURUSD_ORACLE feed identity and proposed future feed-write boundary. It does
not authorize database changes, feed-labelled writes, collector startup,
Council execution, model promotion, or trading. `FINAL=HOLD` remains in force.

## Review baseline

- Branch: `feature/utc-research-m01-m08`.
- Current Linux baseline when prepared: commit `8fd4c60`.
- Reported Windows full-suite run: 5,712 passed, 73 skipped, 617 subtests
  passed, and 5 collection warnings. The report was sent after the Windows
  checkout pulled the branch; the pasted output did not include its exact HEAD.
  The expected tested commit is `a3ad331`, but that association is not
  independently verified. No live MT5 terminal run is evidenced.
- Clone-only engineering evidence: additive schema migration, batch writer
  duplicate retry and rollback, backup/restore verification, and transactional
  removal/rollback of `uq_broker_candle` with a legacy writer. The working
  clone is `eurusd_oracle_dp_m2d8_clone`; the archive was not modified.
- Current guards reject feed-labelled writes. A local feed registry row would
  be a routing label only and is not source admission or write authorization.
- Current counters: independent external gates **0/11**; independently
  admitted source families **0/13**; no production feed registration or
  feed-labelled rows.

## Requested review scope

Review the attached implementation and evidence against the source-family and
gate contracts. Treat hashes as content-integrity evidence only. For each
finding, distinguish code correctness, provenance evidence, and operational
authorization.

### G01–G03: broker, clock, and original-capture proof

| Gate | Evidence required from outside this local checkout | Current result |
|---|---|---|
| G01 Broker/server identity | Provider-origin, account-scoped server identity evidence and independent corroboration, with custody details | Not independently verified |
| G02 Broker clock | Independently timestamped UTC/reference pairs, method, uncertainty, and evidence across relevant seasonal transitions | Not independently verified |
| G03 Original capture | Original ordered tick/bar payloads and genuine capture receipts with independently established receipt times and custody | Not independently verified |

### G04–G11: forward operation and research controls

| Gate | Review requirement | Current result |
|---|---|---|
| G04 | Separately approved new forward epoch and monitored recovery plan; no historical rebind | Not approved |
| G05 | Independent provenance and first-availability evidence for all 13 source families | 0/13 admitted |
| G06 | Immutable Council-derived input, model, data-version, and decision-time lineage | Not independently verified |
| G07 | Authenticated risk limits, veto, kill-switch, and recovery controls | Not independently approved |
| G08 | Externally timestamped pre-decision prediction receipts | None verified |
| G09 | Independent post-horizon outcome receipts | None verified |
| G10 | Broker-original execution costs, or an independently attested no-trade scope | None verified |
| G11 | Independently timestamped evaluation/holdout freeze | None verified |

The 13 source families are `market_state`, `candles`, `ticks`, `cot_data`,
`ecb_data`, `fed_data`, `economic_calendar`, `cross_asset_data`,
`calendar_coverage`, `risk_state`, `risk_limits`, `reliability`, and
`synthesis_policy`.

## Writer and cutover review

The review should inspect these paths and their guards:

- `backend/data_engine/batch_tick_storage.py`
- `backend/data_engine/storage.py`
- `backend/candle_engine/broker_candle_storage.py`
- `backend/models/historical_backfill.py`
- `backend/database/feed_schema_migration.py`
- `backend/database/feed_clone_migration.py`
- `backend/database/feed_clone_rehearsal.py`
- `backend/database/feed_clone_cutover_rehearsal.py`
- `docs/DP_M2D_CUTOVER_SEQUENCE_2026-11-09.md`

Confirm that feed-labelled writes still fail closed, the clone rehearsal cannot
persist its synthetic row or DDL, the old constraint remains in the working
clone after rollback, and no step targets `eurusd_oracle_archive`. Review the
external process inventory limitation: repository search cannot rule out
operator scripts or services outside this checkout.

## Reviewer response record

For each gate, return one of `VERIFIED`, `BLOCKED`, or `NOT_REVIEWED`, cite
evidence by stable identifier and digest, state conflicts of interest, and
describe any limits. Include reviewer identity and role, review time in UTC,
the packet commit/hash reviewed, and an independently verifiable signature or
custody record. Keep credentials, account numbers, and secrets out of the
packet and response.

An independent review result is still not an operator change approval. Any
future write authorization requires the separately approved change process,
verified platform evidence, explicit target confirmation, and a new review of
the exact migration and writer revisions.

## Decision

**No independent review is recorded. No gate is promoted by this packet.**
Keep all feed-labelled writers blocked, leave the archive unchanged, and retain
`FINAL=HOLD`.
