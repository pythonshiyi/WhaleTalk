"""应用型插件（.wtplugin v2 `app`）执行器回归。

背景：`plugins.py` 完整支持 `contents.app`（格式/校验/安装）与 `contents.files`
（写入 `plugins/<slug>/`），但**没有执行入口**——应用型插件装上却跑不起来
（`app.entry` 被写入、被展示，却从未被调用）。本套件锁定补齐的最后一环：

- `plugin_app._module_file` 防越界；
- `load_app` 支持 `module:func` 与 `module:class:func`，按文件加载、不污染 sys.path；
- `run_plugin` 统一返回字符串；
- 端到端：sample_plugins 里的「鲸群社区 · 大脑运行态」能通过 `plugins` 校验、
  安装后 `load_app` 能拿到可调用入口；
- api_server 路由表登记 `POST /v1/plugins/run`。
"""
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402
import plugin_app  # noqa: E402
import plugins  # noqa: E402


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _make_plugin_dir(tmp_path, slug, entry, files):
    plugins_dir = str(tmp_path / "plugins")
    code_dir = os.path.join(plugins_dir, slug)
    for rel, code in files.items():
        _write(os.path.join(code_dir, *rel.split("/")), code)
    plugin = {"slug": slug, "contents": {"app": {"type": "local", "entry": entry}}}
    return plugin, plugins_dir


def test_module_file_rejects_traversal(tmp_path):
    code_dir = str(tmp_path / "plugins" / "p1")
    os.makedirs(code_dir, exist_ok=True)
    with pytest.raises(ValueError):
        plugin_app._module_file(code_dir, "..\\evil")


def test_load_app_func_entry(tmp_path):
    plugin, plugins_dir = _make_plugin_dir(
        tmp_path, "demo", "main:run",
        {"main.py": "def run(arg_text=''):\n    return 'echo:' + str(arg_text)\n"})
    fn = plugin_app.load_app(plugin, plugins_dir)
    assert callable(fn)
    assert fn("hi") == "echo:hi"


def test_load_app_class_entry(tmp_path):
    src = (
        "class App:\n"
        "    def run(self, arg_text=''):\n"
        "        return 'cls:' + str(arg_text)\n"
    )
    plugin, plugins_dir = _make_plugin_dir(tmp_path, "demo2", "main:App:run", {"main.py": src})
    assert plugin_app.run_plugin(plugin, plugins_dir, "x") == "cls:x"


def test_load_app_does_not_pollute_sys_path(tmp_path):
    plugin, plugins_dir = _make_plugin_dir(
        tmp_path, "demo3", "main:run",
        {"main.py": "def run(arg_text=''):\n    return 'ok'\n"})
    code_dir = os.path.join(plugins_dir, "demo3")
    plugin_app.load_app(plugin, plugins_dir)
    assert code_dir not in sys.path, "执行后必须移除临时 sys.path，卸载零残留"


def test_load_app_rejects_bad_entry(tmp_path):
    plugin, plugins_dir = _make_plugin_dir(
        tmp_path, "demo4", "main:run",
        {"main.py": "x = 1\n"})
    with pytest.raises(TypeError):
        plugin_app.load_app(plugin, plugins_dir)
    with pytest.raises(ValueError):
        plugin_app.load_app({"slug": "s", "contents": {"app": {"entry": "no-colon"}}}, plugins_dir)


def test_community_plugin_validates_and_installs(tmp_path):
    """实验场的「鲸群社区 · 大脑运行态」：能过校验、能安装、入口可加载。

    注意：该插件**刻意不放进 sample_plugins/**——鲸群是可选实验场（非鲸语功能），
    放进画廊会让它看起来像官方能力。它随实验本体放在 experiments/鲸群实验场/。
    """
    sample = REPO / "experiments" / "鲸群实验场" / "鲸群社区_大脑运行态.wtplugin"
    if not sample.exists():
        return  # 实验场为 gitignored、可整体删除；缺失即跳过（不视为失败）
    plugin, err = plugins.parse_plugin_file(str(sample))
    assert err == "" and plugin is not None, err
    assert (plugin.get("contents") or {}).get("app", {}).get("type") == "local"

    paths = {
        "plugins_dir": str(tmp_path / "plugins"),
        "user_tools": str(tmp_path / "user_tools.json"),
        "prompts": str(tmp_path / "prompts.json"),
        "workflows": str(tmp_path / "workflows.json"),
    }
    res = plugins.apply_plugin(plugin, paths)
    assert res.get("ok"), res
    installed = plugins.list_plugins(paths["plugins_dir"])
    target = next(p for p in installed if (p.get("meta") or {}).get("name") == plugin["meta"]["name"])
    fn = plugin_app.load_app(target, paths["plugins_dir"])
    assert callable(fn), "app.entry=brain_bridge:run 应解析为可调用入口"


def test_plugins_run_route_registered():
    assert api_server._match_post_route("/v1/plugins/run") == "_p_v1_plugins_run"
    assert hasattr(api_server._Handler, "_p_v1_plugins_run")
