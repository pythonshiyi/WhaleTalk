# -*- coding: utf-8 -*-
"""工具描述保真回归（禁止以「省 token」为由删减描述）。

背景：compact_tool_schema（现 normalize_tool_schema）曾对描述做两类破坏性处理——
  ① 用 12 条正则删除工具描述里的中文括号内容，再按 130 字硬截断；
  ② 参数级无差别删除全部「（…）」后再按 40 字硬截断。
实测收益仅 9 字（≈全量 schema 的 0.01%），却让模型丢失了正确调用工具所必需的
信息（格式示例 / 取值清单 / 默认值 / 取值上限 / 依赖提示 / 语义契约）。

本测试把「描述不得被删减」锁成回归契约：compact 只允许做空白归一，
任何实义字符的丢失都视为回归。

对应门禁：tools/validate_tools.py 第 2 项（规范化无损）与第 5 项（描述保真）。
"""
import re

import deepseek_client as dc


def _sq(s):
    """去空白指纹：与门禁同口径，用于判定内容是否被改动。"""
    return re.sub(r"\s+", "", str(s or ""))


def _props(tool):
    return (tool.get("function") or {}).get("parameters", {}).get("properties") or {}


def test_normalize_preserves_every_tool_description():
    """147 个工具的工具级描述，compact 前后内容必须完全一致（仅空白可归一）。"""
    changed = []
    for t in dc.TOOLS:
        name = t["function"]["name"]
        c = dc.normalize_tool_schema(t)
        if _sq(t["function"].get("description")) != _sq(c["function"].get("description")):
            changed.append(name)
    assert not changed, f"compact 改动了以下工具的描述内容（应无损）：{changed}"


def test_normalize_preserves_every_param_description():
    """全部参数的参数级描述，compact 前后内容必须完全一致。"""
    changed = []
    for t in dc.TOOLS:
        name = t["function"]["name"]
        c = dc.normalize_tool_schema(t)
        for pn, pv in _props(t).items():
            if _sq(pv.get("description")) != _sq(_props(c).get(pn, {}).get("description")):
                changed.append(f"{name}.{pn}")
    assert not changed, f"compact 改动了以下参数描述内容（应无损）：{changed[:20]}"


def test_normalize_does_not_truncate_long_descriptions():
    """超长描述不得被截断（历史 bug：130 字硬截断 + 省略号，且 off-by-one 产出 131 字）。"""
    long_tool = {
        "type": "function",
        "function": {
            "name": "probe_tool",
            "description": "甲" * 500,
            "parameters": {"type": "object", "properties": {
                "p": {"type": "string", "description": "乙" * 200},
            }},
        },
    }
    c = dc.normalize_tool_schema(long_tool)
    assert c["function"]["description"] == "甲" * 500, "工具描述被截断了"
    assert _props(c)["p"]["description"] == "乙" * 200, "参数描述被截断了"
    assert "…" not in c["function"]["description"], "不应引入截断省略号"


def test_normalize_keeps_parenthetical_info():
    """历史被正则删除的括号信息（依赖/审批/可选/默认/Beta）必须保留。"""
    cases = [
        "（可选依赖）", "（需安装）", "（需审批）", "（需用户确认）",
        "（敏感）", "（Beta）", "（默认为 5）", "（默认 120 秒）",
        "（保证生效）", "（可能不严格）",
    ]
    for text in cases:
        tool = {"type": "function", "function": {
            "name": "probe", "description": f"前置说明{text}后置说明",
            "parameters": {"type": "object", "properties": {}},
        }}
        c = dc.normalize_tool_schema(tool)
        assert text in c["function"]["description"], f"括号信息被删除：{text}"


def test_previously_damaged_params_keep_their_constraints():
    """曾因截断丢失关键约束的参数，其信息必须仍在（防回归的代表性样本）。"""
    by_name = {t["function"]["name"]: t for t in dc.TOOLS}
    # (工具, 参数) -> 描述里必须出现的关键词
    samples = {
        ("search_web", "num"): "1-20",
        ("read_file", "max_lines"): "2000",
        ("edit_file", "replacements"): "正则",       # JSON 格式示例曾整体被删
        ("batch_rename", "pattern"): "字面",
        ("database_execute", "sql"): "WHERE",
        ("run_python", "code"): "8000",
    }
    for (tool, param), kw in samples.items():
        assert tool in by_name, f"工具缺失：{tool}"
        props = _props(by_name[tool])
        assert param in props, f"参数缺失：{tool}.{param}"
        d = str(props[param].get("description") or "")
        assert kw in d, f"{tool}.{param} 的描述丢失关键信息「{kw}」：{d[:80]}"


def test_dependency_hints_survive_on_tool_level():
    """工具级依赖提示（模型据此判断能力是否可用）不得被删。"""
    by_name = {t["function"]["name"]: t["function"].get("description", "") for t in dc.TOOLS}
    for tool, kw in {
        "web_screenshot": "playwright",
        "epub_read": "ebooklib",
        "speech_to_text": "faster-whisper",
    }.items():
        assert tool in by_name, f"工具缺失：{tool}"
        assert kw in by_name[tool], f"{tool} 描述丢失依赖提示「{kw}」：{by_name[tool][:80]}"


def test_normalize_desc_handles_non_string():
    """_normalize_desc 对异常 schema 输入必须安全（自定义/插件工具可能传非字符串）。"""
    assert dc._normalize_desc(None) is None
    assert dc._normalize_desc(123) == 123
    assert dc._normalize_desc("a  \n b") == "a b"


def test_normalize_is_pure_and_ordered():
    """normalize_tools_list 不得修改入参、且保持顺序。"""
    before = dc.TOOLS[0]["function"]["description"]
    names = [t["function"]["name"] for t in dc.TOOLS]
    out = dc.normalize_tools_list(dc.TOOLS)
    assert dc.TOOLS[0]["function"]["description"] == before, "compact 不应修改入参"
    assert [t["function"]["name"] for t in out] == names, "compact 不应改变顺序"


def test_legacy_function_names_still_importable():
    """兼容别名：旧名 compact_* 必须仍可调用且与新名是同一函数对象。

    语义已从「有损压缩」改为「无损规范化」，但历史脚本/插件/用户自定义工具
    可能仍 `from deepseek_client import compact_tool_schema`，故保留别名。
    """
    assert dc.compact_tool_schema is dc.normalize_tool_schema
    assert dc.compact_tools_list is dc.normalize_tools_list
    tool = {"type": "function", "function": {
        "name": "probe", "description": "说明（含括号信息）",
        "parameters": {"type": "object", "properties": {}},
    }}
    assert dc.compact_tool_schema(tool)["function"]["description"] == "说明（含括号信息）"
