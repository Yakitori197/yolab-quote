"""The provider contract.

Two deliberate departures from the abstraction this was extracted from:

*Synchronous.* The original exposed ``async def get_quote`` only, which made
it unusable from the synchronous Flask applications that needed it most.
Here the contract is sync and :mod:`yolab_quote.aio` wraps it for async
callers -- the cheap direction. The reverse requires an event loop.

*Historical bars are part of the contract.* The original kept them in a
separate module with hardcoded if/elif routing that imported back into the
provider package, so bars could never participate in fallback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from types import TracebackType

from ..exceptions import ProviderError, ProviderUnavailableError
from ..models import Bar, ProviderHealth, Quote


class Provider(ABC):
    """Base class for every market data source.

    Contract:

    1. :meth:`get_quote` returns a :class:`~yolab_quote.models.Quote` or
       raises. Never return ``None`` -- the caller cannot distinguish a
       missing symbol from a network failure that way.
    2. Configuration problems (no API key, SDK not installed) raise
       :class:`~yolab_quote.exceptions.ProviderUnavailableError`, which tells
       the manager to fall through rather than retry.
    3. Constructing a provider must never fail because of missing
       configuration. A missing key is reported by :meth:`health`, not by an
       exception at import or construction time -- otherwise one
       misconfigured provider takes down the whole process at startup.
    """

    #: Stable identifier. Appears in settings and in ``Quote.source``.
    name: str = "unknown"

    #: Markets this provider can actually serve. Enforced by the manager --
    #: the original declared this attribute but never checked it, so a
    #: misconfigured priority list would happily return a crypto quote in
    #: answer to a US stock query.
    markets: tuple[str, ...] = ()

    # ------------------------------------------------------------------ #
    # Required
    # ------------------------------------------------------------------ #
    @abstractmethod
    def get_quote(self, symbol: str) -> Quote:
        """Fetch a single quote.

        Raises:
            SymbolNotFoundError: the provider works but has no such symbol.
            ProviderUnavailableError: the provider is not configured.
            ProviderError: anything else (network, parse, upstream error).
        """

    @abstractmethod
    def health(self) -> ProviderHealth:
        """Report whether this provider is usable.

        Must not raise and must not perform a network call -- callers use
        this on health endpoints where a hang is worse than a stale answer.
        """

    # ------------------------------------------------------------------ #
    # Optional -- override when the upstream API supports it natively
    # ------------------------------------------------------------------ #
    def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        """Fetch many quotes, returning only the ones that succeeded.

        Keys are the caller's **original** symbols, never the provider's
        normalized form. That is not cosmetic: the original implementation
        wrote its batch cache under the normalized symbol but read it under
        the raw one, so batch cache entries could never be hit, and it
        recovered by prefix-matching -- which happily matched a query for
        ``BTC`` to ``BTCUSDT``.

        Individual failures are omitted rather than raised; compare the
        returned keys against the input to find them. Override this when the
        upstream API has a genuine bulk endpoint.
        """
        results: dict[str, Quote] = {}
        for symbol in symbols:
            try:
                results[symbol] = self.get_quote(symbol)
            except ProviderError:
                continue
            except Exception:  # noqa: BLE001 - one bad symbol must not abort the batch
                continue
        return results

    def get_bars(self, symbol: str, days: int = 30) -> list[Bar]:
        """Fetch recent daily candles, oldest first."""
        raise ProviderUnavailableError(f"{self.name} does not provide historical bars")

    def supports(self, market: str) -> bool:
        """True if this provider serves ``market``."""
        return market in self.markets

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:  # noqa: B027 - deliberately optional, not abstract
        """Release held resources. Idempotent.

        Intentionally a concrete no-op rather than an abstract method: most
        providers hold nothing to release, and forcing every implementation
        to write an empty ``close`` buys nothing.


        The original created a fresh HTTP client per request in five
        different places, so connections were never pooled and there was no
        way to shut one down.
        """

    def __enter__(self) -> Provider:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} markets={self.markets!r}>"
