# Changelog

## 0.2.0 (2026-11-25)

Reads the files of the current order-log converter (`hyperliquid/hlc_v2/`)
and the L2 continuity files.

Order log:

- Statuses 24 `vaultWithdrawalCanceled`, 26 `delistedCanceled` and 27
  `insufficientWeiCanceled` remove the order; 25 `perpMaxPositionRejected`
  and 28 `priorityAloReduceOnlyRejected` are rejections. `REMOVING_STATUSES`,
  `REJECT_STATUSES`, `Status` and `STATUS_NAMES` now match the converter for
  every code. Before, orders that ended with 27 stayed in the book (on
  17 November 2026 the PURR/USDC book was crossed after most blocks), and
  `frames.orderlog_orders(include_rejects=False)` kept 25 and 28.
- Order flags `ORDER_RO_CAP` (0x10), `ORDER_REBUILT` (0x20) and
  `ORDER_SNAPSHOT_FIX` (0x40), with what statuses 250-252 mean with each:
  most 251 and 252 records are regular events (rebuilt Hyperliquidity
  orders, triggered limit orders that start resting, reduce-only caps), not
  corrections.
- `Trade.direction` and `Trade.is_system_print` (direction 3: a trade booked
  without a match in the book, which exchange candles do not count), and a
  `system_print` column in `frames.orderlog_trades`.
- HIP-4 outcome files: the book keeps one queue per side book at each price
  (`OrderBook(outcome=True)`, set by `Replay`), so `queue_position` counts
  only orders of the same book. `queue(side, price, second_side=False)` picks
  the book; `frames.book_frame` gains a `second_side` column for such books,
  and `alphaquick book --queue` lists both queues.
- A file named `HIP4_<id>_HLC_<day>.bin.lz4` (no short name) is an outcome
  file, not the spot pair `HIP4/<id>`.
- Documented: `Block.local_time_ns` equals the block time; `Meta.dex` (254
  for spot and HIP-4, `DEX_SPOT`) and the `HIP4:<id>` coin; the 400 ms limit
  of queue order (priority fees); the `hip4/` folder.

L2:

- Gaps: `read_continuity`, and `read_l2(..., continuity=path | lines | "auto")`
  yields an `l2.Gap` event for each gap in the feed, before the first record
  at or after its time and never after the update with CLEAR that ends it.
  `L2Book.known` is False from a gap until that update (`L2Book.apply_gap`,
  or `apply` given a `Gap`); use `book.consistent and book.known`.
  `l2.book_at` and `frames.l2_quotes` read the continuity file next to the
  data file by default; `l2_quotes` writes no rows inside a gap and a row
  with the quotes missing at the gap (with `exact=True` its quote columns are
  now nullable `Int64`). `alphaquick book` warns when the moment asked is
  inside a gap, `alphaquick info` counts the gaps; both take `--continuity`.
- `l2.book_at` (and `alphaquick book`) decide where to stop on depth updates
  only: a trade stamped a little later no longer ends the book early.
  `l2.replay_into` applies events to a book up to a moment.
- The venue `HYPERLIQUID_BBO` in file names.
- Corrected: `trade_id` is the trade's number in the file, not the venue's id
  (it can repeat in files up to 2026-11-25); trade sides are 1 or 2 only;
  `uncross` is for BINANCE_USDM and ASTER files up to 2026-11-25, which hold
  levels never removed, and does not remove the stale levels deeper in the
  book; files up to 2026-11-25 hold prices and sizes to 6 decimal places.

## 0.1.0 (2026-11-23)

First release.

- Read both AlphaQuick file formats: the shared LZ4 container
  (`iter_blocks`, `iter_raw_records`), L2 depth & trades (`read_l2`) and the
  Hyperliquid order log (`read_orderlog`), with typed records.
- `L2Book`: the price-level book, with an optional uncross rule; `l2.book_at`.
- `OrderBook` and `Replay`: the order-level book with each price's queue,
  queue positions, accounts, the book at a moment (`Replay.run(until=...)`).
- HIP-4 outcome markets with both books: `OutcomeInfo`, `as_placed`, and the
  kind of each trade (mint, merge, first book, second book).
- File names (`names`), the free samples (`samples.fetch`), pandas and Parquet
  (`frames`), and the `alphaquick` command (`info`, `book`, `dump`,
  `to-parquet`, `samples`).
