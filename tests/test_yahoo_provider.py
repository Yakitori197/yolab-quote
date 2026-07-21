"""Yahoo chart API provider.

Payloads mirror the real endpoint's shape, captured from a live call to
``/v8/finance/chart/2330.TW``. The network sits behind the ``_fetch`` seam,
so nothing here touches it.
"""

import pytest

from yolab_quote import markets
from yolab_quote.exceptions import ProviderError, SymbolNotFoundError
from yolab_quote.models import Bar
from yolab_quote.providers.yahoo_provider import (
    YahooProvider,
    parse_chart_bars,
    parse_chart_quote,
    range_for_days,
)


def chart_payload(**meta_overrides):
    meta = {
        "currency": "TWD",
        "symbol": "2330.TW",
        "exchangeName": "TAI",
        "instrumentType": "EQUITY",
        "regularMarketPrice": 2385.0,
        "chartPreviousClose": 2320.0,
        "regularMarketDayHigh": 2385.0,
        "regularMarketDayLow": 2345.0,
        "regularMarketVolume": 13878386,
        "fiftyTwoWeekHigh": 2535.0,
        "fiftyTwoWeekLow": 1125.0,
        "longName": "Taiwan Semiconductor Manufacturing Company Limited",
        "shortName": "TAIWAN SEMICONDUCTOR MANUFACTUR",
    }
    meta.update(meta_overrides)
    return {
        "chart": {
            "error": None,
            "result": [{
                "meta": meta,
                "timestamp": [1767225600],
                "indicators": {"quote": [{
                    "open": [2350.0], "high": [2385.0], "low": [2345.0],
                    "close": [2385.0], "volume": [13878386],
                }]},
            }],
        }
    }


def bars_payload(count=3):
    base = 1767225600
    return {
        "chart": {
            "error": None,
            "result": [{
                "meta": {"symbol": "2330.TW", "currency": "TWD"},
                "timestamp": [base + i * 86400 for i in range(count)],
                "indicators": {"quote": [{
                    "open": [100.0 + i for i in range(count)],
                    "high": [105.0 + i for i in range(count)],
                    "low": [99.0 + i for i in range(count)],
                    "close": [104.0 + i for i in range(count)],
                    "volume": [1000 + i for i in range(count)],
                }]},
            }],
        }
    }


class TestParseQuote:
    def test_maps_core_fields(self):
        quote = parse_chart_quote("2330", chart_payload())
        assert quote.symbol == "2330.TW"
        assert quote.market == markets.TW_STOCK
        assert quote.source == "yahoo"
        assert quote.price == 2385.0
        assert quote.currency == "TWD"
        assert quote.name.startswith("Taiwan Semiconductor")

    def test_uses_chart_previous_close(self):
        """Yahoo names the baseline differently here than on other endpoints."""
        quote = parse_chart_quote("2330", chart_payload())
        assert quote.previous_close == 2320.0
        assert quote.change == pytest.approx(65.0)
        assert quote.change_percent == pytest.approx(65.0 / 2320.0 * 100)

    def test_open_comes_from_the_indicator_series(self):
        """`open` is absent from meta; it only exists in the series."""
        quote = parse_chart_quote("2330", chart_payload())
        assert quote.open == 2350.0

    def test_extra_fields(self):
        quote = parse_chart_quote("2330", chart_payload())
        assert quote.extra["fifty_two_week_high"] == 2535.0
        assert quote.extra["exchange"] == "TAI"

    def test_falls_back_to_short_name(self):
        payload = chart_payload(longName=None)
        assert parse_chart_quote("2330", payload).name == "TAIWAN SEMICONDUCTOR MANUFACTUR"

    def test_us_symbol_detected(self):
        payload = chart_payload(symbol="NVDA", currency="USD")
        assert parse_chart_quote("NVDA", payload).market == markets.US_STOCK

    def test_regular_market_time_becomes_updated_at(self):
        payload = chart_payload(regularMarketTime=1767225600)
        quote = parse_chart_quote("2330", payload)
        assert quote.updated_at.year == 2026


class TestParseQuoteErrors:
    def test_no_price_means_not_found(self):
        payload = chart_payload(regularMarketPrice=None, chartPreviousClose=None)
        with pytest.raises(SymbolNotFoundError):
            parse_chart_quote("NOSUCH", payload)

    def test_upstream_not_found_error(self):
        payload = {"chart": {"error": {"code": "Not Found", "description": "No data found"},
                             "result": None}}
        with pytest.raises(SymbolNotFoundError):
            parse_chart_quote("NOSUCH", payload)

    def test_other_upstream_error_is_a_provider_error(self):
        payload = {"chart": {"error": {"code": "Internal", "description": "boom"}, "result": None}}
        with pytest.raises(ProviderError):
            parse_chart_quote("2330", payload)

    def test_empty_result_list(self):
        with pytest.raises(SymbolNotFoundError):
            parse_chart_quote("2330", {"chart": {"error": None, "result": []}})

    def test_garbage_payload(self):
        with pytest.raises(ProviderError):
            parse_chart_quote("2330", {"nonsense": True})

    def test_missing_meta_block(self):
        payload = {"chart": {"error": None, "result": [{"timestamp": []}]}}
        with pytest.raises(ProviderError):
            parse_chart_quote("2330", payload)


class TestParseBars:
    def test_returns_bars_oldest_first(self):
        bars = parse_chart_bars("2330", bars_payload(3))
        assert len(bars) == 3
        assert isinstance(bars[0], Bar)
        assert bars[0].close == 104.0
        assert bars[0].date < bars[-1].date

    def test_skips_null_sessions(self):
        payload = bars_payload(3)
        payload["chart"]["result"][0]["indicators"]["quote"][0]["close"][1] = None
        bars = parse_chart_bars("2330", payload)
        assert len(bars) == 2

    def test_no_candles_raises(self):
        payload = bars_payload(1)
        payload["chart"]["result"][0]["timestamp"] = []
        with pytest.raises(SymbolNotFoundError):
            parse_chart_bars("2330", payload)

    def test_all_null_candles_raises(self):
        payload = bars_payload(2)
        payload["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [None, None]
        with pytest.raises(SymbolNotFoundError):
            parse_chart_bars("2330", payload)


class TestRangeForDays:
    @pytest.mark.parametrize(
        ("days", "expected"),
        [(1, "1mo"), (15, "1mo"), (20, "3mo"), (60, "6mo"), (120, "1y"), (200, "2y"), (900, "5y")],
    )
    def test_range(self, days, expected):
        assert range_for_days(days) == expected

    def test_120_bars_spans_a_full_year(self):
        """Regression: 120 bars mapped to '6mo' (~126 sessions before
        holidays), so an MA120 built on it was always empty."""
        assert range_for_days(120) == "1y"


class TestProvider:
    def test_get_quote_normalizes_and_requests_one_day(self):
        provider = YahooProvider()
        seen = {}

        def fake_fetch(symbol, *, chart_range, interval):
            seen.update(symbol=symbol, chart_range=chart_range, interval=interval)
            return chart_payload()

        provider._fetch = fake_fetch  # type: ignore[method-assign]
        quote = provider.get_quote("2330")

        assert seen == {"symbol": "2330.TW", "chart_range": "1d", "interval": "1d"}
        assert quote.price == 2385.0

    def test_leveraged_etf_routed_to_taiwan(self):
        provider = YahooProvider()
        seen = []
        provider._fetch = lambda s, **k: (seen.append(s), chart_payload())[1]  # type: ignore[method-assign]
        provider.get_quote("00631L")
        assert seen == ["00631L.TW"]

    def test_get_bars_trims_to_requested_days(self):
        provider = YahooProvider()
        provider._fetch = lambda s, **k: bars_payload(5)  # type: ignore[method-assign]
        assert len(provider.get_bars("2330", 2)) == 2

    def test_rejects_non_positive_days(self):
        with pytest.raises(ValueError):
            YahooProvider().get_bars("2330", 0)

    def test_supports_only_equity_markets(self):
        provider = YahooProvider()
        assert provider.supports(markets.TW_STOCK) is True
        assert provider.supports(markets.CRYPTO_SPOT) is False

    def test_health_is_ready_without_a_network_call(self):
        health = YahooProvider().health()
        assert health.ok is True
        assert health.status == "ready"

    def test_close_is_safe_without_a_client(self):
        YahooProvider().close()

    def test_injected_client_is_not_closed(self):
        class FakeClient:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        client = FakeClient()
        provider = YahooProvider(client=client)  # type: ignore[arg-type]
        provider.close()
        assert client.closed is False  # we do not own it


class TestFallbackIntegration:
    def test_yahoo_takes_over_when_yfinance_fails(self):
        """The point of a second provider: no shared code path with the first."""
        from yolab_quote.client import QuoteClient
        from yolab_quote.providers.yfinance_provider import YFinanceProvider

        broken = YFinanceProvider()
        broken._fetch_info = lambda s: (_ for _ in ()).throw(ProviderError("yfinance down"))  # type: ignore[method-assign]

        yahoo = YahooProvider()
        yahoo._fetch = lambda s, **k: chart_payload()  # type: ignore[method-assign]

        client = QuoteClient(providers={"yfinance": broken, "yahoo": yahoo}, ttl=0)
        quote = client.get_quote("2330")

        assert quote.source == "yahoo"
        assert quote.price == 2385.0
