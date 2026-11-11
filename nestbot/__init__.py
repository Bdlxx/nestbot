"""NestBot —— 基于 OneBot v11 的可扩展机器人框架（预装 WordLib 词库插件）。

本包是阶段 1/2 引入的**核心抽象 + 内置 NoneBot 运行时**，与老的
`main.py` / `utils` 插件体系共存：

    payload ──► 协议适配器 ──► NestEvent ──► 词库插件链 ──► 内置 NoneBot
                                                   └──► transport.send_message（统一发送）

对外只暴露这几样东西：

    from nestbot import NestEvent, send_message, SendTarget, get_transport
    from nestbot import get_adapter, list_adapters      # 适配器注册表
    from nestbot.runtime import NestBotRuntime          # 需要内嵌 NoneBot 时

⚠ 本版**不改**已有代码包名 / import 名 / 配置目录（见 README 的迁移说明），
`nestbot` 只是新增的一层；`utils.*` 与 `plugins.*` 保持原样。
"""

from .adapters import (
    ONE_BOT_V11,
    ProtocolAdapter,
    detect_adapter,
    get_adapter,
    list_adapters,
    register_adapter,
)
from .events import NestEvent, event_from_payload, segments_to_text
from .transport import (
    RecordingTransport,
    SendResult,
    SendTarget,
    Transport,
    WsTransport,
    get_transport,
    send_message,
    set_transport,
)

__version__ = "0.1.0"
__all__ = [
    "NestEvent",
    "event_from_payload",
    "segments_to_text",
    "ProtocolAdapter",
    "register_adapter",
    "get_adapter",
    "detect_adapter",
    "list_adapters",
    "ONE_BOT_V11",
    "Transport",
    "WsTransport",
    "RecordingTransport",
    "SendTarget",
    "SendResult",
    "get_transport",
    "set_transport",
    "send_message",
    "__version__",
]
