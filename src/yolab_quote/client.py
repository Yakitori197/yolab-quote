"""The fallback manager and the package's main entry point.

Routing works in three layers: a market is inferred from (or supplied with)
the symbol, that market maps to an ordered list of provider names, and each
provider in turn is tried until one answers.

Two things the original manager got wrong are fixed here. It never checked
``supported_market_types`` before dispatching, so a misconfigured priority
list would return a crypto quote in answer to a stock query; :meth:`supports`
is now enforced. And it built providers through a zero-argument factory, so
nothing constructed through the fallback chain could receive an API key or a
timeout; ``provider_options`` now flows through.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from . import providers as registry
from .cache import DEFAULT_TTL_SECONDS, TTLCache
from .exceptions import (
    AllProvidersFailedError,
    QuoteError,
)
from .markets import (
    CN_STOCK,
    CRYPTO_FUTURES,
    CRYPTO_FUTURES_COIN,
    CRYPTO_SPOT,
    DE_STOCK,
    HK_STOCK,
    JP_STOCK,
    KR_STOCK,
    TW_STOCK,
    UK_STOCK,
    US_STOCK,
    clean,
    detect_market,
)
from .models import Bar, ProviderHealth, Quote
from .providers.base import Provider

#: Default fallback order per market. Every equity market shares a chain
#: because the same providers serve them; crypto markets are listed
#: separately so their vocabulary never reaches the stock path.
DEFAULT_PRIORITY: dict[str, tuple[str, ...]] = {
    TW_STOCK: ("yfinance",),
    US_STOCK: ("yfinance",),
    HK_STOCK: ("yfinance",),
    JP_STOCK: ("yfinance",),
    CN_STOCK: ("yfinance",),
    UK_STOCK: ("yfinance",),
    DE_STOCK: ("yfinance",),
    KR_STOCK: ("yfinance",),
    CRYPTO_SPOT: (),
    CRYPTO_FUTURES: (),
    CRYPTO_FUTURES_COIN: (),
}

#: Environment override, e.g. YOLAB_QUOTE_TW_STOCK_PROVIDERS=yfinance,yahoo_scraper
ENV_PREFIX = "YOLAB_QUOTE_"

DEFAULT_MAX_WORKERS = 8


def _env_priority(market: str) -> tuple[str, ...] | None:
    raw = os.getenv(f"{ENV_PREFIX}{market.upper()}_PROVIDERS")
    if not raw:
        return None
    names = tuple(part.strip() for part in raw.split(",") if part.strip())
    return names or None


class QuoteClient:
    """Fetches quotes through an ordered chain of providers.

    Args:
        providers: Pre-built provider instances keyed by name. Anything not
            supplied here is built lazily from the registry. Injecting fakes
            here is how the tests avoid the network.
        priority: market -> ordered provider names. Falls back to the
            environment, then to :data:`DEFAULT_PRIORITY`.
        provider_options: name -> kwargs passed to that provider's
            constructor when it is built lazily.
        cache: A :class:`~yolab_quote.cache.TTLCache`, or ``None`` to disable
            caching entirely.
        max_workers: Default thread-pool size for :meth:`get_quotes`.
    """

    def __init__(
        self,
        *,
        providers: dict[str, Provider] | None = None,
        priority: dict[str, Sequence[str]] | None = None,
        provider_options: dict[str, dict[str, Any]] | None = None,
        cache: TTLCache | None = None,
        ttl: float = DEFAULT_TTL_SECONDS,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        self._instances: dict[str, Provider] = dict(providers or {})
        self._explicit_priority = {m: tuple(names) for m, names in (priority or {}).items()}
        self._options = dict(provider_options or {})
        self._cache = TTLCache(ttl) if cache is None and ttl > 0 else cache
        self._max_workers = max(1, max_workers)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #
    def providers_for(self, market: str) -> tuple[str, ...]:
        """Provider names to try for ``market``, in order."""
        if market in self._explicit_priority:
            return self._explicit_priority[market]
        from_env = _env_priority(market)
        if from_env is not None:
            return from_env
        return DEFAULT_PRIORITY.get(market, ())

    def _get_provider(self, name: str) -> Provider | None:
        """Build (once) and return a provider, or ``None`` if it cannot be built.

        A provider that fails to construct must not take down the chain --
        that is the whole point of having a fallback.
        """
        with self._lock:
            existing = self._instances.get(name)
            if existing is not None:
                return existing
            try:
                instance = registry.create(name, **self._options.get(name, {}))
            except Exception:  # noqa: BLE001 - a broken provider is skipped, not fatal
                return None
            self._instances[name] = instance
            return instance

    # ------------------------------------------------------------------ #
    # Quotes
    # ------------------------------------------------------------------ #
    def get_quote(self, symbol: str, market: str | None = None, *, use_cache: bool = True) -> Quote:
        """Fetch one quote, trying each provider for the market in order.

        ``market`` is inferred from the symbol when omitted, which resolves
        to an equity market. Crypto markets must be passed explicitly -- the
        stock path stays free of crypto guesswork that way.

        Raises:
            SymbolError: the symbol is empty or malformed.
            AllProvidersFailedError: every provider failed, carrying the
                individual reasons.
        """
        cleaned = clean(symbol)
        resolved_market = market or detect_market(cleaned)

        if use_cache and self._cache is not None:
            cached = self._cache.get(resolved_market, cleaned)
            if cached is not None:
                return cached

        names = self.providers_for(resolved_market)
        if not names:
            raise AllProvidersFailedError(
                cleaned, resolved_market, {"(none)": f"no providers configured for {resolved_market}"}
            )

        failures: dict[str, str] = {}
        for name in names:
            provider = self._get_provider(name)
            if provider is None:
                failures[name] = "could not be constructed"
                continue
            if not provider.supports(resolved_market):
                failures[name] = f"does not serve {resolved_market}"
                continue
            try:
                quote = provider.get_quote(cleaned)
            except QuoteError as exc:
                failures[name] = str(exc)
                continue
            except Exception as exc:  # noqa: BLE001 - an unexpected error still falls through
                failures[name] = f"unexpected {type(exc).__name__}: {exc}"
                continue

            if self._cache is not None:
                # Same key rule as the read above -- see cache.cache_key.
                self._cache.set(resolved_market, cleaned, quote)
            return quote

        raise AllProvidersFailedError(cleaned, resolved_market, failures)

    def get_quotes(
        self,
        symbols: Iterable[str],
        market: str | None = None,
        *,
        max_workers: int | None = None,
        use_cache: bool = True,
    ) -> dict[str, Quote]:
        """Fetch many quotes concurrently, keyed by the symbols you passed in.

        Symbols that fail are omitted; compare the keys against your input to
        find them. Duplicates are collapsed and input order is preserved.

        The concurrency is the point: fetching a few dozen symbols one at a
        time is what pushed the LINE bot past its reply-token deadline.
        """
        unique = list(dict.fromkeys(clean(s) for s in symbols))
        if not unique:
            return {}

        workers = max(1, min(max_workers or self._max_workers, len(unique)))
        found: dict[str, Quote] = {}

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._quote_or_none, symbol, market, use_cache): symbol
                for symbol in unique
            }
            for future in as_completed(futures):
                quote = future.result()
                if quote is not None:
                    found[futures[future]] = quote

        return {symbol: found[symbol] for symbol in unique if symbol in found}

    def _quote_or_none(self, symbol: str, market: str | None, use_cache: bool) -> Quote | None:
        try:
            return self.get_quote(symbol, market, use_cache=use_cache)
        except QuoteError:
            return None
        except Exception:  # noqa: BLE001 - a single bad symbol must not abort the batch
            return None

    # ------------------------------------------------------------------ #
    # History
    # ------------------------------------------------------------------ #
    def get_bars(self, symbol: str, days: int = 30, market: str | None = None) -> list[Bar]:
        """Fetch recent daily candles, oldest first, with the same fallback chain."""
        cleaned = clean(symbol)
        resolved_market = market or detect_market(cleaned)
        names = self.providers_for(resolved_market)

        failures: dict[str, str] = {}
        for name in names:
            provider = self._get_provider(name)
            if provider is None:
                failures[name] = "could not be constructed"
                continue
            if not provider.supports(resolved_market):
                failures[name] = f"does not serve {resolved_market}"
                continue
            try:
                return provider.get_bars(cleaned, days)
            except QuoteError as exc:
                failures[name] = str(exc)
                continue
            except Exception as exc:  # noqa: BLE001
                failures[name] = f"unexpected {type(exc).__name__}: {exc}"
                continue

        raise AllProvidersFailedError(cleaned, resolved_market, failures or {"(none)": "no providers"})

    # ------------------------------------------------------------------ #
    # Introspection and lifecycle
    # ------------------------------------------------------------------ #
    def health(self) -> dict[str, ProviderHealth]:
        """Health of every registered provider. Never raises, never hits the network."""
        report: dict[str, ProviderHealth] = {}
        # Registry entries plus anything injected -- a caller that supplied a
        # custom provider still expects to see it on a health endpoint.
        for name in sorted(set(registry.available()) | set(self._instances)):
            provider = self._get_provider(name)
            if provider is None:
                report[name] = ProviderHealth(
                    provider=name, ok=False, status="unavailable",
                    detail="could not be constructed",
                )
                continue
            try:
                report[name] = provider.health()
            except Exception as exc:  # noqa: BLE001 - health must never throw
                report[name] = ProviderHealth(
                    provider=name, ok=False, status="error", detail=str(exc)
                )
        return report

    def close(self) -> None:
        """Close every built provider and drop the cache."""
        with self._lock:
            instances = list(self._instances.values())
            self._instances.clear()
        for provider in instances:
            try:
                provider.close()
            except Exception:  # noqa: BLE001 - best effort
                continue
        if self._cache is not None:
            self._cache.clear()

    def __enter__(self) -> QuoteClient:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Module-level convenience API
# --------------------------------------------------------------------------- #
_default_client: QuoteClient | None = None
_default_lock = threading.Lock()


def get_default_client() -> QuoteClient:
    """The process-wide default client, built on first use."""
    global _default_client
    with _default_lock:
        if _default_client is None:
            _default_client = QuoteClient()
        return _default_client


def reset_default_client() -> None:
    """Discard the default client. Mainly for tests."""
    global _default_client
    with _default_lock:
        client, _default_client = _default_client, None
    if client is not None:
        client.close()


def get_quote(symbol: str, market: str | None = None) -> Quote:
    """Fetch one quote using the default client."""
    return get_default_client().get_quote(symbol, market)


def get_quotes(symbols: Iterable[str], market: str | None = None) -> dict[str, Quote]:
    """Fetch many quotes concurrently using the default client."""
    return get_default_client().get_quotes(symbols, market)


def get_bars(symbol: str, days: int = 30, market: str | None = None) -> list[Bar]:
    """Fetch daily candles using the default client."""
    return get_default_client().get_bars(symbol, days, market)


def health() -> dict[str, ProviderHealth]:
    """Provider health from the default client."""
    return get_default_client().health()
