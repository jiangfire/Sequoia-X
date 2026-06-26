"""策略引擎属性测试。"""

import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from hypothesis import given
from hypothesis import settings as h_settings
from hypothesis import strategies as st

from sequoia_x.core.config import Settings
from sequoia_x.data.engine import DataEngine
from sequoia_x.strategy.limit_up_shakeout import LimitUpShakeoutStrategy
from sequoia_x.strategy.ma_volume import MaVolumeStrategy
from sequoia_x.strategy.rps_breakout import RpsBreakoutStrategy
from sequoia_x.strategy.turtle_trade import TurtleTradeStrategy
from sequoia_x.strategy.uptrend_limit_down import UptrendLimitDownStrategy


def _make_engine(rows: list[dict]) -> tuple[DataEngine, Settings]:
    # 注意：return 会触发 with 退出，临时目录随后被清理。
    # 这里安全是因为数据已通过 load_ohlcv_cache() 加载到内存缓存，
    # 策略运行时只读缓存不回访文件。若未来策略 fallback 到数据库查询，需改用持久目录。
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
        settings = Settings(
            db_path=str(Path(tmp_dir) / f"test_{uuid.uuid4().hex}.db"),
            start_date="2024-01-01",
        )
        engine = DataEngine(settings)
        if rows:
            df = pd.DataFrame(rows)
            with engine.connect() as conn:
                df.to_sql("stock_daily", conn, if_exists="append", index=False, method="multi")
        engine.load_ohlcv_cache()
        return engine, settings


def _base_row(symbol: str, trade_date: str, close: float, **kwargs) -> dict:
    return {
        "symbol": symbol,
        "date": trade_date,
        "open": kwargs.get("open", close * 0.99),
        "high": kwargs.get("high", close * 1.01),
        "low": kwargs.get("low", close * 0.98),
        "close": close,
        "volume": kwargs.get("volume", 10000.0),
        "turnover": kwargs.get("turnover", close * 10000.0),
        "turn": kwargs.get("turn", 1.0),
        "is_st": kwargs.get("is_st", 0),
    }


# Feature: sequoia-x-v2, Property 9: 策略 run() 返回值类型正确
@given(
    symbols=st.lists(
        st.text(min_size=6, max_size=6, alphabet="0123456789"),
        min_size=0,
        max_size=3,
        unique=True,
    )
)
@h_settings(max_examples=30, deadline=None)
def test_strategy_run_returns_list_of_str(symbols: list[str]) -> None:
    """属性 9：run() 应返回 list[str]，每个元素为非空字符串。"""
    engine, settings = _make_engine([])
    with (
        patch.object(engine, "get_local_symbols", return_value=symbols),
        patch.object(engine, "get_ohlcv", return_value=pd.DataFrame()),
    ):
        strategy = MaVolumeStrategy(engine=engine, settings=settings)
        result = strategy.run()

    assert isinstance(result, list)
    assert all(isinstance(s, str) and len(s) > 0 for s in result)


def test_turtle_trade_selects_breakout_with_volume() -> None:
    """海龟策略应选出放量突破 20 日新高的股票。"""
    symbol = "600000"
    dates = pd.date_range("2024-01-01", periods=21, freq="D")
    rows = []
    for i, d in enumerate(dates):
        close = 10.0 + i * 0.1
        rows.append(_base_row(symbol, d.strftime("%Y-%m-%d"), close))
    # 第 21 天放量大涨突破
    rows[-1]["close"] = 15.0
    rows[-1]["open"] = 12.0
    rows[-1]["high"] = 15.5
    rows[-1]["volume"] = 1_000_000.0
    rows[-1]["turnover"] = 200_000_000.0

    engine, settings = _make_engine(rows)
    strategy = TurtleTradeStrategy(engine=engine, settings=settings)
    selected = strategy.run()
    assert symbol in selected


def test_limit_up_shakeout_respects_board_threshold() -> None:
    """涨停洗盘策略按板块阈值识别涨停。"""
    symbol = "300001"  # 创业板，阈值 20%
    rows = [
        _base_row(symbol, "2024-01-01", 10.0),
        _base_row(symbol, "2024-01-02", 10.0 * 1.20, high=12.0),  # 涨停
        _base_row(
            symbol,
            "2024-01-03",
            11.8,
            open=12.1,
            high=12.2,
            low=12.0,  # 不破昨日收盘价
            volume=200_000.0,
        ),  # 放量收阴
    ]

    engine, settings = _make_engine(rows)
    strategy = LimitUpShakeoutStrategy(engine=engine, settings=settings)
    selected = strategy.run()
    assert symbol in selected


def test_uptrend_limit_down_respects_board_threshold() -> None:
    """上升趋势跌停策略按板块阈值识别跌停。"""
    symbol = "688001"  # 科创板，阈值 20%
    dates = pd.date_range("2024-01-01", periods=70, freq="D")
    rows = []
    base = 10.0
    for i, d in enumerate(dates):
        close = base + i * 0.05
        rows.append(_base_row(symbol, d.strftime("%Y-%m-%d"), close))
    # 最后一天跌停
    rows[-1]["close"] = rows[-2]["close"] * 0.80
    rows[-1]["volume"] = 1_000_000.0

    engine, settings = _make_engine(rows)
    strategy = UptrendLimitDownStrategy(engine=engine, settings=settings)
    selected = strategy.run()
    assert symbol in selected


def test_rps_breakout_selects_strong_momentum() -> None:
    """RPS 策略应选出 120 日涨幅排名靠前且突破前期新高的股票。"""
    symbol = "000001"
    dates = pd.date_range("2024-01-01", periods=130, freq="D")
    rows = []
    close = 10.0
    for _i, d in enumerate(dates):
        close *= 1.01
        rows.append(_base_row(symbol, d.strftime("%Y-%m-%d"), close))
    # 最后一天的 close 等于最高价，突破 120 日新高
    rows[-1]["high"] = close * 1.02

    engine, settings = _make_engine(rows)
    strategy = RpsBreakoutStrategy(engine=engine, settings=settings)
    selected = strategy.run()
    assert symbol in selected


@pytest.mark.parametrize(
    ("symbol", "is_st", "expected_pct"),
    [
        ("600000", False, 0.10),
        ("000001", True, 0.10),
        ("300001", False, 0.20),
        ("688001", False, 0.20),
        ("920001", False, 0.30),
    ],
)
def test_limit_threshold_by_board(symbol: str, is_st: bool, expected_pct: float) -> None:
    """板块与 ST 状态决定涨跌幅阈值。"""
    from sequoia_x.strategy._utils import get_limit_pct

    assert get_limit_pct(symbol, is_st=is_st) == expected_pct
