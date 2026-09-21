"""在制回合（turn_upsert）崩溃安全落盘回归。

背景：会话此前只在回合结束落盘——新会话生成中途进程被强杀，重启后完全无记录。

覆盖：
- 生成期周期落盘按 turn_id 就地替换（不重复追加 user、不堆积 assistant 分片）；
- 最终保存带同名 turn_id 时，用最终回合替换在制回合并清除标记；
- 崩溃后新回合（不同 turn_id）追加，旧在制回合内容仍然保留。
"""
import json
import os

import api_server


class _H:
    _safe_sid = api_server._Handler._safe_sid


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(api_server, "_index_session_locked", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_save_session_index", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_index_session_file", lambda *a, **k: None)


def _read(tmp_path, sid):
    with open(os.path.join(str(tmp_path), f"{sid}.json"), encoding="utf-8") as f:
        return json.load(f)


def test_turn_upsert_replaces_in_progress(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    h = _H()
    user = {"role": "user", "content": "写首诗"}
    api_server._Handler._save_session(h, {
        "id": "sU1", "messages": [user], "turn_upsert": True, "turn_id": "T1"})
    api_server._Handler._save_session(h, {
        "id": "sU1", "messages": [user, {"role": "assistant", "content": "床前"}],
        "turn_upsert": True, "turn_id": "T1"})
    api_server._Handler._save_session(h, {
        "id": "sU1", "messages": [user, {"role": "assistant", "content": "床前明月光"}],
        "turn_upsert": True, "turn_id": "T1"})
    data = _read(tmp_path, "sU1")
    assert [m["content"] for m in data["messages"]] == ["写首诗", "床前明月光"]  # 不重复、不堆积
    assert data["turn_id"] == "T1"


def test_final_append_with_turn_id_replaces_and_clears(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    h = _H()
    user = {"role": "user", "content": "hi"}
    api_server._Handler._save_session(h, {
        "id": "sU2", "messages": [user], "turn_upsert": True, "turn_id": "T2"})
    api_server._Handler._save_session(h, {
        "id": "sU2", "messages": [user, {"role": "assistant", "content": "半截"}],
        "turn_upsert": True, "turn_id": "T2"})
    # 最终保存（append + 同名 turn_id）：替换在制回合，不追加第二个 user
    api_server._Handler._save_session(h, {
        "id": "sU2", "append": True, "turn_id": "T2",
        "messages": [user, {"role": "assistant", "content": "完整回复"}]})
    data = _read(tmp_path, "sU2")
    assert [m["content"] for m in data["messages"]] == ["hi", "完整回复"]
    assert "turn_id" not in data and "turn_len" not in data  # 标记已清除


def test_new_turn_after_crash_preserves_partial(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    h = _H()
    u1 = {"role": "user", "content": "第一问"}
    api_server._Handler._save_session(h, {
        "id": "sU3", "messages": [u1, {"role": "assistant", "content": "部分…"}],
        "turn_upsert": True, "turn_id": "TA"})
    # 模拟崩溃后重启，用户继续新回合（不同 turn_id）→ 旧在制回合内容保留
    api_server._Handler._save_session(h, {
        "id": "sU3", "append": True, "turn_id": "TB",
        "messages": [{"role": "user", "content": "第二问"},
                     {"role": "assistant", "content": "第二答"}]})
    data = _read(tmp_path, "sU3")
    assert [m["content"] for m in data["messages"]] == ["第一问", "部分…", "第二问", "第二答"]
    assert "turn_id" not in data  # 非 upsert 保存清除标记
