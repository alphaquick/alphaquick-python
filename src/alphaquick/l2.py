"""L2 depth & trades files: every depth update and trade of one instrument for one day.

Records (all little-endian, after the 12-byte header ``kind, size, time``):

* depth update, kind 0, 29 bytes: ``price, volume`` (int64, x10**8) and ``flags``
  (uint8: 1 bid, 2 ask, 4 clear the book first, 8 end of transaction);
* trade, kind 1, 37 bytes: ``id, price, volume`` (int64) and ``side`` (uint8:
  aggressor 1 buy, 2 sell, 0 not reported).

``volume`` of a depth update is the new size at the level, not a change; 0
removes the level. Order-log files carry the same day as an aggregated L2 view
in these two record kinds, so :func:`read_l2` reads them too.

See https://docs.alphaquick.io/data/l2-depth-trades#format-of-binlz4
"""

from __future__ import annotations

import struct
from typing import Iterator, NamedTuple, Union

from ._container import _HEAD, FormatError, Source, iter_blocks
from ._levels import Levels
from ._time import ticks_to_datetime

KIND_DEPTH = 0
KIND_TRADE = 1

#: Depth flags.
BUY = 1
SELL = 2
CLEAR = 4
END_OF_TRANSACTION = 8

#: Trade sides (the aggressor).
SIDE_UNKNOWN = 0
SIDE_BUY = 1
SIDE_SELL = 2

_DEPTH = struct.Struct("<hHqqqB")  # kind, size, time, price, volume, flags   (29 bytes)
_TRADE = struct.Struct("<hHqqqqB")  # kind, size, time, id, price, volume, side (37 bytes)


class DepthUpdate(NamedTuple):
    """One level of the book changed. ``volume`` is the new size; 0 removes the level."""

    time: int  #: 100 ns ticks since 0001-01-01 UTC
    price: int  #: x10**8
    volume: int  #: x10**8, the new size at the level
    flags: int  #: BUY / SELL / CLEAR / END_OF_TRANSACTION

    @property
    def is_bid(self) -> bool:
        return bool(self.flags & BUY)

    @property
    def clears_book(self) -> bool:
        """The book is discarded before this update: a fresh snapshot starts here."""
        return bool(self.flags & CLEAR)

    @property
    def ends_transaction(self) -> bool:
        """The last update of a batch: the book is consistent after it."""
        return bool(self.flags & END_OF_TRANSACTION)

    @property
    def datetime(self):
        return ticks_to_datetime(self.time)


class Trade(NamedTuple):
    """One trade print."""

    time: int  #: 100 ns ticks since 0001-01-01 UTC
    trade_id: int  #: the venue's trade id, where it provides one
    price: int  #: x10**8
    volume: int  #: x10**8
    side: int  #: aggressor: SIDE_BUY, SIDE_SELL or SIDE_UNKNOWN

    @property
    def datetime(self):
        return ticks_to_datetime(self.time)


L2Event = Union[DepthUpdate, Trade]


def read_l2(source: Source, *, include_non_market: bool = False) -> Iterator[L2Event]:
    """Yield every depth update and trade of an L2 file, in file order.

    Trades whose price or volume is not positive are not market prints and are
    skipped unless ``include_non_market`` is set. Other record kinds (the
    order-level records of an order-log file, kinds this version does not
    know) are skipped.
    """
    depth, trade = _DEPTH.unpack_from, _TRADE.unpack_from
    make_depth, make_trade = DepthUpdate._make, Trade._make
    head = _HEAD.unpack_from
    for data in iter_blocks(source):
        pos, end = 0, len(data)
        while pos < end:
            if pos + 12 > end:
                raise FormatError(f"a record header is cut off at the end of a block ({end - pos} bytes left)")
            kind, size = head(data, pos)
            if size < 12 or pos + size > end:
                raise FormatError(f"record of kind {kind} at offset {pos} has an invalid size {size}")
            at, pos = pos, pos + size
            if kind == KIND_DEPTH:
                if size < 29:
                    raise FormatError(f"depth record of {size} bytes")
                yield make_depth(depth(data, at)[2:])
            elif kind == KIND_TRADE:
                if size < 37:
                    raise FormatError(f"trade record of {size} bytes")
                fields = trade(data, at)
                if include_non_market or (fields[4] > 0 and fields[5] > 0):
                    yield make_trade(fields[2:])


class L2Book:
    """A price-level order book rebuilt from depth updates.

    Apply every :class:`DepthUpdate` in order with :meth:`apply`. Sizes and
    prices stay scaled integers (x10**8).

    ``uncross`` removes the opposite levels a new level crosses (a bid at or
    above the best ask, and the reverse). The format does not need it, but some
    venues' feeds never delete levels a move swept, which leaves a crossed
    book; Depth Replay applies this rule.
    """

    def __init__(self, *, uncross: bool = False) -> None:
        self.bids = Levels(descending=True)
        self.asks = Levels(descending=False)
        self.uncross = uncross
        #: True after an update that ends a transaction, False in the middle of one.
        self.consistent = True
        #: Time of the last update applied (ticks), or None.
        self.time: int | None = None

    def apply(self, update: DepthUpdate) -> None:
        if update.flags & CLEAR:
            self.bids.clear()
            self.asks.clear()
        side = self.bids if update.flags & BUY else self.asks
        side.set(update.price, update.volume)
        if self.uncross and update.volume > 0:
            if update.flags & BUY:
                self.asks.remove_through(update.price)
            else:
                self.bids.remove_through(update.price)
        self.consistent = bool(update.flags & END_OF_TRANSACTION)
        self.time = update.time

    def clear(self) -> None:
        self.bids.clear()
        self.asks.clear()

    def best_bid(self) -> tuple[int, int] | None:
        """``(price, size)`` of the best bid, or None."""
        price = self.bids.best()
        return None if price is None else (price, self.bids.size[price])

    def best_ask(self) -> tuple[int, int] | None:
        """``(price, size)`` of the best ask, or None."""
        price = self.asks.best()
        return None if price is None else (price, self.asks.size[price])

    def mid(self) -> float | None:
        """Mid price as a scaled float (divide by SCALE for the price), or None."""
        bid, ask = self.bids.best(), self.asks.best()
        return None if bid is None or ask is None else (bid + ask) / 2

    def spread(self) -> int | None:
        bid, ask = self.bids.best(), self.asks.best()
        return None if bid is None or ask is None else ask - bid

    def depth(self, levels: int | None = 10) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
        """``(bids, asks)``, each ``[(price, size), ...]`` from the best price outwards."""
        return self.bids.top(levels), self.asks.top(levels)


def book_at(source: Source, until: int, *, uncross: bool = False) -> L2Book:
    """The book as it stood at a moment: every depth update up to and including ``until`` (ticks).

    Use :func:`alphaquick.datetime_to_ticks` to turn a datetime into ticks.
    The book may be in the middle of a transaction; check ``book.consistent``.
    Records are in the order they were received, and venue times can step
    back slightly; this stops at the first record later than ``until``.
    """
    book = L2Book(uncross=uncross)
    for event in read_l2(source):
        if event.time > until:
            break
        if type(event) is DepthUpdate:
            book.apply(event)
    return book
