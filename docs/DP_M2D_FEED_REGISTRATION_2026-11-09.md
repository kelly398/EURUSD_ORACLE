# DP-M2D.5: Feed registration workflow

## Objective

Provide a deterministic way to prepare and, when deliberately requested,
register a feed routing label without claiming source admission or enabling
market-data writes.

## Implemented

- `prepare_feed_registration(profile, capture_epoch=...)` validates the local
  MT5 profile and explicit future capture epoch, derives the versioned feed
  identity, and returns only the four registry fields: `feed_id`,
  `profile_id`, `capture_epoch`, and `identity_sha256`.
- `PreparedFeedRegistration.report()` emits a bounded status with explicit
  `independently_authenticated=false`, `source_admitted=false`,
  `market_data_writable=false`, `trading_authorized=false`, and
  `final_action=HOLD`. It omits account aliases and profile contents.
- `register_local_feed(...)` is the explicit persistence operation. It
  validates the configured URL before opening a session, requires the exact
  `eurusd_oracle_local` confirmation, and then rechecks the connected database
  and restricted role.
- Registration inserts only a row in `oracle_mt5_feed_registry`. It never
  writes ticks, candles, quality events, credentials, or profile claims.
- Repeating the same registration is idempotent. A conflicting row is
  preserved and reported; the operation never overwrites it. Local lock and
  statement timeouts bound the transaction.
- No profile was registered during implementation. The sample HFM profile
  was used only in offline tests; no MT5 terminal or broker account was used.

## Verification and limits

- Focused feed identity, registry, migration, storage, repository, profile
  clock, factory, Council, and context suite: **201 passed**, 15 subtests
  passed, with 4 existing pytest collection warnings.
- `git diff --check` passed.
- Synthetic SQLite tests cover minimal fields, idempotence, conflict refusal,
  exact database confirmation, and rejection of archive/remote/SQLite URLs.
- Registration records deterministic local identity, not authentication,
  custody, source admission, or proof of broker time. Feed-labelled market
  writes remain blocked; independent gates remain 0/11 and source-family
  admissions 0/13; `FINAL=HOLD`.

## Next

1. Keep registration separate from admission. Require independently reviewed
   broker/source evidence before any admission state can be represented.
2. Design and rehearse a coordinated feed-aware writer and candle-key
   cutover on a disposable clone; do not modify `eurusd_oracle_archive`.
3. Run Windows MT5 and Stage 7/11 acceptance checks.

