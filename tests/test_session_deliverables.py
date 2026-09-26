"""右栏「交付物 / 最近产物」按会话隔离的回归。

覆盖：`_session_product_paths` 从会话消息（assistant 正文 + 工具结果 + 工具参数）提取
产物；`_deliverables(session_id=...)` / `_files(session_id=...)` 随会话切换；空会话为空；
不传 session_id 时保持跨会话（recent_outputs.json）旧行为。
"""
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402
import stores  # noqa: E402


def _mk_file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return str(path)


def _write_session(sessions_dir, sid, messages):
    (sessions_dir / f"{sid}.json").write_text(
        json.dumps({"messages": messages}, ensure_ascii=False), encoding="utf-8")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(sessions))
    monkeypatch.setattr(api_server, "RECENT_PATH", str(tmp_path / "recent.json"))
    return {"sessions": sessions, "root": tmp_path}


def test_session_products_from_args_and_results(sandbox):
    a = _mk_file(sandbox["root"] / "out" / "report.md")
    b = _mk_file(sandbox["root"] / "pic.png")
    _write_session(sandbox["sessions"], "s1", [
        {"role": "user", "content": "做报告和配图"},
        {"role": "assistant", "content": "开始", "tool_calls": [
            {"id": "c1", "function": {"name": "write_file", "arguments": json.dumps({"path": a})}},
            {"id": "c2", "function": {"name": "image_generate", "arguments": json.dumps({"output": b})}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "write_file", "content": f"已写入 {a}"},
        {"role": "tool", "tool_call_id": "c2", "name": "image_generate", "content": f"生成 {b}"},
    ])
    paths = api_server._session_product_paths("s1")
    assert set(paths) == {a, b}

    items = api_server._deliverables(session_id="s1")["items"]
    by_name = {i["name"]: i for i in items}
    assert set(by_name) == {"report.md", "pic.png"}
    assert by_name["pic.png"]["type"] == "image"
    assert by_name["report.md"]["type"] == "doc"


def test_deliverables_isolated_between_sessions(sandbox):
    a = _mk_file(sandbox["root"] / "a.md")
    b = _mk_file(sandbox["root"] / "b.md")
    _write_session(sandbox["sessions"], "s1", [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "write_file", "arguments": json.dumps({"path": a})}}]},
    ])
    _write_session(sandbox["sessions"], "s2", [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "write_file", "arguments": json.dumps({"path": b})}}]},
    ])
    assert [i["path"] for i in api_server._deliverables(session_id="s1")["items"]] == [a]
    assert [i["path"] for i in api_server._deliverables(session_id="s2")["items"]] == [b]


def test_empty_session_and_missing_session_are_empty(sandbox):
    # 空 session id（新对话）与不存在的会话都应为空，绝不回退到跨会话最近产出
    stores.save_recent(api_server.RECENT_PATH, [_mk_file(sandbox["root"] / "global.md")])
    assert api_server._deliverables(session_id="")["items"] == []
    assert api_server._deliverables(session_id="nope")["items"] == []
    assert api_server._files(session_id="")["recent"] == []


def test_no_session_falls_back_to_global_recent(sandbox):
    g = _mk_file(sandbox["root"] / "global.md")
    stores.save_recent(api_server.RECENT_PATH, [g])
    assert [i["path"] for i in api_server._deliverables()["items"]] == [g]
    assert [i["path"] for i in api_server._files()["recent"]] == [g]


def test_files_session_keeps_workspace_entries_global(sandbox, monkeypatch):
    g = _mk_file(sandbox["root"] / "global.md")
    stores.save_recent(api_server.RECENT_PATH, [g])
    workspace = sandbox["root"] / "ws"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("k", encoding="utf-8")
    monkeypatch.setattr(api_server, "_status", lambda: {"active_dir": str(workspace)})
    data = api_server._files(session_id="")
    assert data["recent"] == []
    assert any(e["name"] == "keep.txt" for e in data["entries"])
