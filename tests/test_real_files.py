"""Real order-log files of the current converter (hyperliquid/hlc_v2/), when you have them.

Set ALPHAQUICK_HLC_V2_DIR to a folder holding files of 17 November 2026
(``PURR_USDC_HLC_20261117.bin.lz4``, and ``HIP4_1473_..._HLC_20261117.bin.lz4``
in it or in its ``hip4/`` folder). Without them these tests are skipped.
"""

import collections
import os
from pathlib import Path

import pytest

from alphaquick import orderlog as ol


def real_file(pattern: str) -> Path:
    folder = os.environ.get("ALPHAQUICK_HLC_V2_DIR")
    if not folder:
        pytest.skip("set ALPHAQUICK_HLC_V2_DIR to a folder of hlc_v2 files to run this test")
    found = sorted(Path(folder).glob(pattern)) + sorted(Path(folder).glob("hip4/" + pattern))
    if not found:
        pytest.skip(f"no {pattern} in {folder}")
    return found[0]


def test_spot_book_never_crosses_with_the_v2_statuses():
    # 279 PURR/USDC orders end with insufficientWeiCanceled (27). Version 0.1.0
    # did not remove them, and the book was crossed at the end of 168,217 of
    # the 199,677 blocks (counted as here).
    replay = ol.Replay(real_file("PURR_USDC_HLC_20261117.bin.lz4"))
    blocks = crossed = 0
    last_status = {}

    def check():
        bid, ask = replay.book.best_bid(), replay.book.best_ask()
        return bid is not None and ask is not None and bid >= ask

    for record in replay:
        kind = type(record)
        if kind is ol.Block:
            if blocks:
                crossed += check()  # the book at the end of the block before
            blocks += 1
        elif kind is ol.Order:
            last_status[record.oid] = record.status
    crossed += check()
    assert blocks == 199_677 and crossed == 0
    gone = [oid for oid, status in last_status.items() if status == ol.Status.INSUFFICIENT_WEI_CANCELED]
    assert len(gone) == 279 and not any(oid in replay.book.orders for oid in gone)
    assert (replay.meta.coin, replay.meta.dex) == ("PURR/USDC", ol.DEX_SPOT)


def test_outcome_fills_take_the_front_of_their_own_books_queue():
    replay = ol.Replay(real_file("HIP4_1473_*_HLC_20261117.bin.lz4"))
    places = collections.Counter()
    system = 0
    for record in replay._records():
        if type(record) is ol.Trade:
            if record.is_system_print:
                system += 1
            elif record.flags & ol.TRADE_MAKER_OID:
                place = replay.book.queue_position(record.maker_oid)
                places["missing" if place is None else "front" if place[0] == 0 else "behind"] += 1
        replay._step(record)
    meta = replay.meta
    assert (meta.coin, meta.dex, meta.asset, meta.is_outcome) == ("HIP4:1473", ol.DEX_SPOT, 65535, True)
    assert replay.book.outcome and system == 136
    # One queue for both books put half of the makers behind orders of the other book.
    assert places["missing"] == 0 and places["behind"] <= 10 and places["front"] >= 6_800
