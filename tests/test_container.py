import io
import struct

import pytest

import alphaquick as aq
from alphaquick._container import decode_frame

from build import container, depth, frame, header, unknown


def test_stored_and_compressed_frames_decode_to_the_same_block():
    # Repetitive data compresses well enough to need each width of the diff field.
    for size in (300, 70_000, 400_000):
        block = (b"abcdefgh" * (size // 8 + 1))[:size]
        assert decode_frame(frame(block, compress=False)) == block
        packed = frame(block, compress=True)
        assert packed[0] >> 6 in (1, 2, 3)
        assert decode_frame(packed) == block
    assert {frame(b"x" * n)[0] >> 6 for n in (100, 30_000, 400_000)} == {1, 2, 3}


def test_iter_blocks_reads_paths_and_file_objects(tmp_path):
    blocks = [depth(1, 100.0, 1.0, 1) * 50, depth(2, 101.0, 2.0, 2)]
    data = container(*blocks)
    path = tmp_path / "x.bin.lz4"
    path.write_bytes(data)
    assert list(aq.iter_blocks(path)) == blocks
    assert list(aq.iter_blocks(str(path))) == blocks
    assert list(aq.iter_blocks(io.BytesIO(data))) == blocks


def test_empty_and_truncated_files():
    assert list(aq.iter_blocks(io.BytesIO(b""))) == []
    assert list(aq.iter_blocks(io.BytesIO(struct.pack("<i", 1)))) == []  # header only: no records
    with pytest.raises(aq.TruncatedFileError):
        list(aq.iter_blocks(io.BytesIO(b"\x00\x00")))
    data = container(depth(1, 1.0, 1.0, 1) * 100)
    for cut in (6, len(data) - 1):
        with pytest.raises(aq.TruncatedFileError):
            list(aq.iter_blocks(io.BytesIO(data[:cut])))


def test_malformed_frames_are_refused():
    with pytest.raises(aq.FormatError, match="version"):
        decode_frame(b"\x01abc")
    with pytest.raises(aq.FormatError, match="invalid frame length"):
        list(aq.iter_blocks(io.BytesIO(struct.pack("<ii", 1, 0))))
    with pytest.raises(aq.FormatError, match="corrupt LZ4"):
        decode_frame(bytes([1 << 6, 200]) + b"\xff\xff\xff\xff")
    with pytest.raises(aq.FormatError, match="not an LZ4 block"):
        decode_frame(bytes([3 << 6]) + (0x3FFFFFF0).to_bytes(4, "little") + b"\x00\x00")  # claims a gigabyte
    with pytest.raises(aq.FormatError, match="shorter than its header"):
        decode_frame(bytes([3 << 6, 1]))


def test_raw_records_and_their_framing():
    block = depth(5, 1.0, 2.0, 1) + unknown(99, 6) + depth(7, 1.5, 1.0, 2)
    records = list(aq.iter_raw_records(io.BytesIO(container(block))))
    assert [(kind, size) for kind, size, _, _ in records] == [(0, 29), (99, 15), (0, 29)]
    kind, size, buf, pos = records[2]
    assert struct.unpack_from("<q", buf, pos + 4)[0] == 7
    with pytest.raises(aq.FormatError, match="invalid size"):
        list(aq.iter_raw_records(io.BytesIO(container(header(0, 200, 1) + b"\x00" * 10))))
    with pytest.raises(aq.FormatError, match="cut off"):
        list(aq.iter_raw_records(io.BytesIO(container(depth(1, 1.0, 1.0, 1) + b"\x00\x00\x00"))))
