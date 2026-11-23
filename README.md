# alphaquick

Read [AlphaQuick](https://alphaquick.io) market data files in Python:

- **L2 depth & trades**: every depth update and trade of one instrument for a
  day, from Binance, Bybit, OKX, Hyperliquid, Lighter, Aster, Upbit, Bithumb and
  dYdX.
- **Hyperliquid order log** (Level 3): every order and every trade of one
  instrument for a day, with accounts and queue positions. It covers
  perpetuals, HIP-3 markets, spot and HIP-4 outcome (prediction) markets.

The package reads the files, rebuilds the order book (price levels for L2;
individual orders in queue order for the order log) and turns them into
pandas DataFrames or Parquet. It also has a command-line tool for looking into
a file without writing code.

```bash
pip install "alphaquick[pandas] @ git+https://github.com/alphaquick/alphaquick-python"
```

Python 3.10 or later. The only required dependency is `lz4`; `[pandas]` adds
pandas and pyarrow for DataFrames and Parquet.

## Try it without an account

The free sample files need no account. List them, fetch one, look inside:

```console
$ alphaquick samples
$ alphaquick samples fetch orderlog-hip4
~/.cache/alphaquick/samples/HIP4_3600_Fed-Oct-no-change_HLC_20261023.bin.lz4
$ alphaquick info ~/.cache/alphaquick/samples/HIP4_3600_Fed-Oct-no-change_HLC_20261023.bin.lz4
HIP4_3600_Fed-Oct-no-change_HLC_20261023.bin.lz4  (0.8 MB)
Order log: Federal Reserve's Open Market Committee October 2026 rate decision — No change (outcome), day 20261023, starts from block 1,193,705,504
HIP-4 outcome market 3600, question: Federal Reserve's Open Market Committee October 2026 rate decision
  sides: Yes / No; prices are Yes's, as shares of 1
241 orders resting at 00:00 UTC, 16,537 blocks, 23,765 order events, 3,279 trades, 82 accounts
blocks from 2026-10-22 23:59:59.773167 to 2026-10-23 23:59:59.679527 UTC
order events: open 8,997, insufficientSpotBalanceRejected 5,738, canceled 5,649, filled 3,326, badAloPxRejected 30, iocCancelRejected 14
trades: first book 183, merge 734, mint 552, second book 1,810
book at the end: best bid 0.93451, best ask 0.93452, 213 bid / 39 ask orders
```

Whole days of other instruments (BTC, `xyz:CL`, Binance BTCUSDT, …) are on
[alphaquick.io/data](https://alphaquick.io/data) under **Free samples**. The
full catalogue needs a subscription; see
[Market data downloads](https://docs.alphaquick.io/data/overview).

## Command line

```console
$ alphaquick info FILE                           # what the file holds
$ alphaquick book FILE --at 12:00 --queue 3      # the book at 12:00 UTC, with the queue at the best prices
$ alphaquick dump FILE --type trade --limit 5    # records as JSON lines, for any other tool
$ alphaquick to-parquet FILE -o out/ --orders    # trades (and order events, or L2 quotes) as Parquet
$ alphaquick samples [fetch NAME]                # the free samples
```

```console
$ alphaquick book xyz_CL_HLC_20261101.bin.lz4 --at 12:00 --levels 3 --queue 2
book at 2026-11-01 12:00:00.000000 UTC
 orders         bid size            bid | ask            ask size         orders
      1          111.247         102.58 | 102.59         848.857          15
      4           13.918         102.57 | 102.6          270.685          12
      8          109.375         102.56 | 102.61         659.845          8

queue at the best bid 102.58, front first:
    1.        111.247  0x7fdafde5cfb5465924316eced2d3715494c517d1  oid 591900215220

queue at the best ask 102.59, front first:
    1.        518.792  0xce975678a14f17a15c946b95704744cd7c677e78  oid 591898455859
    2.          4.875  0x1093158be05ea51ea3c505ea03390569044a7f3f  oid 591898464249
```

## In Python

Prices, sizes and positions are integers × 10⁸ (`alphaquick.SCALE`), exact.
Record times are .NET ticks (100 ns since 0001-01-01 UTC);
`aq.ticks_to_datetime` converts them.

### L2 depth & trades

```python
import alphaquick as aq
from alphaquick import l2

book = aq.L2Book()
for event in aq.read_l2("BTCUSDT_BINANCE_USDM_20261101.bin.lz4"):
    if type(event) is l2.Trade:
        print(event.datetime, event.price / aq.SCALE, event.volume / aq.SCALE, event.side)
    else:
        book.apply(event)          # volume is the new size at the level; 0 removes it
        if book.consistent:        # the end of a batch: the book is whole here
            bid, ask = book.best_bid(), book.best_ask()
```

`l2.book_at(path, aq.datetime_to_ticks(moment))` gives the book at a moment.
`aq.L2Book(uncross=True)` drops the levels a new level crosses, as Depth
Replay does for feeds that never delete swept levels.

### Order log: the book, queues and accounts

```python
from datetime import datetime, timezone

import alphaquick as aq
from alphaquick import orderlog as ol

replay = aq.Replay("xyz_CL_HLC_20261101.bin.lz4")
noon = aq.datetime_to_ticks(datetime(2026, 11, 1, 12, tzinfo=timezone.utc))
replay.run(until=noon)                      # the book as it stood at 12:00 UTC

book = replay.book
best = book.best_bid()
for place, order in enumerate(book.queue(ol.BID, best)):
    print(place, order.size / aq.SCALE, replay.address(order.user_id), order.oid)
print(book.depth(ol.ASK, 5))                # [(price, total size, number of orders), ...]
```

Iterating a `Replay` yields every record after it has been applied, so you can
watch the book change:

```python
replay = aq.Replay(path)
for record in replay:
    if type(record) is ol.Order and record.oid == my_oid:
        print(record.status_name, replay.book.queue_position(my_oid))  # (orders ahead, size ahead)
    elif type(record) is ol.Trade:
        maker = replay.address(record.maker_user_id)
```

`aq.read_orderlog(path)` yields the records without keeping a book: `Meta`,
`Block`, `User`, `SnapOrder`, `Order`, `Trade` and `Outcome`. The status, time
in force and order type codes are in `ol.Status`, `ol.Tif` and `ol.OrderType`;
`ol.status_name(code)` gives Hyperliquid's name.

### HIP-4 outcome (prediction) markets

A file with both books of an outcome market (`replay.meta.is_outcome`) is
written in the first side's terms: every price is the first side's ("Yes"), as
a share of 1, and an order of the second side's book is shown flipped at
`1 − price` with `ol.ORDER_SECOND_SIDE` set. `ol.as_placed(side, price, flags)`
undoes that. For each trade, `replay.last_trade_kind` says what it did:
`MINT` (a buy on each book created a pair), `MERGE` (a sell on each redeemed
one), `FIRST_BOOK` or `SECOND_BOOK` (tokens changed hands on one book).
`replay.outcome` holds the market's name, its sides and, once settled, the
result. Positions in a trade (`maker_position`, `taker_position`) are in the
token of each order's own book.

### pandas and Parquet

```python
from alphaquick import frames

trades = frames.orderlog_trades(path)     # time, block, price, size, taker_side, maker, taker, oids, positions
orders = frames.orderlog_orders(path, include_rejects=False)
quotes = frames.l2_quotes(l2_path)        # best bid and ask after each consistent update
book = frames.book_frame(replay.book, replay.accounts)   # every resting order with its place in the queue
trades.to_parquet("trades.parquet")
```

Floats by default; `exact=True` keeps the integers × 10⁸.

`replay.run(until=…)` always reads the file's first block, which holds the book
at 00:00 UTC, so a moment before the day gives the day-start book. A truncated
file (an interrupted download) raises `alphaquick.TruncatedFileError` after
yielding everything before the cut; `alphaquick info` and `book` report what
it holds, with a warning.

## Speed

One core of a recent laptop, Python 3.13:

Sizes are in MB of 1024 × 1024 bytes, as on alphaquick.io.

| File | Size | Reading every record | Rebuilding the order book |
|---|---|---|---|
| Order log, `xyz:CL`, one day | 88 MB | 2.4 s (5.3 million records) | 5.0 s |
| L2, Hyperliquid `BTC`, one day | 9 MB | 0.3 s (650,000 records) | |

Time grows with the number of records; a day of BTC (2 GB of order log) takes a
few minutes. Files are read one block at a time, so memory stays small apart
from the book and what you keep. For large backtests, the Rust crate below
reads the same files much faster.

## The formats

Both formats share one container: an `int32` size hint, then length-prefixed
frames, each one raw LZ4 block (or stored bytes) in the framing of the
`K4os.Compression.LZ4` pickler. That is not the LZ4 *frame* format, so the
`lz4` command line tool cannot read these files; this package (or any LZ4
library's raw block decoder) can. The full specifications:

- [L2 depth & trades](https://docs.alphaquick.io/data/l2-depth-trades)
- [Hyperliquid order log](https://docs.alphaquick.io/data/hyperliquid-order-log)

The tests check the package against the numbers AlphaQuick's own replay tools
produce for the free samples: record counts, the final book, the queue at the
best price and, for the HIP-4 sample, every trade's kind.

## Also

- [alphaquick-examples](https://github.com/alphaquick/alphaquick-examples):
  ready-to-run scripts (bars and spreads, the book at a moment, the queue
  position of an order, following an account, maker and taker volume, HIP-4
  mints and merges, live feed clients).
- [alphaquick-rust](https://github.com/alphaquick/alphaquick-rust): the same
  readers as a Rust crate.
- [Documentation](https://docs.alphaquick.io/data/libraries) ·
  [Order-Log Replay in the browser](https://alphaquick.io/orderlog) ·
  [Data catalogue](https://alphaquick.io/data)

## Development

```bash
python -m venv .venv && .venv/bin/pip install -e ".[pandas,test]"
.venv/bin/pytest                                   # unit tests; the sample tests run when the samples are cached
ALPHAQUICK_FETCH_SAMPLES=1 .venv/bin/pytest        # also downloads the small samples (about 20 MB)
```

MIT licence. The data itself is covered by your AlphaQuick plan's terms.
