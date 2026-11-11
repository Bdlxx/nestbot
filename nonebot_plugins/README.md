# NoneBot 插件目录

把单个 `.py` 插件文件或插件包（含 `__init__.py` 的目录）放进本目录即可由内置运行时加载。

- 实例 `config.json` 里需要 `"NESTBOT_ENABLED": true`，否则本目录不会被扫描
- 以 `_` 开头的文件/目录不会被加载（可用来临时停用：`mv foo.py _foo.py`）
- 命令前缀由 `NESTBOT_COMMAND_START`（默认 `/`）控制；超级用户默认取 `MASTER_QQ`
- 插件自己的第三方依赖需要 `pip install`（本版本不自动装依赖、不检查版本）
- 插件出异常只会记日志，不会拖垮主框架（NoneBot 隔离 + `[NoneBot]` 前缀的异常日志）

**排查“插件不触发”**

1. `<实例>/runtime.log` 搜 `[NoneBot]`：看有没有「插件目录 …: 载入 [...]」
2. 命令要带前缀（默认 `/`），或改用 `on_message` 规则
3. 同一群里词库等老插件先处理消息；若老插件 `handle()` 返回 True，NoneBot 收不到这条消息
4. 插件导入报错会在日志里出现 `Failed to import "xxx"`（NoneBot 自己隔离，不会中断启动）

示例：把 `_example_ping.py` 重命名为 `ping.py`，在开启 `NESTBOT_ENABLED` 的实例里发 `/ping`。
