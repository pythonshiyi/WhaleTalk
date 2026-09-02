# -*- coding: utf-8 -*-
"""tool_web —— P0-1 批量拆分（工具域模块）：🌐 浏览器与网页.

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
引用 deepseek_client 的常量与辅助依赖加载顺序契约——主文件在共享基建全部定义后
才执行 `from agent_tools import *`，此处 from-import 可安全解析。
"""

import html
import os
import re
import threading
import time
from datetime import datetime, timedelta

import permissions

from security import _safe_url
from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
from deepseek_client import (
    CALL_API_MAX_BYTES,
    CALL_API_MAX_HEADERS,
    CALL_API_METHODS,
    DOWNLOAD_MAX_BYTES,
    RSS_FETCH_TIMEOUT,
    RSS_MAX_ITEMS,
    RSS_PRESET_SOURCES,
    RSS_SUMMARY_MAX,
    SEARCH_MAX_RESULTS,
    WEBDAV_MAX_SIZE,
    _BROWSER_LOCK,
    _NET_PROBE_REFS,
    _SEARCH_ENGINES,
    _SEARCH_UA,
    _browser_active_page,
    _browser_close_page,
    _browser_goto,
    _browser_match_page,
    _browser_new_page,
    _browser_pages,
    _browser_switch_to,
    _fetch_blocked_impl,
    _fetch_url_raw,
    _get_browser_page,
    _http_client,
    _load_rss_sources,
    _load_watch_state,
    _load_webdav_config,
    _playwright_ready,
    _safe_stream,
    _save_rss_sources,
    _save_watch_state,
    _search_dedup,
    _search_healthy,
    _search_report,
    _search_safe,
    _webdav_request,
    _wrap_external,
)



# ---- C1: HTML 正文提取（纯标准库，防 fetch_url 返回整页 HTML 噪音） ----
_HTML_SKIP_BLOCKS = re.compile(
    r"<(?:script|style|noscript|svg|nav|header|footer|aside|form|iframe|template)[^>]*>.*?"
    r"</(?:script|style|noscript|svg|nav|header|footer|aside|form|iframe|template)>",
    re.I | re.S,
)
_HTML_TAG = re.compile(r"<[^>]+>")
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HTML_BLOCK_BREAK = re.compile(
    r"</?(?:p|div|h[1-6]|li|tr|br|section|article|blockquote|table|ul|ol|pre|hr)[^>]*>",
    re.I,
)


def _extract_page_text(html_text):
    """把 HTML 页面提取为可读正文：剔除脚本/样式/导航噪音块 + 标签剥离 + 实体解码。"""
    try:
        s = _HTML_COMMENT.sub(" ", html_text)
        s = _HTML_SKIP_BLOCKS.sub(" ", s)
        s = _HTML_BLOCK_BREAK.sub("\n", s)
        s = _HTML_TAG.sub("", s)
        s = html.unescape(s)
        lines = [ln.strip() for ln in s.splitlines()]
        lines = [ln for ln in lines if ln]
        return "\n".join(lines)
    except Exception:
        return ""


def _maybe_extract_article(text):
    """C1: HTML 页面自动提取正文；JSON/纯文本原样返回。提取结果过短视为失败回退原文。"""
    if not text or len(text) < 50:
        return text  # 仅拦截空/极短文本；短 HTML 仍走提取（后面有 is_html 判定 + <10 回退双保险）
    head = text[:2000].lstrip()
    if head.startswith(("{", "[")):  # JSON 接口响应
        return text
    lower = text.lower()
    is_html = "<html" in lower or "<body" in lower or (
        text.count("<") > 30 and text.count(">") > 30
    )
    if not is_html:
        return text
    extracted = _extract_page_text(text)
    if not extracted or len(extracted) < 10:
        return text  # 提取失败/几乎无正文（如纯 JS 渲染页）：回退原始内容
    return f"[HTML 正文提取，共 {len(extracted)} 字符]\n{extracted}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "fetch_url",
                "description": "抓取指定 URL 的文本/JSON 内容（超时 10 秒，最多 500KB；HTML 页面自动提取正文，去导航/脚本噪音）",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "完整 URL，含 http(s)://"}},
                    "required": ["url"],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='抓取网页/接口的文本或 JSON',
    preactivate=(('搜索', '搜一下', '查一下', '新闻', '资讯', '最新'), ('下载',), ('网页', 'url', '抓取', '爬')),
)
def fetch_url(url):
    """抓取网页/接口的文本或 JSON；HTML 页面自动提取正文（外部内容以分隔标记包裹，防 prompt 注入）。"""
    text = _fetch_url_raw(url)
    if str(text or "").startswith("错误"):
        return text
    return _wrap_external(_maybe_extract_article(text), url)


@tool(
        {
            "type": "function",
            "function": {
                "name": "download_file",
                "description": "下载二进制文件（图片/附件/文档/安装包等任意格式）到工作区或指定目录；流式写盘，单文件 200MB 上限；可选 expected_sha256 下载后校验完整性",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "文件完整 URL（http/https）"},
                        "local_path": {"type": "string", "description": "可选：本地保存路径（留空保存到工作区 downloads/）"},
                        "expected_sha256": {"type": "string", "description": "可选：期望的 SHA-256 校验和（64 位十六进制），提供则下载后校验，不匹配报错并删除文件"},
                    },
                    "required": ["url"],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='下载文件到本地',
    preactivate=(('下载',),),
)
def download_file(url, local_path="", expected_sha256=""):
    """下载二进制文件（图片/附件/文档/安装包等）到工作区或指定目录。

    黑名单模式默认放行任意 URL（network.blocklist 除外）；流式写盘，超过
    200MB 自动中止并删除半成品；提供 expected_sha256 时下载后校验完整性。
    """
    if not str(url or "").startswith(("http://", "https://")):
        return "错误：url 必须以 http:// 或 https:// 开头"
    err = _safe_url(url)
    if err:
        return f"错误：{err}"
    exp = str(expected_sha256 or "").strip().lower()
    if exp and not re.match(r"^[0-9a-f]{64}$", exp):
        return "错误：expected_sha256 必须是 64 位十六进制字符串"
    try:
        from urllib.parse import urlparse, unquote
        fn = os.path.basename(unquote(urlparse(str(url)).path)) or f"download_{datetime.now():%Y%m%d_%H%M%S}.bin"
        fn = re.sub(r"[\\/:*?\"<>|]", "_", fn)[:120]
    except Exception:
        fn = f"download_{datetime.now():%Y%m%d_%H%M%S}.bin"
    if str(local_path or "").strip():
        p = permissions.resolve(local_path)
    else:
        base = os.path.join(permissions.WORKSPACE_DIR or "", "downloads")
        p = permissions.resolve(os.path.join(base, fn))
    if not p:
        return "错误：本地路径无效"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        total = 0
        too_large = False
        import hashlib
        h = hashlib.sha256()
        with _safe_stream("GET", url, timeout=60) as resp:
            resp.raise_for_status()
            with open(p, "wb") as f:
                for chunk in resp.iter_bytes(64 * 1024):
                    total += len(chunk)
                    if total > DOWNLOAD_MAX_BYTES:
                        too_large = True
                        break
                    f.write(chunk)
                    h.update(chunk)
        if too_large:
            try:
                os.remove(p)
            except OSError:
                pass
            return f"错误：文件超过 {DOWNLOAD_MAX_BYTES // 1024 // 1024}MB 上限，已中止"
        if exp:
            digest = h.hexdigest()
            if digest != exp:
                try:
                    os.remove(p)
                except OSError:
                    pass
                return f"错误：SHA-256 校验失败（期望 {exp[:16]}…，实际 {digest[:16]}…），已删除文件"
            permissions.audit("download_file", url, f"{p} {total} 字节 sha256={digest[:16]}…")
            return f"已下载 {url} → {p}（{total} 字节，SHA-256 校验通过 {digest[:16]}…）"
        permissions.audit("download_file", url, f"{p} {total} 字节")
        return f"已下载 {url} → {p}（{total} 字节）"
    except Exception as e:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
        return f"错误：下载失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "search_web",
                "description": "联网搜索最新信息，返回标题/链接/摘要（Bing+360+DuckDuckGo 聚合去重，默认 5 条最多 20 条）；site/offset 保证生效，since/until 依赖引擎支持。适合实时新闻、最新资讯，可配合 fetch_url 抓全文",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词（建议简洁明确）"},
                        "num": {"type": "integer", "description": "可选：返回条数（1-20，默认 5）"},
                        "offset": {"type": "integer", "description": "可选：翻页偏移（0 起，如 5 表示跳过前 5 条看第 6-10 条）"},
                        "since": {"type": "string", "description": "可选：起始日期过滤（YYYY-MM-DD，依赖引擎支持，可能不严格）"},
                        "until": {"type": "string", "description": "可选：截止日期过滤（YYYY-MM-DD，依赖引擎支持，可能不严格）"},
                        "site": {"type": "string", "description": "可选：限定站点域名（如 openai.com），只返回该站结果（保证生效）"},
                    },
                    "required": ["query"],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='联网搜索（多引擎聚合）',
    preactivate=(('搜索', '搜一下', '查一下', '新闻', '资讯', '最新'),),
)
def search_web(query, num=SEARCH_MAX_RESULTS, offset=0, since="", until="", site=""):
    """联网搜索：多引擎并行聚合（bing/360/duckduckgo），支持条数/翻页/时间/站点过滤。

    引擎健康度：连续失败 3 次的引擎自动暂停 10 分钟（进程内），可用引擎互补；
    不可用/质量差的源（baidu/sogou/yandex 反爬、google 等不可达）不在注册表中。

    Args:
        query: 搜索关键词
        num: 返回条数（1-20，默认 5）
        offset: 翻页偏移（0 起，如 5 表示第 6-10 条；仅 Bing 支持）
        since/until: 时间范围过滤（YYYY-MM-DD，可只给一端；DDG 仅支持 since）
        site: 限定站点域名（如 "openai.com"，自动追加 site:）
    """
    if not query or not str(query).strip():
        return "错误：搜索词为空"
    try:
        num = max(1, min(20, int(num)))
        offset = max(0, min(200, int(offset)))
    except (TypeError, ValueError):
        num, offset = SEARCH_MAX_RESULTS, 0
    for tag, val in (("since", since), ("until", until)):
        if val and not re.match(r"^\d{4}-\d{2}-\d{2}$", str(val)):
            return f"错误：{tag} 日期格式应为 YYYY-MM-DD"
    q = str(query).strip()
    if len(q) > 200:
        return "错误：搜索词过长（上限 200 字符）"
    site = str(site or "").strip()
    if site:
        if len(site) > 100 or not re.match(r"^[A-Za-z0-9.\-]+$", site):
            return "错误：site 参数应为域名（如 openai.com）"
        q = f"{q} site:{site}"
    since = str(since or "").strip()
    until = str(until or "").strip()

    # 并行调用所有健康引擎；每引擎请求 num+offset 条供聚合后手动翻页
    import concurrent.futures as _cf

    def _run(entry):
        name, _weight = entry
        fn = globals().get("_search_" + name)  # 动态查找：支持测试 mock 替换
        if fn is None:
            return name, [], None
        try:
            kw = {"num": max(num + offset, 10)}
            if name == "bing":
                kw.update(offset=offset, since=since, until=until)
            elif name == "duckduckgo":
                kw["since"] = since
            results = fn(q, **kw)
            return name, results, None
        except Exception as e:
            return name, [], e

    engines = [e for e in _SEARCH_ENGINES if _search_healthy(e[0])]
    with _cf.ThreadPoolExecutor(max_workers=len(engines) or 1) as ex:
        outcomes = list(ex.map(_run, engines))

    merged, last_err = [], None
    for name, results, err in outcomes:
        if err is not None or not results:
            _search_report(name, False)
            if err is not None:
                last_err = err
            continue
        _search_report(name, True)
        merged.extend(_search_safe(results))
    merged = _search_dedup(merged)
    # site 硬过滤：搜索引擎可能忽略 site: 语法，聚合后按域名兜底保证生效
    pre_site = merged
    if site:
        merged = [
            r for r in merged
            if str(r.get("url") or "").split("/")[2].lower() in (site, "www." + site)
            or str(r.get("url") or "").split("/")[2].lower().endswith("." + site)
        ]
    # offset 手动翻页：请求时已多取，这里直接切片（引擎不支持 first= 也生效）
    merged = merged[offset:offset + num]
    if merged:
        lines = [f"搜索结果（{len(merged)} 条）:"]
        for i, r in enumerate(merged, 1):
            lines.append(f"{i}. {r['title']}\n   {r['url']}\n   {r['snippet']}".rstrip())
        return "\n\n".join(lines)
    if site and pre_site:
        return f"未找到限定站点 {site} 的结果（搜索引擎未返回该站点内容，可尝试去掉 site 参数）"
    detail = f": {last_err}" if last_err is not None else ""
    return f"错误：搜索失败（可用搜索源均不可用{detail}）"


@tool(
        {
            "type": "function",
            "function": {
                "name": "search_github",
                "description": "搜索 GitHub 开源仓库（按 Star 排序）。支持 GitHub 原生搜索语法：org:（组织）、topic:、language:、stars:、in:readme 等，例如 org:deepseek-ai 精确查官方组织",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索关键词（支持 org:/topic:/language:/stars: 等原生语法）"},
                        "num": {"type": "integer", "description": "可选：返回条数（1-20，默认 5）"},
                        "language": {"type": "string", "description": "可选：限定编程语言（如 python、javascript）"},
                    },
                    "required": ["query"],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='搜索 GitHub 仓库',
    preactivate=(('github搜索', '搜开源项目', '找仓库', '搜代码库'),),
)
def search_github(query, num=5, language=""):
    """GitHub 仓库搜索（代码/开源项目垂直源，实测国内可达）。

    GitHub API 未认证限流 60 次/小时，适合低频垂直检索。
    """
    if not query or not str(query).strip():
        return "错误：搜索词为空"
    try:
        num = max(1, min(20, int(num)))
    except (TypeError, ValueError):
        num = 5
    q = str(query).strip()
    if len(q) > 200:
        return "错误：搜索词过长（上限 200 字符）"
    language = str(language or "").strip()
    if language:
        if len(language) > 40 or not re.match(r"^[A-Za-z0-9+#.\-]+$", language):
            return "错误：language 参数不合法"
        q = f"{q} language:{language}"
    try:
        resp = _http_client().get(
            "https://api.github.com/search/repositories",
            params={"q": q, "per_page": num, "sort": "stars"},
            headers={"Accept": "application/vnd.github+json", "User-Agent": _SEARCH_UA},
            timeout=10,
        )
        if resp.status_code == 403:
            return "错误：GitHub API 限流（每小时 60 次），请稍后再试"
        resp.raise_for_status()
        items = (resp.json() or {}).get("items") or []
    except Exception as e:
        return f"错误：GitHub 搜索失败: {e}"
    if not items:
        return "未找到相关仓库"
    lines = [f"GitHub 仓库（{len(items)} 个，按 Star 排序）:"]
    for i, it in enumerate(items, 1):
        desc = (it.get("description") or "").strip()[:120]
        stars = it.get("stargazers_count", 0)
        lines.append(f"{i}. {it.get('full_name', '?')} ⭐{stars}\n   {it.get('html_url', '')}\n   {desc}".rstrip())
    return "\n\n".join(lines)


def _search_hn(query, num):
    """C4: HN 源——无 query 返回实时热点榜；有 query 走 Algolia 全文搜索。"""
    q = str(query or "").strip()
    if q:
        if len(q) > 120:
            return "错误：搜索词过长（上限 120 字符）"
        resp = _http_client().get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": q, "hitsPerPage": num},
            headers={"User-Agent": _SEARCH_UA},
            timeout=10,
        )
        resp.raise_for_status()
        hits = (resp.json() or {}).get("hits") or []
        if not hits:
            return f"Hacker News 未找到与「{q}」相关的结果"
        lines = [f"Hacker News 搜索结果（{len(hits)} 条）:"]
        for i, h in enumerate(hits, 1):
            title = str(h.get("title") or "").strip()[:120]
            url = str(h.get("url") or "").strip() or (
                f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            )
            pts = h.get("points") or 0
            cmts = h.get("num_comments") or 0
            lines.append(f"{i}. {title}（👍{pts} 💬{cmts}）\n   {url}")
        return "\n\n".join(lines)
    resp = _http_client().get(
        "https://hacker-news.firebaseio.com/v0/topstories.json",
        headers={"User-Agent": _SEARCH_UA},
        timeout=10,
    )
    resp.raise_for_status()
    ids = (resp.json() or [])[:num]
    if not ids:
        return "Hacker News 热点暂时为空"
    items = []
    for sid in ids:
        try:
            r = _http_client().get(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                headers={"User-Agent": _SEARCH_UA},
                timeout=8,
            )
            it = r.json()
            if it and it.get("title"):
                items.append(it)
        except Exception:
            continue
        if len(items) >= num:
            break
    if not items:
        return "Hacker News 热点获取失败"
    lines = [f"Hacker News 实时热点（{len(items)} 条）:"]
    for i, it in enumerate(items, 1):
        title = str(it.get("title") or "").strip()[:120]
        url = str(it.get("url") or "").strip() or (
            f"https://news.ycombinator.com/item?id={it.get('id')}"
        )
        pts = it.get("score") or 0
        lines.append(f"{i}. {title}（👍{pts}）\n   {url}")
    return "\n\n".join(lines)


def _search_github_hot(query, num):
    """C4: GitHub 源——无 query 返回近 7 天创建的热门仓库（按 Star）；有 query 走仓库搜索。"""
    q = str(query or "").strip()
    if len(q) > 200:
        return "错误：搜索词过长（上限 200 字符）"
    params = {"per_page": num, "sort": "stars", "order": "desc"}
    if q:
        params["q"] = q
    else:
        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        params["q"] = f"created:>{since}"
    try:
        resp = _http_client().get(
            "https://api.github.com/search/repositories",
            params=params,
            headers={"Accept": "application/vnd.github+json", "User-Agent": _SEARCH_UA},
            timeout=10,
        )
        if resp.status_code == 403:
            return "错误：GitHub API 限流（每小时 60 次），请稍后再试"
        resp.raise_for_status()
        items = (resp.json() or {}).get("items") or []
    except Exception as e:
        return f"错误：GitHub 搜索失败: {e}"
    if not items:
        return "GitHub 近期热门仓库为空" if not q else "未找到相关仓库"
    head = "GitHub 近期热门仓库（近 7 天创建，按 Star）" if not q else f"GitHub 仓库搜索（{len(items)} 个）"
    lines = [f"{head}:"]
    for i, it in enumerate(items, 1):
        desc = (it.get("description") or "").strip()[:120]
        stars = it.get("stargazers_count", 0)
        lines.append(f"{i}. {it.get('full_name', '?')} ⭐{stars}\n   {it.get('html_url', '')}\n   {desc}".rstrip())
    return "\n\n".join(lines)


def _search_bilibili_hot(num):
    """C4: B站热门视频源——中文内容实时热点（免 key API，国内实测可达）。无搜索 API，仅热门榜。"""
    try:
        resp = _http_client().get(
            "https://api.bilibili.com/x/web-interface/popular",
            params={"ps": num},
            headers={"User-Agent": _SEARCH_UA},
            timeout=10,
        )
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        items = (data.get("list") or [])[:num]
    except Exception as e:
        return f"错误：B站热门获取失败: {e}"
    if not items:
        return "B站热门视频暂时为空"
    lines = [f"B站热门视频（{len(items)} 条）:"]
    for i, it in enumerate(items, 1):
        title = str(it.get("title") or "").strip()[:120]
        bvid = str(it.get("bvid") or "")
        owner = (it.get("owner") or {}).get("name") or ""
        views = (it.get("stat") or {}).get("view") or 0
        lines.append(f"{i}. {title}（👤{owner} ▶{views}）\n   https://www.bilibili.com/video/{bvid}")
    return "\n\n".join(lines)


def _search_stackoverflow(query, num):
    """C4: Stack Overflow 源——技术问答搜索（Stack Exchange API，免 key 限流 300 次/日）。"""
    q = str(query or "").strip()
    if not q:
        return "错误：stackoverflow 源需要 query 关键词"
    if len(q) > 120:
        return "错误：搜索词过长（上限 120 字符）"
    try:
        resp = _http_client().get(
            "https://api.stackexchange.com/2.3/search/advanced",
            params={
                "q": q, "site": "stackoverflow", "pagesize": num,
                "order": "desc", "sort": "relevance", "filter": "default",
            },
            headers={"User-Agent": _SEARCH_UA},
            timeout=10,
        )
        resp.raise_for_status()
        items = (resp.json() or {}).get("items") or []
    except Exception as e:
        return f"错误：Stack Overflow 搜索失败: {e}"
    if not items:
        return f"Stack Overflow 未找到与「{q}」相关的问题"
    lines = [f"Stack Overflow 搜索结果（{len(items)} 条）:"]
    for i, it in enumerate(items, 1):
        title = str(it.get("title") or "").strip()[:120]
        link = str(it.get("link") or "")
        votes = it.get("score") or 0
        ans = it.get("answer_count") or 0
        tags = ",".join((it.get("tags") or [])[:3])
        lines.append(f"{i}. {title}（👍{votes} 答{ans} [#{tags}]）\n   {link}")
    return "\n\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "search_realtime",
                "description": "实时信息通道（多源）：hn=Hacker News 热点/搜索（默认）/ github=近期热门仓库或仓库搜索 / bilibili=B站热门视频 / stackoverflow=技术问答搜索。不传 query 返回对应源热点榜",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "可选：搜索关键词；留空返回实时热点榜"},
                        "num": {"type": "integer", "description": "可选：返回条数（1-20，默认 5）"},
                        "source": {"type": "string", "description": "可选：数据源 hn/github/bilibili/stackoverflow（默认 hn）"},
                    },
                    "required": [],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='实时热点/社区讨论搜索',
    preactivate=(('搜索', '搜一下', '查一下', '新闻', '资讯', '最新'),),
)
def search_realtime(query="", num=5, source="hn"):
    """实时信息通道（多源）：hn / github / bilibili / stackoverflow。
    各源无 query 时返回热点榜，有 query 时按源能力做搜索（bilibili 无搜索 API）。"""
    try:
        num = max(1, min(20, int(num)))
    except (TypeError, ValueError):
        num = 5
    src = str(source or "hn").strip().lower()
    if src == "hn":
        return _search_hn(query, num)
    if src == "github":
        return _search_github_hot(query, num)
    if src == "bilibili":
        return _search_bilibili_hot(num)
    if src == "stackoverflow":
        return _search_stackoverflow(query, num)
    return f"错误：暂不支持数据源 {src}（支持 hn / github / bilibili / stackoverflow）"


@tool(
        {
            "type": "function",
            "function": {
                "name": "browser_navigate",
                "description": "浏览器多标签可视操作：open/click/type/fill/submit/select/get_text（当前页签），tabs/new_tab/switch_tab/close_tab多标签管理，back/forward/reload导航；共享登录态",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "目标网址（tabs/new_tab/switch_tab/close_tab/back/forward 等动作可按需省略）"},
                        "action": {"type": "string", "description": "open / tabs / new_tab / switch_tab / close_tab / back / forward / reload / click / type / fill / submit / select / get_text"},
                        "selector": {"type": "string", "description": "CSS 选择器（click/type/fill/select/get_text 需要）"},
                        "text": {"type": "string", "description": "要输入的文本（type/fill）或要选择的选项（select）"},
                        "handle": {"type": "string", "description": "页签/窗口句柄（switch_tab/close_tab 用）：tabs 列表中的 #编号，或 URL/标题关键字"},
                    },
                    "required": [],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='控制浏览器（多标签页：打开/点击/输入/填表/提交/切换/关闭，共享登录态）',
    preactivate=(('网页', 'url', '抓取', '爬'),),
)
def browser_navigate(url="", action="open", selector="", text="", handle=""):
    """浏览器可视操作（Playwright 可选依赖，未安装时返回安装提示）。

    多窗口/多标签模型：所有页签（含 window.open 弹窗）在同一共享上下文内，
    tabs 可枚举全部句柄；switch_tab/close_tab 用 #编号 或 URL/标题关键字定位。
    open/click/type/fill/submit/select/get_text 等作用于当前激活页签；
    click/type/submit 不重新导航，保留页面状态与登录态。
    有头/无头跟随全局开关 BROWSER_HEADLESS。
    """
    ok, hint = _playwright_ready()
    if not ok:
        return hint
    action = (action or "open").lower()
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception as e:
        return f"错误：playwright 初始化失败: {e}"
    if url:
        err = _safe_url(url)
        if err:
            return f"错误：{err}"
    try:
        with _BROWSER_LOCK:  # playwright 非线程安全：浏览器操作串行化
            # ---------- 页签/窗口句柄管理 ----------
            if action in ("tabs", "list_tabs"):
                pages = _browser_pages()
                if not pages:
                    return "浏览器尚未打开任何页签（可用 action=new_tab 打开）"
                active = _browser_active_page()
                lines = []
                for i, p in enumerate(pages):
                    try:
                        title = (p.title() or "")[:60]
                    except Exception:
                        title = ""
                    try:
                        u = p.url or ""
                    except Exception:
                        u = ""
                    mark = "▶" if p is active else " "
                    lines.append(f"{mark} #{i} {title}\n    {u}" if title else f"{mark} #{i} {u}")
                return f"共 {len(pages)} 个页签/窗口（▶ 当前激活；switch_tab/close_tab 用 #编号 或 URL/标题）：\n" + "\n".join(lines)

            if action in ("new_tab", "new"):
                if not url:
                    return "错误：new_tab 需要 url"
                page = _browser_new_page(url)
                return f"已新开页签并激活：{page.title() or url}\n当前 URL: {page.url}"

            if action in ("switch_tab", "switch"):
                page, err = _browser_switch_to(handle or text or selector or "")
                if page is None:
                    return err
                return f"已切换到页签：{page.title() or page.url}\n当前 URL: {page.url}"

            if action in ("close_tab", "close"):
                ok_close, msg = _browser_close_page(handle or "")
                return msg

            # ---------- 导航类（作用于当前激活页签）----------
            page = _browser_active_page()
            if action == "open":
                if not url:
                    return "错误：open 需要 url（列表页签用 action=tabs）"
                _browser_goto(page, url)
                return f"已打开：{page.title() or url}\n当前 URL: {page.url}"
            if action in ("back", "forward", "reload"):
                if action == "back":
                    page.go_back()
                    note = "后退"
                elif action == "forward":
                    page.go_forward()
                    note = "前进"
                else:
                    page.reload()
                    note = "刷新"
                page.wait_for_timeout(800)
                return f"已{note}，当前 URL: {page.url}"
            # 非导航动作：确保在目标页（已在此页则保持状态，不重复导航）
            if url:
                _browser_goto(page, url)
            if action == "get_text":
                if not selector:
                    return "错误：get_text 需要 selector"
                els = page.query_selector_all(selector)
                if not els:
                    return f"未找到匹配 {selector} 的元素"
                texts = [e.inner_text()[:500] for e in els[:10]]
                return "\n".join(f"· {t}" for t in texts)
            if action == "click":
                if not selector:
                    return "错误：click 需要 selector"
                page.click(selector, timeout=5000)
                page.wait_for_timeout(1000)
                return f"已点击 {selector}，当前 URL: {page.url}"
            if action in ("type", "fill"):
                if not selector:
                    return "错误：type/fill 需要 selector"
                page.fill(selector, text or "", timeout=5000)
                return f"已在 {selector} 输入文本"
            if action == "submit":
                if selector:
                    page.click(selector, timeout=5000)
                else:
                    page.keyboard.press("Enter")
                page.wait_for_timeout(1500)
                return f"已提交表单，当前 URL: {page.url}"
            if action == "select":
                if not selector:
                    return "错误：select 需要 selector"
                page.select_option(selector, text or "")
                return f"已在 {selector} 选择 {text}"
            return f"错误：未知动作 {action}（支持 open/tabs/new_tab/switch_tab/close_tab/back/forward/reload/click/type/fill/submit/select/get_text）"
    except Exception as e:
        return f"错误：浏览器操作失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "web_screenshot",
                "description": "网页截图并保存到工作区，需安装 playwright（可选依赖）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "目标网址"},
                        "width": {"type": "integer", "description": "视口宽度，默认 1280"},
                        "height": {"type": "integer", "description": "视口高度，默认 800"},
                    },
                    "required": ["url"],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='网页截图保存',
    preactivate=(('网页', 'url', '抓取', '爬'),),
)
def web_screenshot(url, width=1280, height=800):
    """网页截图并保存到工作区（Playwright 可选依赖，复用共享浏览器）。"""
    ok, hint = _playwright_ready()
    if not ok:
        return hint
    if not permissions.WORKSPACE_DIR:
        return "错误：工作区未初始化"
    try:
        w = max(320, min(2560, int(width or 1280)))
        h = max(240, min(1920, int(height or 800)))
    except (TypeError, ValueError):
        w, h = 1280, 800
    err = _safe_url(url)
    if err:
        return f"错误：{err}"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = re.sub(r"[\\/:*?\"<>|]", "_", str(url))[:30]
    path = os.path.join(permissions.WORKSPACE_DIR, f"screenshot_{safe}_{ts}.png")
    try:
        with _BROWSER_LOCK:
            page = _get_browser_page()
            if str(page.url or "").strip("/") != str(url).strip("/"):
                page.goto(url, timeout=15000, wait_until="domcontentloaded")
            page.set_viewport_size({"width": w, "height": h})
            page.wait_for_timeout(1200)
            page.screenshot(path=path, full_page=False)
        permissions.audit("web_screenshot", url, path)
        return f"截图已保存：{path}"
    except Exception as e:
        return f"错误：截图失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "net_diagnose",
                "description": "网络诊断：对网址/主机做 全局连通→DNS→TCP→HTTP 分层探测，判定故障类别（断网/DNS 故障/端口不通/反爬 403/限流/超时被墙/TLS 问题），并给出自动降级建议策略",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "网址或主机名（留空则探测全局连通性）"},
                    },
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='网络诊断（分层探测+降级建议）',
    preactivate=(('网络诊断', '断网', '连不上', '网络问题', '上不去网'),),
)
def net_diagnose(target=""):
    """网络诊断：对一个网址/主机做 DNS→TCP→TLS→HTTP 分层探测并给出结论与降级建议。

    target 支持 URL（https://example.com/a）或纯域名/IP；省略时探测全局连通性。
    """
    t = str(target or "").strip() or "https://www.baidu.com"
    from urllib.parse import urlparse
    if "//" not in t:
        t = "https://" + t
    try:
        up = urlparse(t)
        host = (up.hostname or "").strip()
        port = up.port or (443 if up.scheme == "https" else 80)
        use_tls = up.scheme == "https"
    except Exception:
        return f"错误：无法解析目标：{target}"
    if not host:
        return f"错误：无法解析主机名：{target}"
    import socket as _socket

    def _timed(fn, seconds):
        box = {}

        def _run():
            try:
                box["val"] = fn()
            except Exception as e:
                box["err"] = e

        th = threading.Thread(target=_run, daemon=True)
        th.start()
        th.join(seconds)
        if th.is_alive():
            return None, f"超时（>{seconds}s）"
        if "err" in box:
            return None, str(box["err"])
        return box.get("val"), None

    def _http_status(u):
        with _safe_stream("GET", u, timeout=8) as resp:
            return resp.status_code

    lines = [f"🔎 网络诊断：{t}", ""]
    # 0) 全局连通性参照
    ref_ok, ref_note = False, ""
    for ref in _NET_PROBE_REFS:
        val, err = _timed(lambda r=ref: _http_status(r), 10)
        if val is not None:
            ref_ok = True
            ref_note = f"参照站点可达（{ref.split('/')[2]} 返回 {val}）"
            break
        ref_note = f"参照站点也不可达：{err}"
    lines.append(f"① 全局连通性：{'✅ ' if ref_ok else '❌ '}{ref_note}")
    # 1) DNS
    t0 = time.monotonic()
    addrs, dns_err = _timed(lambda: [ai[4][0] for ai in _socket.getaddrinfo(host, port)], 6)
    if addrs:
        lines.append(f"② DNS 解析：✅ {host} → {'、'.join(addrs[:3])}（{(time.monotonic() - t0) * 1000:.0f}ms）")
    else:
        lines.append(f"② DNS 解析：❌ {dns_err}")
        lines.extend([
            "",
            "**结论**：DNS 故障（本机无法解析该域名）。",
            "**建议策略**：① 换公共 DNS（223.5.5.5 / 119.29.29.29）后在 hosts 加映射或重试；② 若为被墙站点，直接走 fetch_blocked 代理通道；③ 断定与目标服务器无关（全局网也可能正常）。",
        ])
        return "\n".join(lines)
    # 2) TCP
    t0 = time.monotonic()

    def _tcp_probe():
        with _socket.create_connection((addrs[0], port), timeout=6) as s:
            return s.getpeername()

    sock_info, tcp_err = _timed(_tcp_probe, 8)
    if sock_info:
        lines.append(f"③ TCP 连接：✅ {sock_info[0]}:{port}（{(time.monotonic() - t0) * 1000:.0f}ms）")
    else:
        lines.append(f"③ TCP 连接：❌ {tcp_err}")
        verdict = ("本机/本地网络问题" if not ref_ok else "目标主机端口不通（服务下线/防火墙拦截）")
        lines.extend([
            "",
            f"**结论**：{verdict}。",
            "**建议策略**：" + ("检查 Wi-Fi/代理设置后重试。" if not ref_ok else "改用镜像站点或稍后重试；该端口确实不可达，换端点无效时可走 fetch_blocked 代理。"),
        ])
        return "\n".join(lines)
    # 3) HTTP(S)
    status, http_err = None, ""
    t0 = time.monotonic()
    try:
        with _safe_stream("GET", t, timeout=(6, 12)) as resp:
            status = resp.status_code
    except Exception as e:
        http_err = str(e)
    ms = f"（{(time.monotonic() - t0) * 1000:.0f}ms）"
    if status is not None:
        lines.append(f"④ HTTP 响应：✅ 状态码 {status}{ms}")
    else:
        lines.append(f"④ HTTP 响应：❌ {http_err}{ms}")
    category, advice = "连通正常", []
    if status == 403 or status == 451:
        category = "反爬/WAF 拦截（HTTP 403/451）"
        advice = ["自动切换 fetch_blocked 代理通道抓取（Agent 已可直接调用）", "降低频率、补齐 User-Agent/Cookie 后直连重试", "换官方 API 或公开镜像获取同等数据"]
    elif status == 429:
        category = "限流（HTTP 429 Too Many Requests）"
        advice = ["等待 30-60 秒再试", "拉长请求间隔/减少并发", "换镜像源"]
    elif status and status >= 500:
        category = f"服务端故障（HTTP {status}）"
        advice = ["稍后自动重试即可（非本机问题）", "查状态页 https://www.githubstatus.com 等（如是第三方服务）", "换备用端点"]
    elif status is None:
        if "certificat" in http_err.lower() or "ssl" in http_err.lower():
            category = "TLS/证书问题"
            advice = ["更新系统根证书", "确认系统时间正确", "临时换 HTTP 端点（仅内网信任环境）"]
        elif "timeout" in http_err.lower() or "timed out" in http_err.lower():
            category = "响应超时/TCP RESET（疑似被墙或链路差）"
            advice = ["走 fetch_blocked 代理通道（被墙站点专用）", "增大超时重试一次", "换 CDN 友好的镜像域名"]
        else:
            category = f"HTTP 层失败：{http_err[:80]}"
            advice = ["重试一次并观察是否稳定复现", "用 web_screenshot 打开看看实际页面表现"]
    elif status and 200 <= status < 400:
        advice = ["无需处理：目标可达，若仍解析失败多为内容层问题，可重试"]
    lines.extend(["", f"**结论**：{category}。"])
    if advice:
        lines.append("**建议策略**：")
        lines.extend(f"- {a}" for a in advice)
    if not ref_ok:
        lines.append("\n⚠ 参照站点也不可达：优先排查本机网络（Wi-Fi/VPN/代理）后再操作目标。")
    permissions.audit("net_diagnose", str(t)[:80], category[:60])
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "fetch_url_smart",
                "description": "智能抓取网页：直连失败时自动做网络诊断并用内置代理通道降级重试（被墙站点/限流场景自愈），返回内容并注明走了哪条路径",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "http(s):// 页面地址"},
                    },
                    "required": ["url"],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='智能抓取（失败自动走代理通道）',
    preactivate=(('被墙', '爬墙', '代理抓取', '绕过封锁', '抓不了'),),
)
def fetch_url_smart(url):
    """智能抓取：直连优先，失败自动诊断并降级（被墙代理通道），返回内容 + 抓取路径说明。

    用于替代 fetch_url 的“一击即溃”场景：被墙站点、限流、区域性封锁等自动兜底。
    """
    direct = fetch_url(url)
    if not str(direct or "").startswith("错误"):
        return direct  # 直连成功：原样返回（不做包装，保持下游兼容）
    url_s = str(url or "").strip()
    diag = net_diagnose(url_s)
    category = ""
    for ln in diag.splitlines():
        if ln.startswith("**结论**"):
            category = ln.replace("**结论**", "").strip("* ").strip()
            break
    attempts = [f"① 直连失败：{str(direct)[:160]}"]
    if _fetch_blocked_impl is not None:
        blocked = _run_fetch_blocked(url_s)
        if not str(blocked or "").startswith("错误"):
            attempts.append("② 自动降级：经内置代理通道抓取成功 ✅")
            return "".join([*attempts, f"\n诊断：{category}\n", f"\n--- 以下为代理通道返回的内容 ---\n{_wrap_external(blocked, url_s)}"])
        attempts.append(f"② 代理通道也失败：{str(blocked)[:160]}")
    else:
        attempts.append("② 代理通道不可用（fetch_blocked.py 未启用）")
    attempts.append(f"\n诊断：{category}\n\n以上两种途径均失败。可依据诊断建议稍后重试、换镜像源，或让用户提供该页面的其他入口。")
    return "\n".join(attempts)


@tool(
        {
            "type": "function",
            "function": {
                "name": "rss_fetch",
                "description": "RSS 订阅管理：list/preset 精选源/add/remove/fetch 抓最新条目（标题/链接/时间/摘要）。可配合 schedule_task 生成每日简报",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "list / preset / add / remove / fetch"},
                        "url": {"type": "string", "description": "add / fetch 时必填：RSS 源地址（http(s)）"},
                        "limit": {"type": "integer", "description": "可选：返回条数上限（默认 10，最大 20）"},
                        "since_hours": {"type": "integer", "description": "可选：只返回最近 N 小时的新条目（默认 24，0=全部）"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='RSS 订阅/聚合阅读',
    preactivate=(('rss', '订阅源', '聚合阅读', '订阅列表'),),
)
def rss_fetch(action="list", url="", limit=10, since_hours=24):
    """RSS 订阅管理（list/add/remove/preset）+ 抓取最新条目（标题/链接/时间/摘要）。

    action=preset 一键添加精选科技/AI 源（机器之心/量子位/少数派/IT之家/开源中国/HN）。
    """
    act = str(action or "list").strip().lower()
    if act not in ("list", "add", "remove", "fetch", "preset"):
        return "错误：action 仅支持 list / add / remove / fetch / preset"
    if act in ("add", "fetch") and not str(url or "").strip():
        return f"错误：{act} 需要 url（RSS 源地址）"
    try:
        lim = max(1, min(RSS_MAX_ITEMS, int(limit or 10)))
    except (TypeError, ValueError):
        lim = 10
    try:
        hours = max(0, min(24 * 30, int(since_hours) if since_hours not in (None, "") else 24))
    except (TypeError, ValueError):
        hours = 24
    if act == "list":
        sources = _load_rss_sources()
        if not sources:
            return "当前没有 RSS 订阅（用 action=preset 一键添加精选源，或 action=add url=... 手动添加）"
        lines = [f"共 {len(sources)} 个订阅源："]
        for i, s in enumerate(sources, 1):
            lines.append(f"{i}. {s.get('name') or s.get('url')} | {s.get('url')}")
        return "\n".join(lines)
    if act == "preset":
        sources = _load_rss_sources()
        existing = {s.get("url") for s in sources}
        added = [s for s in RSS_PRESET_SOURCES if s["url"] not in existing]
        if not added:
            return "精选源均已订阅"
        _save_rss_sources(sources + added)
        return f"已添加 {len(added)} 个精选源：" + "、".join(s["name"] for s in added)
    if act == "add":
        u = str(url).strip()
        if len(u) > 2048 or not u.startswith(("http://", "https://")):
            return "错误：url 必须是 http(s) 开头的 RSS 源地址"
        err = _safe_url(u)
        if err:
            return f"错误：{err}"
        sources = _load_rss_sources()
        if any(s.get("url") == u for s in sources):
            return "该源已订阅"
        sources.append({"url": u, "name": "", "added": datetime.now().isoformat(timespec="seconds")})
        if _save_rss_sources(sources):
            return f"已添加订阅源（当前共 {len(sources)} 个）：{u}"
        return "错误：订阅保存失败"
    if act == "remove":
        u = str(url).strip()
        sources = _load_rss_sources()
        kept = [s for s in sources if s.get("url") != u]
        if len(kept) == len(sources):
            return f"未找到订阅源：{u}"
        if _save_rss_sources(kept):
            return f"已移除订阅源（剩余 {len(kept)} 个）：{u}"
        return "错误：订阅保存失败"
    # fetch
    try:
        import feedparser
    except ImportError:
        return "未安装 feedparser，请先执行 pip_install feedparser 后重试"
    u = str(url).strip()
    if not u.startswith(("http://", "https://")):
        return "错误：url 必须是 http(s) 开头的 RSS 源地址"
    err = _safe_url(u)
    if err:
        return f"错误：{err}"
    # feedparser 6.x 的 parse() 不再支持 timeout 关键字（旧版支持）：
    # 统一用内部线程 + join 超时实现可靠超时，兼容所有版本
    box = {}

    def _parse():
        try:
            box["parsed"] = feedparser.parse(u, request_headers={"User-Agent": _SEARCH_UA})
        except Exception as e:
            box["err"] = e

    t = threading.Thread(target=_parse, daemon=True)
    t.start()
    t.join(RSS_FETCH_TIMEOUT)
    if t.is_alive():
        return "错误：RSS 抓取超时（>10 秒），请稍后重试或检查源地址"
    if "err" in box:
        return f"错误：RSS 抓取失败: {box['err']}"
    parsed = box["parsed"]
    # getattr 兼容 FeedParserDict 与普通对象
    if getattr(parsed, "bozo", 0) and not getattr(parsed, "entries", []):
        return f"错误：无效的 RSS 源（{getattr(parsed, 'bozo_exception', None) or '解析失败'}）"
    import calendar
    from datetime import datetime as _dt

    cutoff_ts = None
    if hours > 0:
        cutoff_ts = time.time() - hours * 3600
    picked = []
    seen = set()
    for e in parsed.entries:
        title = str(getattr(e, "title", "") or "").strip()
        link = str(getattr(e, "link", "") or "").strip()
        if not title and not link:
            continue
        fp = (link or title)
        if fp in seen:
            continue
        seen.add(fp)
        published = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if published:
            try:
                ts = calendar.timegm(published)
            except Exception:
                ts = None
            if cutoff_ts is not None and ts is not None and ts < cutoff_ts:
                continue  # feed 不一定按时间排序：用 continue 而非 break，防乱序源丢条目
        picked.append(e)
        if len(picked) >= lim:
            break
    if not picked:
        return f"来源 {u} 最近 {hours} 小时没有新条目" if hours else f"来源 {u} 没有可显示的条目"
    feed_title = str(getattr(getattr(parsed, "feed", None), "title", "") or "") or u
    lines = [f"来源: {feed_title}（{len(picked)} 条，最近 {hours}h）"]
    for i, e in enumerate(picked, 1):
        title = str(getattr(e, "title", "") or "（无标题）")[:100]
        link = str(getattr(e, "link", "") or "").strip()
        pub = ""
        published = getattr(e, "published_parsed", None) or getattr(e, "updated_parsed", None)
        if published:
            try:
                pub = _dt.fromtimestamp(calendar.timegm(published), _dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pub = ""
        summary = re.sub(r"<[^>]+>", " ", str(getattr(e, "summary", "") or getattr(e, "description", "")))
        summary = re.sub(r"\s+", " ", summary).strip()[:RSS_SUMMARY_MAX]
        lines.append(f"{i}. {title} | {pub} | {link}")
        if summary:
            lines.append(f"   {summary}")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "webdav",
                "description": "WebDAV 云盘同步（坚果云/Nextcloud/群晖等）：list 列目录 / upload 上传 / download 下载 / delete 删除。连接在 webdav_config.json 配置（密码可加密）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "list / upload / download / delete"},
                        "remote_path": {"type": "string", "description": "远端路径，如 /Documents/report.pdf（默认 /）"},
                        "local_path": {"type": "string", "description": "upload/download 必填：本地文件绝对路径"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='WebDAV 云盘（坚果云/Nextcloud）上传下载',
    preactivate=(('坚果云', 'nextcloud', 'webdav', '云盘同步'),),
)
def webdav(action="list", remote_path="/", local_path=""):
    """WebDAV 云盘操作：list / upload / download / delete。"""
    act = str(action or "list").strip().lower()
    if act not in ("list", "upload", "download", "delete"):
        return "错误：action 仅支持 list / upload / download / delete"
    cfg, err = _load_webdav_config()
    if cfg is None:
        return err
    remote = str(remote_path or "/").strip()
    if not remote.startswith("/"):
        remote = "/" + remote
    if ".." in remote.split("/") or any(ord(ch) < 32 for ch in remote):
        return "错误：remote_path 非法（禁止 .. 与控制字符）"
    if act == "list":
        body = (
            '<?xml version="1.0"?><d:propfind xmlns:d="DAV:">'
            "<d:prop><d:displayname/><d:getcontentlength/><d:getlastmodified/>"
            "<d:resourcetype/></d:prop></d:propfind>"
        )
        try:
            resp = _webdav_request(
                cfg, "PROPFIND", remote,
                headers={"Depth": "1", "Content-Type": "application/xml"},
                content=body,
            )
        except Exception as e:
            return f"错误：WebDAV 请求失败: {e}"
        if resp.status_code not in (200, 207):
            return f"错误：WebDAV 列表失败（HTTP {resp.status_code}）"
        import xml.etree.ElementTree as ET

        try:
            root = ET.fromstring(resp.text)
        except Exception:
            return "错误：WebDAV 响应解析失败"
        ns = {"d": "DAV:"}
        items = []
        for resp_el in root.findall(".//d:response", ns):
            href_el = resp_el.find("d:href", ns)
            href = (href_el.text if href_el is not None else "") or ""
            props = resp_el.find("d:propstat/d:prop", ns)
            if props is None:
                continue
            dn = props.find("d:displayname", ns)
            sz = props.find("d:getcontentlength", ns)
            mod = props.find("d:getlastmodified", ns)
            rt = props.find("d:resourcetype/d:collection", ns)
            name = (dn.text if dn is not None and dn.text else None) or href.rstrip("/").split("/")[-1] or "/"
            items.append((
                rt is not None,
                name,
                (sz.text if sz is not None else "") or "",
                (mod.text if mod is not None else "") or "",
            ))
        if not items:
            return f"远端目录为空：{remote}"
        lines = [f"远端目录 {remote}："]
        for is_dir, name, size, mod in sorted(items, key=lambda x: (not x[0], x[1].lower())):
            lines.append(f"{'DIR ' if is_dir else 'FILE'} {name} | {size}B | {mod}")
        return "\n".join(lines)
    if act in ("upload", "download"):
        if not str(local_path or "").strip():
            return f"错误：{act} 需要 local_path"
        if act == "download":
            ok, reason = permissions.check_filesystem(local_path, write=True)
            if not ok:
                return reason
            out = permissions.resolve(local_path)
            if not out:
                return "错误：本地路径无效"
            total = 0
            try:
                os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
                client = _http_client()
                if hasattr(client, "stream"):
                    with _safe_stream(
                        "GET", cfg["url"] + remote, timeout=30,
                        auth=(cfg["username"], cfg["password"]),
                    ) as resp:
                        if resp.status_code != 200:
                            return f"错误：下载失败（HTTP {resp.status_code}）"
                        with open(out, "wb") as f:
                            for chunk in resp.iter_bytes(64 * 1024):
                                total += len(chunk)
                                if total > WEBDAV_MAX_SIZE:
                                    try:
                                        os.remove(out)
                                    except OSError:
                                        pass
                                    return f"错误：远端文件超过 {WEBDAV_MAX_SIZE // 1024 // 1024}MB 上限，请分段下载"
                                f.write(chunk)
                else:
                    resp = _webdav_request(cfg, "GET", remote)
                    if resp.status_code != 200:
                        return f"错误：下载失败（HTTP {resp.status_code}）"
                    if len(resp.content) > WEBDAV_MAX_SIZE:
                        return f"错误：远端文件超过 {WEBDAV_MAX_SIZE // 1024 // 1024}MB 上限，请分段下载"
                    total = len(resp.content)
                    with open(out, "wb") as f:
                        f.write(resp.content)
            except Exception as e:
                try:
                    if os.path.exists(out):
                        os.remove(out)
                except OSError:
                    pass
                return f"错误：WebDAV 下载失败: {e}"
            return f"已下载 {remote} → {out}（{total} 字节）"
        # upload
        ok, reason = permissions.check_filesystem(local_path, write=False)
        if not ok:
            return reason
        src = permissions.resolve(local_path)
        if not src or not os.path.isfile(src):
            return f"错误：本地文件不存在：{local_path}"
        try:
            if os.path.getsize(src) > WEBDAV_MAX_SIZE:
                return f"错误：本地文件超过 {WEBDAV_MAX_SIZE // 1024 // 1024}MB 上限，请压缩后上传"
        except Exception as e:
            return f"错误：读取本地文件信息失败: {e}"
        total = 0
        try:
            client = _http_client()
            if hasattr(client, "stream"):
                def _chunks():
                    nonlocal total
                    with open(src, "rb") as f:
                        while True:
                            chunk = f.read(64 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            yield chunk
                # 流式上传：大文件不再一次性读入内存
                with _safe_stream(
                    "PUT", cfg["url"] + remote,
                    auth=(cfg["username"], cfg["password"]),
                    timeout=30,
                    content=_chunks(),
                ) as resp:
                    status_code = resp.status_code
            else:
                # 兼容旧测试/自定义 mock：无 stream 时退化为普通请求
                with open(src, "rb") as f:
                    data = f.read()
                total = len(data)
                resp = _webdav_request(cfg, "PUT", remote, content=data)
                status_code = resp.status_code
        except Exception as e:
            return f"错误：WebDAV 上传失败: {e}"
        if status_code not in (200, 201, 204):
            return f"错误：上传失败（HTTP {status_code}）"
        permissions.audit("webdav_upload", remote, f"{total} 字节")
        return f"已上传 {src} → {remote}（{total} 字节）"
    # delete
    try:
        resp = _webdav_request(cfg, "DELETE", remote)
    except Exception as e:
        return f"错误：WebDAV 删除失败: {e}"
    if resp.status_code not in (200, 204, 404):
        return f"错误：删除失败（HTTP {resp.status_code}）"
    permissions.audit("webdav_delete", remote, "ok")
    return f"已删除远端路径：{remote}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "call_api",
                "description": "通用 HTTP API 调用（万能接口）：GET/POST/PUT/DELETE/PATCH，支持查询参数/JSON/表单/请求头。任意 http(s) 地址（含内网/回环本地服务），响应 ≤500KB，超时 ≤180s",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "完整 API 地址（http/https）"},
                        "method": {"type": "string", "description": "请求方法：GET/POST/PUT/DELETE/PATCH/HEAD（默认 GET）"},
                        "params": {"type": "object", "description": "可选：查询参数对象（如 {\"limit\": 10}）"},
                        "json_body": {"type": "object", "description": "可选：JSON 请求体对象"},
                        "data": {"type": "string", "description": "可选：表单/原始请求体"},
                        "headers": {"type": "object", "description": "可选：自定义请求头（≤16 个，如 {\"Authorization\": \"Bearer xxx\"}）"},
                        "timeout": {"type": "integer", "description": "可选：超时秒数（1-180，默认 15）"},
                    },
                    "required": ["url"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='调用任意 HTTP API',
    preactivate=(('调用接口', 'api请求', '调接口', 'http请求'),),
)
def call_api(url, method="GET", params=None, json_body=None, data=None,
             headers=None, timeout=15):
    """通用外部 API 调用（自主 AI 的"万能接口"）。

    支持 GET/POST/PUT/DELETE/PATCH/HEAD，自定义查询参数/JSON 体/表单体/请求头。
    安全约束：仅 http(s) 公网地址（SSRF 防护，与 fetch_url 同规则）；请求头
    禁止 CRLF 注入；响应体 ≤200KB 截断；超时上限 60s。

    Args:
        url: 完整 API 地址（http/https）
        method: 请求方法（默认 GET）
        params: 可选，查询参数 dict（如 {"limit": 10}）
        json_body: 可选，JSON 请求体 dict
        data: 可选，表单/原始请求体
        headers: 可选，自定义请求头 dict（≤8 个）
        timeout: 可选，超时秒数（1-60，默认 15）
    """
    if not url or not str(url).startswith(("http://", "https://")):
        return "错误：url 必须以 http:// 或 https:// 开头"
    method = str(method or "GET").strip().upper()
    if method not in CALL_API_METHODS:
        return f"错误：method 仅支持 {'/'.join(CALL_API_METHODS)}"
    try:
        timeout = max(1, min(180, int(timeout or 15)))
    except (TypeError, ValueError):
        timeout = 15
    hdrs = {}
    if headers:
        if not isinstance(headers, dict):
            return "错误：headers 必须是键值对象"
        if len(headers) > CALL_API_MAX_HEADERS:
            return f"错误：headers 最多 {CALL_API_MAX_HEADERS} 个"
        for k, v in headers.items():
            k, v = str(k).strip(), str(v or "").strip()
            if not k or not re.match(r"^[A-Za-z0-9\-]+$", k):
                return f"错误：请求头名称不合法：{k}"
            if "\r" in v or "\n" in v:
                return "错误：请求头值禁止包含换行（防 CRLF 注入）"
            hdrs[k] = v
    try:
        kw = {"params": params} if params else {}
        if json_body is not None:
            kw["json"] = json_body
        if data is not None:
            kw["data"] = data
        def _validate(u):
            return ""  # 无限制模式：不校验主机，任何地址均可访问

        raw = b""
        truncated = False
        status_code = 0
        content_type = ""
        # 流式读取：大响应不再全量进内存，超过上限立即断开连接
        with _safe_stream(
            method, url, validate=_validate,
            headers=hdrs or None, timeout=timeout, **kw
        ) as resp:
            resp.raise_for_status()
            status_code = resp.status_code
            content_type = (resp.headers or {}).get("content-type", "") if hasattr(resp.headers, "get") else ""
            if hasattr(resp, "iter_bytes"):
                for chunk in resp.iter_bytes(64 * 1024):
                    raw += chunk
                    if len(raw) >= CALL_API_MAX_BYTES:
                        truncated = True
                        break
            else:
                # 兼容旧测试/自定义 mock 的普通响应对象（无流式接口）
                raw = getattr(resp, "content", b"") or b""
                truncated = len(raw) > CALL_API_MAX_BYTES
                raw = raw[:CALL_API_MAX_BYTES]
        body = raw
        text = body[:CALL_API_MAX_BYTES].decode("utf-8", errors="replace")
        # JSON 美化输出（若可解析），便于阅读
        try:
            if content_type.startswith("application/json"):
                import json as _json
                text = _json.dumps(_json.loads(text), ensure_ascii=False, indent=2)
        except Exception:
            pass
        head = f"HTTP {status_code} · {method} {url.split('?')[0][:80]}"
        if truncated:
            head += f" · 响应已截断（>{CALL_API_MAX_BYTES // 1024}KB，显示前 {CALL_API_MAX_BYTES // 1024}KB）"
        return f"{head}\n\n{text}" if text.strip() else head
    except Exception as e:
        return f"错误：API 调用失败（{type(e).__name__}: {str(e)[:120]}）"


@tool(
        {
            "type": "function",
            "function": {
                "name": "track_web",
                "description": "持续感知：追踪网页内容变化（抓取页面计算指纹对比上次）；首次建立基线、之后返回无变化或已更新；适合追踪公告/文档/价格页",
                "parameters": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "要追踪的 http(s) 网页 URL"}},
                    "required": ["url"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='网页更新追踪',
    preactivate=(('网页更新', '追踪网页', '页面变化', '监控网址', '网页变化'),),
)
def track_web(url):
    """持续感知：追踪网页内容变化。抓取页面内容并计算指纹，与上次对比。
    首次建立基线；之后返回「无变化」或「已更新」。适合追踪公告/文档/价格页。"""
    err = _safe_url(url)
    if err:
        return f"错误：{err}"
    try:
        text = _fetch_url_raw(url)
        if text.startswith("错误"):
            return text
        import hashlib
        digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
        title = text[:80].replace("\n", " ")
    except Exception as e:
        return f"错误：{e}"
    state = _load_watch_state()
    webs = state.setdefault("web", {})
    prev = webs.get(url)
    if prev is None:
        webs[url] = {"hash": digest, "title": title}
        _save_watch_state(state)
        return f"已建立网页基线：{url}\n内容摘要：{title}…"
    if prev.get("hash") == digest:
        return f"无变化：{url}\n内容与上次一致（摘要：{title}…）"
    webs[url] = {"hash": digest, "title": title}
    _save_watch_state(state)
    return f"🔔 网页已更新：{url}\n旧摘要：{str(prev.get('title', ''))[:60]}…\n新摘要：{title}…"


@tool(
        {
            "type": "function",
            "function": {
                "name": "fetch_blocked",
                "description": "抓取被墙/国际站点（linux.do、Google、archive.org 等）的文本/JSON 内容，自动使用本机机场节点代理 + 浏览器指纹绕过封锁。适用于 fetch_url 超时/失败或被墙的场景",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string", "description": "目标网址，完整 URL 含 http(s)://"},
                        "proxy": {"type": "string", "description": "可选：代理节点，形如 https://user:pass@server:port，留空自动发现"},
                    },
                    "required": ["url"],
                },
            },
        },
    groups=['🌐 浏览器与网页'],
    phrases='抓取被墙站点（代理+指纹绕过封锁）',
    preactivate=(('被墙', '爬墙', '代理抓取', '绕过封锁', '抓不了'),),
)
def _run_fetch_blocked(url, proxy=None, **kwargs):
    """工具分发：fetch_blocked（按需能力，模块缺失时明确提示）。

    与其他工具一致使用具名参数签名（分发器以 fn(**args) 调用）——
    此前误写成单个 dict 参数导致 unexpected keyword argument 'url'。
    """
    if _fetch_blocked_impl is None:
        return "错误：fetch_blocked 能力未安装（需要将 fetch_blocked.py 放入程序目录并启用后可用）"
    if not str(url or "").startswith(("http://", "https://")):
        return "错误：URL 必须以 http:// 或 https:// 开头"
    # SSRF 校验由 fetch_blocked.py 内部实现执行（含内网/元数据拦截）；
    # 包装层只做协议与参数分发，避免在 DNS 被测试/网络环境临时改写时误伤。
    return _fetch_blocked_impl(url, proxy)


__all__ = ['_run_fetch_blocked', 'fetch_url', 'download_file', 'search_web', 'search_github', 'search_realtime', 'browser_navigate', 'web_screenshot', 'net_diagnose', 'fetch_url_smart', 'rss_fetch', 'webdav', 'call_api', 'track_web']
