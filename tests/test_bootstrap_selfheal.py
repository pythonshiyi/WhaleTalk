"""依赖自愈回归（bootstrap 安装后校验 + 换源重试 + 失败不静默启动）。

背景（真实缺陷）：`bootstrap.py run` 此前**丢弃 `_install` 的返回值**，无论安装成败
都直接启动；网络/镜像/代理导致部分包失败时，用户拿到一个坏环境却只看到滚动日志里的
一句警告。本测试锁定修复后的行为：
  1. 依赖仍缺失 → 不启动、退出码 2、给出可操作提示；
  2. 依赖齐全 → 正常启动；
  3. `--skip-install` 仍可显式绕过（调试/离线自带依赖）；
  4. `_selfheal_deps` 语义：齐全返回 []、offline 不换源、探测失败不误报。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import bootstrap  # noqa: E402
import deps  # noqa: E402


def _patch(monkeypatch, *, install_ok, still, launch_rec):
    monkeypatch.setattr(bootstrap, "_install", lambda *a, **k: install_ok)
    monkeypatch.setattr(bootstrap, "_selfheal_deps", lambda *a, **k: still)
    monkeypatch.setattr(bootstrap, "_launch", lambda *a, **k: launch_rec.setdefault("ok", True) or 0)


def test_missing_after_heal_blocks_launch(monkeypatch):
    rec = {}
    _patch(monkeypatch, install_ok=False, still=["openai"], launch_rec=rec)
    rc = bootstrap.main(["run", "--no-venv"])
    assert rc == 2
    assert "ok" not in rec, "核心依赖仍缺失时不得静默启动"


def test_heal_ok_launches(monkeypatch):
    rec = {}
    _patch(monkeypatch, install_ok=True, still=[], launch_rec=rec)
    bootstrap.main(["run", "--no-venv"])
    assert rec.get("ok") is True, "依赖齐全时应正常启动"


def test_skip_install_bypasses_verification(monkeypatch):
    """--skip-install：显式绕过（调试场景），不触发 _selfheal_deps。"""
    rec = {}
    called = {"heal": False}

    def _boom(*a, **k):
        called["heal"] = True
        return ["openai"]

    monkeypatch.setattr(bootstrap, "_install", lambda *a, **k: True)
    monkeypatch.setattr(bootstrap, "_selfheal_deps", _boom)
    monkeypatch.setattr(bootstrap, "_launch", lambda *a, **k: rec.setdefault("ok", True) or 0)
    bootstrap.main(["run", "--no-venv", "--skip-install"])
    assert called["heal"] is False, "--skip-install 不应做安装后校验"
    assert rec.get("ok") is True


# ── _selfheal_deps 语义 ────────────────────────────────
def test_selfheal_returns_empty_when_complete(monkeypatch):
    monkeypatch.setattr(bootstrap, "_missing_in", lambda python: [])
    assert bootstrap._selfheal_deps(sys.executable, None) == []


def test_selfheal_probe_failure_not_reported(monkeypatch):
    """探测失败（None）不应误报为缺失（返回 []）。"""
    monkeypatch.setattr(bootstrap, "_missing_in", lambda python: None)
    assert bootstrap._selfheal_deps(sys.executable, None) == []


def test_selfheal_offline_no_mirror_switch(monkeypatch):
    """offline 模式不换源，直接返回缺失项。"""
    monkeypatch.setattr(bootstrap, "_missing_in", lambda python: ["openai"])
    called = {"switch": False}
    monkeypatch.setattr(deps, "pip_install_from",
                        lambda *a, **k: called.__setitem__("switch", True) or True)
    out = bootstrap._selfheal_deps(sys.executable, None, offline=True)
    assert out == ["openai"] and called["switch"] is False


def test_selfheal_switches_mirror_when_online(monkeypatch):
    """在线模式：对缺失项换官方源重试。"""
    seq = iter([["openai"], []])  # 首次缺失，换源后再校验齐全
    monkeypatch.setattr(bootstrap, "_missing_in", lambda python: next(seq))
    used = []
    monkeypatch.setattr(deps, "pip_install_from",
                        lambda name, mirror=None, on_line=None, python=None: used.append(mirror) or True)
    out = bootstrap._selfheal_deps(sys.executable, None, offline=False)
    assert out == []
    assert used and used[0] == deps.OFFICIAL_PYPI
