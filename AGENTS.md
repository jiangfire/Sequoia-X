# Sequoia-X Agent Notes

A-Share quantitative stock selection system, forked and being customized into a personal quant strategy.

## Tooling

- Python >= 3.10 (ruff targets py311).
- Package manager: `uv`. Lockfile is `uv.lock`; deps and tool config live in `pyproject.toml`.
- Dev extras include pytest, hypothesis, pytest-mock; install with `uv sync --extra dev`.

## Everyday Commands

```bash
uv sync --extra dev                 # install runtime + dev dependencies
uv run python main.py               # daily mode: sync data + run strategies + notify Feishu
uv run python main.py --backfill    # one-time historical backfill (~12 min)
uv run pytest                       # property-based tests
uv run ruff check .                 # lint
uv run ruff format .
```

## Environment

- Copy `.env.example` to `.env`; `FEISHU_WEBHOOK_URL` is required at runtime.
- Optional per-strategy webhooks: `STRATEGY_WEBHOOK_<KEY>` env vars are collected into `Settings.strategy_webhooks` (key lowercased). `webhook_key="default"` falls back to the default URL.
- `data/sequoia_v2.db` is SQLite and gitignored; created automatically. It can be copied across machines.

## Architecture

- `main.py` loads `.env` before other imports, sets a 10 s socket timeout, builds `DataEngine`, runs every registered strategy, and pushes results via `FeishuNotifier`.
- `sequoia_x/core/config.py` — pydantic-settings singleton via `get_settings()`. Reads `.env` and `STRATEGY_WEBHOOK_*` variables.
- `sequoia_x/core/logger.py` — rich-based colored logs; use `get_logger(__name__)`.
- `sequoia_x/data/engine.py` — SQLite storage and baostock sync.
  - `stock_daily` columns: `symbol, date, open, high, low, close, volume, turnover`.
  - `adjustflag="1"` means back-adjusted (后复权) daily K-line data.
  - `sync_today_bulk()` uses 8-worker `multiprocessing.Pool`.
  - `backfill()` is single-threaded with retries and periodic baostock re-login.
  - `get_all_symbols()` fetches the full A-share list from baostock; `get_local_symbols()` reads symbols already in the DB.
- `sequoia_x/strategy/base.py` — `BaseStrategy.run() -> list[str]`.
- `sequoia_x/notify/feishu.py` — builds an interactive card and POSTs to the strategy's webhook.

## Adding or Changing a Strategy

1. Create a class in `sequoia_x/strategy/` inheriting `BaseStrategy`.
2. Set `webhook_key` if you want a dedicated Feishu bot; otherwise it uses the default.
3. Implement `run()` to return a `list[str]` of numeric stock codes.
4. Import and append the strategy instance in `main.py:strategies`.
5. Prefer vectorized pandas operations; avoid `iterrows` over OHLCV data.

## Conventions

- Stock codes are stored as plain numeric strings (e.g. `000001`, `600519`).
- Baostock codes: `sh.<code>` for 6/9 prefixes, `sz.<code>` otherwise.
- Xueqiu codes: `SH<code>` for 6, `BJ<code>` for 4/8, `SZ<code>` otherwise.
- Do not commit `.env` or `data/*.db`.

## Testing Notes

- Tests are property-based (Hypothesis).
- `tests/test_feishu.py` patches `requests.post`, but `_build_card()` still calls baostock to resolve stock names, so those tests can hang or fail without network. Consider mocking `FeishuNotifier._get_stock_names` when iterating on notification logic.
- `tests/test_main.py` imports `main` at module level to avoid repeated imports inside `@given`.
