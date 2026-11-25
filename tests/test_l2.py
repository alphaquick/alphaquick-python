import io
import json
from datetime import datetime, timezone

import pytest

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


def test_book_at_stops_on_depth_updates_only():
    # A trade stamped after the moment, written before a depth update stamped before it.
    src = container(depth(10, 100.0, 1.0, l2.BUY | l2.END_OF_TRANSACTION), l2_trade(25, 100.0, 1.0, l2.SIDE_BUY),
                    depth(19, 100.0, 2.0, l2.BUY | l2.END_OF_TRANSACTION), depth(30, 100.0, 3.0, l2.BUY | l2.END_OF_TRANSACTION))
    assert l2.book_at(io.BytesIO(src), 20).best_bid() == (100 * P, 2 * P)


# -- continuity and gaps -------------------------------------------------------

MS = 10_000  # ticks per millisecond


def ms(ticks: int) -> int:
    return (ticks - aq.TICKS_AT_UNIX_EPOCH) // MS


def seed_line(at: int, reason: str = "snapshot", ordinal: int = 1) -> dict:
    return {"kind": "seed", "reason": reason, "ordinal": ordinal, "exchange": "EX", "symbol": "SYM@EX",
            "ts_ms": ms(at), "received_at_ms": ms(at), "written_at_ms": ms(at) + 1, "seq": None, "levels": 2,
            "epoch": None, "bt_ms": None}


def gap_line(at: int, reason: str = "sequence", ordinal: int = 2) -> dict:
    return {"kind": "gap", "reason": reason, "ordinal": ordinal, "exchange": "EX", "symbol": "SYM@EX",
            "received_at_ms": ms(at), "written_at_ms": ms(at) + 1}


def seed(at: int, bid: float, ask: float) -> bytes:
    return depth(at, bid, 1.0, l2.BUY | l2.CLEAR) + depth(at, ask, 1.0, l2.SELL | l2.END_OF_TRANSACTION)


S1, D1, TR1, G, TR2, S2, TR3 = (T0 + k * 10 * MS for k in range(7))  # 10 ms apart


def gapped() -> bytes:
    """A seed, a delta, a trade, (the gap), a trade, the resync seed, a trade: as the collector writes them."""
    return container(seed(S1, 100.0, 101.0) + depth(D1, 100.5, 2.0, l2.BUY | l2.END_OF_TRANSACTION)
                     + l2_trade(TR1, 101.0, 1.0, l2.SIDE_BUY, 1) + l2_trade(TR2, 100.0, 1.0, l2.SIDE_SELL, 2)
                     + seed(S2, 99.0, 99.5) + l2_trade(TR3, 99.5, 1.0, l2.SIDE_BUY, 3))


GAPPED_LINES = [seed_line(S1), gap_line(G), seed_line(S2, "resync", 3)]


def kinds(events):
    return [type(e).__name__ if type(e) is not l2.DepthUpdate else ("Clear" if e.flags & l2.CLEAR else "Depth")
            for e in events]


def test_read_continuity(tmp_path):
    path = tmp_path / "SYM_EX_20261023.continuity.jsonl"
    whole = json.dumps(seed_line(S1)) + "\n"
    glued = '{"kind":"gap","rea' + json.dumps(gap_line(G))  # a cut-short write, then the next line
    future = json.dumps({"kind": "something_new", "x": 1, "reason": "?"})
    path.write_text(whole + glued + "\n\n" + future + "\n" + '{"kind":"seed","reas')  # the last line cut short
    lines = aq.read_continuity(path)
    assert [line["kind"] for line in lines] == ["seed", "gap", "something_new"]
    assert lines[1] == gap_line(G) and lines[2]["x"] == 1  # unknown kinds and fields kept
    assert aq.read_continuity(io.BytesIO(whole.encode())) == [seed_line(S1)]
    assert aq.read_continuity(io.StringIO("")) == []
    path.write_text(whole + "not json\n" + whole)
    with pytest.raises(aq.FormatError, match="line 2"):
        aq.read_continuity(path)


def test_gaps_come_between_the_records():
    events = list(aq.read_l2(io.BytesIO(gapped()), continuity=GAPPED_LINES))
    assert kinds(events) == ["Clear", "Depth", "Depth", "Trade", "Gap", "Trade", "Clear", "Depth", "Trade"]
    assert events[4] == l2.Gap(G, "sequence") and events[4].datetime == aq.ticks_to_datetime(G)
    assert kinds(aq.read_l2(io.BytesIO(gapped()))) == [k for k in kinds(events) if k != "Gap"]  # none by default
    as_text = [json.dumps(line) for line in GAPPED_LINES]
    assert list(aq.read_l2(io.BytesIO(gapped()), continuity=as_text)) == events


def test_a_gap_never_comes_after_the_seed_that_ends_it():
    # The recorder's clock is ahead of the venue's: the gap's time is after
    # every record of the file, yet it ends at the resync seed.
    late = [seed_line(S1), gap_line(TR3 + 1000 * MS), seed_line(S2, "resync")]
    events = list(aq.read_l2(io.BytesIO(gapped()), continuity=late))
    assert kinds(events) == ["Clear", "Depth", "Depth", "Trade", "Trade", "Gap", "Clear", "Depth", "Trade"]
    # ... and never before the seed it follows, nor inside a transaction.
    early = [seed_line(S1), gap_line(S1 - 1000 * MS), seed_line(S2, "resync")]
    assert kinds(aq.read_l2(io.BytesIO(gapped()), continuity=early))[:4] == ["Clear", "Depth", "Gap", "Depth"]
    # A gap that no seed ends: the book stays unknown to the end of the file.
    unended = container(seed(S1, 100.0, 101.0) + l2_trade(TR1, 101.0, 1.0, l2.SIDE_BUY, 1))
    tail = list(aq.read_l2(io.BytesIO(unended), continuity=[seed_line(S1), gap_line(TR3)]))
    assert kinds(tail) == ["Clear", "Depth", "Trade", "Gap"] and tail[-1].time == TR3


def test_gaps_of_the_earlier_recorder():
    # Node venues up to 2026-11-25: the gap line follows the seed that ends it
    # and times the break with detected_at_ms.
    old = [
        {"kind": "seed", "reason": "snapshot", "ordinal": 1, "received_at_ms": ms(S1)},
        {"kind": "incident", "detected_at_ms": ms(G), "reason": "sequence_jump"},
        {"kind": "seed", "reason": "snapshot", "ordinal": 3, "received_at_ms": ms(S2)},
        {"kind": "gap", "detected_at_ms": ms(TR3 + 1000 * MS), "reason": "sequence_jump",
         "last_good_ordinal": 2, "clear_ordinal": 3, "exact": False},
        {"kind": "writer_failure", "error": "disk full"},
    ]
    events = list(aq.read_l2(io.BytesIO(gapped()), continuity=old))
    assert kinds(events) == ["Clear", "Depth", "Depth", "Trade", "Trade", "Gap", "Clear", "Depth", "Trade"]
    assert events[5].reason == "sequence_jump"
    old[3]["detected_at_ms"] = ms(G)
    assert kinds(aq.read_l2(io.BytesIO(gapped()), continuity=old))[4] == "Gap"


def test_the_book_is_not_known_inside_a_gap():
    book = aq.L2Book()
    states = []
    for event in aq.read_l2(io.BytesIO(gapped()), continuity=GAPPED_LINES):
        if type(event) is not l2.Trade:
            book.apply(event)  # a Gap goes to apply_gap
        states.append(book.known)
    assert states == [True, True, True, True, False, False, True, True, True]
    assert book.gap is None
    book.apply_gap(l2.Gap(1, "disconnect"))
    assert not book.known and book.gap.reason == "disconnect" and book.best_bid() == (99 * P, P)  # stale levels stay
    path_src = io.BytesIO(gapped())
    inside = l2.book_at(path_src, TR2, continuity=GAPPED_LINES)
    assert not inside.known and inside.consistent and inside.gap == l2.Gap(G, "sequence")
    assert l2.book_at(io.BytesIO(gapped()), TR1, continuity=GAPPED_LINES).known
    after = l2.book_at(io.BytesIO(gapped()), S2, continuity=GAPPED_LINES)
    assert after.known and after.best_bid() == (99 * P, P)


def test_auto_finds_the_continuity_file_next_to_the_data(tmp_path):
    data = tmp_path / "SYM_EX_20261023.bin.lz4"
    data.write_bytes(gapped())
    assert not any(type(e) is l2.Gap for e in aq.read_l2(data, continuity="auto"))  # none there yet
    side = tmp_path / "SYM_EX_20261023.continuity.jsonl"
    side.write_text("".join(json.dumps(line) + "\n" for line in GAPPED_LINES))
    assert l2.continuity_path(data) == str(side)
    assert [e for e in aq.read_l2(data, continuity="auto") if type(e) is l2.Gap] == [l2.Gap(G, "sequence")]
    assert [e for e in aq.read_l2(data, continuity=side) if type(e) is l2.Gap] == [l2.Gap(G, "sequence")]
    assert not l2.book_at(data, TR2).known  # book_at reads it by default
    assert l2.book_at(data, TR2, continuity=None).known
