"""测试用 NoneBot 插件：覆盖「触发 / 自报身份 / 崩溃隔离 / API 转接」四件事。

⚠ 这个目录**只给测试用**（tests/fixtures），不会被线上实例加载。
"""

from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent

ping = on_command("nbping", priority=5, block=True)


@ping.handle()
async def _ping(bot, event: MessageEvent):
    await bot.send(event, "pong")


ident = on_command("nbid", priority=5, block=True)


@ident.handle()
async def _ident(bot, event: MessageEvent):
    await bot.send(event, f"self={bot.self_id} user={event.get_user_id()}")


bomb = on_command("nbbomb", priority=5, block=True)


@bomb.handle()
async def _bomb(bot, event: MessageEvent):
    raise RuntimeError("intentional plugin crash")


groupinfo = on_command("nbgroup", priority=5, block=True)


@groupinfo.handle()
async def _groupinfo(bot, event: MessageEvent):
    info = await bot.call_api("get_group_info", group_id=int(event.group_id))
    await bot.send(event, f"group={info}")


unknown_api = on_command("nbunknown", priority=5, block=True)


@unknown_api.handle()
async def _unknown(bot, event: MessageEvent):
    try:
        await bot.call_api("get_cookies", domain="example.com")
    except Exception as e:  # 转接不到时应抛出协议层异常
        await bot.send(event, f"err={type(e).__name__}")
    else:
        await bot.send(event, "err=none")
