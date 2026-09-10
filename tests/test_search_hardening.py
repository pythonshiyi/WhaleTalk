# -*- coding: utf-8 -*-
"""搜索工具健壮性回归（G21）。

背景：一次取证式压测发现 3 个 P0 + 4 个 P1 缺陷。本用例锁死修复后的契约：
  1. 健康电路按「原因」分档冷却（超时 30min vs 普通 10min）
  2. search_web 软超时聚合（慢引擎不拖垮整次搜索）
  3. 翻页越界如实报错（不再误报「搜索失败」）
  4. num 期望/实返差异提示
  5. site 空结果区分「引擎不支持 site」与「该站无内容」
  6. 360 /link?m= 加密跳转标注
  7. search_github 额度预警 + 正确的限流文案（10/分钟，非 60/小时）
"""
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import deepseek_client as dc  # noqa: E402  必须先导入，构建工具注册表
from agent_tools import tool_web  # noqa: E402


def _r(url, title="标题", snippet=""):
    return {"title": title, "url": url, "snippet": snippet}


class _FakeResp:
    def __init__(self, text="", status_code=200, headers=None, payload=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    def get(self, *a, **k):
        return self._resp


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(dc, "_SEARCH_HEALTH", {})
    yield


# ── 1. 健康电路分档冷却 ────────────────────────────────────────────
def test_report_timeout_longer_cooldown():
    dc._search_report("x", False, reason="timeout")
    dc._search_report("x", False, reason="timeout")
    dc._search_report("x", False, reason="timeout")
    timeout_delta = dc._SEARCH_HEALTH["x"]["skip_until"] - time.time()
    assert abs(timeout_delta - dc._SEARCH_HEALTH_COOLDOWN_TIMEOUT) < 2

    dc._SEARCH_HEALTH.clear()
    dc._search_report("y", False, reason="error")
    dc._search_report("y", False, reason="error")
    dc._search_report("y", False, reason="error")
    err_delta = dc._SEARCH_HEALTH["y"]["skip_until"] - time.time()
    assert abs(err_delta - dc._SEARCH_HEALTH_COOLDOWN) < 2
    assert timeout_delta > err_delta, "超时类失败应比普通失败冷却更久"


# ── 2/3/4/5：search_web 行为（mock 引擎，不联网） ───────────────────
def _mock_engines(monkeypatch, bing=None, so360=None, ddg=None):
    monkeypatch.setattr(tool_web, "_search_bing", lambda *a, **k: [] if bing is None else bing)
    monkeypatch.setattr(tool_web, "_search_so360", lambda *a, **k: [] if so360 is None else so360)
    monkeypatch.setattr(tool_web, "_search_duckduckgo", lambda *a, **k: [] if ddg is None else ddg)
    # 隔离 URL 安全校验（其对假域名做 DNS 解析，会让测试慢/抖动），聚焦聚合逻辑
    monkeypatch.setattr(tool_web, "_search_safe", lambda rs: list(rs))


def test_offset_out_of_range_reports_honestly(monkeypatch):
    urls = [f"https://e{i}.com/x" for i in range(10)]
    _mock_engines(monkeypatch, bing=[_r(u) for u in urls[:6]], so360=[_r(u) for u in urls[4:]])
    out = tool_web.search_web("测试", num=5, offset=10)
    assert out.startswith("错误：翻页超出范围"), out
    assert "共搜到 10 条" in out, "应告知可用条数而非误报搜索失败"


def test_num_note_when_short(monkeypatch):
    _mock_engines(monkeypatch, bing=[_r(f"https://a{i}.com") for i in range(6)])
    out = tool_web.search_web("测试", num=10)
    assert "期望 10 条，实返 6 条" in out, out
    assert "num 上限 20" not in out, "num=10 未超上限，不应提示上限"


def test_num_note_mentions_cap_when_over_20(monkeypatch):
    _mock_engines(monkeypatch, bing=[_r(f"https://a{i}.com") for i in range(10)])
    out = tool_web.search_web("测试", num=100)
    assert "期望 100 条" in out and "num 上限 20" in out, out


def test_site_empty_distinguishes_engine_limitation(monkeypatch):
    _mock_engines(monkeypatch, bing=[_r("https://python.org/x")], so360=[_r("https://baidu.com/y")])
    out = tool_web.search_web("python", site="openai.com")
    assert "搜索引擎对 site: 语法支持有限" in out, out
    assert "不代表该站无相关内容" in out, "应说明是引擎限制，而非断定该站无内容"


def test_site_when_engine_returns_match(monkeypatch):
    _mock_engines(
        monkeypatch,
        bing=[_r("https://openai.com/docs"), _r("https://python.org/x")],
    )
    out = tool_web.search_web("api", site="openai.com")
    assert "openai.com/docs" in out and "python.org" not in out


def test_soft_timeout_marks_slow_engine_and_returns_fast(monkeypatch):
    """慢引擎（DDG）不得拖垮整次搜索；未返回者按「超时」记入健康电路。"""
    reports = []
    monkeypatch.setattr(tool_web, "_search_report", lambda n, ok, reason="": reports.append((n, ok, reason)))
    monkeypatch.setattr(tool_web, "_search_bing", lambda *a, **k: [_r(f"https://b{i}.com") for i in range(5)])

    def slow_ddg(*a, **k):
        time.sleep(0.4)
        return []

    monkeypatch.setattr(tool_web, "_search_duckduckgo", slow_ddg)
    monkeypatch.setattr(tool_web, "_search_safe", lambda rs: list(rs))  # 隔离 URL 安全校验的 DNS 延迟
    monkeypatch.setattr(tool_web, "SEARCH_SOFT_DEADLINE", 0.05)

    t0 = time.time()
    out = tool_web.search_web("测试", num=5)
    elapsed = time.time() - t0
    assert "b0.com" in out, "软超时下仍应返回快速引擎的结果"
    assert elapsed < 0.35, f"慢引擎拖慢了整次搜索：{elapsed:.2f}s"
    ddg_report = [r for r in reports if r[0] == "duckduckgo"]
    assert ddg_report and ddg_report[0][2] == "timeout", "未返回的引擎应按超时记账"


# ── 6. 360 /link?m= 跳转标注 ───────────────────────────────────────
def test_so360_annotates_redirect_links(monkeypatch):
    html = (
        '<h3><a href="/link?m=abc123">标题一</a></h3>'
        '<h3><a href="https://example.com/x">标题二</a></h3>'
    )
    monkeypatch.setattr(dc, "_http_client", lambda: _FakeClient(_FakeResp(text=html)))
    res = dc._search_so360("测试", num=10)
    assert len(res) == 2
    jump = [r for r in res if "/link?m=" in r["url"]][0]
    assert "360 跳转" in jump["snippet"], "加密跳转链接应标注，避免污染域名判断"
    assert res[1]["url"] == "https://example.com/x"


# ── 7. search_github 额度预警 ──────────────────────────────────────
def test_gh_rate_hint_low_quota():
    resp = _FakeResp(headers={"x-ratelimit-remaining": "1", "x-ratelimit-reset": str(int(time.time()) + 30)})
    assert "剩余 1 次" in tool_web._gh_rate_hint(resp) and "10 次/分钟" in tool_web._gh_rate_hint(resp)


def test_gh_rate_hint_plenty():
    resp = _FakeResp(headers={"x-ratelimit-remaining": "8"})
    assert tool_web._gh_rate_hint(resp) == ""


def test_search_github_403_message(monkeypatch):
    resp = _FakeResp(status_code=403, headers={"x-ratelimit-remaining": "0"})
    monkeypatch.setattr(tool_web, "_http_client", lambda: _FakeClient(resp))
    out = tool_web.search_github("whaletalk")
    assert "10 次/分钟" in out and "限流" in out
    assert "60 次" not in out, "搜索 API 限流是 10/分钟，不是 60/小时"


def test_search_github_quota_warning_appended(monkeypatch):
    resp = _FakeResp(
        headers={"x-ratelimit-remaining": "1", "x-ratelimit-reset": str(int(time.time()) + 30)},
        payload={"items": [{"full_name": "a/b", "stargazers_count": 9, "html_url": "https://github.com/a/b", "description": "x"}]},
    )
    monkeypatch.setattr(tool_web, "_http_client", lambda: _FakeClient(resp))
    out = tool_web.search_github("whaletalk")
    assert "额度告急" in out and "a/b" in out
