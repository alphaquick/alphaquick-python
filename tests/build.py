"""Write small synthetic captures for the tests, in the real container format."""

from __future__ import annotations

import struct

import lz4.block

P = 100_000_000  # 1.0 in the format's fixed point
T0 = 639_283_104_000_000_000  # 2026-10-23 00:00:00 UTC in ticks


def frame(block: bytes, compress: bool = True) -> bytes:
    """One K4os pickler frame holding ``block`` (compressed when that saves bytes)."""
    if compress:
        packed = lz4.block.compress(block, store_size=False)
        diff = len(block) - len(packed)
        if diff > 0:
            if diff < 0x100:
                width_code, width = 1, 1
            elif diff < 0x10000:
                width_code, width = 2, 2
            else:
                width_code, width = 3, 4
            return bytes([width_code << 6]) + diff.to_bytes(width, "little") + packed
    return b"\x00" + block


def container(*blocks: bytes, compress: bool = True) -> bytes:
    out = bytearray(struct.pack("<i", 65536))
    for block in blocks:
        f = frame(block, compress)
        out += struct.pack("<i", len(f)) + f
    return bytes(out)


def header(kind: int, size: int, time: int) -> bytes:
    return struct.pack("<hHq", kind, size, time)


# -- L2 ------------------------------------------------------------------

def depth(time: int, price: float, volume: float, flags: int) -> bytes:
    return header(0, 29, time) + struct.pack("<qqB", round(price * P), round(volume * P), flags)


def l2_trade(time: int, price: float, volume: float, side: int, trade_id: int = 0) -> bytes:
    return header(1, 37, time) + struct.pack("<qqqB", trade_id, round(price * P), round(volume * P), side)


def unknown(kind: int, time: int, payload: bytes = b"\x01\x02\x03") -> bytes:
    return header(kind, 12 + len(payload), time) + payload


# -- order log -----------------------------------------------------------

def meta(coin: str, day: int = 20261023, flags: int = 0, time: int = T0) -> bytes:
    name = coin.encode()
    body = struct.pack("<IHBBHHIQQB", int.from_bytes(b"HLC1", "little"), 1, 2, 0, 7, flags, day, 1000, 1_792_713_600_000_000_000, len(name)) + name
    return header(16, 12 + len(body), time) + body


def block(number: int, time: int) -> bytes:
    ns = (time - 621_355_968_000_000_000) * 100
    return header(17, 36, time) + struct.pack("<QQQ", number, ns, ns + 300_000_000)


def user(user_id: int, address_byte: int, time: int = T0) -> bytes:
    return header(21, 36, time) + struct.pack("<I", user_id) + bytes([address_byte]) * 20


def snap(oid: int, side: int, price: float, size: float, user_id: int, flags: int = 0, tif: int = 1, time: int = T0) -> bytes:
    return header(20, 44, time) + struct.pack("<BBBxQqqI", side, tif, flags, oid, round(price * P), round(size * P), user_id)


def order(status: int, oid: int, side: int, price: float, size: float, user_id: int, tif: int = 1,
          order_type: int = 0, flags: int = 0, time: int = T0) -> bytes:
    return header(18, 45, time) + struct.pack("<BBBBBQqqI", status, side, tif, order_type, flags, oid,
                                              round(price * P), round(size * P), user_id)


def trade(taker_side: int, price: float, size: float, maker_oid: int, taker_oid: int, maker: int, taker: int,
          flags: int = 3, maker_pos: float = 0, taker_pos: float = 0, time: int = T0) -> bytes:
    return header(19, 70, time) + struct.pack("<BBqqQQIIqq", taker_side, flags, round(price * P), round(size * P),
                                              maker_oid, taker_oid, maker, taker, round(maker_pos * P), round(taker_pos * P))


def outcome(text: str, time: int = T0) -> bytes:
    body = struct.pack("<H", 1) + text.encode()
    return header(22, 12 + len(body), time) + body
