# nestbot —— 核心抽象 + 内置 NoneBot 运行时

本目录是「阶段 1 口子 + 阶段 2 内置运行时」的落地代码，**与老插件体系并存**：
`utils.*` / `plugins.*` / 配置目录一律没动（下个大版本再统一迁移）。

## 管线

```
WS 收到 OneBot payload
   └─► nestbot.adapters（注册表，目前只有 onebot_v11）
         ├─► to_internal()      → NestEvent（统一内部事件）
         └─► to_nonebot_event() → nonebot onebot.v11 Event
               │
main.py：老插件链（字母序，首个 handle()==True 独占）
               │ 未被处理
               └─► NestBotRuntime.submit(payload)     ← 线程安全，递进 asyncio 循环
                     └─► bot.handle_event(event)      ← NoneBot 事件分发
                           └─► 插件 bot.send(...) → call_api("send_msg")
                                 └─► nestbot.transport.send_message（统一发送接口）
                                       └─► utils.api.ws_send（沿用原通道）
```

## 模块

| 文件 | 职责 |
|---|---|
| `events.py` | `NestEvent`：不可变内部事件；`segments_to_text` 把消息段拍平成文本 |
| `transport.py` | `Transport` / `WsTransport` / `RecordingTransport`；`send_message` 是**唯一**发送入口 |
| `adapters/` | `ProtocolAdapter` 基类 + 注册表（`register_adapter` / `get_adapter` / `detect_adapter`） |
| `adapters/onebot11.py` | payload ↔ 内部事件 / NoneBot 事件（复用 `Adapter.json_to_event`，不重写协议） |
| `nonebot_runtime.py` | `nonebot.init(driver="~none")`、注册 Adapter、`EmbeddedBot`（重写 `call_api`）、插件加载、事件注入 |
| `runtime.py` | 后台事件循环 + 线程安全 `submit()`；`build_from_config()` 读 `NESTBOT_*` 配置 |

## 关键决策

1. **none 驱动**：NoneBot 不监听任何端口；NapCat 仍然只连 main.py 那一条 WS。
2. **只重写 `call_api`**：`send_msg` / `send_group_msg` / `send_private_msg` 转统一发送接口；
   白名单查询 API 走 NapCat HTTP（`asyncio.to_thread`，不阻塞循环）；其余回退 NoneBot 原生实现
   （none 驱动下会抛 `ApiNotAvailable`，插件按常规异常处理即可）。
3. **发送加锁**：内置 NoneBot 在自己的循环线程里发送，老插件在 WS 线程里发送，
   `WsTransport` 用一把全局锁串行化 `ws_send`（websocket-client 的 `send` 非线程安全）。
4. **异常隔离**：插件导入失败由 NoneBot 隔离；handler 崩溃在独立 task 里，NoneBot 记录；
   再兜一层事件循环 `exception_handler`，日志带 `[NoneBot]` 前缀。
5. **零侵入默认值**：`NESTBOT_ENABLED` 不为 true、或没装 nonebot 时，`build_from_config()`
   返回 `None`，main.py 行为与改动前完全一致。

## 已知边界（留给阶段 3+）

- `send_msg` 的 `message_id` 是「尽力而为」：等 NapCat 回执最多 2 秒，拿不到就返回 0；
  依赖 `message_id` 撤回的插件需要阶段 3 再打磨。
- 还没有命令前缀/黑白名单/优先级策略（老插件链永远先跑），也没有把 NoneBot 插件纳入
  Web 面板的「群 × 插件」开关矩阵。
- 没有做 Web 面板的插件管理页、依赖自动安装、并发压测。

## 测试

```bash
pip3 install -r requirements.txt
python3 -m unittest tests.test_nestbot_embed -v
```

测试用 `RecordingTransport` 顶替 WS，所以**不需要 NapCat 在线**；覆盖：
适配器转换、统一发送接口、群/私聊消息触发插件、`get_bot()` 可拿、白名单 API 转接、
未知 API 降级、插件崩溃隔离、发送失败不致命、echo/非事件 payload 不注入。
