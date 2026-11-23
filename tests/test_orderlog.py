import io

import pytest

import alphaquick as aq
from alphaquick import orderlog as ol

from build import P, T0, block, container, depth, meta, order, outcome, snap, trade, user

BID, ASK = ol.BID, ol.ASK
B1, B2, B3 = T0 + 1_000_000, T0 + 2_000_000, T0 + 3_000_000  # three block times, 0.1 s apart


def capture() -> io.BytesIO:
    """A small day: two accounts, a resting book, then three blocks of events."""
    return io.BytesIO(container(
        meta("xyz:CL"),
        user(1, 0xAA) + user(2, 0xBB)
        + snap(10, BID, 100.0, 1.0, 1) + snap(11, BID, 100.0, 2.0, 2) + snap(12, ASK, 101.0, 3.0, 1)
        + depth(T0, 100.0, 3.0, 1),  # the aggregated L2 view: skipped by the order-log reader
        block(500, B1)
        + order(ol.Status.OPEN, 13, BID, 100.0, 4.0, 2, time=B1)                # back of the 100 queue
        + order(ol.Status.OPEN, 14, ASK, 105.0, 1.0, 1, tif=ol.Tif.NONE, order_type=ol.OrderType.STOP_MARKET, time=B1)  # a trigger order waits off the book
        + order(ol.Status.BAD_ALO_PX_REJECTED, 15, BID, 101.0, 1.0, 2, time=B1)  # never rested
        + trade(ASK, 100.0, 0.5, 10, 99, 1, 2, flags=ol.TRADE_MAKER_OID | ol.TRADE_TAKER_OID, time=B1)  # partial fill of 10
        + order(ol.Status.CANCELED, 11, BID, 100.0, 2.0, 2, time=B1),
        block(501, B2)
        + order(ol.Status.CORRECTION_RESTS, 16, ASK, 102.0, 2.0, 2, time=B2)
        + order(ol.Status.CORRECTION_SIZE, 13, BID, 100.0, 3.5, 2, time=B2)     # new size, same place
        + trade(ASK, 100.0, 0.5, 10, 0, 1, 2, flags=ol.TRADE_MAKER_OID, time=B2)  # fills the rest of 10
        + order(ol.Status.FILLED, 10, BID, 100.0, 0.0, 1, time=B2),
        block(502, B3)
        + order(ol.Status.OPEN, 17, BID, 100.0, 1.0, 1, time=B3),
    ))


def test_records_come_back_typed():
    records = list(aq.read_orderlog(capture()))
    kinds = [type(r).__name__ for r in records]
    assert kinds[:6] == ["Meta", "User", "User", "SnapOrder", "SnapOrder", "SnapOrder"]
    assert "DepthUpdate" not in kinds
    m = records[0]
    assert (m.coin, m.day, m.size_decimals, m.asset, m.start_block, m.is_outcome) == ("xyz:CL", 20261023, 2, 7, 1000, False)
    assert records[1] == ol.User(T0, 1, "0x" + "aa" * 20)
    assert records[3] == ol.SnapOrder(T0, BID, 1, 0, 10, 100 * P, 1 * P, 1)
    o = next(r for r in records if isinstance(r, ol.Order))
    assert (o.status, o.side, o.oid, o.price, o.size, o.user_id, o.status_name) == (0, BID, 13, 100 * P, 4 * P, 2, "open")
    t = next(r for r in records if isinstance(r, ol.Trade))
    assert (t.taker_side, t.price, t.size, t.maker_oid, t.taker_oid, t.has_maker_oid, t.has_taker_oid) == (ASK, 100 * P, P // 2, 10, 99, True, True)
    b = next(r for r in records if isinstance(r, ol.Block))
    assert b.number == 500 and b.datetime.isoformat() == "2026-10-23T00:00:00.100000+00:00"


def test_book_follows_the_format_rules():
    replay = aq.Replay(capture()).run()
    book = replay.book
    assert replay.accounts == {1: "0x" + "aa" * 20, 2: "0x" + "bb" * 20}
    assert replay.block.number == 502
    assert set(book.orders) == {12, 13, 16, 17}  # 10 filled, 11 canceled, 14 waits, 15 rejected
    assert [o.oid for o in book.queue(BID, 100 * P)] == [13, 17]
    assert book.orders[13].size == 350_000_000
    assert book.queue_position(17) == (1, 350_000_000)
    assert book.queue_position(404) is None
    assert book.best_bid() == 100 * P and book.best_ask() == 101 * P
    assert book.depth(BID) == [(100 * P, 450_000_000, 2)]
    assert book.depth(ASK) == [(101 * P, 3 * P, 1), (102 * P, 2 * P, 1)]
    assert [o.oid for o in book.orders_of(1)] == [12, 17]


def test_run_until_stops_before_later_blocks():
    replay = aq.Replay(capture()).run(until=B1)
    book = replay.book
    assert replay.block.number == 500
    assert [o.oid for o in book.queue(BID, 100 * P)] == [10, 13]  # 11 canceled in this block
    assert book.orders[10].size == P // 2  # the partial fill only
    assert 16 not in book.orders
    # The first block is always read: here it is block 500, and 11 is canceled in it.
    assert 11 not in aq.Replay(capture()).run(until=T0).book.orders


def test_a_moment_before_the_day_gives_the_day_start_book():
    # Real files put the day-start orders after the seed block, whose time is
    # before 00:00 UTC; a moment before it must still give that book.
    seeded = io.BytesIO(container(
        meta("BTC") + user(1, 0xAA),
        block(499, T0 - 1000) + snap(10, BID, 100.0, 1.0, 1) + snap(11, ASK, 101.0, 1.0, 1),
        block(500, B1) + order(ol.Status.CANCELED, 10, BID, 100.0, 1.0, 1, time=B1),
    ))
    replay = aq.Replay(seeded).run(until=0)
    assert replay.block.number == 499 and sorted(replay.book.orders) == [10, 11]
    replay.run(until=B1)
    assert sorted(replay.book.orders) == [11]


def test_run_until_uses_the_block_clock():
    # USER records come before an account's first use but keep the time of the
    # block it first appears in: a later time must not stop the run early.
    early_user = io.BytesIO(container(
        meta("BTC") + user(1, 0xAA) + user(2, 0xBB, time=B3) + snap(10, BID, 100.0, 1.0, 1),
        block(500, B1) + order(ol.Status.OPEN, 11, BID, 100.0, 1.0, 1, time=B1),
        block(502, B3) + order(ol.Status.OPEN, 12, BID, 99.0, 1.0, 2, time=B3),
    ))
    replay = aq.Replay(early_user).run(until=B2)
    assert replay.block.number == 500 and sorted(replay.book.orders) == [10, 11]
    assert replay.accounts[2] == "0x" + "bb" * 20
    replay.run()
    assert sorted(replay.book.orders) == [10, 11, 12]


def test_a_replay_runs_on_in_steps():
    stepped = aq.Replay(capture())
    stepped.run(until=B1)
    assert stepped.block.number == 500 and 16 not in stepped.book.orders
    stepped.run(until=B2)
    assert stepped.block.number == 501 and 16 in stepped.book.orders and 10 not in stepped.book.orders
    rest = [type(r).__name__ for r in stepped]
    assert rest == ["Block", "Order"]  # block 502 and its open, nothing read twice
    whole = aq.Replay(capture()).run()
    assert sorted(stepped.book.orders) == sorted(whole.book.orders)
    assert stepped.book.queue_position(17) == whole.book.queue_position(17)
    assert list(stepped) == [] and stepped.run().block.number == 502  # at the end: nothing more


def test_iteration_yields_each_record_after_applying_it():
    replay = aq.Replay(capture())
    seen = []
    for record in replay:
        if isinstance(record, ol.Order) and record.oid == 13 and record.status == ol.Status.OPEN:
            seen.append(replay.book.queue_position(13))
    assert seen == [(2, 3 * P)]


def test_book_operations_directly():
    book = ol.OrderBook()
    book.insert(1, BID, 10 * P, P, 7)
    book.insert(2, BID, 10 * P, 2 * P, 8)
    book.insert(1, BID, 11 * P, P, 7)  # the same id again: replaced, at the back of its new price
    assert book.best_bid() == 11 * P and [o.oid for o in book.queue(BID, 10 * P)] == [2]
    book.insert(3, BID, 10 * P, P, 9)
    assert book.reduce(2, 5 * P) and book.orders[2].size == 0  # not below zero; stays until removed
    assert [o.oid for o in book.queue(BID, 10 * P)] == [2, 3]  # still listed, at size 0
    assert book.queue_position(3) == (0, 0)  # but not counted ahead
    assert book.depth(BID) == [(11 * P, P, 1), (10 * P, P, 1)]
    book.remove(3)
    assert not book.reduce(99, P) and not book.resize(99, P)
    assert book.remove(2).oid == 2 and book.remove(2) is None
    book.clear()
    assert len(book) == 0 and book.best_bid() is None


def test_format_errors():
    bad_magic = bytearray(meta("BTC"))
    bad_magic[12:16] = b"XXXX"
    with pytest.raises(aq.FormatError, match="HLC1"):
        list(aq.read_orderlog(io.BytesIO(container(bytes(bad_magic)))))
    short = bytearray(order(0, 1, BID, 1.0, 1.0, 1))
    short[2:4] = (20).to_bytes(2, "little")
    with pytest.raises(aq.FormatError, match="fewer than"):
        list(aq.read_orderlog(io.BytesIO(container(bytes(short[:20])))))
    assert ol.status_name(77) == "status 77" and ol.status_name(250) == "correction: gone"


# -- HIP-4 outcome markets ---------------------------------------------------

SECOND = ol.ORDER_SECOND_SIDE
OUTCOME_TEXT = "outcome=3600\ntitle=Fed decision — No change\nside0=Yes\nside1=No\nquestion_id=289\nfields=time:1792713600|price:80\nsettled=1\nnot a pair\nfuture_key=1"


def outcome_capture() -> io.BytesIO:
    """Both books of one outcome, in the first side's terms."""
    return io.BytesIO(container(
        meta("Fed decision", flags=ol.META_OUTCOME) + outcome(OUTCOME_TEXT),
        user(1, 1) + user(2, 2)
        + snap(1, BID, 0.60, 10, 1)                    # Yes buy 0.60
        + snap(2, ASK, 0.65, 10, 1, flags=SECOND)       # No buy 0.35, shown as an ask at 0.65
        + snap(3, ASK, 0.70, 10, 2)                    # Yes sell 0.70
        + snap(4, BID, 0.55, 10, 2, flags=SECOND),      # No sell 0.45, shown as a bid at 0.55
        block(1, B1)
        # taker buys Yes from the first book's ask: tokens change hands
        + trade(BID, 0.70, 1, 3, 90, 2, 1, flags=3, time=B1)
        # taker buys Yes (first book) against a No buyer (second book): a mint
        + trade(BID, 0.65, 1, 2, 91, 1, 2, flags=3, time=B1)
        # taker on the second book sells No (placed: a sell) against a Yes seller: a merge
        + trade(BID, 0.70, 1, 3, 92, 2, 1, flags=3 | ol.TRADE_SECOND_SIDE, time=B1)
        # taker on the second book buys No (placed: a buy) against a No seller: second book
        + trade(ASK, 0.55, 1, 4, 93, 2, 1, flags=3 | ol.TRADE_SECOND_SIDE, time=B1)
        # maker not in the book
        + trade(BID, 0.70, 1, 777, 94, 2, 1, flags=3, time=B1)
        # the maker's removal written before its trade in the same block: still known
        + order(ol.Status.CANCELED, 1, BID, 0.60, 9, 1, time=B1)
        + trade(ASK, 0.60, 1, 1, 95, 1, 2, flags=3, time=B1),
    ))


def test_outcome_info_parses_and_defaults():
    info = ol.OutcomeInfo.parse(OUTCOME_TEXT)
    assert (info.outcome, info.title, info.sides, info.question_id, info.settled) == (3600, "Fed decision — No change", ("Yes", "No"), 289, 1.0)
    assert info.fields == [("time", "1792713600"), ("price", "80")]
    empty = ol.OutcomeInfo.parse("outcome=12\nsettled=nan")
    assert (empty.title, empty.sides, empty.settled) == ("Outcome 12", ("Yes", "No"), None)
    assert ol.OutcomeInfo.parse("").title == "Outcome market"


def test_as_placed_undoes_the_first_side_terms():
    assert ol.as_placed(ASK, 92_302_000, SECOND) == (True, BID, 7_698_000)
    assert ol.as_placed(BID, 55 * P // 100, SECOND) == (True, ASK, 45 * P // 100)
    assert ol.as_placed(BID, 60 * P // 100, 0) == (False, BID, 60 * P // 100)


def test_trade_kinds_of_an_outcome_market():
    replay = aq.Replay(outcome_capture())
    kinds = []
    for record in replay:
        if isinstance(record, ol.Trade):
            kinds.append(replay.last_trade_kind)
    T = ol.TradeKind
    assert kinds == [T.FIRST_BOOK, T.MINT, T.MERGE, T.SECOND_BOOK, T.UNKNOWN, T.FIRST_BOOK]
    assert replay.meta.is_outcome and replay.outcome.outcome == 3600
    assert ol.trade_kind(0, BID, None) is T.UNKNOWN


def test_trade_kind_is_none_outside_outcome_files():
    replay = aq.Replay(capture())
    assert all(replay.last_trade_kind is ol.TradeKind.NONE for _ in replay)
