# Releasing yolab-quote

How to cut a release and publish it to PyPI. Every step runs locally; the
only account this touches is your own PyPI account, and your token never
leaves your machine.

## Prerequisites

- A PyPI account with **2FA enabled** (PyPI requires it to upload).
- A PyPI **API token** (Account settings → API tokens). It looks like
  `pypi-AgEIcHl...`. Scope it to this project once the project exists;
  the first upload needs an account-wide token.
- Build tooling:
  ```
  python -m pip install --upgrade build twine
  ```

## Release steps

### 1. Bump the version

The version lives in **two** places and they must match:

- `pyproject.toml` → `version = "X.Y.Z"`
- `src/yolab_quote/__init__.py` → `__version__ = "X.Y.Z"`

Follow [SemVer](https://semver.org): patch for fixes, minor for
backwards-compatible features (e.g. a new provider), major for anything that
breaks the public API.

### 2. Pass the full quality gate

Nothing ships unless all three are green:

```
python -m pytest
python -m mypy src
python -m ruff check src tests
```

### 3. Build clean artifacts

Delete any stale artifacts first — `twine upload dist/*` uploads *everything*
in `dist/`, so a leftover build from an old version would be published by
mistake:

```
# PowerShell
Remove-Item dist -Recurse -Force -ErrorAction SilentlyContinue
python -m build
```

This produces `dist/yolab_quote-X.Y.Z-py3-none-any.whl` and
`dist/yolab_quote-X.Y.Z.tar.gz`.

### 4. Check the artifacts

```
python -m twine check dist/*
```

Both files must report `PASSED`. This validates the metadata and the
long-description rendering before anything is public.

Optionally, inspect what is actually inside the wheel — it should contain the
package modules and `LICENSE`, and **nothing** from `tests/` or any local
scratch files:

```
python -c "import zipfile; [print(n) for n in zipfile.ZipFile('dist/yolab_quote-X.Y.Z-py3-none-any.whl').namelist()]"
```

### 5. (Optional) Dry-run on TestPyPI

TestPyPI is a separate instance with its own account and token. Useful to
preview the project page before committing to the real thing:

```
python -m twine upload --repository testpypi dist/*
```

Then install from it in a throwaway environment to confirm it works
(`--extra-index-url` is needed because dependencies like `httpx` live on the
real PyPI):

```
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ \
            yolab-quote
```

### 6. Upload to PyPI

```
python -m twine upload dist/*
```

When prompted:

- **username:** `__token__` (literally, with the leading and trailing double
  underscores)
- **password:** paste your `pypi-...` token

### 7. Tag the release

```
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

Optionally create a GitHub Release from the tag (`gh release create vX.Y.Z`)
with notes drawn from the changes since the last tag.

## Security — read before every upload

Your API token is a credential, equivalent to a password.

- **Never** commit it, paste it into a file, or put it on the command line
  (your shell keeps a history). Type it interactively when `twine` asks.
- **Never** share it — not in a chat, an issue, or a screenshot. Anyone with
  the token can publish under your name.
- If a token is ever exposed, revoke it immediately in your PyPI account
  settings and issue a new one.
- The upload step is deliberately manual. No script here, and no automation,
  should ever be handed your token.

## A published version is permanent

PyPI does **not** allow re-uploading or overwriting a version that already
exists. If you publish `0.1.0` and then find a problem, you cannot replace
it — you must fix the issue and release `0.1.1`. This is why steps 2–5 exist:
catch it before the upload, not after.

## After a successful release

Once `pip install yolab-quote` resolves the new version, the three bots that
currently depend on it via a moving git reference can be pinned to the
published release instead — more reproducible, and a faster install:

```
# before
yolab-quote[yfinance] @ git+https://github.com/Yakitori197/yolab-quote.git@main
# after
yolab-quote[yfinance]>=X.Y.Z,<NEXT_MAJOR
```

The affected files are `requirements.txt` in `Stock_LineBot`,
`Stock_LineBot_Public`, and `Discord_StockBot` (plus `requirements.lock` in
`Discord_StockBot`, which Render builds from).

---

## 繁體中文重點

- **上傳一律你自己在終端執行**，token 不寫進任何檔案、不放進指令列（會留在
  PowerShell 歷史）、不交給任何人或工具。`twine` 提示時才互動式貼上。
  username 填 `__token__`（前後各兩個底線）。
- **版本號用掉就永久佔用**，PyPI 不允許覆蓋。發現問題只能發下一個版號
  （例如 `0.1.0` → `0.1.1`），所以步驟 2–5 的檢查要在上傳前做完。
- 版本號要**同時**改 `pyproject.toml` 與 `src/yolab_quote/__init__.py` 兩處。
- 發布成功後，三隻 bot 的相依可從 `git+https://...@main` 改成固定的 PyPI
  版本（見上一節），較穩、Render build 較快。
