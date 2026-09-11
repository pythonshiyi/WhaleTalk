# -*- coding: utf-8 -*-
"""tool_brain —— P0-1 批量拆分（工具域模块）：🧠 记忆与定时任务.

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
阈值常量/锁统一从 shared 导入（P1-3 下沉：见 shared.py「工具域阈值与锁」节）；
仅剩余辅助函数仍依赖主文件加载顺序契约（在 `from agent_tools import *` 前已定义）。
"""

import difflib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime

import permissions
import stores

from shared import clamp_int, cron_field_ok, MEMORY_MAX_ITEMS, MEMORY_MAX_TEXT, _MEMORY_LOCK, SELF_PROFILE_LOCK, _SELF_PROFILE_LIST_FIELDS, SCHEDULES_LOCK, _WORKFLOW_LOCK
from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import deepseek_client as _dc  # 可变注入配置动态访问（dc.X 注入后立即生效）
from deepseek_client import (

    _brain_sync_delete,
    _brain_sync_memory,
    _brain_sync_update,
    _knowledge_snippet,
    _knowledge_walk,
    _load_memory,
    _load_schedules_plain,
    _load_self_profile,
    _mem_idf,
    _mem_score,
    _mem_tokens,
    _save_memory,
    _save_schedules_plain,
    _save_self_profile,
    _workflow_step_text,
)

# 流程运行互斥标志：模块级初始化（run_workflow 在函数内 global 声明并置位，
# 若缺少此处初始化，首次调用在检查 `if _WORKFLOW_RUNNING` 时抛 NameError，
# 被外层 except 吞掉变成"读取流程失败"）
_WORKFLOW_RUNNING = False


def _mem_similar(a, b):
    """近重复记忆判定：difflib 字符相似度（0~1）。

    实测校准（2026-09-01）：语序微调 0.92 / 标点差异 0.96 / 同义改写 0.70 /
    实质不同（喝咖啡vs喝茶）0.77 —— 故阈值取 0.85（宁漏勿误，误并丢信息）。
    关键数字集合不一致时强惩罚（凌晨3点 vs 凌晨4点 ≈0.89 会误并，需压到阈值下）。"""
    if not a or not b:
        return 0.0
    sim = difflib.SequenceMatcher(None, a, b).ratio()
    nums_a = set(re.findall(r"\d+(?:\.\d+)?", a))
    nums_b = set(re.findall(r"\d+(?:\.\d+)?", b))
    if nums_a and nums_b and nums_a != nums_b:
        sim *= 0.5
    return sim


_MEM_SIM_MERGE = 0.85  # 近重复合并阈值（difflib 字符相似度）


def _mem_entity_alias(e, known):
    """实体别名归并：新实体与既有实体（相似度>=0.75 或包含关系）匹配时返回既有名。"""
    if not e or not known:
        return e
    for k in known:
        if not k:
            continue
        if e == k:
            return k
        if _mem_similar(e, k) >= 0.75:
            return k
        if len(e) >= 2 and len(k) >= 2 and (e in k or k in e):
            return k if len(k) <= len(e) * 2 else e
    return e


@tool(
        {
            "type": "function",
            "function": {
                "name": "write_memory",
                "description": "写入一条长期记忆（用户偏好、关键结论、重要事实），自动去重，最多 2000 条；可附带类型、实体与关系三元组形成知识图谱。写入时请如实声明 origin（血缘）：用户明确要求记住的内容用 user，你自己推断总结的用 agent（默认）——注入上下文时会对非 user 来源加标注",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要记住的内容"},
                        "tags": {"type": "string", "description": "可选：逗号分隔的标签，便于检索；首项同时作为该记忆的 key（key 相同的写入会作废旧条目）"},
                        "type": {"type": "string", "description": "可选：记忆类型（偏好/事实/项目/联系/规则 等）"},
                        "entities": {"type": "string", "description": "可选：涉及的实体列表，逗号分隔，如 张三,项目A"},
                        "relations": {"type": "string", "description": "可选：关系三元组，分号分隔的 实体-关系-实体，如 张三-负责-项目A"},
                        "origin": {"type": "string", "description": "可选：记忆血缘——user（用户明确要求记住）/ agent（你自己推断，默认）/ web（来自外部网页内容）/ system。请如实填写，这决定注入时是否被标注"},
                        "confidence": {"type": "number", "description": "可选：0-1 置信度（默认按 origin：user/system=1.0、agent=0.5、web=0.3）"},
                        "supersede_key": {"type": "string", "description": "可选：要取代的旧记忆（其 id / key / 完全相同文本）。只有显式传入才会作废旧条目；上一次写入若提示「已有相似记忆」，把其中的 id 传到这里即可完成取代"},
                    },
                    "required": ["text"],
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='写入长期记忆',
    preactivate=(('记忆', '记住', '偏好', '忘记', '删除记忆', '修改记忆'),),
)
def write_memory(text, tags="", type="", entities="", relations="",
                 origin="agent", confidence=None, supersede_key=""):
    """写入一条长期记忆（统一走 memory_facade：去重 / 取代 / 血缘）。

    type：记忆类型（偏好/事实/项目/联系/规则 等，便于分类检索）；
    entities：涉及的实体列表（逗号分隔，如 张三,项目A），构成知识图谱节点；
    relations：关系三元组（分号分隔的 "实体-关系-实体"，如 张三-负责-项目A）；
    origin：记忆**血缘**——这一项决定注入时是否被标注，请如实填写：
        user（用户明确要求记住）/ agent（你自己推断总结，默认）/
        web（来自外部网页等内容）/ system；
    confidence：0-1 置信度（默认按 origin：user/system=1.0、agent=0.5、web=0.3）；
    supersede_key：要**取代**的旧记忆（其 id / key / 完全相同文本）。**只有显式传入
        才会作废旧条目**——绝不按相似度自动作废（实测证明词面相似度无法区分
        「应作废」与「绝不能作废」）。若上一次写入提示了「已有相似记忆」，把其中的
        id 传到这里即可完成取代。
    """
    if not _dc.MEMORY_ENABLED:
        return "记忆功能已关闭（可在设置中开启），未写入"
    text = str(text or "").strip()
    if not text:
        return "错误：记忆内容为空"
    if len(text) > MEMORY_MAX_TEXT:
        text = text[:MEMORY_MAX_TEXT] + "…"
    import memory_facade as _mf
    # 门面路径随主文件注入（测试会 monkeypatch dc.MEMORY_FILE，故每次调用都对齐）
    _mf.init(_dc.MEMORY_FILE)
    key = str(tags or "").strip().split(",")[0].strip() or "自动记忆"
    # 实体别名归并：新实体优先映射到既有图谱节点（避免 张三/张三2 双节点）
    known_ents = set()
    for f in _mf.all_facts():
        known_ents.update(f.get("entities") or [])
    ent = []
    for e in str(entities or "").split(","):
        e = e.strip()[:30]
        if not e:
            continue
        ent.append(_mem_entity_alias(e, known_ents))
    ent = list(dict.fromkeys(ent))  # 保序去重
    rels = []
    for r in str(relations or "").split(";"):
        parts = [p.strip() for p in str(r).split("-") if p.strip()]
        if len(parts) == 3:
            rels.append({"rel": parts[1][:20], "to": parts[2][:30]})
    res = _mf.remember(text, origin=origin, type=type, tags=tags, key=key,
                       confidence=confidence, entities=ent, relations=rels,
                       supersede_key=supersede_key)
    if not res.get("ok"):
        return res.get("message") or "错误：记忆写入失败"
    if res.get("action") == "duplicate":
        return "该内容已存在，未重复写入"
    _brain_sync_memory(text, key, str(type or "").strip(), ent, rels)
    total = len(_mf.all_facts())
    extra = ("；" + res["message"]) if res.get("message") else ""
    return f"已写入记忆（当前共 {total} 条）{extra}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "self_profile",
                "description": "核心自我状态（跨会话连续自我）：get 查看身份/偏好/长期目标/演进历程；update 更新身份与焦点；append 沉淀偏好/目标/里程碑/用户画像/历程/心愿",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "get/update/append"},
                        "field": {"type": "string", "description": "update: identity/focus；append: preferences/goals/milestones/user_model/history/wishes"},
                        "value": {"type": "string", "description": "要更新或追加的内容"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='核心自我状态',
    preactivate=(('自我', '我是谁', '自我状态', '身份', '长期目标', '我的进化', '成长'),),
)
def self_profile(action="get", field=None, value=None):
    """核心自我状态（跨会话连续自我）。

    与记忆（事实记录）不同，这里存「我」本身：身份/偏好/长期目标/里程碑/
    用户心智模型/演进历程/当前焦点/未完成心愿。跨所有会话延续，形成连续的自我叙事。

    action:
      get      读取全部摘要或指定字段（field）
      update   更新标量字段（identity 对象 / focus 字符串）
      append   追加到列表字段（preferences/goals/milestones/user_model/history/wishes）
    """
    from datetime import datetime

    act = (action or "get").strip().lower()
    with SELF_PROFILE_LOCK:
        data = _load_self_profile()

        if act == "get":
            if field:
                v = data.get(field)
                if not v:
                    return f"（{field} 为空）"
                return json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
            lines = ["[核心自我状态]"]
            if data.get("identity"):
                lines.append("身份：" + "、".join(f"{k}:{v}" for k, v in data["identity"].items()))
            if data.get("focus"):
                lines.append(f"当前焦点：{data['focus']}")
            if data.get("goals"):
                goals = []
                for g in data["goals"][-8:]:
                    mark = "[x]" if g.get("done") else "[ ]"
                    goals.append(f"{mark} {str(g.get('text', ''))[:60]}")
                lines.append("长期目标：" + "；".join(goals))
            if data.get("preferences"):
                lines.append("偏好：" + "；".join(str(p)[:40] for p in data["preferences"][-10:]))
            if data.get("user_model"):
                lines.append("用户画像：" + "；".join(str(u.get("insight", ""))[:40] for u in data["user_model"][-5:]))
            if data.get("history"):
                lines.append(f"演进历程 {len(data['history'])} 条，最近：{str(data['history'][-1].get('event', ''))[:50]}")
            if data.get("wishes"):
                lines.append("未完成心愿：" + "；".join(str(w)[:40] for w in data["wishes"][-5:]))
            return "\n".join(lines)

        if act == "update":
            if field not in ("identity", "focus"):
                return "错误：update 仅支持 identity/focus；列表字段用 append"
            data[field] = value
            data["updated_at"] = datetime.now().isoformat(timespec="seconds")
            if _save_self_profile(data):
                return f"已更新 {field}：{str(value)[:100]}"
            return "错误：自我状态保存失败"

        if act == "append":
            if field not in _SELF_PROFILE_LIST_FIELDS:
                return f"错误：append 仅支持 {'/'.join(_SELF_PROFILE_LIST_FIELDS)}"
            if not value:
                return "错误：append 需要 value"
            now = datetime.now().isoformat(timespec="seconds")
            if field in ("goals", "milestones"):
                item = {"text": str(value)[:200], "done": False, "created_at": now}
            elif field == "user_model":
                item = {"insight": str(value)[:300], "ts": now}
            elif field == "history":
                item = {"event": str(value)[:200], "ts": now}
            else:
                item = str(value)[:200]
            data[field].append(item)
            data["updated_at"] = now
            if _save_self_profile(data):
                return f"已追加到 {field}（共 {len(data[field])} 条）"
            return "错误：自我状态保存失败"

        return "错误：未知 action，可用 get/update/append"


@tool(
        {
            "type": "function",
            "function": {
                "name": "delete_memory",
                "description": "删除长期记忆：按内容关键词匹配删除（keyword 必填，避免误删全部）。记忆写错/过时/用户要求忘记时使用",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "要删除的记忆内容关键词（如 预算 / 用户偏好）"},
                    },
                    "required": ["keyword"],
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='删除长期记忆（按关键词）',
    preactivate=(('记忆', '记住', '偏好', '忘记', '删除记忆', '修改记忆'),),
)
def delete_memory(keyword=""):
    """删除长期记忆：按内容关键词匹配删除一条或多条（keyword 必填，避免误删全部）。

    匹配范围：记忆内容（value）与键（key）。记忆写错 / 过时 / 用户要求忘记时使用。
    """
    kw = str(keyword or "").strip()
    if not kw:
        return "错误：keyword 必填（要删除的记忆内容关键词，如「预算」或「用户偏好」）"
    with _MEMORY_LOCK:
        data = _load_memory()
        facts = data.get("facts") or []
        kwl = kw.lower()
        kept, removed = [], 0
        for f in facts:
            hay = str(f.get("value") or "") + " " + str(f.get("key") or "")
            if kwl in hay.lower():
                removed += 1
            else:
                kept.append(f)
        if removed == 0:
            return "（未找到匹配的记忆，未删除任何条目）"
        data["facts"] = kept
        if _save_memory(data):
            _brain_sync_delete(kw)
            return f"已删除 {removed} 条相关记忆（剩余 {len(kept)} 条）"
        return "错误：记忆删除失败"


@tool(
        {
            "type": "function",
            "function": {
                "name": "update_memory",
                "description": "修改长期记忆：把内容包含 old 的条目更新为 new（记忆不准确/过时时修正，可同时更新标签/类型/实体/关系）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "old": {"type": "string", "description": "要修改的原记忆内容关键词"},
                        "new": {"type": "string", "description": "更新后的新内容"},
                        "tags": {"type": "string", "description": "可选：新的标签（逗号分隔）"},
                        "type": {"type": "string", "description": "可选：新的记忆类型"},
                        "entities": {"type": "string", "description": "可选：新的实体列表（逗号分隔）"},
                        "relations": {"type": "string", "description": "可选：新的关系三元组（分号分隔 实体-关系-实体）"},
                    },
                    "required": ["old", "new"],
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='修改长期记忆（按关键词定位）',
    preactivate=(('记忆', '记住', '偏好', '忘记', '删除记忆', '修改记忆'),),
)
def update_memory(old, new, tags="", type="", entities="", relations=""):
    """修改长期记忆：把内容包含 old 的条目更新为 new（记忆不准确/过时时修正）。

    可同时更新标签/类型/实体/关系；多个匹配只更新最新一条；找不到则不改。
    """
    old = str(old or "").strip()
    new = str(new or "").strip()
    if not old or not new:
        return "错误：old（要修改的原内容关键词）与 new（新内容）都必填"
    with _MEMORY_LOCK:
        data = _load_memory()
        facts = data.get("facts") or []
        idx = None
        for i, f in enumerate(facts):
            hay = str(f.get("value") or "") + " " + str(f.get("key") or "")
            if old.lower() in hay.lower():
                idx = i
        if idx is None:
            return "（未找到匹配的记忆，未修改）"
        entry = facts[idx]
        entry["value"] = str(new)[:MEMORY_MAX_TEXT]
        if str(tags or "").strip():
            entry["key"] = str(tags).strip().split(",")[0].strip()[:40]
        if str(type or "").strip():
            entry["type"] = str(type).strip()[:20]
        ent = [e.strip()[:30] for e in str(entities or "").split(",") if e.strip()]
        if ent:
            entry["entities"] = ent
        rels = []
        for r in str(relations or "").split(";"):
            parts = [p.strip() for p in str(r).split("-") if p.strip()]
            if len(parts) == 3:
                rels.append({"rel": parts[1][:20], "to": parts[2][:30]})
        if rels:
            entry["relations"] = rels
        entry["ts"] = datetime.now().isoformat(timespec="seconds")
        if _save_memory(data):
            _brain_sync_update(old, str(new)[:MEMORY_MAX_TEXT])
            return f"已修改记忆（当前共 {len(facts)} 条）"
        return "错误：记忆修改失败"


@tool(
        {
            "type": "function",
            "function": {
                "name": "read_memory",
                "description": "读取长期记忆：关键词支持语义相似度检索（不含关键词也能匹配相关记忆）；可按类型/实体过滤",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string", "description": "可选：检索关键词（按语义相似度排序）"},
                        "max_items": {"type": "integer", "description": "可选：返回条数上限（默认 20）"},
                        "type": {"type": "string", "description": "可选：按记忆类型过滤（偏好/事实/项目/联系/规则 等）"},
                        "entity": {"type": "string", "description": "可选：按实体过滤（知识图谱节点）"},
                    },
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='检索长期记忆',
    preactivate=(('记忆', '记住', '偏好', '忘记', '删除记忆', '修改记忆'),),
)
def read_memory(keyword="", max_items=20, type="", entity=""):
    """读取长期记忆（facts + notes）。

    keyword 为空时按时间倒序返回；非空时按语义相似度排序（TF-IDF + bigram，
    即使不含关键词也能检索到相关记忆）。
    type：按记忆类型过滤；entity：按实体过滤（知识图谱节点检索）。
    """
    data = _load_memory()
    facts = data.get("facts") or []
    if str(type or "").strip():
        t = str(type).strip()
        facts = [f for f in facts if str(f.get("type") or "") == t]
    if str(entity or "").strip():
        e = str(entity).strip().lower()
        facts = [f for f in facts if e in [x.lower() for x in (f.get("entities") or [])]]
    entries = []
    for f in facts:
        k = str(f.get("key") or "").strip()
        v = str(f.get("value") or "").strip()
        if k or v:
            entries.append((f"{k}: {v}".strip(": "), v, f))
    for n in data.get("notes") or []:
        t = str(n.get("text") or "").strip()
        if t:
            entries.append((t, t, {}))
    try:
        limit = clamp_int(max_items, 20, lo=1, hi=100)
    except (TypeError, ValueError):
        limit = 20
    kw = str(keyword or "").strip()
    if kw:
        kwl = kw.lower()
        q_tokens = _mem_tokens(kw)
        idf = _mem_idf(facts)
        scored = []
        for label, v, f in entries:
            text = label + " " + v
            # 强匹配（包含关键词）优先；语义相似度作为补充（低相似度不返回，
            # 避免短关键词把无关条目带出来）
            exact = kwl in text.lower()
            sim = _mem_score(q_tokens, idf, text)
            if exact or sim >= 0.15:
                scored.append((1.0 if exact else sim, label, v, f))
        scored.sort(key=lambda x: -x[0])
        entries = [(label, v, f) for _, label, v, f in scored[:limit]]
        if not entries:
            return "（无匹配记忆）"
    else:
        entries = entries[-limit:][::-1]  # 最新在前
    if not entries:
        return "（暂无记忆）" if not kw else "（无匹配记忆）"
    lines = []
    for label, v, f in entries:
        meta = []
        if f.get("type"):
            meta.append(f"类型:{f['type']}")
        if f.get("entities"):
            meta.append(f"实体:{','.join(f['entities'])}")
        if f.get("relations"):
            meta.append(f"关系:{';'.join(r['rel'] + '→' + r['to'] for r in f['relations'])}")
        suffix = f" [{', '.join(meta)}]" if meta else ""
        lines.append(f"- {label}{suffix}")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "query_memory_graph",
                "description": "知识图谱查询：按实体或关系检索关联记忆（返回结构化图谱片段），适合查找人与项目/任务间的关联",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "description": "可选：实体名（如 张三 / 项目A）"},
                        "relation": {"type": "string", "description": "可选：关系名（如 负责 / 参与）"},
                        "max_items": {"type": "integer", "description": "可选：返回条数上限（默认 20）"},
                    },
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='记忆知识图谱查询',
    preactivate=(('记忆', '记住', '偏好', '忘记', '删除记忆', '修改记忆'),),
)
def query_memory_graph(entity=None, relation="", max_items=20):
    """知识图谱查询：按实体/关系检索关联记忆（返回结构化的图谱片段）。

    示例：query_memory_graph(entity='张三') 返回所有涉及张三的记忆；
    query_memory_graph(relation='负责') 返回所有"负责"关系。
    """
    data = _load_memory()
    facts = data.get("facts") or []
    e = str(entity or "").strip().lower() if entity else ""
    r = str(relation or "").strip() if relation else ""
    hits = []
    for f in facts:
        match_e = not e or e in [x.lower() for x in (f.get("entities") or [])]
        rels = f.get("relations") or []
        match_r = not r or any(rel.get("rel") == r for rel in rels)
        if match_e and match_r:
            hits.append(f)
    try:
        limit = clamp_int(max_items, 20, lo=1, hi=100)
    except (TypeError, ValueError):
        limit = 20
    hits = hits[-limit:]
    if not hits:
        return "（图谱中无匹配记忆）"
    lines = []
    for f in hits:
        v = str(f.get("value") or "").strip()
        lines.append(f"- {v}")
        for rel in f.get("relations") or []:
            lines.append(f"    {rel.get('rel')} → {rel.get('to')}")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "knowledge_index",
                "description": "对目录内文本文件建立语义检索索引（TF-IDF+bigram，零依赖）。建索引后可语义检索，措辞不同也能命中",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "可选：要索引的目录（默认工作区）"},
                        "force": {"type": "boolean", "description": "可选：强制重建"},
                    },
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='建立知识库索引',
    preactivate=(('建索引', '知识库索引', '语义检索', '知识库搜索'),),
)
def knowledge_index(directory="", force=False):
    """对目录内文本文件建立语义检索索引（增量：mtime/size 未变的文档直接复用）。"""
    root = permissions.resolve(str(directory or "").strip() or permissions.WORKSPACE_DIR or ".")
    if not root or not os.path.isdir(root):
        return f"错误：目录不存在：{directory}"
    ok, reason = permissions.check_filesystem(root, write=False)
    if not ok:
        return reason
    try:
        docs = _knowledge_walk(root)
        if not docs:
            return f"错误：目录内没有可索引的文本文件：{root}"
        # 增量复用：上次索引的文档 mtime/size 未变则沿用旧文本（省读取+解析）
        old_docs = {}
        if not force and _dc.KNOWLEDGE_INDEX_FILE and os.path.exists(_dc.KNOWLEDGE_INDEX_FILE):
            try:
                with open(_dc.KNOWLEDGE_INDEX_FILE, "r", encoding="utf-8") as f:
                    old = json.load(f)
                for d in old.get("docs") or []:
                    if isinstance(d, dict) and d.get("path"):
                        old_docs[d["path"]] = d
            except Exception:
                old_docs = {}
        entries = []
        reused = 0
        for full in sorted(docs):
            try:
                st = os.stat(full)
            except OSError:
                continue
            old = old_docs.get(full)
            # 复用判据用纳秒时间戳：秒级 st_mtime 在同一秒内快速改写
            # （size 恰好相同）时误判"未变化"，导致索引漏更新（真实 bug）
            if old and old.get("mtime_ns") == st.st_mtime_ns and old.get("size") == st.st_size:
                if old.get("text"):
                    entries.append({
                        "path": full,
                        "text": old["text"][:100000],
                        "mtime_ns": st.st_mtime_ns,
                        "mtime": st.st_mtime,
                        "size": st.st_size,
                    })
                    reused += 1
                continue
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(200000)
            except Exception:
                continue
            if text.strip():
                entries.append({
                    "path": full,
                    "text": text[:100000],
                    "mtime_ns": st.st_mtime_ns,
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                })
        if not entries:
            return "错误：没有可索引的内容"
        idf = _mem_idf([{"value": e["text"][:50000], "key": e["path"]} for e in entries])
        index = {
            "root": root,
            "count": len(entries),
            "idf": idf,
            "docs": entries,
        }
        if _dc.KNOWLEDGE_INDEX_FILE:
            os.makedirs(os.path.dirname(_dc.KNOWLEDGE_INDEX_FILE) or ".", exist_ok=True)
            tmp = _dc.KNOWLEDGE_INDEX_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(index, f, ensure_ascii=False)
            os.replace(tmp, _dc.KNOWLEDGE_INDEX_FILE)
        extra = f"（新增 {len(entries) - reused}，复用 {reused}）"
        return f"已索引 {len(entries)} 个文档（{root}）{extra}，可用 knowledge_search 检索"
    except Exception as e:
        return f"错误：建索引失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "knowledge_search",
                "description": "语义检索知识库（先 knowledge_index 建索引）：找『之前写过的关于预算的文档』这类措辞模糊的问题",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "检索关键词/语义描述"},
                        "top_k": {"type": "integer", "description": "可选：返回条数（默认 5，最大 10）"},
                    },
                    "required": ["query"],
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='语义检索知识库',
    preactivate=(('建索引', '知识库索引', '语义检索', '知识库搜索'),),
)
def knowledge_search(query, top_k=5):
    """语义检索知识库（TF-IDF + bigram，措辞不同也能命中）。"""
    q = str(query or "").strip()
    if not q:
        return "错误：query 必填"
    if not _dc.KNOWLEDGE_INDEX_FILE or not os.path.exists(_dc.KNOWLEDGE_INDEX_FILE):
        return "错误：知识库尚未建立索引（先用 knowledge_index 对目录建索引）"
    try:
        with open(_dc.KNOWLEDGE_INDEX_FILE, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        logging.exception("读取知识库索引失败")
        return "错误：索引读取失败，请重新 knowledge_index"
    idf = index.get("idf") or {}
    docs = index.get("docs") or []
    if not docs:
        return "知识库为空（请先用 knowledge_index 建索引）"
    try:
        k = clamp_int(top_k, 5, lo=1, hi=10)
    except (TypeError, ValueError):
        k = 5
    qt = _mem_tokens(q)
    scored = []
    for d in docs:
        s = _mem_score(qt, idf, d.get("text") or "")
        if s > 0:
            scored.append((s, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    top = scored[:k]
    if not top:
        return f"未找到相关内容（知识库共 {len(docs)} 个文档，查询：{q}）"
    lines = [f"知识库命中 {len(top)}/{len(docs)} 个文档（查询：{q}）："]
    for s, d in top:
        lines.append(f"\n【{s:.2f}】{d['path']}\n{_knowledge_snippet(d.get('text') or '', q)}")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "schedule_task",
                "description": "创建定时任务（每周五周报、每小时巡检、生日提醒等）。expr_type=cron（5字段）/time（HH:MM 每日）/every（每 N 分钟）；action=message 到点执行指令 / notify 提醒 / backup 备份",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "expr_type": {"type": "string", "description": "cron / time / every（默认 cron）"},
                        "expr": {"type": "string", "description": "表达式：cron 如 '30 9 * * 1'；time 如 '09:00'；every 如 '60'"},
                        "content": {"type": "string", "description": "到点要执行的内容（message 发指令 / notify 提醒文本）"},
                        "action": {"type": "string", "description": "message / notify / backup（默认 message）"},
                        "name": {"type": "string", "description": "可选：任务名称（便于后续取消/查看）"},
                        "enabled": {"type": "boolean", "description": "可选：是否启用（默认 true）"},
                        "off_peak": {"type": "boolean", "description": "可选：高峰错峰省费——触发时刻处于高峰时段（9-12 / 14-18 时）自动顺延到低谷执行（默认 false）"},
                    },
                    "required": ["expr", "content"],
                },
            },
        },
    groups=['⏰ 定时与任务'],
    phrases='定时任务（cron/每日/周期）',
    preactivate=(('定时', '提醒', '计划', '日程'),),
)
def schedule_task(expr_type="cron", expr="", content="", action="message", name="", enabled=True, off_peak=False):
    """创建定时任务（与手动「定时任务」面板同文件同引擎，AI 可主动安排）。

    expr_type: cron（5 字段：分 时 日 月 周）/ time（HH:MM 每日一次）/ every（每 N 分钟）
    action: message（到点自动发送指令执行任务）/ notify（状态栏提醒）/ backup（项目备份）
            / workflow（到点自动运行 workflows.json 中的流程，content 为流程名）
    off_peak: 高峰错峰——触发时刻处于高峰时段（9-12 / 14-18）时自动顺延到
            最近空闲时段开始执行（官方峰谷定价：空闲价格仅为高峰一半）
    """
    expr = str(expr or "").strip()
    if not expr:
        return "错误：expr 必填（cron 表达式 / HH:MM / 分钟数）"
    if not _dc.SCHEDULES_FILE:
        return "错误：定时任务模块未初始化"
    s = {"enabled": bool(enabled), "action": str(action or "message"), "last": "", "last_run": 0}
    if expr_type == "time":
        if not re.match(r"^\d{1,2}:\d{2}$", expr):
            return "错误：time 格式应为 HH:MM（如 09:00）"
        hh, _, mm = expr.partition(":")
        if not 0 <= int(hh) <= 23 or not 0 <= int(mm) <= 59:
            return "错误：time 时间非法（小时 0-23，分钟 0-59）"
        s["time"] = expr
    elif expr_type == "every":
        try:
            n = int(expr)
        except (TypeError, ValueError):
            return "错误：every 需要整数分钟数"
        if not 1 <= n <= 1440:
            return "错误：every 应在 1-1440 分钟之间"
        s["every"] = n
    else:
        fields = expr.split()
        if len(fields) != 5:
            return "错误：cron 需 5 个字段：分 时 日 月 周（如 30 9 * * 1）"
        if not all(cron_field_ok(f, i) for i, f in enumerate(fields)):
            return (
                "错误：cron 字段非法（值域：分 0-59，时 0-23，日 1-31，月 1-12，周 1-7；"
                "仅支持数字、*、,、-、/）"
            )
        s["cron"] = expr
    if str(action or "") not in ("message", "notify", "backup", "workflow"):
        return "错误：action 仅支持 message / notify / backup / workflow"
    if str(action) in ("message", "notify") and not str(content or "").strip():
        return "错误：message / notify 动作需要 content 内容"
    if str(action) == "workflow" and not str(content or "").strip():
        return "错误：workflow 动作需要流程名称（workflows.json 中的流程名）"
    if off_peak:
        s["off_peak"] = True
    if str(name or "").strip():
        s["name"] = str(name).strip()[:40]
    if str(content or "").strip():
        s["text"] = str(content).strip()[:2000]
    s["id"] = f"s{int(time.time() * 1000)}"
    with SCHEDULES_LOCK:
        schedules = _load_schedules_plain()
        schedules.append(s)
        _save_schedules_plain(schedules)
    when = expr
    permissions.audit("schedule_task", s["id"], f"{expr_type}:{expr} -> {action}")
    return f"已创建定时任务（id={s['id']}）：{when} 执行「{s.get('text', '')[:60]}」"


@tool(
        {
            "type": "function",
            "function": {
                "name": "list_schedules",
                "description": "列出全部定时任务（id/时间/动作/内容/启用状态），配合 cancel_schedule 管理",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    groups=['⏰ 定时与任务'],
    phrases='查看定时任务',
    preactivate=(('查看定时', '我的定时任务', '取消定时', '列出定时'),),
)
def list_schedules():
    """列出全部定时任务（含 id/时间/动作/内容/状态）。"""
    with SCHEDULES_LOCK:
        schedules = _load_schedules_plain()
    if not schedules:
        return "当前没有定时任务"
    act_map = {"message": "发指令", "notify": "提醒", "backup": "备份", "workflow": "流程"}
    lines = [f"共 {len(schedules)} 个定时任务："]
    for i, s in enumerate(schedules, 1):
        act = act_map.get(str(s.get("action") or "message"), str(s.get("action")))
        if s.get("cron"):
            when = f"cron:{s['cron']}"
        elif s.get("every"):
            when = f"每{s['every']}分钟"
        else:
            when = f"每日 {s.get('time', '')}"
        status = "启用" if s.get("enabled") else "停用"
        name = str(s.get("name") or "").strip()
        content = str(s.get("text") or "").strip()
        lines.append(
            f"{i}. [{status}] id={s.get('id', '-')} {name} | {when} | {act} | {content[:60]}"
        )
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "cancel_schedule",
                "description": "取消定时任务（按 list_schedules 返回的 id 或名称）",
                "parameters": {
                    "type": "object",
                    "properties": {"target": {"type": "string", "description": "任务 id 或名称"}},
                    "required": ["target"],
                },
            },
        },
    groups=['⏰ 定时与任务'],
    phrases='取消定时任务',
    preactivate=(('查看定时', '我的定时任务', '取消定时', '列出定时'),),
)
def cancel_schedule(target=""):
    """取消定时任务（按 id 或名称）。"""
    t = str(target or "").strip()
    if not t:
        return "错误：target 必填（任务 id 或名称，可用 list_schedules 查看）"
    with SCHEDULES_LOCK:
        schedules = _load_schedules_plain()
        if not schedules:
            return "当前没有定时任务"
        kept = []
        removed = None
        for s in schedules:
            sid = str(s.get("id") or "")
            sname = str(s.get("name") or "")
            if (sid and sid == t) or (sname and sname == t):
                removed = s
            else:
                kept.append(s)
        if removed is None:
            return f"错误：未找到定时任务：{t}（可用 list_schedules 查看）"
        _save_schedules_plain(kept)
    permissions.audit("cancel_schedule", t, "removed")
    return f"已取消定时任务：{removed.get('name') or t}（{removed.get('cron') or removed.get('every') or removed.get('time', '')}）"


@tool(
        {
            "type": "function",
            "function": {
                "name": "task_checkpoint_save",
                "description": "保存任务进度检查点（长任务每完成一步就保存，崩溃/重启后可用 task_checkpoint_load 从断点继续）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "任务名称"},
                        "status": {"type": "string", "description": "可选：状态（进行中/已完成/阻塞等）"},
                        "pending": {"type": "array", "description": "可选：剩余待办步骤列表", "items": {"type": "string"}},
                        "notes": {"type": "string", "description": "可选：进度备注"},
                        "auto": {"type": "boolean", "description": "可选：true 时自动保存（调用方内部使用，默认 false）"},
                    },
                    "required": ["name"],
                },
            },
        },
    groups=['⏰ 定时与任务'],
    phrases='保存任务断点',
    preactivate=(('断点', '检查点', '保存进度', '恢复进度', '继续上次'),),
)
def task_checkpoint_save(name="", status="进行中", pending=None, notes="", auto=False):
    """保存任务进度检查点（崩溃/重启后可从此继续）。

    auto=True：鲸语工具链执行中的自动断点（main 每步工具后写入），
    任务正常完成时由 task_checkpoint_clear 自动清除；手动断点不受影响。
    """
    if not str(name or "").strip() and not str(notes or "").strip():
        return "错误：name 或 notes 必填"
    data = {
        "name": str(name or "未命名任务")[:60],
        "status": str(status or "进行中")[:20],
        "pending": [str(p) for p in (pending or [])][:20],
        "notes": str(notes or "")[:2000],
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    if auto:
        data["auto"] = True
    if not _dc.CHECKPOINT_FILE:
        return "错误：检查点模块未初始化"
    try:
        os.makedirs(os.path.dirname(_dc.CHECKPOINT_FILE) or ".", exist_ok=True)
        tmp = _dc.CHECKPOINT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _dc.CHECKPOINT_FILE)
        return f"已保存任务检查点：{data['name']}（{data['status']}）"
    except Exception as e:
        return f"错误：保存检查点失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "task_checkpoint_load",
                "description": "读取任务检查点，恢复未完成任务上下文（配合 task_checkpoint_save 使用）",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    groups=['⏰ 定时与任务'],
    phrases='加载任务断点',
    preactivate=(('断点', '检查点', '保存进度', '恢复进度', '继续上次'),),
)
def task_checkpoint_load():
    """读取任务检查点（断点续跑时恢复任务上下文）。"""
    if not _dc.CHECKPOINT_FILE or not os.path.exists(_dc.CHECKPOINT_FILE):
        return "当前没有任务检查点"
    try:
        with open(_dc.CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return "检查点文件损坏"
        lines = [
            f"任务：{data.get('name', '')}（状态：{data.get('status', '')}）",
            f"保存时间：{data.get('saved_at', '')}",
        ]
        if data.get("auto"):
            lines.append("（自动断点：由鲸语在任务执行中自动保存）")
        if data.get("pending"):
            lines.append("待办步骤：\n" + "\n".join(f"- {p}" for p in data["pending"]))
        if data.get("notes"):
            lines.append(f"备注：{data['notes']}")
        return "\n".join(lines)
    except Exception:
        logging.exception("读取检查点失败")
        return "错误：读取检查点失败"


@tool(
        {
            "type": "function",
            "function": {
                "name": "run_workflow",
                "description": "运行已保存的流程模板（workflows.json）：按顺序逐条发送指令，上一步完成后自动执行下一步",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "description": "流程名称"}},
                    "required": ["name"],
                },
            },
        },
    groups=['⏰ 定时与任务'],
    phrases='执行工作流',
    preactivate=(('执行流程', '运行工作流', '跑流程', '流程模板'),),
)
def run_workflow(name):
    """运行已保存的流程模板：按顺序逐条发送指令，上一步完成后自动执行下一步。

    步骤支持 {"text": "任务目标", "recipe": "配方名"}：自动注入配方工具链。
    """
    global _WORKFLOW_RUNNING
    if not _dc.WORKFLOWS_FILE:
        return "错误：流程模块未初始化"
    if not _dc._SEND_CALLBACK:
        return "错误：发送通道不可用"
    try:
        if not os.path.exists(_dc.WORKFLOWS_FILE):
            return "错误：没有已保存的流程（workflows.json 为空）"
        with open(_dc.WORKFLOWS_FILE, "r", encoding="utf-8") as f:
            wf = json.load(f)
        steps = wf.get(str(name)) if isinstance(wf, dict) else None
        if not steps or not isinstance(steps, dict):
            avail = list(wf) if isinstance(wf, dict) else []
            return f"错误：未找到流程「{name}」（可用：{avail}）"
        step_list = steps.get("steps")
        if not isinstance(step_list, list) or not step_list:
            return f"错误：流程「{name}」没有步骤"
        texts = []
        for st in step_list:
            t = _workflow_step_text(st, name)
            if t:
                texts.append(t)
        if not texts:
            return f"错误：流程「{name}」的步骤均为空"
        desc = f"启动流程「{name}」（{len(texts)} 步）\n" + "\n".join(f"{i}. {t[:100]}" for i, t in enumerate(texts, 1))
        # 校验全部通过后才检查-置位（同一临界区：并行工具调用下防双流程同时启动；
        # 校验失败绝不占位，避免一次失败流程标记永久占用）
        with _WORKFLOW_LOCK:
            if _WORKFLOW_RUNNING:
                return "错误：已有流程正在运行，请等待完成后再启动新流程"
            _WORKFLOW_RUNNING = True
        # 异步执行：在后台线程逐条下发，等待上一步生成结束
        def _run():
            global _WORKFLOW_RUNNING
            try:
                for i, t in enumerate(texts, 1):
                    deadline = time.time() + 600
                    while _dc._BUSY_PROVIDER and _dc._BUSY_PROVIDER():
                        if time.time() > deadline:
                            return
                        time.sleep(1)
                    _dc._SEND_CALLBACK(t)
                    time.sleep(2)  # 让主线程进入生成状态
            except Exception:
                logging.exception("流程执行异常")
            finally:
                with _WORKFLOW_LOCK:
                    _WORKFLOW_RUNNING = False
        threading.Thread(target=_run, daemon=True).start()
        return desc
    except Exception as e:
        with _WORKFLOW_LOCK:
            _WORKFLOW_RUNNING = False
        return f"错误：读取流程失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "failure_memory",
                "description": "失败模式生命周期管理：查看/消解(标记已修复)/重开/移除失败记录。工具失败时自动记录，同一类错误按指纹归并并累计复现次数；该工具后续调用成功后自动消解，已消解记录不再注入上下文。用途：① 怀疑自己被过期失败结论误导时先 list 核实 ② 修复后主动 resolve（附修复说明）③ 确认是误报时 forget。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "list=查看(默认) / stats=统计 / resolve=标记已修复 / reopen=撤销消解 / forget=彻底移除"},
                        "fingerprint": {"type": "string", "description": "精确命中一条记录（list 输出里的 fingerprint 字段）"},
                        "tool": {"type": "string", "description": "按工具名批量操作（resolve/reopen/forget 时生效）"},
                        "note": {"type": "string", "description": "resolve 时的修复说明：怎么修的、怎么验证的"},
                        "unresolved_only": {"type": "boolean", "description": "list 时只看未消解项（默认 true）"},
                    },
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='失败记忆查看/消解/移除',
    preactivate=(('失败模式', '失败记忆', '老是报错', '已修复', '消解'),),
)
def failure_memory(action="list", fingerprint="", tool="", note="", unresolved_only=True):
    """失败记忆生命周期管理（查看/消解/重开/移除）。

    失败记忆必须有生命周期，否则过期结论会被持续注入上下文反过来误导判断
    （历史事故：evolutions 目录当时不存在被记下，目录补建后该条仍在注入）。
    """
    path = _dc.FAILURES_FILE
    if not path:
        return "错误：失败模式库路径未注入（FAILURES_FILE 为空），无法读取失败记忆"
    arch = _dc.FAILURES_ARCHIVE_FILE
    act = str(action or "list").strip().lower()
    fp = str(fingerprint or "").strip()
    tl = str(tool or "").strip()
    only = not (unresolved_only is False or str(unresolved_only).strip().lower() in ("0", "false", "no", "none"))
    try:
        if act in ("stats", "stat", "统计"):
            st = stores.failure_stats(path)
            return (
                f"失败模式库：活跃 {st['total']} 条"
                f"（未消解 {st['unresolved']} · 已消解 {st['resolved']}）"
                f"，其中复现 >1 次的 {st['recurring']} 条。"
                f"\n（已消解项不再注入上下文；活跃上限 {stores.FAILURES_MAX} 条，溢出优先归档已消解项）"
            )
        if act in ("resolve", "reopen", "forget"):
            if not fp and not tl:
                return "错误：resolve/reopen/forget 需要 fingerprint 或 tool 之一"
            if act == "resolve":
                n, items = stores.resolve_failures(path, fingerprint=fp or None, tool=tl or None,
                                                  note=note, archive_path=arch)
                verb = "已标记为修复"
            elif act == "reopen":
                n, items = stores.reopen_failures(path, fingerprint=fp or None, tool=tl or None)
                verb = "已撤销消解（重新视为未修复）"
            else:
                n, items = stores.forget_failures(path, fingerprint=fp or None, tool=tl or None)
                verb = "已彻底移除"
            if not n:
                return f"{verb}：0 条（未匹配到记录，先用 action=list 确认 fingerprint/tool）"
            live = [it for it in items if not it.get("resolved")]
            return (f"{verb}：{n} 条。当前未消解 {len(live)} 条，"
                    + ("遇到同类情况时这些记录才会被注入上下文。" if live else "失败记忆已清空，后续不会再注入失败提示。"))
        # 默认 list
        items = [x for x in (stores.normalize_failure(i) for i in stores.load_failures(path)) if x]
        if only:
            items = [it for it in items if not it.get("resolved")]
        items.sort(key=lambda x: str(x.get("last_ts") or ""), reverse=True)
        st = stores.failure_stats(path)
        if not items:
            return (f"没有{'未消解的' if only else ''}失败记录（活跃 {st['total']} 条："
                    f"未消解 {st['unresolved']} · 已消解 {st['resolved']}）。")
        lines = [f"失败记录 {len(items)} 条（活跃 {st['total']} · 未消解 {st['unresolved']} · 已消解 {st['resolved']}）："]
        for it in items[:30]:
            flag = "已消解" if it.get("resolved") else "未消解"
            lines.append(
                f"- [{flag}] {it.get('tool')} × {int(it.get('hits') or 1)} 次"
                f"（最近 {str(it.get('last_ts') or '')[:16]}）\n"
                f"  fingerprint: {it.get('fingerprint')}\n"
                f"  错误：{str(it.get('error') or '')[:100]}"
                + (f"\n  备注：{it.get('note')}" if it.get("note") else "")
            )
        if only:
            lines.append("提示：确认某条不再适用时，用 action=resolve（附 note 说明）或 forget 移除。")
        return "\n".join(lines)
    except Exception as e:
        return f"错误：失败记忆操作失败: {e}"


__all__ = ['write_memory', 'self_profile', 'delete_memory', 'update_memory', 'read_memory', 'query_memory_graph', 'knowledge_index', 'knowledge_search', 'schedule_task', 'list_schedules', 'cancel_schedule', 'task_checkpoint_save', 'task_checkpoint_load', 'run_workflow', 'failure_memory']
