# -*- coding: utf-8 -*-
"""面板免密登录令牌（QQ 私聊指令「面板登录」签发）。

设计要点（安全第一）：
- 只在**私聊**里把链接发给主人/管理员，绝不进群消息；
- 45 秒有效、一次性；落盘只存 sha256，明文不落盘、不进日志；
- 所有实例与面板共用同一份文件（`$ASTERCORE_HOME/data/panel_tokens.json`），
  用 fcntl 文件锁做读改写，避免两个 bot 同时签发互相覆盖。
"""
import hashlib
import json
import os
import time

try:
    import fcntl
except ImportError:          # Windows 端不跑这个模块，留个兜底
    fcntl = None


def _home():
    return os.environ.get("ASTERCORE_HOME") or "/root/botnest"


def token_file():
    return os.path.join(_home(), "data", "panel_tokens.json")


def _sha(token):
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def _load(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _prune(data, now=None):
    now = now or time.time()
    out = {}
    for k, v in (data or {}).items():
        try:
            if float(v.get("exp") or 0) > now:
                out[k] = v
        except Exception:
            continue
    return out


def _locked(fn):
    """在跨进程文件锁里执行 fn(path)，返回 fn 的返回值。"""
    path = token_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lock_path = path + ".lock"
    fh = open(lock_path, "a+")
    try:
        if fcntl:
            fcntl.flock(fh, fcntl.LOCK_EX)
        return fn(path)
    finally:
        try:
            if fcntl:
                fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()


def issue(bot_qq, role="admin", allowed=None, ttl=45):
    """签发一次性令牌；返回 (token, expires_at)。allowed="*" 表示可切换到全部实例。"""
    import secrets
    try:
        ttl = int(ttl)
    except Exception:
        ttl = 45
    ttl = max(10, min(ttl, 300))
    token = secrets.token_urlsafe(32)
    exp = time.time() + ttl
    rec = {
        "exp": exp,
        "bot_qq": str(bot_qq or ""),
        "role": role if role in ("master", "admin") else "admin",
        "allowed": allowed if allowed is not None else [str(bot_qq or "")],
    }
    key = _sha(token)

    def _do(path):
        data = _prune(_load(path))
        data[key] = rec
        _save(path, data)
        return exp

    _locked(_do)
    return token, exp


def consume(token):
    """校验并**原子删除**（一次性）；成功返回记录 dict，失败返回 None。"""
    token = (token or "").strip()
    if not token or len(token) > 128:
        return None
    key = _sha(token)
    box = {}

    def _do(path):
        data = _prune(_load(path))
        rec = data.pop(key, None)
        _save(path, data)
        if isinstance(rec, dict):
            box.update(rec)

    _locked(_do)
    return box or None


def sweep():
    """清掉所有过期令牌（面板启动时调用即可，失败不抛）。"""
    try:
        def _do(path):
            _save(path, _prune(_load(path)))
        _locked(_do)
    except Exception:
        pass
