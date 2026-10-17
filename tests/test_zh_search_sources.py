"""中文搜索源与微博通道回归（v3.16.22）。

背景：用户希望「稳定的微博/贴吧搜索」。实测（2026-10，本机）结论：
  - 贴吧所有搜索入口被百度安全验证拦截（403），移动端只是 JS 空壳，开放 API 空体；
  - 微博所有**关键词搜索**接口都要求登录态（s.weibo.com 302；
    m.weibo.cn/api 与 weibo.com/ajax/statuses/search 均返回 ok:-100）；
  - 唯一免登录可用的是热搜榜 `weibo.com/ajax/side/hotSearch`。
因此本批做的是：① 微博热搜（免登录，关键词相关才返回）；② 微博关键词搜索
（**可选 Cookie**，未配置即跳过）；③ 补齐 3 个稳定的中文技术源（CSDN/掘金/V2EX）。

另含一项质量修复：GitHub 源原先 sort=stars，会返回「全站最热门」而非「与查询相关」
的仓库（搜「pdf 转 word」给书单和政治资料），改为相关度排序 + stars 下限。
"""
import pytest

import deepseek_client as dc
import shared
from agent_tools import tool_web

ZH_ENGINES = ("csdn", "juejin", "v2ex", "weibo", "weibo_hot", "zhihu")


class _Resp:
    def __init__(self, payload=None, status_code=200, headers=None, flag_redirect=False):
        self._payload = payload if payload is not None else {}
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = flag_redirect

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload=None, **kw):
        self._r = _Resp(payload, **kw)
        self.last = None

    def get(self, url, **kw):
        self.last = (url, kw)
        return self._r

    def post(self, url, **kw):
        self.last = (url, kw)
        return self._r


@pytest.fixture(autouse=True)
def _clean_health(monkeypatch):
    monkeypatch.setattr(dc, "_SEARCH_HEALTH", {})


def _patch_client(monkeypatch, mod, client):
    monkeypatch.setattr(mod, "_http_client", lambda *a, **k: client)


# ── 注册表与实现 ──────────────────────────────────────────────────

def test_zh_engines_registered_and_imported():
    names = [n for n, _w in shared._SEARCH_ENGINES]
    for n in ZH_ENGINES:
        assert n in names, f"{n} 未注册"
        assert callable(getattr(dc, "_search_" + n, None)), f"缺 _search_{n} 实现"
        assert tool_web.__dict__.get("_search_" + n) is not None, f"tool_web 未 import {n}"


def test_weights_still_descending():
    weights = [w for _n, w in shared._SEARCH_ENGINES]
    assert weights == sorted(weights, reverse=True), weights


# ── 微博热搜：只返回与查询相关的热词 ──────────────────────────────

def test_weibo_hot_filters_by_keyword(monkeypatch):
    payload = {"data": {"realtime": [
        {"word": "广交会真的太全面了", "num": 804409},
        {"word": "某明星恋情", "num": 700000},
        {"word": "DeepSeek新模型发布", "num": 600000},
    ]}}
    _patch_client(monkeypatch, dc, _Client(payload))
    rs = dc._search_weibo_hot("DeepSeek", num=5)
    assert len(rs) == 1, rs
    assert "DeepSeek" in rs[0]["title"]
    assert "微博热搜" in rs[0]["snippet"]


def test_weibo_hot_returns_empty_when_unrelated(monkeypatch):
    """无关查询必须返回空——把无关热榜塞进搜索结果是有害噪音，不是帮助。"""
    payload = {"data": {"realtime": [{"word": "广交会真的太全面了", "num": 1}]}}
    _patch_client(monkeypatch, dc, _Client(payload))
    assert dc._search_weibo_hot("python asyncio", num=5) == []


def test_weibo_hot_token_matching(monkeypatch):
    """多词查询按 token 命中（整句子串匹配永远匹配不上）。"""
    payload = {"data": {"realtime": [{"word": "Python 3.15 发布", "num": 9}]}}
    _patch_client(monkeypatch, dc, _Client(payload))
    assert len(dc._search_weibo_hot("python asyncio", num=5)) == 1


# ── 微博关键词搜索：未配置 Cookie 时明确跳过 ──────────────────────

def test_weibo_without_cookie_raises_not_silent(monkeypatch):
    """未配置 Cookie 必须抛错（→ 该源记为不可用并跳过），不得伪装成「无结果」。"""
    import config_utils
    monkeypatch.setattr(config_utils, "load_config", lambda *a, **k: {})
    with pytest.raises(RuntimeError, match="cookie"):
        dc._search_weibo("deepseek")


def test_weibo_expired_cookie_raises(monkeypatch):
    """ok:-100 表示登录态失效 → 抛错交健康电路冷却，不能当成 0 条结果。"""
    import config_utils
    monkeypatch.setattr(config_utils, "load_config", lambda *a, **k: {"weibo_cookie": "SUB=fake"})
    _patch_client(monkeypatch, dc, _Client({"ok": -100, "url": "https://passport.weibo.com/sso/signin"}))
    with pytest.raises(RuntimeError, match="expired|invalid"):
        dc._search_weibo("deepseek")


def test_weibo_parses_mblog_cards(monkeypatch):
    import config_utils
    monkeypatch.setattr(config_utils, "load_config", lambda *a, **k: {"weibo_cookie": "SUB=ok"})
    payload = {"ok": 1, "data": {"cards": [
        {"card_type": 9, "mblog": {
            "id": "123", "text": "<a>#话题#</a> 正文内容",
            "user": {"screen_name": "某人"}}},
        {"card_type": 11},  # 非正文卡片应被跳过
    ]}}
    _patch_client(monkeypatch, dc, _Client(payload))
    rs = dc._search_weibo("x", num=5)
    assert len(rs) == 1, rs
    assert rs[0]["url"] == "https://m.weibo.cn/detail/123"
    assert "<a>" not in rs[0]["title"], "标题必须去标签"


# ── 中文技术源解析 ────────────────────────────────────────────────

def test_csdn_parses_and_strips_tracking(monkeypatch):
    payload = {"result_vos": [
        {"title": "<em>python</em> 教程", "url": "https://blog.csdn.net/a/1?ops_request_misc=x",
         "view_count": "1234", "digg": "5"},
    ]}
    _patch_client(monkeypatch, dc, _Client(payload))
    rs = dc._search_csdn("python", num=5)
    assert len(rs) == 1
    assert "ops_request_misc" not in rs[0]["url"], "应去掉跟踪参数"
    assert rs[0]["title"] == "python 教程", "标题应去 <em> 高亮"
    assert "1234" in rs[0]["snippet"]


def test_juejin_parses_article_info(monkeypatch):
    payload = {"err_no": 0, "data": [
        {"result_type": 2, "result_model": {"article_info": {
            "article_id": "999", "title": "asyncio 入门", "digg_count": 29}}},
    ]}
    _patch_client(monkeypatch, dc, _Client(payload))
    rs = dc._search_juejin("asyncio", num=5)
    assert len(rs) == 1
    assert rs[0]["url"] == "https://juejin.cn/post/999"
    assert "29" in rs[0]["snippet"]


def test_juejin_error_code_raises(monkeypatch):
    _patch_client(monkeypatch, dc, _Client({"err_no": 2, "err_msg": "请求路由不存在"}))
    with pytest.raises(RuntimeError):
        dc._search_juejin("x")


def test_v2ex_parses_hits(monkeypatch):
    payload = {"hits": [
        {"_source": {"id": 508863, "title": "asyncio 讨论", "node": 90, "replies": 12}},
    ]}
    _patch_client(monkeypatch, dc, _Client(payload))
    rs = dc._search_v2ex("asyncio", num=5)
    assert len(rs) == 1
    assert rs[0]["url"] == "https://www.v2ex.com/t/508863"
    assert "12 回复" in rs[0]["snippet"]


def test_zhihu_uses_hot_search_semantics(monkeypatch):
    """知乎关键词搜索需 zse-96 签名（403），本源只承诺热搜词语义。"""
    payload = {"top_search": {"words": [{"display_query": "DeepSeek崩了"}]}}
    _patch_client(monkeypatch, dc, _Client(payload))
    rs = dc._search_zhihu("deepseek", num=5)
    assert len(rs) == 1
    assert "知乎热搜词" in rs[0]["snippet"]
    assert "zhihu.com/search" in rs[0]["url"]


# ── GitHub 相关度修复 ────────────────────────────────────────────

def test_github_uses_relevance_not_stars_sort(monkeypatch):
    """sort=stars 会返回「全站最热门」而非相关仓库，必须用相关度排序。"""
    client = _Client({"items": []})
    _patch_client(monkeypatch, dc, client)
    dc._search_github("pdf 转 word", num=5)
    _url, kw = client.last
    params = kw.get("params") or {}
    assert "sort" not in params, f"不应再按 stars 排序：{params}"
    assert "stars:>=10" in str(params.get("q") or ""), "应过滤空壳仓库"
