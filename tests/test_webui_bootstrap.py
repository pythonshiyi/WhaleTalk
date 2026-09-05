# -*- coding: utf-8 -*-
"""WebUI 开箱即用自举逻辑回归：_ensure_webui_build 的依赖判定与构建触发。

保证用户「双击启动即自动装依赖 + 自动构建」，无需手工 npm install/build。
不实际执行 npm（慢且需网络）；只验证决策逻辑正确。
"""
import json
import os

import web_app


def test_deps_stale_detects_missing_top_level_dep(tmp_path, monkeypatch):
    """缺 package.json 顶层声明的依赖时视为 stale（需自动补装）。"""
    wd = tmp_path / "webui"
    (wd / "node_modules").mkdir(parents=True)
    (wd / "node_modules" / "react").mkdir(parents=True)
    pkg = {
        "name": "t", "dependencies": {
            "react": "^19", "docx-preview": "^0.4",
        },
    }
    (wd / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    monkeypatch.setattr(web_app, "WEBUI_DIR", str(wd))
    # docx-preview 缺 → stale
    assert web_app._webui_deps_stale() is True
    # 补上 → 不再 stale（且不受 node_modules/.package-lock.json mtime 影响）
    (wd / "node_modules" / "docx-preview").mkdir(parents=True)
    assert web_app._webui_deps_stale() is False


def test_deps_stale_no_node_modules(tmp_path, monkeypatch):
    """node_modules 完全缺失时 stale=True（触发 npm install）。"""
    wd = tmp_path / "webui"
    wd.mkdir(parents=True)
    (wd / "package.json").write_text('{"name":"t","dependencies":{"react":"^19"}}', encoding="utf-8")
    monkeypatch.setattr(web_app, "WEBUI_DIR", str(wd))
    assert web_app._webui_deps_stale() is True


def test_deps_stale_scoped_package(tmp_path, monkeypatch):
    """scoped 依赖（@scope/name）检查路径 node_modules/@scope/name。"""
    wd = tmp_path / "webui"
    (wd / "node_modules" / "@scope").mkdir(parents=True)
    pkg = {"name": "t", "dependencies": {"@scope/lib": "^1"}}
    (wd / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    monkeypatch.setattr(web_app, "WEBUI_DIR", str(wd))
    # @scope/lib 缺 → stale
    assert web_app._webui_deps_stale() is True
    (wd / "node_modules" / "@scope" / "lib").mkdir()
    assert web_app._webui_deps_stale() is False


def test_needs_build_when_dist_missing(tmp_path, monkeypatch):
    """dist/index.html 缺失 → 需要构建（触发 npm run build）。"""
    wd = tmp_path / "webui"
    wd.mkdir(parents=True)
    (wd / "src").mkdir()
    monkeypatch.setattr(web_app, "WEBUI_DIR", str(wd))
    monkeypatch.setattr(web_app, "_webui_sources_mtime", lambda: 0.0)
    assert web_app._webui_needs_build() is True


def _mock_build_env(tmp_path, monkeypatch, deps_stale=False, built_dist=False):
    wd = tmp_path / "webui"
    (wd / "src").mkdir(parents=True, exist_ok=True)
    (wd / "node_modules" / "react").mkdir(parents=True, exist_ok=True)
    if built_dist:
        d = wd / "dist"
        d.mkdir(exist_ok=True)
        (d / "index.html").write_text("x", encoding="utf-8")
    (wd / "package.json").write_text(
        '{"name":"t","dependencies":{"react":"^19","docx-preview":"^0.4","pptx-preview":"^1"}}', encoding="utf-8")
    if built_dist:
        for pkg in ("docx-preview", "pptx-preview"):
            (wd / "node_modules" / pkg).mkdir(exist_ok=True)
    monkeypatch.setattr(web_app, "WEBUI_DIR", str(wd))
    monkeypatch.setattr(web_app, "WEBUI_DIR", str(wd))
    if not built_dist:
        monkeypatch.setattr(web_app, "_webui_needs_build", lambda: True)
    else:
        monkeypatch.setattr(web_app, "_webui_needs_build", lambda: False)
    monkeypatch.setattr(web_app, "_run_npm", lambda *a, **k: (True, "ok"))
    monkeypatch.setattr(web_app, "_run_npm_stream", lambda *a, **k: (True, "ok"))


def test_headless_no_window_falls_back_to_console(tmp_path, monkeypatch, capsys):
    """无 GUI（_can_show_tk False）→ 不弹进度窗，走控制台 npm 输出，正常返回。"""
    _mock_build_env(tmp_path, monkeypatch, deps_stale=False, built_dist=False)
    monkeypatch.setattr(web_app, "_can_show_tk", lambda: False)
    opened = []
    monkeypatch.setattr(web_app, "_setup_progress_window",
                        lambda *a, **k: opened.append(1) or True)
    ok, note = web_app._ensure_webui_build()
    assert ok is True, note
    assert opened == [], "headless 不应调用进度窗"
    out = capsys.readouterr().out
    assert "npm run build" in out or "构建" in out, "应走控制台输出"


def test_desktop_uses_progress_window(tmp_path, monkeypatch):
    """桌面（_can_show_tk True）→ 走友好进度窗并返回成功。"""
    _mock_build_env(tmp_path, monkeypatch, deps_stale=True, built_dist=False)
    monkeypatch.setattr(web_app, "_can_show_tk", lambda: True)
    monkeypatch.setattr(web_app, "_setup_progress_window", lambda *a, **k: True)
    ok, note = web_app._ensure_webui_build()
    assert ok is True and "进度窗" in note, note


def test_window_failure_reported(tmp_path, monkeypatch):
    """进度窗返回失败 → _ensure_webui_build 报失败（不误报成功）。"""
    _mock_build_env(tmp_path, monkeypatch, deps_stale=True, built_dist=False)
    monkeypatch.setattr(web_app, "_can_show_tk", lambda: True)
    monkeypatch.setattr(web_app, "_setup_progress_window", lambda *a, **k: False)
    ok, note = web_app._ensure_webui_build()
    assert ok is False and "失败" in note, note


def test_window_raise_falls_back(tmp_path, monkeypatch):
    """进度窗本身抛异常 → 回退控制台，不 crash。"""
    _mock_build_env(tmp_path, monkeypatch, deps_stale=False, built_dist=False)
    monkeypatch.setattr(web_app, "_can_show_tk", lambda: True)

    def _boom(*a, **k):
        raise RuntimeError("no display")
    monkeypatch.setattr(web_app, "_setup_progress_window", _boom)
    ok, note = web_app._ensure_webui_build()
    assert ok is True, note  # 回退 console 成功


def test_dist_fresh_but_deps_stale_forces_rebuild(tmp_path, monkeypatch, capsys):
    """核心回归：dist 存在且 _webui_needs_build()=False（mtime 看似够新），
    但 package.json 声明了新依赖、node_modules 缺它 → 必须强制重建，不得误跳。
    覆盖"用户增量拉代码后 dist 时间戳巧合/被保留导致界面缺新功能"的体验断层。"""
    wd = tmp_path / "webui"
    (wd / "src").mkdir(parents=True)
    (wd / "node_modules" / "react").mkdir(parents=True)
    d = wd / "dist"; d.mkdir(exist_ok=True)
    (d / "index.html").write_text("old", encoding="utf-8")
    # package.json 声明了 docx-preview，但 node_modules 缺它 → stale=True
    (wd / "package.json").write_text(
        '{"name":"t","dependencies":{"react":"^19","docx-preview":"^0.4","pptx-preview":"^1"}}',
        encoding="utf-8")
    monkeypatch.setattr(web_app, "WEBUI_DIR", str(wd))
    monkeypatch.setattr(web_app, "_webui_needs_build", lambda: False)  # mtime 看似已构建
    monkeypatch.setattr(web_app, "_can_show_tk", lambda: False)        # headless 走 console
    built = []
    monkeypatch.setattr(web_app, "_run_npm",
                        lambda args, **k: (built.append(" ".join(args)) or True, "ok"))
    ok, note = web_app._ensure_webui_build()
    assert ok is True, note
    assert built and any("build" in b for b in built), "依赖过期时即使 mtime 够新也必须重建"


def test_hard_deps_missing_returns_2tuples(monkeypatch):
    """回归：_hard_deps_missing() 必须返回 (pip包名, 显示名) 2 元组供 install_many 消费。
    曾误把 HARD_DEPS 的 (import名,pip包名,显示名) 3 元组传入 → install_many 解包 ValueError
    → GUI 初始化窗 worker 静默崩 → UI 永卡"正在准备…"。"""
    monkeypatch.setattr(web_app, "_importable", lambda name: False)
    missing = web_app._hard_deps_missing()
    assert missing, "应检测到缺失硬依赖"
    for item in missing:
        # install_many 内部 `for i,(pkg,label) in enumerate(miss)` 需恰好 2 元组
        pkg, label = item  # 若为 3 元组此处即 ValueError
        assert isinstance(pkg, str) and isinstance(label, str)
        assert pkg and label
