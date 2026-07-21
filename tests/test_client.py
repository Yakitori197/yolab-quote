"""Fallback chain, market gating, caching, and concurrent batches."""

import pytest

from yolab_quote import markets
from yolab_quote.cache import TTLCache
from yolab_quote.client import QuoteClient
from yolab_quote.exceptions import (
    AllProvidersFailedError,
    ProviderError,
    ProviderUnavailableError,
    SymbolNotFoundError,
)
from yolab_quote.models import ProviderHealth, Quote
from yolab_quote.providers.base import Provider


class FakeProvider(Provider):
    """A provider that answers from memory and records what it was asked."""

    def __init__(self, name, supported_markets, *, error=None, price=100.0, bars=None):
        self.name = name
        self.markets = tuple(supported_markets)
        self._error = error
        self._price = price
        self._bars = bars
        self.calls: list[str] = []
        self.closed = False

    def get_quote(self, symbol: str) -> Quote:
        self.calls.append(symbol)
        if self._error is not None:
            raise self._error
        return Quote.create(
            symbol=symbol, market=self.markets[0], source=self.name, price=self._price
        )

    def get_bars(self, symbol: str, days: int = 30):
        self.calls.append(f"bars:{symbol}")
        if self._error is not None:
            raise self._error
        return list(self._bars or [])

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, ok=True, status="ready", markets=self.markets)

    def close(self) -> None:
        self.closed = True


def client_with(*providers, priority=None, **kwargs):
    instances = {p.name: p for p in providers}
    order = priority or {markets.TW_STOCK: [p.name for p in providers],
                         markets.US_STOCK: [p.name for p in providers]}
    return QuoteClient(providers=instances, priority=order, **kwargs)


class TestFallback:
    def test_first_success_short_circuits(self):
        first = FakeProvider("first", [markets.TW_STOCK])
        second = FakeProvider("second", [markets.TW_STOCK])
        quote = client_with(first, second, ttl=0).get_quote("2330")
        assert quote.source == "first"
        assert second.calls == []

    def test_falls_through_on_provider_error(self):
        broken = FakeProvider("broken", [markets.TW_STOCK], error=ProviderError("boom"))
        good = FakeProvider("good", [markets.TW_STOCK])
        quote = client_with(broken, good, ttl=0).get_quote("2330")
        assert quote.source == "good"
        assert broken.calls == ["2330"]

    def test_falls_through_on_symbol_not_found(self):
        """Coverage differs per provider, so a miss is worth retrying elsewhere."""
        missing = FakeProvider("missing", [markets.TW_STOCK], error=SymbolNotFoundError("nope"))
        good = FakeProvider("good", [markets.TW_STOCK])
        assert client_with(missing, good, ttl=0).get_quote("2330").source == "good"

    def test_falls_through_on_unexpected_exception(self):
        """An unforeseen error must not sink the whole chain."""
        rogue = FakeProvider("rogue", [markets.TW_STOCK], error=RuntimeError("unexpected"))
        good = FakeProvider("good", [markets.TW_STOCK])
        assert client_with(rogue, good, ttl=0).get_quote("2330").source == "good"

    def test_all_failed_reports_every_reason(self):
        a = FakeProvider("a", [markets.TW_STOCK], error=ProviderError("network down"))
        b = FakeProvider("b", [markets.TW_STOCK], error=ProviderUnavailableError("no api key"))
        with pytest.raises(AllProvidersFailedError) as excinfo:
            client_with(a, b, ttl=0).get_quote("2330")
        error = excinfo.value
        assert set(error.failures) == {"a", "b"}
        assert "network down" in error.failures["a"]
        assert "no api key" in error.failures["b"]
        assert error.symbol == "2330"
        assert error.market == markets.TW_STOCK

    def test_no_providers_configured_raises(self):
        client = QuoteClient(providers={}, priority={markets.TW_STOCK: []}, ttl=0)
        with pytest.raises(AllProvidersFailedError):
            client.get_quote("2330")


class TestMarketGating:
    def test_provider_that_does_not_serve_the_market_is_skipped(self):
        """Regression: the original never checked its own supported-markets
        declaration, so a misconfigured priority list could return a crypto
        quote in answer to a stock query."""
        crypto_only = FakeProvider("crypto", [markets.CRYPTO_SPOT])
        stocks = FakeProvider("stocks", [markets.TW_STOCK])
        client = client_with(crypto_only, stocks, priority={markets.TW_STOCK: ["crypto", "stocks"]}, ttl=0)

        quote = client.get_quote("2330")

        assert quote.source == "stocks"
        assert crypto_only.calls == []

    def test_gating_reason_is_reported(self):
        crypto_only = FakeProvider("crypto", [markets.CRYPTO_SPOT])
        client = client_with(crypto_only, priority={markets.TW_STOCK: ["crypto"]}, ttl=0)
        with pytest.raises(AllProvidersFailedError) as excinfo:
            client.get_quote("2330")
        assert "does not serve tw_stock" in excinfo.value.failures["crypto"]


class TestMarketRouting:
    def test_market_inferred_from_symbol(self):
        tw = FakeProvider("tw", [markets.TW_STOCK])
        us = FakeProvider("us", [markets.US_STOCK])
        client = client_with(tw, us, priority={markets.TW_STOCK: ["tw", "us"],
                                               markets.US_STOCK: ["tw", "us"]}, ttl=0)
        assert client.get_quote("2330").source == "tw"
        assert client.get_quote("NVDA").source == "us"

    def test_explicit_market_overrides_inference(self):
        crypto = FakeProvider("crypto", [markets.CRYPTO_SPOT])
        client = client_with(crypto, priority={markets.CRYPTO_SPOT: ["crypto"]}, ttl=0)
        assert client.get_quote("BTCUSDT", markets.CRYPTO_SPOT).source == "crypto"


class TestCaching:
    def test_second_call_is_served_from_cache(self):
        provider = FakeProvider("p", [markets.TW_STOCK])
        client = client_with(provider, ttl=60)
        client.get_quote("2330")
        client.get_quote("2330")
        assert provider.calls == ["2330"]

    def test_use_cache_false_always_refetches(self):
        provider = FakeProvider("p", [markets.TW_STOCK])
        client = client_with(provider, ttl=60)
        client.get_quote("2330")
        client.get_quote("2330", use_cache=False)
        assert len(provider.calls) == 2

    def test_ttl_zero_disables_caching(self):
        provider = FakeProvider("p", [markets.TW_STOCK])
        client = client_with(provider, ttl=0)
        client.get_quote("2330")
        client.get_quote("2330")
        assert len(provider.calls) == 2

    def test_injected_cache_is_used(self):
        provider = FakeProvider("p", [markets.TW_STOCK])
        cache = TTLCache(ttl=60)
        client = client_with(provider, cache=cache)
        client.get_quote("2330")
        assert cache.get(markets.TW_STOCK, "2330") is not None


class TestBatch:
    def test_returns_all_successes_keyed_by_input(self):
        provider = FakeProvider("p", [markets.TW_STOCK, markets.US_STOCK])
        client = client_with(provider, ttl=0)
        result = client.get_quotes(["2330", "0050", "NVDA"])
        assert set(result) == {"2330", "0050", "NVDA"}

    def test_preserves_input_order(self):
        provider = FakeProvider("p", [markets.TW_STOCK, markets.US_STOCK])
        client = client_with(provider, ttl=0)
        result = client.get_quotes(["2330", "NVDA", "0050"])
        assert list(result) == ["2330", "NVDA", "0050"]

    def test_deduplicates(self):
        provider = FakeProvider("p", [markets.TW_STOCK])
        client = client_with(provider, ttl=0)
        result = client.get_quotes(["2330", "2330", "  2330  "])
        assert list(result) == ["2330"]
        assert len(provider.calls) == 1

    def test_failures_are_omitted_not_raised(self):
        """Callers diff the returned keys against their input to find failures."""
        broken = FakeProvider("broken", [markets.TW_STOCK], error=ProviderError("down"))
        client = client_with(broken, ttl=0)
        assert client.get_quotes(["2330", "0050"]) == {}

    def test_empty_input(self):
        provider = FakeProvider("p", [markets.TW_STOCK])
        assert client_with(provider, ttl=0).get_quotes([]) == {}

    def test_many_symbols_all_resolve(self):
        """The concurrency exists because serial fetches blew the LINE
        bot's reply-token deadline at ~33 symbols."""
        provider = FakeProvider("p", [markets.TW_STOCK])
        client = client_with(provider, ttl=0, max_workers=8)
        symbols = [f"{2000 + i}" for i in range(33)]
        result = client.get_quotes(symbols)
        assert len(result) == 33
        assert list(result) == symbols


class TestBars:
    def test_returns_bars(self):
        from yolab_quote.models import Bar

        bars = [Bar(date="2026-01-02", open=1.0, high=2.0, low=0.5, close=1.5)]
        provider = FakeProvider("p", [markets.TW_STOCK], bars=bars)
        assert client_with(provider, ttl=0).get_bars("2330", 5) == bars

    def test_falls_through_to_a_provider_that_has_history(self):
        no_history = FakeProvider("nohist", [markets.TW_STOCK],
                                  error=ProviderUnavailableError("no bars here"))
        from yolab_quote.models import Bar

        bars = [Bar(date="2026-01-02", open=1.0, high=2.0, low=0.5, close=1.5)]
        good = FakeProvider("good", [markets.TW_STOCK], bars=bars)
        assert client_with(no_history, good, ttl=0).get_bars("2330") == bars


class TestHealthAndLifecycle:
    def test_health_includes_injected_providers(self):
        provider = FakeProvider("p", [markets.TW_STOCK])
        report = client_with(provider, ttl=0).health()
        assert "yfinance" in report  # from the registry
        assert all(isinstance(v, ProviderHealth) for v in report.values())

    def test_close_closes_providers(self):
        provider = FakeProvider("p", [markets.TW_STOCK])
        client = client_with(provider, ttl=0)
        client.close()
        assert provider.closed is True

    def test_context_manager_closes(self):
        provider = FakeProvider("p", [markets.TW_STOCK])
        with client_with(provider, ttl=0):
            pass
        assert provider.closed is True


class TestEnvironmentOverride:
    def test_env_sets_priority(self, monkeypatch):
        monkeypatch.setenv("YOLAB_QUOTE_TW_STOCK_PROVIDERS", "second,first")
        first = FakeProvider("first", [markets.TW_STOCK])
        second = FakeProvider("second", [markets.TW_STOCK])
        client = QuoteClient(providers={"first": first, "second": second}, ttl=0)
        assert client.get_quote("2330").source == "second"

    def test_explicit_priority_beats_env(self, monkeypatch):
        monkeypatch.setenv("YOLAB_QUOTE_TW_STOCK_PROVIDERS", "second")
        first = FakeProvider("first", [markets.TW_STOCK])
        second = FakeProvider("second", [markets.TW_STOCK])
        client = QuoteClient(
            providers={"first": first, "second": second},
            priority={markets.TW_STOCK: ["first"]},
            ttl=0,
        )
        assert client.get_quote("2330").source == "first"
