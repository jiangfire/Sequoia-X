"""策略工具函数。"""

from __future__ import annotations


def get_limit_pct(symbol: str, is_st: bool | None = None) -> float:
    """返回股票日内涨跌幅限制比例（相对前收盘价）。

    规则依据 2026-07-06 起施行的新版交易规则：
    - 沪深主板（60/00/000/001/002/003/004 开头）：±10%，含 ST/*ST
    - 创业板（300/301 开头）：±20%
    - 科创板（688 开头）：±20%
    - 北交所（4/8/920 开头）：±30%

    Args:
        symbol: 纯数字 6 位股票代码。
        is_st: 是否 ST/*ST。当前规则下 ST 状态不影响任何板块的阈值，
            保留此参数供未来规则扩展（例如创业板 ST 若单独调整）。

    Returns:
        涨跌幅限制比例，如 0.10、0.20、0.30。
    """
    if symbol.startswith(("30", "688")):
        return 0.20
    if symbol.startswith(("4", "8", "920")):
        return 0.30
    return 0.10
