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
