from datetime import datetime, timezone

import alphaquick as aq
from alphaquick import names


def test_l2_names_split_the_venue_from_the_end():
    cases = {
        "BTCUSDT_BINANCE_USDM_20261101.bin.lz4": ("BTCUSDT", "BINANCE_USDM"),
        "BTCUSDT_261225_BINANCE_USDM_20261101.bin.lz4": ("BTCUSDT_261225", "BINANCE_USDM"),
        "BTC-USDT-SWAP_OKX_20261101.bin.lz4": ("BTC-USDT-SWAP", "OKX"),
        "YB-USD_UM_XPERP-310829_OKX_20261101.bin.lz4": ("YB-USD_UM_XPERP-310829", "OKX"),
        "YGG_HYPERLIQUID_NODE_B_bbo_20261101.bin.lz4": ("YGG", "HYPERLIQUID_NODE_B_bbo"),
        "BTC_HYPERLIQUID_20261101.bin.lz4": ("BTC", "HYPERLIQUID"),
        "BTC_HYPERLIQUID_bbo_20261101.bin.lz4": ("BTC", "HYPERLIQUID_bbo"),
        "xyz_TSLA_HYPERLIQUID_bbo_20261101.bin.lz4": ("xyz_TSLA", "HYPERLIQUID_bbo"),
        "xyz_TSLA_HYPERLIQUID_20261101.bin.lz4": ("xyz_TSLA", "HYPERLIQUID"),
        "KRW-BTC_UPBIT_20261101.bin.lz4": ("KRW-BTC", "UPBIT"),
        "ABC_NEWVENUE_20261101.bin.lz4": ("ABC", "NEWVENUE"),
    }
    for name, (symbol, venue) in cases.items():
        parsed = names.parse_l2_name("/data/20261101/" + name)
        assert (parsed.symbol, parsed.venue, parsed.day, parsed.continuity) == (symbol, venue, "20261101", False), name
    side = names.parse_l2_name("BTC_HYPERLIQUID_20261101.continuity.jsonl")
    assert side.continuity and side.venue == "HYPERLIQUID"
    side = names.parse_l2_name("xyz_TSLA_HYPERLIQUID_bbo_20261126.continuity.jsonl")
    assert (side.symbol, side.venue, side.continuity) == ("xyz_TSLA", "HYPERLIQUID_bbo", True)
    assert names.parse_l2_name("BTC_HLC_20261101.bin.lz4") is None
    assert names.parse_l2_name("notes.txt") is None
    assert names.parse_l2_name("BTC_OKX_\uff120261101.bin.lz4") is None  # only ASCII digits make a day


def test_orderlog_names_read_back_the_coin():
    cases = {
        "BTC": ("BTC", "perp"), "kBONK": ("kBONK", "perp"), "xyz_CL": ("xyz:CL", "xyz"),
        "xyz_BRK.B": ("xyz:BRK.B", "xyz"), "PURR_USDC": ("PURR/USDC", "spot"), "_107": ("@107", "spot"),
        "#114220": ("#114220", "outcome"),
        "HIP4_3600_Fed-Oct-no-change": ("HIP4_3600_Fed-Oct-no-change", "outcome"),
        # the converter leaves the short name out when the title gives none: still an outcome file
        "HIP4_3600": ("HIP4_3600", "outcome"),
        "HIP4_1473_2026-2027-English-Premier-League-winner-A": ("HIP4_1473_2026-2027-English-Premier-League-winner-A", "outcome"),
        "ABC_USDC": ("ABC/USDC", "spot"),
    }
    for stem, (coin, market) in cases.items():
        parsed = names.parse_orderlog_name(f"{stem}_HLC_20261101.bin.lz4")
        assert (parsed.coin, parsed.market, parsed.day) == (coin, market, "20261101"), stem
        assert names.orderlog_stem(coin) == stem
    assert names.parse_orderlog_name("BT C_HLC_20261101.bin.lz4") is None
    assert names.parse_orderlog_name("/data/hlc_v2/20261117/hip4/HIP4_1473_HLC_20261117.bin.lz4").market == "outcome"
    assert names.file_kind("xyz_CL_HLC_20261101.bin.lz4") == "orderlog"
    assert names.file_kind("BTCUSDT_BINANCE_USDM_20261101.bin.lz4") == "l2"
    assert names.file_kind("ZEC_HLC_20261014_sample.bin.lz4") is None


def test_times_and_fixed_point():
    moment = datetime(2026, 11, 1, 12, 30, 15, 250_000, tzinfo=timezone.utc)
    ticks = aq.datetime_to_ticks(moment)
    assert aq.ticks_to_datetime(ticks) == moment
    assert aq.ticks_to_unix_ns(ticks) == 1_793_536_215 * 10**9 + 250_000_000
    assert aq.datetime_to_ticks(datetime(1970, 1, 1)) == aq.TICKS_AT_UNIX_EPOCH  # naive means UTC
    assert aq.unix_ns_to_datetime(1_792_713_600_000_000_000) == datetime(2026, 10, 23, tzinfo=timezone.utc)
    assert aq.to_float(12_345_000_000) == 123.45
