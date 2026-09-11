# -*- coding: utf-8 -*-
"""出网账本测试。

核心不变量：
- 每一次「带内容出网」都留痕（通道/目的地/字节数/内容摘要/结果）；
- 账本**默认不存明文内容**；目的地 URL 必须去掉 query（可能含 token）；
- 读操作不得被当作 egress（agent_mail list / call_api GET / 无 body 的 API 调用）；
- 留痕是旁路：任何异常都不得影响发送本身。
"""
import json

import pytest

import egress


@pytest.fixture(autouse=True)
def _clean(tmp_path):
    # 每个用例独立账本文件，避免相互污染
    egress.init(path=str(tmp_path / "egress.jsonl"), store_preview=False, reset=True)
    yield
    egress.init(path=None, store_preview=False, reset=True)


# ── 抽取与判定 ───────────────────────────────────────────────────────────

def test_send_email_recorded():
    e = egress.record("send_email", {"to": "a@b.com", "subject": "周报", "body": "本周进展"},
                      ok=True, duration=0.4)
    assert e["channel"] == "email" and e["target"] == "a@b.com"
    assert e["ok"] is True and e["bytes"] > 0 and len(e["digest"]) == 16
    assert set(e["keys"]) == {"subject", "body"}
    assert e["duration"] == 0.4


def test_url_target_query_is_stripped():
    """URL 目的地必须脱敏：query 常含 token，不该进账本。"""
    e = egress.record("send_webhook",
                      {"url": "https://hooks.example.com/x/y?token=SECRET123&a=1", "text": "hi"})
    assert "SECRET123" not in json.dumps(e, ensure_ascii=False)
    assert e["target"] == "https://hooks.example.com/x/y"


def test_content_is_not_stored_by_default():
    e = egress.record("im_send", {"channel": "ops", "title": "t", "text": "机密内容XYZ"})
    assert "preview" not in e
    assert "机密内容XYZ" not in json.dumps(e, ensure_ascii=False)   # 只留摘要
    assert e["digest"]


def test_preview_opt_in_is_truncated(tmp_path):
    egress.init(path=str(tmp_path / "e2.jsonl"), store_preview=True, reset=True)
    e = egress.record("im_send", {"channel": "ops", "text": "x" * 500})
    assert e["preview"].startswith("text=")      # 带 key 前缀，便于人读
    assert "x" * 500 not in e["preview"]          # 已截断
    assert len(e["preview"]) <= egress.PREVIEW_CHARS


def test_non_egress_tool_returns_none():
    assert egress.record("read_file", {"path": "a.txt"}) is None
    assert egress.record("search_web", {"query": "x"}) is None


def test_read_actions_are_not_egress():
    """同工具里的读操作不得被误记（agent_mail 的 list / call_api 的 GET）。"""
    assert egress.record("agent_mail", {"action": "list"}) is None
    assert egress.record("agent_mail", {"action": "send", "to": "x@y.com", "body": "hi"}) is not None
    assert egress.record("call_api", {"url": "https://a.com", "method": "GET"}) is None
    assert egress.record("call_api", {"url": "https://a.com", "method": "POST"}) is None  # 无 body
    assert egress.record("call_api", {"url": "https://a.com", "method": "POST",
                                      "json_body": {"a": 1}}) is not None
    assert egress.record("webdav", {"action": "list", "remote_path": "/x"}) is None
    assert egress.record("webdav", {"action": "upload", "remote_path": "/x",
                                    "local_path": "f.txt"}) is not None


def test_failed_send_still_recorded():
    e = egress.record("send_email", {"to": "a@b.com", "body": "hi"}, ok=False)
    assert e["ok"] is False
    assert egress.summary()["failed"] == 1


# ── 摘要 / 提示 / 落盘 ───────────────────────────────────────────────────

def test_summary_aggregates():
    egress.record("send_email", {"to": "a@b.com", "body": "x" * 100})
    egress.record("im_send", {"channel": "ops", "text": "y" * 50})
    s = egress.summary()
    assert s["count"] == 2 and s["bytes"] >= 150
    assert "a@b.com" in s["targets"] and "ops" in s["targets"]


def test_notice_below_threshold_is_empty():
    egress.record("send_email", {"to": "a@b.com", "body": "hi"})
    assert egress.notice() == ""


def test_notice_above_threshold_lists_targets():
    for i in range(egress.NOTICE_MIN_SENDS):
        egress.record("send_email", {"to": f"{i}@b.com", "body": "x" * 10})
    n = egress.notice()
    assert "出网留痕" in n and "@b.com" in n
    assert "如实告知用户" in n or "告知用户" in n


def test_ledger_persisted_as_jsonl(tmp_path):
    p = tmp_path / "led.jsonl"
    egress.init(path=str(p), reset=True)
    egress.record("send_email", {"to": "a@b.com", "body": "hi"})
    lines = p.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1 and json.loads(lines[0])["channel"] == "email"
    # 跨会话读取
    hist = egress.read_ledger()
    assert hist and hist[0]["tool"] == "send_email"


def test_never_raises_on_broken_input(tmp_path):
    egress.init(path=tmp_path / "nope" / "\x00bad" / "x.jsonl", reset=True)
    assert egress.record("send_email", None) is not None
    assert egress.record("send_email", {"to": object(), "body": object()}) is not None
    assert egress.record(None, {}) is None
    assert egress.summary()["count"] >= 1


# ── 与钩子管线的接线 ─────────────────────────────────────────────────────

def test_egress_hook_records_through_tool_pipeline():
    """经 tool_hooks 包装的工具（声明了 egress）应自动留痕——工具体零改动。"""
    import tool_hooks

    def fake_send_email(to, subject="", body=""):
        return "已发送"

    w = tool_hooks.wrap("send_email", fake_send_email, ("egress",))
    assert w("a@b.com", "周报", "本周进展") == "已发送"
    snap = egress.snapshot()
    assert snap and snap[0]["channel"] == "email" and snap[0]["target"] == "a@b.com"


def test_egress_hook_records_failure_outcome():
    import tool_hooks

    def broken(to):
        raise RuntimeError("smtp 拒绝")

    with pytest.raises(RuntimeError):
        tool_hooks.wrap("send_email", broken, ("egress",))("a@b.com")
    assert egress.snapshot()[0]["ok"] is False


def test_egress_hook_ignores_undeclared_tools():
    import tool_hooks

    tool_hooks.wrap("send_email", lambda to, body="": "ok")(  "a@b.com", "hi")
    assert egress.snapshot() == []


def test_egress_tools_declare_hook_in_registry():
    """接线必须落到真实注册表：7 个出网工具都应声明 egress。"""
    import deepseek_client as dc
    import tool_hooks
    for name in ("send_email", "publish_draft", "send_webhook", "im_send",
                 "agent_mail", "webdav", "call_api"):
        fn = dc.TOOL_CALL_MAP.get(name)
        assert fn is not None, f"{name} 未注册"
        assert "egress" in tool_hooks.declared_names(fn), f"{name} 未声明 egress 钩子"


def test_error_string_result_counts_as_not_sent():
    """本项目约定「失败返回 错误：… 而非抛异常」——账本必须如实记为未发出。"""
    import tool_hooks

    def no_smtp(to, body=""):
        return "错误：未配置邮件。请在数据目录创建 email_config.json"

    out = tool_hooks.wrap("send_email", no_smtp, ("egress",))("a@b.com", "hi")
    assert out.startswith("错误")
    snap = egress.snapshot()
    assert snap and snap[0]["ok"] is False


def test_error_prefix_only_counts_at_start():
    """正文里出现「错误」二字不算失败（只在结果开头才算）。"""
    import tool_hooks
    tool_hooks.wrap("send_email", lambda to, body="": "已发送（内容含错误示范）",
                    ("egress",))("a@b.com", "hi")
    assert egress.snapshot()[0]["ok"] is True
