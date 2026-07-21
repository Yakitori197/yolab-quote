"""Exception hierarchy for yolab-quote.

Design note: a provider must never silently return ``None`` on failure. The
three bots this package was extracted from all did exactly that, which made
"this symbol does not exist" indistinguishable from "the network is down" --
callers had no way to tell a typo from an outage. Every failure path here
raises something specific.
"""

from __future__ import annotations

from collections.abc import Mapping


class QuoteError(Exception):
    """Base class for every error raised by this package."""


class SymbolError(QuoteError):
    """The symbol is malformed, empty, or cannot be routed to a market."""


class SymbolNotFoundError(QuoteError):
    """The provider answered, but has no data for this symbol.

    Distinct from :class:`ProviderError`: the provider worked fine, the
    symbol simply does not exist there. Callers usually want to show
    "no such stock" rather than "service unavailable".
    """


class ProviderError(QuoteError):
    """A provider failed to answer -- network, parse, or upstream error."""


class ProviderUnavailableError(ProviderError):
    """A provider cannot run at all: missing SDK or missing API key.

    Separate from :class:`ProviderError` because this is a *configuration*
    problem, not a transient one. Retrying will not help; falling back to
    another provider will.
    """


class AllProvidersFailedError(ProviderError):
    """Every provider in the fallback chain failed.

    Carries the per-provider reasons so callers can log *why* each one
    failed instead of a single opaque message.
    """

    def __init__(self, symbol: str, market: str, failures: Mapping[str, str]) -> None:
        self.symbol = symbol
        self.market = market
        self.failures: dict[str, str] = dict(failures)
        detail = " | ".join(f"{name}: {reason}" for name, reason in self.failures.items())
        super().__init__(f"all providers failed for {market}:{symbol} -> {detail}")
