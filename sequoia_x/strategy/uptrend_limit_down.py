"""上升趋势跌停策略：趋势中放量跌停，捕捉错杀机会。"""

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy._utils import get_limit_pct
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class UptrendLimitDownStrategy(BaseStrategy):
    """上升趋势跌停策略。

    选股条件（向量化，严禁 iterrows）：
    1. 处于上升趋势：昨日20日均线 > 昨日60日均线
    2. 放量跌停：今日 close <= 昨日 close * (1 - 板块涨跌幅限制)
                 且今日 volume > 20日均量的 2.0 倍
    """

    _MIN_BARS: int = 60  # 至少需要 60 根 K 线（60日均线）

    def run(self) -> list[str]:
        """
        基于全表缓存向量化计算，返回满足上升趋势跌停条件的股票代码列表。

        Returns:
            满足条件的股票代码列表。
        """
        cache = self.engine.ohlcv_cache
        if cache is None:
            cache = self.engine.load_ohlcv_cache()

        if cache.empty:
            return []

        df = cache.reset_index().sort_values(["symbol", "date"])

        # 按 symbol 分组滚动均线
        df["ma20"] = df.groupby("symbol")["close"].transform(lambda s: s.rolling(20).mean())
        df["ma60"] = df.groupby("symbol")["close"].transform(lambda s: s.rolling(60).mean())
        df["vol_ma20"] = df.groupby("symbol")["volume"].transform(lambda s: s.rolling(20).mean())
        df["prev_ma20"] = df.groupby("symbol")["ma20"].shift(1)
        df["prev_ma60"] = df.groupby("symbol")["ma60"].shift(1)
        df["prev_close"] = df.groupby("symbol")["close"].shift(1)

        latest = df.groupby("symbol").tail(1)
        latest = latest.dropna(subset=["prev_ma20", "prev_ma60", "vol_ma20", "prev_close"])

        # 按板块动态阈值
        latest["limit_pct"] = latest["symbol"].map(get_limit_pct)

        uptrend = latest["prev_ma20"] > latest["prev_ma60"]
        limit_down = latest["close"] <= latest["prev_close"] * (1 - latest["limit_pct"] + 0.005)
        volume_surge = latest["volume"] > latest["vol_ma20"] * 2.0

        selected = latest[uptrend & limit_down & volume_surge]["symbol"].tolist()

        logger.info(f"UptrendLimitDownStrategy 选出 {len(selected)} 只股票")
        return selected
