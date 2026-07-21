"""Yahoo Finance chart API provider.

A structured-JSON alternative to yfinance that shares no code path with it,
which is what makes it a genuine fallback rather than a second way to fail.

The implementation this package replaces used an HTML scraper for this role
-- 330 lines of parsers against a page layout its own docstring warned could
"break at any time". The public chart endpoint returns the same numbers as
JSON, needs no API key, and costs no extra dependency since httpx is already
required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from ..exceptions import ProviderError, SymbolNotFoundError
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

_SOURCE = "yahoo"

BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Yahoo rejects requests without a browser-like agent.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

DEFAULT_TIMEOUT = 15.0


def range_for_days(days: int) -> str:
    """Smallest chart ``range`` yielding at least ``days`` *trading* sessions.

    Callers ask for a number of daily bars, not a span of calendar days, and
    a year holds only ~252 sessions. Mapping 120 bars onto "6mo" (~126
    calendar-derived sessions, fewer after holidays) came up short, which
    silently produced an empty MA120 downstream. Each bucket is sized with
    headroom so the requested count is actually available.
    """
    if days <= 15:
        return "1mo"
    if days <= 40:
        return "3mo"
    if days <= 80:
        return "6mo"
    if days <= 170:
        return "1y"
    if days <= 340:
        return "2y"
    return "5y"


def _result(payload: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Pull the single chart result out of a response, or explain why not."""
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise ProviderError(f"unexpected chart payload for {symbol!r}")

    error = chart.get("error")
    if error:
        code = error.get("code") if isinstance(error, dict) else None
        description = error.get("description") if isinstance(error, dict) else str(error)
        if code in {"Not Found", "None"} or "No data found" in str(description):
            raise SymbolNotFoundError(f"Yahoo has no data for {symbol!r}: {description}")
        raise ProviderError(f"Yahoo error for {symbol!r}: {description}")

    results = chart.get("result")
    if not results:
        raise SymbolNotFoundError(f"Yahoo returned no result for {symbol!r}")
    first = results[0]
    if not isinstance(first, dict):
        raise ProviderError(f"unexpected result shape for {symbol!r}")
    return first


def _at(series: Any, index: int) -> float | None:
    """Value at ``index`` of an indicator series, or ``None`` if absent.

    Module level rather than a closure inside the loop: capturing the loop
    variable would rebind on every iteration, which is correct only as long
    as the function is called immediately and silently wrong the moment it
    is not.
    """
    if not isinstance(series, list) or index >= len(series):
        return None
    return to_float(series[index])


def _last_present(values: Any) -> Any:
    """Last non-None entry of an indicator series, if it is a list."""
    if not isinstance(values, list):
        return None
    for value in reversed(values):
        if value is not None:
            return value
    return None


def parse_chart_quote(symbol: str, payload: dict[str, Any]) -> Quote:
    """Map a chart response onto a :class:`Quote`.

    A module-level pure function so the field mapping -- the part that breaks
    when the upstream shape changes -- is testable without a network call.
    """
    result = _result(payload, symbol)
    meta = result.get("meta")
    if not isinstance(meta, dict):
        raise ProviderError(f"chart response for {symbol!r} carries no meta block")

    yahoo_symbol = meta.get("symbol") or normalize_stock(symbol)
    price = to_float(meta.get("regularMarketPrice"))
    # Yahoo names this differently from every other endpoint.
    previous_close = to_float(meta.get("chartPreviousClose") or meta.get("previousClose"))

    if price is None and previous_close is None:
        raise SymbolNotFoundError(f"no price in Yahoo chart response for {symbol!r}")

    # `open` is not in meta; it lives in the indicator series.
    open_price = None
    indicators = result.get("indicators")
    if isinstance(indicators, dict):
        quote_series = indicators.get("quote")
        if isinstance(quote_series, list) and quote_series and isinstance(quote_series[0], dict):
            open_price = to_float(_last_present(quote_series[0].get("open")))

    market = detect_market(str(yahoo_symbol))
    extra: dict[str, Any] = {}
    for source_key, target_key in (
        ("fiftyTwoWeekHigh", "fifty_two_week_high"),
        ("fiftyTwoWeekLow", "fifty_two_week_low"),
        ("exchangeName", "exchange"),
        ("instrumentType", "instrument_type"),
    ):
        value = meta.get(source_key)
        if value is not None:
            numeric = to_float(value)
            extra[target_key] = numeric if numeric is not None else value

    timestamp = to_float(meta.get("regularMarketTime"))
    updated_at = (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        if timestamp is not None
        else datetime.now(timezone.utc)
    )

    return Quote.create(
        symbol=str(yahoo_symbol),
        market=market,
        source=_SOURCE,
        updated_at=updated_at,
        name=meta.get("longName") or meta.get("shortName") or None,
        price=price,
        previous_close=previous_close,
        open=open_price,
        high=meta.get("regularMarketDayHigh"),
        low=meta.get("regularMarketDayLow"),
        volume=meta.get("regularMarketVolume"),
        currency=meta.get("currency") or currency_for(market),
        is_delayed=True,
        extra=extra,
        raw=dict(meta),
    )


def parse_chart_bars(symbol: str, payload: dict[str, Any]) -> list[Bar]:
    """Map a chart response onto daily candles, oldest first."""
    result = _result(payload, symbol)
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")

    if not isinstance(timestamps, list) or not timestamps:
        raise SymbolNotFoundError(f"Yahoo returned no candles for {symbol!r}")
    if not isinstance(indicators, dict):
        raise ProviderError(f"chart response for {symbol!r} carries no indicators")

    series = indicators.get("quote")
    if not isinstance(series, list) or not series or not isinstance(series[0], dict):
        raise ProviderError(f"chart response for {symbol!r} carries no quote series")
    quote_block = series[0]

    opens = quote_block.get("open") or []
    highs = quote_block.get("high") or []
    lows = quote_block.get("low") or []
    closes = quote_block.get("close") or []
    volumes = quote_block.get("volume") or []

    bars: list[Bar] = []
    for index, epoch in enumerate(timestamps):
        epoch_value = to_float(epoch)
        if epoch_value is None:
            continue

        open_ = _at(opens, index)
        high = _at(highs, index)
        low = _at(lows, index)
        close = _at(closes, index)
        if None in (open_, high, low, close):
            continue  # halted or non-trading sessions come back as null

        bars.append(
            Bar(
                date=datetime.fromtimestamp(epoch_value, tz=timezone.utc).strftime("%Y-%m-%d"),
                open=open_,  # type: ignore[arg-type]
                high=high,  # type: ignore[arg-type]
                low=low,  # type: ignore[arg-type]
                close=close,  # type: ignore[arg-type]
                volume=_at(volumes, index),
            )
        )

    if not bars:
        raise SymbolNotFoundError(f"Yahoo candles for {symbol!r} contained no usable rows")
    return bars


class YahooProvider(Provider):
    """Delayed equity quotes from Yahoo's public chart endpoint."""

    name = _SOURCE
    markets = (TW_STOCK, US_STOCK, HK_STOCK, JP_STOCK, CN_STOCK, UK_STOCK, DE_STOCK, KR_STOCK)

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT, client: httpx.Client | None = None) -> None:
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    def _http(self) -> httpx.Client:
        """The pooled client, created on first use.

        The original opened a fresh connection per request in five separate
        places, so nothing was ever pooled and nothing could be shut down.
        """
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, headers=DEFAULT_HEADERS)
        return self._client

    # -- seam: overridden in tests so no network is needed ------------------ #
    def _fetch(self, yahoo_symbol: str, *, chart_range: str, interval: str) -> dict[str, Any]:
        url = BASE_URL.format(symbol=yahoo_symbol)
        try:
            response = self._http().get(url, params={"range": chart_range, "interval": interval})
        except httpx.HTTPError as exc:
            raise ProviderError(f"Yahoo request failed for {yahoo_symbol!r}: {exc}") from exc

        if response.status_code == 404:
            raise SymbolNotFoundError(f"Yahoo has no such symbol: {yahoo_symbol!r}")
        if response.status_code >= 400:
            raise ProviderError(
                f"Yahoo returned HTTP {response.status_code} for {yahoo_symbol!r}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(f"Yahoo returned non-JSON for {yahoo_symbol!r}") from exc
        if not isinstance(payload, dict):
            raise ProviderError(f"Yahoo returned unexpected JSON for {yahoo_symbol!r}")
        return payload

    # -- contract ----------------------------------------------------------- #
    def get_quote(self, symbol: str) -> Quote:
        yahoo_symbol = normalize_stock(symbol)
        payload = self._fetch(yahoo_symbol, chart_range="1d", interval="1d")
        return parse_chart_quote(symbol, payload)

    def get_bars(self, symbol: str, days: int = 30) -> list[Bar]:
        if days <= 0:
            raise ValueError("days must be positive")
        yahoo_symbol = normalize_stock(symbol)
        payload = self._fetch(yahoo_symbol, chart_range=range_for_days(days), interval="1d")
        return parse_chart_bars(symbol, payload)[-days:]

    def health(self) -> ProviderHealth:
        # No network call: a health endpoint that hangs is worse than a stale
        # answer. This provider needs no key, so it is ready by construction.
        return ProviderHealth(
            provider=self.name, ok=True, status="ready", markets=self.markets
        )

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
        self._client = None
