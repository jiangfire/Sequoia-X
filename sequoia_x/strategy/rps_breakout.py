import numpy as np
import pandas as pd

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class RpsBreakoutStrategy(BaseStrategy):
    """RPS 极强动量突破策略"""

    rps_period: int = 120
    rps_threshold: int = 90

    def run(self) -> list[str]:
        # 优先使用全表缓存；若未加载则主动加载
        cache = self.engine.ohlcv_cache
        if cache is None:
            cache = self.engine.load_ohlcv_cache()

        if cache.empty:
            return []

        df = cache.reset_index()[["symbol", "date", "close", "high"]].copy()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values(["symbol", "date"])

        # 纵向计算涨幅
        df["close_shift"] = df.groupby("symbol")["close"].shift(self.rps_period)
        df["pct_change"] = (df["close"] - df["close_shift"]) / df["close_shift"]
        # 用 shift(1) 排除当天，计算过去 rps_period 天的最高价
        df["roll_high"] = df.groupby("symbol")["high"].transform(
            lambda s: s.shift(1)
            .rolling(window=self.rps_period, min_periods=self.rps_period // 2)
            .max()
        )

        latest_date = df["date"].max()
        latest_df = df[df["date"] == latest_date].copy()
        latest_df = latest_df.dropna(subset=["pct_change"])

        # 将 inf/-inf 替换为 NaN 后再 dropna，避免异常排名
        latest_df["pct_change"] = latest_df["pct_change"].replace([np.inf, -np.inf], np.nan)
        latest_df = latest_df.dropna(subset=["pct_change"])

        # 横向排位 (RPS)
        latest_df["rps"] = latest_df["pct_change"].rank(pct=True) * 100
        strong_stocks = latest_df[latest_df["rps"] >= self.rps_threshold].copy()

        # 突破判定：收盘价突破过去 rps_period 天最高价（不含当天）
        breakout_condition = strong_stocks["close"] >= strong_stocks["roll_high"]
        selected = strong_stocks[breakout_condition]

        logger.info(f"RpsBreakoutStrategy 选出 {len(selected)} 只股票")
        return selected["symbol"].tolist()
