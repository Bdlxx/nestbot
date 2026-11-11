# 协议适配器基类（阶段 1）
#
# 目的：把“收到的 payload 怎么变成内部事件”和“内部事件怎么变成某框架的事件”
# 这两件事从核心逻辑里拆出去。当前只有 OneBot v11 一个实现，但注册机制先立好，
# 阶段 5 加平台时不用再动 pipeline / runtime。

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..events import NestEvent


class ProtocolAdapter(ABC):
    """一个平台的协议适配器。"""

    name: str = "base"

    @abstractmethod
    def supports(self, payload: dict[str, Any]) -> bool:
        """这条 payload 归不归我处理。"""

    @abstractmethod
    def to_internal(self, payload: dict[str, Any]) -> NestEvent:
        """原始 payload → 内部统一事件。"""

    def to_nonebot_event(self, payload: dict[str, Any]) -> Any:
        """原始 payload → NoneBot 事件对象（默认不支持）。"""
        raise NotImplementedError(f"{self.name} 未实现 NoneBot 事件转换")
