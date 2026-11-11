# NestBot 统一发送接口（阶段 1）
#
# 规则：**任何插件（词库 / NoneBot / 将来其它）都只调这里的 send_message**，
# 不允许自己拼 WS 帧。当前唯一实现是把消息交给老的 `utils.api.ws_send`，
# 所以行为、日志、echo 回调与改动前完全一致。
#
# 测试/无网络环境下可以 `set_transport(RecordingTransport())` 把消息记下来。

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .events import NestEvent, _as_segments


@dataclass(frozen=True)
class SendTarget:
    """消息发往哪里（与事件解耦，便于插件在非事件上下文里主动发消息）。"""

    message_type: str  # "group" / "private"
    user_id: str = ""
    group_id: str = ""

    @classmethod
    def from_event(cls, event: NestEvent) -> "SendTarget":
        return cls(
            message_type=event.message_type,
            user_id=event.user_id,
            group_id=event.group_id,
        )

    def to_onebot(self) -> dict[str, str]:
        """老 `utils.api` 认识的 event 形状（只要 message_type/user_id/group_id）。"""
        if self.message_type == "group":
            return {"message_type": "group", "group_id": self.group_id}
        if self.message_type == "private":
            return {"message_type": "private", "user_id": self.user_id}
        raise ValueError(f"不支持的消息目标: {self.message_type!r}")

    def __str__(self) -> str:
        if self.message_type == "group":
            return f"群:{self.group_id}"
        if self.message_type == "private":
            return f"私聊:{self.user_id}"
        return "<未定>"


@dataclass
class SendResult:
    ok: bool
    message_id: Optional[int] = None
    error: str = ""

    def __bool__(self) -> bool:  # 让 `if send_message(...)` 可用
        return self.ok


class Transport(ABC):
    """发送通道抽象。一个进程同时只有一个“当前通道”。"""

    name: str = "base"

    @abstractmethod
    def send(
        self,
        target: SendTarget,
        message: Any,
        *,
        on_ok: Optional[Callable[[dict], None]] = None,
    ) -> SendResult:
        """发一条消息。`message` 可以是 str / 消息段 list。

        `on_ok` 会拿到 NapCat 的 API 回执（含 message_id），实现方可忽略。
        """

    def close(self) -> None:  # pragma: no cover - 目前无资源可释放
        pass


class WsTransport(Transport):
    """把消息交给老的 `utils.api.ws_send`（OneBot v11 WebSocket 正向连接）。

    选它作为默认实现有三个理由：
    1. 发送日志 / echo 回调 / 撤回调度全走原路径，老插件行为零变化；
    2. 本版本不新增网络连接（NapCat 仍然只连这一条 WS）；
    3. 将来换 HTTP 或换平台，只要实现新的 Transport 即可，插件不用改。
    """

    name = "onebot_v11_ws"

    # websocket-client 的 WebSocket.send 不是线程安全的；内置 NoneBot 在自己的
    # 事件循环线程里发消息，老插件仍在 WS 线程里发 —— 用一把全局锁串行化。
    _send_lock = threading.Lock()

    def send(self, target, message, *, on_ok=None) -> SendResult:
        segments = _as_segments(message)
        try:
            from utils.api import ws_send  # 延迟导入：本模块可脱离 utils 单测
        except Exception as e:  # pragma: no cover - 只有依赖缺失时走到
            return SendResult(False, error=f"utils.api 不可用: {e}")
        try:
            with self._send_lock:
                ok = ws_send(target.to_onebot(), segments, on_ok=on_ok)
        except Exception as e:
            return SendResult(False, error=f"{type(e).__name__}: {e}")
        return SendResult(bool(ok), error="" if ok else "WebSocket 未连接")


class RecordingTransport(Transport):
    """测试用：只记录，不联网。`sent` 里是 `(SendTarget, message段)`。"""

    name = "recording"

    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.sent: list[tuple[SendTarget, list[dict]]] = []
        self._lock = threading.Lock()

    def send(self, target, message, *, on_ok=None) -> SendResult:
        segments = _as_segments(message)
        with self._lock:
            self.sent.append((target, segments))
        if on_ok:
            try:
                on_ok({"status": "ok", "retcode": 0, "data": {"message_id": 1000 + len(self.sent)}})
            except Exception:
                pass
        return SendResult(self.ok)

    def texts(self) -> list[str]:
        """把已记录的消息拍平成纯文本，断言起来方便。"""
        from .events import segments_to_text
        return [segments_to_text(segs) for _, segs in self.sent]

    def clear(self) -> None:
        with self._lock:
            self.sent.clear()


_active: Optional[Transport] = None
_lock = threading.Lock()


def set_transport(transport: Optional[Transport]) -> None:
    """切换当前发送通道（None = 恢复默认 WsTransport）。"""
    global _active
    with _lock:
        _active = transport


def get_transport() -> Transport:
    global _active
    with _lock:
        if _active is None:
            _active = WsTransport()
        return _active


def send_message(
    event: NestEvent | SendTarget,
    message: Any,
    *,
    transport: Optional[Transport] = None,
    on_ok: Optional[Callable[[dict], None]] = None,
) -> SendResult:
    """**统一发送入口**。

    词库插件、NoneBot 内置运行时、将来的面板主动推送都调它。
    """
    target = event if isinstance(event, SendTarget) else SendTarget.from_event(event)
    return (transport or get_transport()).send(target, message, on_ok=on_ok)
