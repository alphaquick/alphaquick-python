"""What a file is, from its name.

L2 files are ``<INSTRUMENT>_<VENUE>_<YYYYMMDD>.bin.lz4``. Both the instrument
(``BTCUSDT_261225``) and the venue (``BINANCE_USDM``) can contain underscores,
so the venue is matched as a known code at the end of the name, not found by
splitting.

Order-log files are ``<COIN>_HLC_<YYYYMMDD>.bin.lz4``, the Hyperliquid coin
with ``:``, ``@`` and ``/`` written as ``_`` (``xyz:CL`` -> ``xyz_CL``), or
``HIP4_<outcome id>_<short name>`` for a HIP-4 outcome market with both books
(``HIP4_<outcome id>`` alone when the market's title gives no short name).
They are published one folder per day, the outcome files in a ``hip4/``
folder of their own: ``<YYYYMMDD>/hip4/HIP4_..._HLC_<YYYYMMDD>.bin.lz4`` (in R2
under ``hyperliquid/hlc_v2/``).
"""

from __future__ import annotations

import os
import re
from typing import NamedTuple, Optional

#: L2 venue codes, longest first (a venue not listed yet is still read, with
#: the last part of the name as its code).
L2_VENUES = tuple(sorted((
    "BINANCE_BBO_COINM", "BINANCE_BBO_USDM", "BINANCE_BBO_SPOT",
    "BYBIT_INVERSE", "BINANCE_USDM", "BINANCE_COINM", "BINANCE_SPOT", "BINANCE_HFT",
    "HYPERLIQUID_NODE", "HYPERLIQUID_BBO", "HYPERLIQUID", "BINANCE", "BITHUMB", "LIGHTER", "ASTER",
    "BYBIT", "UPBIT", "DYDX", "OKX",
), key=len, reverse=True))

_L2_FILE = re.compile(r"^(.+)_([0-9]{8})\.(bin\.lz4|continuity\.jsonl)$")
_NODE_VENUE = re.compile(r"_(HYPERLIQUID_NODE_[A-Z]_[A-Za-z0-9]+)$")
_HLC_FILE = re.compile(r"^(.+)_HLC_([0-9]{8})\.bin\.lz4$")
# HIP4_<outcome id>, with the short name of the market's title when it has one
_MERGED_OUTCOME = re.compile(r"^HIP4_[0-9]{1,10}(?:_[A-Za-z0-9][A-Za-z0-9.-]{0,60})?$")


class L2Name(NamedTuple):
    symbol: str  #: the venue's own symbol, e.g. ``BTCUSDT`` or ``BTC-USDT-SWAP``
    venue: str  #: the venue code, e.g. ``BINANCE_USDM``
    day: str  #: YYYYMMDD (UTC)
    continuity: bool  #: True for the ``.continuity.jsonl`` sidecar


class OrderLogName(NamedTuple):
    #: Hyperliquid's name: ``BTC``, ``xyz:CL``, ``@107``, ``PURR/USDC``, ``#114220``, or for an
    #: outcome file with both books the file's ``HIP4_<id>[_<short name>]`` (its META says ``HIP4:<id>``)
    coin: str
    market: str  #: ``perp``, the HIP-3 dex (``xyz``), ``spot`` or ``outcome``
    day: str  #: YYYYMMDD (UTC)


def _base(name: str) -> str:
    return os.path.basename(os.fspath(name))


def split_l2_stem(stem: str) -> Optional[tuple[str, str]]:
    """``(symbol, venue)`` of ``<SYMBOL>_<VENUE>``, or None."""
    node = _NODE_VENUE.search(stem)
    if node and node.start() > 0:
        return stem[:node.start()], node.group(1)
    for venue in L2_VENUES:
        if len(stem) > len(venue) + 1 and stem.endswith("_" + venue):
            return stem[:-(len(venue) + 1)], venue
    cut = stem.rfind("_")
    if cut <= 0 or cut == len(stem) - 1:
        return None
    return stem[:cut], stem[cut + 1:]


def parse_l2_name(name: str) -> Optional[L2Name]:
    """Read an L2 file name (a path is fine). None when it is not one."""
    match = _L2_FILE.match(_base(name))
    if not match or "_HLC_" in match.group(1) + "_":
        return None
    split = split_l2_stem(match.group(1))
    if split is None:
        return None
    return L2Name(split[0], split[1], match.group(2), match.group(3) == "continuity.jsonl")


def orderlog_coin(stem: str) -> Optional[tuple[str, str]]:
    """``(coin, market)`` of the name part of an order-log file, or None."""
    if len(stem) > 80:
        return None
    if re.fullmatch(r"#[0-9]{2,12}", stem):
        return stem, "outcome"
    if _MERGED_OUTCOME.match(stem):
        return stem, "outcome"
    dex = re.fullmatch(r"([a-z][a-z0-9]{0,15})_([A-Za-z0-9][A-Za-z0-9.-]*)", stem)
    if dex:
        return f"{dex.group(1)}:{dex.group(2)}", dex.group(1)
    index = re.fullmatch(r"_([0-9]{1,6})", stem)
    if index:
        return f"@{index.group(1)}", "spot"
    pair = re.fullmatch(r"([A-Z0-9][A-Za-z0-9]*)_([A-Z0-9][A-Za-z0-9]*)", stem)
    if pair:
        return f"{pair.group(1)}/{pair.group(2)}", "spot"
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]*", stem):
        return stem, "perp"
    return None


def parse_orderlog_name(name: str) -> Optional[OrderLogName]:
    """Read an order-log file name (a path is fine). None when it is not one."""
    match = _HLC_FILE.match(_base(name))
    if not match:
        return None
    coin = orderlog_coin(match.group(1))
    if coin is None:
        return None
    return OrderLogName(coin[0], coin[1], match.group(2))


def orderlog_stem(coin: str) -> str:
    """The file-name form of a coin: ``xyz:CL`` -> ``xyz_CL`` (the inverse of :func:`orderlog_coin`)."""
    return re.sub(r"[:@/]", "_", coin)


def file_kind(name: str) -> Optional[str]:
    """``"orderlog"``, ``"l2"`` or None, from a file name."""
    if parse_orderlog_name(name):
        return "orderlog"
    if parse_l2_name(name):
        return "l2"
    return None
