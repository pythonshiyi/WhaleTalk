"""plugin_app.py —— 应用型插件（.wtplugin v2 `app`）执行器。

补齐插件体系的最后一环：`plugins.py` 负责校验/安装，本模块负责**执行**。
- 约定：`contents.app = {"type":"local","entry":"module:func" | "module:class:func"}`，
  自带代码安装到 `plugins/<slug>/`；
- 按文件加载（`spec_from_file_location`），**不污染 sys.path**，卸载零残留；
- 纯标准库；鉴权与异常兜底由调用方负责。

设计参照 `brain_api.py` 的形态：纯函数 + 统一入口，主程序只需薄薄一层调用。
"""
import contextlib
import importlib.util
import os
import sys


def _module_file(code_dir, module):
    """把 entry 的 module 段解析为 plugins/<slug>/ 下的 .py 路径（防越界）。"""
    rel = module.replace(".", os.sep) + ".py"
    base = os.path.normpath(code_dir)
    path = os.path.normpath(os.path.join(base, rel))
    if path != base and not path.startswith(base + os.sep):
        raise ValueError(f"非法模块路径：{module}")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    return path


def load_app(plugin, plugins_dir):
    """加载应用型插件，返回可调用的 `run(arg_text)->str`。"""
    app = (plugin.get("contents") or {}).get("app") or {}
    entry = str(app.get("entry") or "").strip()
    if ":" not in entry:
        raise ValueError("app.entry 缺失或格式非法（应为 module:func 或 module:class:func）")
    slug = str(plugin.get("slug") or "").strip()
    if not slug:
        raise ValueError("插件缺少 slug")
    code_dir = os.path.join(plugins_dir, slug)
    parts = entry.split(":")
    module_name, func_name = parts[0], parts[-1]
    class_name = parts[1] if len(parts) == 3 else None

    path = _module_file(code_dir, module_name)
    modname = f"_wtplugin_{slug}_{module_name}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载插件模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    added = code_dir not in sys.path
    if added:
        sys.path.insert(0, code_dir)  # 允许插件内部互相 import
    try:
        spec.loader.exec_module(mod)
    finally:
        if added:
            with contextlib.suppress(ValueError):
                sys.path.remove(code_dir)

    target = getattr(mod, class_name)() if class_name else mod
    func = getattr(target, func_name, None)
    if not callable(func):
        raise TypeError(f"入口不可调用：{entry}")
    return func


def run_plugin(plugin, plugins_dir, arg_text=""):
    """执行应用型插件，返回字符串结果。异常向上抛，由调用方兜底。"""
    return str(load_app(plugin, plugins_dir)(arg_text))
