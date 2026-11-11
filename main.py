import importlib
import pkgutil
import time
import websocket
import json
import threading
import sys
import os
import argparse
import plugins
import utils.ws

# ====== 命令行参数（机器人昵称 + QQ，各插件直接调用 config 模块读取）======
parser = argparse.ArgumentParser(description="NapCat 机器人")
parser.add_argument("--bot-name", default=None, help="机器人昵称（如 羽笙/依星）")
parser.add_argument("--bot-qq", default=None, help="机器人 QQ 号")
args = parser.parse_args()

from utils.config import set_cli_params
set_cli_params(bot_name=args.bot_name, bot_qq=args.bot_qq)

# ====== 日志文件 ======
LOG_FILE = os.path.join(os.path.dirname(__file__), "runtime.log")
log_file = open(LOG_FILE, "a", encoding="utf-8", buffering=1)

class Tee:
    def write(self, msg):
        log_file.write(msg)
        sys.__stdout__.write(msg)
    def flush(self):
        log_file.flush()
        sys.__stdout__.flush()

sys.stdout = Tee()
sys.stderr = Tee()
# ===========================

# WebSocket 连接地址
from utils.config import get_config as _get_cfg
from utils.command_table import build_command_table
from utils.api import send_message

# WebSocket 连接地址：**必须**写在实例 config.json 的 WS_URL 里（含 access_token）。
# 这里绝不硬编码 token —— 本仓库是公开的，写进来的凭据等于公开；
# 而且一旦进了 git 历史就删不掉（远端缓存/他人克隆），只能靠轮换作废。
WS_URL = _get_cfg("WS_URL", "ws://127.0.0.1:3001/")
if "access_token=" not in WS_URL:
    print("⚠ WS_URL 里没有 access_token：请在实例 config.json 的 WS_URL 里带上它"
          "（NapCat 开启 WS token 校验时会连不上）。")

def _is_master(user_id):
    ml = _get_cfg("MASTER_QQ", [])
    if not isinstance(ml, list):
        ml = [ml]
    return str(user_id) in [str(m) for m in ml]


def _is_admin(user_id):
    """管理员：实例 config.json 的 ADMIN_QQ（全插件通用）；老实例没配则回退词库 admins"""
    u = str(user_id)
    try:
        al = _get_cfg("ADMIN_QQ", [])
        if not isinstance(al, list):
            al = [al]
        if any(str(x).strip() == u for x in al):
            return True
    except Exception:
        pass
    try:
        import json as _json, os as _os
        wl = _json.load(open(_os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                                           "data", "wordlib_config.json"), encoding="utf-8"))
        return u in [str(x) for x in (wl.get("admins") or [])]
    except Exception:
        return False


def _panel_allowed_bots(user_id):
    """这个 QQ 在哪些实例里有管理权限（MASTER_QQ / ADMIN_QQ；老实例回退词库 admins）。

    规则（用户 2026-11-08 定）：主人/管理员一律按列表判定，不做"主人看全部"的特殊放行；
    没有权限的实例不会出现在面板切换列表里。返回实例 QQ 列表（去重保序）。
    """
    u = str(user_id)
    home = os.environ.get("ASTERCORE_HOME") or os.path.dirname(os.path.abspath(__file__))
    if os.path.isfile(os.path.join(home, "config.json")):
        home = os.path.dirname(home)      # 传进来的是实例目录（里有 config.json）→ 用它的上级
    out = []
    try:
        names = sorted(os.listdir(home))
    except Exception:
        names = []
    for name in names:
        inst = os.path.join(home, name)
        if not os.path.isdir(inst):
            continue
        try:
            with open(os.path.join(inst, "config.json"), encoding="utf-8") as f:
                cfg = json.load(f) or {}
        except Exception:
            continue
        qq = str(cfg.get("BOT_QQ") or name)
        masters = cfg.get("MASTER_QQ") or []
        admins = cfg.get("ADMIN_QQ") or []
        if not isinstance(masters, list):
            masters = [masters]
        if not isinstance(admins, list):
            admins = [admins]
        if u in [str(x) for x in masters] or u in [str(x) for x in admins]:
            out.append(qq)
            continue
        if not admins:          # 老实例：管理员只在词库 admins 里配过
            try:
                with open(os.path.join(inst, "data", "wordlib_config.json"), encoding="utf-8") as f:
                    wl = json.load(f) or {}
                if u in [str(x) for x in (wl.get("admins") or [])]:
                    out.append(qq)
            except Exception:
                pass
    seen, res = set(), []
    for q in out:
        if q not in seen:
            seen.add(q)
            res.append(q)
    return res

# 加载所有插件
plugin_handlers = []
plugin_meta_by_handler = {}  # {handler: name_cn}，用于日志标注处理插件
loaded_plugins_info = {}  # {name: {name_cn, version, desc, ...}}
for finder, name, ispkg in pkgutil.iter_modules(plugins.__path__):
    module = importlib.import_module(f"plugins.{name}")
    if hasattr(module, "handle"):
        plugin_handlers.append(module.handle)

        # 读取插件元数据（SDK 规范：模块级 __plugin_xxx__ 变量）
        meta_info = {
            "name_cn": getattr(module, "__plugin_name_cn__", None) or name,
            "name_en": getattr(module, "__plugin_name_en__", None) or name,
            "version": getattr(module, "__plugin_version__", None) or "1.0.0",
            "desc": getattr(module, "__plugin_desc__", None) or "",
            "author": getattr(module, "__plugin_author__", None) or "",
        }

        plugin_meta_by_handler[module.handle] = meta_info["name_cn"]
        loaded_plugins_info[name] = meta_info

        # 输出加载信息
        cn = meta_info["name_cn"]
        ver = meta_info["version"]
        desc = meta_info["desc"]
        if desc:
            print(f"  ✓ {cn} v{ver} — {desc}")
        else:
            print(f"  ✓ {cn} v{ver}")

# ====== 内置 NoneBot 运行时（阶段 2；config.json 的 NESTBOT_ENABLED 打开才生效）======
# 不额外起进程、不让 NapCat 多连一条 WS：NoneBot 用 none 驱动跑在本进程自己的
# asyncio 事件循环里，发送仍走 utils.api（见 nestbot/transport.py）。
_nestbot = None


def _init_nestbot():
    """按配置装配内置 NoneBot；没装依赖 / 没开开关时保持 None，行为与改动前一致。"""
    global _nestbot
    try:
        from nestbot.runtime import build_from_config
    except Exception as e:
        print(f"[NoneBot] nestbot 包不可用（{type(e).__name__}: {e}），跳过内置运行时")
        return
    try:
        _nestbot = build_from_config(
            _get_cfg, instance_root=os.path.dirname(os.path.abspath(__file__)))
    except Exception as e:
        import traceback
        print(f"[NoneBot] 装配失败: {type(e).__name__}: {e}")
        traceback.print_exc()
        _nestbot = None
        return
    if _nestbot is None:
        print("[NoneBot] 未启用（实例 config.json 里加 \"NESTBOT_ENABLED\": true 可开启）")
        return
    if _nestbot.start(timeout=60):
        print(f"[NoneBot] 内置运行时就绪，已载入插件: {_nestbot.loaded_plugins or '（无）'}")
    else:
        _nestbot = None
        print("[NoneBot] 启动失败，已跳过（老功能不受影响）")


# ====== 配置热重载：Web 端保存后通过 SIGUSR1 通知各插件重新加载配置 ======
import signal as _signal


def _reload_plugins_config(signum, frame):
    """收到 SIGUSR1 时，调用所有插件的 reload_config()（若提供）"""
    reloaded = []
    for finder, name, ispkg in pkgutil.iter_modules(plugins.__path__):
        try:
            module = importlib.import_module(f"plugins.{name}")
            fn = getattr(module, "reload_config", None)
            if callable(fn):
                fn()
                reloaded.append(name)
        except Exception as e:
            print(f"重载 {name} 配置失败: {e}")
    print(f"[配置重载] 已刷新插件配置: {reloaded}")


try:
    _signal.signal(_signal.SIGUSR1, _reload_plugins_config)
    print("[配置重载] SIGUSR1 处理器已注册（Web 保存配置后自动生效）")
except Exception as e:
    print(f"[配置重载] 注册失败: {e}")

def on_message(ws, message):
    try:
        event = json.loads(message)
        if "echo" in event:
            # 分发到已注册的 WS 回调（如 jm 撤回调度拿 message_id）
            try:
                from utils.api import handle_ws_echo
                handle_ws_echo(event)
            except Exception:
                pass
            # API 调用的响应 — 记录失败情况以便调试
            status = event.get("status")
            retcode = event.get("retcode")
            if status != "ok" or retcode != 0:
                print(f"[API响应] status={status}, retcode={retcode}, msg={event.get('message','')[:80]}")
            return

        from utils.log import log, log_msg_event

        # 消息事件：NapCat 风格接收日志（不打印完整内容）
        if event.get("post_type") == "message":
            log_msg_event(event, "接收")

        # ===== 面板登录（私聊指令）：主人/管理员 → 一次性免密链接（默认 45 秒、仅私聊发放） =====
        if event.get("post_type") == "message":
            _pl_cmd = str(_get_cfg("PANEL_LOGIN_COMMAND", "面板登录") or "面板登录").strip()
            _pl_raw = (event.get("raw_message") or "").strip()
            if _pl_raw == _pl_cmd:
                _pl_sender = event.get("user_id")
                _pl_priv = event.get("message_type") == "private"
                _pl_master = _is_master(_pl_sender)
                _pl_admin = (not _pl_master) and _is_admin(_pl_sender)
                if not _pl_priv:
                    if _pl_master or _pl_admin:
                        send_message(event, f"「{_pl_cmd}」只在私聊里发：加我好友后私聊发送即可。")
                    return
                if not (_pl_master or _pl_admin):
                    send_message(event, f"「{_pl_cmd}」仅主人/管理员可用。")
                    return
                try:
                    import utils.panel_token as _pt
                    _pl_qq = str(_get_cfg("BOT_QQ", "") or "")
                    _ttl = int(_get_cfg("PANEL_TOKEN_TTL", 45) or 45)
                    _role = "master" if _pl_master else "admin"
                    _allowed = _panel_allowed_bots(_pl_sender)
                    if not _allowed:
                        send_message(event, "你还没有任何实例的管理权限（MASTER_QQ / ADMIN_QQ 都没有你）。")
                        return
                    _tok, _exp = _pt.issue(_pl_qq, _role, _allowed, _ttl)
                    _base = str(_get_cfg("PANEL_PUBLIC_URL", "https://bot.xn--kivt1l.online") or "").rstrip("/")
                    _link = f"{_base}/panel/#t={_tok}"
                    send_message(event, f"🔗 面板登录（{_ttl} 秒内有效，仅一次）：\n{_link}\n"
                                        f"打开即登录；过期就重新发「{_pl_cmd}」。")
                    print(f"[面板登录] 已为 {_pl_sender}（{'主人' if _pl_master else '管理员'}）签发令牌，{_ttl}s 有效")
                except Exception as e:
                    send_message(event, f"面板登录链接生成失败：{type(e).__name__}")
                    print(f"[面板登录] 签发失败: {type(e).__name__} {e}")
                return

        # 主人私聊「命令表」命令
        if (event.get("post_type") == "message"
                and event.get("message_type") == "private"
                and _is_master(event.get("user_id"))):
            raw = event.get("raw_message", "").strip()
            if raw == "命令表":
                bot_name = _get_cfg("BOT_NAME", "羽笙")
                table = build_command_table(bot_name=bot_name)
                paragraphs = table.split("\n\n")
                for para in paragraphs:
                    chunk = para.strip()
                    if chunk:
                        send_message(event, chunk)
                        time.sleep(0.5)
                print(f"[命令表] 已发送 {len(paragraphs)} 段给主人")
                return

        # ===== 群状态指令（#状态）：2026-11-03 从词库插件迁到主程序，渲染成图片 =====
        # 放在插件链之前，所以词库在某个群被关掉也照样能查；也避免被伪人插件当闲聊吃掉
        if event.get("post_type") == "message":
            try:
                from utils.group_status import try_handle as _try_status
                if _try_status(event):
                    return
            except Exception as _e:
                print(f"[状态] 处理失败: {_e}")

        # ===== 消息处理管线（阶段 1/2）=====
        # WS payload → 协议适配器转 NestEvent → 词库等老插件链 → 内置 NoneBot 注入
        # 老插件先跑（字母序、首个返回 True 独占），未处理时才交给 NoneBot，
        # 现在还没有冲突策略，所以这是最保守的先后关系（阶段 3 再细化）。
        handled = False
        for handler in plugin_handlers:
            pname = plugin_meta_by_handler.get(handler, '')
            from utils.log import set_current_plugin
            set_current_plugin(pname)
            try:
                handled = handler(event)
            finally:
                set_current_plugin(None)
            if handled:
                if pname and event.get("post_type") == "message":
                    log('info', f'处理 <- 插件「{pname}」')
                break

        # NoneBot 内置运行时（NESTBOT_ENABLED 打开时才有值）
        if not handled and _nestbot is not None:
            try:
                _nestbot.submit(event)
            except Exception as e:
                print(f"[NoneBot] 事件注入失败: {type(e).__name__}: {e}")
    except Exception as e:
        import traceback
        print(f"处理事件出错: {e}")
        traceback.print_exc()  # 输出完整堆栈

def on_error(ws, error):
    print(f"连接错误: {error}")

def on_close(ws, close_status_code, close_msg):
    print("连接已关闭")
    utils.ws.ws = None  # 清空全局 WebSocket 对象

def on_open(ws):
    print("WebSocket 连接成功，等待事件...")
    utils.ws.ws = ws  # 设置全局 WebSocket 对象

if __name__ == "__main__":
    _init_nestbot()

    ws = websocket.WebSocketApp(WS_URL,
                                on_open=on_open,
                                on_message=on_message,
                                on_error=on_error,
                                on_close=on_close)
    wst = threading.Thread(target=ws.run_forever, kwargs={"reconnect": 5})
    wst.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ws.close()
        if _nestbot is not None:
            _nestbot.stop()