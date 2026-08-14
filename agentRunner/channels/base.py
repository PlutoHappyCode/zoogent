"""
channels/base.py — 渠道抽象
============================

渠道只负责收发与渲染，所有智能（Agent 循环、命令路由）都在 core。
新增渠道 = 继承 Channel，实现 start / send，然后在 agent.json
的 channels 节加配置并在 main.py 注册。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class IncomingMessage:
    channel: str                     # "feishu" / "terminal"
    chat_id: str
    user_id: str
    text: str
    message_id: str | None = None    # 终端渠道没有


class Channel(ABC):
    name: str = "?"

    @abstractmethod
    def start(self) -> None:
        """启动收消息循环（实现方自行决定阻塞或起线程）"""
        ...

    @abstractmethod
    def send(self, chat_id: str, text: str, meta: dict | None = None) -> None:
        """主动推送到某个会话（调度器等场景用）"""
        ...
