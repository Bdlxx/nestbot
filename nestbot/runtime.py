# 内置运行时的进程级外壳（阶段 2）
#
# 老 main.py 用的是 `websocket.WebSocketApp` + 线程；NoneBot 必须是 asyncio。
# 这里给它们接一根线：
#
#   WS 线程 ──submit(payload)──► run_coroutine_threadsafe ──► NoneBot 事件循环
#
# 关键约定：
# - **只有一个事件循环**（NoneBot 与框架共用），线程只负责把活递进去；
# - NoneBot 初始化/插件加载都在这个循环里完成，避免跨循环用 asyncio 原语；
# - 没装 nonebot 依赖时 `build_from_config()` 返回 None，老功能照常跑（不 import 也不报错）。

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any, Iterable, Optional

from .nonebot_runtime import BuiltinNoneBot, _log, install_loop_exception_handler
from .transport import Transport, get_transport

CONFIG_KEYS = {
    "enabled": "NESTBOT_ENABLED",
    "plugins_dir": "NESTBOT_PLUGINS_DIR",
    "superusers": "NESTBOT_SUPERUSERS",
    "command_start": "NESTBOT_COMMAND_START",
    "api_whitelist": "NESTBOT_API_WHITELIST",
}


class NestBotRuntime:
    """一个实例的内置 NoneBot 运行时（后台线程 + 独占事件循环）。"""

    def __init__(
        self,
        *,
        self_id: str,
        nickname: Iterable[str] = (),
        superusers: Iterable[str] = (),
        command_start: Iterable[str] = ("/",),
        plugin_dirs: Iterable[str] = (),
        transport: Optional[Transport] = None,
        api_bridge=None,
        api_whitelist: Optional[set[str]] = None,
        submit_timeout: float = 10.0,
    ) -> None:
        self.self_id = str(self_id)
        self.submit_timeout = submit_timeout
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._builtin: Optional[BuiltinNoneBot] = None
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self._builtin_args = dict(
            self_id=self.self_id,
            nickname=tuple(nickname),
            superusers=tuple(superusers),
            command_start=tuple(command_start),
            plugin_dirs=tuple(plugin_dirs),
            transport=transport or get_transport(),
            api_bridge=api_bridge,
            api_whitelist=api_whitelist,
        )

    # -------- 生命周期 --------
    def start(self, timeout: float = 30.0) -> bool:
        """在后台线程里起事件循环并初始化 NoneBot。返回是否成功。"""
        if self._thread is not None:
            return self._ready.is_set() and self._error is None

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop
            install_loop_exception_handler(loop)
            try:
                loop.run_until_complete(self._async_startup())
            except BaseException as e:  # noqa: BLE001 - 启动失败要让主线程知道
                self._error = e
                _log("ERROR", f"启动失败: {type(e).__name__}: {e}")
                self._ready.set()
                loop.close()
                return
            self._ready.set()
            try:
                loop.run_forever()
            finally:
                try:
                    loop.run_until_complete(self._async_shutdown())
                except Exception:
                    pass
                loop.close()

        self._thread = threading.Thread(target=_runner, name=f"nestbot-{self.self_id}", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        if self._error is not None:
            return False
        return self._ready.is_set()

    async def _async_startup(self) -> None:
        self._builtin = BuiltinNoneBot(**self._builtin_args)
        # start() 里要 import nonebot / 注册 adapter / 建 Bot，全部在这个循环里完成
        self._builtin.start()
        _log("INFO", f"内置运行时就绪（QQ {self.self_id}，插件 {self._builtin.loaded_plugins or '无'}）")

    async def _async_shutdown(self) -> None:
        if self._builtin is not None:
            self._builtin.stop()

    def stop(self, timeout: float = 5.0) -> None:
        if self.loop is not None and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread = None

    @property
    def ready(self) -> bool:
        return self._ready.is_set() and self._error is None

    @property
    def loaded_plugins(self) -> list[str]:
        return list(self._builtin.loaded_plugins) if self._builtin else []

    # -------- 事件入口 --------
    def submit(self, payload: dict[str, Any]) -> bool:
        """线程安全地把一条 payload 递进 NoneBot 循环。调用方（WS 线程）不阻塞太久。"""
        if not self.ready or self.loop is None or self._builtin is None:
            return False
        if not isinstance(payload, dict) or "post_type" not in payload or "echo" in payload:
            return False
        try:
            fut = asyncio.run_coroutine_threadsafe(self._builtin.handle_payload(payload), self.loop)
        except Exception as e:
            _log("ERROR", f"提交事件失败: {type(e).__name__}: {e}")
            return False
        try:
            return bool(fut.result(timeout=self.submit_timeout))
        except Exception as e:
            _log("ERROR", f"事件处理超时/失败（{self.submit_timeout}s）: {type(e).__name__}: {e}")
            return False


# ---------------------------------------------------------------- 配置装配


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p.strip() for p in str(value).replace("，", ",").split(",") if p.strip()]


def build_from_config(get_config, *, instance_root: Optional[str] = None) -> Optional[NestBotRuntime]:
    """按实例 `config.json` 里的 `NESTBOT_*` 开关装配运行时。

    没开、或没装 nonebot 依赖 → 返回 None（调用方什么都不用做，行为与改动前一致）。
    """
    enabled = str(get_config(CONFIG_KEYS["enabled"], "") or "").strip().lower()
    if enabled not in {"1", "true", "yes", "on"}:
        return None

    try:
        import nonebot  # noqa: F401
    except Exception as e:
        _log("WARN", f"NESTBOT_ENABLED 已开启，但 nonebot 未安装（{e}）；本次跳过内置运行时")
        return None

    root = os.path.abspath(instance_root or os.getcwd())
    dir_name = str(get_config(CONFIG_KEYS["plugins_dir"], "nonebot_plugins") or "nonebot_plugins")
    plugin_dir = dir_name if os.path.isabs(dir_name) else os.path.join(root, dir_name)

    bot_name = str(get_config("BOT_NAME", "") or "")
    bot_qq = str(get_config("BOT_QQ", "") or "")

    superusers = _as_list(get_config(CONFIG_KEYS["superusers"], []))
    if not superusers:
        superusers = _as_list(get_config("MASTER_QQ", []))
    command_start = _as_list(get_config(CONFIG_KEYS["command_start"], [])) or ["/"]

    whitelist: Optional[set[str]] = None
    raw_wl = get_config(CONFIG_KEYS["api_whitelist"], None)
    if raw_wl:
        whitelist = set(_as_list(raw_wl))

    return NestBotRuntime(
        self_id=bot_qq,
        nickname=[bot_name] if bot_name else [],
        superusers=superusers,
        command_start=command_start,
        plugin_dirs=[plugin_dir],
        api_whitelist=whitelist,
    )
