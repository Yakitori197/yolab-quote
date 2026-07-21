"""Symbol normalization -- the rule three separate implementations got wrong."""

import pytest

from yolab_quote import exceptions, markets


class TestTaiwanCodes:
    """Regression tests for the leveraged/inverse ETF suffix.

    ``code.isdigit()`` (Stock_LineBot) and ``^\\d{4,6}$`` (Discord_StockBot)
    both reject ``00631L``, so those bots treated it as a US ticker and
    looked up an instrument that does not exist.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2330", "2330.TW"),
            ("0050", "0050.TW"),
            ("00631L", "00631L.TW"),  # leveraged ETF
            ("00632R", "00632R.TW"),  # inverse ETF
            ("911616", "911616.TW"),  # 6-digit listing
        ],
    )
    def test_bare_codes_gain_tw_suffix(self, raw, expected):
        assert markets.normalize_stock(raw) == expected

    @pytest.mark.parametrize("raw", ["00631L", "00632R", "2330", "0050"])
    def test_recognised_as_tw_codes(self, raw):
        assert markets.is_tw_code(raw) is True

    def test_digits_only_rule_would_have_failed(self):
        """Documents the exact bug: the old predicate rejects a real ETF."""
        assert "00631L".isdigit() is False  # what the old code checked
        assert markets.is_tw_code("00631L") is True  # what is actually true


class TestExistingSuffixes:
    @pytest.mark.parametrize(
        "symbol",
        ["6488.TWO", "2330.TW", "0700.HK", "7203.T", "600519.SS", "005930.KS"],
    )
    def test_known_suffix_is_left_alone(self, symbol):
        assert markets.normalize_stock(symbol) == symbol

    def test_lowercase_is_upcased(self):
        assert markets.normalize_stock("2330.tw") == "2330.TW"


class TestUsSymbols:
    @pytest.mark.parametrize(("raw", "expected"), [("nvda", "NVDA"), ("  aapl  ", "AAPL")])
    def test_us_tickers_pass_through(self, raw, expected):
        assert markets.normalize_stock(raw) == expected

    def test_dotted_but_unknown_suffix_is_not_treated_as_exchange(self):
        # BRK.B is a US share class, not an exchange suffix.
        assert markets.normalize_stock("BRK.B") == "BRK.B"
        assert markets.detect_market("BRK.B") == markets.US_STOCK


class TestMarketDetection:
    @pytest.mark.parametrize(
        ("symbol", "market"),
        [
            ("2330.TW", markets.TW_STOCK),
            ("6488.TWO", markets.TW_STOCK),
            ("00631L", markets.TW_STOCK),
            ("NVDA", markets.US_STOCK),
            ("0700.HK", markets.HK_STOCK),
            ("7203.T", markets.JP_STOCK),
            ("600519.SS", markets.CN_STOCK),
            ("005930.KS", markets.KR_STOCK),
        ],
    )
    def test_detect(self, symbol, market):
        assert markets.detect_market(symbol) == market

    def test_every_detected_market_is_a_stock_market(self):
        for symbol in ("2330", "NVDA", "0700.HK", "7203.T"):
            assert markets.is_stock_market(markets.detect_market(symbol))


class TestCurrency:
    @pytest.mark.parametrize(
        ("symbol", "currency"),
        [("2330.TW", "TWD"), ("NVDA", "USD"), ("0700.HK", "HKD"), ("7203.T", "JPY")],
    )
    def test_currency_from_symbol(self, symbol, currency):
        assert markets.currency_for(symbol) == currency

    def test_currency_from_market_identifier(self):
        assert markets.currency_for(markets.TW_STOCK) == "TWD"


class TestRejects:
    @pytest.mark.parametrize("bad", ["", "   ", "\t"])
    def test_empty_symbol_raises(self, bad):
        with pytest.raises(exceptions.SymbolError):
            markets.normalize_stock(bad)

    def test_non_string_raises(self):
        with pytest.raises(exceptions.SymbolError):
            markets.clean(None)
