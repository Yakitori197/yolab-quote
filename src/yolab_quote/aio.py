"""Async wrapper around the synchronous client.

Wrapping sync in async is the cheap direction -- it needs only a worker
thread. The reverse forces every caller to own an event loop, which is why
the async-only abstraction this package replaces was unusable from the
synchronous Flask apps that needed it most.

The Discord bot this serves already hand-rolled exactly this
(``await asyncio.to_thread(get_stock_info, symbol)``); now it is part of the
package and shares the manager's cache and fallback chain.

    import yolab_quote.aio as aq
    quote = await aq.get_quote("2330")
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from .client import QuoteClient, get_default_client
from .models import Bar, ProviderHealth, Quote, SearchResult


class AsyncQuoteClient:
    """Async facade over a :class:`~yolab_quote.client.QuoteClient`.

    Every call runs in a worker thread, so a slow provider never blocks the
    event loop. State (cache, provider instances) is shared with the wrapped
    synchronous client.
    """

    def __init__(self, client: QuoteClient | None = None) -> None:
        self._client = client or get_default_client()

    @property
    def sync_client(self) -> QuoteClient:
        """The underlying synchronous client."""
        return self._client

    async def get_quote(
        self, symbol: str, market: str | None = None, *, use_cache: bool = True
    ) -> Quote:
        return await asyncio.to_thread(
            self._client.get_quote, symbol, market, use_cache=use_cache
        )

    async def get_quotes(
        self,
        symbols: Iterable[str],
        market: str | None = None,
        *,
        max_workers: int | None = None,
        use_cache: bool = True,
    ) -> dict[str, Quote]:
        return await asyncio.to_thread(
            self._client.get_quotes,
            symbols,
            market,
            max_workers=max_workers,
            use_cache=use_cache,
        )

    async def get_bars(self, symbol: str, days: int = 30, market: str | None = None) -> list[Bar]:
        return await asyncio.to_thread(self._client.get_bars, symbol, days, market)

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        return await asyncio.to_thread(self._client.search, query, limit)

    async def health(self) -> dict[str, ProviderHealth]:
        return await asyncio.to_thread(self._client.health)

    async def close(self) -> None:
        await asyncio.to_thread(self._client.close)

    async def __aenter__(self) -> AsyncQuoteClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()


# --------------------------------------------------------------------------- #
# Module-level convenience API, mirroring the sync one
# --------------------------------------------------------------------------- #
async def get_quote(symbol: str, market: str | None = None) -> Quote:
    """Fetch one quote off the event loop, using the default client."""
    return await asyncio.to_thread(get_default_client().get_quote, symbol, market)


async def get_quotes(symbols: Iterable[str], market: str | None = None) -> dict[str, Quote]:
    """Fetch many quotes off the event loop, using the default client."""
    return await asyncio.to_thread(get_default_client().get_quotes, symbols, market)


async def get_bars(symbol: str, days: int = 30, market: str | None = None) -> list[Bar]:
    """Fetch daily candles off the event loop, using the default client."""
    return await asyncio.to_thread(get_default_client().get_bars, symbol, days, market)


async def search_symbols(query: str, limit: int = 5) -> list[SearchResult]:
    """Look up symbols off the event loop, using the default client."""
    return await asyncio.to_thread(get_default_client().search, query, limit)


async def health() -> dict[str, ProviderHealth]:
    """Provider health off the event loop, using the default client."""
    return await asyncio.to_thread(get_default_client().health)


__all__ = [
    "AsyncQuoteClient",
    "get_quote",
    "get_quotes",
    "get_bars",
    "search_symbols",
    "health",
]
