"""涨停洗盘策略：昨日涨停后今日放量收阴但不破昨收。"""

from sequoia_x.core.logger import get_logger
from sequoia_x.strategy._utils import get_limit_pct
from sequoia_x.strategy.base import BaseStrategy

logger = get_logger(__name__)


class LimitUpShakeoutStrategy(BaseStrategy):
    """涨停洗盘策略。

    选股条件（向量化，严禁 iterrows）：
    1. 昨日涨停：昨日 close >= 前日 close * (1 + 板块涨跌幅限制)
    2. 今日收阴：今日 close < 今日 open
    3. 今日放量：今日 volume > 昨日 volume * 2.0
    4. 支撑不破：今日 low >= 昨日 close
    """

    _MIN_BARS: int = 3  # 至少需要 3 根 K 线（前日、昨日、今日）

    def run(self) -> list[str]:
        """
        基于全表缓存向量化计算，返回满足涨停洗盘条件的股票代码列表。

        Returns:
            满足条件的股票代码列表。
        """
        cache = self.engine.ohlcv_cache
        if cache is None:
            cache = self.engine.load_ohlcv_cache()

        if cache.empty:
            return []

        df = cache.reset_index().sort_values(["symbol", "date"])

        # 取最近三根 K 线对应的数据
        df["prev1_close"] = df.groupby("symbol")["close"].shift(1)
        df["prev2_close"] = df.groupby("symbol")["close"].shift(2)
        df["prev1_volume"] = df.groupby("symbol")["volume"].shift(1)

        latest = df.groupby("symbol").tail(1)
        latest = latest.dropna(subset=["prev1_close", "prev2_close", "prev1_volume"])

        # 按板块动态阈值
        latest["limit_pct"] = latest["symbol"].map(get_limit_pct)

        limit_up_yesterday = latest["prev1_close"] >= latest["prev2_close"] * (
            1 + latest["limit_pct"] - 0.005
        )
        bearish_today = latest["close"] < latest["open"]
        volume_surge = latest["volume"] > latest["prev1_volume"] * 2.0
        support_hold = latest["low"] >= latest["prev1_close"]

        selected = latest[limit_up_yesterday & bearish_today & volume_surge & support_hold][
            "symbol"
        ].tolist()

        logger.info(f"LimitUpShakeoutStrategy 选出 {len(selected)} 只股票")
        return selected
