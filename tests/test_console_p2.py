"""控制台 P2 契约：文件全局搜索 / 重命名端点注册 / 进程资源快照。

覆盖：
  1. `_search_files` 按文件名递归检索：命中文件+目录、跳过隐藏/依赖目录、上限截断；
  2. 新端点已进路由表（`/v1/files/search` GET、`/v1/files/rename` POST）；
  3. `_processes()` 在无进程时返回稳定结构（资源字段为可选增强，不破坏旧契约）。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402


def _isolate_guard(monkeypatch):
    """绕开权限模块（测试目录在仓库外，可能被真实配置的路径规则拦截）。"""
    monkeypatch.setattr(api_server, "_guard_path", lambda p, write=False: (str(p), None))


def test_search_files_matches_files_and_skips_hidden(tmp_path, monkeypatch):
    _isolate_guard(monkeypatch)
    (tmp_path / "alpha_report.txt").write_text("x", encoding="utf-8")
    (tmp_path / "beta.txt").write_text("x", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "alpha_deep.txt").write_text("x", encoding="utf-8")
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "alpha_hidden.txt").write_text("x", encoding="utf-8")
    pyc = tmp_path / "__pycache__"
    pyc.mkdir()
    (pyc / "alpha_cache.pyc").write_bytes(b"x")

    data, err = api_server._search_files(str(tmp_path), "alpha", 50)
    assert err is None
    names = sorted(e["name"] for e in data["entries"])
    assert names == ["alpha_deep.txt", "alpha_report.txt"]
    assert all("hidden" not in e["path"] and "__pycache__" not in e["path"] for e in data["entries"])


def test_search_files_limit_and_empty_query(tmp_path, monkeypatch):
    _isolate_guard(monkeypatch)
    for i in range(5):
        (tmp_path / f"match_{i}.txt").write_text("x", encoding="utf-8")
    data, err = api_server._search_files(str(tmp_path), "match", 2)
    assert err is None
    assert len(data["entries"]) == 2 and data["truncated"] is True

    bad, bad_err = api_server._search_files(str(tmp_path), "   ", 2)
    assert bad is None and bad_err


def test_search_files_missing_dir(tmp_path, monkeypatch):
    _isolate_guard(monkeypatch)
    data, err = api_server._search_files(str(tmp_path / "nope"), "x", 5)
    assert data is None and err


def test_p2_routes_registered():
    assert api_server._match_get_route("/v1/files/search") == "_g_v1_files_search"
    assert api_server._match_get_route("/v1/files/search?q=x") == "_g_v1_files_search"
    assert api_server._match_post_route("/v1/files/rename") == "_p_v1_files_rename"
    assert hasattr(api_server._Handler, "_g_v1_files_search")
    assert hasattr(api_server._Handler, "_p_v1_files_rename")


def test_processes_snapshot_shape():
    out = api_server._processes()
    assert isinstance(out, dict) and isinstance(out.get("processes"), dict)
    for entry in out["processes"].values():
        for key in ("pid", "name", "command", "exited", "lines"):
            assert key in entry
