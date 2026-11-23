"""Files as pandas DataFrames. Needs ``pip install "alphaquick[pandas]"``.

Every frame of records has a ``time`` column of UTC timestamps (nanosecond
resolution); :func:`book_frame` is a book at one moment and has none.
Prices, sizes and positions are floats by default; pass ``exact=True`` to keep
the scaled integers (x10**8) for exact sums.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, Optional

from . import l2, orderlog
from ._container import Source
from ._time import SCALE, TICKS_AT_UNIX_EPOCH

if TYPE_CHECKING:  # pragma: no cover
    import pandas as pd


def _pandas():
    try:
        import pandas
    except ImportError:  # pragma: no cover - depends on the environment
        raise ImportError('alphaquick.frames needs pandas: pip install "alphaquick[pandas]"') from None
    return pandas


def _frame(rows: list, columns: list[str], scaled: Iterable[str], exact: bool, time_ticks: bool = True) -> "pd.DataFrame":
    pd = _pandas()
    frame = pd.DataFrame(rows, columns=columns)
    if time_ticks:
        frame["time"] = pd.to_datetime((frame["time"].astype("int64") - TICKS_AT_UNIX_EPOCH) * 100, unit="ns", utc=True)
    if not exact:
        for column in scaled:
            frame[column] = frame[column] / SCALE
    return frame


_SIDES = {l2.SIDE_BUY: "buy", l2.SIDE_SELL: "sell", l2.SIDE_UNKNOWN: ""}


def l2_trades(source: Source, *, exact: bool = False) -> "pd.DataFrame":
    """Every trade of an L2 file: ``time, price, volume, side, trade_id``. ``side`` is the aggressor."""
    rows = [(t.time, t.price, t.volume, _SIDES.get(t.side, ""), t.trade_id)
            for t in l2.read_l2(source) if type(t) is l2.Trade]
    return _frame(rows, ["time", "price", "volume", "side", "trade_id"], ("price", "volume"), exact)


def l2_quotes(source: Source, *, exact: bool = False, uncross: bool = False) -> "pd.DataFrame":
    """Best bid and ask after each update that ends a transaction (the book is consistent there).

    Columns ``time, bid, bid_size, ask, ask_size``; a row is written only while
    both sides have a level.
    """
    book = l2.L2Book(uncross=uncross)
    rows = []
    for event in l2.read_l2(source):
        if type(event) is not l2.DepthUpdate:
            continue
        book.apply(event)
        if book.consistent:
            bid, ask = book.bids.best(), book.asks.best()
            if bid is not None and ask is not None:
                rows.append((event.time, bid, book.bids.size[bid], ask, book.asks.size[ask]))
    return _frame(rows, ["time", "bid", "bid_size", "ask", "ask_size"], ("bid", "bid_size", "ask", "ask_size"), exact)


def orderlog_trades(source: Source, *, exact: bool = False) -> "pd.DataFrame":
    """Every trade of an order-log file, with both accounts.

    Columns: ``time, block, price, size, taker_side`` (``buy``/``sell``),
    ``maker, taker`` (addresses), ``maker_oid, taker_oid`` (None when not set),
    ``maker_position, taker_position`` (before the trade), ``flags`` and, for
    a HIP-4 outcome file with both books, ``kind`` (``mint``, ``merge``,
    ``first_book``, ``second_book`` or ``unknown``). In such a file each
    position is in the token of the order's own book, while ``taker_side``
    and ``price`` are in the first side's terms (see ``orderlog.as_placed``).
    """
    replay = orderlog.Replay(source)
    rows = []
    for record in replay:
        if type(record) is orderlog.Trade:
            rows.append((
                record.time, replay.block.number if replay.block else None, record.price, record.size,
                "buy" if record.taker_side == orderlog.BID else "sell",
                replay.address(record.maker_user_id), replay.address(record.taker_user_id),
                record.maker_oid if record.flags & orderlog.TRADE_MAKER_OID else None,
                record.taker_oid if record.flags & orderlog.TRADE_TAKER_OID else None,
                record.maker_position, record.taker_position, record.flags,
                replay.last_trade_kind.name.lower() if replay.meta and replay.meta.is_outcome else None,
            ))
    columns = ["time", "block", "price", "size", "taker_side", "maker", "taker", "maker_oid", "taker_oid",
               "maker_position", "taker_position", "flags", "kind"]
    frame = _frame(rows, columns, ("price", "size", "maker_position", "taker_position"), exact)
    # Order ids can be missing: nullable integers keep them exact.
    frame["maker_oid"] = frame["maker_oid"].astype("Int64")
    frame["taker_oid"] = frame["taker_oid"].astype("Int64")
    if replay.meta is None or not replay.meta.is_outcome:
        frame = frame.drop(columns=["kind"])
    return frame


def orderlog_orders(source: Source, *, exact: bool = False, include_rejects: bool = True) -> "pd.DataFrame":
    """Every order event of an order-log file.

    Columns: ``time, block, status`` (Hyperliquid's name), ``side``
    (``bid``/``ask``), ``tif, order_type, flags, oid, price, size`` (unfilled
    at the event), ``account``. Rejected orders make up a large share of a
    busy file; ``include_rejects=False`` leaves them out.
    """
    replay = orderlog.Replay(source)
    rows = []
    rejects = orderlog.REJECT_STATUSES
    tif_names = {t.value: t.name for t in orderlog.Tif}
    type_names = {t.value: t.name for t in orderlog.OrderType}
    for record in replay:
        if type(record) is orderlog.Order:
            if not include_rejects and record.status in rejects:
                continue
            rows.append((
                record.time, replay.block.number if replay.block else None, orderlog.status_name(record.status),
                "bid" if record.side == orderlog.BID else "ask", tif_names.get(record.tif, str(record.tif)),
                type_names.get(record.order_type, str(record.order_type)), record.flags, record.oid,
                record.price, record.size, replay.address(record.user_id),
            ))
    columns = ["time", "block", "status", "side", "tif", "order_type", "flags", "oid", "price", "size", "account"]
    return _frame(rows, columns, ("price", "size"), exact)


def book_frame(book: orderlog.OrderBook, accounts: Optional[dict[int, str]] = None, *, exact: bool = False) -> "pd.DataFrame":
    """Every resting order of an order-level book: ``side, price, queue, size, account, oid``.

    ``queue`` is the place in its price's queue, 0 at the front.
    """
    rows = []
    for side in (orderlog.BID, orderlog.ASK):
        for price, _total, _count in book.depth(side, None):
            for place, order in enumerate(book.queue(side, price)):
                account = accounts.get(order.user_id, "?") if accounts else order.user_id
                rows.append(("bid" if side == orderlog.BID else "ask", price, place, order.size, account, order.oid))
    return _frame(rows, ["side", "price", "queue", "size", "account", "oid"], ("price", "size"), exact, time_ticks=False)
