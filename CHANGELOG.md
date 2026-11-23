# Changelog

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
