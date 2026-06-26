"""高旗形整理策略：强动量后极度收敛缩量。"""

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class HighTightFlagStrategy(BaseStrategy):
    """高旗形整理策略。

    选股条件（向量化，严禁 iterrows）：
    1. 强动量：过去40天区间最高价 / 区间最低价 > 1.6（涨幅超60%）
    2. 极度收敛：最近10天区间最高价 / 区间最低价 < 1.15（振幅低于15%）
    3. 缩量：今日 volume < 过去20日 volume 均值的 0.6 倍
    """

    _MIN_BARS: int = 40  # 至少需要 40 根 K 线

    def run(self) -> list[str]:
        """
        基于全表缓存向量化计算，返回满足高旗形整理条件的股票代码列表。

        Returns:
            满足条件的股票代码列表。
        """
        cache = self.engine.ohlcv_cache
        if cache is None:
            cache = self.engine.load_ohlcv_cache()

        if cache.empty:
            return []

        df = cache.reset_index().sort_values(["symbol", "date"])

        # 按 symbol 分组滚动窗口
        df["high_40"] = df.groupby("symbol")["high"].transform(
            lambda s: s.rolling(40, min_periods=40).max()
        )
        df["low_40"] = df.groupby("symbol")["low"].transform(
            lambda s: s.rolling(40, min_periods=40).min()
        )
        df["high_10"] = df.groupby("symbol")["high"].transform(
            lambda s: s.rolling(10, min_periods=10).max()
        )
        df["low_10"] = df.groupby("symbol")["low"].transform(
            lambda s: s.rolling(10, min_periods=10).min()
        )
        # 截至昨日的 20 日均量
        df["vol_ma20_prev"] = df.groupby("symbol")["volume"].transform(
            lambda s: s.rolling(20).mean().shift(1)
        )

        latest = df.groupby("symbol").tail(1)
        latest = latest.dropna(subset=["high_40", "low_40", "high_10", "low_10", "vol_ma20_prev"])
        latest = latest[(latest["low_40"] > 0) & (latest["low_10"] > 0)]

        momentum = latest["high_40"] / latest["low_40"] > 1.6
        consolidation = latest["high_10"] / latest["low_10"] < 1.15
        high_level = latest["low_10"] >= latest["high_40"] * 0.8
        shrink = latest["volume"] < latest["vol_ma20_prev"] * 0.6

        selected = latest[momentum & consolidation & high_level & shrink]["symbol"].tolist()

        logger.info(f"HighTightFlagStrategy 选出 {len(selected)} 只股票")
        return selected
