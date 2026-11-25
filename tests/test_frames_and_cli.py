import json
import struct

import pytest

import alphaquick as aq
from alphaquick import cli, l2, orderlog as ol

from build import P, T0, block, container, depth, header, l2_trade, meta, order, outcome, snap, trade, user
from test_l2 import G, GAPPED_LINES, S2, TR2, gapped
from test_orderlog import B1, OUTCOME_TEXT, capture, outcome_capture

pd = pytest.importorskip("pandas")
frames = pytest.importorskip("alphaquick.frames")


@pytest.fixture
def l2_file(tmp_path):
    path = tmp_path / "BTC_HYPERLIQUID_20261023.bin.lz4"
    path.write_bytes(container(
        depth(T0, 100.0, 1.0, l2.BUY | l2.CLEAR),
        depth(T0 + 10, 101.0, 2.0, l2.SELL | l2.END_OF_TRANSACTION)
        + l2_trade(T0 + 20, 100.5, 0.25, l2.SIDE_SELL, 5)
        + depth(T0 + 30, 100.0, 0.0, l2.BUY)
        + depth(T0 + 40, 99.5, 3.0, l2.BUY | l2.END_OF_TRANSACTION),
    ))
    return path


@pytest.fixture
def orderlog_file(tmp_path):
    path = tmp_path / "xyz_CL_HLC_20261023.bin.lz4"
    path.write_bytes(capture().getvalue())
    return path


@pytest.fixture
def outcome_file(tmp_path):
    path = tmp_path / "HIP4_3600_Fed-decision_HLC_20261023.bin.lz4"
    path.write_bytes(outcome_capture().getvalue())
    return path


def test_l2_frames(l2_file):
    trades = frames.l2_trades(l2_file)
    assert list(trades.columns) == ["time", "price", "volume", "side", "trade_id"]
    assert trades.iloc[0][["price", "volume", "side", "trade_id"]].tolist() == [100.5, 0.25, "sell", 5]
    assert str(trades["time"].dt.tz) == "UTC"
    quotes = frames.l2_quotes(l2_file)
    assert quotes[["bid", "ask"]].values.tolist() == [[100.0, 101.0], [99.5, 101.0]]
    exact = frames.l2_quotes(l2_file, exact=True)
    assert exact["bid"].tolist() == [10_000_000_000, 9_950_000_000]


def test_orderlog_frames(orderlog_file, outcome_file):
    trades = frames.orderlog_trades(orderlog_file)
    assert "kind" not in trades.columns
    first = trades.iloc[0]
    assert first[["block", "price", "size", "taker_side", "maker", "taker_oid"]].tolist() == [500, 100.0, 0.5, "sell", "0x" + "aa" * 20, 99]
    assert pd.isna(trades.iloc[1].taker_oid)  # the second trade names no taker order
    assert str(trades["maker_oid"].dtype) == "Int64"
    orders = frames.orderlog_orders(orderlog_file)
    assert "badAloPxRejected" in orders["status"].tolist()
    assert "badAloPxRejected" not in frames.orderlog_orders(orderlog_file, include_rejects=False)["status"].tolist()
    assert orders.iloc[1][["tif", "order_type"]].tolist() == ["NONE", "STOP_MARKET"]
    kinds = frames.orderlog_trades(outcome_file)["kind"].tolist()
    assert kinds == ["first_book", "mint", "merge", "second_book", "unknown", "first_book"]
    replay = aq.Replay(orderlog_file).run(until=B1)
    book = frames.book_frame(replay.book, replay.accounts)
    assert book[["side", "price", "queue"]].values.tolist() == [["bid", 100.0, 0], ["bid", 100.0, 1], ["ask", 101.0, 0]]


def test_cli_info_book_dump(l2_file, orderlog_file, outcome_file, capsys):
    assert cli.main(["info", str(orderlog_file)]) == 0
    out = capsys.readouterr().out
    assert "Order log: xyz:CL (xyz), day 20261023" in out
    assert "3 orders resting at 00:00 UTC, 3 blocks, 8 order events, 2 trades, 2 accounts" in out
    assert "best bid 100, best ask 101" in out
    assert cli.main(["info", str(outcome_file)]) == 0
    out = capsys.readouterr().out
    assert "HIP-4 outcome market 3600" in out and "mint 1" in out and "unknown 1" in out
    assert "Order log: Fed decision \u2014 No change (outcome)" in out  # the full title, not META's
    assert cli.main(["info", str(l2_file)]) == 0
    assert "BTC on HYPERLIQUID" in capsys.readouterr().out
    assert cli.main(["book", str(orderlog_file), "--at", "00:00:00.1", "--queue", "5"]) == 0
    out = capsys.readouterr().out
    assert "book at 2026-10-23 00:00:00.100000 UTC" in out and "queue at the best bid 100" in out
    assert cli.main(["book", str(l2_file), "--levels", "1"]) == 0
    assert "99.5" in capsys.readouterr().out
    assert cli.main(["dump", str(orderlog_file), "--type", "trade", "--limit", "1"]) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["type"] == "trade" and line["maker_oid"] == 10 and line["datetime"].startswith("2026-10-23T00:00:00.1")


def test_cli_to_parquet_and_errors(l2_file, orderlog_file, tmp_path, capsys):
    pytest.importorskip("pyarrow")
    out_dir = tmp_path / "out"
    assert cli.main(["to-parquet", str(orderlog_file), "-o", str(out_dir), "--orders"]) == 0
    assert sorted(p.name for p in out_dir.iterdir()) == ["xyz_CL_HLC_20261023.orders.parquet", "xyz_CL_HLC_20261023.trades.parquet"]
    assert len(pd.read_parquet(out_dir / "xyz_CL_HLC_20261023.trades.parquet")) == 2
    assert cli.main(["to-parquet", str(l2_file), "-o", str(out_dir)]) == 0
    assert (out_dir / "BTC_HYPERLIQUID_20261023.quotes.parquet").exists()
    broken = tmp_path / "broken.bin.lz4"
    broken.write_bytes(container(depth(T0, 1.0, 1.0, 1))[:-3])
    assert cli.main(["info", str(broken)]) == 1
    assert "alphaquick:" in capsys.readouterr().err
    assert cli.main(["book", str(l2_file), "--at", "nonsense"]) == 1
    # Cut inside its second frame: what the file holds is still reported, with a warning.
    whole = container(depth(T0, 1.0, 1.0, 1), depth(T0 + 1, 2.0, 1.0, 1) * 40)
    assert cli.main(["info", str(tmp_path / "missing.bin.lz4")]) == 1
    capsys.readouterr()
    l2_cut = tmp_path / "ETH_HYPERLIQUID_20261023.bin.lz4"
    l2_cut.write_bytes(whole[:-5])
    assert cli.main(["info", str(l2_cut)]) == 0
    out = capsys.readouterr().out
    assert "1 depth updates" in out and "warning: the file is cut short" in out


def test_parse_moment():
    from datetime import date, datetime, timezone
    day = date(2026, 11, 1)
    assert cli.parse_moment("9:05", day) == datetime(2026, 11, 1, 9, 5, tzinfo=timezone.utc)
    assert cli.parse_moment("12:00:30.5", day) == datetime(2026, 11, 1, 12, 0, 30, 500_000, tzinfo=timezone.utc)
    assert cli.parse_moment("2026-11-02T01:00Z", None) == datetime(2026, 11, 2, 1, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        cli.parse_moment("12:00", None)


def test_detect(l2_file, orderlog_file, tmp_path):
    assert aq.detect(l2_file) == "l2" and aq.detect(orderlog_file) == "orderlog"
    empty = tmp_path / "empty.bin.lz4"
    empty.write_bytes(b"")
    assert aq.detect(empty) is None
    assert ol.KIND_META == 16


# -- 0.2.0 ---------------------------------------------------------------------


@pytest.fixture
def gapped_file(tmp_path):
    path = tmp_path / "SYM_EX_20261023.bin.lz4"
    path.write_bytes(gapped())
    (tmp_path / "SYM_EX_20261023.continuity.jsonl").write_text("".join(json.dumps(x) + "\n" for x in GAPPED_LINES))
    return path


def test_l2_quotes_do_not_carry_a_book_through_a_gap(gapped_file):
    quotes = frames.l2_quotes(gapped_file)
    assert quotes["time"].is_monotonic_increasing
    values = quotes[["bid", "ask"]].values.tolist()
    assert values[:2] == [[100.0, 101.0], [100.5, 101.0]] and values[3] == [99.0, 99.5]
    assert pd.isna(values[2][0]) and pd.isna(values[2][1])  # the gap: no quote from here
    assert quotes["time"].iloc[2] == pd.Timestamp(aq.ticks_to_datetime(G))
    exact = frames.l2_quotes(gapped_file, exact=True)
    assert str(exact["bid"].dtype) == "Int64" and exact["bid"].isna().tolist() == [False, False, True, False]
    assert exact["bid"].iloc[0] == 100 * P
    plain = frames.l2_quotes(gapped_file, continuity=None)  # gaps not read: the old rows only
    assert len(plain) == 3 and not plain["bid"].isna().any()
    # a size too large for a float stays exact
    big = gapped_file.parent / "BIG_EX_20261023.bin.lz4"
    huge = header(0, 29, T0) + struct.pack("<qqB", P, 100_000_000_000_000_001, l2.BUY | l2.CLEAR)  # 1e9 + 1e-8
    big.write_bytes(container(huge + depth(T0, 2.0, 1.0, l2.SELL | l2.END_OF_TRANSACTION)))
    assert frames.l2_quotes(big, exact=True)["bid_size"].iloc[0] == 100_000_000_000_000_001


def test_cli_warns_inside_a_gap(gapped_file, capsys):
    moment = aq.ticks_to_datetime(TR2).strftime("%H:%M:%S.%f")
    assert cli.main(["book", str(gapped_file), "--at", moment]) == 0
    out = capsys.readouterr().out
    assert "warning: the moment asked is inside a gap in the feed since 2026-10-23" in out and "(sequence)" in out
    assert cli.main(["book", str(gapped_file), "--at", aq.ticks_to_datetime(S2).strftime("%H:%M:%S.%f")]) == 0
    assert "warning" not in capsys.readouterr().out
    assert cli.main(["book", str(gapped_file), "--at", moment, "--continuity", str(gapped_file) + ".none"]) == 1
    assert "alphaquick:" in capsys.readouterr().err
    assert cli.main(["info", str(gapped_file)]) == 0
    out = capsys.readouterr().out
    assert "gaps in the feed (continuity file): 1 (sequence 1)" in out and "warning" not in out


def test_orderlog_frames_v2(tmp_path):
    path = tmp_path / "HMSTR_HLC_20261023.bin.lz4"
    system = ol.TRADE_MAKER_OID | (ol.TRADE_SYSTEM_PRINT << ol.TRADE_DIRECTION_SHIFT)
    path.write_bytes(container(
        meta("HMSTR") + user(1, 0xAA) + user(2, 0xBB) + snap(10, ol.BID, 1.0, 5.0, 1),
        block(500, B1)
        + order(ol.Status.PERP_MAX_POSITION_REJECTED, 20, ol.BID, 1.2, 1.0, 2, time=B1)
        + order(ol.Status.PRIORITY_ALO_REDUCE_ONLY_REJECTED, 21, ol.ASK, 0.8, 1.0, 2, tif=ol.Tif.ALO, time=B1)
        + trade(ol.ASK, 1.0, 1.0, 10, 0, 1, 2, flags=ol.TRADE_MAKER_OID, time=B1)
        + trade(ol.ASK, 1.0, 2.0, 77, 0, 2, 1, flags=system, time=B1)
        + order(ol.Status.DELISTED_CANCELED, 10, ol.BID, 1.0, 4.0, 1, time=B1),
    ))
    trades = frames.orderlog_trades(path)
    assert trades["system_print"].tolist() == [False, True] and trades["system_print"].dtype == bool
    assert trades.loc[~trades.system_print, "size"].sum() == 1.0
    orders = frames.orderlog_orders(path, include_rejects=False)
    assert orders["status"].tolist() == ["delistedCanceled"]
    assert len(frames.orderlog_orders(path)) == 3


def test_outcome_book_frame_and_cli_queues(tmp_path, capsys):
    path = tmp_path / "HIP4_3600_HLC_20261023.bin.lz4"
    path.write_bytes(container(
        meta("HIP4:3600", flags=ol.META_OUTCOME) + outcome(OUTCOME_TEXT) + user(1, 1) + user(2, 2),
        block(1, B1)
        + order(ol.Status.OPEN, 1, ol.BID, 0.60, 5, 1, time=B1)
        + order(ol.Status.OPEN, 2, ol.BID, 0.60, 7, 2, flags=ol.ORDER_SECOND_SIDE, time=B1)
        + order(ol.Status.OPEN, 3, ol.BID, 0.60, 1, 1, time=B1)
        + order(ol.Status.OPEN, 4, ol.ASK, 0.61, 1, 1, time=B1),
    ))
    replay = aq.Replay(path).run()
    book = frames.book_frame(replay.book, replay.accounts)
    assert book[["oid", "queue", "second_side"]].values.tolist() == [[1, 0, False], [3, 1, False], [2, 0, True], [4, 0, False]]
    assert cli.main(["book", str(path), "--queue", "5"]) == 0
    out = capsys.readouterr().out
    assert "queue at the best bid 0.6 (Yes's book), front first:" in out
    assert "queue at the best bid 0.6 (No's book), front first:" in out
    assert "(No's book)" not in out.split("queue at the best ask")[1]  # no empty queue listed
    assert cli.main(["info", str(path)]) == 0
    assert "Order log: Fed decision — No change (outcome)" in capsys.readouterr().out
