"""Hyperliquid order-log files (HLC): every order and trade of one instrument for one day.

A file holds, in order: a META record (what the file is), for a HIP-4 outcome
market with both books an OUTCOME record, then the BLOCK the day starts from
(timed just before 00:00 UTC) followed by the resting book at 00:00 UTC as
SNAPORDER records in queue order, then each block of the day: a BLOCK record
followed by that block's ORDER events and TRADEs. USER records name each
account before its first use, and carry the time of the block it first
appears in, so the BLOCK records are the clock.

Rebuild the book with :class:`Replay` (or :class:`OrderBook` directly)::

    replay = Replay("BTC_HLC_20261101.bin.lz4")
    for record in replay:          # each record is applied, then yielded
        ...
    replay.book.best_bid()

See https://docs.alphaquick.io/data/hyperliquid-order-log#format
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Iterator, NamedTuple, Optional, Union

from ._container import _HEAD, FormatError, Source, iter_blocks
from ._levels import Levels
from ._time import SCALE, ticks_to_datetime, unix_ns_to_datetime

# --------------------------------------------------------------------------
# Record kinds and codes
# --------------------------------------------------------------------------

KIND_META = 16
KIND_BLOCK = 17
KIND_ORDER = 18
KIND_TRADE = 19
KIND_SNAPORDER = 20
KIND_USER = 21
KIND_OUTCOME = 22

#: Sides.
BID = 1
ASK = 2

#: META flags.
META_OUTCOME = 1  #: a HIP-4 outcome market with both books in one file

#: Order flags (ORDER and SNAPORDER).
ORDER_REDUCE_ONLY = 1
ORDER_TRIGGER = 2
ORDER_POSITION_TPSL = 4
ORDER_SECOND_SIDE = 0x80  #: outcome files: the order rests on the second side's book

#: Trade flags.
TRADE_MAKER_OID = 1  #: the maker order id is set
TRADE_TAKER_OID = 2  #: the taker order id is set
TRADE_MAKER_IS_BUYER = 4
TRADE_DIRECTION_SHIFT = 3  #: bits 3-4: Hyperliquid's direction override (0 none, 1 buy, 2 sell)
TRADE_MAKER_TWAP = 32
TRADE_TAKER_TWAP = 64
TRADE_SECOND_SIDE = 0x80  #: outcome files: the trade printed on the second side's book


class Status(IntEnum):
    """Order event statuses. The names are Hyperliquid's; 250-252 are corrections."""

    OPEN = 0
    FILLED = 1
    CANCELED = 2
    BAD_ALO_PX_REJECTED = 3
    PERP_MARGIN_REJECTED = 4
    MIN_TRADE_NTL_REJECTED = 5
    IOC_CANCEL_REJECTED = 6
    TOO_MANY_OPEN_ORDERS_REJECTED = 7
    INSUFFICIENT_SPOT_BALANCE_REJECTED = 8
    REDUCE_ONLY_CANCELED = 9
    REDUCE_ONLY_REJECTED = 10
    ORACLE_REJECTED = 11
    POSITION_INCREASE_AT_OPEN_INTEREST_CAP_REJECTED = 12
    SELF_TRADE_CANCELED = 13
    TRIGGERED = 14
    SIBLING_FILLED_CANCELED = 15
    POSITION_FLIP_AT_OPEN_INTEREST_CAP_REJECTED = 16
    OUTCOME_SETTLED_CANCELED = 17
    SCHEDULED_CANCEL = 18
    MARGIN_CANCELED = 19
    LIQUIDATED_CANCELED = 20
    TOO_AGGRESSIVE_AT_OPEN_INTEREST_CAP_REJECTED = 21
    OPEN_INTEREST_CAP_CANCELED = 22
    OPEN_INTEREST_INCREASE_REJECTED = 23
    CORRECTION_GONE = 250  #: the converter's correction: the order is gone
    CORRECTION_RESTS = 251  #: the converter's correction: the order rests
    CORRECTION_SIZE = 252  #: the converter's correction: the order's remaining size
    OTHER = 255  #: a status the format has no code for


#: Hyperliquid's own name of each status.
STATUS_NAMES = {
    0: "open", 1: "filled", 2: "canceled", 3: "badAloPxRejected", 4: "perpMarginRejected",
    5: "minTradeNtlRejected", 6: "iocCancelRejected", 7: "tooManyOpenOrdersRejected",
    8: "insufficientSpotBalanceRejected", 9: "reduceOnlyCanceled", 10: "reduceOnlyRejected",
    11: "oracleRejected", 12: "positionIncreaseAtOpenInterestCapRejected", 13: "selfTradeCanceled",
    14: "triggered", 15: "siblingFilledCanceled", 16: "positionFlipAtOpenInterestCapRejected",
    17: "outcomeSettledCanceled", 18: "scheduledCancel", 19: "marginCanceled",
    20: "liquidatedCanceled", 21: "tooAggressiveAtOpenInterestCapRejected",
    22: "openInterestCapCanceled", 23: "openInterestIncreaseRejected",
    250: "correction: gone", 251: "correction: rests", 252: "correction: size", 255: "other",
}

#: Statuses that remove the order from the book.
REMOVING_STATUSES = frozenset({1, 2, 9, 13, 15, 17, 18, 19, 20, 22, 250})
#: Statuses that are rejections: the order never rested.
REJECT_STATUSES = frozenset({3, 4, 5, 6, 7, 8, 10, 11, 12, 16, 21, 23})


class Tif(IntEnum):
    """Time in force. NONE marks a trigger order (stop or take-profit) that waits off the book."""

    NONE = 0
    GTC = 1
    ALO = 2  #: add liquidity only (post-only)
    IOC = 3
    FRONTEND_MARKET = 4
    LIQUIDATION_MARKET = 5
    OTHER = 255


class OrderType(IntEnum):
    LIMIT = 0
    MARKET = 1
    STOP_MARKET = 2
    STOP_LIMIT = 3
    TAKE_PROFIT_MARKET = 4
    TAKE_PROFIT_LIMIT = 5
    OTHER = 255


class TradeKind(IntEnum):
    """What a trade of a HIP-4 outcome market did (see :func:`trade_kind`)."""

    NONE = 0  #: not an outcome market
    FIRST_BOOK = 1  #: both orders on the first side's book: tokens changed hands
    SECOND_BOOK = 2  #: both orders on the second side's book
    MINT = 3  #: a buy on each book: a new pair of tokens was created
    MERGE = 4  #: a sell on each book: a pair was redeemed for its collateral
    UNKNOWN = 5  #: the maker order was not in the book, so its book is not known


def status_name(code: int) -> str:
    """Hyperliquid's name of a status code, or ``"status <code>"`` for one this version does not know."""
    return STATUS_NAMES.get(code, f"status {code}")


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------

_META = struct.Struct("<hHqIHBBHHIQQB")  # header, magic, version, szDecimals, dex, asset, flags, day, block, blockTimeNs, coinLen
_BLOCK = struct.Struct("<hHqQQQ")  # header, number, time ns, local time ns
_ORDER = struct.Struct("<hHqBBBBBQqqI")  # header, status, side, tif, type, flags, oid, px, sz, user
_TRADE = struct.Struct("<hHqBBqqQQIIqq")  # header, takerSide, flags, px, sz, makerOid, takerOid, makerUser, takerUser, makerPos, takerPos
_SNAPORDER = struct.Struct("<hHqBBBxQqqI")  # header, side, tif, flags, pad, oid, px, sz, user
_USER = struct.Struct("<hHqI20s")  # header, user id, address
_OUTCOME_HEAD = struct.Struct("<hHqH")  # header, version

_MAGIC = int.from_bytes(b"HLC1", "little")


class Meta(NamedTuple):
    """What the file is. The first record of every file."""

    time: int
    version: int
    size_decimals: int  #: size decimals of the instrument
    dex: int  #: dex index (0 for the main perpetuals)
    asset: int  #: asset index within the dex
    flags: int  #: META_OUTCOME
    day: int  #: the day, as the number YYYYMMDD
    start_block: int  #: the block the day starts from
    start_block_time_ns: int  #: that block's time, Unix nanoseconds
    coin: str  #: the coin (``xyz:CL``); for an outcome file with both books, the market's title cut to 64 bytes

    @property
    def is_outcome(self) -> bool:
        """A HIP-4 outcome market with both books in one file."""
        return bool(self.flags & META_OUTCOME)


class Block(NamedTuple):
    """A new block: the records after it, up to the next Block, happened in it."""

    time: int
    number: int
    time_ns: int  #: consensus block time, Unix nanoseconds: the clock to sequence by
    local_time_ns: int  #: when AlphaQuick's node wrote the block, Unix nanoseconds

    @property
    def datetime(self):
        return unix_ns_to_datetime(self.time_ns)


class User(NamedTuple):
    """An account. Each appears once, before the first record that refers to it."""

    time: int
    user_id: int  #: a number local to the file
    address: str  #: ``0x`` and 40 hex digits


class SnapOrder(NamedTuple):
    """One order of the book at 00:00 UTC. The records come in queue order."""

    time: int
    side: int  #: BID or ASK
    tif: int
    flags: int
    oid: int
    price: int  #: x10**8
    size: int  #: remaining size, x10**8
    user_id: int


class Order(NamedTuple):
    """An order event: one change of state of one order."""

    time: int
    status: int  #: a :class:`Status` code (kept as int: a newer file can carry new codes)
    side: int  #: BID or ASK
    tif: int  #: a :class:`Tif` code
    order_type: int  #: an :class:`OrderType` code
    flags: int
    oid: int
    price: int  #: x10**8
    size: int  #: the order's unfilled size at the event, x10**8
    user_id: int

    @property
    def status_name(self) -> str:
        return status_name(self.status)


class Trade(NamedTuple):
    """A fill between a maker and a taker order."""

    time: int
    taker_side: int  #: BID: the taker bought; ASK: the taker sold
    flags: int
    price: int  #: x10**8
    size: int  #: x10**8
    maker_oid: int
    taker_oid: int
    maker_user_id: int
    taker_user_id: int
    maker_position: int  #: the maker's position before the trade, x10**8
    taker_position: int  #: the taker's position before the trade, x10**8

    @property
    def has_maker_oid(self) -> bool:
        return bool(self.flags & TRADE_MAKER_OID)

    @property
    def has_taker_oid(self) -> bool:
        return bool(self.flags & TRADE_TAKER_OID)

    @property
    def on_second_side(self) -> bool:
        """Outcome files: the trade printed on the second side's book."""
        return bool(self.flags & TRADE_SECOND_SIDE)


class Outcome(NamedTuple):
    """A HIP-4 outcome market's description (outcome files with both books only)."""

    time: int
    version: int
    text: str  #: UTF-8, one ``key=value`` per line

    def info(self) -> "OutcomeInfo":
        return OutcomeInfo.parse(self.text)


OrderLogRecord = Union[Meta, Block, User, SnapOrder, Order, Trade, Outcome]


def read_orderlog(source: Source) -> Iterator[OrderLogRecord]:
    """Yield every order-log record of a file, in file order.

    The aggregated L2 view some files also carry (record kinds 0 and 1, read
    with :func:`alphaquick.l2.read_l2`) and kinds this version does not know
    are skipped.
    """
    meta, block, order, trade = _META.unpack_from, _BLOCK.unpack_from, _ORDER.unpack_from, _TRADE.unpack_from
    snap, user, outcome_head = _SNAPORDER.unpack_from, _USER.unpack_from, _OUTCOME_HEAD.unpack_from
    make_block, make_order, make_trade, make_snap = Block._make, Order._make, Trade._make, SnapOrder._make
    head = _HEAD.unpack_from
    for buf in iter_blocks(source):
        pos, end = 0, len(buf)
        while pos < end:
            if pos + 12 > end:
                raise FormatError(f"a record header is cut off at the end of a block ({end - pos} bytes left)")
            kind, size = head(buf, pos)
            if size < 12 or pos + size > end:
                raise FormatError(f"record of kind {kind} at offset {pos} has an invalid size {size}")
            at, pos = pos, pos + size
            if kind == KIND_ORDER:
                _need(kind, size, 45)
                yield make_order(order(buf, at)[2:])
            elif kind == KIND_TRADE:
                _need(kind, size, 70)
                yield make_trade(trade(buf, at)[2:])
            elif kind == KIND_BLOCK:
                _need(kind, size, 36)
                yield make_block(block(buf, at)[2:])
            elif kind == KIND_SNAPORDER:
                _need(kind, size, 44)
                yield make_snap(snap(buf, at)[2:])
            elif kind == KIND_USER:
                _need(kind, size, 36)
                _, _, time, user_id, address = user(buf, at)
                yield User(time, user_id, "0x" + address.hex())
            elif kind == KIND_META:
                _need(kind, size, 45)
                fields = meta(buf, at)
                if fields[3] != _MAGIC:
                    raise FormatError("META record without the HLC1 magic: not an order-log file")
                coin_len = fields[12]
                if 45 + coin_len > size:
                    raise FormatError("META coin name runs past the record")
                coin = bytes(buf[at + 45:at + 45 + coin_len]).decode("utf-8", "replace")
                yield Meta(fields[2], *fields[4:12], coin)
            elif kind == KIND_OUTCOME:
                _need(kind, size, 14)
                _, _, time, version = outcome_head(buf, at)
                text = bytes(buf[at + 14:at + size]).decode("utf-8", "replace")
                yield Outcome(time, version, text)


def _need(kind: int, size: int, minimum: int) -> None:
    if size < minimum:
        raise FormatError(f"record of kind {kind} has {size} bytes, fewer than its {minimum}")


# --------------------------------------------------------------------------
# HIP-4 outcome markets
# --------------------------------------------------------------------------

#: One whole unit of collateral per pair, in the format's fixed point.
ONE = SCALE


@dataclass
class OutcomeInfo:
    """What an OUTCOME record says about the market. Keys a version does not know are ignored."""

    outcome: Optional[int] = None  #: the outcome id (the coin ``#N`` is the id times ten plus the side)
    title: str = ""  #: the market as Hyperliquid's app names it
    sides: tuple[str, str] = ("Yes", "No")  #: names of the first and the second side
    question_id: Optional[int] = None
    question: str = ""  #: the question the outcome answers, when it is one of several
    template: str = ""
    fields: list[tuple[str, str]] = field(default_factory=list)
    venue: str = ""
    deployer: str = ""
    settled: Optional[float] = None  #: the first side's settlement value (1, 0 or 0.5), when settled
    settle_detail: str = ""

    @classmethod
    def parse(cls, text: str) -> "OutcomeInfo":
        info = cls()
        sides = ["", ""]
        for line in text.splitlines():
            key, sep, value = line.partition("=")
            if not sep:
                continue
            key, value = key.strip(), value.strip()
            if key == "outcome":
                info.outcome = _int_or_none(value)
            elif key == "title":
                info.title = value
            elif key == "side0":
                sides[0] = value
            elif key == "side1":
                sides[1] = value
            elif key == "question_id":
                info.question_id = _int_or_none(value)
            elif key == "question":
                info.question = value
            elif key == "template":
                info.template = value
            elif key == "fields":
                info.fields = [tuple(pair.split(":", 1)) for pair in value.split("|") if ":" in pair]  # type: ignore[misc]
            elif key == "venue":
                info.venue = value
            elif key == "deployer":
                info.deployer = value
            elif key == "settled":
                try:
                    number = float(value)
                    info.settled = number if number == number and abs(number) != float("inf") else None
                except ValueError:
                    info.settled = None
            elif key == "settle_detail":
                info.settle_detail = value
        info.sides = (sides[0] or "Yes", sides[1] or "No")
        if not info.title:
            info.title = f"Outcome {info.outcome}" if info.outcome is not None else "Outcome market"
        return info


def _int_or_none(value: str) -> Optional[int]:
    try:
        return int(value)
    except ValueError:
        return None


def as_placed(side: int, price: int, flags: int) -> tuple[bool, int, int]:
    """An order of an outcome file as it was placed: ``(on_second_book, side, price)``.

    Outcome files store every order in the first side's terms: a buy of the
    second side at ``q`` is written as an ask at ``1 - q`` with
    ``ORDER_SECOND_SIDE``. This undoes that: a buy of "No" at 0.07698 comes
    back as ``(True, BID, 7_698_000)``.
    """
    if flags & ORDER_SECOND_SIDE:
        return True, (ASK if side == BID else BID), ONE - price
    return False, side, price


def trade_kind(trade_flags: int, taker_side: int, maker_flags: Optional[int]) -> TradeKind:
    """What a trade of an outcome file did.

    ``maker_flags`` are the maker order's flags as it rests in the book before
    the trade (None when it is not there). Both orders on one book exchanged
    tokens; a taker and maker on different books minted a pair when the
    taker's order, as placed, was a buy, and merged one when it was a sell.
    """
    if maker_flags is None:
        return TradeKind.UNKNOWN
    taker_second = bool(trade_flags & TRADE_SECOND_SIDE)
    maker_second = bool(maker_flags & ORDER_SECOND_SIDE)
    if not taker_second and not maker_second:
        return TradeKind.FIRST_BOOK
    if taker_second and maker_second:
        return TradeKind.SECOND_BOOK
    placed = (ASK if taker_side == BID else BID) if taker_second else taker_side
    return TradeKind.MINT if placed == BID else TradeKind.MERGE


# --------------------------------------------------------------------------
# The order-level book
# --------------------------------------------------------------------------


class RestingOrder:
    """An order in the book. ``size`` is its remaining size (x10**8)."""

    __slots__ = ("oid", "side", "price", "size", "user_id", "flags", "seq")

    def __init__(self, oid: int, side: int, price: int, size: int, user_id: int, flags: int, seq: int) -> None:
        self.oid = oid
        self.side = side
        self.price = price
        self.size = size
        self.user_id = user_id
        self.flags = flags
        #: arrival number: lower is earlier, so further ahead in its price's queue
        self.seq = seq

    def __repr__(self) -> str:
        side = "bid" if self.side == BID else "ask"
        return f"RestingOrder(oid={self.oid}, {side} {self.price / SCALE:g} x {self.size / SCALE:g}, user={self.user_id})"


class OrderBook:
    """The order-level (L3) book: every resting order, in queue order at each price.

    Apply SNAPORDER, ORDER and TRADE records in file order with :meth:`apply`
    (:class:`Replay` does this for you). The rules are the format's:

    * SNAPORDER adds an order; the records come in queue order.
    * ORDER ``open`` with a time in force other than NONE adds an order at the
      back of its price; correction 251 adds one; correction 252 sets the
      remaining size and keeps the place in the queue.
    * ORDER with a removing status (:data:`REMOVING_STATUSES`) removes it.
    * TRADE reduces the maker and the taker order, where each rests.
    * Everything else (rejects, ``triggered``, trigger-order placements)
      leaves the book as it is.

    An order filled down to zero stays listed (size 0) until the event that
    removes it, which follows in the same block.
    """

    def __init__(self) -> None:
        #: oid -> RestingOrder
        self.orders: dict[int, RestingOrder] = {}
        #: total resting size per price, best first
        self.bids = Levels(descending=True)
        self.asks = Levels(descending=False)
        self._queues: dict[tuple[int, int], dict[int, RestingOrder]] = {}
        self._seq = 0

    def __len__(self) -> int:
        return len(self.orders)

    # -- changes -----------------------------------------------------------

    def apply(self, record: OrderLogRecord) -> None:
        """Apply one record. Records that do not change the book are ignored."""
        kind = type(record)
        if kind is Order:
            self.apply_order(record)  # type: ignore[arg-type]
        elif kind is Trade:
            self.apply_trade(record)  # type: ignore[arg-type]
        elif kind is SnapOrder:
            self.insert(record.oid, record.side, record.price, record.size, record.user_id, record.flags)  # type: ignore[union-attr]

    def apply_order(self, record: Order) -> None:
        status = record.status
        if status == 252:  # correction: remaining size, same place in the queue
            self.resize(record.oid, record.size)
        elif (status == 0 and record.tif != 0) or status == 251:  # open (not a waiting trigger order), correction: rests
            self.insert(record.oid, record.side, record.price, record.size, record.user_id, record.flags)
        elif status in REMOVING_STATUSES:
            self.remove(record.oid)

    def apply_trade(self, record: Trade) -> None:
        if record.flags & TRADE_MAKER_OID:
            self.reduce(record.maker_oid, record.size)
        if record.flags & TRADE_TAKER_OID:
            self.reduce(record.taker_oid, record.size)

    def insert(self, oid: int, side: int, price: int, size: int, user_id: int, flags: int = 0) -> RestingOrder:
        """Add an order at the back of its price. An order already there with this id is replaced."""
        if oid in self.orders:
            self.remove(oid)
        self._seq += 1
        order = RestingOrder(oid, side, price, size, user_id, flags, self._seq)
        self.orders[oid] = order
        key = (side, price)
        queue = self._queues.get(key)
        if queue is None:
            self._queues[key] = {oid: order}
        else:
            queue[oid] = order
        (self.bids if side == BID else self.asks).add(price, size)
        return order

    def remove(self, oid: int) -> Optional[RestingOrder]:
        order = self.orders.pop(oid, None)
        if order is not None:
            (self.bids if order.side == BID else self.asks).add(order.price, -order.size)
            key = (order.side, order.price)
            queue = self._queues[key]
            del queue[oid]
            if not queue:
                del self._queues[key]
        return order

    def reduce(self, oid: int, by: int) -> bool:
        """Take ``by`` off an order's remaining size (not below zero). False when it is not in the book."""
        order = self.orders.get(oid)
        if order is None:
            return False
        taken = min(by, order.size)
        order.size -= taken
        self._levels(order.side).add(order.price, -taken)
        return True

    def resize(self, oid: int, size: int) -> bool:
        """Set an order's remaining size and keep its place in the queue."""
        order = self.orders.get(oid)
        if order is None:
            return False
        self._levels(order.side).add(order.price, size - order.size)
        order.size = size
        return True

    def clear(self) -> None:
        self.orders.clear()
        self._queues.clear()
        self.bids.clear()
        self.asks.clear()
        self._seq = 0

    def _levels(self, side: int) -> Levels:
        return self.bids if side == BID else self.asks

    # -- reading -----------------------------------------------------------

    def best_bid(self) -> Optional[int]:
        """The best bid price (x10**8), or None."""
        return self.bids.best()

    def best_ask(self) -> Optional[int]:
        """The best ask price (x10**8), or None."""
        return self.asks.best()

    def queue(self, side: int, price: int) -> list[RestingOrder]:
        """The orders resting at a price, front of the queue first.

        An order filled down to zero is still listed (size 0) until the event
        that removes it, later in the same block.
        """
        return list(self._queues.get((side, price), {}).values())

    def queue_position(self, oid: int) -> Optional[tuple[int, int]]:
        """``(orders_ahead, size_ahead)`` of a resting order in its price's queue, or None."""
        order = self.orders.get(oid)
        if order is None:
            return None
        ahead = size = 0
        for other in self._queues[(order.side, order.price)].values():
            if other is order:
                return ahead, size
            if other.size > 0:  # an order filled to zero is about to be removed
                ahead += 1
                size += other.size
        raise AssertionError("an order is missing from its queue")  # pragma: no cover

    def depth(self, side: int, levels: Optional[int] = 10) -> list[tuple[int, int, int]]:
        """``[(price, total_size, order_count), ...]`` from the best price outwards.

        Orders filled down to zero (removed by an event later in the same
        block) are not counted.
        """
        out = []
        for price, total in self._levels(side).top(levels):
            queue = self._queues.get((side, price), {})
            out.append((price, total, sum(1 for order in queue.values() if order.size > 0)))
        return out

    def orders_of(self, user_id: int) -> list[RestingOrder]:
        """Every resting order of one account."""
        return [order for order in self.orders.values() if order.user_id == user_id]


# --------------------------------------------------------------------------
# Replay: records applied in order, with the state a reader needs
# --------------------------------------------------------------------------


class Replay:
    """Read a file and keep its book up to date as you iterate.

    Iterating yields each record after it has been applied to :attr:`book`.
    Alongside it the replay keeps the file's :attr:`meta`, :attr:`outcome`
    (outcome files with both books), the accounts seen so far
    (:attr:`accounts`, user id -> address), the current :attr:`block` and, for
    each trade of an outcome market, its :attr:`last_trade_kind` (read from the
    book before the trade changed it).
    """

    def __init__(self, source: Source) -> None:
        self.source = source
        self.book = OrderBook()
        self.meta: Optional[Meta] = None
        self.outcome: Optional[OutcomeInfo] = None
        self.accounts: dict[int, str] = {}
        self.block: Optional[Block] = None
        self.last_trade_kind: TradeKind = TradeKind.NONE
        self._outcome_file = False
        self._reader: Optional[Iterator[OrderLogRecord]] = None
        self._pending: Optional[OrderLogRecord] = None
        # Orders removed in the current block, with their flags: a block's
        # removals are meant to follow its trades, and a file that writes one
        # first must not lose the maker's book (Order-Log Replay regroups too).
        self._removed_in_block: dict[int, int] = {}

    def __iter__(self) -> Iterator[OrderLogRecord]:
        """Yield each record after applying it, from wherever the replay stands."""
        step = self._step
        for record in self._records():
            step(record)
            yield record

    def run(self, until: Optional[int] = None) -> "Replay":
        """Read on and return the replay.

        With ``until`` (a time in ticks, see :func:`alphaquick.datetime_to_ticks`),
        stop before the first block later than it: the book is then as it
        stood at that moment. The file's first block, which carries the
        day-start book, is always read, so a moment before the day starts gives
        the day-start book. Otherwise read to the end of the file. Blocks are the
        clock: other records can carry other times (an account's USER record,
        written before its first use, keeps the time of the block it first
        appears in). A replay can be run on in steps
        (``run(until=t1)``, then ``run(until=t2)`` or a ``for`` loop): each
        continues where the previous one stopped.
        """
        step = self._step
        for record in self._records():
            if until is not None and type(record) is Block and record.time > until and self.block is not None:
                self._pending = record  # read but not applied: the next run starts with it
                break
            step(record)
        return self

    def _records(self) -> Iterator[OrderLogRecord]:
        # Not "yield from": stopping a loop over this generator must not close
        # the file reader, so that the replay can go on later.
        if self._reader is None:
            self._reader = read_orderlog(self.source)
        while True:
            if self._pending is not None:
                record, self._pending = self._pending, None
            else:
                record = next(self._reader, None)
                if record is None:
                    return
            yield record

    def _step(self, record: OrderLogRecord) -> None:
        kind = type(record)
        if kind is Order:
            if self._outcome_file and record.status in REMOVING_STATUSES:  # type: ignore[union-attr]
                gone = self.book.orders.get(record.oid)  # type: ignore[union-attr]
                if gone is not None:
                    self._removed_in_block[gone.oid] = gone.flags
            self.book.apply_order(record)  # type: ignore[arg-type]
        elif kind is Trade:
            if self._outcome_file:
                self.last_trade_kind = trade_kind(record.flags, record.taker_side, self._maker_flags(record))  # type: ignore[arg-type, union-attr]
            self.book.apply_trade(record)  # type: ignore[arg-type]
        elif kind is Block:
            self.block = record  # type: ignore[assignment]
            self._removed_in_block.clear()
        elif kind is SnapOrder:
            self.book.apply(record)
        elif kind is User:
            self.accounts[record.user_id] = record.address  # type: ignore[union-attr]
        elif kind is Meta:
            self.meta = record  # type: ignore[assignment]
            self._outcome_file = record.is_outcome  # type: ignore[union-attr]
        elif kind is Outcome:
            self.outcome = record.info()  # type: ignore[union-attr]

    def _maker_flags(self, trade: Trade) -> Optional[int]:
        if not trade.flags & TRADE_MAKER_OID:
            return None
        maker = self.book.orders.get(trade.maker_oid)
        if maker is not None:
            return maker.flags
        return self._removed_in_block.get(trade.maker_oid)

    def address(self, user_id: int) -> str:
        """The address of an account id, or ``"?"`` when it has not been seen."""
        return self.accounts.get(user_id, "?")

    @property
    def datetime(self):
        """The time of the current block, as a UTC datetime, or None before the first block."""
        return None if self.block is None else self.block.datetime


def record_datetime(record: OrderLogRecord):
    """The time in a record's header as a UTC datetime."""
    return ticks_to_datetime(record.time)
