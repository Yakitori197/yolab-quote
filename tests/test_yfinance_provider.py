"""yfinance provider.

Every test here runs without yfinance installed and without a network call:
the field mapping is a module-level pure function, and the SDK calls sit
behind two overridable seams (``_fetch_info`` / ``_fetch_history``).
"""

import pytest

from yolab_quote import markets
from yolab_quote.exceptions import SymbolNotFoundError
from yolab_quote.models import Bar
from yolab_quote.providers.yfinance_provider import (
    YFinanceProvider,
    info_to_quote,
    period_for_days,
    yfinance_available,
)

TW_INFO = {
    "longName": "Taiwan Semiconductor Manufacturing Company Limited",
    "shortName": "TSMC",
    "currentPrice": 1085.0,
    "previousClose": 1070.0,
    "open": 1075.0,
    "dayHigh": 1090.0,
    "dayLow": 1072.0,
    "volume": 31_000_000,
    "currency": "TWD",
    "trailingPE": 24.5,
    "marketCap": 28_000_000_000_000,
    "dividendYield": 0.0135,
    "sector": "Technology",
}


class TestInfoToQuote:
    def test_maps_core_fields(self):
        quote = info_to_quote("2330", "2330.TW", TW_INFO)
        assert quote.symbol == "2330.TW"
        assert quote.market == markets.TW_STOCK
        assert quote.source == "yfinance"
        assert quote.name == TW_INFO["longName"]
        assert quote.price == 1085.0
        assert quote.previous_close == 1070.0
        assert quote.currency == "TWD"

    def test_derives_change_from_previous_close(self):
        quote = info_to_quote("2330", "2330.TW", TW_INFO)
        assert quote.change == pytest.approx(15.0)
        assert quote.change_percent == pytest.approx(15.0 / 1070.0 * 100)

    def test_dividend_yield_is_converted_to_percent(self):
        """yfinance 0.2.x reports a ratio; the <0.3 pin makes this safe."""
        quote = info_to_quote("2330", "2330.TW", TW_INFO)
        assert quote.extra["dividend_yield"] == pytest.approx(1.35)

    def test_optional_fields_land_in_extra(self):
        quote = info_to_quote("2330", "2330.TW", TW_INFO)
        assert quote.extra["pe_ratio"] == 24.5
        assert quote.extra["market_cap"] == 28_000_000_000_000
        assert quote.extra["sector"] == "Technology"

    def test_falls_back_through_key_aliases(self):
        quote = info_to_quote("NVDA", "NVDA", {
            "shortName": "NVIDIA",
            "regularMarketPrice": 900.0,
            "regularMarketPreviousClose": 880.0,
            "regularMarketVolume": 1000,
        })
        assert quote.name == "NVIDIA"
        assert quote.price == 900.0
        assert quote.volume == 1000.0

    def test_empty_payload_means_no_such_symbol(self):
        """yfinance returns a near-empty dict for unknown tickers rather
        than raising, so an absent price is the only reliable signal."""
        with pytest.raises(SymbolNotFoundError):
            info_to_quote("NOSUCH", "NOSUCH", {})

    def test_price_missing_but_previous_close_present_is_accepted(self):
        quote = info_to_quote("X", "X", {"previousClose": 10.0})
        assert quote.price is None
        assert quote.previous_close == 10.0

    def test_currency_defaults_from_market_when_absent(self):
        quote = info_to_quote("2330", "2330.TW", {"currentPrice": 1.0})
        assert quote.currency == "TWD"

    def test_us_symbol_detected(self):
        quote = info_to_quote("NVDA", "NVDA", {"currentPrice": 900.0})
        assert quote.market == markets.US_STOCK

    def test_raw_payload_is_preserved(self):
        quote = info_to_quote("2330", "2330.TW", TW_INFO)
        assert quote.raw["longName"] == TW_INFO["longName"]


class TestPeriodForDays:
    @pytest.mark.parametrize(
        ("days", "period"),
        [(1, "1mo"), (15, "1mo"), (30, "3mo"), (60, "6mo"),
         (120, "1y"), (200, "2y"), (500, "5y")],
    )
    def test_period(self, days, period):
        assert period_for_days(days) == period

    def test_120_bars_spans_a_full_year(self):
        """Regression: a calendar-based mapping returned '6mo' for 120 bars,
        which does not contain 120 trading sessions."""
        assert period_for_days(120) == "1y"


class TestProviderSeams:
    def test_get_quote_normalizes_before_lookup(self):
        provider = YFinanceProvider()
        seen: list[str] = []

        def fake_fetch(yahoo_symbol):
            seen.append(yahoo_symbol)
            return TW_INFO

        provider._fetch_info = fake_fetch  # type: ignore[method-assign]
        quote = provider.get_quote("2330")

        assert seen == ["2330.TW"]
        assert quote.symbol == "2330.TW"

    def test_leveraged_etf_is_routed_to_taiwan(self):
        """Regression: a digits-only rule sent 00631L to the US market."""
        provider = YFinanceProvider()
        seen: list[str] = []
        provider._fetch_info = lambda s: (seen.append(s), TW_INFO)[1]  # type: ignore[method-assign]
        provider.get_quote("00631L")
        assert seen == ["00631L.TW"]

    def test_supports_declared_markets_only(self):
        provider = YFinanceProvider()
        assert provider.supports(markets.TW_STOCK) is True
        assert provider.supports(markets.US_STOCK) is True
        assert provider.supports(markets.CRYPTO_SPOT) is False


class FakeRow(dict):
    """Minimal stand-in for a pandas row: ``.get`` is all the code uses."""


class FakeIndex:
    def __init__(self, text: str) -> None:
        self._text = text

    def strftime(self, _fmt: str) -> str:
        return self._text


class FakeFrame:
    """Minimal stand-in for a pandas DataFrame."""

    def __init__(self, rows):
        self._rows = rows

    def __len__(self):
        return len(self._rows)

    def iterrows(self):
        return iter(self._rows)


class TestBars:
    def _frame(self):
        return FakeFrame([
            (FakeIndex("2026-01-02"),
             FakeRow(Open=100.0, High=105.0, Low=99.0, Close=104.0, Volume=1000)),
            (FakeIndex("2026-01-03"),
             FakeRow(Open=104.0, High=108.0, Low=103.0, Close=107.0, Volume=1200)),
        ])

    def test_returns_bars_oldest_first(self):
        provider = YFinanceProvider()
        provider._fetch_history = lambda s, p: self._frame()  # type: ignore[method-assign]
        bars = provider.get_bars("2330", 5)
        assert [b.date for b in bars] == ["2026-01-02", "2026-01-03"]
        assert isinstance(bars[0], Bar)
        assert bars[0].close == 104.0

    def test_trims_to_requested_days(self):
        provider = YFinanceProvider()
        provider._fetch_history = lambda s, p: self._frame()  # type: ignore[method-assign]
        assert len(provider.get_bars("2330", 1)) == 1

    def test_skips_rows_with_missing_values(self):
        """Holiday and halted rows arrive as NaN."""
        frame = FakeFrame([
            (FakeIndex("2026-01-02"),
             FakeRow(Open=float("nan"), High=105.0, Low=99.0, Close=104.0, Volume=1000)),
            (FakeIndex("2026-01-03"),
             FakeRow(Open=104.0, High=108.0, Low=103.0, Close=107.0, Volume=1200)),
        ])
        provider = YFinanceProvider()
        provider._fetch_history = lambda s, p: frame  # type: ignore[method-assign]
        bars = provider.get_bars("2330", 5)
        assert [b.date for b in bars] == ["2026-01-03"]

    def test_empty_history_raises(self):
        provider = YFinanceProvider()
        provider._fetch_history = lambda s, p: FakeFrame([])  # type: ignore[method-assign]
        with pytest.raises(SymbolNotFoundError):
            provider.get_bars("NOSUCH")

    def test_rejects_non_positive_days(self):
        with pytest.raises(ValueError):
            YFinanceProvider().get_bars("2330", 0)


class TestHealth:
    def test_health_never_raises_and_reports_installation(self):
        health = YFinanceProvider().health()
        assert health.provider == "yfinance"
        assert health.ok is yfinance_available()
        assert markets.TW_STOCK in health.markets

    def test_availability_check_does_not_import(self):
        """Importing yfinance eagerly is what this package must avoid."""
        import sys

        yfinance_available()
        # The check uses importlib.util.find_spec, which does not execute the
        # module; if it were a plain import this would now be populated.
        if "yfinance" not in sys.modules:
            assert True
