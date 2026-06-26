"""海龟交易策略：20日新高突破 + 成交额过亿 + 动量阳线过滤。"""

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class TurtleTradeStrategy(BaseStrategy):
    """海龟交易策略（A股防诱多改良版）。

    选股条件（向量化，严禁 iterrows）：
    1. 突破新高：今日 close > 前20个交易日 high 的最大值
    2. 流动性：今日 turnover > 100,000,000
    3. 防诱多过滤：今日必须是实体阳线（今日 close > 今日 open），且必须真涨（今日 close > 昨日 close）
    """

    _MIN_BARS: int = 21  # 至少需要 21 根 K 线（20日窗口 + 当日）

    def run(self) -> list[str]:
        """
        基于全表缓存向量化计算，返回满足海龟突破条件的股票代码列表。
        """
        cache = self.engine.ohlcv_cache
        if cache is None:
            cache = self.engine.load_ohlcv_cache()

        if cache.empty:
            return []

        df = cache.reset_index().sort_values(["symbol", "date"])

        # 前20日 high 的滚动最大值（不含当日）
        df["high_20"] = df.groupby("symbol")["high"].transform(
            lambda s: s.shift(1).rolling(20).max()
        )
        df["prev_close"] = df.groupby("symbol")["close"].shift(1)

        # 取每只股票最新一天
        latest = df.groupby("symbol").tail(1)
        latest = latest.dropna(subset=["high_20"])

        breakout = latest["close"] > latest["high_20"]
        liquid = latest["turnover"] > 100_000_000
        is_yang = latest["close"] > latest["open"]
        is_up = latest["close"] > latest["prev_close"]

        candidates_df = latest[breakout & liquid & is_yang & is_up].copy()

        # 按流通市值从大到小排序：close * volume / (turn / 100)
        if not candidates_df.empty:
            candidates_df["market_cap"] = 0.0
            valid = candidates_df["turn"] > 0
            candidates_df.loc[valid, "market_cap"] = (
                candidates_df.loc[valid, "volume"]
                / (candidates_df.loc[valid, "turn"] / 100)
                * candidates_df.loc[valid, "close"]
            )
            candidates_df = candidates_df.sort_values("market_cap", ascending=False)

        candidates = candidates_df["symbol"].tolist()

        logger.info(f"TurtleTradeStrategy 选出 {len(candidates)} 只股票")
        return candidates
