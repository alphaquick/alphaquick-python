"""Price levels of one side of a book, kept sorted."""

from __future__ import annotations

from bisect import bisect_left, insort
from typing import Iterator


class Levels:
    """``price -> size`` for one side of a book, with the prices kept sorted.

    ``descending`` is True for bids (best price is the highest) and False for
    asks (best price is the lowest).
    """

    __slots__ = ("size", "_prices", "_descending")

    def __init__(self, descending: bool) -> None:
        #: price -> size, both scaled integers.
        self.size: dict[int, int] = {}
        self._prices: list[int] = []  # ascending
        self._descending = descending

    def __len__(self) -> int:
        return len(self.size)

    def __contains__(self, price: int) -> bool:
        return price in self.size

    def get(self, price: int) -> int:
        return self.size.get(price, 0)

    def set(self, price: int, size: int) -> None:
        """Set the size at a price; a size of 0 or less removes the level."""
        if size <= 0:
            self.remove(price)
            return
        if price not in self.size:
            insort(self._prices, price)
        self.size[price] = size

    def add(self, price: int, delta: int) -> None:
        """Add ``delta`` (may be negative) to the size at a price; a total of 0 or less removes the level."""
        size = self.size
        current = size.get(price)
        if current is None:
            if delta > 0:
                insort(self._prices, price)
                size[price] = delta
            return
        total = current + delta
        if total > 0:
            size[price] = total
        else:
            del size[price]
            del self._prices[bisect_left(self._prices, price)]

    def remove(self, price: int) -> None:
        if self.size.pop(price, None) is not None:
            i = bisect_left(self._prices, price)
            del self._prices[i]

    def clear(self) -> None:
        self.size.clear()
        self._prices.clear()

    def best(self) -> int | None:
        """The best price, or None when the side is empty."""
        if not self._prices:
            return None
        return self._prices[-1] if self._descending else self._prices[0]

    def prices(self) -> Iterator[int]:
        """Prices from the best outwards."""
        return reversed(self._prices) if self._descending else iter(self._prices)

    def top(self, n: int | None = None) -> list[tuple[int, int]]:
        """``[(price, size), ...]`` from the best price outwards, at most ``n`` levels."""
        out = []
        for price in self.prices():
            if n is not None and len(out) >= n:
                break
            out.append((price, self.size[price]))
        return out

    def remove_through(self, price: int) -> None:
        """Remove every level at ``price`` or better (used to uncross a book)."""
        if self._descending:  # bids: remove prices >= price
            i = bisect_left(self._prices, price)
            for p in self._prices[i:]:
                del self.size[p]
            del self._prices[i:]
        else:  # asks: remove prices <= price
            i = bisect_left(self._prices, price)
            if i < len(self._prices) and self._prices[i] == price:
                i += 1
            for p in self._prices[:i]:
                del self.size[p]
            del self._prices[:i]

    def total(self) -> int:
        return sum(self.size.values())
