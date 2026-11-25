"""L2 depth & trades files: every depth update and trade of one instrument for one day.

Records (all little-endian, after the 12-byte header ``kind, size, time``):

* depth update, kind 0, 29 bytes: ``price, volume`` (int64, x10**8) and ``flags``
  (uint8: 1 bid, 2 ask, 4 clear the book first, 8 end of transaction);
* trade, kind 1, 37 bytes: ``id, price, volume`` (int64) and ``side`` (uint8:
  aggressor 1 buy, 2 sell).

``volume`` of a depth update is the new size at the level, not a change; 0
removes the level. Order-log files carry the same day as an aggregated L2 view
in these two record kinds, so :func:`read_l2` reads them too.

Prices and sizes are integers x10**8. Files up to 2026-11-25 (the earlier
recorder) hold them to 6 decimal places only; later files keep the venue's
digits.

Continuity: next to each file, ``<name>.continuity.jsonl`` holds one JSON line
for each fresh start of the book (``seed``) and each break in the venue's feed
(``gap``). After a gap no depth is written for the instrument until the next
seed, a transaction that starts with the CLEAR flag; trades go on. Pass
``continuity`` to :func:`read_l2` to get each gap as a :class:`Gap` event, and
check ``book.consistent and book.known`` before trusting an :class:`L2Book`.

See https://docs.alphaquick.io/data/l2-depth-trades#format-of-binlz4
"""

from __future__ import annotations

import json
import os
import struct
from typing import Any, Iterable, Iterator, NamedTuple, Optional, Union

from ._container import _HEAD, FormatError, Source, iter_blocks
from ._levels import Levels
from ._time import TICKS_AT_UNIX_EPOCH, ticks_to_datetime

KIND_DEPTH = 0
KIND_TRADE = 1

#: Depth flags.
BUY = 1
SELL = 2
CLEAR = 4
END_OF_TRANSACTION = 8

#: Trade sides (the aggressor). The recorders write only SIDE_BUY and
#: SIDE_SELL; SIDE_UNKNOWN is not a value of the format, only a name for a 0.
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
    #: the number of the trade in the file (1, 2, ...), given by the recorder,
    #: not the venue's trade id. Files up to 2026-11-25 start again at 1 after a
    #: restart of the recorder, so a number can repeat within such a file.
    trade_id: int
    price: int  #: x10**8
    volume: int  #: x10**8
    #: the aggressor: SIDE_BUY or SIDE_SELL (0, SIDE_UNKNOWN, is not written).
    #: LIGHTER files up to 2026-11-25 have SIDE_BUY on every trade.
    side: int

    @property
    def datetime(self):
        return ticks_to_datetime(self.time)


class Gap(NamedTuple):
    """The recording lost the venue's book here (from the continuity file).

    No depth was written for the instrument from the gap until the next update
    with the CLEAR flag, which starts a fresh book; trades go on. Until then the
    book is stale: :meth:`L2Book.apply_gap` sets ``book.known`` to False.

    ``time`` is when the recorder noticed the break (its clock, while records
    mostly carry the venue's time), so the event's place among the trades
    around it is approximate. It always comes before the CLEAR update that
    ends the gap.
    """

    time: int  #: 100 ns ticks since 0001-01-01 UTC
    #: why: ``disconnect``, ``sequence``, ``crossed``, ``unparsable`` or
    #: ``server_gap`` in files from 2026-11-26; ``sequence_jump``,
    #: ``epoch_change``, ``server_gap`` and others in earlier node-venue files
    reason: str

    @property
    def datetime(self):
        return ticks_to_datetime(self.time)


L2Event = Union[DepthUpdate, Trade, Gap]


def read_l2(source: Source, *, include_non_market: bool = False, continuity: Any = None) -> Iterator[L2Event]:
    """Yield every depth update and trade of an L2 file, in file order.

    Trades whose price or volume is not positive are not market prints and are
    skipped unless ``include_non_market`` is set. Other record kinds (the
    order-level records of an order-log file, kinds this version does not
    know) are skipped.

    ``continuity`` adds the feed's gaps as :class:`Gap` events: a path to the
    ``.continuity.jsonl`` file, its lines (as :func:`read_continuity` returns
    them), or ``"auto"`` for the file next to ``source`` when there is one.
    A gap comes before the first record whose time is at or after it, and
    never after the CLEAR update that ends it (nor before the seed it follows,
    nor inside a depth transaction). The records themselves are unchanged.
    """
    events = _read(source, include_non_market)
    lines = _continuity_lines(source, continuity)
    pending = _gap_schedule(lines) if lines else []
    return _with_gaps(events, pending) if pending else events


def _read(source: Source, include_non_market: bool) -> Iterator[Union[DepthUpdate, Trade]]:
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


# --------------------------------------------------------------------------
# Continuity files
# --------------------------------------------------------------------------

#: Continuity kinds that mark a CLEAR update (a fresh book) in the data file.
SEED_KINDS = frozenset({"seed", "legacy_seed"})


def read_continuity(source: Union[str, "os.PathLike[str]", Any]) -> list[dict]:
    """Every line of a ``.continuity.jsonl`` file, as dicts, in file order.

    Kinds and fields this version does not know are kept as they are. A last
    line cut short (the recorder stopped while writing it) is skipped; a line
    an earlier cut-short write left in front of a whole line keeps the whole
    line. Any other damaged line raises :class:`alphaquick.FormatError`.

    The kinds: ``seed`` (a fresh book was written: ``reason`` ``snapshot``,
    ``resync`` after a gap, or ``day_roll``; files up to 2026-11-25 also have
    ``legacy_seed``) and ``gap`` (the feed broke: ``reason``, ``ordinal``,
    ``exchange``, ``symbol``, ``received_at_ms``, ``written_at_ms``). Files up
    to 2026-11-25 from Hyperliquid node venues (``HYPERLIQUID_NODE_*``) also
    have ``incident``, ``resume_complete``, ``no_loss`` and ``writer_failure``
    lines, and their ``gap`` lines carry ``detected_at_ms``,
    ``last_good_ordinal`` and ``clear_ordinal`` instead and are written after
    the seed that ends the gap.
    """
    if isinstance(source, (str, os.PathLike)):
        name = os.fspath(source)
        with open(source, "rb") as stream:
            data = stream.read()
    else:
        name = getattr(source, "name", "the continuity file")
        data = source.read()
    text = data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else data
    rows = text.split("\n")
    last = max((i for i, row in enumerate(rows) if row.strip()), default=-1)
    out = []
    decoder = json.JSONDecoder()
    for number, row in enumerate(rows):
        row = row.strip()
        if not row:
            continue
        value = _json_object(decoder, row)
        if value is None:
            if number == last:
                continue  # cut short while being written
            raise FormatError(f"{name}: line {number + 1} is not a JSON object "
                              "(to read the data file without it, pass continuity=None)")
        out.append(value)
    return out


def _json_object(decoder: json.JSONDecoder, row: str) -> Optional[dict]:
    """The JSON object of a line or, after a cut-short write, the whole object that ends it."""
    try:
        value = json.loads(row)
        return value if isinstance(value, dict) else None
    except ValueError:
        pass
    at = row.find("{", 1)
    while at > 0:
        try:
            value, end = decoder.raw_decode(row, at)
            if end == len(row) and isinstance(value, dict):
                return value
        except ValueError:
            pass
        at = row.find("{", at + 1)
    return None


def continuity_path(path: Union[str, "os.PathLike[str]"]) -> str:
    """The continuity file that goes with a data file: ``X.bin.lz4`` -> ``X.continuity.jsonl``."""
    name = os.fspath(path)
    if not name.endswith(".bin.lz4"):
        raise ValueError(f"{name} is not a .bin.lz4 file")
    return name[: -len(".bin.lz4")] + ".continuity.jsonl"


def _continuity_lines(source: Source, continuity: Any) -> list[dict]:
    if continuity is None:
        return []
    if isinstance(continuity, str) and continuity == "auto":
        if not isinstance(source, (str, os.PathLike)) or not os.fspath(source).endswith(".bin.lz4"):
            return []
        sibling = continuity_path(source)
        return read_continuity(sibling) if os.path.isfile(sibling) else []
    if isinstance(continuity, (str, os.PathLike)):
        return read_continuity(continuity)
    lines = []
    for line in continuity:
        if isinstance(line, (str, bytes)):
            line = json.loads(line) if line.strip() else None
        if line is not None:
            if not isinstance(line, dict):
                raise TypeError("continuity lines must be dicts (or JSON objects as text)")
            lines.append(line)
    return lines


class _Pending(NamedTuple):
    time: Optional[int]  # ticks, or None when the line gives no time
    reason: str
    before: int  # the gap comes before the CLEAR update with this number (1 = the file's first)


def _ms_to_ticks(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return TICKS_AT_UNIX_EPOCH + int(value) * 10_000


def _gap_schedule(lines: Iterable[dict]) -> list[_Pending]:
    """Each gap line, with the CLEAR update (counted from the file's start) it must precede.

    Every seed line marks one CLEAR update in the data file, in the same
    order. A gap line of the current recorder comes before the seed that ends
    the gap; one of the earlier recorder (it has ``clear_ordinal``) after it.
    """
    seeds = 0
    out = []
    for line in lines:
        kind = line.get("kind")
        if kind in SEED_KINDS:
            seeds += 1
        elif kind == "gap":
            time = None
            for key in ("received_at_ms", "detected_at_ms", "written_at_ms"):
                time = _ms_to_ticks(line.get(key))
                if time is not None:
                    break
            before = seeds if "clear_ordinal" in line else seeds + 1
            out.append(_Pending(time, str(line.get("reason") or "gap"), max(before, 1)))
    return out


def _with_gaps(events: Iterator[Union[DepthUpdate, Trade]], pending: list[_Pending]) -> Iterator[L2Event]:
    """Merge the gaps into the records: see :func:`read_l2`."""
    i, n = 0, len(pending)
    clears = 0  # CLEAR updates read so far
    in_transaction = False  # the last record was a depth update that did not end its transaction
    last_time = None
    for event in events:
        if i < n:
            depth = type(event) is DepthUpdate
            starts = depth and bool(event.flags & CLEAR)
            at = event.time
            while i < n:
                gap = pending[i]
                if gap.before - 1 > clears:
                    break  # the seed the gap follows is not read yet
                if starts and gap.before <= clears + 1:
                    pass  # this CLEAR ends the gap: the gap comes before it
                elif in_transaction or gap.time is None or gap.time > at:
                    break
                yield Gap(at if gap.time is None else gap.time, gap.reason)
                i += 1
            if starts:
                clears += 1
            if depth:
                in_transaction = not event.flags & END_OF_TRANSACTION
        last_time = event.time
        yield event
    for gap in pending[i:]:  # gaps the file does not reach the end of
        if gap.time is not None or last_time is not None:
            yield Gap(last_time if gap.time is None else gap.time, gap.reason)


class L2Book:
    """A price-level order book rebuilt from depth updates.

    Apply every :class:`DepthUpdate` (and :class:`Gap`) in order with
    :meth:`apply`. Sizes and prices stay scaled integers (x10**8). The book is
    the venue's when ``book.consistent and book.known``: not in the middle of a
    transaction, and not inside a gap.

    ``uncross`` removes the opposite levels a new level crosses (a bid at or
    above the best ask, and the reverse); Depth Replay applies this rule. The
    format does not need it. It is there for BINANCE_USDM and ASTER files up
    to 2026-11-25: the earlier recorder did not check the venue's sequence or
    take fresh snapshots, so those files hold levels that were never removed.
    ``uncross`` hides the crossed part near the best prices; stale levels
    deeper in the book remain. Files from 2026-11-26 are sequence-checked.
    """

    def __init__(self, *, uncross: bool = False) -> None:
        self.bids = Levels(descending=True)
        self.asks = Levels(descending=False)
        self.uncross = uncross
        #: True after an update that ends a transaction, False in the middle of one.
        self.consistent = True
        #: False from a :class:`Gap` until the next update with CLEAR: the
        #: levels are the last ones before the gap, no longer the venue's.
        self.known = True
        #: The gap the book is in while ``known`` is False, else None.
        self.gap: Optional[Gap] = None
        #: Time of the last update applied (ticks), or None.
        self.time: int | None = None

    def apply(self, update: Union[DepthUpdate, Gap]) -> None:
        """Apply a depth update (a :class:`Gap` goes to :meth:`apply_gap`)."""
        if type(update) is Gap:
            self.apply_gap(update)  # type: ignore[arg-type]
            return
        flags = update.flags  # type: ignore[union-attr]
        if flags & CLEAR:
            self.bids.clear()
            self.asks.clear()
            self.known = True
            self.gap = None
        side = self.bids if flags & BUY else self.asks
        side.set(update.price, update.volume)  # type: ignore[union-attr]
        if self.uncross and update.volume > 0:  # type: ignore[union-attr]
            if flags & BUY:
                self.asks.remove_through(update.price)  # type: ignore[union-attr]
            else:
                self.bids.remove_through(update.price)  # type: ignore[union-attr]
        self.consistent = bool(flags & END_OF_TRANSACTION)
        self.time = update.time

    def apply_gap(self, gap: Optional[Gap] = None) -> None:
        """The feed broke: the book is not known until the next update with CLEAR."""
        self.known = False
        self.gap = gap

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


def book_at(source: Source, until: int, *, uncross: bool = False, continuity: Any = "auto") -> L2Book:
    """The book as it stood at a moment: every depth update up to and including ``until`` (ticks).

    Use :func:`alphaquick.datetime_to_ticks` to turn a datetime into ticks.
    Only depth updates decide where to stop: it stops at the first one later
    than ``until`` (records are in the order they were received, and venue
    times can step back slightly), so a trade stamped a little later does not
    end the book early. The book may be in the middle of a transaction or
    inside a gap: check ``book.consistent and book.known``. ``continuity`` is
    as for :func:`read_l2`; by default the continuity file next to ``source``
    is used when there is one.
    """
    book = L2Book(uncross=uncross)
    replay_into(book, read_l2(source, continuity=continuity), until)
    return book


def replay_into(book: L2Book, events: Iterable[L2Event], until: Optional[int] = None) -> L2Book:
    """Apply events to ``book``: depth updates up to the first one later than ``until``, and the gaps before it.

    Trades are passed over and do not decide where to stop. A gap later than
    ``until`` is not applied. ``until`` None applies everything. When reading
    fails part way (a truncated file), ``book`` holds what was read.
    """
    for event in events:
        kind = type(event)
        if kind is DepthUpdate:
            if until is not None and event.time > until:
                break
            book.apply(event)  # type: ignore[arg-type]
        elif kind is Gap and (until is None or event.time <= until):
            book.apply_gap(event)  # type: ignore[arg-type]
    return book
