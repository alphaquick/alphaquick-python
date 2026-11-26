"""What a file is, from its name.

L2 files are ``<INSTRUMENT>_<VENUE>_<YYYYMMDD>.bin.lz4``. Both the instrument
(``BTCUSDT_261225``) and the venue (``BINANCE_USDM``) can contain underscores,
so the venue is matched as a known code at the end of the name, not found by
splitting.

Order-log files are published one folder per day and, inside it, one folder
per market type (in R2 under ``hyperliquid/hlc_v2/``)::

    <YYYYMMDD>/PERPS/<COIN>_HLC_<YYYYMMDD>.bin.lz4             main perpetuals: BTC, kPEPE
    <YYYYMMDD>/HIP3/<dex>_<COIN>_HLC_<YYYYMMDD>.bin.lz4        HIP-3 markets: xyz_NVDA is xyz:NVDA
    <YYYYMMDD>/SPOT/<BASE>_<QUOTE>_HLC_<YYYYMMDD>.bin.lz4      spot pairs: HYPE_USDC (@107), PURR_USDC
    <YYYYMMDD>/HIP4/<title>_id<outcome id>_HLC_<YYYYMMDD>.bin.lz4   HIP-4 outcome markets, both books
    <YYYYMMDD>/INSTRUMENTS_HLC_<YYYYMMDD>.json                 every file of the day, with its venue id

A HIP-4 market's title is written with ``22:50`` as ``2250``, ``$`` left out
and every other run of characters but letters, digits and dots inside numbers
as ``-``, never shortened (``BTC-touches-60000-by-2026-12-01-0000-UTC_id37348``).
The name is a label to read: the venue's own id of a spot pair (``@107``) or
of an outcome (``HIP4:<id>``) is the coin of the file's META record and is
listed in ``INSTRUMENTS_HLC_<YYYYMMDD>.json``.

When a path has one of those folders, the folder says the market type. A bare
name is read by its shape. Names of an earlier layout, which was not
published, are still read: ``_107`` (the spot pair @107),
``HIP4_<outcome id>[_<short name>]`` (from a ``hip4/`` folder) and
``#<outcome id><side>`` (one side of an outcome market).
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
#: The folders of a day's order-log files, one per market type.
ORDERLOG_FOLDERS = ("PERPS", "HIP3", "SPOT", "HIP4")
_COIN = r"[A-Za-z0-9][A-Za-z0-9.-]*"
# <title>_id<outcome id>: a HIP-4 outcome market with both books
_OUTCOME_FILE = re.compile(rf"({_COIN})_id([0-9]{{1,10}})")
_DEX_COIN = re.compile(rf"([a-z][a-z0-9]{{0,15}})_({_COIN})")
# An earlier layout: HIP4_<outcome id>, with a short name of the title when it has one
_MERGED_OUTCOME = re.compile(r"^HIP4_[0-9]{1,10}(?:_[A-Za-z0-9][A-Za-z0-9.-]{0,60})?$")
#: Longest name part of a perp, HIP-3 or spot file.
_MAX_NAME = 80
#: Longest name part of a HIP-4 file: its title is never shortened, up to a 255-byte file name.
_MAX_OUTCOME_NAME = 255 - len("_HLC_YYYYMMDD.bin.lz4")


class L2Name(NamedTuple):
    symbol: str  #: the venue's own symbol, e.g. ``BTCUSDT`` or ``BTC-USDT-SWAP``
    venue: str  #: the venue code, e.g. ``BINANCE_USDM``
    day: str  #: YYYYMMDD (UTC)
    continuity: bool  #: True for the ``.continuity.jsonl`` sidecar


class OrderLogName(NamedTuple):
    #: Hyperliquid's name: ``BTC``, ``xyz:CL``, ``PURR/USDC``, or ``HIP4:<id>`` for a HIP-4 outcome
    #: file (as its META says). A spot file is named by its tokens, so ``SPOT/HYPE_USDC`` reads
    #: ``HYPE/USDC``; the venue's ``@107`` is in META and INSTRUMENTS. Names of the earlier layout
    #: read as before: ``_107`` is ``@107``, ``HIP4_<id>[_<short name>]`` itself, ``#114220``.
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


def orderlog_coin(stem: str, folder: Optional[str] = None) -> Optional[tuple[str, str]]:
    """``(coin, market)`` of the name part of an order-log file, or None.

    ``folder`` is the name of the folder the file is in. ``PERPS``, ``HIP3``,
    ``SPOT`` and ``HIP4`` say the market type, and the name must have that
    type's shape. Otherwise the name is read by its shape: ``<title>_id<N>`` is
    the outcome ``HIP4:<N>``, ``<dex>_<COIN>`` (the dex in lower case) a HIP-3
    market, ``<BASE>_<QUOTE>`` the spot pair ``BASE/QUOTE`` (a display name: the
    venue's ``@N`` is in the file's META and in INSTRUMENTS), anything else of
    letters, digits, dots and dashes a perpetual.
    """
    if folder in ORDERLOG_FOLDERS:
        return _in_folder(stem, folder)
    outcome = _outcome(stem)
    if outcome or len(stem) > _MAX_NAME:
        return outcome
    if re.fullmatch(r"#[0-9]{2,12}", stem):
        return stem, "outcome"
    if _MERGED_OUTCOME.match(stem):
        return stem, "outcome"
    dex = _DEX_COIN.fullmatch(stem)
    if dex:
        return f"{dex.group(1)}:{dex.group(2)}", dex.group(1)
    index = re.fullmatch(r"_([0-9]{1,6})", stem)
    if index:
        return f"@{index.group(1)}", "spot"
    pair = re.fullmatch(r"([A-Z0-9][A-Za-z0-9]*)_([A-Z0-9][A-Za-z0-9]*)", stem)
    if pair:
        return f"{pair.group(1)}/{pair.group(2)}", "spot"
    if re.fullmatch(_COIN, stem):
        return stem, "perp"
    return None


def _outcome(stem: str) -> Optional[tuple[str, str]]:
    match = _OUTCOME_FILE.fullmatch(stem) if len(stem) <= _MAX_OUTCOME_NAME else None
    return (f"HIP4:{match.group(2)}", "outcome") if match else None


def _in_folder(stem: str, folder: str) -> Optional[tuple[str, str]]:
    if folder == "HIP4":
        return _outcome(stem)
    if len(stem) > _MAX_NAME:
        return None
    if folder == "PERPS":
        return (stem, "perp") if re.fullmatch(_COIN, stem) else None
    if folder == "HIP3":
        dex = _DEX_COIN.fullmatch(stem)
        return (f"{dex.group(1)}:{dex.group(2)}", dex.group(1)) if dex else None
    pair = re.fullmatch(rf"({_COIN})_({_COIN})", stem)
    return (f"{pair.group(1)}/{pair.group(2)}", "spot") if pair else None


def parse_orderlog_name(name: str) -> Optional[OrderLogName]:
    """Read an order-log file name (a path is fine). None when it is not one.

    In a path, the folder the file is in decides the market type when it is
    ``PERPS``, ``HIP3``, ``SPOT`` or ``HIP4`` (see :func:`orderlog_coin`).
    """
    path = os.fspath(name)
    match = _HLC_FILE.match(os.path.basename(path))
    if not match:
        return None
    coin = orderlog_coin(match.group(1), os.path.basename(os.path.dirname(path)))
    if coin is None:
        return None
    return OrderLogName(coin[0], coin[1], match.group(2))


def orderlog_stem(coin: str) -> str:
    """The file-name form of a coin: ``xyz:CL`` -> ``xyz_CL``, ``PURR/USDC`` -> ``PURR_USDC``.

    The inverse of :func:`orderlog_coin` for perpetuals, HIP-3 markets and
    spot pairs named by their tokens. A spot pair the venue calls ``@107`` and
    a HIP-4 outcome are named by their tokens and their title, which the coin
    does not hold: take their names from ``INSTRUMENTS_HLC_<YYYYMMDD>.json``
    (``orderlog_stem("@107")`` gives the earlier layout's ``_107``).
    """
    return re.sub(r"[:@/]", "_", coin)


def file_kind(name: str) -> Optional[str]:
    """``"orderlog"``, ``"l2"`` or None, from a file name."""
    if parse_orderlog_name(name):
        return "orderlog"
    if parse_l2_name(name):
        return "l2"
    return None
