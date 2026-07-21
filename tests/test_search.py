"""Online symbol search.

Payloads mirror the real endpoint, captured from a live call to
/v1/finance/search?q=nvidia. The network sits behind the _fetch_search seam.
"""

import asyncio

import pytest

from yolab_quote import markets
from yolab_quote.aio import AsyncQuoteClient
from yolab_quote.client import QuoteClient
from yolab_quote.exceptions import AllProvidersFailedError, ProviderError
from yolab_quote.models import ProviderHealth, Quote, SearchResult
from yolab_quote.providers.base import Provider
from yolab_quote.providers.yahoo_provider import YahooProvider, parse_search_results

SEARCH_PAYLOAD = {
    "quotes": [
        {
            "exchange": "NMS", "shortname": "NVIDIA Corporation", "quoteType": "EQUITY",
            "symbol": "NVDA", "longname": "NVIDIA Corporation", "exchDisp": "NASDAQ",
            "sector": "Technology",
        },
        {
            "exchange": "TOR", "shortname": "NVIDIA CDR (CAD HEDGED)", "quoteType": "EQUITY",
            "symbol": "NVDA.TO", "longname": "NVIDIA Corporation", "exchDisp": "Toronto",
        },
        {
            "exchange": "BTS", "shortname": "T-Rex 2X Long NVIDIA Daily Target",
            "quoteType": "ETF", "symbol": "NVDX", "exchDisp": "BATS Trading",
        },
    ]
}


class TestParseSearchResults:
    def test_maps_fields(self):
        results = parse_search_results(SEARCH_PAYLOAD)
        assert results[0] == SearchResult(
            symbol="NVDA", name="NVIDIA Corporation",
            exchange="NASDAQ", quote_type="EQUITY",
        )

    def test_falls_back_to_shortname(self):
        """The ETF entry carries no longname."""
        etf = parse_search_results(SEARCH_PAYLOAD)[2]
        assert etf.symbol == "NVDX"
        assert etf.name.startswith("T-Rex")
        assert etf.quote_type == "ETF"

    def test_respects_limit(self):
        assert len(parse_search_results(SEARCH_PAYLOAD, limit=2)) == 2

    def test_entries_without_a_symbol_are_dropped(self):
        payload = {"quotes": [{"shortname": "no symbol here"}, {"symbol": "OK", "shortname": "Fine"}]}
        results = parse_search_results(payload)
        assert [r.symbol for r in results] == ["OK"]

    def test_missing_quotes_key(self):
        assert parse_search_results({}) == []

    def test_garbage_quotes_value(self):
        assert parse_search_results({"quotes": "nonsense"}) == []

    def test_non_dict_entries_are_skipped(self):
        assert parse_search_results({"quotes": ["junk", {"symbol": "OK"}]})[0].symbol == "OK"


class TestProviderSearch:
    def test_passes_query_and_limit_through(self):
        provider = YahooProvider()
        seen = {}

        def fake(query, limit):
            seen.update(query=query, limit=limit)
            return SEARCH_PAYLOAD

        provider._fetch_search = fake  # type: ignore[method-assign]
        results = provider.search("  nvidia  ", limit=3)

        assert seen == {"query": "nvidia", "limit": 3}
        assert len(results) == 3

    def test_empty_query_short_circuits(self):
        provider = YahooProvider()
        provider._fetch_search = lambda query, limit: pytest.fail("should not be called")  # type: ignore[method-assign]
        assert provider.search("") == []
        assert provider.search("   ") == []

    def test_rejects_non_positive_limit(self):
        with pytest.raises(ValueError):
            YahooProvider().search("nvidia", limit=0)

    def test_base_provider_reports_unsupported(self):
        """A provider that cannot search says so rather than returning junk."""

        class Minimal(Provider):
            name = "minimal"
            markets = (markets.TW_STOCK,)

            def get_quote(self, symbol: str) -> Quote:
                raise NotImplementedError

            def health(self) -> ProviderHealth:
                raise NotImplementedError

        from yolab_quote.exceptions import ProviderUnavailableError

        with pytest.raises(ProviderUnavailableError):
            Minimal().search("nvidia")


class SearchingProvider(Provider):
    name = "yahoo"  # matches DEFAULT_SEARCH_PROVIDERS
    markets = (markets.US_STOCK,)

    def __init__(self, *, error=None, results=None):
        self._error = error
        self._results = results if results is not None else [
            SearchResult(symbol="NVDA", name="NVIDIA Corporation")
        ]
        self.calls: list[tuple[str, int]] = []

    def get_quote(self, symbol: str) -> Quote:
        raise NotImplementedError

    def search(self, query: str, limit: int = 5):
        self.calls.append((query, limit))
        if self._error is not None:
            raise self._error
        return list(self._results)

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, ok=True, status="ready", markets=self.markets)


class TestClientSearch:
    def test_returns_results(self):
        client = QuoteClient(providers={"yahoo": SearchingProvider()}, ttl=0)
        assert client.search("nvidia")[0].symbol == "NVDA"

    def test_empty_query_returns_nothing_without_calling_a_provider(self):
        provider = SearchingProvider()
        client = QuoteClient(providers={"yahoo": provider}, ttl=0)
        assert client.search("") == []
        assert provider.calls == []

    def test_no_matches_is_not_an_error(self):
        """An empty result set means 'nothing found', not 'search broke'."""
        client = QuoteClient(providers={"yahoo": SearchingProvider(results=[])}, ttl=0)
        assert client.search("zzzzz") == []

    def test_provider_failure_raises_with_the_reason(self):
        client = QuoteClient(
            providers={"yahoo": SearchingProvider(error=ProviderError("upstream down"))}, ttl=0
        )
        with pytest.raises(AllProvidersFailedError) as excinfo:
            client.search("nvidia")
        assert "upstream down" in excinfo.value.failures["yahoo"]

    def test_unexpected_error_also_surfaces(self):
        client = QuoteClient(
            providers={"yahoo": SearchingProvider(error=RuntimeError("boom"))}, ttl=0
        )
        with pytest.raises(AllProvidersFailedError):
            client.search("nvidia")

    def test_limit_is_forwarded(self):
        provider = SearchingProvider()
        QuoteClient(providers={"yahoo": provider}, ttl=0).search("nvidia", limit=2)
        assert provider.calls == [("nvidia", 2)]


class TestAsyncSearch:
    def test_async_search(self):
        sync = QuoteClient(providers={"yahoo": SearchingProvider()}, ttl=0)
        results = asyncio.run(AsyncQuoteClient(sync).search("nvidia"))
        assert results[0].symbol == "NVDA"


class TestSearchResultModel:
    def test_to_dict(self):
        result = SearchResult(symbol="NVDA", name="NVIDIA", exchange="NASDAQ", quote_type="EQUITY")
        assert result.to_dict() == {
            "symbol": "NVDA", "name": "NVIDIA", "exchange": "NASDAQ", "quote_type": "EQUITY",
        }

    def test_optional_fields_default_to_none(self):
        result = SearchResult(symbol="X", name="Y")
        assert result.exchange is None
        assert result.quote_type is None
