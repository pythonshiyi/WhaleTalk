# -*- coding: utf-8 -*-
"""能力按需加载机制优化（方案 A/B/C）回归。

对应审查报告：data/workspace/148项能力自描述/能力按需加载机制-审查报告.md

  方案 A —— `_preactivate_from_messages` 扫描窗口 1 → N：
            省略式追问（"继续"/"然后呢"）不再丢失预激活。
  方案 B —— 新增只读自省工具 `list_my_capabilities`：
            自我认知内生、可核验（total 直取注册表，与 activate_tools 同源）。
  方案 C —— 预激活命中埋点：让手工维护的 `_HINT_ORDER` 有数据可依。

回归护栏：工具索引缓存指纹（prompt cache 不破）、默认输出保持轻量。
"""
import json

import pytest

import deepseek_client as dc

CAP_TOOL = "list_my_capabilities"


@pytest.fixture()
def hint_state(tmp_path, monkeypatch):
    """隔离命中计数：指向临时落盘文件 + 清空内存计数（不污染真实 DATA_DIR）。"""
    target = tmp_path / "preactivate_hits.json"
    monkeypatch.setattr(dc, "HINT_HITS_FILE", str(target), raising=False)
    monkeypatch.setattr(dc, "_hint_hits", {}, raising=False)
    monkeypatch.setattr(dc, "_hint_hits_loaded", True, raising=False)
    return target


def _tools_for(keyword):
    """按关键词反查该条 hint 预激活的工具集合（不硬编码工具名，随表自动跟随）。"""
    for kws, tools in dc._PREACTIVATE_HINTS:
        if keyword in kws:
            return set(tools)
    raise AssertionError("_HINT_ORDER 中找不到关键词 %r" % keyword)


# ── 方案 B：自省工具 ─────────────────────────────────────────────


def test_capability_tool_registered_in_all_six_layers():
    names = [t["function"]["name"] for t in dc.TOOLS]
    assert CAP_TOOL in names, "必须进入 TOOLS（第 1 层）"
    impl = dc.TOOL_CALL_MAP.get(CAP_TOOL)
    assert callable(impl), "必须进入 TOOL_CALL_MAP（第 3 层）"
    assert impl is getattr(dc, CAP_TOOL), "CALL_MAP 应指向同一函数对象（re-export 生效）"
    assert impl.__module__ == "agent_tools.tool_system", "应归属 tool_system 域模块"
    grouped = {m for _, ms in dc.TOOL_GROUPS for m in ms}
    assert CAP_TOOL in grouped, "必须进入能力地图分组（第 5 层）"
    assert CAP_TOOL in dc._TOOL_ACTION_PHRASES, "必须有动作短语（第 4 层）"
    pre = {t for _, ts in dc._PREACTIVATE_HINTS for t in ts}
    assert CAP_TOOL in pre, "必须参与关键词预激活（第 6 层）"
    assert CAP_TOOL in dc._TOOL_ORDER, "必须在顺序表内（顺序表缺项会直接抛错）"


def test_reported_total_matches_live_registry():
    out = dc.list_my_capabilities()
    assert "%d 项能力" % len(dc.TOOLS) in out, "总数必须直取注册表实时数据"


def test_group_filter_lists_whole_group():
    cat, members = next((c, m) for c, m in dc.TOOL_GROUPS if "数据与文档" in c)
    out = dc.list_my_capabilities(group="数据与文档")
    assert "%d 项）" % len(members) in out
    for n in members:
        assert n in out, "整组成员 %s 应出现在明细里" % n
    # 带 emoji 的规范组名同样可用（activate_tools 的两种键都支持）
    assert dc.list_my_capabilities(group=cat) == out


def test_unknown_group_is_honest_about_available_groups():
    out = dc.list_my_capabilities(group="绝无此组")
    assert "没有名为" in out
    for c, _ in dc.TOOL_GROUPS:
        assert (c.split(" ", 1)[-1] if " " in c else c) in out


def test_query_matches_tool_name_and_phrase():
    out = dc.list_my_capabilities(query="pdf")
    assert "pdf_extract" in out and "html_to_pdf" in out
    assert "匹配" in out


def test_query_no_match_does_not_invent_capabilities():
    out = dc.list_my_capabilities(query="绝无此能力zzz")
    assert "没有匹配" in out
    assert "共 %d 项" % len(dc.TOOLS) in out


def test_cross_group_membership_math_is_explained():
    """跨组工具会让「成员合计 > 总数」，必须解释清楚——这正是「148 与 149 对不上」的成因。"""
    memberships = sum(len(ms) for _, ms in dc.TOOL_GROUPS)
    dup = memberships - len(dc.TOOLS)
    out = dc.list_my_capabilities()
    assert dup > 0, "当前注册表存在跨组工具，本用例前提成立"
    assert "跨组重复归属" in out
    assert "%d 项" % dup in out


def test_default_output_stays_cheap():
    """默认必须只给分组汇总：若退化成全量 dump，等于把按需加载省下的上下文又还回去。"""
    out = dc.list_my_capabilities()
    assert "list_my_capabilities(group=" in out, "应给出下钻指引"
    assert "html_to_ppt" not in out, "默认输出不应包含组内明细"
    assert len(out) < 800, "默认输出应保持轻量（实测 %d 字符）" % len(out)


# ── 方案 A：预激活扫描窗口 ───────────────────────────────────────


def test_window_constant_is_conservative():
    assert dc._PREACTIVATE_WINDOW == 3


def test_omitted_followup_keeps_preactivation(hint_state):
    """核心场景：首轮含关键词、末轮是「继续」——原实现只扫 1 条会整体失效。"""
    msgs = [
        {"role": "user", "content": "帮我抓取 https://example.com 的内容"},
        {"role": "assistant", "content": "好的"},
        {"role": "user", "content": "继续"},
    ]
    activated = dc._preactivate_from_messages(msgs, set())
    assert _tools_for("网页") <= activated


def test_followup_still_covered_two_rounds_back(hint_state):
    msgs = [
        {"role": "user", "content": "帮我抓取 https://example.com 的内容"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "继续"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "然后呢"},
    ]
    assert _tools_for("网页") <= dc._preactivate_from_messages(msgs, set())


def test_message_beyond_window_is_not_scanned(hint_state):
    """窗口边界：带关键词的消息落到第 4 条 user 消息时不应再触发（防误激活无上限）。"""
    msgs = [{"role": "user", "content": "帮我抓取 https://example.com 的内容"}]
    for i in range(3):
        msgs += [
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "收到%d" % i},
        ]
    activated = dc._preactivate_from_messages(msgs, set())
    assert not (_tools_for("网页") & activated)


def test_blank_user_message_does_not_consume_window(hint_state):
    """纯图片/空文本消息不占窗口名额（否则追问场景刚修好又会漏回去）。"""
    msgs = [
        {"role": "user", "content": "帮我抓取 https://example.com 的内容"},
        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]},
        {"role": "user", "content": "继续"},
        {"role": "user", "content": "然后呢"},
    ]
    assert _tools_for("网页") <= dc._preactivate_from_messages(msgs, set())


def test_preset_tools_are_preserved(hint_state):
    """预激活是「并集」语义：调用方传入的 preset 集合不得被覆盖/清空。"""
    activated = dc._preactivate_from_messages([{"role": "user", "content": "你好"}], {"read_file"})
    assert "read_file" in activated


def test_no_user_message_returns_unchanged(hint_state):
    activated = {"write_file"}
    assert dc._preactivate_from_messages(
        [{"role": "assistant", "content": "在的"}], activated
    ) is activated


# ── 方案 C：命中埋点 ─────────────────────────────────────────────


def test_hit_is_counted_and_persisted(hint_state):
    dc._preactivate_from_messages(
        [{"role": "user", "content": "帮我抓取 https://example.com 的内容"}], set()
    )
    assert dc.hint_hits_snapshot().get("网页") == 1
    assert json.loads(hint_state.read_text(encoding="utf-8")) == {"网页": 1}


def test_hit_counter_accumulates_across_calls(hint_state):
    msg = [{"role": "user", "content": "帮我抓取 https://example.com 的内容"}]
    dc._preactivate_from_messages(msg, set())
    dc._preactivate_from_messages(msg, set())
    assert dc.hint_hits_snapshot().get("网页") == 2


def test_miss_is_not_counted(hint_state):
    dc._preactivate_from_messages([{"role": "user", "content": "hello there"}], set())
    assert dc.hint_hits_snapshot() == {}


def test_persist_failure_is_silent(hint_state, tmp_path, monkeypatch):
    """埋点绝不干扰对话主流程：落盘失败只吞掉异常，计数仍在内存生效。"""
    monkeypatch.setattr(dc, "HINT_HITS_FILE", str(tmp_path), raising=False)  # 目录当文件 → 必失败
    dc._preactivate_from_messages(
        [{"role": "user", "content": "帮我抓取 https://example.com 的内容"}], set()
    )
    assert dc.hint_hits_snapshot().get("网页") == 1


def test_snapshot_is_read_only(hint_state):
    dc._preactivate_from_messages(
        [{"role": "user", "content": "帮我抓取 https://example.com 的内容"}], set()
    )
    snap = dc.hint_hits_snapshot()
    snap["网页"] = 999
    assert dc.hint_hits_snapshot()["网页"] == 1, "快照必须是副本，外部改动不得回写计数"


def test_corrupt_hits_file_degrades_to_empty(hint_state, tmp_path, monkeypatch):
    target = tmp_path / "broken.json"
    target.write_text("{ 不是合法 JSON", encoding="utf-8")
    monkeypatch.setattr(dc, "HINT_HITS_FILE", str(target), raising=False)
    monkeypatch.setattr(dc, "_hint_hits", {}, raising=False)
    monkeypatch.setattr(dc, "_hint_hits_loaded", False, raising=False)
    assert dc.hint_hits_snapshot() == {}


def test_hint_report_exposes_never_hit_keywords(hint_state, tmp_path, monkeypatch):
    """usage_report 出口：把「从未命中」摊开，才能数据驱动删改关键词。"""
    from datetime import date

    stats = tmp_path / "stats.json"
    stats.write_text(
        json.dumps(
            {date.today().isoformat(): {"deepseek-flash":
                {"prompt": 100, "completion": 10, "cache_hit": 5, "cache_miss": 95}}}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dc, "STATS_FILE", str(stats), raising=False)
    dc._preactivate_from_messages(
        [{"role": "user", "content": "帮我抓取 https://example.com 的内容"}], set()
    )
    out = dc.usage_report(7)
    assert "预激活关键词命中统计" in out, "命中统计必须接进用量报告出口"
    assert "网页 1 次" in out
    assert "从未命中" in out


# ── 回归护栏 ─────────────────────────────────────────────────────


def test_tool_index_cache_returns_same_object_when_unchanged():
    """缓存键为内容指纹：内容不变必须命中缓存，否则 index 漂移会打穿 prompt cache。"""
    assert dc.build_tool_index() is dc.build_tool_index()


def test_tool_index_cache_survives_rebuilt_list():
    """传入重建列表（id 变、内容不变）也必须命中缓存——刻意不用 id() 的原因。"""
    rebuilt = [dict(t) for t in dc.TOOLS]
    assert dc.build_tool_index(rebuilt) is dc.build_tool_index()


def test_tool_index_lists_new_capability():
    assert CAP_TOOL in dc.build_tool_index()


def test_smart_hint_reports_live_total():
    hint = dc.build_smart_hint(loaded={"read_file"})
    assert "共拥有 %d 项能力" % len(dc.TOOLS) in hint


def test_activate_tool_description_backfills_new_total():
    """activate_tools 描述里的总数/组名是回填的，新增工具后必须同步。"""
    desc = dc.ACTIVATE_TOOL["function"]["description"]
    assert "%d 项能力" % len(dc.TOOLS) in desc
    for cat, _ in dc.TOOL_GROUPS:
        bare = cat.split(" ", 1)[-1] if " " in cat else cat
        assert bare in desc
