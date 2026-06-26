"""数据引擎属性测试。"""

import contextlib
import sqlite3
import tempfile
import uuid
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine


def make_engine() -> tuple[DataEngine, Settings]:
    """创建使用独立共享内存数据库的 DataEngine 实例。"""
    settings = Settings(
        db_path=f"file:mem_{uuid.uuid4().hex}?mode=memory&cache=shared",
        start_date="2024-01-01",
    )
    engine = DataEngine(settings)
    return engine, settings


def make_engine_in_file(tmp_dir: str) -> tuple[DataEngine, Settings]:
    """创建使用临时文件数据库的 DataEngine 实例。"""
    settings = Settings(
        db_path=str(Path(tmp_dir) / "test.db"),
        start_date="2024-01-01",
    )
    engine = DataEngine(settings)
    return engine, settings


def _insert_rows(engine: DataEngine, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    with engine.connect() as conn:
        df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")


# Property 4: (symbol, date) 唯一约束防止重复写入
@given(
    symbol=st.text(min_size=6, max_size=6, alphabet="0123456789"),
    trade_date=st.dates(min_value=date(2024, 1, 1), max_value=date(2025, 12, 31)),
)
@h_settings(max_examples=50, deadline=None)
def test_unique_symbol_date_constraint(symbol: str, trade_date: date) -> None:
    """相同 (symbol, date) 插入两次，数据库中该组合记录数应保持为 1。"""
    engine, _ = make_engine()
    row = {
        "symbol": symbol,
        "date": str(trade_date),
        "open": 10.0,
        "high": 11.0,
        "low": 9.0,
        "close": 10.5,
        "volume": 1000.0,
        "turnover": 10500.0,
    }
    df = pd.DataFrame([row])
    with engine.connect() as conn:
        df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
        with contextlib.suppress(sqlite3.IntegrityError):
            df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
        count = conn.execute(
            "SELECT COUNT(*) FROM stock_daily WHERE symbol=? AND date=?",
            (symbol, str(trade_date)),
        ).fetchone()[0]
    assert count == 1


def test_ohlcv_cache_avoids_repeated_queries() -> None:
    """缓存加载后，get_ohlcv() 不再访问 SQLite。"""
    engine, _ = make_engine()
    _insert_rows(
        engine,
        [
            {
                "symbol": "000001",
                "date": "2024-01-01",
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000.0,
                "turnover": 10500.0,
                "turn": 1.0,
                "is_st": 0,
            }
        ],
    )

    cache = engine.load_ohlcv_cache()
    assert not cache.empty

    with patch.object(sqlite3, "connect") as mock_connect:
        df = engine.get_ohlcv("000001")
        mock_connect.assert_not_called()

    assert len(df) == 1
    assert df.iloc[-1]["close"] == 10.5


def test_sync_today_bulk_does_not_delete_other_symbols() -> None:
    """sync_today_bulk 更新某只股票时，不应删除同日期其他 symbol 的数据。"""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        engine, _ = make_engine_in_file(tmp_dir)
        base_date = "2024-01-01"
        _insert_rows(
            engine,
            [
                {
                    "symbol": "000001",
                    "date": base_date,
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.0,
                    "close": 10.5,
                    "volume": 1000.0,
                    "turnover": 10500.0,
                    "turn": 1.0,
                    "is_st": 0,
                },
                {
                    "symbol": "000002",
                    "date": base_date,
                    "open": 20.0,
                    "high": 21.0,
                    "low": 19.0,
                    "close": 20.5,
                    "volume": 2000.0,
                    "turnover": 20500.0,
                    "turn": 2.0,
                    "is_st": 0,
                },
            ],
        )

        # 模拟只更新 000001 返回新数据
        def _fake_fetch(tasks):
            return [
                [
                    "000001",
                    base_date,
                    "10.1",
                    "11.1",
                    "9.1",
                    "10.6",
                    "1001",
                    "10501",
                    "1.0",
                    "0",
                ]
            ]

        with patch("multiprocessing.Pool") as mock_pool_cls:
            mock_pool = mock_pool_cls.return_value.__enter__.return_value
            mock_pool.map.return_value = [_fake_fetch(None)]
            count = engine.sync_today_bulk()

        assert count == 1
        with engine.connect() as conn:
            rows = conn.execute(
                "SELECT symbol, close FROM stock_daily WHERE date=? ORDER BY symbol",
                (base_date,),
            ).fetchall()
        assert len(rows) == 2
        assert dict(rows)["000001"] == 10.6
        assert dict(rows)["000002"] == 20.5
