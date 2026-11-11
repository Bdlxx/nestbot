"""NestBot 内部事件对象（阶段 1）

把 OneBot v11 的原始 payload 收敛成框架内部的**统一事件**，之后所有下游
（词库插件、NoneBot 注入层、Web 面板统计…）都只认这一种对象。

设计约束：
- **不依赖 nonebot**：本模块只用标准库，保证词库等老插件链路在没有安装
  nonebot 的环境里照常工作。
- **不可变**：`NestEvent` 是 frozen dataclass，`message` 用 tuple 存消息段，
  避免下游误改同一份事件。
- **原始 payload 完整保留**在 `raw` 里：NoneBot 的 `Adapter.json_to_event`
  需要原始结构（`sender` / `message_id` / `raw_message` 等），老插件也还能
  拿到它。

消息段格式与 OneBot v11 一致：`[{"type": "text", "data": {"text": "hi"}}]`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# OneBot v11 里表示“纯文本”的类型名
TEXT = "text"


def _as_segments(message: Any) -> list[dict]:
    """把 str / dict / list 统一成消息段列表（不做合法性校验）。"""
    if message is None:
        return []
    if isinstance(message, str):
        return [{"type": TEXT, "data": {"text": message}}]
    if isinstance(message, dict):
        return [dict(message)]
    if isinstance(message, (list, tuple)):
        return [dict(m) if isinstance(m, dict) else {"type": TEXT, "data": {"text": str(m)}}
                for m in message]
    return [{"type": TEXT, "data": {"text": str(message)}}]


def segments_to_text(message: Any) -> str:
    """消息段列表 → 便于日志/命令匹配的纯文本。

    非文本段用占位符表示：`[图片]`、`[表情]`、`[at:123]`、`[face:1]`…
    这样 `#状态` 之类的整条消息全等判断不会被图片/at 段悄悄破坏。
    """
    parts: list[str] = []
    for seg in _as_segments(message):
        stype = str(seg.get("type") or "")
        data = seg.get("data") or {}
        if stype == TEXT:
            parts.append(str(data.get("text") or ""))
        elif stype == "at":
            parts.append(f"[at:{data.get('qq', '')}]")
        elif stype == "image":
            parts.append("[图片]")
        elif stype == "face":
            parts.append(f"[face:{data.get('id', '')}]")
        elif stype == "reply":
            parts.append("[回复]")
        else:
            parts.append(f"[{stype}]")
    return "".join(parts)


@dataclass(frozen=True)
class NestEvent:
    """框架内部统一事件。

    字段尽量与 OneBot v11 对齐，但**语义固定**（不同适配器负责填充）：
    - `self_id`：机器人自己
    - `user_id`：触发者（私聊对方 / 群成员 / 操作者）
    - `group_id`：群聊才有，私聊为 ""
    - `message_type`：`group` / `private` / ""（非消息事件）
    """

    platform: str
    self_id: str
    post_type: str
    message_type: str = ""
    sub_type: str = ""
    time: int = 0
    event_id: int = 0
    user_id: str = ""
    group_id: str = ""
    message: tuple[dict, ...] = ()
    raw_message: str = ""
    sender: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)

    # ---------- 便捷判定 ----------
    @property
    def is_group(self) -> bool:
        return self.message_type == "group" and bool(self.group_id)

    @property
    def is_private(self) -> bool:
        return self.message_type == "private" and bool(self.user_id)

    @property
    def is_message(self) -> bool:
        return self.post_type == "message"

    @property
    def segments(self) -> list[dict]:
        """消息段列表（副本，调用方可安全修改）。"""
        return [dict(seg) for seg in self.message]

    @property
    def plain_text(self) -> str:
        """纯文本视图；`raw_message` 缺失时由消息段反推。"""
        return self.raw_message or segments_to_text(self.message)

    @property
    def sender_name(self) -> str:
        s = self.sender or {}
        return str(s.get("card") or s.get("nickname") or "")

    def summary(self) -> str:
        """单行摘要，只用于日志（不打印完整消息内容）。"""
        where = f"群:{self.group_id}" if self.is_group else "私聊"
        return (f"{self.platform} {self.post_type}/{self.message_type or '-'} "
                f"{where} {self.user_id}: {self.plain_text[:60]}")


def event_from_payload(payload: dict[str, Any], platform: str = "onebot_v11") -> NestEvent:
    """兜底构造器：结构完全按 OneBot v11 解析。

    正常路径走 `nestbot.adapters` 里注册的适配器；这里只是给测试/未知 payload
    一个不抛异常的最小实现。
    """
    message = payload.get("message")
    return NestEvent(
        platform=platform,
        self_id=str(payload.get("self_id", "") or ""),
        post_type=str(payload.get("post_type", "") or ""),
        message_type=str(payload.get("message_type", "") or ""),
        sub_type=str(payload.get("sub_type", "") or ""),
        time=int(payload.get("time", 0) or 0),
        event_id=int(payload.get("event_id", payload.get("message_id", 0)) or 0),
        user_id=str(payload.get("user_id", "") or ""),
        group_id=str(payload.get("group_id", "") or ""),
        message=tuple(_as_segments(message)),
        raw_message=str(payload.get("raw_message", "") or ""),
        sender=dict(payload.get("sender") or {}),
        raw=dict(payload),
    )
