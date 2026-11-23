"""The real files: the free samples, checked against the numbers Order-Log Replay
and Depth Replay produce for them.

These run when the samples are already in the cache, or when
ALPHAQUICK_FETCH_SAMPLES=1 lets the tests download them (about 20 MB).
"""

import json
import os

import pytest

import alphaquick as aq
from alphaquick import l2, orderlog as ol, samples

pytestmark = pytest.mark.samples


def sample(name: str):
    entry = samples.SAMPLES[name]
    cached = samples.cache_dir() / "samples" / entry.filename
    if not cached.exists() and os.environ.get("ALPHAQUICK_FETCH_SAMPLES") != "1":
        pytest.skip(f"sample {name} not cached; set ALPHAQUICK_FETCH_SAMPLES=1 to download it")
    return samples.fetch(name)


def count(replay: ol.Replay):
    counts = {"SnapOrder": 0, "Block": 0, "Order": 0, "Trade": 0}
    kinds = {}
    for record in replay:
        name = type(record).__name__
        if name in counts:
            counts[name] += 1
        if name == "Trade" and replay.meta.is_outcome:
            kinds[replay.last_trade_kind.name] = kinds.get(replay.last_trade_kind.name, 0) + 1
    return counts, kinds


def sides(book: ol.OrderBook):
    bids = sum(1 for o in book.orders.values() if o.side == ol.BID)
    return bids, len(book.orders) - bids


def test_zec_order_log():
    replay = ol.Replay(sample("orderlog-zec-30min"))
    counts, kinds = count(replay)
    assert counts == {"SnapOrder": 10_134, "Block": 19_764, "Order": 306_433, "Trade": 2_815}
    assert kinds == {}
    assert len(replay.accounts) == 1_799
    assert (replay.meta.coin, replay.meta.day, replay.meta.is_outcome) == ("ZEC", 20261014, False)
    book = replay.book
    assert sides(book) == (6_625, 3_409)
    assert (book.best_bid(), book.best_ask()) == (123_100_000_000, 123_110_000_000)
    front = book.queue(ol.BID, book.best_bid())[0]
    assert (front.size, replay.address(front.user_id)) == (1_624_000_000, "0x41a914768eda813d6d4b2ef5a3cdcb872a496a32")


def test_zec_book_at_a_moment_and_running_on():
    from datetime import datetime, timezone
    path = sample("orderlog-zec-30min")
    moment = datetime(2026, 10, 14, 0, 15, tzinfo=timezone.utc)
    replay = ol.Replay(path).run(until=aq.datetime_to_ticks(moment))
    assert replay.block.datetime <= moment
    assert replay._pending is not None and replay._pending.datetime > moment  # the next block
    assert 0 < len(replay.book) and replay.book.best_bid() < replay.book.best_ask()
    replay.run()
    whole = ol.Replay(path).run()
    assert {oid: o.size for oid, o in replay.book.orders.items()} == {oid: o.size for oid, o in whole.book.orders.items()}


def test_hip4_outcome_market_with_both_books():
    assert len(ol.Replay(sample("orderlog-hip4")).run(until=0).book) == 241  # before the day: the day-start book
    replay = ol.Replay(sample("orderlog-hip4"))
    counts, kinds = count(replay)
    assert counts == {"SnapOrder": 241, "Block": 16_537, "Order": 23_765, "Trade": 3_279}
    assert kinds == {"FIRST_BOOK": 183, "SECOND_BOOK": 1_810, "MINT": 552, "MERGE": 734}
    assert len(replay.accounts) == 82
    assert replay.meta.is_outcome and replay.outcome.outcome == 3600
    assert replay.outcome.title.endswith("No change") and replay.outcome.sides == ("Yes", "No")
    book = replay.book
    assert sides(book) == (213, 39)
    assert (book.best_bid(), book.best_ask()) == (93_451_000, 93_452_000)
    second = sum(1 for o in book.orders.values() if o.flags & ol.ORDER_SECOND_SIDE)
    assert 0 < second < len(book.orders)


def test_l2_whole_day_and_its_continuity_file():
    path = sample("l2-btc-hyperliquid")
    book = aq.L2Book()
    depth = trades = 0
    for event in aq.read_l2(path):
        if type(event) is l2.Trade:
            trades += 1
        else:
            depth += 1
            book.apply(event)
    assert (depth, trades) == (489_807, 163_895)
    assert book.best_bid() == (7_832_600_000_000, 150_219_000) and book.best_ask() == (7_832_700_000_000, 1_010_900_000)
    assert (len(book.bids), len(book.asks)) == (20, 20)
    lines = [json.loads(line) for line in sample("l2-btc-hyperliquid-continuity").read_text().splitlines()]
    assert len(lines) == 1 and lines[0]["kind"] == "seed"


def test_l2_depth_replay_sample():
    events = list(aq.read_l2(sample("l2-btc-hyperliquid-15min")))
    assert len(events) == 347_278
    book = aq.L2Book()
    for event in events:
        if type(event) is l2.DepthUpdate:
            book.apply(event)
    assert book.best_bid()[0] == 6_405_500_000_000 and book.best_ask()[0] == 6_405_600_000_000
    assert (len(book.bids), len(book.asks)) == (210, 286)
