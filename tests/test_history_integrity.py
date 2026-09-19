"""长任务历史完整性回归：任务模式一轮上百条 tool 消息，不得被截断/腰斩。

覆盖（本次修复的根因）：
1. `_save_session` 不再把 tool_calls 截到 16 条——否则 17+ 条 tool 结果变孤儿。
2. `_load_session_messages` 超 2000 条时保留**尾部**（最近进度），而非头部。
3. `_emit_tool_cb` 给回调携带 tool_call_id，且兼容旧签名（自动降级）。
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import api_server  # noqa: E402
import deepseek_client as dc  # noqa: E402


def _stub_index(monkeypatch):
    monkeypatch.setattr(api_server, "_index_session_locked", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_save_session_index", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_index_session_file", lambda *a, **k: None)


def _handler():
    class H:
        _safe_sid = api_server._Handler._safe_sid

    return H()


def test_save_session_keeps_all_tool_calls(tmp_path, monkeypatch):
    """一轮 30 个工具调用：tool_calls 与 tool 结果必须一一对应保留。"""
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    _stub_index(monkeypatch)

    n = 30
    tcs = [
        {"id": f"call_{i}", "type": "function",
         "function": {"name": "read_file", "arguments": "{}"}}
        for i in range(n)
    ]
    msgs = [
        {"role": "user", "content": "全片推进"},
        {"role": "assistant", "content": "", "tool_calls": tcs},
    ] + [
        {"role": "tool", "tool_call_id": f"call_{i}", "name": "read_file", "content": f"结果{i}"}
        for i in range(n)
    ]

    sid, err = api_server._Handler._save_session(_handler(), {"id": "sT", "messages": msgs})
    assert err is None, err
    loaded = api_server._Handler._load_session_messages(_handler(), sid)
    assert loaded is not None
    out = loaded["messages"]
    asst = [m for m in out if m.get("role") == "assistant"][0]
    assert len(asst["tool_calls"]) == n, "tool_calls 不得截断（旧实现截到 16）"
    tools = [m for m in out if m.get("role") == "tool"]
    assert len(tools) == n
    # 每个 tool_call_id 都有配对
    ids = {tc["id"] for tc in asst["tool_calls"]}
    assert {t["tool_call_id"] for t in tools} == ids


def test_load_session_keeps_tail_beyond_2000(tmp_path, monkeypatch):
    """超过 2000 条时保留尾部（最近进度），而不是头部。"""
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    total = 2500
    data = {
        "id": "sTail",
        "name": "长会话",
        "messages": [
            {"role": "user", "content": f"m{i}"} for i in range(total)
        ],
    }
    (tmp_path / "sTail.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    loaded = api_server._Handler._load_session_messages(_handler(), "sTail")
    assert loaded is not None
    out = loaded["messages"]
    assert len(out) == 2000
    assert out[0]["content"] == f"m{total - 2000}", "应保留最后 2000 条（取尾部）"
    assert out[-1]["content"] == f"m{total - 1}"


def test_emit_tool_cb_passes_id_and_degrades():
    """新回调拿到 tool_call_id；旧签名回调自动降级（不报错）。"""
    new_seen = []
    dc._emit_tool_cb(lambda n, a, r, cid: new_seen.append((n, r, cid)), "read_file", {}, "ok", "call_7")
    assert new_seen == [("read_file", "ok", "call_7")]

    old_seen = []
    dc._emit_tool_cb(lambda n, a, r: old_seen.append((n, r)), "read_file", {}, "ok", "call_8")
    assert old_seen == [("read_file", "ok")]
