"""通知模块抽象基类：定义推送接口。"""

from abc import ABC, abstractmethod


class Notifier(ABC):
    """推送通知抽象基类。

    所有具体推送实现（如飞书、钉钉、邮件等）必须继承此类并实现 send() 方法。

    Attributes:
        name: 通知器名称，用于日志标识。
    """

    name: str = "default"

    @abstractmethod
    def send(self, symbols: list[str], strategy_name: str) -> None:
        """推送选股结果。

        Args:
            symbols: 选股结果代码列表。
            strategy_name: 策略名称。
        """
        ...
