"""The README is a contract: everything it tells a user to type must exist.

Two real drifts motivated this file. The README recommended
``YOLAB_QUOTE_TW_STOCK_PROVIDERS=yfinance,yahoo_scraper`` while the registry
only knows ``yahoo`` -- copying it silently removed the fallback. And it
showed a one-line crypto quote although no built-in provider serves crypto.
"""

import ast
import re
from pathlib import Path

import pytest

import yolab_quote as yq
from yolab_quote import markets, providers
from yolab_quote.client import DEFAULT_PRIORITY, ENV_PREFIX, QuoteClient
from yolab_quote.exceptions import AllProvidersFailedError

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
PYPROJECT = (ROOT / "pyproject.toml").read_text(encoding="utf-8")


def code_blocks(lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", README, flags=re.DOTALL)


def pyproject_extras() -> set[str]:
    section = PYPROJECT.split("[project.optional-dependencies]", 1)[1].split("\n[", 1)[0]
    return set(re.findall(r"^(\w[\w-]*)\s*=", section, flags=re.MULTILINE))


class TestPythonExamples:
    @pytest.mark.parametrize("block", code_blocks("python"))
    def test_every_python_block_parses(self, block):
        # The async example uses top-level await, as a REPL or notebook would.
        compile(block, "README.md", "exec", flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

    def test_every_yq_attribute_exists(self):
        names = set(re.findall(r"\byq\.(\w+)", README))
        assert names, "expected the README to use the yq alias"
        missing = sorted(n for n in names if not hasattr(yq, n))
        assert missing == []

    def test_every_aio_attribute_exists(self):
        import yolab_quote.aio as aq

        missing = sorted(n for n in set(re.findall(r"\baq\.(\w+)", README)) if not hasattr(aq, n))
        assert missing == []

    def test_every_from_import_exists(self):
        for match in re.finditer(r"from yolab_quote import ([^\n]+)", README):
            for name in (part.strip() for part in match.group(1).split(",")):
                assert hasattr(yq, name), f"README imports {name!r}, which does not exist"

    def test_custom_provider_example_runs(self):
        """The registration example is offline, so it can run verbatim."""
        block = next(b for b in code_blocks("python") if "class MyProvider" in b)
        namespace: dict[str, object] = {}
        try:
            exec(block, namespace)  # noqa: S102 - our own README
            client = QuoteClient(priority={markets.TW_STOCK: ["mine"]}, ttl=0)
            quote = client.get_quote("2330")
            assert quote.source == "mine"
            assert quote.price == 100.0
        finally:
            providers.unregister("mine")


class TestProviderNames:
    def test_env_examples_name_real_markets_and_providers(self):
        lines = re.findall(rf"^{ENV_PREFIX}(\w+)_PROVIDERS=(.+)$", README, flags=re.MULTILINE)
        assert lines, "expected at least one environment example"
        for market, names in lines:
            assert market.lower() in markets.ALL_MARKETS, market
            for name in names.split(","):
                assert name.strip() in providers.available(), (
                    f"README suggests provider {name.strip()!r}; available: {providers.available()}"
                )

    def test_default_chain_names_are_all_buildable(self):
        for market, names in DEFAULT_PRIORITY.items():
            for name in names:
                assert name in providers.available(), (market, name)

    def test_unknown_env_provider_reports_why(self, monkeypatch):
        """The exact mistake the README used to make must be diagnosable."""
        monkeypatch.setenv(f"{ENV_PREFIX}TW_STOCK_PROVIDERS", "yahoo_scraper")
        with pytest.raises(AllProvidersFailedError) as excinfo:
            QuoteClient(ttl=0).get_quote("2330")
        assert "unknown provider 'yahoo_scraper'" in excinfo.value.failures["yahoo_scraper"]


class TestCryptoClaim:
    def test_no_builtin_provider_serves_crypto(self):
        """If one is added, update the README's Crypto section and this test."""
        crypto = (markets.CRYPTO_SPOT, markets.CRYPTO_FUTURES, markets.CRYPTO_FUTURES_COIN)
        for market in crypto:
            assert DEFAULT_PRIORITY[market] == ()
        assert "no built-in provider" in README.lower()

    def test_crypto_query_raises_as_documented(self):
        with pytest.raises(AllProvidersFailedError) as excinfo:
            QuoteClient(ttl=0).get_quote("BTCUSDT", markets.CRYPTO_SPOT)
        assert "no providers configured for crypto_spot" in str(excinfo.value.failures)


class TestInstallInstructions:
    def test_extras_mentioned_exist(self):
        mentioned = set(re.findall(r"yolab-quote\[([\w,-]+)\]", README))
        mentioned |= set(re.findall(r"^\| `\[(\w+)\]`", README, flags=re.MULTILINE))
        extras = pyproject_extras()
        for group in mentioned:
            for extra in group.split(","):
                assert extra in extras, f"README mentions extra {extra!r}; pyproject has {extras}"

    def test_install_pins_the_current_release_tag(self):
        tags = set(re.findall(r"yolab-quote\.git@([\w.]+)", README))
        assert tags == {f"v{yq.__version__}"}, (
            "README install lines must pin the current release tag, never a branch"
        )

    def test_version_matches_pyproject(self):
        """RELEASING.md: the version lives in two places and they must match."""
        match = re.search(r'^version = "([^"]+)"', PYPROJECT, flags=re.MULTILINE)
        assert match is not None
        assert match.group(1) == yq.__version__
