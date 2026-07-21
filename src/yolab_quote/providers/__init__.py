"""Provider registry.

Lazy by design: importing this package must not pull in ``yfinance``,
``bs4``, or anything else heavy. Entries hold module paths and are resolved
only when a provider is actually built.

Unlike the private hardcoded dict this replaces, third parties can register
their own providers through :func:`register`, and :func:`create` forwards
keyword arguments -- the original called its factory with no arguments at
all, so any provider built through the fallback chain could never receive an
API key or a timeout.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module

from ..exceptions import ProviderError
from .base import Provider

#: name -> (module path, class name). Resolved on first use.
_LAZY: dict[str, tuple[str, str]] = {
    "yfinance": (".yfinance_provider", "YFinanceProvider"),
}

#: name -> callable returning a Provider. Populated by register().
_FACTORIES: dict[str, Callable[..., Provider]] = {}


def register(name: str, factory: Callable[..., Provider]) -> None:
    """Register a provider factory under ``name``, replacing any existing one."""
    if not name or not isinstance(name, str):
        raise ValueError("provider name must be a non-empty string")
    if not callable(factory):
        raise TypeError("factory must be callable")
    _FACTORIES[name] = factory


def unregister(name: str) -> None:
    """Remove a registered factory. Silent if it was never registered."""
    _FACTORIES.pop(name, None)


def available() -> list[str]:
    """Every provider name that can be built, sorted."""
    return sorted(set(_LAZY) | set(_FACTORIES))


def create(name: str, **kwargs: object) -> Provider:
    """Build a provider by name.

    Keyword arguments reach the provider's constructor, so per-provider
    settings (api_key, timeout, base_url) work through the fallback chain.
    """
    factory = _FACTORIES.get(name)
    if factory is not None:
        instance = factory(**kwargs)
    else:
        entry = _LAZY.get(name)
        if entry is None:
            raise ProviderError(f"unknown provider {name!r} (available: {', '.join(available())})")
        module_path, class_name = entry
        module = import_module(module_path, package=__name__)
        provider_cls = getattr(module, class_name)
        instance = provider_cls(**kwargs)

    # Checked on both paths: a registered factory returning the wrong type
    # would otherwise slip into the fallback chain and fail there, where the
    # error is swallowed and the provider just looks mysteriously dead.
    if not isinstance(instance, Provider):
        raise ProviderError(
            f"{name!r} produced {type(instance).__name__}, not a Provider instance"
        )
    return instance


__all__ = ["Provider", "available", "create", "register", "unregister"]
