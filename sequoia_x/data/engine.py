"""数据引擎模块：负责 SQLite 行情数据存储与 baostock 增量同步。"""

import sqlite3
from pathlib import Path
from typing import cast

import pandas as pd

from sequoia_x.core.config import Settings
from sequoia_x.core.logger import get_logger

logger = get_logger(__name__)

# 从 baostock 拉取的字段（后复权日线）
_BS_DAILY_FIELDS = "date,open,high,low,close,volume,amount,turn,isST"
# 本地表的列顺序（不含自增 id）
_TABLE_COLUMNS = [
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "turn",
    "is_st",
]

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS stock_daily (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol   TEXT    NOT NULL,
    date     TEXT    NOT NULL,
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    turnover REAL,
    turn     REAL,
    is_st    INTEGER,
    UNIQUE (symbol, date)
);
"""

_CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_symbol_date ON stock_daily (symbol, date);
"""

# 数值列（用于类型转换）
_NUMERIC_COLUMNS = ["open", "high", "low", "close", "volume", "turnover", "turn"]


def _bs_fetch_batch(tasks: list[tuple[str, str, str, str]]) -> list[list[str]]:
    """多进程 worker：独立 login，批量拉取 baostock 数据。"""
    import baostock as bs

    bs.login()
    results = []
    for symbol, bs_code, start, end in tasks:
        rs = bs.query_history_k_data_plus(
            bs_code,
            _BS_DAILY_FIELDS,
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="1",  # 后复权
        )
        if rs is None or rs.error_code != "0":
            continue
        while rs.next():
            results.append([symbol, *rs.get_row_data()])
    bs.logout()
    return results


class DataEngine:
    """行情数据引擎，负责 SQLite 存储和 baostock 数据同步。"""

    def __init__(self, settings: Settings) -> None:
        self.db_path: str = settings.db_path
        self.start_date: str = settings.start_date
        self._ohlcv_cache: pd.DataFrame | None = None
        self._init_db()

    def connect(self) -> sqlite3.Connection:
        """创建 SQLite 连接；支持共享内存数据库 URI。"""
        if self.db_path.startswith("file:"):
            return sqlite3.connect(self.db_path, uri=True)
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        is_memory_db = self.db_path.startswith("file:") and "mode=memory" in self.db_path
        if not is_memory_db:
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute(_CREATE_TABLE_SQL)
            conn.execute(_CREATE_INDEX_SQL)
            # WAL 在内存数据库上不受支持，仅对文件数据库开启
            if not is_memory_db:
                conn.executescript(
                    """
                    PRAGMA journal_mode = WAL;
                    PRAGMA synchronous = NORMAL;
                    PRAGMA cache_size = -64000;
                    """
                )
            self._migrate_add_columns(conn)
            conn.commit()
        logger.info(f"数据库初始化完成：{self.db_path}")

    @staticmethod
    def _migrate_add_columns(conn: sqlite3.Connection) -> None:
        """兼容旧库：动态添加 turn / is_st 列。"""
        cursor = conn.execute("PRAGMA table_info(stock_daily)")
        existing = {row[1] for row in cursor.fetchall()}
        if "turn" not in existing:
            conn.execute("ALTER TABLE stock_daily ADD COLUMN turn REAL")
        if "is_st" not in existing:
            conn.execute("ALTER TABLE stock_daily ADD COLUMN is_st INTEGER")

    def _expire_cache(self) -> None:
        """数据写入后清空内存缓存，确保后续读取为最新。"""
        self._ohlcv_cache = None

    @property
    def ohlcv_cache(self) -> pd.DataFrame | None:
        """当前已加载的全表 OHLCV 缓存（MultiIndex symbol/date），未加载时为 None。"""
        return self._ohlcv_cache

    def _get_last_date_map(self) -> dict[str, str]:
        """返回 {symbol: max_date}，避免 N+1 查询。"""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT symbol, MAX(date) FROM stock_daily GROUP BY symbol"
            ).fetchall()
        return {symbol: max_date for symbol, max_date in rows if max_date}

    def get_ohlcv(self, symbol: str) -> pd.DataFrame:
        """返回某只股票的 OHLCV 数据（优先从内存缓存切片）。"""
        if self._ohlcv_cache is not None:
            try:
                df = self._ohlcv_cache.loc[[symbol]].reset_index()
                return df[_TABLE_COLUMNS]
            except KeyError:
                return pd.DataFrame(columns=_TABLE_COLUMNS)

        with self.connect() as conn:
            df = pd.read_sql(
                f"SELECT {', '.join(_TABLE_COLUMNS)} FROM stock_daily "
                "WHERE symbol = ? ORDER BY date",
                conn,
                params=(symbol,),
            )
            df["date"] = pd.to_datetime(df["date"])
            return df

    def load_ohlcv_cache(
        self,
        since: str | None = None,
    ) -> pd.DataFrame:
        """一次性加载全市场 OHLCV 到内存，返回 MultiIndex (symbol, date)。

        Args:
            since: 只加载该日期之后的数据（格式 YYYY-MM-DD）。
        """
        select_cols = _TABLE_COLUMNS
        query = f"SELECT {', '.join(select_cols)} FROM stock_daily WHERE 1=1"
        params: list[str] = []
        if since:
            query += " AND date >= ?"
            params.append(since)
        query += " ORDER BY symbol, date"

        with self.connect() as conn:
            df = pd.read_sql(query, conn, params=params)

        if df.empty:
            self._ohlcv_cache = pd.DataFrame(
                columns=select_cols,
            ).set_index(["symbol", "date"])
            return self._ohlcv_cache

        df["date"] = pd.to_datetime(df["date"])
        for col in _NUMERIC_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["is_st"] = pd.to_numeric(df["is_st"], errors="coerce").fillna(0).astype(int)

        df = df.set_index(["symbol", "date"])
        self._ohlcv_cache = df
        logger.info(f"OHLCV 缓存加载完成：{len(df)} 条记录")
        return df

    @staticmethod
    def to_baostock_code(symbol: str) -> str:
        """将纯数字代码转为 baostock 格式：6/9开头 -> sh，其余 -> sz。

        注意：A 股代码没有 "9" 开头，"900" 开头是沪市 B 股，此处映射到 sh 是正确的。
        """
        prefix = "sh" if symbol.startswith(("6", "9")) else "sz"
        return f"{prefix}.{symbol}"

    # ── 数据同步 ──

    def sync_today_bulk(self, daily_query_limit: int = 3500) -> int:
        """多进程并行通过 baostock 拉取增量数据（后复权），写入 SQLite。

        为避免触发 baostock 单日查询上限，仅处理最近 3 个交易日未更新的股票，
        且总查询次数不超过 daily_query_limit。
        """
        from datetime import date, timedelta
        from multiprocessing import Pool

        today_str = date.today().strftime("%Y-%m-%d")

        last_date_map = self._get_last_date_map()
        if not last_date_map:
            logger.warning("本地无股票数据，请先执行 --backfill")
            return 0

        # 只处理近 3 个交易日未更新的股票，避免每天全量更新
        cutoff_date = (date.today() - timedelta(days=3)).strftime("%Y-%m-%d")

        tasks = []
        for symbol, last_date in last_date_map.items():
            if last_date >= today_str:
                continue
            if last_date >= cutoff_date:
                # 最近已有数据，不浪费额度追平停牌日
                continue
            start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")
            tasks.append((symbol, self.to_baostock_code(symbol), start, today_str))

        if len(tasks) > daily_query_limit:
            logger.warning(
                f"需更新 {len(tasks)} 只股票，超过单日上限 {daily_query_limit}，"
                f"仅处理前 {daily_query_limit} 只"
            )
            tasks = tasks[:daily_query_limit]

        if not tasks:
            logger.info("所有股票已是最新，无需更新")
            return 0

        logger.info(f"需要更新 {len(tasks)} 只股票，启动多进程并行拉取...")

        n_workers = min(8, len(tasks))
        chunks = [tasks[i::n_workers] for i in range(n_workers)]

        with Pool(n_workers) as pool:
            batch_results = pool.map(_bs_fetch_batch, chunks)

        all_rows = []
        for batch in batch_results:
            all_rows.extend(batch)

        if not all_rows:
            logger.info("无新数据（可能非交易日）")
            return 0

        df = pd.DataFrame(all_rows, columns=_TABLE_COLUMNS)
        for col in _NUMERIC_COLUMNS:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["is_st"] = pd.to_numeric(df["is_st"], errors="coerce").fillna(0).astype(int)
        df = cast(pd.DataFrame, df.dropna(subset=["close"]))
        df = df[df["volume"] > 0]

        count = len(df)
        with self.connect() as conn:
            # 只删除本次实际会插入的 (symbol, date) 对，避免误删停牌/失败股票
            pairs = list(zip(df["symbol"].tolist(), df["date"].tolist(), strict=False))
            conn.executemany(
                "DELETE FROM stock_daily WHERE symbol = ? AND date = ?",
                pairs,
            )
            df.to_sql(
                "stock_daily", conn, if_exists="append", index=False, method="multi", chunksize=500
            )
            conn.commit()

        self._expire_cache()
        logger.info(f"sync_today_bulk: 写入 {count} 条数据")
        return count

    def backfill(self, symbols: list[str], daily_query_limit: int = 3500) -> None:
        """通过 baostock 批量回填历史日 K 线数据（后复权）。

        容错机制：
        - 单只股票失败自动重试 3 次，间隔递增（2s/4s/8s）
        - 每 200 只股票自动重连 baostock（防止长连接超时）
        - 已入库的自动 skip，中断后可重跑续传
        - 优先回填本地无数据的股票，避免每日在已有数据上重复消耗额度
        - 达到 daily_query_limit 后主动停止，便于次日继续
        """
        import time
        from datetime import date, timedelta

        import baostock as bs

        today_str = date.today().strftime("%Y-%m-%d")
        max_retries = 3
        reconnect_interval = 200  # 每处理 N 只股票重连一次

        last_date_map = self._get_last_date_map()
        local_symbols = set(last_date_map.keys())

        # 优先处理本地无数据的股票，其次才是需要续传的股票
        missing_symbols = [s for s in symbols if s not in local_symbols]
        existing_symbols = [s for s in symbols if s in local_symbols]
        ordered_symbols = missing_symbols + existing_symbols

        logger.info(
            f"待回填 {len(symbols)} 只：本地无数据 {len(missing_symbols)} 只，"
            f"已存在 {len(existing_symbols)} 只，单日查询上限 {daily_query_limit}"
        )

        def _login(max_attempts: int = 3):
            for attempt in range(max_attempts):
                try:
                    lg = bs.login()
                    if lg.error_code == "0":
                        return True
                    logger.warning(f"baostock 登录返回错误: {lg.error_msg}")
                except Exception as exc:
                    logger.warning(f"baostock 登录异常: {exc}")
                if attempt < max_attempts - 1:
                    wait = 2 ** (attempt + 1)
                    logger.info(f"baostock 登录第 {attempt + 1} 次失败，{wait}s 后重试...")
                    time.sleep(wait)
            logger.error("baostock 登录失败: 已达到最大重试次数")
            return False

        if not _login():
            return

        logger.info(f"开始回填 {len(symbols)} 只股票的历史数据...")

        success = 0
        skipped = 0
        failed = 0
        total_rows = 0
        query_count = 0
        since_reconnect = 0
        pending_frames: list[pd.DataFrame] = []

        try:
            for i, symbol in enumerate(ordered_symbols):
                if query_count >= daily_query_limit:
                    logger.info(
                        f"达到单日查询上限 {daily_query_limit}，暂停回填，"
                        f"剩余 {len(symbols) - i} 只留到次日继续"
                    )
                    break

                last_date = last_date_map.get(symbol)
                if last_date and last_date >= today_str:
                    skipped += 1
                    if (i + 1) % 100 == 0:
                        logger.info(
                            f"已处理 {i + 1}/{len(symbols)}，"
                            f"成功 {success} | 跳过 {skipped} | 失败 {failed}"
                        )
                    continue

                # 定期重连，防止长连接超时
                since_reconnect += 1
                if since_reconnect >= reconnect_interval:
                    bs.logout()
                    time.sleep(1)
                    if not _login():
                        logger.error("重连失败，终止回填")
                        return
                    since_reconnect = 0

                start = self.start_date
                if last_date:
                    start = (date.fromisoformat(last_date) + timedelta(days=1)).strftime("%Y-%m-%d")

                bs_code = self.to_baostock_code(symbol)

                # 带重试的查询
                rows: list[list[str]] = []
                fields: list[str] = []
                query_ok = False
                for attempt in range(max_retries):
                    try:
                        rs = bs.query_history_k_data_plus(
                            bs_code,
                            _BS_DAILY_FIELDS,
                            start_date=start,
                            end_date=today_str,
                            frequency="d",
                            adjustflag="1",  # 后复权
                        )

                        if rs is None or rs.error_code != "0":
                            raise RuntimeError(rs.error_msg if rs else "查询返回 None")

                        if rs.fields is not None:
                            fields = list(rs.fields)
                        rows = []
                        while rs.next():
                            row_data = rs.get_row_data()
                            if row_data is not None:
                                rows.append(list(row_data))
                        query_ok = True
                        break

                    except Exception as exc:
                        if attempt < max_retries - 1:
                            wait = 2 ** (attempt + 1)
                            logger.warning(
                                f"[{symbol}] 第{attempt + 1}次失败: {exc}，{wait}s 后重试"
                            )
                            time.sleep(wait)
                            # 重连 baostock
                            bs.logout()
                            time.sleep(1)
                            _login()
                        else:
                            logger.warning(f"[{symbol}] {max_retries}次重试均失败，跳过")

                query_count += 1

                if not query_ok:
                    failed += 1
                    continue

                if not rows:
                    skipped += 1
                    continue

                df = pd.DataFrame(rows, columns=fields)
                for col in ["open", "high", "low", "close", "volume", "amount", "turn"]:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
                df = cast(pd.DataFrame, df.dropna(subset=["close"]))
                df = df[df["volume"] > 0]

                if df.empty:
                    skipped += 1
                    continue

                df["symbol"] = symbol
                if "isST" not in df.columns:
                    logger.warning(f"[{symbol}] baostock 返回字段缺少 isST，默认标记为非 ST")
                df["is_st"] = (
                    pd.to_numeric(df.get("isST", 0), errors="coerce").fillna(0).astype(int)
                )
                df = df.rename(columns={"amount": "turnover"})  # pyright: ignore[reportCallIssue]
                df = df[
                    [
                        "symbol",
                        "date",
                        "open",
                        "high",
                        "low",
                        "close",
                        "volume",
                        "turnover",
                        "turn",
                        "is_st",
                    ]
                ]

                pending_frames.append(df)
                success += 1

                # 每 500 只股票批量写入一次，平衡内存与性能
                if len(pending_frames) >= 500:
                    total_rows += self._bulk_append(pending_frames)
                    pending_frames = []

                if (i + 1) % 100 == 0:
                    logger.info(
                        f"已处理 {i + 1}/{len(symbols)}，"
                        f"成功 {success} | 跳过 {skipped} | 失败 {failed}"
                    )

            # 写入剩余数据
            if pending_frames:
                total_rows += self._bulk_append(pending_frames)

        finally:
            bs.logout()

        self._expire_cache()
        logger.info(
            f"回填完成 — 成功: {success} | 跳过: {skipped} | 失败: {failed} | "
            f"总条数: {total_rows} | 本次查询: {query_count}"
        )

    def _bulk_append(self, frames: list[pd.DataFrame]) -> int:
        """批量追加 DataFrame 到 stock_daily，遇到重复键仅警告。"""
        if not frames:
            return 0
        df = pd.concat(frames, ignore_index=True)
        row_count = len(df)
        try:
            with self.connect() as conn:
                df.to_sql(
                    "stock_daily",
                    conn,
                    if_exists="append",
                    index=False,
                    method="multi",
                    chunksize=500,
                )
                conn.commit()
            logger.info(f"批量写入 {row_count} 条数据到 SQLite")
            return row_count
        except sqlite3.IntegrityError as exc:
            logger.warning(f"批量写入时遇到重复数据（已忽略）：{exc}")
            return 0

    # ── 股票列表 ──

    def get_all_symbols(self) -> list[str]:
        """通过 baostock 获取全市场 A 股代码列表。"""
        import baostock as bs

        lg = bs.login()
        if lg.error_code != "0":
            logger.error(f"baostock 登录失败: {lg.error_msg}")
            return []

        try:
            rs = bs.query_stock_basic(code_name="", code="")
            symbols = []
            while rs.next():
                row = rs.get_row_data()
                if len(row) < 6:
                    continue
                code = row[0]  # "sh.600000" or "sz.000001"
                status = row[4]  # "1" = 上市
                stock_type = row[5]  # "1" = 股票
                if status == "1" and stock_type == "1":
                    symbols.append(code.split(".")[1])  # 提取纯数字代码
            logger.info(f"获取股票列表完成，共 {len(symbols)} 只")
            return symbols
        except Exception as e:
            logger.error(f"获取股票列表失败: {e}")
            return []
        finally:
            bs.logout()

    def get_local_symbols(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT DISTINCT symbol FROM stock_daily").fetchall()
        return [row[0] for row in rows]
