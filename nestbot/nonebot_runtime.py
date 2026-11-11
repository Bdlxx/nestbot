# 内置 NoneBot 运行时（阶段 2 核心）
#
# 目标：**不额外起进程、不让 NapCat 多连一条 WS**，直接把 NoneBot 当成库跑在
# 本框架自己的 asyncio 事件循环里。
#
# 三个关键点（都已在 nonebot 2.5.0 / onebot 2.4.6 上实测）：
#
# 1. `nonebot.init(driver="~none")`
#    —— 用 none 驱动，NoneBot 自己**不监听**任何端口/WS；网络仍由老 main.py 的
#    `WebSocketApp` 负责。
# 2. `driver.register_adapter(Adapter)` + `adapter.bot_connect(bot)`
#    —— 手动注册 onebot.v11 Adapter 与一个 `EmbeddedBot` 实例，于是
#    `nonebot.get_bot()` / `bot.send()` 在插件里都能正常用。
# 3. `EmbeddedBot.call_api` 拦 `send_msg` 一族
#    —— 转交 `nestbot.transport.send_message`（统一发送接口），**不自己拼 WS 帧**；
#    其它 API 先按白名单走 NapCat HTTP，再退回 NoneBot 的原生实现。
#
# 事件注入：`await runtime.handle_payload(payload)` → `Adapter.json_to_event`
# → `bot.handle_event(event)`。

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from typing import Any, Callable, Iterable, Optional

from .events import _as_segments
from .transport import SendTarget, Transport, get_transport

# 这些 API 一律转交统一发送接口
SEND_APIS = {
    "send_msg",
    "send_group_msg",
    "send_private_msg",
    "send_group_forward_msg",
    "send_private_forward_msg",
    "send_forward_msg",
}

# 默认按需转接的查询/操作 API（走 NapCat HTTP）。阶段 3 按插件实际用到的再扩。
DEFAULT_API_WHITELIST = {
    "get_group_info",
    "get_group_list",
    "get_group_member_info",
    "get_group_member_list",
    "get_login_info",
    "get_stranger_info",
    "get_msg",
    "delete_msg",
    "send_like",
    "set_group_ban",
    "get_status",
}

_PREFIX = "[NoneBot]"


def _log(level: str, msg: str, exc: bool = False) -> None:
    print(f"{_PREFIX} {level} {msg}")
    if exc:
        traceback.print_exc()


# ---------------------------------------------------------------- 初始化


def init_nonebot(
    *,
    nickname: Iterable[str] = (),
    superusers: Iterable[str] = (),
    command_start: Iterable[str] = ("/",),
    extra_config: Optional[dict[str, Any]] = None,
):
    """初始化 NoneBot（幂等；重复调用返回已有 driver）。

    注意：`nonebot.init` 只能在本进程里真正生效一次，重复调用会拿到同一个 driver。
    """
    import nonebot

    try:
        driver = nonebot.get_driver()
        _log("DEBUG", "NoneBot 已初始化，复用现有 driver")
        return driver
    except ValueError:
        pass

    kwargs: dict[str, Any] = {
        "driver": "~none",
        # 不读 .env：配置统一由实例 config.json 提供，避免和现有部署方式打架
        "_env_file": None,
        "superusers": set(superusers or ()),
        "command_start": set(command_start or {"/"}),
        "nickname": set(nickname or ()),
        # 日志走 stdout（main.py 已经 tee 到 runtime.log）
        "log_level": "INFO",
    }
    if extra_config:
        kwargs.update(extra_config)
    nonebot.init(**kwargs)
    _log("INFO", f"NoneBot 初始化完成（driver={nonebot.get_driver().type}）")
    return nonebot.get_driver()


def register_onebot_adapter():
    """注册 nonebot-adapter-onebot 的 Adapter，并返回其实例。"""
    import nonebot
    from nonebot.adapters.onebot.v11 import Adapter

    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    adapter = nonebot.get_adapter(Adapter)
    _log("INFO", f"已注册协议适配器: {adapter.get_name()}")
    return adapter


def _api_bridge_http(api: str, params: dict[str, Any]) -> Any:
    """默认 API 桥：走 NapCat 的 HTTP 接口（不新增 WS 连接）。"""
    from utils.api import http_get

    return http_get(api, dict(params))


# ---------------------------------------------------------------- EmbeddedBot


def build_bot_class(base_bot_cls: type):
    """生成 `EmbeddedBot`：继承 OneBot v11 的 Bot，重写 `call_api`。"""
    from nonebot.adapters.onebot.v11.exception import ActionFailed, NetworkError

    class EmbeddedBot(base_bot_cls):  # type: ignore[misc, valid-type]
        """把 NoneBot 的 API 调用接到本框架上。

        - `send_msg` / `send_group_msg` / `send_private_msg` → 统一发送接口
        - 白名单里的查询类 API → NapCat HTTP（在事件循环里 `to_thread`，不阻塞）
        - 其它 → 交给 NoneBot 原生实现（none 驱动下通常抛 `ApiNotAvailable`）
        """

        def __init__(
            self,
            adapter,
            self_id: str,
            *,
            transport: Optional[Transport] = None,
            api_bridge: Optional[Callable[[str, dict], Any]] = None,
            api_whitelist: Optional[set[str]] = None,
        ) -> None:
            super().__init__(adapter, self_id)
            self.transport = transport
            self.api_bridge = api_bridge or _api_bridge_http
            self.api_whitelist = set(api_whitelist or DEFAULT_API_WHITELIST)

        # -------- 发送 --------
        def _send_target(self, api: str, data: dict) -> SendTarget:
            if api == "send_group_msg" or (
                api == "send_msg" and data.get("message_type") == "group"
            ):
                return SendTarget("group", group_id=str(data.get("group_id", "")))
            if api == "send_private_msg" or (
                api == "send_msg" and data.get("message_type") == "private"
            ):
                return SendTarget("private", user_id=str(data.get("user_id", "")))
            # 转发类：group 还是 private 由参数决定
            if data.get("group_id"):
                return SendTarget("group", group_id=str(data["group_id"]))
            return SendTarget("private", user_id=str(data.get("user_id", "")))

        async def _send_via_transport(self, api: str, data: dict) -> dict:
            target = self._send_target(api, data)
            segments = self._normalize_message(data.get("message"))

            # 尽力捕获 message_id（NapCat 回执带 echo），但不为了它拖慢发送
            loop = asyncio.get_running_loop()
            fut: asyncio.Future = loop.create_future()

            def _on_ok(resp: dict) -> None:
                def _set() -> None:
                    if not fut.done():
                        fut.set_result(resp)

                try:
                    loop.call_soon_threadsafe(_set)
                except Exception:
                    pass

            result = (self.transport or get_transport()).send(target, segments, on_ok=_on_ok)
            if not result.ok:
                _log("ERROR", f"{api} 发送失败 → {target}: {result.error}")
                raise ActionFailed(
                    status="failed", retcode=-1, message=result.error or "发送失败",
                    api=api, target=str(target),
                )

            message_id = result.message_id
            if message_id is None:
                try:
                    resp = await asyncio.wait_for(fut, timeout=2.0)
                    message_id = (resp.get("data") or {}).get("message_id")
                except Exception:
                    message_id = None
            return {"message_id": message_id if message_id is not None else 0}

        @staticmethod
        def _normalize_message(message: Any) -> list[dict]:
            """NoneBot 的 Message/MessageSegment → 纯 dict 消息段。"""
            if message is None:
                return []
            if isinstance(message, str):
                return _as_segments(message)
            segs: list[dict] = []
            for seg in message:
                stype = getattr(seg, "type", None)
                sdata = getattr(seg, "data", None)
                if stype is None:
                    segs.extend(_as_segments(str(seg)))
                else:
                    segs.append({"type": stype, "data": dict(sdata or {})})
            return segs

        # -------- API 总入口 --------
        async def call_api(self, api: str, **data: Any) -> Any:
            if api in SEND_APIS:
                return await self._send_via_transport(api, data)

            if api in self.api_whitelist and self.api_bridge is not None:
                try:
                    resp = await asyncio.to_thread(self.api_bridge, api, data)
                except Exception as e:
                    _log("WARN", f"API {api} 转接异常: {type(e).__name__}: {e}")
                    resp = None
                if isinstance(resp, dict):
                    retcode = resp.get("retcode", 0)
                    if retcode not in (0, None):
                        raise ActionFailed(**resp)
                    return resp.get("data")
                raise NetworkError(f"API {api} 无响应（NapCat HTTP 不可用？）")

            # 其它 API：交给 NoneBot 原生实现（none 驱动下会抛 ApiNotAvailable）
            _log("DEBUG", f"API {api} 未转接，回退 NoneBot 原生实现")
            return await super().call_api(api, **data)

    return EmbeddedBot


# ---------------------------------------------------------------- 运行时


class BuiltinNoneBot:
    """一个实例的“内置 NoneBot”。`start()` 必须在已运行的事件循环里调用。"""

    def __init__(
        self,
        *,
        self_id: str,
        nickname: Iterable[str] = (),
        superusers: Iterable[str] = (),
        command_start: Iterable[str] = ("/",),
        plugin_dirs: Iterable[str] = (),
        transport: Optional[Transport] = None,
        api_bridge: Optional[Callable[[str, dict], Any]] = None,
        api_whitelist: Optional[set[str]] = None,
        extra_config: Optional[dict[str, Any]] = None,
    ) -> None:
        self.self_id = str(self_id)
        self.plugin_dirs = [os.path.abspath(d) for d in plugin_dirs]
        self.loaded_plugins: list[str] = []
        self._transport = transport
        self._api_bridge = api_bridge
        self._api_whitelist = api_whitelist
        self._extra_config = extra_config
        self._nickname = nickname
        self._superusers = superusers
        self._command_start = command_start
        self.adapter = None
        self.bot = None
        self.started = False

    # -------- 生命周期 --------
    def start(self) -> None:
        import nonebot
        from nonebot.adapters.onebot.v11 import Bot as OB11Bot

        if self.started:
            return
        init_nonebot(
            nickname=self._nickname,
            superusers=self._superusers,
            command_start=self._command_start,
            extra_config=self._extra_config,
        )
        self.adapter = register_onebot_adapter()

        bot_cls = build_bot_class(OB11Bot)
        existing = nonebot.get_bots().get(self.self_id)
        if existing is not None:
            self.bot = existing
            _log("DEBUG", f"Bot {self.self_id} 已连接，复用")
        else:
            self.bot = bot_cls(
                self.adapter,
                self.self_id,
                transport=self._transport,
                api_bridge=self._api_bridge,
                api_whitelist=self._api_whitelist,
            )
            self.adapter.bot_connect(self.bot)

        if self.plugin_dirs:
            self.load_plugins(self.plugin_dirs)
        self.started = True

    def stop(self) -> None:
        import nonebot

        if not self.started or self.bot is None:
            return
        try:
            self.adapter.bot_disconnect(self.bot)
        except Exception as e:  # 已经断开就不用管
            _log("DEBUG", f"断开 Bot 时忽略: {e}")
        self.started = False

    # -------- 插件加载 --------
    def load_plugins(self, dirs: Iterable[str]) -> list[str]:
        """加载插件目录。单个插件导入失败由 NoneBot 自己隔离（只打日志）。"""
        import nonebot

        # NoneBot 用 path_to_module_name() 算模块名，是**相对 CWD** 的；
        # 同时要求 CWD 在 sys.path 里，否则带点号的父目录 import 不进来。
        cwd = os.path.abspath(os.getcwd())
        if cwd not in sys.path:
            sys.path.insert(0, cwd)

        names: list[str] = []
        for d in dirs:
            if not os.path.isdir(d):
                _log("INFO", f"插件目录不存在，跳过: {d}")
                continue
            try:
                rel = os.path.relpath(d, cwd)
            except ValueError:
                rel = d
            if rel.startswith(".."):
                _log("ERROR", f"插件目录必须在启动目录（{cwd}）之内，否则 import 不到: {d}")
                continue
            try:
                plugins = nonebot.load_plugins(d)
            except Exception as e:
                _log("ERROR", f"加载插件目录失败 {d}: {type(e).__name__}: {e}", exc=True)
                continue
            hit = sorted({p.name for p in plugins})
            names.extend(hit)
            _log("INFO", f"插件目录 {rel}: 载入 {hit or '（无）'}")
        self.loaded_plugins = sorted(set(self.loaded_plugins) | set(names))
        return self.loaded_plugins

    # -------- 事件注入 --------
    async def handle_payload(self, payload: dict[str, Any]) -> bool:
        """把一条 OneBot payload 注入 NoneBot 事件分发器。"""
        if not isinstance(payload, dict) or "post_type" not in payload:
            return False

        from .adapters import detect_adapter

        adapter = detect_adapter(payload)
        if adapter is None:
            _log("WARN", f"没有适配器能处理该 payload: {payload.get('post_type')}")
            return False
        try:
            event = adapter.to_nonebot_event(payload)
        except Exception as e:
            _log("ERROR", f"事件转换失败: {type(e).__name__}: {e}", exc=True)
            return False
        if event is None:
            _log("WARN", f"NoneBot 无法解析该事件（post_type={payload.get('post_type')}）")
            return False

        try:
            await self.bot.handle_event(event)
        except Exception as e:
            # 同步阶段的异常（规则/权限等）。插件 handler 的异常在独立 task 里，
            # 由事件循环的异常处理器兜住（见 install_loop_exception_handler）。
            _log("ERROR", f"事件分发异常: {type(e).__name__}: {e}", exc=True)
            return False
        return True


def install_loop_exception_handler(loop: asyncio.AbstractEventLoop) -> None:
    """保证单个插件的运行时崩溃不会影响主框架。

    NoneBot 的 matcher 在独立 task 里跑；没有这个处理器时 Python 只会打一行
    "Task exception was never retrieved"，这里统一记一条能定位到插件的日志。
    """
    previous = loop.get_exception_handler()

    def _handler(loop_: asyncio.AbstractEventLoop, context: dict) -> None:
        exc = context.get("exception")
        if exc is not None:
            _log("ERROR", f"插件任务异常（已隔离）: {type(exc).__name__}: {exc}", exc=True)
            return
        if previous is not None:
            previous(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(_handler)
