"""Immutable data models.

These are plain dataclasses rather than pydantic models on purpose. The bots
this package serves have no pydantic dependency, and a quote library has no
business forcing a validation framework onto its callers. The only guarantee
that actually matters here is enforced by :func:`to_float`: every numeric
field is a real Python ``float``.

That guarantee is not theoretical. One of the bots passed ``numpy.float64``
values from a pandas DataFrame straight into its Discord embeds, so any
change in pandas' formatting behaviour would surface as broken output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def to_float(value: Any) -> float | None:
    """Coerce anything numeric to a real ``float``, or ``None``.

    Accepts ``numpy`` scalars, ``Decimal``, and numeric strings -- all of
    which turn up in provider payloads. Returns ``None`` for anything that
    is not a finite number, so callers never have to guard against ``NaN``
    leaking into arithmetic.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # NaN != NaN; inf breaks any downstream formatting.
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _derive_change(
    price: float | None,
    previous_close: float | None,
    change: float | None,
    change_percent: float | None,
) -> tuple[float | None, float | None]:
    """Fill in whichever of change / change_percent the provider omitted.

    Every provider is normalized against the same baseline -- the previous
    close -- because the three bots each picked a different one
    (``hist.iloc[-2]['Close']`` vs ``info['previousClose']``) and could
    report different daily changes for the same stock at the same moment.

    The ``previous_close`` truthiness test is the division guard; one of the
    bots divided without one and could raise ZeroDivisionError on a halted
    or newly listed symbol.
    """
    if change is None and price is not None and previous_close is not None:
        change = price - previous_close
    if change_percent is None and change is not None and previous_close:
        change_percent = (change / previous_close) * 100.0
    return change, change_percent


@dataclass(frozen=True)
class Quote:
    """A point-in-time snapshot of one instrument.

    Only :attr:`symbol`, :attr:`market`, :attr:`source` and
    :attr:`updated_at` are guaranteed present. Every price field is optional
    because real providers routinely omit them -- a thinly traded stock may
    have no bid, a halted one no price. Build instances with :meth:`create`
    rather than the constructor so the numeric coercion and the change
    derivation are applied consistently.
    """

    symbol: str
    market: str
    source: str
    updated_at: datetime

    name: str | None = None
    price: float | None = None
    previous_close: float | None = None
    change: float | None = None
    change_percent: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    currency: str | None = None

    #: False only when a provider genuinely streams real-time data.
    is_delayed: bool = True

    #: Market-specific fields that do not belong on every quote:
    #: ``pe_ratio``/``dividend_yield`` for equities, ``funding_rate``/
    #: ``mark_price`` for perpetuals. Keeping them out of the main body is
    #: what stops crypto vocabulary from leaking into the stock API.
    extra: dict[str, Any] = field(default_factory=dict)

    #: The provider's untouched payload, for debugging.
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        symbol: str,
        market: str,
        source: str,
        updated_at: datetime | None = None,
        name: str | None = None,
        price: Any = None,
        previous_close: Any = None,
        change: Any = None,
        change_percent: Any = None,
        open: Any = None,  # noqa: A002 - mirrors the OHLC field name
        high: Any = None,
        low: Any = None,
        volume: Any = None,
        bid: Any = None,
        ask: Any = None,
        currency: str | None = None,
        is_delayed: bool = True,
        extra: dict[str, Any] | None = None,
        raw: dict[str, Any] | None = None,
    ) -> Quote:
        """Build a quote, coercing every numeric field and deriving change."""
        price_f = to_float(price)
        prev_f = to_float(previous_close)
        change_f, change_pct_f = _derive_change(
            price_f, prev_f, to_float(change), to_float(change_percent)
        )
        return cls(
            symbol=symbol,
            market=market,
            source=source,
            updated_at=updated_at or datetime.now(timezone.utc),
            name=name,
            price=price_f,
            previous_close=prev_f,
            change=change_f,
            change_percent=change_pct_f,
            open=to_float(open),
            high=to_float(high),
            low=to_float(low),
            volume=to_float(volume),
            bid=to_float(bid),
            ask=to_float(ask),
            currency=currency,
            is_delayed=is_delayed,
            extra=dict(extra or {}),
            raw=dict(raw or {}),
        )

    @property
    def is_up(self) -> bool | None:
        """True/False if the instrument moved, ``None`` if change is unknown."""
        if self.change is None:
            return None
        return self.change >= 0

    def to_dict(self) -> dict[str, Any]:
        """Flat dict of the quote, excluding :attr:`raw`."""
        return {
            "symbol": self.symbol,
            "market": self.market,
            "source": self.source,
            "updated_at": self.updated_at.isoformat(),
            "name": self.name,
            "price": self.price,
            "previous_close": self.previous_close,
            "change": self.change,
            "change_percent": self.change_percent,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "volume": self.volume,
            "bid": self.bid,
            "ask": self.ask,
            "currency": self.currency,
            "is_delayed": self.is_delayed,
            **self.extra,
        }


@dataclass(frozen=True)
class Bar:
    """One OHLCV candle.

    ``date`` is an exchange-local trading day (``YYYY-MM-DD``) rather than a
    timestamp: daily bars are what every caller here actually asked for, and
    a date string sidesteps the timezone ambiguity of "which day is this
    candle" across markets.
    """

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def to_dict(self) -> dict[str, Any]:
        """Short-key form (``o``/``h``/``l``/``c``/``v``)."""
        return {
            "date": self.date,
            "o": self.open,
            "h": self.high,
            "l": self.low,
            "c": self.close,
            "v": self.volume,
        }


@dataclass(frozen=True)
class ProviderHealth:
    """A provider's self-report.

    Deliberately a typed structure: the abstraction this was extracted from
    returned a free-form dict whose keys differed between every provider, so
    no caller could inspect health generically.
    """

    provider: str
    ok: bool
    status: str
    detail: str | None = None
    markets: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "ok": self.ok,
            "status": self.status,
            "detail": self.detail,
            "markets": list(self.markets),
        }
