# 适配器注册表（阶段 1）
#
# 当前只有一个实现（OneBot v11），但核心代码只通过 `get_adapter()` 取，
# 不 import 具体适配器——阶段 5 加平台时注册一下即可。
#
# 用法：
#     from nestbot.adapters import get_adapter, register_adapter
#     adapter = get_adapter("onebot_v11")          # 取已注册的
#     register_adapter(MyAdapter(), replace=True)  # 覆盖/新增

from __future__ import annotations

from .base import ProtocolAdapter
from .onebot11 import OneBotV11Adapter

ONE_BOT_V11 = "onebot_v11"

_registry: dict[str, ProtocolAdapter] = {}


def register_adapter(adapter: ProtocolAdapter, *, replace: bool = False) -> ProtocolAdapter:
    name = adapter.name
    if name in _registry and not replace:
        raise ValueError(f"适配器已注册: {name}（如需覆盖请传 replace=True）")
    _registry[name] = adapter
    return adapter


def get_adapter(name: str = ONE_BOT_V11) -> ProtocolAdapter:
    try:
        return _registry[name]
    except KeyError:
        raise KeyError(f"未知适配器: {name!r}，已注册: {sorted(_registry)}") from None


def list_adapters() -> list[str]:
    return sorted(_registry)


def detect_adapter(payload: dict) -> ProtocolAdapter | None:
    """按 payload 形状找适配器（目前只有 v11，找不到就说找不到）。"""
    for adapter in _registry.values():
        if adapter.supports(payload):
            return adapter
    return None


register_adapter(OneBotV11Adapter())

__all__ = [
    "ONE_BOT_V11",
    "ProtocolAdapter",
    "OneBotV11Adapter",
    "register_adapter",
    "get_adapter",
    "list_adapters",
    "detect_adapter",
]
