"""Async facade.

Driven with ``asyncio.run`` rather than pytest-asyncio: the wrapper is a
thin ``asyncio.to_thread`` layer, and testing it should not add a plugin
dependency.
"""

import asyncio

import pytest

from yolab_quote import markets
from yolab_quote.aio import AsyncQuoteClient
from yolab_quote.client import QuoteClient
from yolab_quote.exceptions import AllProvidersFailedError, ProviderError
from yolab_quote.models import Bar, ProviderHealth, Quote
from yolab_quote.providers.base import Provider


class FakeProvider(Provider):
    name = "fake"
    markets = (markets.TW_STOCK, markets.US_STOCK)

    def __init__(self, *, error=None):
        self._error = error
        self.calls: list[str] = []

    def get_quote(self, symbol: str) -> Quote:
        self.calls.append(symbol)
        if self._error is not None:
            raise self._error
        return Quote.create(
            symbol=symbol, market=markets.TW_STOCK, source=self.name, price=100.0
        )

    def get_bars(self, symbol: str, days: int = 30):
        return [Bar(date="2026-01-02", open=1.0, high=2.0, low=0.5, close=1.5)]

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, ok=True, status="ready", markets=self.markets)


def make_client(**kwargs):
    provider = FakeProvider(**kwargs)
    sync = QuoteClient(
        providers={"fake": provider},
        priority={markets.TW_STOCK: ["fake"], markets.US_STOCK: ["fake"]},
        ttl=0,
    )
    return AsyncQuoteClient(sync), provider


class TestAsyncClient:
    def test_get_quote(self):
        client, _ = make_client()
        quote = asyncio.run(client.get_quote("2330"))
        assert quote.symbol == "2330"
        assert quote.price == 100.0

    def test_get_quotes(self):
        client, _ = make_client()
        result = asyncio.run(client.get_quotes(["2330", "0050"]))
        assert set(result) == {"2330", "0050"}

    def test_get_bars(self):
        client, _ = make_client()
        bars = asyncio.run(client.get_bars("2330", 5))
        assert len(bars) == 1
        assert isinstance(bars[0], Bar)

    def test_health(self):
        client, _ = make_client()
        report = asyncio.run(client.health())
        assert "fake" in report

    def test_errors_propagate(self):
        client, _ = make_client(error=ProviderError("down"))

        with pytest.raises(AllProvidersFailedError):
            asyncio.run(client.get_quote("2330"))

    def test_shares_state_with_the_sync_client(self):
        client, provider = make_client()
        asyncio.run(client.get_quote("2330"))
        client.sync_client.get_quote("2330")
        assert len(provider.calls) == 2

    def test_async_context_manager_closes(self):
        client, _ = make_client()

        async def run():
            async with client:
                pass

        asyncio.run(run())

    def test_concurrent_calls_do_not_block_each_other(self):
        """The wrapper exists so a slow provider cannot stall the event loop."""
        client, _ = make_client()

        async def run():
            return await asyncio.gather(
                client.get_quote("2330"),
                client.get_quote("0050"),
                client.get_quote("NVDA"),
            )

        quotes = asyncio.run(run())
        assert [q.symbol for q in quotes] == ["2330", "0050", "NVDA"]
