"""自我洞察（insight）单元测试：能力热力图 / 自我述职 / 报告渲染。

均为纯函数，不依赖项目运行时（不 import deepseek_client），可独立运行。
"""
import os
import sys
from datetime import date

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import insight  # noqa: E402


def _ts(days_ago=0):
    from datetime import timedelta
    return (date.today() - timedelta(days=days_ago)).isoformat() + " 10:00:00"


# ── 能力热力图 ────────────────────────────────────────────────────────────

def test_heatmap_counts_tools_and_chains():
    tasks = [
        {"title": "A", "chain": ["read_file", "write_file", "run_python"], "ts": _ts(0)},
        {"title": "B", "chain": ["read_file", "read_file", "search_web"], "ts": _ts(1)},
    ]
    hm = insight.build_heatmap(tasks=tasks, days=30)
    assert hm["summary"]["tasks"] == 2
    assert hm["summary"]["tool_calls"] == 6
    by = {r["tool"]: r for r in hm["tools"]}
    assert by["read_file"]["calls"] == 3          # 出现 3 次
    assert by["read_file"]["tasks"] == 2          # 出现在 2 个任务
    assert hm["summary"]["avg_chain_len"] == 3.0
    assert "read_file" in hm["hot"]


def test_heatmap_window_filters_old_tasks():
    tasks = [
        {"title": "old", "chain": ["a", "b"], "ts": _ts(40)},
        {"title": "new", "chain": ["c"], "ts": _ts(1)},
    ]
    hm = insight.build_heatmap(tasks=tasks, days=7)
    assert hm["summary"]["tasks"] == 1
    assert {r["tool"] for r in hm["tools"]} == {"c"}


def test_heatmap_struggles_from_failures():
    failures = [
        {"tool": "fetch_url", "hits": 5, "resolved": False, "last_ts": _ts(0)},
        {"tool": "fetch_url", "hits": 1, "resolved": True, "last_ts": _ts(1)},
        {"tool": "run_python", "hits": 2, "resolved": False, "last_ts": _ts(2)},
    ]
    hm = insight.build_heatmap(failures=failures, days=30)
    by = {r["tool"]: r for r in hm["tools"]}
    assert by["fetch_url"]["failure_hits"] == 6
    assert by["fetch_url"]["unresolved"] == 1
    assert hm["summary"]["unresolved"] == 2
    assert hm["struggles"][0]["tool"] == "fetch_url"


def test_heatmap_crystallized_and_preactivate():
    prompts = [
        {"name": "自动技能 · 周报", "auto_skill": True, "hits": 3, "source_chain": ["a", "b", "c"]},
        {"name": "普通指令", "auto_skill": False},
    ]
    hm = insight.build_heatmap(prompt_items=prompts, hint_hits={"搜索": 4, "写": 2}, days=30)
    assert hm["summary"]["crystallized"] == 1
    assert hm["crystallized"][0]["name"].startswith("自动技能")
    assert hm["preactivate"][0] == {"keyword": "搜索", "hits": 4}


def test_heatmap_empty_is_safe():
    hm = insight.build_heatmap(days=30)
    assert hm["summary"]["tasks"] == 0
    assert hm["tools"] == []
    assert hm["hot"] == []


# ── 自我述职 ──────────────────────────────────────────────────────────────

def test_self_report_aggregates_sections():
    decisions = [
        {"ts": _ts(1), "decision": "采用镜像源", "reason": "直连超时", "status": "kept"},
        {"ts": _ts(2), "decision": "改用 SQLite", "status": "open"},
        {"ts": _ts(30), "decision": "旧决定", "status": "kept"},
    ]
    goals = [
        {"title": "完成报价目录", "status": "active", "progress": "40%"},
        {"title": "写文档", "status": "done"},
    ]
    evolution = {"proposals": [{"id": "P-1"}], "adopted": [
        {"date": _ts(1), "title": "优化搜索", "implemented": "短超时"},
        {"date": _ts(60), "title": "远古进化"},
    ]}
    tasks = [{"title": "任务1", "chain": ["a", "b"], "ts": _ts(0)}]
    usage = {date.today().isoformat(): {"deepseek-flash": {
        "prompt": 1000, "completion": 200, "cache_hit": 800, "cache_miss": 200}}}
    prompts = [{"name": "自动技能 · X", "auto_skill": True, "hits": 2}]

    rep = insight.build_self_report(
        decisions=decisions, goals=goals, evolution=evolution,
        tasks=tasks, usage=usage, prompt_items=prompts, days=7)
    assert rep["summary"]["decisions"] == 2          # 30 天前的被窗口过滤
    assert rep["summary"]["tasks"] == 1
    assert rep["decisions"]["open"] == 1
    assert rep["goals"]["active_count"] == 1 and rep["goals"]["done_count"] == 1
    assert len(rep["evolution"]["items"]) == 1       # 只保留窗口内采纳
    assert rep["growth"]["crystallized"] == 1
    assert rep["usage"]["prompt_tokens"] == 1000


def test_self_report_render_has_sections():
    rep = insight.build_self_report(
        decisions=[{"ts": _ts(1), "decision": "D", "status": "kept"}],
        goals=[{"title": "G", "status": "active"}], days=7)
    md = insight.render_report(rep, title="自我述职")
    for frag in ("自我述职", "做了什么", "决定", "目标", "成长"):
        assert frag in md


def test_self_report_empty_is_safe():
    rep = insight.build_self_report(days=7)
    assert rep["summary"]["tasks"] == 0
    md = insight.render_report(rep)
    assert "自我述职" in md
