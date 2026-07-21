"""Chinese name <-> symbol resolution.

Two bots each carried their own hand-maintained table; merging them produced
zero conflicting entries, only different coverage. This module holds the
union and the lookup rules.

The fuzzy matching here is deliberately timid, because the permissive
version shipped a real bug: matching on any substring in either direction
and taking the first hit meant querying the single character "金" returned
Fubon Financial (2881) -- a stock the user never asked about, with nothing
in the reply to signal the guess. The rules below are the fix:

* at least two characters before fuzzy matching is attempted at all
* prefix matches only, never "contains"
* more than one candidate means ambiguous, so return nothing

Refusing to answer is the correct behaviour when the alternative is
confidently returning the wrong instrument.
"""

from __future__ import annotations

import threading

from ._names_data import ALIASES, CN_NAMES, TW_NAMES, US_NAMES
from .markets import CN_STOCK, TW_STOCK, US_STOCK, clean, normalize_stock

#: Fewer characters than this and a fuzzy lookup is refused outright.
MIN_FUZZY_LENGTH = 2

_lock = threading.Lock()

# Mutable copies so register() can extend the bundled data.
_NAMES: dict[str, dict[str, str]] = {
    TW_STOCK: dict(TW_NAMES),
    US_STOCK: dict(US_NAMES),
    CN_STOCK: dict(CN_NAMES),
}
_ALIASES: dict[str, str] = dict(ALIASES)

#: name -> code, rebuilt whenever the tables change.
_REVERSE: dict[str, str] = {}


def _rebuild_reverse() -> None:
    reverse: dict[str, str] = {}
    # Later markets do not clobber earlier ones on a name collision.
    for market in (TW_STOCK, US_STOCK, CN_STOCK):
        for code, name in _NAMES[market].items():
            reverse.setdefault(name, code)
    global _REVERSE
    _REVERSE = reverse


_rebuild_reverse()


def get_name(code: str, market: str | None = None) -> str | None:
    """Chinese name for a listing code, or ``None`` if unknown.

    The code may carry an exchange suffix; ``2330.TW`` and ``2330`` both
    resolve.
    """
    cleaned = clean(code)
    bare = cleaned.split(".")[0]
    markets = (market,) if market else (TW_STOCK, US_STOCK, CN_STOCK)
    for candidate_market in markets:
        table = _NAMES.get(candidate_market or "")
        if table:
            name = table.get(bare) or table.get(cleaned)
            if name:
                return name
    return None


def get_code(name: str) -> str | None:
    """Listing code for a Chinese name, or ``None`` when unknown or ambiguous.

    Exact matches win. Failing that, a prefix match is attempted, but only
    for inputs of at least :data:`MIN_FUZZY_LENGTH` characters and only when
    exactly one candidate matches.
    """
    if not isinstance(name, str):
        return None
    query = name.strip()
    if not query:
        return None

    exact = _REVERSE.get(query)
    if exact is not None:
        return exact

    if len(query) < MIN_FUZZY_LENGTH:
        return None

    matches = {
        code
        for known, code in _REVERSE.items()
        if known.startswith(query) or query.startswith(known)
    }
    if len(matches) == 1:
        return next(iter(matches))
    return None


def resolve(query: str) -> str | None:
    """Turn user input into a symbol you can pass to ``get_quote``.

    Accepts a listing code, a Chinese name, or an English alias (including
    the misspellings the Discord bot had collected, such as ``nvida``).
    Returns a normalized symbol, or ``None`` if nothing matched confidently.

        >>> resolve("台積電")
        '2330.TW'
        >>> resolve("2330")
        '2330.TW'
        >>> resolve("nvidia")
        'NVDA'
        >>> resolve("金") is None    # ambiguous -- refuses to guess
        True
    """
    if not isinstance(query, str) or not query.strip():
        return None
    raw = query.strip()

    alias = _ALIASES.get(raw.lower())
    if alias is not None:
        return normalize_stock(alias)

    code = get_code(raw)
    if code is not None:
        return normalize_stock(code)

    # Fall through to treating the input as a symbol. The ASCII test is load
    # bearing: str.isalnum() is true for CJK characters, so without it an
    # unmatched Chinese query like "金" would be forwarded to a provider as
    # if it were a ticker instead of being refused here.
    upper = raw.upper()
    candidate = upper.replace(".", "").replace("-", "")
    if candidate and candidate.isascii() and candidate.isalnum():
        return normalize_stock(upper)
    return None


def search(keyword: str, limit: int = 5) -> list[tuple[str, str]]:
    """Find listings whose name or code contains ``keyword``.

    Unlike :func:`get_code` this is allowed to be generous: the caller gets
    every candidate and picks, so there is no risk of silently substituting
    one instrument for another.
    """
    if not isinstance(keyword, str) or not keyword.strip():
        return []
    query = keyword.strip()
    query_upper = query.upper()

    hits: list[tuple[str, str]] = []
    for market in (TW_STOCK, US_STOCK, CN_STOCK):
        for code, name in _NAMES[market].items():
            if query in name or query_upper in code:
                hits.append((code, name))
                if len(hits) >= limit:
                    return hits
    return hits


def register(
    mapping: dict[str, str],
    market: str = TW_STOCK,
    *,
    aliases: dict[str, str] | None = None,
) -> None:
    """Add or override name entries.

    Call during startup; lookups are read-heavy and this rebuilds an index.

        register({"1234": "範例公司"}, market=TW_STOCK)
    """
    if market not in _NAMES:
        raise ValueError(f"unknown market {market!r}; expected one of {sorted(_NAMES)}")
    with _lock:
        for code, name in mapping.items():
            _NAMES[market][str(code).strip().upper()] = str(name).strip()
        if aliases:
            for alias, code in aliases.items():
                _ALIASES[str(alias).strip().lower()] = str(code).strip().upper()
        _rebuild_reverse()


def known_codes(market: str | None = None) -> list[str]:
    """Every code the bundled tables know about."""
    if market:
        return sorted(_NAMES.get(market, {}))
    return sorted({code for table in _NAMES.values() for code in table})


def stats() -> dict[str, int]:
    """Entry counts, useful on a health endpoint."""
    return {
        "tw": len(_NAMES[TW_STOCK]),
        "us": len(_NAMES[US_STOCK]),
        "cn": len(_NAMES[CN_STOCK]),
        "aliases": len(_ALIASES),
    }


__all__ = [
    "MIN_FUZZY_LENGTH",
    "get_name",
    "get_code",
    "resolve",
    "search",
    "register",
    "known_codes",
    "stats",
]
