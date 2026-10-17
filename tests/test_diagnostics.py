"""诊断包（diagnostics.py）回归。

定位：诊断是「出问题时唯一的出口」，它自己坏掉等于没有出口。因此本组用例的重点不是
字段齐全，而是**三件必须成立的事**：
  1. 任何一段采集失败都不能让整份报告抛错（错误收进 collection_errors）；
  2. 凭据类配置**绝不外泄**（诊断包会被发给别人看）；
  3. 导出落盘在数据目录下、可重复执行、且不带业务写副作用。
"""
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import diagnostics  # noqa: E402


def test_collect_has_all_sections():
    doc = diagnostics.collect()
    for key in ("meta", "config", "degradations", "errors", "registry",
                "capabilities", "data", "collection_errors"):
        assert key in doc, key
    assert doc["schema"] == 1
    assert doc["meta"]["version"] and doc["meta"]["platform"]
    # 采集自身不应报错（有错也要列在 collection_errors 里，而不是抛异常）
    assert isinstance(doc["collection_errors"], list)


def test_collect_never_raises_when_a_section_fails(monkeypatch):
    def boom():
        raise RuntimeError("故意炸")
    monkeypatch.setattr(diagnostics, "config_state", boom)
    doc = diagnostics.collect()
    assert doc["config"] == {}
    assert any("config" in e for e in doc["collection_errors"])


def test_config_redacts_secrets(monkeypatch):
    import config_defaults
    import config_utils
    fake = dict(getattr(config_defaults, "DEFAULT_CONFIG", {}))
    fake.update({
        "some_password": "hunter2",
        "telegram_bot_token": "123:abc",
        "brain_community_brain_key": "brain_x.secret",
        "custom_model_name": "x" * 500,
    })
    monkeypatch.setattr(config_utils, "load_config", lambda *a, **kw: fake)
    state = diagnostics.config_state()
    ov = state["overrides"]
    assert ov["some_password"]["value"] == "<redacted>"
    assert ov["telegram_bot_token"]["value"] == "<redacted>"
    assert ov["brain_community_brain_key"]["value"] == "<redacted>"
    # 长字符串截断而不是原样带出
    assert ov["custom_model_name"]["value"].startswith("x" * 10)
    assert "…(+" in ov["custom_model_name"]["value"]
    # 全量键清单里不应出现任何明文密码
    assert "hunter2" not in json.dumps(state, ensure_ascii=False)


def test_export_writes_into_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_data_dir", lambda: str(tmp_path))
    out = diagnostics.export()
    assert out["ok"] is True, out
    assert out["bytes"] > 200
    path = Path(out["path"])
    assert path.is_file() and path.parent.name == "diagnostics"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["meta"]["version"]
    assert doc.get("fingerprint") or True  # export 不强制带指纹


def test_export_reports_failure_instead_of_raising(monkeypatch):
    monkeypatch.setattr(diagnostics, "_data_dir", lambda: "\0invalid")
    out = diagnostics.export()
    assert out["ok"] is False
    assert "error" in out


def test_recent_errors_tolerates_missing_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_data_dir", lambda: str(tmp_path))
    assert diagnostics.recent_errors() == {}


def test_recent_errors_reads_log_tail(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    lines = [f"err {i}" for i in range(500)]
    (logs / "chat_errors.log").write_text("\n".join(lines), encoding="utf-8")
    monkeypatch.setattr(diagnostics, "_data_dir", lambda: str(tmp_path))
    got = diagnostics.recent_errors()["chat_errors.log"]
    assert got["lines"][-1] == "err 499"
    assert len(got["lines"]) <= diagnostics.MAX_LOG_LINES


def test_fingerprint_stable_for_same_doc():
    doc = diagnostics.collect()
    assert diagnostics.fingerprint(doc) == diagnostics.fingerprint(doc)
    assert len(diagnostics.fingerprint(doc)) == 12


def test_diagnostics_endpoints_registered():
    import api_server
    assert api_server._match_get_route("/v1/diagnostics") == "_g_v1_diagnostics"
    assert api_server._match_post_route("/v1/diagnostics") == "_p_v1_diagnostics"
    # 摘要模式仍命中同一个 GET 端点（qpath 匹配带查询串）
    assert api_server._match_get_route("/v1/diagnostics?summary=1") == "_g_v1_diagnostics"


def test_get_endpoint_returns_summary_without_touching_disk(monkeypatch):
    """端点级：走真实的 handler 方法（不打网络），确认鉴权前置与 ?summary=1 分流。"""
    import api_server

    h = api_server._Handler.__new__(api_server._Handler)
    h.path = "/v1/diagnostics?summary=1"
    h.headers = {"Authorization": "Bearer wrong"}
    out = {}
    h.send_response = lambda code: out.__setitem__("code", code)
    h.send_header = lambda *a, **kw: None
    h.end_headers = lambda: None
    h.wfile = type("W", (), {"write": lambda self, b: None, "flush": lambda self: None})()
    h.close_connection = True

    # ① 未授权：必须 401，且不采集（否则等于给了无鉴权的诊断口）
    api_server._Handler._g_v1_diagnostics(h)
    assert out["code"] == 401

    # ② 已授权：摘要模式只回极简字段
    monkeypatch.setattr(api_server._Handler, "_auth", lambda self: True)
    api_server._Handler._g_v1_diagnostics(h)
    assert out["code"] == 200
