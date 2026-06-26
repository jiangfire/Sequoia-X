"""均线+成交量选股策略：5日均线上穿20日均线且成交量放大。"""

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class MaVolumeStrategy(BaseStrategy):
    """均线+成交量选股策略。

    选股条件（全部向量化，严禁 iterrows）：
    1. 5日收盘均线上穿20日收盘均线（金叉）
    2. 当日成交量 > 20日均量的 1.5 倍（放量确认）
    """

    def run(self) -> list[str]:
        """
        基于全表缓存向量化计算，返回满足均线金叉+放量条件的股票代码列表。

        Returns:
            满足条件的股票代码列表。
        """
        cache = self.engine.ohlcv_cache
        if cache is None:
            cache = self.engine.load_ohlcv_cache()

        if cache.empty:
            return []

        df = cache.reset_index().sort_values(["symbol", "date"])

        # 按 symbol 分组滚动计算指标
        df["ma5"] = df.groupby("symbol")["close"].transform(lambda s: s.rolling(5).mean())
        df["ma20"] = df.groupby("symbol")["close"].transform(lambda s: s.rolling(20).mean())
        df["vol_ma20"] = df.groupby("symbol")["volume"].transform(lambda s: s.rolling(20).mean())
        df["prev_ma5"] = df.groupby("symbol")["ma5"].shift(1)
        df["prev_ma20"] = df.groupby("symbol")["ma20"].shift(1)

        # 取每只股票最新一天
        latest = df.groupby("symbol").tail(1)
        latest = latest.dropna(subset=["ma20", "prev_ma5", "prev_ma20"])

        golden_cross = (latest["prev_ma5"] < latest["prev_ma20"]) & (latest["ma5"] > latest["ma20"])
        volume_surge = latest["volume"] > latest["vol_ma20"] * 1.5
        selected = latest[golden_cross & volume_surge]["symbol"].tolist()

        logger.info(f"MaVolumeStrategy 选出 {len(selected)} 只股票")
        return selected
