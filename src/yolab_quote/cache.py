"""Time-to-live quote cache.

Three fixes over the implementation this replaces:

*One key rule.* Reads and writes go through :func:`cache_key`. The original
wrote batch results under the provider's normalized symbol but read them
under the caller's raw symbol, so batch entries were never hit and every
lookup went back out to the network.

*Thread safe.* The manager fans batch requests out across a thread pool;
the original had no lock at all.

*Bounded.* Entries are capped and expired ones are evicted on write, so a
long-running bot that queries many symbols cannot grow the cache forever.
The original had no limit and no periodic sweep.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

from .models import Quote

DEFAULT_TTL_SECONDS = 10.0
DEFAULT_MAX_ENTRIES = 1024


def cache_key(market: str, symbol: str) -> str:
    """The single key rule shared by every read and write."""
    return f"{market}:{symbol.strip().upper()}"


class TTLCache:
    """A small, thread-safe, bounded cache of quotes."""

    def __init__(
        self,
        ttl: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        time_func: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl < 0:
            raise ValueError("ttl must not be negative")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self.ttl = ttl
        self.max_entries = max_entries
        self._now = time_func
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[Quote, float]] = {}

    def get(self, market: str, symbol: str) -> Quote | None:
        """Return a live cached quote, or ``None`` if absent or expired."""
        key = cache_key(market, symbol)
        now = self._now()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            quote, expires_at = entry
            if now >= expires_at:
                del self._entries[key]
                return None
            return quote

    def set(self, market: str, symbol: str, quote: Quote, ttl: float | None = None) -> None:
        """Cache ``quote``. A ttl of 0 stores nothing."""
        effective_ttl = self.ttl if ttl is None else ttl
        if effective_ttl <= 0:
            return
        key = cache_key(market, symbol)
        now = self._now()
        with self._lock:
            if len(self._entries) >= self.max_entries and key not in self._entries:
                self._evict_locked(now)
            self._entries[key] = (quote, now + effective_ttl)

    def _evict_locked(self, now: float) -> None:
        """Drop expired entries; if none, drop the soonest to expire."""
        expired = [k for k, (_, exp) in self._entries.items() if now >= exp]
        for key in expired:
            del self._entries[key]
        if not expired and self._entries:
            oldest = min(self._entries.items(), key=lambda item: item[1][1])[0]
            del self._entries[oldest]

    def purge_expired(self) -> int:
        """Drop every expired entry, returning how many were removed."""
        now = self._now()
        with self._lock:
            expired = [k for k, (_, exp) in self._entries.items() if now >= exp]
            for key in expired:
                del self._entries[key]
            return len(expired)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __repr__(self) -> str:
        return f"<TTLCache entries={len(self)} ttl={self.ttl}s max={self.max_entries}>"
