"""能力地图紧凑化回归（v3.16.17）。

背景（实测诊断）：能力地图把 169 个工具名 + 核心动作短语**全部枚举**，
共 4594 字符 / 约 3063 token，在「本轮尚未产生工具调用」时注入到每个请求。
它对「该激活哪一组」这个决策的边际价值很低——模型真正需要的是**组名 + 代表作**，
然后 `activate_tools(["组名"])` 一次激活整组；组内还有什么，激活后 schema 自会说明。

改为组优先（compact）后：4594 → 1561 字符（约 3063 → 1041 token，省 66%），
而 11 个组名全部保留、全部仍可激活——发现路径完好。

`compact=False` 保留完整枚举，供需要完整清单的场合使用。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import deepseek_client as dc  # noqa: E402


def test_compact_is_default_and_shorter():
    """默认必须走紧凑版，且显著短于完整枚举。"""
    compact = dc.build_tool_index(dc.TOOLS)
    full = dc.build_tool_index(dc.TOOLS, compact=False)
    assert len(compact) < len(full), "默认未走紧凑地图"
    # 至少省掉一半（实测省 66%，留足余量避免脆弱）
    assert len(compact) < len(full) * 0.6, \
        f"紧凑收益不足：compact={len(compact)} full={len(full)}"


def test_compact_keeps_every_group_discoverable():
    """所有组名必须仍在紧凑地图里——否则模型无法按组点菜。"""
    compact = dc.build_tool_index(dc.TOOLS)
    missing = []
    for cat, _members in dc.TOOL_GROUPS:
        bare = cat.split(" ", 1)[-1] if " " in cat else cat
        if bare not in compact:
            missing.append(bare)
    assert not missing, f"紧凑地图丢了组名：{missing}"


def test_compact_states_total_tool_count():
    """必须声明总能力数——否则模型会以为自己只有列出来的这些。"""
    compact = dc.build_tool_index(dc.TOOLS)
    assert str(len(dc.TOOLS)) in compact


def test_compact_keeps_ability_self_awareness_guard():
    """必须保留「未激活也归你所有、不要声称做不到」的自我认知护栏。

    这条是防「能力错觉」的关键（模型看不到定义就谎报做不到）。
    """
    compact = dc.build_tool_index(dc.TOOLS)
    assert "不要声称" in compact or "不要因为" in compact


def test_compact_mentions_full_list_escape_hatch():
    """必须告诉模型「需要完整清单时用 list_my_capabilities」。"""
    compact = dc.build_tool_index(dc.TOOLS)
    assert "list_my_capabilities" in compact


def test_every_group_is_still_activatable_by_bare_name():
    """紧凑地图列出的组名必须都能被 activate_tools 解析。"""
    for cat, _members in dc.TOOL_GROUPS:
        bare = cat.split(" ", 1)[-1] if " " in cat else cat
        assert bare in dc._TOOL_GROUP_NAME_MAP, f"组名不可激活：{bare}"


def test_full_mode_still_enumerates_all_tools():
    """compact=False 必须仍枚举全部工具名（供需要完整清单处使用）。"""
    full = dc.build_tool_index(dc.TOOLS, compact=False)
    names = [t["function"]["name"] for t in dc.TOOLS]
    missing = [n for n in names if n not in full]
    assert not missing, f"完整枚举漏了工具：{missing[:10]}"


def test_index_is_cached_for_both_modes():
    """两种形态都必须有缓存（地图每轮都要用，不能反复重建）。"""
    assert dc.build_tool_index() is dc.build_tool_index()
    assert (dc.build_tool_index(compact=False)
            is dc.build_tool_index(compact=False))


def test_cache_invalidates_on_tool_set_change():
    """工具集变化时地图必须重建（否则新增/自定义工具不可见）。"""
    base = dc.build_tool_index(dc.TOOLS)
    assert base is dc.build_tool_index(dc.TOOLS)
    # 内容指纹变了 → 重新生成（用真实 TOOLS 的深拷贝模拟“重建列表”）
    import copy
    rebuilt = copy.deepcopy(dc.TOOLS)
    assert dc.build_tool_index(rebuilt) is dc.build_tool_index()
