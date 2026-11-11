"""阶段 2 最小闭环验收：一条群消息触发 NoneBot 插件，回复从本框架发出去。

跑法（需要先装 nonebot 依赖，见 requirements.txt）：

    python3 -m unittest tests.test_nestbot_embed -v

测试用一个 `RecordingTransport` 顶替真实 WS，所以**不需要 NapCat 在线**；
真正上线时同一路径把消息交给 `utils.api.ws_send`。
"""

import os
import time
import unittest

try:
    import nonebot as _nonebot_check  # noqa: F401

    HAS_NONEBOT = True
except Exception:  # 没装依赖时整套跳过，不能拖累其它测试
    HAS_NONEBOT = False

from nestbot.adapters import ONE_BOT_V11, get_adapter, list_adapters
from nestbot.events import NestEvent, segments_to_text
from nestbot.runtime import NestBotRuntime
from nestbot.transport import RecordingTransport, SendTarget, send_message

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_DIR = os.path.join(ROOT, "tests", "fixtures", "nonebot_plugins")

SELF_ID = "10001"
MASTER = "20002"
GROUP = "30003"


def _group_payload(text, *, user_id=20002, msg_id=1):
    return {
        "time": int(time.time()),
        "self_id": int(SELF_ID),
        "post_type": "message",
        "sub_type": "normal",
        "user_id": int(user_id),
        "message_type": "group",
        "message_id": msg_id,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "font": 0,
        "sender": {"user_id": int(user_id), "nickname": "tester", "card": "", "role": "member"},
        "group_id": int(GROUP),
    }


def _private_payload(text, *, user_id=20002, msg_id=2):
    return {
        "time": int(time.time()),
        "self_id": int(SELF_ID),
        "post_type": "message",
        "sub_type": "friend",
        "user_id": int(user_id),
        "message_type": "private",
        "message_id": msg_id,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "font": 0,
        "sender": {"user_id": int(user_id), "nickname": "tester"},
    }


def _fake_bridge(api, params):
    """假装是 NapCat HTTP：只认 get_group_info。"""
    if api == "get_group_info":
        return {"status": "ok", "retcode": 0, "data": {"group_name": "测试群", "group_id": params.get("group_id")}}
    return {"status": "failed", "retcode": 1404, "data": None}


@unittest.skipUnless(HAS_NONEBOT, "未安装 nonebot2（pip3 install -r requirements.txt）")
class NestBotEmbeddedRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.transport = RecordingTransport()
        cls.rt = NestBotRuntime(
            self_id=SELF_ID,
            nickname=["NestBot"],
            superusers={MASTER},
            command_start={"/"},
            plugin_dirs=[PLUGIN_DIR],
            transport=cls.transport,
            api_bridge=_fake_bridge,
            submit_timeout=10.0,
        )
        if not cls.rt.start(timeout=60):
            raise RuntimeError("内置 NoneBot 启动失败（依赖没装？见 requirements.txt）")

    @classmethod
    def tearDownClass(cls):
        cls.rt.stop()

    def setUp(self):
        self.transport.clear()
        self.transport.ok = True

    # -------- 小工具 --------
    def _wait_text(self, expect, timeout=5.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            texts = self.transport.texts()
            if any(expect in t for t in texts):
                return texts
            time.sleep(0.02)
        self.fail(f"{timeout}s 内没等到包含 {expect!r} 的回复，实际: {self.transport.texts()}")

    # -------- 1. 注册表 / 转换层 --------
    def test_adapter_registry(self):
        self.assertIn(ONE_BOT_V11, list_adapters())
        self.assertEqual(get_adapter(ONE_BOT_V11).name, "onebot_v11")

    def test_payload_to_internal_event(self):
        ev = get_adapter().to_internal(_group_payload("你好"))
        self.assertIsInstance(ev, NestEvent)
        self.assertEqual((ev.self_id, ev.user_id, ev.group_id), (SELF_ID, MASTER, GROUP))
        self.assertTrue(ev.is_group)
        self.assertEqual(ev.plain_text, "你好")
        self.assertEqual(ev.segments, [{"type": "text", "data": {"text": "你好"}}])

    def test_segments_to_text_placeholders(self):
        segs = [{"type": "text", "data": {"text": "看"}}, {"type": "image", "data": {"file": "a.png"}}]
        self.assertEqual(segments_to_text(segs), "看[图片]")

    # -------- 2. 统一发送接口 --------
    def test_send_message_unified_interface(self):
        res = send_message(SendTarget("group", group_id=GROUP), "直发", transport=self.transport)
        self.assertTrue(res)
        self.assertEqual(self.transport.texts(), ["直发"])

    # -------- 3. 最小闭环：注入群消息 → 插件回复 --------
    def test_group_message_triggers_plugin(self):
        self.assertTrue(self.rt.submit(_group_payload("/nbping")))
        self._wait_text("pong")
        target, segs = self.transport.sent[-1]
        self.assertEqual(target.message_type, "group")
        self.assertEqual(target.group_id, GROUP)
        self.assertEqual(segments_to_text(segs), "pong")

    def test_private_message_triggers_plugin(self):
        self.assertTrue(self.rt.submit(_private_payload("/nbid")))
        texts = self._wait_text("self=10001")
        target, _ = self.transport.sent[-1]
        self.assertEqual(target.message_type, "private")
        self.assertEqual(target.user_id, MASTER)
        self.assertEqual(texts[-1], f"self={SELF_ID} user={MASTER}")

    def test_bot_registered_in_nonebot(self):
        import nonebot

        bots = nonebot.get_bots()
        self.assertIn(SELF_ID, bots)
        self.assertIs(bots[SELF_ID], self.rt._builtin.bot)

    # -------- 4. API 转接 --------
    def test_whitelisted_api_goes_through_bridge(self):
        self.assertTrue(self.rt.submit(_group_payload("/nbgroup")))
        self._wait_text("测试群")

    def test_unknown_api_degrades_gracefully(self):
        self.assertTrue(self.rt.submit(_group_payload("/nbunknown")))
        texts = self._wait_text("err=")
        self.assertNotEqual(texts[-1], "err=none")

    # -------- 5. 异常隔离 --------
    def test_plugin_crash_is_isolated(self):
        self.assertTrue(self.rt.submit(_group_payload("/nbbomb")))
        time.sleep(0.3)  # 让崩溃的 task 跑完、异常处理器记录
        self.assertTrue(self.rt.ready, "插件崩溃后运行时不应退出")
        # 崩溃之后其它插件照常工作
        self.assertTrue(self.rt.submit(_group_payload("/nbping")))
        self._wait_text("pong")

    def test_send_failure_is_reported_not_fatal(self):
        self.transport.ok = False
        self.assertTrue(self.rt.submit(_group_payload("/nbping")))
        time.sleep(0.3)
        self.assertTrue(self.rt.ready, "发送失败后运行时不应退出")
        self.transport.ok = True
        self.assertTrue(self.rt.submit(_group_payload("/nbping")))
        self._wait_text("pong")

    # -------- 6. 配置装配（main.py 的开关逻辑） --------
    def test_build_from_config_disabled(self):
        from nestbot.runtime import build_from_config

        self.assertIsNone(build_from_config(lambda k, d=None: d))
        cfg = {"NESTBOT_ENABLED": "false"}
        self.assertIsNone(build_from_config(lambda k, d=None: cfg.get(k, d)))

    def test_build_from_config_enabled(self):
        from nestbot.runtime import build_from_config

        cfg = {
            "NESTBOT_ENABLED": "true",
            "BOT_QQ": "10001",
            "BOT_NAME": "NestBot",
            "MASTER_QQ": ["20002", "20003"],
            "NESTBOT_COMMAND_START": ["/", "#"],
        }
        rt = build_from_config(
            lambda k, d=None: cfg.get(k, d),
            instance_root="/tmp/inst",
        )
        self.assertIsNotNone(rt)
        self.assertEqual(rt.self_id, "10001")
        self.assertEqual(rt._builtin_args["superusers"], ("20002", "20003"))
        self.assertEqual(rt._builtin_args["command_start"], ("/", "#"))
        self.assertTrue(rt._builtin_args["plugin_dirs"][0].endswith("/nonebot_plugins"))

    # -------- 7. 不该注入的东西 --------
    def test_echo_and_non_event_payloads_ignored(self):
        self.assertFalse(self.rt.submit({"status": "ok", "retcode": 0, "echo": "x", "post_type": "message"}))
        self.assertFalse(self.rt.submit({"hello": "world"}))


if __name__ == "__main__":
    unittest.main(verbosity=2)
