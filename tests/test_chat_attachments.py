# -*- coding: utf-8 -*-
"""对话附件链路契约：拖拽/粘贴任意文件上传 + 会话附件持久化。

覆盖：
- _safe_upload_name 文件名消毒（路径穿越/控制字符/空名）
- _upload_file 原样落盘到 uploads/files 且返回 path/name/size
- /v1/files/upload 端点已注册
- _save_session 持久化 images/files，_load_session_messages 原样回读
"""
import base64
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import api_server  # noqa: E402


def test_safe_upload_name_blocks_traversal_and_control_chars():
    assert api_server._safe_upload_name("../../etc/passwd") == "passwd"
    assert api_server._safe_upload_name(r"C:\windows\evil.exe") == "evil.exe"
    assert api_server._safe_upload_name("a/b/c.txt") == "c.txt"
    assert api_server._safe_upload_name("中文 报告 v2.pdf") == "中文 报告 v2.pdf"
    assert api_server._safe_upload_name("") == "file"
    assert api_server._safe_upload_name("   ") == "file"
    # 控制字符被替换为下划线
    assert api_server._safe_upload_name("a\x00b\x1fc.txt").startswith("a")
    assert "\x00" not in api_server._safe_upload_name("a\x00b.txt")


def test_upload_file_writes_and_returns_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "DATA_DIR", str(tmp_path))
    raw = b"id,name\n1,hello\n"
    body = {"data": "data:text/csv;base64," + base64.b64encode(raw).decode("ascii"), "name": "数据.csv"}
    result, err = api_server._upload_file(body)
    assert err is None, err
    assert result["name"] == "数据.csv"
    assert result["size"] == len(raw)
    p = Path(result["path"])
    assert p.is_file()
    assert p.read_bytes() == raw
    # 落盘目录固定为 uploads/files（不写别处）
    assert p.parent == tmp_path / "uploads" / "files"


def test_upload_file_rejects_empty_and_bad_base64(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "DATA_DIR", str(tmp_path))
    assert api_server._upload_file({})[1] is not None
    assert api_server._upload_file({"data": "!!!not-base64!!!"})[1] is not None


def test_files_upload_route_registered():
    assert api_server._match_post_route("/v1/files/upload") == "_p_v1_files_upload"


def test_save_session_persists_and_reads_attachments(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(api_server, "_index_session_locked", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_save_session_index", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_index_session_file", lambda *a, **k: None)

    class H:
        _safe_sid = api_server._Handler._safe_sid

    h = H()
    body = {
        "id": "s1",
        "name": "附件会话",
        "messages": [
            {
                "role": "user",
                "content": "看看这两份材料",
                "images": [r"C:\data\uploads\a.png"],
                "files": [{"path": r"C:\data\uploads\files\r.pdf", "name": "r.pdf", "size": 1234}],
            },
            {"role": "assistant", "content": "好的"},
        ],
    }
    sid, err = api_server._Handler._save_session(h, body)
    assert err is None, err
    assert sid == "s1"

    loaded = api_server._Handler._load_session_messages(h, "s1")
    assert loaded is not None
    um = loaded["messages"][0]
    assert um["content"] == "看看这两份材料"
    assert um["images"] == [r"C:\data\uploads\a.png"]
    assert um["files"][0]["path"] == r"C:\data\uploads\files\r.pdf"
    assert um["files"][0]["name"] == "r.pdf"
    assert um["files"][0]["size"] == 1234


def test_save_session_drops_malformed_attachments(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(api_server, "_index_session_locked", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_save_session_index", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_index_session_file", lambda *a, **k: None)

    class H:
        _safe_sid = api_server._Handler._safe_sid

    h = H()
    body = {
        "id": "s2",
        "messages": [
            {
                "role": "user",
                "content": "hi",
                "images": ["", "  ", 123],           # 只保留非空字符串
                "files": [{"path": ""}, {"name": "no-path"}],  # 无 path 的丢弃
            },
        ],
    }
    api_server._Handler._save_session(h, body)
    loaded = api_server._Handler._load_session_messages(h, "s2")
    um = loaded["messages"][0]
    assert um.get("images") in (None, [])
    assert um.get("files") is None
    # 原始文件里也不应残留空值
    raw = json.loads((tmp_path / "s2.json").read_text(encoding="utf-8"))
    assert "images" not in raw["messages"][0]
    assert "files" not in raw["messages"][0]
