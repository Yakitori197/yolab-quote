# Changelog

All notable changes to this project. Versions follow [SemVer](https://semver.org).

## 0.1.1 — 2026-10-15

First tagged release. Consumers should pin `@v0.1.1` instead of `@main`.

### Fixed

- README recommended `YOLAB_QUOTE_TW_STOCK_PROVIDERS=yfinance,yahoo_scraper`,
  but no `yahoo_scraper` provider exists; copying it silently removed the
  fallback. The example now uses `yfinance,yahoo`.
- README showed `get_quote("BTCUSDT", CRYPTO_SPOT)` as if it worked out of the
  box. No built-in provider serves crypto; the README now says so and shows
  how to register one.
- README install instructions assumed a PyPI release that does not exist.
  They now install a pinned git tag, and note that slim Docker images need
  `git` for that.
- When a provider in the chain cannot be built, `AllProvidersFailedError.failures`
  (and `health()`) now give the reason, e.g. `unknown provider 'typo'`,
  instead of a bare "could not be constructed".

### Removed

- The `scraper` extra, which installed `beautifulsoup4` for a provider that
  does not exist. `[all]` now equals `[yfinance]`.

### Added

- GitHub Actions CI: pytest, mypy and ruff on Python 3.10–3.13 for Linux and
  Windows, a core-only (no yfinance) job, and a build + `twine check` job.
- `tests/test_readme.py`: checks the README's provider names, extras, public
  names, crypto claim and pinned tag against the code.

## 0.1.0

Initial version, consumed via `@main`.
