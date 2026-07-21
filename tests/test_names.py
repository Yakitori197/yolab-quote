"""Chinese name resolution, including the ambiguity bug this replaces."""

import pytest

from yolab_quote import markets, names


class TestGetName:
    def test_taiwan_code(self):
        assert names.get_name("2330") == "台積電"

    def test_accepts_an_exchange_suffix(self):
        assert names.get_name("2330.TW") == "台積電"

    def test_us_ticker(self):
        assert names.get_name("AAPL") == "蘋果"

    def test_lowercase(self):
        assert names.get_name("aapl") == "蘋果"

    def test_china_code(self):
        assert names.get_name("600519") == "貴州茅台"

    def test_unknown_returns_none(self):
        assert names.get_name("9999999") is None

    def test_market_can_be_narrowed(self):
        assert names.get_name("2330", markets.TW_STOCK) == "台積電"
        assert names.get_name("2330", markets.US_STOCK) is None


class TestGetCode:
    def test_exact_name(self):
        assert names.get_code("台積電") == "2330"

    def test_whitespace_is_trimmed(self):
        assert names.get_code("  台積電  ") == "2330"

    def test_unique_prefix_resolves(self):
        """A prefix matching exactly one name still resolves."""
        names.register({"9998": "獨一無二測試公司"}, market=markets.TW_STOCK)
        assert names.get_code("獨一無二") == "9998"

    def test_taiji_prefix_is_genuinely_ambiguous(self):
        """Merging the two tables introduced a real collision: 台積電 (2330)
        and 台積電ADR (TSM) share the 台積 prefix, so it must not resolve."""
        assert names.get_code("台積") is None
        assert names.get_code("台積電") == "2330"

    def test_unknown_returns_none(self):
        assert names.get_code("這不是股票") is None

    def test_empty_returns_none(self):
        assert names.get_code("") is None
        assert names.get_code(None) is None  # type: ignore[arg-type]


class TestAmbiguityIsRefused:
    """Regression: the permissive matcher answered '金' with Fubon (2881).

    It matched on any substring in either direction and took the first hit,
    so a single common character returned a stock the user never asked for,
    with nothing in the reply to indicate a guess had been made.
    """

    def test_single_character_is_refused(self):
        assert names.get_code("金") is None

    @pytest.mark.parametrize("query", ["金", "台", "電", "中", "大", "元", "光"])
    def test_common_single_characters_never_guess(self, query):
        assert names.get_code(query) is None

    def test_ambiguous_prefix_is_refused(self):
        """Several names start with 中華; none of them may be assumed."""
        result = names.get_code("中")
        assert result is None

    def test_a_returned_code_always_matches_the_query(self):
        """The property that actually matters: no unrelated instrument."""
        for query in ["台積電", "台積", "鴻海", "聯發科"]:
            code = names.get_code(query)
            if code is None:
                continue
            name = names.get_name(code)
            assert name is not None
            assert name.startswith(query) or query.startswith(name)


class TestResolve:
    def test_chinese_name(self):
        assert names.resolve("台積電") == "2330.TW"

    def test_bare_code(self):
        assert names.resolve("2330") == "2330.TW"

    def test_leveraged_etf_code(self):
        assert names.resolve("00631L") == "00631L.TW"

    def test_us_ticker(self):
        assert names.resolve("NVDA") == "NVDA"

    def test_english_alias(self):
        assert names.resolve("nvidia") == "NVDA"

    def test_misspelled_alias(self):
        """The Discord bot had collected real user typos; they carry over."""
        assert names.resolve("nvida") == "NVDA"

    def test_alias_is_case_insensitive(self):
        assert names.resolve("NVIDIA") == "NVDA"

    def test_ambiguous_input_is_refused(self):
        assert names.resolve("金") is None

    def test_junk_is_refused(self):
        assert names.resolve("!!!") is None
        assert names.resolve("") is None
        assert names.resolve(None) is None  # type: ignore[arg-type]


class TestSearch:
    def test_finds_by_partial_name(self):
        hits = names.search("台")
        assert hits
        assert all(len(hit) == 2 for hit in hits)

    def test_respects_the_limit(self):
        assert len(names.search("台", limit=2)) <= 2

    def test_search_may_be_generous_where_get_code_may_not(self):
        """Returning several candidates is safe; silently picking one is not."""
        assert names.get_code("金") is None
        assert len(names.search("金")) >= 1

    def test_empty_query(self):
        assert names.search("") == []


class TestRegister:
    def test_adds_an_entry(self):
        names.register({"1234": "測試公司"}, market=markets.TW_STOCK)
        assert names.get_name("1234") == "測試公司"
        assert names.get_code("測試公司") == "1234"
        assert names.resolve("測試公司") == "1234.TW"

    def test_adds_an_alias(self):
        names.register({}, aliases={"testco": "1234"})
        assert names.resolve("testco") == "1234.TW"

    def test_rejects_an_unknown_market(self):
        with pytest.raises(ValueError):
            names.register({"1": "x"}, market="not_a_market")


class TestStats:
    def test_reports_counts(self):
        counts = names.stats()
        assert counts["tw"] >= 100
        assert counts["us"] >= 60
        assert counts["aliases"] >= 150

    def test_known_codes(self):
        assert "2330" in names.known_codes(markets.TW_STOCK)
        assert "2330" in names.known_codes()
