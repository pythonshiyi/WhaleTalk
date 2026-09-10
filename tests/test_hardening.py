# -*- coding: utf-8 -*-
"""加固回归：错误信息脱敏（P0-3）、SSRF 硬底线（P0-2）、名字消毒（P2-1）、
SQL 函数调用校验（P2-3）、配置副本语义（P1-4）。

对应代码评审报告中的 P0/P1/P2 修复项，锁定行为防回退。
"""
import os
import tempfile

import pytest

import api_server
import permissions
from security import _safe_url


# ── P0-3 错误信息脱敏 ────────────────────────────────────────────
@pytest.mark.parametrize("raw, leaked", [
    (r"文件不存在：C:\Users\Admin\Documents\WhaleTalk\config.json", ["C:\\Users", "Documents"]),
    ("读取失败 /home/admin/.config/whaletalk/config.json", ["/home/admin"]),
    ('File "E:/app/api_server.py", line 42, in _p_v1_config', ["api_server.py", "line 42"]),
    (r"\\server\share\data\x.json 无法访问", ["\\\\server"]),
])
def test_sanitize_error_text_removes_paths_and_locations(raw, leaked):
    out = api_server._sanitize_error_text(raw)
    for token in leaked:
        assert token not in out, f"脱敏后仍含 {token!r}：{out}"


def test_sanitize_error_text_truncates():
    out = api_server._sanitize_error_text("错" * 500, limit=200)
    assert len(out) <= 201 and out.endswith("…")


def test_friendly_error_never_returns_raw_absolute_path():
    """未映射的异常也必须脱敏——这是此前 12 处 `str(e)` 直传的根因。"""
    msg = api_server._friendly_error(FileNotFoundError(r"C:\Users\Admin\secret\a.json 不存在"))
    assert "C:\\Users" not in msg
    assert "<路径>" in msg


def test_friendly_error_keeps_mapped_hints():
    """已知错误码仍返回可操作中文提示（脱敏不得误伤这些分支）。"""
    assert "限速" in api_server._friendly_error(Exception("429 Too Many Requests"))
    assert "API Key" in api_server._friendly_error(Exception("401 unauthorized"))
    assert "deepseek-flash" in api_server._friendly_error(Exception("model does not support image"))


def test_no_response_returns_raw_exception_text():
    """源码级防回退：不得再出现「响应体里直传 str(e)」的形态。

    只检查**真正构造响应**的行（含 self._json( 或 return {），避免把说明性
    注释/文档字符串里引用的示例误判为问题。
    """
    src = open(api_server.__file__, encoding="utf-8").read()
    suspicious = [
        ln.strip() for ln in src.splitlines()
        if ("str(e)" in ln or "str(_e)" in ln)
        and ("self._json(" in ln or "return {" in ln)
    ]
    assert not suspicious, "仍存在错误原文直传：\n" + "\n".join(suspicious)


# ── P0-2 SSRF 硬底线 ────────────────────────────────────────────
@pytest.fixture
def net_cfg():
    """以临时目录初始化权限，并返回可改写的 network 配置。"""
    td = tempfile.mkdtemp(prefix="wt_hard_")
    permissions.init(os.path.join(td, "config.json"), os.path.join(td, "ws"))
    permissions.set_audit_enabled(False)
    data = permissions.get_data()
    saved = {k: data.get(k) for k in ("blocklist_enabled",)}
    net_saved = dict(data.get("network") or {})
    yield data
    data["blocklist_enabled"] = saved["blocklist_enabled"]
    data["network"] = net_saved


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",   # 云元数据
    "http://169.254.10.10/",                      # 链路本地（同网段其它地址）
    "http://10.0.0.5:8080/admin",                 # 私网 10/8
    "http://172.16.3.4/",                         # 私网 172.16/12
    "http://192.168.1.1/",                        # 私网 192.168/16
])
def test_ssrf_hard_floor_blocks_private(url, net_cfg):
    assert _safe_url(url), f"应被硬底线拦截：{url}"


@pytest.mark.parametrize("url", [
    "https://api.deepseek.com/v1",
    "http://example.com/page",
    "http://127.0.0.1:8745/v1/token",   # 回环默认放行（本机单用户软件 + 本地开发）
    "http://localhost:3000/",
])
def test_ssrf_hard_floor_allows_public_and_loopback(url, net_cfg):
    assert _safe_url(url) == "", f"应放行：{url}"


def test_ssrf_loopback_can_be_hardened(net_cfg):
    net_cfg["network"]["allow_loopback"] = False
    assert _safe_url("http://127.0.0.1:8745/v1/token")
    assert _safe_url("http://localhost:3000/")


def test_ssrf_floor_can_be_disabled(net_cfg):
    net_cfg["network"]["block_private"] = False
    assert _safe_url("http://10.0.0.5/") == ""
    # 用户黑名单仍独立生效（169.254.169.254 是出厂预置项）
    assert _safe_url("http://169.254.169.254/")


def test_ssrf_all_off_switch(net_cfg):
    net_cfg["blocklist_enabled"] = False
    assert _safe_url("http://10.0.0.5/") == ""
    assert _safe_url("http://169.254.169.254/") == ""


def test_ssrf_rejects_non_http(net_cfg):
    assert _safe_url("ftp://example.com")
    assert _safe_url("file:///etc/passwd")


# ── P2-1 路径片段消毒 ────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    "../../config.json", "a/b", "a\\b", "..", "", "  ", "x" * 200, "a\nb", "a\x00b",
])
def test_valid_name_rejects_malformed(bad):
    assert api_server._valid_name(bad) is None


@pytest.mark.parametrize("good", ["read_file", "deepseek-flash", "小红书文案助手", "evolve-fix-中文"])
def test_valid_name_accepts_legit_names(good):
    assert api_server._valid_name(good) == good


# ── P2-3 SQL 只读校验：函数调用类需防插空格绕过 ──────────────────
@pytest.mark.parametrize("sql", [
    "SELECT SLEEP(1)",
    "SELECT SLEEP (1)",              # 插空格：旧的子串匹配会漏
    "select sleep ( 1 )",
    "SELECT BENCHMARK (1000000, MD5('a'))",
    "SELECT pg_sleep (2)",
    "SELECT pg_read_file ('/etc/passwd')",
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    "SELECT load_file('/etc/passwd')",
    "SELECT 1; DROP TABLE t",
    "UPDATE t SET a=1",
])
def test_readonly_stmt_rejects(sql):
    import db_utils
    assert db_utils.readonly_stmt(sql) is False, f"应拒绝：{sql}"


@pytest.mark.parametrize("sql", [
    "SELECT id, name FROM users WHERE id = 1",
    "SELECT count(*) FROM logs",
    "PRAGMA table_info(users)",
    "EXPLAIN SELECT 1",
    "SELECT asleep FROM t",           # 含 sleep 子串但非函数调用
    "SELECT * FROM pg_stat_activity",
])
def test_readonly_stmt_accepts(sql):
    import db_utils
    assert db_utils.readonly_stmt(sql) is True, f"应放行：{sql}"


def test_force_limit_still_applies_and_strips_trailing_comment():
    import db_utils
    out = db_utils.force_limit("SELECT * FROM t -- now", 20)
    assert "LIMIT 20" in out.upper()
    assert not out.rstrip().upper().endswith("NOW")


# ── P1-4 配置副本语义 ────────────────────────────────────────────
def test_mutable_config_is_a_copy():
    """mutable_config 必须返回副本：改它不能污染共享缓存。"""
    import config_utils

    a = config_utils.load_config()
    b = config_utils.mutable_config()
    assert b == a and b is not a
    probe_key = "_hardening_probe"
    a.pop(probe_key, None)
    b[probe_key] = "changed"
    assert probe_key not in config_utils.load_config(), "共享缓存被副本的修改污染了"
