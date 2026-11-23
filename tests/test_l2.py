import io
from datetime import datetime, timezone

import alphaquick as aq
from alphaquick import l2

from build import P, T0, container, depth, l2_trade, unknown


def file_of(*records: bytes) -> io.BytesIO:
    return io.BytesIO(container(b"".join(records)))


def test_read_l2_yields_updates_and_trades_in_order():
    src = file_of(
        depth(T0, 100.0, 1.5, l2.BUY | l2.CLEAR),
        depth(T0 + 1, 101.0, 2.0, l2.SELL | l2.END_OF_TRANSACTION),
        unknown(42, T0 + 2),
        l2_trade(T0 + 3, 100.5, 0.25, l2.SIDE_BUY, trade_id=77),
        l2_trade(T0 + 4, 0.0, 1.0, l2.SIDE_SELL),  # not a market print
    )
    events = list(aq.read_l2(src))
    assert events == [
        l2.DepthUpdate(T0, 100 * P, 150_000_000, l2.BUY | l2.CLEAR),
        l2.DepthUpdate(T0 + 1, 101 * P, 2 * P, l2.SELL | l2.END_OF_TRANSACTION),
        l2.Trade(T0 + 3, 77, 10_050_000_000, 25_000_000, l2.SIDE_BUY),
    ]
    assert events[0].is_bid and events[0].clears_book and not events[0].ends_transaction
    assert events[2].datetime == datetime(2026, 10, 23, tzinfo=timezone.utc)  # 3 ticks = 0.3 microseconds
    every = list(aq.read_l2(file_of(l2_trade(T0, 0.0, 1.0, 2)), include_non_market=True))
    assert len(every) == 1 and every[0].price == 0


def test_book_levels_clear_and_transactions():
    book = aq.L2Book()
    updates = [
        l2.DepthUpdate(1, 100 * P, 1 * P, l2.BUY | l2.CLEAR),
        l2.DepthUpdate(2, 99 * P, 2 * P, l2.BUY),
        l2.DepthUpdate(3, 101 * P, 3 * P, l2.SELL),
        l2.DepthUpdate(4, 102 * P, 4 * P, l2.SELL | l2.END_OF_TRANSACTION),
    ]
    for u in updates[:3]:
        book.apply(u)
    assert not book.consistent
    book.apply(updates[3])
    assert book.consistent
    assert book.best_bid() == (100 * P, 1 * P) and book.best_ask() == (101 * P, 3 * P)
    assert book.spread() == 1 * P and book.mid() == 100.5 * P
    assert book.depth(5) == ([(100 * P, P), (99 * P, 2 * P)], [(101 * P, 3 * P), (102 * P, 4 * P)])
    book.apply(l2.DepthUpdate(5, 100 * P, 0, l2.BUY | l2.END_OF_TRANSACTION))  # 0 removes the level
    assert book.best_bid() == (99 * P, 2 * P)
    book.apply(l2.DepthUpdate(6, 99 * P, 5 * P, l2.BUY))  # the new size, not a change
    assert book.best_bid() == (99 * P, 5 * P)
    book.apply(l2.DepthUpdate(7, 50 * P, 1 * P, l2.BUY | l2.CLEAR))  # a fresh snapshot starts
    assert book.depth(None) == ([(50 * P, P)], [])
    assert book.time == 7


def test_uncross_removes_levels_a_new_level_crosses():
    plain, uncrossed = aq.L2Book(), aq.L2Book(uncross=True)
    for book in (plain, uncrossed):
        for u in (l2.DepthUpdate(1, 101 * P, P, l2.SELL), l2.DepthUpdate(1, 102 * P, P, l2.SELL),
                  l2.DepthUpdate(1, 100 * P, P, l2.BUY), l2.DepthUpdate(2, 101 * P, P, l2.BUY)):
            book.apply(u)
    assert plain.best_ask() == (101 * P, P)  # crossed: bid 101, ask 101
    assert uncrossed.best_ask() == (102 * P, P) and uncrossed.best_bid() == (101 * P, P)
    uncrossed.apply(l2.DepthUpdate(3, 99 * P, P, l2.SELL))
    assert uncrossed.best_bid() is None and uncrossed.best_ask() == (99 * P, P)


def test_book_at_stops_at_the_moment():
    src = container(depth(10, 100.0, 1.0, l2.BUY), depth(20, 100.0, 2.0, l2.BUY), depth(30, 100.0, 3.0, l2.BUY))
    assert l2.book_at(io.BytesIO(src), 20).best_bid() == (100 * P, 2 * P)
    assert l2.book_at(io.BytesIO(src), 9).best_bid() is None
