"""Provider registry: lazy resolution, third-party registration, kwargs."""

import sys

import pytest

from yolab_quote import markets, providers
from yolab_quote.exceptions import ProviderError
from yolab_quote.models import ProviderHealth, Quote
from yolab_quote.providers.base import Provider
from yolab_quote.providers.yfinance_provider import YFinanceProvider


class CustomProvider(Provider):
    name = "custom"
    markets = (markets.TW_STOCK,)

    def __init__(self, *, api_key: str | None = None, timeout: float = 5.0) -> None:
        self.api_key = api_key
        self.timeout = timeout

    def get_quote(self, symbol: str) -> Quote:
        return Quote.create(symbol=symbol, market=markets.TW_STOCK, source=self.name, price=1.0)

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, ok=True, status="ready", markets=self.markets)


@pytest.fixture(autouse=True)
def _clean_registry():
    yield
    providers.unregister("custom")


class TestBuiltins:
    def test_yfinance_is_available(self):
        assert "yfinance" in providers.available()

    def test_creates_the_right_class(self):
        assert isinstance(providers.create("yfinance"), YFinanceProvider)

    def test_unknown_name_raises_with_a_useful_message(self):
        with pytest.raises(ProviderError) as excinfo:
            providers.create("nope")
        assert "nope" in str(excinfo.value)
        assert "yfinance" in str(excinfo.value)  # lists what is available


class TestRegistration:
    def test_third_party_can_register(self):
        """The registry this replaces was a private hardcoded dict -- adding
        a provider meant editing the package."""
        providers.register("custom", CustomProvider)
        assert "custom" in providers.available()
        assert isinstance(providers.create("custom"), CustomProvider)

    def test_kwargs_reach_the_constructor(self):
        """Regression: the original factory took no arguments, so nothing
        built through the fallback chain could receive an API key."""
        providers.register("custom", CustomProvider)
        instance = providers.create("custom", api_key="secret", timeout=30.0)
        assert instance.api_key == "secret"
        assert instance.timeout == 30.0

    def test_unregister(self):
        providers.register("custom", CustomProvider)
        providers.unregister("custom")
        assert "custom" not in providers.available()

    def test_unregister_is_silent_when_absent(self):
        providers.unregister("never-registered")

    def test_rejects_bad_registration(self):
        with pytest.raises(ValueError):
            providers.register("", CustomProvider)
        with pytest.raises(TypeError):
            providers.register("custom", "not callable")  # type: ignore[arg-type]

    def test_factory_must_produce_a_provider(self):
        providers.register("custom", lambda **_: "not a provider")
        with pytest.raises(ProviderError):
            providers.create("custom")


class TestLaziness:
    def test_importing_the_package_does_not_import_yfinance(self):
        """A hard import here would break the bots that ship without it --
        one of them actually ran in production that way."""
        assert "yfinance" not in sys.modules

    def test_listing_providers_does_not_import_yfinance(self):
        providers.available()
        assert "yfinance" not in sys.modules
