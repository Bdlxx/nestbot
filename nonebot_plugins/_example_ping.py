"""最小 NoneBot 插件示例（文件名以 `_` 开头，默认不会被加载）。

用法：把本文件重命名为 `ping.py`（或 `_ping.py` → `ping.py`），确认实例
`config.json` 里 `NESTBOT_ENABLED=true`，重启 bot 后在群里发 `/ping`。

要点：
- 发送用 `bot.send(event, ...)`，框架会把它转成 `nestbot.transport.send_message`
  → `utils.api.ws_send`，不额外建立连接；
- 插件里 `nonebot.get_bot()` / `nonebot.get_bots()` 都能拿到本实例的 Bot；
- 插件抛异常只影响本插件（NoneBot 隔离 + `[NoneBot]` 异常日志）。
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent


ping = on_command("ping", priority=10, block=True)


@ping.handle()
async def _ping(bot, event: MessageEvent):
    await bot.send(event, f"pong（self_id={bot.self_id}）")
