"""Market identifiers and symbol normalization.

Each of the three bots this package replaces had its own rule for deciding
"is this a Taiwan stock code?", and two of them were wrong:

    Stock_LineBot     code.isdigit()        -> fails on 00631L
    Discord_StockBot  ^\\d{4,6}$             -> fails on 00631L
    stockmap          ^\\d{4,6}[A-Z]{0,2}$   -> correct

Taiwan leveraged/inverse ETFs carry a trailing letter (00631L, 00632R), so
the digits-only rules silently treated them as US symbols and looked up the
wrong instrument. This module is the single source of truth.
"""

from __future__ import annotations

import re

# --------------------------------------------------------------------------- #
# Market identifiers
# --------------------------------------------------------------------------- #
TW_STOCK = "tw_stock"
US_STOCK = "us_stock"
HK_STOCK = "hk_stock"
JP_STOCK = "jp_stock"
CN_STOCK = "cn_stock"
UK_STOCK = "uk_stock"
DE_STOCK = "de_stock"
KR_STOCK = "kr_stock"
CRYPTO_SPOT = "crypto_spot"
CRYPTO_FUTURES = "crypto_futures"
CRYPTO_FUTURES_COIN = "crypto_futures_coin"

#: Every market this package knows how to route.
ALL_MARKETS: tuple[str, ...] = (
    TW_STOCK,
    US_STOCK,
    HK_STOCK,
    JP_STOCK,
    CN_STOCK,
    UK_STOCK,
    DE_STOCK,
    KR_STOCK,
    CRYPTO_SPOT,
    CRYPTO_FUTURES,
    CRYPTO_FUTURES_COIN,
)

#: Markets that hold equities (as opposed to crypto).
STOCK_MARKETS: tuple[str, ...] = (
    TW_STOCK,
    US_STOCK,
    HK_STOCK,
    JP_STOCK,
    CN_STOCK,
    UK_STOCK,
    DE_STOCK,
    KR_STOCK,
)

# --------------------------------------------------------------------------- #
# Symbol patterns
# --------------------------------------------------------------------------- #
#: A bare Taiwan listing code: 4-6 digits plus an optional 1-2 letter suffix
#: (00631L = leveraged, 00632R = inverse). The trailing letters are the part
#: the other implementations got wrong.
TW_CODE_RE = re.compile(r"^\d{4,6}[A-Z]{0,2}$")

#: Yahoo-style exchange suffix -> (market, ISO currency).
_SUFFIX_TABLE: dict[str, tuple[str, str]] = {
    ".TW": (TW_STOCK, "TWD"),
    ".TWO": (TW_STOCK, "TWD"),
    ".HK": (HK_STOCK, "HKD"),
    ".T": (JP_STOCK, "JPY"),
    ".SS": (CN_STOCK, "CNY"),
    ".SZ": (CN_STOCK, "CNY"),
    ".L": (UK_STOCK, "GBP"),
    ".DE": (DE_STOCK, "EUR"),
    ".KS": (KR_STOCK, "KRW"),
    ".KQ": (KR_STOCK, "KRW"),
}

_CURRENCY_BY_MARKET: dict[str, str] = {
    TW_STOCK: "TWD",
    US_STOCK: "USD",
    HK_STOCK: "HKD",
    JP_STOCK: "JPY",
    CN_STOCK: "CNY",
    UK_STOCK: "GBP",
    DE_STOCK: "EUR",
    KR_STOCK: "KRW",
    CRYPTO_SPOT: "USDT",
    CRYPTO_FUTURES: "USDT",
    CRYPTO_FUTURES_COIN: "USD",
}


def clean(symbol: str) -> str:
    """Strip and upper-case a user-supplied symbol.

    Raises:
        SymbolError: if the symbol is empty or whitespace only.
    """
    from .exceptions import SymbolError

    if not isinstance(symbol, str):
        raise SymbolError(f"symbol must be a string, got {type(symbol).__name__}")
    cleaned = symbol.strip().upper()
    if not cleaned:
        raise SymbolError("symbol is empty")
    return cleaned


def is_tw_code(symbol: str) -> bool:
    """True if this is a bare Taiwan listing code such as ``2330`` or ``00631L``."""
    return TW_CODE_RE.match(clean(symbol)) is not None


def normalize_stock(symbol: str) -> str:
    """Return the Yahoo-style symbol for an equity.

    A bare Taiwan code gains a ``.TW`` suffix; anything that already carries a
    known exchange suffix is left alone; everything else is treated as US.

        >>> normalize_stock("2330")
        '2330.TW'
        >>> normalize_stock("00631L")
        '00631L.TW'
        >>> normalize_stock("6488.TWO")
        '6488.TWO'
        >>> normalize_stock("nvda")
        'NVDA'
    """
    cleaned = clean(symbol)
    if "." in cleaned:
        suffix = cleaned[cleaned.rindex(".") :]
        if suffix in _SUFFIX_TABLE:
            return cleaned
    if TW_CODE_RE.match(cleaned):
        return f"{cleaned}.TW"
    return cleaned


def detect_market(symbol: str) -> str:
    """Infer the market from an already-normalized equity symbol."""
    cleaned = clean(symbol)
    if "." in cleaned:
        suffix = cleaned[cleaned.rindex(".") :]
        entry = _SUFFIX_TABLE.get(suffix)
        if entry is not None:
            return entry[0]
    if TW_CODE_RE.match(cleaned):
        return TW_STOCK
    return US_STOCK


def currency_for(symbol_or_market: str) -> str:
    """Best-effort ISO currency for a symbol or a market identifier."""
    if symbol_or_market in _CURRENCY_BY_MARKET:
        return _CURRENCY_BY_MARKET[symbol_or_market]
    return _CURRENCY_BY_MARKET.get(detect_market(symbol_or_market), "USD")


def is_stock_market(market: str) -> bool:
    """True if ``market`` is an equity market rather than crypto."""
    return market in STOCK_MARKETS
