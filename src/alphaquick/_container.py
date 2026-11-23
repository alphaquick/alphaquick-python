"""The container both file formats share: LZ4 blocks in K4os pickler frames.

A ``.bin.lz4`` file is::

    int32            size hint of the writer's buffer (not needed to read)
    repeat until the end of the file:
        int32        N, the length of the frame that follows
        N bytes      one frame, holding one block of records

A frame starts with one header byte: bits 0-2 are the frame version (always
0) and bits 6-7 the width of the ``diff`` field that follows (0, 1, 2 or 4
bytes). ``diff`` is how many bytes compression saved; 0 means the rest of the
frame is the block as is, otherwise it is one raw LZ4 block that decompresses
to ``len(payload) + diff`` bytes. This is the format of the
``K4os.Compression.LZ4`` pickler. It is not the LZ4 *frame* format, so the
``lz4`` command line tool cannot read these files.

See https://docs.alphaquick.io/data/l2-depth-trades#container
"""

from __future__ import annotations

import os
import struct
from typing import BinaryIO, Iterator, Union

import lz4.block

Source = Union[str, "os.PathLike[str]", BinaryIO]

_INT32 = struct.Struct("<i")
_DIFF_WIDTHS = (0, 1, 2, 4)
# A frame larger than this is not something the writers produce: the file is
# not a capture, or is damaged.
_MAX_FRAME = 1 << 30


class FormatError(ValueError):
    """The bytes are not a valid AlphaQuick capture."""


class TruncatedFileError(FormatError):
    """The file ends in the middle of a frame, for example an interrupted download."""


def decode_frame(frame: bytes) -> bytes:
    """Decode one frame into the block of records it holds."""
    if not frame:
        raise FormatError("empty frame")
    header = frame[0]
    if header & 0x07:
        raise FormatError(f"unsupported frame version {header & 0x07}")
    width = _DIFF_WIDTHS[(header >> 6) & 0x03]
    if len(frame) < 1 + width:
        raise FormatError("frame shorter than its header")
    diff = int.from_bytes(frame[1:1 + width], "little")
    payload = frame[1 + width:]
    if diff == 0:
        return bytes(payload)
    # LZ4 expands at most about 255 times: refuse a frame that claims more
    # before allocating for it (a few damaged bytes must not ask for a gigabyte).
    if diff > len(payload) * 255 + 16:
        raise FormatError(f"frame claims {len(payload) + diff} bytes from {len(payload)}: not an LZ4 block")
    try:
        return lz4.block.decompress(payload, uncompressed_size=len(payload) + diff)
    except lz4.block.LZ4BlockError as error:
        raise FormatError(f"corrupt LZ4 block: {error}") from None


def _blocks_of(stream: BinaryIO) -> Iterator[bytes]:
    hint = stream.read(4)
    if not hint:
        return  # an empty file holds no records
    if len(hint) < 4:
        raise TruncatedFileError("the file is shorter than its 4-byte header")
    while True:
        head = stream.read(4)
        if not head:
            return
        if len(head) < 4:
            raise TruncatedFileError("the file ends inside a frame length")
        (length,) = _INT32.unpack(head)
        if length <= 0 or length > _MAX_FRAME:
            raise FormatError(f"invalid frame length {length}")
        frame = stream.read(length)
        if len(frame) < length:
            raise TruncatedFileError(f"the file ends inside a frame ({len(frame)} of {length} bytes)")
        yield decode_frame(frame)


def iter_blocks(source: Source) -> Iterator[bytes]:
    """Yield each decompressed block of a ``.bin.lz4`` file, in order.

    ``source`` is a path or a binary file object. The file is read one frame
    at a time, so memory stays small whatever its size.
    """
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as stream:
            yield from _blocks_of(stream)
    else:
        yield from _blocks_of(source)


_HEAD = struct.Struct("<hH")  # kind, size


def iter_raw_records(source: Source) -> Iterator[tuple[int, int, bytes, int]]:
    """Yield ``(kind, size, block, offset)`` for every record of a file.

    Every record starts with a 12-byte header: ``int16 kind``, ``uint16 size``
    (of the whole record, header included) and ``int64 time``, all
    little-endian. The record is ``block[offset:offset + size]``; unpack it with
    :mod:`struct` straight from ``block`` at ``offset``. This is the lowest level
    the package offers: :func:`alphaquick.l2.read_l2` and
    :func:`alphaquick.orderlog.read_orderlog` turn records into named tuples.
    """
    for block in iter_blocks(source):
        yield from records_of_block(block)


def records_of_block(block: bytes) -> Iterator[tuple[int, int, bytes, int]]:
    """Yield ``(kind, size, block, offset)`` for every record of one block."""
    pos, end = 0, len(block)
    unpack = _HEAD.unpack_from
    while pos < end:
        if pos + 12 > end:
            raise FormatError(f"a record header is cut off at the end of a block ({end - pos} bytes left)")
        kind, size = unpack(block, pos)
        if size < 12 or pos + size > end:
            raise FormatError(f"record of kind {kind} at offset {pos} has an invalid size {size}")
        yield kind, size, block, pos
        pos += size
