"""Read AlphaQuick market data files.

Two datasets, one container:

* **L2 depth & trades** (``<INSTRUMENT>_<VENUE>_<YYYYMMDD>.bin.lz4``): every
  depth update and trade of one instrument for a day. :mod:`alphaquick.l2`.
* **Hyperliquid order log** (``<YYYYMMDD>/<TYPE>/<NAME>_HLC_<YYYYMMDD>.bin.lz4``,
  TYPE one of PERPS, HIP3, SPOT, HIP4; see :mod:`alphaquick.names`): every order
  and trade with accounts and queue positions (Level 3). :mod:`alphaquick.orderlog`.

Quick start::

    import alphaquick as aq

    for event in aq.read_l2("BTC_HYPERLIQUID_20261101.bin.lz4", continuity="auto"):
        ...                        # DepthUpdate, Trade, and Gap from the continuity file

    replay = aq.Replay("xyz_CL_HLC_20261101.bin.lz4").run()
    print(replay.book.best_bid(), replay.book.best_ask())

Formats: https://docs.alphaquick.io/data/overview
"""

from __future__ import annotations

__version__ = "0.2.0"

from ._container import FormatError, TruncatedFileError, iter_blocks, iter_raw_records
from ._time import (
    SCALE,
    TICKS_AT_UNIX_EPOCH,
    datetime_to_ticks,
    ticks_to_datetime,
    ticks_to_unix_ns,
    to_float,
    unix_ns_to_datetime,
)
from . import l2, names, orderlog, samples
from .l2 import DepthUpdate, Gap, L2Book, read_continuity, read_l2
from .orderlog import OrderBook, Replay, read_orderlog

__all__ = [
    "__version__", "FormatError", "TruncatedFileError", "iter_blocks", "iter_raw_records",
    "SCALE", "TICKS_AT_UNIX_EPOCH", "datetime_to_ticks", "ticks_to_datetime", "ticks_to_unix_ns",
    "to_float", "unix_ns_to_datetime", "l2", "names", "orderlog", "samples",
    "DepthUpdate", "Gap", "L2Book", "read_continuity", "read_l2", "OrderBook", "Replay", "read_orderlog", "detect",
]


def detect(source) -> str | None:
    """``"orderlog"`` or ``"l2"`` from a file's first record, or None for an empty file.

    An order-log file starts with its META record (kind 16); an L2 file with a
    depth update or a trade (kind 0 or 1).
    """
    for kind, _size, _block, _pos in iter_raw_records(source):
        if kind == orderlog.KIND_META:
            return "orderlog"
        if kind in (l2.KIND_DEPTH, l2.KIND_TRADE):
            return "l2"
        raise FormatError(f"the file starts with a record of kind {kind}, which is neither format's first record")
    return None
