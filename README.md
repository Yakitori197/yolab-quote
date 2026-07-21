# yolab-quote

Unified market data for Taiwan / US stocks and crypto. One synchronous API,
several providers, automatic fallback.

```python
import yolab_quote as yq

quote = yq.get_quote("2330")        # bare Taiwan code -> 2330.TW
print(quote.name, quote.price, quote.change_percent)

quotes = yq.get_quotes(["2330", "0050", "NVDA"])   # concurrent
bars = yq.get_bars("2330", days=30)                # daily candles
```

## Why

This library was extracted from three production bots that each grew their
own market-data layer, then drifted apart. Consolidating them surfaced a set
of bugs worth fixing once, in one place:

**Taiwan leveraged and inverse ETFs were silently mis-routed.** Two of the
three used `code.isdigit()` or `^\d{4,6}$` to recognise a Taiwan listing.
Both reject `00631L` and `00632R`, so those symbols fell through to the US
branch and looked up instruments that do not exist.

```python
"00631L".isdigit()          # False  <- what the old check asked
yq.is_tw_code("00631L")     # True   <- what is actually true
yq.normalize_stock("00631L")  # '00631L.TW'
```

**The same stock could report two different daily changes.** One bot computed
change against `history().iloc[-2]['Close']`, another against
`info['previousClose']`. Here every provider is normalized to the same
baseline, and the percentage calculation is guarded against a zero previous
close (one of the originals was not, and could raise `ZeroDivisionError` on a
halted or newly listed symbol).

**Failures were indistinguishable from empty results.** All three returned
`None` on error, so "no such ticker" and "the network is down" looked
identical to the caller. Every failure path here raises something specific,
and `AllProvidersFailedError` carries the reason each provider gave.

**Batches were fetched serially.** Fetching a few dozen symbols one at a time
is what pushed one bot past its messaging platform's reply deadline.
`get_quotes()` fans out across a thread pool.

## Install

```bash
pip install yolab-quote[yfinance]
```

`yfinance` is an optional extra, not a hard dependency: every bot this came
from guarded its import with `try/except ImportError`, and one of them
actually shipped to production without it installed. The core package pulls
in only `httpx`.

```bash
pip install yolab-quote              # core only
pip install yolab-quote[yfinance]    # + the yfinance provider
pip install yolab-quote[all]         # every provider
```

## Usage

### Quotes

```python
import yolab_quote as yq

quote = yq.get_quote("2330")
quote.symbol          # '2330.TW'  -- normalized
quote.market          # 'tw_stock' -- inferred
quote.price           # 1085.0     -- always a real float
quote.change_percent  # 1.40
quote.currency        # 'TWD'
quote.source          # 'yfinance' -- which provider answered
quote.extra           # {'pe_ratio': 24.5, 'dividend_yield': 1.35, ...}
```

Every numeric field is a genuine Python `float`, never a `numpy` scalar. One
of the original bots passed pandas values straight into its message
formatting, which breaks whenever pandas changes its formatting behaviour.

### Batches

```python
quotes = yq.get_quotes(["2330", "0050", "NVDA"])   # concurrent, order preserved
```

Symbols that fail are omitted rather than raising -- compare the returned
keys against your input to find them:

```python
requested = ["2330", "NOSUCH"]
got = yq.get_quotes(requested)
missing = [s for s in requested if s not in got]     # ['NOSUCH']
```

### Historical bars

```python
bars = yq.get_bars("2330", days=30)     # oldest first
bars[-1].close
bars[-1].to_dict()   # {'date': '2026-01-02', 'o': ..., 'h': ..., 'l': ..., 'c': ..., 'v': ...}
```

### Errors

```python
from yolab_quote import AllProvidersFailedError, SymbolNotFoundError

try:
    quote = yq.get_quote("NOSUCH")
except AllProvidersFailedError as exc:
    print(exc.failures)   # {'yfinance': 'no price data for ...'}
```

| Exception | Meaning |
|---|---|
| `SymbolError` | The symbol is empty or malformed. |
| `SymbolNotFoundError` | The provider works, but has no such symbol. |
| `ProviderUnavailableError` | Not configured -- missing SDK or API key. Retrying will not help. |
| `ProviderError` | Network, parse, or upstream failure. |
| `AllProvidersFailedError` | Every provider failed; `.failures` holds each reason. |

### Async

```python
import yolab_quote.aio as aq

quote = await aq.get_quote("2330")
quotes = await aq.get_quotes(["2330", "NVDA"])
```

The core is synchronous and the async layer wraps it, not the other way
round. That ordering is deliberate: wrapping sync in async costs a worker
thread, while the reverse forces every caller to own an event loop -- which
is exactly what made the async-only implementation this replaces unusable
from the synchronous web apps that needed it most.

### Client instances

The module-level functions use a shared default client. Build your own when
you need different settings:

```python
from yolab_quote import QuoteClient

client = QuoteClient(ttl=30, max_workers=16)
quote = client.get_quote("2330")
health = client.health()
client.close()
```

## Markets

| Market | Identifier | Example |
|---|---|---|
| Taiwan | `tw_stock` | `2330`, `0050`, `00631L`, `6488.TWO` |
| United States | `us_stock` | `NVDA`, `BRK.B` |
| Hong Kong | `hk_stock` | `0700.HK` |
| Japan | `jp_stock` | `7203.T` |
| China | `cn_stock` | `600519.SS` |
| UK / Germany / Korea | `uk_stock` / `de_stock` / `kr_stock` | `.L` / `.DE` / `.KS` |
| Crypto | `crypto_spot`, `crypto_futures`, `crypto_futures_coin` | pass the market explicitly |

Equity markets are inferred from the symbol. Crypto markets must be passed
explicitly, which keeps crypto guesswork out of the stock path:

```python
yq.get_quote("BTCUSDT", yq.CRYPTO_SPOT)
```

## Configuration

Provider order per market, via environment variable:

```bash
YOLAB_QUOTE_TW_STOCK_PROVIDERS=yfinance,yahoo_scraper
YOLAB_QUOTE_US_STOCK_PROVIDERS=yfinance
```

Or in code, which also lets you pass per-provider settings -- API keys and
timeouts reach the constructor even for providers built lazily inside the
fallback chain:

```python
client = QuoteClient(
    priority={yq.TW_STOCK: ["yfinance"]},
    provider_options={"yfinance": {"timeout": 20.0}},
)
```

## Custom providers

Implement two methods and register the class:

```python
from yolab_quote import Provider, Quote, ProviderHealth, TW_STOCK, register

class MyProvider(Provider):
    name = "mine"
    markets = (TW_STOCK,)

    def get_quote(self, symbol: str) -> Quote:
        return Quote.create(symbol=symbol, market=TW_STOCK, source=self.name, price=100.0)

    def health(self) -> ProviderHealth:
        return ProviderHealth(provider=self.name, ok=True, status="ready", markets=self.markets)

register("mine", MyProvider)
```

`Quote.create()` handles the numeric coercion and derives `change` /
`change_percent` for you, so every provider produces consistent output.

Optionally override `get_quotes()` when the upstream API has a real bulk
endpoint, and `get_bars()` for history. The manager checks `markets` before
dispatching, so a provider is never handed a market it does not serve.

## 繁體中文說明

這是一套統一的行情查詢函式庫，支援台股、美股與加密貨幣，同步 API 為主、
提供 async 包裝層。

重點特色：

- **正確處理台股代碼**，包含槓桿／反向 ETF（`00631L`、`00632R`）。這是常見
  的踩雷點：用 `isdigit()` 或 `^\d{4,6}$` 判斷會漏掉這些標的。
- **多資料源自動 fallback**，失敗會明確拋出例外並附上每個來源的失敗原因，
  不會靜默回傳 `None` 讓你分不清「查無此股」和「網路掛了」。
- **批次查詢併發處理**，數十檔一次查完，不會逐檔序列等待。
- **數值一律為原生 `float`**，不會外洩 pandas／numpy 型別。
- `yfinance` 是選用相依（`pip install yolab-quote[yfinance]`），核心只依賴
  `httpx`。

```python
import yolab_quote as yq

q = yq.get_quote("2330")
print(q.name, q.price, q.change_percent)   # 台積電 1085.0 1.40
```

## Development

```bash
python -m pytest          # 152 tests, no network required
python -m mypy src        # strict: disallow_untyped_defs
python -m ruff check src tests
```

Tests never touch the network. Providers expose seams (`_fetch_info`,
`_fetch_history`) and the field mapping is a module-level pure function, so
the parts that actually break when an upstream API changes can be tested
directly.

## License

MIT
