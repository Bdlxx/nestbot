# OneBot v11 适配器（阶段 1 / 阶段 2）
#
# - `to_internal`：payload → `NestEvent`（给词库等老插件与框架核心用）
# - `to_nonebot_event`：payload → NoneBot 的 onebot.v11 Event（给内置运行时注入用）
#
# 转换直接复用 nonebot-adapter-onebot 的 `Adapter.json_to_event`——**不自己写**
# 事件解析（§“不重写 OneBot v11 适配器”）。

from __future__ import annotations

from typing import Any

from ..events import NestEvent, event_from_payload
from .base import ProtocolAdapter


class OneBotV11Adapter(ProtocolAdapter):
    name = "onebot_v11"

    def supports(self, payload: dict[str, Any]) -> bool:
        return isinstance(payload, dict) and "post_type" in payload

    def to_internal(self, payload: dict[str, Any]) -> NestEvent:
        return event_from_payload(payload, platform=self.name)

    def to_nonebot_event(self, payload: dict[str, Any]) -> Any:
        """原始 payload → NoneBot Event；解析失败返回 None（不抛）。"""
        from nonebot.adapters.onebot.v11 import Adapter  # 延迟导入

        try:
            return Adapter.json_to_event(payload)
        except Exception:
            return None
