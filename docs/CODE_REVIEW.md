# Code Review — 2026-06-25

> 对 Sequoia-X 性能优化 + 飞书推送移除后的全量代码审查。

## 审查范围

| 模块 | 文件 |
|---|---|
| 数据引擎 | `sequoia_x/data/engine.py` |
| 策略基类 | `sequoia_x/strategy/base.py` |
| 策略实现 | `ma_volume.py`, `turtle_trade.py`, `high_tight_flag.py`, `limit_up_shakeout.py`, `uptrend_limit_down.py`, `rps_breakout.py`, `private_placement.py`, `_utils.py` |
| 通知 | `sequoia_x/notify/base.py` |
| 配置 | `sequoia_x/core/config.py` |
| 日志 | `sequoia_x/core/logger.py` |
| 入口 | `main.py` |
| 测试 | `tests/` 全部 |

## 已修复问题（本轮审查中即时修复）

### 1. `engine.py` 重复定义 `_migrate_add_columns`

**严重级别：Critical** — 已修复

`_migrate_add_columns` 被粘贴了两次（行 113 和行 123），第二个定义静默覆盖第一个。虽然逻辑相同不影响运行，但属于明显的复制粘贴遗留。

### 2. `private_placement.py` docstring 残留"推送"

**严重级别：Nit** — 已修复

模块 docstring 仍写"推送最近发布的定向增发公告"，但推送层已移除。改为"筛选"。

### 3. `test_strategy.py` patch 目标错误

**严重级别：Important** — 已修复

`test_strategy_run_returns_list_of_str` 中 patch 了 `get_all_symbols`，但 `MaVolumeStrategy.run()` 实际调用的是 `get_local_symbols()`。patch 是 no-op，测试碰巧通过是因为空数据库的 `get_local_symbols()` 也返回空列表。已改为 patch `get_local_symbols`。

---

## 待改进项

### 4. `get_limit_pct()` 的 `is_st` 参数是死代码

**严重级别：Optional** — `sequoia_x/strategy/_utils.py:6`

`is_st` 参数被接收但从未使用。docstring 解释"按新规则不影响主板阈值，保留参数供扩展"。如果确实不需要，建议移除以减少误导；如果未来需要区分创业板 ST（目前创业板 ST 也是 ±20%），可以保留但应在 docstring 中说明当前为何 no-op。

### 5. `get_limit_pct()` 前缀匹配存在冗余

**严重级别：Nit** — `sequoia_x/strategy/_utils.py:24`

```python
if symbol.startswith(("4", "8", "43", "83", "87", "88", "920")):
```

`"43"` 已被 `"4"` 覆盖，`"83"`/`"87"`/`"88"` 已被 `"8"` 覆盖。只有 `"920"` 是独立分支。建议简化为：

```python
if symbol.startswith(("4", "8", "920")):
```

### 6. RPS 策略独立加载缓存可能覆盖全量缓存

**严重级别：Important** — `sequoia_x/strategy/rps_breakout.py:18-20`

```python
cache = self.engine.ohlcv_cache
if cache is None:
    cache = self.engine.load_ohlcv_cache(columns=["symbol", "date", "close", "high"])
```

如果 RPS 在 `main.py` 未预加载缓存的情况下被独立调用，它会用部分列加载缓存。此后其他策略调用 `get_ohlcv()` 时，缓存切片会缺少 `open`/`volume` 等列，导致 `df[_TABLE_COLUMNS]` 抛 KeyError。

**当前不会触发**，因为 `main.py:67` 总是先全量加载。但这是一个脆弱的隐式依赖。建议：
- 要么 `load_ohlcv_cache` 始终加载全部列（移除 `columns` 参数）；
- 要么 `get_ohlcv` 在缓存列不足时 fallback 到数据库查询。

### 7. 策略逐 symbol 循环调用 `get_ohlcv` 仍可向量化

**严重级别：Optional** — `ma_volume.py`, `turtle_trade.py`, `high_tight_flag.py`, `limit_up_shakeout.py`, `uptrend_limit_down.py`

虽然缓存已经让 `get_ohlcv()` 变成了内存切片（不再有 SQLite IO），但 5 个策略仍然是 `for symbol in symbols: df = get_ohlcv(symbol); ...` 的逐只循环。对于 5000+ 只股票，这仍然是 Python 层面的循环开销。

RPS 策略已经展示了全量向量化做法（`cache.reset_index()` + `groupby`）。其他策略如果性能仍然不够，可以参考同样的模式重构。当前优先级不高，因为缓存命中后主要开销在 pandas 计算而非 IO。

### 8. `to_baostock_code` 的 "9" 前缀映射

**严重级别：FYI** — `sequoia_x/data/engine.py:210-211`

```python
prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
```

A 股没有 "9" 开头的股票代码（"900" 开头是沪市 B 股）。这个映射不会对 A 股产生错误，但如果未来数据库中混入 B 股代码，会被路由到 `sh`，这是正确的。保留即可，但建议在注释中说明 "9" 对应的是 B 股而非 A 股。

### 9. `logger.py` 未检查已有 handler 类型

**严重级别：Optional** — `sequoia_x/core/logger.py:26-27`

```python
if logger.handlers:
    return logger
```

如果 logger 已被添加了非 RichHandler（例如 pytest 的 caplog），函数会跳过 RichHandler 的添加。建议检查是否已存在 RichHandler：

```python
if any(isinstance(h, RichHandler) for h in logger.handlers):
    return logger
```

### 10. `main.py` 中 `logger` 变量作用域

**严重级别：FYI** — `main.py:47, 103`

`logger` 在 `try` 块内（行 47）赋值，在 `try` 块外（行 103）使用。如果异常发生在行 47 之前，`except` 块会 `sys.exit(1)` 不会到达行 103。当前控制流是安全的，但如果有人重构 `except` 块不退出，行 103 会 `NameError`。建议将 `logger` 初始化提到 `try` 块之前。

### 11. 测试中 `_make_engine` 返回后临时目录可能被清理

**严重级别：FYI** — `tests/test_strategy.py:23-35`

```python
def _make_engine(rows: list[dict]) -> tuple[DataEngine, Settings]:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        ...
        return engine, settings
```

`return` 触发 `with` 退出，临时目录会被清理。由于 `ignore_cleanup_errors=True`，不会报错，但数据库文件可能在使用中被删除。当前测试能通过是因为数据已经加载到内存缓存中，策略不再访问文件。但如果未来有策略在运行时 fallback 到数据库查询，可能会失败。

### 12. `backfill()` 中 `isST` 字段可能缺失

**严重级别：Optional** — `sequoia_x/data/engine.py:402-404`

```python
df["is_st"] = pd.to_numeric(df.get("isST", 0), errors="coerce").fillna(0).astype(int)
```

`df.get("isST", 0)` 在 DataFrame 没有该列时返回标量 `0`，`pd.to_numeric(0)` 能正常工作。但如果 baostock 返回的 fields 中 `isST` 列名有变化（例如大小写），这里会静默用 0 填充，所有股票都会被标记为非 ST。建议加一行日志或 assert 验证 fields 包含 `isST`。

### 13. `Notifier` 抽象基类未被 `main.py` 使用

**严重级别：FYI** — `sequoia_x/notify/base.py`

`Notifier` 基类已定义但 `main.py` 中没有任何 notifier 实例。这是符合预期的（用户不需要推送），但如果未来要接入推送，需要在 `main.py` 中注入 notifier。当前设计是干净的——`main.py` 只做日志输出，需要推送时在 `main.py` 中构造 `Notifier` 子类即可。

---

## 架构评价

### 优点

1. **全表缓存方案** 有效消除了策略层的 N+1 SQLite 连接问题，`load_ohlcv_cache()` + `get_ohlcv()` 的缓存切片设计简洁有效。
2. **`sync_today_bulk` 的 DELETE 修复** 正确解决了停牌/失败股票数据被误删的 bug。
3. **板块动态阈值** 通过 `_utils.py` 集中管理，扩展性好。
4. **通知层抽象化** 干净利落，`Notifier` ABC 定义了最小接口，不引入多余复杂度。
5. **config 精简** 移除飞书后只剩 `db_path` 和 `start_date`，非常清爽。

### 需要关注的设计决策

1. **缓存列一致性**：`load_ohlcv_cache(columns=...)` 支持部分列加载，但 `get_ohlcv()` 假设缓存有全部列。建议要么移除 `columns` 参数，要么在 `get_ohlcv()` 中做列完整性检查（见第 6 项）。
2. **`backfill` 批量写入**：当前每 500 只股票批量写入一次，如果中途崩溃会丢失这 500 只的数据。可以考虑减小批量或增加 checkpoint 频率。

---

## 验证结果

| 检查项 | 结果 |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run ruff format .` | 25 files left unchanged |
| `uv run pytest` | 16 passed in 5.71s |

---

## 总结

本轮改动（性能优化 + 飞书移除）整体质量良好。审查中发现的 3 个问题已即时修复（重复方法、docstring 残留、测试 patch 错误）。剩余 10 项均为 Optional/FYI 级别，不影响当前运行，可在后续迭代中逐步处理。

最值得优先处理的是 **第 6 项**（RPS 部分列缓存可能覆盖全量缓存），这是唯一一个可能在特定调用顺序下触发的功能 bug。
