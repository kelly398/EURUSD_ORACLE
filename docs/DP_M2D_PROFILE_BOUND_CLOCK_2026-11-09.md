# DP-M2D.4: Profile-bound broker clock checks

## Objective

Ensure a feed-scoped quality gate gets its broker clock from the same MT5
profile and capture epoch represented by its selected `feed_id`.

## Implemented

- `DataQualityEngine` accepts an explicit broker profile and capture epoch as
  a pair. It derives the deterministic feed identity and refuses a mismatch
  before constructing the provider.
- A feed-scoped quality engine without a matching profile has no generic MT5
  clock fallback. Its quality report records a blocking broker-clock issue.
- A matched profile is passed to `MT5Provider(broker_profile=...)`, which
  already checks terminal, account, and instrument metadata without switching
  accounts or selecting symbols.
- `MarketStateEngine` forwards optional profile and capture-epoch inputs into
  the quality engine. No credentials or profile claims are added to market
  snapshots or Council contexts.
- Legacy mode with no `feed_id` retains its existing provider behavior.

## Verification and limits

- Focused feed, migration, storage, repository, clock-binding, factory,
  Council, and context suite: **194 passed**, 15 subtests passed, with 4
  existing pytest collection warnings.
- `git diff --check` passed.
- Tests use synthetic profiles/providers. No MT5 terminal was started and no
  broker account was contacted.
- Profile matching verifies local configuration consistency. It is not
  independent broker authentication or source-admission evidence; all gates
  remain closed and `FINAL=HOLD`.

## Next

1. Design the feed-registry registration/capture workflow with immutable
   profile and epoch provenance, without enabling writes.
2. Review a coordinated, reversible feed-aware writer and candle uniqueness
   cutover against a disposable database clone.
3. Run the Windows MT5 and Stage 7/11 acceptance checks before claiming
   milestone acceptance.

