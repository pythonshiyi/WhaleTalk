# -*- coding: utf-8 -*-
"""跨平台 app_manage（包管理器自动探测）纯逻辑回归测试。

覆盖：包管理器 argv 模板（winget/scoop/choco/brew 与 Linux 系统管理器）、
scoop 探测兜底（shims 目录）、_pkg_argv 未知操作返回 None、schema 中
action/source 枚举与 Python 参数一致（防止装饰器声明与实现漂移）。
探测类函数只走 shutil.which / 目录存在性检查，不执行任何安装命令。
"""
import os
import sys

# 必须先 import deepseek_client（其顶层会完整构建六层注册表并加载 agent_tools）；
# 直接 import agent_tools.tool_system 会触发 __init__ 循环导入导致 TOOLS 未注册报错
import deepseek_client as dsc  # noqa: F401

from agent_tools.tool_system import (
    _PKG_PRIORITY,
    _find_pkg_manager,
    _pkg_argv,
    _pkg_available,
    _pkg_platform_key,
    _pkg_run,
    app_manage,
)


def _first_argv(mgr, op, q=""):
    """构造 `scoop/brew/...` 伪路径执行 argv 模板。"""
    return _pkg_argv(mgr, f"/usr/bin/{mgr}" if os.name != "nt" else f"C:\\tools\\{mgr}.exe", op, q)


# ── 平台优先级表（探测顺序即文档承诺）────────────────────────────────

def test_platform_priority_tables():
    assert _PKG_PRIORITY["nt"] == ("winget", "scoop", "choco")
    assert _PKG_PRIORITY["darwin"] == ("brew",)
    assert "apt" in _PKG_PRIORITY["linux"]
    assert "dnf" in _PKG_PRIORITY["linux"]
    assert _pkg_platform_key() in _PKG_PRIORITY


# ── 各包管理器 argv 模板 ────────────────────────────────────────────

def test_winget_templates():
    argv = _first_argv("winget", "search", "vim")
    assert argv[-2:] == ["search", "vim"]
    argv = _first_argv("winget", "install", "Python.Python.3.12")
    assert argv[1:3] == ["install", "--id"]
    assert "--silent" in argv and "--accept-package-agreements" in argv
    argv = _first_argv("winget", "uninstall", "Python.Python.3.12")
    assert argv[1:3] == ["uninstall", "--id"]
    assert _first_argv("winget", "upgrade")[1] == "upgrade"


def test_scoop_templates():
    argv = _first_argv("scoop", "install", "git")
    assert argv[-2:] == ["install", "git"]  # 名称直装，无 --id
    argv = _first_argv("scoop", "uninstall", "git")
    assert argv[-2:] == ["uninstall", "git"]
    assert _first_argv("scoop", "upgrade")[1] == "status"  # list 语义：查可升级
    assert _first_argv("scoop", "search", "git")[1] == "search"


def test_choco_and_brew_templates():
    choco = _first_argv("choco", "install", "git")
    assert choco[-3:] == ["install", "git", "-y"]
    assert _first_argv("choco", "upgrade")[1] == "outdated"
    brew = _first_argv("brew", "install", "git")
    assert brew[-2:] == ["install", "git"]
    assert _first_argv("brew", "upgrade")[1] == "outdated"


def test_linux_system_templates_never_null():
    for mgr in ("apt", "dnf", "pacman", "apk"):
        for op in ("search", "install", "uninstall", "upgrade", "list"):
            argv = _first_argv(mgr, op, "vim")
            assert argv is not None, f"{mgr}.{op} 应有模板"


def test_unknown_operation_returns_none():
    assert _pkg_argv("winget", "x", "fly", "q") is None
    assert _pkg_argv("scoop", "x", "fly", "q") is None
    # 平台优先级表外的管理器无模板
    assert _pkg_argv("pipx", "x", "install", "q") is None


# ── 自动探测（只读，不执行任何安装）──────────────────────────────────

def test_available_probe_order_follows_platform():
    avail = _pkg_available()
    names = [n for n, _ in avail]
    # 探测结果必须是平台优先级表的子序列（先探测的优先生效）
    prio = _PKG_PRIORITY[_pkg_platform_key()]
    idx = {n: i for i, n in enumerate(prio)}
    ranked = sorted(idx[n] for n in names)
    assert ranked == sorted(ranked), "探测顺序应与平台优先级一致"
    assert names == [n for n in names if n in idx]


def test_find_scoop_graceful_without_install(monkeypatch, tmp_path):
    """未装 scoop 时探测应优雅返回 None；装了 shims 时应命中（目录兜底）。"""
    monkeypatch.setattr("agent_tools.tool_system._which_any", lambda *a: None)
    # 无任何安装目录 → None
    monkeypatch.delenv("SCOOP", raising=False)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path / "nohome"))
    assert _find_pkg_manager("scoop") is None
    # 构造 SCOOP 环境变量目录 shims/scoop.cmd → 命中目录兜底
    shims = tmp_path / "scoop" / "shims"
    shims.mkdir(parents=True)
    (shims / "scoop.cmd").write_text("", encoding="utf-8")
    expect_cmd = os.path.normpath(str(shims / "scoop.cmd"))
    monkeypatch.setenv("SCOOP", str(tmp_path / "scoop"))
    assert os.path.normpath(_find_pkg_manager("scoop")) == expect_cmd
    # 回归 ~/scoop 兜底分支（expanduser 伪 HOME=tmp_path，则 ~/scoop → tmp_path/scoop）
    monkeypatch.delenv("SCOOP", raising=False)
    monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path) + p.replace("~", "", 1))
    assert os.path.normpath(_find_pkg_manager("scoop")) == expect_cmd
    # 回归 shutil.which 优先路径
    monkeypatch.setattr(
        "agent_tools.tool_system._which_any",
        lambda *a: r"C:\Users\x\scoop\shims\scoop.cmd" if a == ("scoop",) else None,
    )
    assert _find_pkg_manager("scoop").endswith("scoop.cmd")


def test_schema_matches_python_signature():
    """装饰器 schema 的 action/source 枚举与实现/平台表不漂移。"""
    import inspect

    schema = next(
        t for t in dsc.TOOLS if t["function"]["name"] == "app_manage"
    )["function"]
    props = schema["parameters"]["properties"]
    assert set(props) == set(inspect.signature(app_manage).parameters)
    assert props["action"]["enum"] == ["managers", "list", "search", "install", "uninstall", "upgrade", "bootstrap"]
    for src in ("winget", "scoop", "choco", "brew", "apt", "dnf", "pacman", "apk"):
        assert src in props["source"]["enum"]
    assert set(props["source"]["enum"]) == {m for mg in _PKG_PRIORITY.values() for m in mg} | {"auto"}


# ── _pkg_run 包装：scoop .cmd 走 cmd /c（不真跑，只验 argv 包装头）──

def test_scoop_cmd_shim_wrapping(monkeypatch):
    """scoop 命中 .cmd shim 时 _pkg_run 应经 cmd /c 包装（避免 WinError 193）。"""
    calls = {}

    def fake_capture(argv, timeout):
        calls["argv"] = argv
        calls["timeout"] = timeout
        return 0, "ok"

    monkeypatch.setattr("agent_tools.tool_system._proc_capture", fake_capture)
    rc, out = _pkg_run("scoop", r"C:\Users\x\scoop\shims\scoop.cmd", "install", "git", timeout=60)
    assert rc == 0 and out == "ok"
    argv = calls["argv"]
    assert argv[0].lower().endswith("cmd.exe") or os.path.basename(argv[0]).lower() == "cmd.exe"
    assert "/c" in argv[:2]
    assert argv[2].endswith("scoop.cmd")
    assert argv[-2:] == ["install", "git"]
