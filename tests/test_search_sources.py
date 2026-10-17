"""搜索源扩充与聚合策略回归（v3.16.21）。

背景：原注册表只有 bing / so360 / duckduckgo 三个通用网页引擎。实测（2026-10）：
  - duckduckgo 国内**长期不可达**（16s 连接超时），每次搜索都白等到软超时；
  - 通用引擎回答不了「找开源库 / 这个问题别人怎么解 / 有没有视频教程 / 公众号怎么说」
    这类意图；
  - 更糟的是聚合按「谁先返回」拼接且**权重从未被使用**，8 个源混合时顺序随机。

本用例锁死三件事：
  1. 注册表里的每个引擎都能被 tool_web 解析到（防拆分再犯，见
     test_search_engines_resolvable）且实现在 deepseek_client 上；
  2. 聚合改为**轮转（round-robin）**：任何一个可用源都不会被某个高产源挤掉；
  3. 搜索引擎的「功能入口」伪结果（翻译/图片聚合/站内搜索页）被剔除。
"""
import re

import pytest

import deepseek_client as dc
import search_utils
import shared
from agent_tools import tool_web


def _r(url, title="标题", snippet=""):
    return {"title": title, "url": url, "snippet": snippet}


@pytest.fixture(autouse=True)
def _clean_health(monkeypatch):
    monkeypatch.setattr(dc, "_SEARCH_HEALTH", {})


# ── 1. 注册表完整性 ────────────────────────────────────────────────

NEW_ENGINES = ("github", "stackexchange", "hackernews", "bilibili", "sogou_weixin")


def test_new_engines_registered_with_implementation():
    names = [n for n, _w in shared._SEARCH_ENGINES]
    for n in NEW_ENGINES:
        assert n in names, f"新引擎 {n} 未注册进 _SEARCH_ENGINES"
        fn = getattr(dc, "_search_" + n, None)
        assert callable(fn), f"deepseek_client 缺 _search_{n} 实现"


def test_every_registered_engine_is_imported_into_tool_web():
    """search_web 用 globals().get('_search_'+name) 分发；缺 import 会静默空结果。"""
    for name, _w in shared._SEARCH_ENGINES:
        assert tool_web.__dict__.get("_search_" + name) is not None, (
            f"tool_web 未 import _search_{name}（该源会恒返回空）"
        )


def test_engine_weights_descending_in_registry():
    """注册表顺序即同轮内的展示优先级：权重必须递减，否则排序语义自相矛盾。"""
    weights = [w for _n, w in shared._SEARCH_ENGINES]
    assert weights == sorted(weights, reverse=True), weights


# ── 2. 轮转聚合：低产源不被高产源挤掉 ──────────────────────────────

def test_round_robin_keeps_low_volume_engine_visible(monkeypatch):
    """bing 一次返回 20 条时，只返回 1 条的 stackexchange 仍须出现在前几名。

    这是本次改动的核心动机：旧实现按源拼接，bing 会占满整个 num 配额，
    新增的专用源「加了等于没加」。
    """
    fake = {
        "bing": [_r(f"https://web{i}.com/p") for i in range(20)],
        "stackexchange": [_r("https://stackoverflow.com/questions/1")],
    }
    for name, _w in shared._SEARCH_ENGINES:
        payload = fake.get(name, [])
        monkeypatch.setattr(tool_web, "_search_" + name, lambda *a, _p=payload, **k: _p)
    monkeypatch.setattr(tool_web, "_search_safe", lambda rs: list(rs))

    out = tool_web.search_web("q", num=5)
    assert "stackoverflow.com" in out, f"低产专用源被挤掉：\n{out}"
    # 第 1 条来自权重最高的 bing，第 2 条应是 stackexchange 的第一条
    assert out.index("web0.com") < out.index("stackoverflow.com")


def test_results_carry_source_tag(monkeypatch):
    """结果须标注来源引擎：模型据此判断可信度（网页 / 仓库 / 问答 / 视频）。"""
    payload = {"github": [_r("https://github.com/a/b", title="a/b")]}
    for name, _w in shared._SEARCH_ENGINES:
        p = payload.get(name, [])
        monkeypatch.setattr(tool_web, "_search_" + name, lambda *a, _p=p, **k: _p)
    monkeypatch.setattr(tool_web, "_search_safe", lambda rs: list(rs))

    out = tool_web.search_web("q", num=3)
    assert "[github]" in out, out


def test_only_failed_engines_reported_as_failure(monkeypatch):
    """返回 0 条是有效结果，不得计入失败电路（否则冷门词会把源全部熔断）。"""
    def boom(*a, **k):
        raise RuntimeError("nope")

    for name, _w in shared._SEARCH_ENGINES:
        monkeypatch.setattr(tool_web, "_search_" + name, boom)
    monkeypatch.setattr(tool_web, "_search_bing", lambda *a, **k: [])
    monkeypatch.setattr(tool_web, "_search_safe", lambda rs: list(rs))

    tool_web.search_web("冷门词", num=3)
    # bing 正常返回 0 条 → 应记为「成功」（fails 保持 0），而不是失败；
    # 其余抛错的源才累计 fails（健康电路据此冷却）。
    assert dc._SEARCH_HEALTH.get("bing", {}).get("fails", 0) == 0, dc._SEARCH_HEALTH
    assert dc._SEARCH_HEALTH.get("github", {}).get("fails", 0) == 1, dc._SEARCH_HEALTH


# ── 3. 功能入口伪结果剔除 ──────────────────────────────────────────

@pytest.mark.parametrize("url,keep", [
    ("https://www.so.com/link?m=abc", True),          # 自然结果（加密跳转）
    ("https://image.so.com/i?src=imageonebox&q=x", False),  # 图片聚合
    ("https://fanyi.so.com/?src=onebox", False),      # 翻译 onebox
    ("https://www.so.com/s?q=x", False),              # 站内搜索页
    ("https://www.bilibili.com/video/BV1x", True),
    ("https://news.ycombinator.com/item?id=1", True),
    ("https://github.com/a/b", True),
    ("https://stackoverflow.com/questions/1", True),
])
def test_junk_filter_keeps_real_results(url, keep):
    got = len(search_utils.search_drop_junk([{"url": url}])) == 1
    assert got is keep, f"{url} 期望 keep={keep}，实际 {got}"


def test_so360_engine_skips_onebox_blocks():
    """360 的翻译/短视频/热搜 onebox 与自然结果同构（都在 h3>a），须按块 class 区分。

    自然结果块带 res-list；onebox 块是 g-mohe。这里用离线 HTML 片段验证判定逻辑。
    """
    html = (
        '<div class="g-mohe"><h3><a href="/link?m=ONEBOX">酷炫短视频大全</a></h3></div>'
        '<div class="res-list"><h3><a href="/link?m=REAL1">真实结果一</a></h3></div>'
        '<div class="res-list"><h3><a href="/link?m=REAL2">真实结果二</a></h3></div>'
    )

    class _Resp:
        text = html
        status_code = 200

        def raise_for_status(self):
            pass

    class _Client:
        def get(self, *a, **k):
            return _Resp()

    import agent_tools.tool_docs  # noqa: F401  (保证注册顺序真实)
    orig = dc._http_client
    dc._http_client = lambda *a, **k: _Client()
    try:
        rs = dc._search_so360("q", num=10)
    finally:
        dc._http_client = orig
    titles = [r["title"] for r in rs]
    assert "真实结果一" in titles and "真实结果二" in titles, titles
    assert "酷炫短视频大全" not in titles, titles


def test_duckduckgo_still_registered_but_lowest_weight():
    """DDG 国内长期不可达，但保留（海外/代理环境可用），权重压到最低。"""
    reg = dict(shared._SEARCH_ENGINES)
    assert "duckduckgo" in reg
    assert reg["duckduckgo"] == min(reg.values()), "DDG 应为最低权重"
