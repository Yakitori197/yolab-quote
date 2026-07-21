"""yolab-quote -- unified market data for Taiwan/US stocks and crypto.

One synchronous API over several providers, with automatic fallback:

    >>> import yolab_quote as yq
    >>> quote = yq.get_quote("2330")          # doctest: +SKIP
    >>> quote.symbol, quote.market            # doctest: +SKIP
    ('2330.TW', 'tw_stock')

Bare Taiwan codes are resolved for you, including the leveraged and inverse
ETFs (``00631L``, ``00632R``) that a digits-only rule silently mis-routes.

Nothing heavy is imported here: ``yfinance`` and ``bs4`` load only when a
provider that needs them is actually built.
"""

from __future__ import annotations

from .cache import TTLCache, cache_key
from .client import (
    QuoteClient,
    get_bars,
    get_default_client,
    get_quote,
    get_quotes,
    health,
    reset_default_client,
    search_symbols,
)
from .exceptions import (
    AllProvidersFailedError,
    ProviderError,
    ProviderUnavailableError,
    QuoteError,
    SymbolError,
    SymbolNotFoundError,
)
from .markets import (
    ALL_MARKETS,
    CN_STOCK,
    CRYPTO_FUTURES,
    CRYPTO_FUTURES_COIN,
    CRYPTO_SPOT,
    DE_STOCK,
    HK_STOCK,
    JP_STOCK,
    KR_STOCK,
    STOCK_MARKETS,
    TW_STOCK,
    UK_STOCK,
    US_STOCK,
    currency_for,
    detect_market,
    is_stock_market,
    is_tw_code,
    normalize_stock,
)
from .models import Bar, ProviderHealth, Quote, SearchResult, to_float
from .names import get_code, get_name, resolve
from .names import search as search_names  # offline tables; cf. search_symbols (online)
from .providers import Provider, available, create, register, unregister

__version__ = "0.1.0"

__all__ = [
    # models
    "Quote",
    "Bar",
    "SearchResult",
    "ProviderHealth",
    "to_float",
    # client
    "QuoteClient",
    "get_quote",
    "get_quotes",
    "get_bars",
    "health",
    "get_default_client",
    "reset_default_client",
    # providers
    "Provider",
    "available",
    "create",
    "register",
    "unregister",
    # names (offline tables) and symbol search (online)
    "resolve",
    "get_name",
    "get_code",
    "search_names",
    "search_symbols",
    # markets
    "normalize_stock",
    "detect_market",
    "currency_for",
    "is_tw_code",
    "is_stock_market",
    "ALL_MARKETS",
    "STOCK_MARKETS",
    "TW_STOCK",
    "US_STOCK",
    "HK_STOCK",
    "JP_STOCK",
    "CN_STOCK",
    "UK_STOCK",
    "DE_STOCK",
    "KR_STOCK",
    "CRYPTO_SPOT",
    "CRYPTO_FUTURES",
    "CRYPTO_FUTURES_COIN",
    # cache
    "TTLCache",
    "cache_key",
    # exceptions
    "QuoteError",
    "SymbolError",
    "SymbolNotFoundError",
    "ProviderError",
    "ProviderUnavailableError",
    "AllProvidersFailedError",
    "__version__",
]
