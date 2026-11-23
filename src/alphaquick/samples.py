"""The free sample files: whole files anyone may download, with no account.

``fetch(name)`` downloads one into a cache folder (once) and returns its path::

    from alphaquick import samples
    path = samples.fetch("orderlog-hip4")

The cache is ``$ALPHAQUICK_CACHE`` if set, otherwise ``~/.cache/alphaquick``.
The full, current list of samples is on https://alphaquick.io/data and in the
site's Terminal (``data samples``). Downloads from ``/api/data/samples`` are
limited to 30 an hour per address (the replay tools' samples are plain
files); the cache means each file is fetched once.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import NamedTuple, Optional

from . import __version__

BASE_URL = "https://alphaquick.io"


class Sample(NamedTuple):
    name: str  #: the key to fetch it by
    path: str  #: its address on alphaquick.io
    filename: str
    size: int  #: bytes
    sha256: Optional[str]  #: pinned for the files the tests use; None to check the size only
    description: str

    @property
    def url(self) -> str:
        return BASE_URL + self.path


_S = "/api/data/samples"
SAMPLES: dict[str, Sample] = {s.name: s for s in (
    Sample("l2-btc-hyperliquid", f"{_S}/l2-depth-trades/BTC_HYPERLIQUID_20261101.bin.lz4", "BTC_HYPERLIQUID_20261101.bin.lz4",
           9_546_149, "e9c3d2ad81c078f3f8f003e84acd2f8aa8e5121670b7a131930af58ff094445f",
           "L2: Hyperliquid BTC perpetual, 1 November 2026, whole day"),
    Sample("l2-btc-hyperliquid-continuity", f"{_S}/l2-depth-trades/BTC_HYPERLIQUID_20261101.continuity.jsonl",
           "BTC_HYPERLIQUID_20261101.continuity.jsonl", 214,
           "01de752679a47ef287498c79d774f498118ff1891339b9d5c4d89a4d1b6ab6db",
           "L2: the continuity sidecar of l2-btc-hyperliquid"),
    Sample("l2-btc-okx", f"{_S}/l2-depth-trades/BTC-USDT-SWAP_OKX_20261101.bin.lz4", "BTC-USDT-SWAP_OKX_20261101.bin.lz4",
           29_732_488, None, "L2: OKX BTC-USDT-SWAP, 1 November 2026, whole day"),
    Sample("l2-btcusdt-binance", f"{_S}/l2-depth-trades/BTCUSDT_BINANCE_USDM_20261101.bin.lz4",
           "BTCUSDT_BINANCE_USDM_20261101.bin.lz4", 123_163_698, None,
           "L2: Binance USD-M BTCUSDT, 1 November 2026, whole day"),
    Sample("l2-ethusdt-binance", f"{_S}/l2-depth-trades/ETHUSDT_BINANCE_USDM_20261101.bin.lz4",
           "ETHUSDT_BINANCE_USDM_20261101.bin.lz4", 128_480_510, None,
           "L2: Binance USD-M ETHUSDT, 1 November 2026, whole day"),
    Sample("l2-btc-hyperliquid-15min", "/replay/sample/BTC_HYPERLIQUID_20260622_sample.bin.lz4",
           "BTC_HYPERLIQUID_20260622_sample.bin.lz4", 3_973_075,
           "01f4f24ba62e3b086af2637740d2c56a9b13af5c32a3a59cc75c6819699d71a9",
           "L2: Hyperliquid BTC, 15 minutes of 22 June 2026 (Depth Replay's built-in sample)"),
    Sample("orderlog-hip4", f"{_S}/hyperliquid-orderlog/HIP4_3600_Fed-Oct-no-change_HLC_20261023.bin.lz4",
           "HIP4_3600_Fed-Oct-no-change_HLC_20261023.bin.lz4", 870_679,
           "1a214b91636f0b3ba8de76fa5ac26ac32caeacb4f3641c1d6532542215c24968",
           "Order log: HIP-4 prediction market, Fed October 2026 rate decision - No change, both books, 23 October 2026"),
    Sample("orderlog-xyz-cl", f"{_S}/hyperliquid-orderlog/xyz_CL_HLC_20261101.bin.lz4", "xyz_CL_HLC_20261101.bin.lz4",
           92_563_554, "487e9f147e33655d508fa2ea4d2e8f39c89316bd830196b4fa3a54cb78acbd68",
           "Order log: xyz:CL (HIP-3 oil contract), 1 November 2026, whole day"),
    Sample("orderlog-xyz-xyz100", f"{_S}/hyperliquid-orderlog/xyz_XYZ100_HLC_20261101.bin.lz4",
           "xyz_XYZ100_HLC_20261101.bin.lz4", 103_612_438, None,
           "Order log: xyz:XYZ100 (HIP-3 index), 1 November 2026, whole day"),
    Sample("orderlog-btc", f"{_S}/hyperliquid-orderlog/BTC_HLC_20261101.bin.lz4", "BTC_HLC_20261101.bin.lz4",
           2_289_643_256, None, "Order log: BTC perpetual, 1 November 2026, whole day (2.1 GB)"),
    Sample("orderlog-zec-30min", "/orderlog/sample/ZEC_HLC_20261014_sample.bin.lz4", "ZEC_HLC_20261014_sample.bin.lz4",
           5_065_015, "03402adfdbb84fa96d0b59eb8f5f679236b4ff62d03939869196b5737914b731",
           "Order log: ZEC, the first 30 minutes of 14 October 2026 (Order-Log Replay's built-in sample)"),
)}


class SampleError(RuntimeError):
    """A sample could not be downloaded, or what arrived is not the file expected."""


def cache_dir() -> Path:
    """Where samples are kept: ``$ALPHAQUICK_CACHE`` or ``~/.cache/alphaquick``."""
    return Path(os.environ.get("ALPHAQUICK_CACHE") or Path.home() / ".cache" / "alphaquick")


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _valid(path: Path, sample: Sample) -> bool:
    if not path.is_file() or path.stat().st_size != sample.size:
        return False
    return sample.sha256 is None or _digest(path) == sample.sha256


def fetch(name: str, directory: Optional[os.PathLike] = None, *, quiet: bool = True) -> Path:
    """Download a sample once and return its path. ``name`` is a key of :data:`SAMPLES`."""
    try:
        sample = SAMPLES[name]
    except KeyError:
        raise SampleError(f"no sample named {name!r}; one of: {', '.join(SAMPLES)}") from None
    folder = Path(directory) if directory is not None else cache_dir() / "samples"
    target = folder / sample.filename
    if _valid(target, sample):
        return target
    folder.mkdir(parents=True, exist_ok=True)
    if not quiet:
        print(f"downloading {sample.url} ({sample.size / 1024 ** 2:.1f} MB)")
    request = urllib.request.Request(sample.url, headers={"User-Agent": f"alphaquick-python/{__version__}"})
    fd, tmp_name = tempfile.mkstemp(prefix=sample.filename + ".", suffix=".part", dir=folder)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(request, timeout=60) as response:
            shutil.copyfileobj(response, out, 1 << 20)
        if not _valid(tmp, sample):
            raise SampleError(
                f"{sample.filename} did not arrive as expected (size or SHA-256 differ). The sample may have "
                "been replaced on alphaquick.io: update alphaquick, or download it by hand from " + sample.url)
        os.replace(tmp, target)
    except OSError as error:
        raise SampleError(f"could not download {sample.url}: {error}") from error
    finally:
        tmp.unlink(missing_ok=True)
    return target
