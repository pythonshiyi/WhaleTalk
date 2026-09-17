# -*- coding: utf-8 -*-
"""代码生图 image_codegen：源码提取 / 视觉自评闭环 / 降级路径 回归。

用假 client + 假 html_render + 假 image_understand，端到端跑闭环而不依赖
网络与 playwright；真实渲染与模型调用不在单测范围。
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import permissions  # noqa: E402
import deepseek_client as dc  # noqa: E402,F401  （先完整初始化工具注册表）
import agent_tools.tool_codegen as tg  # noqa: E402


def _fake_client(html="<html><body>ok</body></html>"):
    class _Msg:
        content = html

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    class _Completions:
        def create(self, **kw):
            return _Resp()

    class _ChatNS:
        completions = _Completions()

    class _OpenAI:
        chat = _ChatNS()

    class _Client:
        model = "test-model"
        client = _OpenAI()

    return _Client()


def test_extract_html_strips_fence_and_preamble():
    assert tg._extract_html("```html\n<div>x</div>\n```") == "<div>x</div>"
    assert tg._extract_html("说明文字\n<div>y</div>") == "<div>y</div>"
    assert tg._extract_html("```\n<html>z</html>\n```") == "<html>z</html>"
    assert tg._extract_html("") == ""


def test_prompts_contain_hard_constraints():
    p = tg._author_prompt("画个火箭", "illustration", 1024, 768, "深色渐变")
    assert "1024×768" in p and "自包含" in p and "深色渐变" in p
    c = tg._critique_prompt("火箭", "icon")
    assert "verdict" in c and "score" in c


def test_brief_required():
    assert "brief 必填" in tg.image_codegen("")


def test_no_client_returns_error(monkeypatch):
    monkeypatch.setattr(tg, "get_active_client", lambda: None)
    assert "没有可用客户端" in tg.image_codegen("画个测试图")


def test_closed_loop_ok(monkeypatch, tmp_path):
    out = os.path.join(str(permissions.WORKSPACE_DIR), "codegen_ok.png")
    calls = {"render": 0, "vision": 0}

    def fake_render(html="", output="", **kw):
        calls["render"] += 1
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with open(output, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        return f"已渲染 HTML 为 {output}"

    def fake_vision(path, question=""):
        calls["vision"] += 1
        return '{"score": 8, "issues": [], "verdict": "ok"}'

    monkeypatch.setattr(tg, "get_active_client", lambda: _fake_client())
    monkeypatch.setattr(tg._dc, "html_render", fake_render)
    monkeypatch.setattr(tg._dc, "image_understand", fake_vision)

    r = tg.image_codegen(brief="画个测试图", output=out, max_rounds=2)
    assert "已用代码生成图像" in r, r
    assert os.path.exists(out)
    assert os.path.exists(os.path.splitext(out)[0] + ".html")
    assert calls["render"] == 1 and calls["vision"] == 1  # 自评 ok 即收敛


def test_max_rounds_one_skips_vision(monkeypatch):
    out = os.path.join(str(permissions.WORKSPACE_DIR), "codegen_fast.png")
    calls = {"vision": 0}

    def fake_render(html="", output="", **kw):
        os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
        with open(output, "wb") as f:
            f.write(b"\x89PNG")
        return f"已渲染 HTML 为 {output}"

    def fake_vision(path, question=""):
        calls["vision"] += 1
        return "{}"

    monkeypatch.setattr(tg, "get_active_client", lambda: _fake_client())
    monkeypatch.setattr(tg._dc, "html_render", fake_render)
    monkeypatch.setattr(tg._dc, "image_understand", fake_vision)

    r = tg.image_codegen(brief="图标", output=out, max_rounds=1)
    assert "已用代码生成图像（1 轮" in r
    assert calls["vision"] == 0


def test_render_failure_saves_source(monkeypatch):
    out = os.path.join(str(permissions.WORKSPACE_DIR), "codegen_fail.png")

    def fake_render(html="", output="", **kw):
        return "错误：HTML 渲染失败：no edge"

    monkeypatch.setattr(tg, "get_active_client", lambda: _fake_client())
    monkeypatch.setattr(tg._dc, "html_render", fake_render)

    r = tg.image_codegen(brief="画个测试图", output=out, max_rounds=2)
    assert r.startswith("错误")
    assert os.path.exists(os.path.splitext(out)[0] + ".html")
