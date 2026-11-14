"""插件图标映射的一致性 + 完整性校验。

同一张「插件 → 图标」表存在两处，必须一致：

| 位置 | 用途 |
|---|---|
| `web/ui_pages.py::PLUGIN_ICONS` | 后端渲染的仪表盘插件卡（权威） |
| `web/panel/index.html::PANEL.pluginIcons` | 侧栏插件菜单 / 老后端时的兜底（镜像） |

另外每个映射到的 symbol 都必须真的存在于 sprite（`panel.js` 的
`PLUGIN_ICON_SPRITE` + `ICON_SPRITE`），否则渲染出来是空白。

跑法：cd /root/botnest && python3 -m unittest tests.test_plugin_icons -v
"""

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(ROOT, "web")
PANEL_JS = os.path.join(WEB, "panel", "panel.js")
PANEL_HTML = os.path.join(WEB, "panel", "index.html")

if WEB not in sys.path:
    sys.path.insert(0, WEB)

import ui_pages  # noqa: E402


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _js_map():
    """从 index.html 里抠出 PANEL.pluginIcons = { ... }; 并解析成 dict。

    （映射不能放共用外壳 panel.js —— 那里禁止出现插件名，见 test_dual_end_assets）"""
    src = _read(PANEL_HTML)
    m = re.search(r"PANEL\.pluginIcons\s*=\s*\{(.*?)\};", src, re.S)
    if not m:
        raise AssertionError("index.html 里找不到 PANEL.pluginIcons 映射")
    body = m.group(1)
    body = re.sub(r"//.*", "", body)          # 去掉行尾注释
    pairs = re.findall(r"([A-Za-z0-9_]+)\s*:\s*'([^']+)'", body)
    return dict(pairs)


def _sprite_ids():
    """panel.js 里所有 symbol id（两段 sprite 合起来看）。

    ICON_SPRITE 是双引号 JS 串，属性引号在源码里是转义的（id=\"i-x\"），
    所以正则要容忍一个可选的反斜杠。"""
    src = _read(PANEL_JS)
    return set(re.findall(r'<symbol id=\\?"i-([a-z0-9-]+)', src))


class PluginIconTest(unittest.TestCase):
    def test_js_map_matches_python_map(self):
        self.assertEqual(_js_map(), ui_pages.PLUGIN_ICONS,
                         "index.html 的 PANEL.pluginIcons 与 ui_pages.PLUGIN_ICONS 不一致")

    def test_every_icon_has_symbol(self):
        ids = _sprite_ids()
        for key, icon in ui_pages.PLUGIN_ICONS.items():
            self.assertIn(icon, ids, f"插件 {key} 映射到不存在的 symbol i-{icon}")

    def test_unknown_plugin_falls_back(self):
        self.assertEqual(ui_pages.plugin_icon("not_a_plugin"), "plugin")
        self.assertEqual(ui_pages.plugin_icon(""), "plugin")
        self.assertEqual(ui_pages.plugin_icon(None), "plugin")


if __name__ == "__main__":
    unittest.main(verbosity=2)
