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
        "BTC_HYPERLIQUID_BBO_20261101.bin.lz4": ("BTC", "HYPERLIQUID_BBO"),
        "xyz_TSLA_HYPERLIQUID_BBO_20261101.bin.lz4": ("xyz_TSLA", "HYPERLIQUID_BBO"),
        "xyz_TSLA_HYPERLIQUID_20261101.bin.lz4": ("xyz_TSLA", "HYPERLIQUID"),
        "KRW-BTC_UPBIT_20261101.bin.lz4": ("KRW-BTC", "UPBIT"),
        "ABC_NEWVENUE_20261101.bin.lz4": ("ABC", "NEWVENUE"),
    }
    for name, (symbol, venue) in cases.items():
        parsed = names.parse_l2_name("/data/20261101/" + name)
        assert (parsed.symbol, parsed.venue, parsed.day, parsed.continuity) == (symbol, venue, "20261101", False), name
    side = names.parse_l2_name("BTC_HYPERLIQUID_20261101.continuity.jsonl")
    assert side.continuity and side.venue == "HYPERLIQUID"
    side = names.parse_l2_name("xyz_TSLA_HYPERLIQUID_BBO_20261126.continuity.jsonl")
    assert (side.symbol, side.venue, side.continuity) == ("xyz_TSLA", "HYPERLIQUID_BBO", True)
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


def test_orderlog_names_of_the_published_layout():
    title = "2026-2027-NBA-winner-Oklahoma-City-Thunder_id11492"
    in_folders = {
        "PERPS/BTC": ("BTC", "perp"),
        "PERPS/kPEPE": ("kPEPE", "perp"),
        "HIP3/xyz_NVDA": ("xyz:NVDA", "xyz"),
        "HIP3/xyz_BRK.B": ("xyz:BRK.B", "xyz"),
        "SPOT/HYPE_USDC": ("HYPE/USDC", "spot"),  # the venue's @107: in META and INSTRUMENTS
        "SPOT/PURR_USDC": ("PURR/USDC", "spot"),
        "SPOT/abc_USDC": ("abc/USDC", "spot"),  # the folder decides: bare, it reads as the HIP-3 dex abc
        "HIP4/Anthropic-first-day-market-cap-above-1500B_id8573": ("HIP4:8573", "outcome"),
        "HIP4/" + title: ("HIP4:11492", "outcome"),
        "HIP4/BTC-above-64800-at-2026-11-17-2250-UTC_id32511": ("HIP4:32511", "outcome"),
        "HIP4/outcome_id4021": ("HIP4:4021", "outcome"),  # a market with no title
    }
    for path, (coin, market) in in_folders.items():
        parsed = names.parse_orderlog_name(f"/data/hlc_v2/20261117/{path}_HLC_20261117.bin.lz4")
        assert (parsed.coin, parsed.market, parsed.day) == (coin, market, "20261117"), path
        assert names.file_kind(f"{path}_HLC_20261117.bin.lz4") == "orderlog", path
    # Bare names, read by their shape.
    bare = {
        "BTC": ("BTC", "perp"), "xyz_NVDA": ("xyz:NVDA", "xyz"), "HYPE_USDC": ("HYPE/USDC", "spot"),
        "abc_USDC": ("abc:USDC", "abc"), title: ("HIP4:11492", "outcome"), "outcome_id4021": ("HIP4:4021", "outcome"),
        "BTC-touches-60000-by-2026-12-01-0000-UTC_id37348": ("HIP4:37348", "outcome"),
    }
    for stem, (coin, market) in bare.items():
        parsed = names.parse_orderlog_name(f"{stem}_HLC_20261119.bin.lz4")
        assert (parsed.coin, parsed.market) == (coin, market), stem
    # A title is never shortened: longer than any other name.
    long_title = "-".join(["Will-the-Federal-Reserve-cut-rates"] * 5) + "_id99"
    assert len(long_title) > 80
    assert names.parse_orderlog_name(f"HIP4/{long_title}_HLC_20261119.bin.lz4").coin == "HIP4:99"
    assert names.parse_orderlog_name(f"{long_title}_HLC_20261119.bin.lz4").coin == "HIP4:99"
    assert names.parse_orderlog_name(f"PERPS/{'A' * 81}_HLC_20261119.bin.lz4") is None
    # In a type folder the name must have that type's shape.
    for path in ("PERPS/xyz_NVDA", "HIP3/BTC", "HIP3/HYPE_USDC", "SPOT/BTC", "SPOT/_107", "HIP4/BTC",
                 "HIP4/HIP4_3600_Fed-Oct-no-change", "HIP4/Fed_id", "HIP4/_id12", "HIP4/a_b_id12"):
        assert names.parse_orderlog_name(f"20261117/{path}_HLC_20261117.bin.lz4") is None, path
    # Any other folder: the name alone, so the earlier layout reads as before.
    assert names.parse_orderlog_name("20261117/hip4/HIP4_1473_x_HLC_20261117.bin.lz4").coin == "HIP4_1473_x"
    assert names.parse_orderlog_name("20261117/_107_HLC_20261117.bin.lz4").coin == "@107"
    assert names.orderlog_coin(title, "HIP4") == ("HIP4:11492", "outcome")
    assert names.orderlog_coin("HYPE_USDC") == ("HYPE/USDC", "spot")


def test_times_and_fixed_point():
    moment = datetime(2026, 11, 1, 12, 30, 15, 250_000, tzinfo=timezone.utc)
    ticks = aq.datetime_to_ticks(moment)
    assert aq.ticks_to_datetime(ticks) == moment
    assert aq.ticks_to_unix_ns(ticks) == 1_793_536_215 * 10**9 + 250_000_000
    assert aq.datetime_to_ticks(datetime(1970, 1, 1)) == aq.TICKS_AT_UNIX_EPOCH  # naive means UTC
    assert aq.unix_ns_to_datetime(1_792_713_600_000_000_000) == datetime(2026, 10, 23, tzinfo=timezone.utc)
    assert aq.to_float(12_345_000_000) == 123.45
