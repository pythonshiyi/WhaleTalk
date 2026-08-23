# -*- coding: utf-8 -*-
"""应用型插件（.wtplugin v2 · app）执行器。

应用型插件 = 自带 Python 代码的"应用"（区别于底层工具）：
- 安装时代码写入 plugins/<slug>/，卸载时整目录删除 → 可插拔、零残留
- 通过触发词（/名称 或 @名称 + 可选参数文本）直接调用
- 入口 entry 两种格式：
    "module:func"          → 文件 <code_dir>/module.py 内 func（文件级加载，隔离）
    "pkg:module:func"      → 包 <code_dir>/pkg/module.py 内 func（包内可互相 import）
"""
import importlib
import importlib.util
import logging
import os
import sys

import plugins as plugins_mod

logger = logging.getLogger("whaletalk.plugin_app")


def list_app_plugins(plugins_dir):
    """枚举已启用应用型插件：[{slug, name, description, triggers, entry, param}]。"""
    out = []
    for p in plugins_mod.list_plugins(plugins_dir):
        if not p.get("enabled", True):
            continue
        contents = p.get("contents") or {}
        app = contents.get("app")
        if not isinstance(app, dict):
            continue
        meta = p.get("meta") or {}
        triggers = meta.get("triggers") or []
        if meta.get("trigger"):
            triggers = [meta["trigger"]] + [t for t in triggers if t != meta["trigger"]]
        if not triggers:
            continue
        out.append({
            "slug": p.get("slug") or "",
            "name": str(meta.get("name") or ""),
            "description": str(meta.get("description") or ""),
            "triggers": [str(t) for t in triggers if str(t).strip()],
            "entry": str(app.get("entry") or ""),
            "param": str(app.get("param") or ""),
            "icon": str(meta.get("icon") or "🧩"),
        })
    return out


def _match_trigger(text, plugins_dir):
    """匹配触发词：输入以 /名称 或 @名称 开头。返回 (plugin_dict, arg_text) 或 (None, None)。"""
    t = str(text or "").strip()
    if not t or not t[0] in ("/", "@"):
        return None, None
    for p in list_app_plugins(plugins_dir):
        for trig in p["triggers"]:
            trig = str(trig).strip()
            if not trig or trig[0] not in ("/", "@"):
                continue
            if t == trig:
                return p, ""
            if t.startswith(trig + " ") or t.startswith(trig + "\n"):
                return p, t[len(trig):].strip()
    return None, None


# 已加载插件入口指纹：slug -> (pkg, mod_name, 入口文件 mtime)
# 只在首次加载 / 插件更新（mtime 变化）时清除并重载模块；
# 日常重复执行复用缓存模块 → 插件内存状态（如记忆库连接）跨调用保留。
_LOADED = {}


def _purge_pkg_modules(pkg):
    for key in [k for k in list(sys.modules) if k == pkg or k.startswith(pkg + ".")]:
        sys.modules.pop(key, None)


def _load_entry(p, plugins_dir):
    """加载入口函数。返回 (callable, error)。"""
    slug = p.get("slug") or ""
    base = plugins_mod.code_dir(plugins_dir, slug)
    if not os.path.isdir(base):
        return None, f"插件代码目录不存在（{base}，请重新安装）"
    entry = str(p.get("entry") or "").strip()
    parts = entry.split(":")
    if len(parts) == 3:
        pkg, mod_name, func_name = parts
    elif len(parts) == 2:
        pkg, mod_name, func_name = "", parts[0], parts[1]
    else:
        return None, f"entry 格式错误：{entry}"
    try:
        if pkg:
            # 包式加载：自然支持包内互相 import；仅首次/更新（mtime 变化）时重载
            entry_file = os.path.join(base, pkg, mod_name + ".py")
            mtime = os.path.getmtime(entry_file) if os.path.isfile(entry_file) else 0
            if _LOADED.get(slug) != (pkg, mod_name, mtime):
                _purge_pkg_modules(pkg)
                _LOADED[slug] = (pkg, mod_name, mtime)
            sys.path.insert(0, base)
            try:
                mod = importlib.import_module(f"{pkg}.{mod_name}")
            finally:
                try:
                    sys.path.remove(base)
                except ValueError:
                    pass
        else:
            # 文件级加载：唯一模块名隔离（多插件同名模块不冲突）；mtime 变化时重载
            file = os.path.join(base, mod_name + ".py")
            if not os.path.isfile(file):
                return None, f"入口文件不存在：{file}"
            uniq = f"_xfb_{slug}_{mod_name}"
            mtime = os.path.getmtime(file)
            if _LOADED.get(slug) != ("", uniq, mtime):
                sys.modules.pop(uniq, None)
                _LOADED[slug] = ("", uniq, mtime)
            if uniq in sys.modules:
                mod = sys.modules[uniq]
            else:
                spec = importlib.util.spec_from_file_location(uniq, file)
                if spec is None or spec.loader is None:
                    return None, f"入口加载失败：{file}"
                mod = importlib.util.module_from_spec(spec)
                sys.modules[uniq] = mod
                try:
                    spec.loader.exec_module(mod)
                except Exception:
                    sys.modules.pop(uniq, None)
                    raise
        target = getattr(mod, func_name, None)
        if target is None:
            return None, f"入口函数不存在：{func_name}（{entry}）"
        return target, ""
    except Exception as e:
        logger.exception("插件入口加载失败")
        return None, f"插件入口加载失败: {e}"


def run_app_plugin(p, plugins_dir, arg_text=""):
    """执行应用型插件。arg_text 为触发词后的参数文本。返回 (ok, text)。

    兼容两种入参：list_app_plugins 的扁平结构（entry/param 在顶层），
    或 list_plugins 的原始插件结构（contents.app 嵌套）。
    """
    if not p.get("entry"):
        app = (p.get("contents") or {}).get("app") or {}
        p = dict(p)
        p["entry"] = str(app.get("entry") or "").strip()
        p["param"] = str(app.get("param") or "").strip()
        if not p.get("slug"):
            p["slug"] = plugins_mod._slug((p.get("meta") or {}).get("name") or "")
    target, err = _load_entry(p, plugins_dir)
    if target is None:
        return False, err
    try:
        param = str(p.get("param") or "").strip()
        if param:
            result = target(**{param: arg_text})
        elif arg_text.strip():
            result = target(arg_text)
        else:
            result = target()
        return True, str(result or "（插件执行完成，无输出）")
    except Exception as e:
        logger.exception("插件执行失败")
        return False, f"插件执行失败: {e}"