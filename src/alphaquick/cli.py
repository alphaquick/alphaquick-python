"""The ``alphaquick`` command: look into a file without writing code.

    alphaquick info FILE                 what the file holds
    alphaquick book FILE [--at 12:00]    the book at a moment (UTC), or at the end
    alphaquick dump FILE [--limit N]     records as JSON lines, for any other tool
    alphaquick to-parquet FILE [-o DIR]  trades (and quotes or order events) as Parquet
    alphaquick samples [fetch NAME]      the free sample files
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from typing import Optional, Sequence

from . import __version__, detect, l2, names, orderlog, samples
from ._container import FormatError, TruncatedFileError
from ._time import SCALE, datetime_to_ticks, ticks_to_datetime


def _px(value: Optional[int]) -> str:
    return "-" if value is None else f"{value / SCALE:,.8f}".rstrip("0").rstrip(".")


def _sz(value: int) -> str:
    return f"{value / SCALE:,.8f}".rstrip("0").rstrip(".")


def _kind_of(path: str) -> str:
    kind = names.file_kind(path) or detect(path)
    if kind is None:
        raise FormatError("the file holds no records")
    return kind


def _file_day(path: str, kind: str) -> Optional[date]:
    """The UTC day of a file, from its name or (order log) its META record, or its first record."""
    parsed = names.parse_orderlog_name(path) if kind == "orderlog" else names.parse_l2_name(path)
    if parsed:
        return datetime.strptime(parsed.day, "%Y%m%d").date()
    if kind == "orderlog":
        for record in orderlog.read_orderlog(path):
            if isinstance(record, orderlog.Meta):
                return datetime.strptime(str(record.day), "%Y%m%d").date()
            break
        return None
    for event in l2.read_l2(path):
        return ticks_to_datetime(event.time).date()
    return None


def parse_moment(text: str, day: Optional[date]) -> datetime:
    """``HH:MM[:SS[.ffffff]]`` on the file's day, or a full ISO date and time; always UTC."""
    clock = re.fullmatch(r"(\d{1,2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?", text.strip())
    if clock:
        if day is None:
            raise ValueError("the file's day is not known: give a full date and time, e.g. 2026-11-01T12:00")
        hours, minutes, seconds, fraction = clock.groups()
        moment = dtime(int(hours), int(minutes), int(seconds or 0), int((fraction or "0").ljust(6, "0")))
        return datetime.combine(day, moment, timezone.utc)
    # Python 3.10's fromisoformat takes 0, 3 or 6 fraction digits: pad to 6.
    iso = re.sub(r"\.(\d{1,6})", lambda m: "." + m.group(1).ljust(6, "0"), text.strip().replace("Z", "+00:00"))
    moment = datetime.fromisoformat(iso)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------


def cmd_info(args: argparse.Namespace) -> int:
    path = args.file
    kind = _kind_of(path)
    size = os.path.getsize(path)
    print(f"{os.path.basename(path)}  ({_mb(size)})")
    if kind == "l2":
        parsed = names.parse_l2_name(path)
        if parsed:
            print(f"L2 depth & trades: {parsed.symbol} on {parsed.venue}, {parsed.day}")
        book = l2.L2Book()
        depth = trades = clears = 0
        first = last = None
        truncated = None
        try:
            for event in l2.read_l2(path):
                first = event.time if first is None else first
                last = event.time
                if type(event) is l2.Trade:
                    trades += 1
                else:
                    depth += 1
                    clears += bool(event.flags & l2.CLEAR)
                    book.apply(event)
        except TruncatedFileError as error:
            truncated = error
        print(f"{depth:,} depth updates ({clears:,} snapshots start), {trades:,} trades")
        if first is not None:
            print(f"from {ticks_to_datetime(first):%Y-%m-%d %H:%M:%S.%f} to {ticks_to_datetime(last):%Y-%m-%d %H:%M:%S.%f} UTC")
        bid, ask = book.best_bid(), book.best_ask()
        print(f"book at the end: best bid {_px(bid and bid[0])}, best ask {_px(ask and ask[0])}, "
              f"{len(book.bids):,} bid / {len(book.asks):,} ask levels")
        return _truncated(truncated)

    replay = orderlog.Replay(path)
    counts: Counter = Counter()
    statuses: Counter = Counter()
    kinds: Counter = Counter()
    first_block = None
    truncated = None
    try:
        for record in replay:
            kind_name = type(record).__name__
            counts[kind_name] += 1
            if kind_name == "Order":
                statuses[record.status] += 1  # type: ignore[union-attr]
            elif kind_name == "Trade" and replay.meta is not None and replay.meta.is_outcome:
                kinds[replay.last_trade_kind.name.lower()] += 1
            elif kind_name == "Block" and first_block is None:
                first_block = record
    except TruncatedFileError as error:
        truncated = error
    meta = replay.meta
    if meta is not None:
        market = names.parse_orderlog_name(path)
        coin = replay.outcome.title if replay.outcome is not None else meta.coin  # META holds an outcome's title cut short
        print(f"Order log: {coin}{f' ({market.market})' if market else ''}, day {meta.day}, starts from block {meta.start_block:,}")
    if replay.outcome is not None:
        info = replay.outcome
        print(f"HIP-4 outcome market {info.outcome}" + (f", question: {info.question}" if info.question else ""))
        print(f"  sides: {info.sides[0]} / {info.sides[1]}; prices are {info.sides[0]}'s, as shares of 1")
    print(f"{counts['SnapOrder']:,} orders resting at 00:00 UTC, {counts['Block']:,} blocks, "
          f"{counts['Order']:,} order events, {counts['Trade']:,} trades, {len(replay.accounts):,} accounts")
    if first_block is not None and replay.block is not None:
        print(f"blocks from {first_block.datetime:%Y-%m-%d %H:%M:%S.%f} to {replay.block.datetime:%Y-%m-%d %H:%M:%S.%f} UTC")
    if statuses:
        common = ", ".join(f"{orderlog.status_name(code)} {n:,}" for code, n in statuses.most_common(6))
        print(f"order events: {common}")
    if kinds:
        print("trades: " + ", ".join(f"{name.replace('_', ' ')} {n:,}" for name, n in sorted(kinds.items())))
    book = replay.book
    print(f"book at the end: best bid {_px(book.best_bid())}, best ask {_px(book.best_ask())}, "
          f"{sum(1 for o in book.orders.values() if o.side == orderlog.BID):,} bid / "
          f"{sum(1 for o in book.orders.values() if o.side == orderlog.ASK):,} ask orders")
    return _truncated(truncated)


def _mb(size: int) -> str:
    """A size in MB of 1024 x 1024 bytes, the unit alphaquick.io uses."""
    return f"{size / 1024 ** 2:,.1f} MB"


def _truncated(error) -> int:
    if error is None:
        return 0
    print(f"warning: the file is cut short ({error}); the figures above cover what it holds, "
          "the book is as far as the file reaches. Download the file again for the whole day.")
    return 0


def cmd_book(args: argparse.Namespace) -> int:
    path = args.file
    kind = _kind_of(path)
    until = None
    if args.at:
        moment = parse_moment(args.at, _file_day(path, kind))
        until = datetime_to_ticks(moment)
        print(f"book at {moment:%Y-%m-%d %H:%M:%S.%f} UTC")
    else:
        print("book at the end of the file")
    if kind == "l2":
        book = l2.L2Book()
        truncated = None
        try:
            for event in l2.read_l2(path):
                if until is not None and event.time > until:
                    break
                if type(event) is l2.DepthUpdate:
                    book.apply(event)
        except TruncatedFileError as error:
            truncated = error
        if truncated is not None and book.time is not None:
            print(f"(the file ends at {ticks_to_datetime(book.time):%Y-%m-%d %H:%M:%S.%f} UTC: the book below is from there)")
        bids, asks = book.depth(args.levels)
        _print_ladder([(p, s, None) for p, s in bids], [(p, s, None) for p, s in asks])
        if not book.consistent:
            print("(the book is in the middle of a transaction at this moment)")
        return _truncated(truncated)
    replay = orderlog.Replay(path)
    truncated = None
    try:
        replay.run(until)
    except TruncatedFileError as error:
        truncated = error
        if replay.block is not None:
            print(f"(the file ends at block {replay.block.number:,}, {replay.block.datetime:%Y-%m-%d %H:%M:%S.%f} UTC: the book below is from there)")
    book = replay.book
    _print_ladder(book.depth(orderlog.BID, args.levels), book.depth(orderlog.ASK, args.levels))
    if args.queue:
        for side, best in ((orderlog.BID, book.best_bid()), (orderlog.ASK, book.best_ask())):
            if best is None:
                continue
            print(f"\nqueue at the best {'bid' if side == orderlog.BID else 'ask'} {_px(best)}, front first:")
            for place, order in enumerate(book.queue(side, best)[: args.queue], 1):
                print(f"  {place:>3}. {_sz(order.size):>14}  {replay.address(order.user_id)}  oid {order.oid}")
    return _truncated(truncated)



def _print_ladder(bids, asks) -> None:
    print(f"{'orders':>7} {'bid size':>16} {'bid':>14} | {'ask':<14} {'ask size':<16} {'orders':<7}".rstrip())
    for i in range(max(len(bids), len(asks))):
        left = right = ""
        if i < len(bids):
            price, size, count = bids[i]
            left = f"{'' if count is None else count:>7} {_sz(size):>16} {_px(price):>14}"
        else:
            left = " " * 39
        if i < len(asks):
            price, size, count = asks[i]
            right = f"{_px(price):<14} {_sz(size):<16} {'' if count is None else count:<7}"
        print(f"{left} | {right}".rstrip())


def _record_json(record) -> dict:
    out = {"type": type(record).__name__.lower()}
    out.update(record._asdict())
    out["datetime"] = ticks_to_datetime(record.time).isoformat()
    return out


def cmd_dump(args: argparse.Namespace) -> int:
    path = args.file
    kind = _kind_of(path)
    reader = l2.read_l2(path) if kind == "l2" or args.l2 else orderlog.read_orderlog(path)
    wanted = {k.lower() for k in args.type} if args.type else None
    shown = 0
    out = sys.stdout
    try:
        for record in reader:
            if wanted and type(record).__name__.lower() not in wanted:
                continue
            out.write(json.dumps(_record_json(record)) + "\n")
            shown += 1
            if args.limit and shown >= args.limit:
                break
    except BrokenPipeError:  # e.g. piped into head
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
    return 0


def cmd_to_parquet(args: argparse.Namespace) -> int:
    from . import frames

    path = args.file
    kind = _kind_of(path)
    folder = Path(args.output or ".")
    folder.mkdir(parents=True, exist_ok=True)
    stem = os.path.basename(path).removesuffix(".bin.lz4")
    written = []
    if kind == "l2":
        written.append(_save(frames.l2_trades(path, exact=args.exact), folder / f"{stem}.trades.parquet"))
        written.append(_save(frames.l2_quotes(path, exact=args.exact), folder / f"{stem}.quotes.parquet"))
    else:
        written.append(_save(frames.orderlog_trades(path, exact=args.exact), folder / f"{stem}.trades.parquet"))
        if args.orders:
            written.append(_save(frames.orderlog_orders(path, exact=args.exact, include_rejects=not args.no_rejects),
                                 folder / f"{stem}.orders.parquet"))
    for target, rows in written:
        print(f"{target}  ({rows:,} rows)")
    return 0


def _save(frame, target: Path):
    frame.to_parquet(target, index=False)
    return target, len(frame)


def cmd_samples(args: argparse.Namespace) -> int:
    if args.action == "fetch":
        if not args.name:
            print("which sample? alphaquick samples fetch NAME", file=sys.stderr)
            return 2
        print(samples.fetch(args.name, args.dir, quiet=False))
        return 0
    width = max(len(name) for name in samples.SAMPLES)
    for sample in samples.SAMPLES.values():
        print(f"{sample.name:<{width}}  {sample.size / 1024 ** 2:>8.1f} MB  {sample.description}")
    print(f"\nalphaquick samples fetch NAME   downloads one into {samples.cache_dir() / 'samples'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="alphaquick", description="Look into AlphaQuick market data files (.bin.lz4).")
    parser.add_argument("--version", action="version", version=f"alphaquick {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("info", help="what a file holds")
    p.add_argument("file")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("book", help="the book at a moment, or at the end of the file")
    p.add_argument("file")
    p.add_argument("--at", help="a UTC time on the file's day (12:00, 12:00:30.5) or a full date and time")
    p.add_argument("--levels", type=int, default=10, help="price levels each side (default 10)")
    p.add_argument("--queue", type=int, default=0, metavar="N", help="order log: also list the first N orders at each best price")
    p.set_defaults(func=cmd_book)

    p = sub.add_parser("dump", help="records as JSON lines")
    p.add_argument("file")
    p.add_argument("--limit", type=int, default=0, help="stop after N records")
    p.add_argument("--type", action="append", help="only these record types (repeatable): order, trade, block, user, snaporder, meta, outcome, depthupdate")
    p.add_argument("--l2", action="store_true", help="order log: read its aggregated L2 view instead")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("to-parquet", help='trades (and quotes or order events) as Parquet; needs "alphaquick[pandas]"')
    p.add_argument("file")
    p.add_argument("-o", "--output", help="folder to write into (default: here)")
    p.add_argument("--orders", action="store_true", help="order log: also every order event")
    p.add_argument("--no-rejects", action="store_true", help="with --orders: leave rejected orders out")
    p.add_argument("--exact", action="store_true", help="keep prices and sizes as integers x10^8")
    p.set_defaults(func=cmd_to_parquet)

    p = sub.add_parser("samples", help="list the free sample files, or fetch one")
    p.add_argument("action", nargs="?", choices=["list", "fetch"], default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--dir", help="where to save (default: the cache)")
    p.set_defaults(func=cmd_samples)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (FormatError, ValueError, samples.SampleError, FileNotFoundError, ImportError) as error:
        print(f"alphaquick: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
