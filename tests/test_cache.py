"""TTL cache: one key rule, bounded size, expiry."""

import pytest

from yolab_quote.cache import TTLCache, cache_key
from yolab_quote.models import Quote


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_quote(symbol: str = "2330.TW", price: float = 100.0) -> Quote:
    return Quote.create(symbol=symbol, market="tw_stock", source="fake", price=price)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def cache(clock):
    return TTLCache(ttl=10.0, time_func=clock)


class TestKeyRule:
    def test_key_is_case_and_whitespace_insensitive(self):
        assert cache_key("tw_stock", "  2330.tw ") == cache_key("tw_stock", "2330.TW")

    def test_market_is_part_of_the_key(self):
        assert cache_key("tw_stock", "X") != cache_key("us_stock", "X")

    def test_read_and_write_agree(self, cache):
        """Regression: the original wrote batch entries under the provider's
        normalized symbol but read them under the caller's raw one, so batch
        cache entries were never hit."""
        cache.set("tw_stock", "2330.TW", make_quote())
        assert cache.get("tw_stock", "  2330.tw  ") is not None

    def test_different_markets_do_not_collide(self, cache):
        cache.set("tw_stock", "X", make_quote(price=1.0))
        cache.set("us_stock", "X", make_quote(price=2.0))
        assert cache.get("tw_stock", "X").price == 1.0
        assert cache.get("us_stock", "X").price == 2.0


class TestExpiry:
    def test_hit_before_ttl(self, cache, clock):
        cache.set("tw_stock", "2330", make_quote())
        clock.advance(9.0)
        assert cache.get("tw_stock", "2330") is not None

    def test_miss_after_ttl(self, cache, clock):
        cache.set("tw_stock", "2330", make_quote())
        clock.advance(10.0)
        assert cache.get("tw_stock", "2330") is None

    def test_expired_entry_is_dropped_on_read(self, cache, clock):
        cache.set("tw_stock", "2330", make_quote())
        clock.advance(11.0)
        cache.get("tw_stock", "2330")
        assert len(cache) == 0

    def test_per_entry_ttl_override(self, cache, clock):
        cache.set("tw_stock", "SHORT", make_quote(), ttl=1.0)
        cache.set("tw_stock", "LONG", make_quote())
        clock.advance(2.0)
        assert cache.get("tw_stock", "SHORT") is None
        assert cache.get("tw_stock", "LONG") is not None

    def test_zero_ttl_stores_nothing(self, cache):
        cache.set("tw_stock", "X", make_quote(), ttl=0)
        assert cache.get("tw_stock", "X") is None

    def test_purge_expired(self, cache, clock):
        cache.set("tw_stock", "A", make_quote())
        cache.set("tw_stock", "B", make_quote())
        clock.advance(11.0)
        cache.set("tw_stock", "C", make_quote())
        assert cache.purge_expired() == 2
        assert cache.get("tw_stock", "C") is not None


class TestBounded:
    def test_evicts_when_full(self, clock):
        cache = TTLCache(ttl=100.0, max_entries=3, time_func=clock)
        for i in range(5):
            cache.set("tw_stock", f"S{i}", make_quote())
        assert len(cache) <= 3

    def test_prefers_evicting_expired_entries(self, clock):
        cache = TTLCache(ttl=10.0, max_entries=2, time_func=clock)
        cache.set("tw_stock", "OLD", make_quote())
        clock.advance(11.0)
        cache.set("tw_stock", "NEW", make_quote())
        cache.set("tw_stock", "NEWER", make_quote())
        assert cache.get("tw_stock", "NEW") is not None
        assert cache.get("tw_stock", "NEWER") is not None

    def test_rejects_bad_config(self):
        with pytest.raises(ValueError):
            TTLCache(ttl=-1)
        with pytest.raises(ValueError):
            TTLCache(max_entries=0)


class TestClear:
    def test_clear(self, cache):
        cache.set("tw_stock", "X", make_quote())
        cache.clear()
        assert len(cache) == 0
