"""yfinance-backed provider for equities.

Written against the yfinance **0.2.x** API (``Ticker.info`` dict keys,
``Ticker.history`` DataFrame). 1.x is a breaking rewrite, which is why
``pyproject.toml`` pins ``yfinance>=0.2.36,<0.3`` rather than leaving the
upper bound open. The project this was extracted from installed yfinance
bare in its Dockerfile with no bound at all.

The import is lazy in both directions: importing this module does not import
yfinance, and :func:`yfinance_available` reports installation status without
triggering an import.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from ..exceptions import ProviderError, ProviderUnavailableError, SymbolNotFoundError
from ..markets import (
    CN_STOCK,
    DE_STOCK,
    HK_STOCK,
    JP_STOCK,
    KR_STOCK,
    TW_STOCK,
    UK_STOCK,
    US_STOCK,
    currency_for,
    detect_market,
    normalize_stock,
)
from ..models import Bar, ProviderHealth, Quote, to_float
from .base import Provider

_SOURCE = "yfinance"


def yfinance_available() -> bool:
    """True if yfinance is importable, without actually importing it."""
    return importlib.util.find_spec("yfinance") is not None


def _require_yfinance() -> Any:
    try:
        import yfinance  # noqa: PLC0415 - intentionally deferred
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ProviderUnavailableError(
            "yfinance is not installed; install it with: pip install yolab-quote[yfinance]"
        ) from exc
    return yfinance


def _first(info: dict[str, Any], *keys: str) -> Any:
    """First present, non-None value among ``keys``."""
    for key in keys:
        value = info.get(key)
        if value is not None:
            return value
    return None


def info_to_quote(symbol: str, yahoo_symbol: str, info: dict[str, Any]) -> Quote:
    """Map a yfinance ``Ticker.info`` dict onto a :class:`Quote`.

    Kept as a module-level pure function so the field mapping -- the part
    that actually breaks when yfinance changes -- can be tested without a
    network call or a mocked SDK.

    Raises:
        SymbolNotFoundError: when the payload carries no usable price. For an
            unknown ticker yfinance returns a near-empty dict rather than an
            error, so an absent price is the only reliable signal.
    """
    price = to_float(_first(info, "currentPrice", "regularMarketPrice", "lastPrice"))
    previous_close = to_float(_first(info, "previousClose", "regularMarketPreviousClose"))

    if price is None and previous_close is None:
        raise SymbolNotFoundError(f"no price data for {symbol!r} (resolved to {yahoo_symbol!r})")

    market = detect_market(yahoo_symbol)

    extra: dict[str, Any] = {}
    pe_ratio = to_float(_first(info, "trailingPE", "forwardPE"))
    if pe_ratio is not None:
        extra["pe_ratio"] = pe_ratio
    market_cap = to_float(info.get("marketCap"))
    if market_cap is not None:
        extra["market_cap"] = market_cap
    # yfinance 0.2.x reports this as a ratio (0.0234 == 2.34%). The pin on
    # <0.3 is what makes this conversion safe to hardcode.
    dividend_yield = to_float(info.get("dividendYield"))
    if dividend_yield is not None:
        extra["dividend_yield"] = dividend_yield * 100.0
    for key, field in (("sector", "sector"), ("industry", "industry")):
        value = info.get(key)
        if value:
            extra[field] = value

    return Quote.create(
        symbol=yahoo_symbol,
        market=market,
        source=_SOURCE,
        name=_first(info, "longName", "shortName") or None,
        price=price,
        previous_close=previous_close,
        open=_first(info, "open", "regularMarketOpen"),
        high=_first(info, "dayHigh", "regularMarketDayHigh"),
        low=_first(info, "dayLow", "regularMarketDayLow"),
        volume=_first(info, "volume", "regularMarketVolume"),
        bid=info.get("bid"),
        ask=info.get("ask"),
        currency=info.get("currency") or currency_for(market),
        is_delayed=True,
        extra=extra,
        raw=dict(info),
    )


def period_for_days(days: int) -> str:
    """Smallest yfinance period string that covers ``days`` calendar days."""
    if days <= 5:
        return "5d"
    if days <= 30:
        return "1mo"
    if days <= 90:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 365:
        return "1y"
    return "2y"


class YFinanceProvider(Provider):
    """Delayed equity quotes via yfinance."""

    name = _SOURCE
    markets = (TW_STOCK, US_STOCK, HK_STOCK, JP_STOCK, CN_STOCK, UK_STOCK, DE_STOCK, KR_STOCK)

    def __init__(self, *, timeout: float | None = None) -> None:
        # Accepted for interface symmetry; yfinance manages its own session.
        self.timeout = timeout

    # -- seams: overridden in tests so no network or SDK is needed --------- #
    def _fetch_info(self, yahoo_symbol: str) -> dict[str, Any]:
        yf = _require_yfinance()
        try:
            info = yf.Ticker(yahoo_symbol).info
        except Exception as exc:  # noqa: BLE001 - yfinance raises many unrelated types
            raise ProviderError(f"yfinance lookup failed for {yahoo_symbol!r}: {exc}") from exc
        return dict(info or {})

    def _fetch_history(self, yahoo_symbol: str, period: str) -> Any:
        yf = _require_yfinance()
        try:
            return yf.Ticker(yahoo_symbol).history(period=period)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"yfinance history failed for {yahoo_symbol!r}: {exc}") from exc

    # -- contract ---------------------------------------------------------- #
    def get_quote(self, symbol: str) -> Quote:
        yahoo_symbol = normalize_stock(symbol)
        return info_to_quote(symbol, yahoo_symbol, self._fetch_info(yahoo_symbol))

    def get_bars(self, symbol: str, days: int = 30) -> list[Bar]:
        if days <= 0:
            raise ValueError("days must be positive")
        yahoo_symbol = normalize_stock(symbol)
        frame = self._fetch_history(yahoo_symbol, period_for_days(days))

        if frame is None or len(frame) == 0:
            raise SymbolNotFoundError(f"no history for {symbol!r} (resolved to {yahoo_symbol!r})")

        bars: list[Bar] = []
        for index, row in frame.iterrows():
            close = to_float(row.get("Close"))
            open_ = to_float(row.get("Open"))
            high = to_float(row.get("High"))
            low = to_float(row.get("Low"))
            if None in (open_, high, low, close):
                continue  # holiday / halted rows arrive as NaN
            bars.append(
                Bar(
                    date=index.strftime("%Y-%m-%d"),
                    open=open_,  # type: ignore[arg-type]
                    high=high,  # type: ignore[arg-type]
                    low=low,  # type: ignore[arg-type]
                    close=close,  # type: ignore[arg-type]
                    volume=to_float(row.get("Volume")),
                )
            )
        if not bars:
            raise SymbolNotFoundError(f"history for {symbol!r} contained no usable rows")
        return bars[-days:]

    def get_quotes(self, symbols: Sequence[str]) -> dict[str, Quote]:
        # yfinance has no batch endpoint worth the complexity here; the
        # concurrency that actually matters lives in the manager.
        return super().get_quotes(symbols)

    def health(self) -> ProviderHealth:
        installed = yfinance_available()
        return ProviderHealth(
            provider=self.name,
            ok=installed,
            status="ready" if installed else "unavailable",
            detail=None if installed else "yfinance is not installed",
            markets=self.markets,
        )


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
