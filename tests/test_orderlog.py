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


# -- statuses and the converter's records (format v2) -------------------------

# The writer's own tables, copied from hl_convert/hlc/src/records.rs
# (status_from_str, status_removes, status_is_reject): the reader must agree
# with them code for code.
WRITER_NAMES = {
    0: "open", 1: "filled", 2: "canceled", 3: "badAloPxRejected", 4: "perpMarginRejected",
    5: "minTradeNtlRejected", 6: "iocCancelRejected", 7: "tooManyOpenOrdersRejected",
    8: "insufficientSpotBalanceRejected", 9: "reduceOnlyCanceled", 10: "reduceOnlyRejected",
    11: "oracleRejected", 12: "positionIncreaseAtOpenInterestCapRejected", 13: "selfTradeCanceled",
    14: "triggered", 15: "siblingFilledCanceled", 16: "positionFlipAtOpenInterestCapRejected",
    17: "outcomeSettledCanceled", 18: "scheduledCancel", 19: "marginCanceled", 20: "liquidatedCanceled",
    21: "tooAggressiveAtOpenInterestCapRejected", 22: "openInterestCapCanceled",
    23: "openInterestIncreaseRejected", 24: "vaultWithdrawalCanceled", 25: "perpMaxPositionRejected",
    26: "delistedCanceled", 27: "insufficientWeiCanceled", 28: "priorityAloReduceOnlyRejected",
}
WRITER_REMOVES = {1, 2, 9, 13, 15, 17, 18, 19, 20, 22, 24, 26, 27, 250}
WRITER_REJECTS = {3, 4, 5, 6, 7, 8, 10, 11, 12, 16, 21, 23, 25, 28}


def test_every_status_code_matches_the_writer():
    assert ol.REMOVING_STATUSES == WRITER_REMOVES and ol.REJECT_STATUSES == WRITER_REJECTS
    for code in list(range(29)) + [250, 251, 252, 255]:
        assert ol.Status(code).value == code
        if code in WRITER_NAMES:
            assert ol.status_name(code) == WRITER_NAMES[code]
        # On the book: an order resting at 100, then an event of this status for it.
        book = ol.OrderBook()
        book.insert(1, BID, 100 * P, 2 * P, 7)
        book.apply_order(ol.Order(T0, code, BID, ol.Tif.GTC, 0, 0, 1, 100 * P, P, 7))
        if code in WRITER_REMOVES:
            assert 1 not in book.orders, code
        elif code == 252:
            assert book.orders[1].size == P  # resized in place
        elif code in (0, 251):
            assert book.orders[1].size == P  # placed again (replaced)
        else:
            assert book.orders[1].size == 2 * P, code  # rejects, triggered, other: no effect
        # A new order of a reject status never rests.
        book.apply_order(ol.Order(T0, code, ASK, ol.Tif.GTC, 0, 0, 2, 101 * P, P, 7))
        assert (2 in book.orders) == (code in (0, 251)), code


def test_new_statuses_24_to_28():
    book_file = io.BytesIO(container(
        meta("PURR/USDC") + user(1, 0xAA)
        + snap(10, BID, 1.0, 1.0, 1) + snap(11, BID, 0.9, 1.0, 1) + snap(12, ASK, 1.1, 1.0, 1),
        block(500, B1)
        + order(ol.Status.PERP_MAX_POSITION_REJECTED, 20, BID, 1.2, 1.0, 1, time=B1)
        + order(ol.Status.PRIORITY_ALO_REDUCE_ONLY_REJECTED, 21, ASK, 0.8, 1.0, 1, tif=ol.Tif.ALO, flags=ol.ORDER_REDUCE_ONLY, time=B1)
        + order(ol.Status.VAULT_WITHDRAWAL_CANCELED, 10, BID, 1.0, 1.0, 1, time=B1)
        + order(ol.Status.DELISTED_CANCELED, 11, BID, 0.9, 1.0, 1, time=B1)
        + order(ol.Status.INSUFFICIENT_WEI_CANCELED, 12, ASK, 1.1, 1.0, 1, time=B1),
    ))
    replay = aq.Replay(book_file).run()
    assert len(replay.book) == 0 and replay.book.best_bid() is None  # 24, 26, 27 removed; 25, 28 never rested
    assert [ol.status_name(c) for c in range(24, 29)] == [
        "vaultWithdrawalCanceled", "perpMaxPositionRejected", "delistedCanceled",
        "insufficientWeiCanceled", "priorityAloReduceOnlyRejected"]
    assert ol.Status.INSUFFICIENT_WEI_CANCELED == 27 and ol.Status.PRIORITY_ALO_REDUCE_ONLY_REJECTED == 28


def converter_capture() -> io.BytesIO:
    """The converter's own records, in the order it writes them."""
    RO = ol.ORDER_REDUCE_ONLY
    return io.BytesIO(container(
        meta("HMSTR") + user(1, 0xAA) + user(2, 0xBB) + user(3, 0xFF)
        + snap(10, ASK, 101.0, 5.0, 2, flags=RO) + snap(11, ASK, 101.0, 2.0, 1),
        block(500, B1)
        # a Hyperliquidity order, rebuilt, placed in this block
        + order(ol.Status.CORRECTION_RESTS, 30, BID, 99.0, 4.0, 3, flags=ol.ORDER_REBUILT, time=B1)
        # a stop-limit order triggers and starts resting (no open for it)
        + order(ol.Status.TRIGGERED, 31, BID, 98.0, 1.0, 1, tif=ol.Tif.NONE, order_type=ol.OrderType.STOP_LIMIT,
                flags=ol.ORDER_TRIGGER, time=B1)
        + order(ol.Status.CORRECTION_RESTS, 31, BID, 98.0, 1.0, 1, order_type=ol.OrderType.STOP_LIMIT,
                flags=ol.ORDER_TRIGGER, time=B1)
        # account 2 buys back 3 of its short: its reduce-only ask of 5 is capped at what is left
        + trade(BID, 101.0, 1.0, 10, 40, 2, 1, flags=ol.TRADE_MAKER_OID, maker_pos=-3.0, time=B1)
        + order(ol.Status.CORRECTION_SIZE, 10, ASK, 101.0, 2.0, 2, tif=0, flags=RO | ol.ORDER_RO_CAP, time=B1),
        # snapshot corrections, in a block of their own
        block(1_000_000, B2)
        + order(ol.Status.CORRECTION_GONE, 31, BID, 98.0, 1.0, 1, tif=0, flags=ol.ORDER_SNAPSHOT_FIX, time=B2)
        + order(ol.Status.CORRECTION_SIZE, 11, ASK, 101.0, 1.5, 1, tif=0, flags=ol.ORDER_SNAPSHOT_FIX, time=B2)
        + order(ol.Status.CORRECTION_RESTS, 32, BID, 99.0, 1.0, 2, flags=ol.ORDER_SNAPSHOT_FIX, time=B2),
    ))


def test_the_converter_records_apply_in_file_order():
    assert (ol.ORDER_RO_CAP, ol.ORDER_REBUILT, ol.ORDER_SNAPSHOT_FIX, ol.ORDER_SECOND_SIDE) == (0x10, 0x20, 0x40, 0x80)
    replay = aq.Replay(converter_capture()).run(until=B1)
    book = replay.book
    assert book.orders[30].size == 4 * P and book.orders[31].size == P  # rebuilt and triggered orders rest
    assert book.orders[10].size == 2 * P  # 5, filled 1, then capped to 2 after the trade
    assert book.queue_position(11) == (1, 2 * P)  # the cap keeps 10's place in the queue
    replay.run()
    assert replay.block.number == 1_000_000
    assert 31 not in book.orders and book.orders[11].size == 150_000_000 and book.orders[32].size == P
    assert [o.oid for o in book.queue(BID, 99 * P)] == [30, 32]


def test_system_prints():
    flags = ol.TRADE_MAKER_OID | ol.TRADE_TAKER_OID
    plain = ol.Trade(T0, BID, flags, P, P, 1, 2, 1, 2, 0, 0)
    system = plain._replace(flags=flags | (ol.TRADE_SYSTEM_PRINT << ol.TRADE_DIRECTION_SHIFT))
    sell = plain._replace(flags=flags | (2 << ol.TRADE_DIRECTION_SHIFT))
    assert (plain.direction, plain.is_system_print) == (0, False)
    assert (system.direction, system.is_system_print) == (3, True)
    assert (sell.direction, sell.is_system_print) == (2, False)
    assert ol.TRADE_DIRECTION_MASK == 3 << ol.TRADE_DIRECTION_SHIFT


def test_outcome_files_keep_one_queue_per_side_book():
    # At the same price in first-side terms: a Yes buy at 0.60, then a No sell at
    # 0.40 (shown as a bid at 0.60 on the second book), then another Yes buy.
    data = io.BytesIO(container(
        meta("HIP4:3600", flags=ol.META_OUTCOME) + outcome(OUTCOME_TEXT) + user(1, 1) + user(2, 2),
        block(1, B1)
        + order(ol.Status.OPEN, 1, BID, 0.60, 5, 1, time=B1)
        + order(ol.Status.OPEN, 2, BID, 0.60, 7, 2, flags=SECOND, time=B1)
        + order(ol.Status.OPEN, 3, BID, 0.60, 1, 1, time=B1),
    ))
    replay = aq.Replay(data).run()
    book = replay.book
    assert book.outcome
    assert book.queue_position(2) == (0, 0)  # first on its own book, whatever rests on the other
    assert book.queue_position(3) == (1, 5 * P)  # behind 1 only
    assert [o.oid for o in book.queue(BID, 60 * P // 100)] == [1, 3]
    assert [o.oid for o in book.queue(BID, 60 * P // 100, second_side=True)] == [2]
    assert book.depth(BID) == [(60 * P // 100, 13 * P, 3)]  # the level adds up both books
    book.remove(1)
    assert book.queue_position(3) == (0, 0)
    # Turning the split off (or on) re-keys the orders in their arrival order.
    book.outcome = False
    assert [o.oid for o in book.queue(BID, 60 * P // 100)] == [2, 3] and book.queue(BID, 60 * P // 100, True) == []
    assert book.queue_position(3) == (1, 7 * P)
    book.outcome = True
    assert book.queue_position(3) == (0, 0) and book.depth(BID) == [(60 * P // 100, 8 * P, 2)]
    # Outside outcome files there is one queue, as before.
    assert not aq.Replay(capture()).run().book.outcome
