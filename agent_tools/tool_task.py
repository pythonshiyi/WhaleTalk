"""tool_task —— 定时任务 / 检查点 / 流程 / 知识索引 / 失败记忆 工具域。

原 tool_brain.py。长期记忆、自我状态与大脑相关工具已随「大脑 / 长期记忆」模块整体移除，
本模块只保留任务基建：定时任务、任务检查点、流程编排、目录知识索引、失败模式查询。

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
阈值常量/锁统一从 shared 导入；仅剩余辅助函数仍依赖主文件加载顺序契约
（在 `from agent_tools import *` 前已定义）。
"""

import json
import logging
import os
import re
import threading
import time
from datetime import datetime

import deepseek_client as _dc  # 可变注入配置动态访问（dc.X 注入后立即生效）
import permissions
import stores
import task_plan
from deepseek_client import (
    _knowledge_snippet,
    _knowledge_walk,
    _load_schedules_plain,
    _mem_idf,
    _mem_score,
    _mem_tokens,
    _save_schedules_plain,
    _workflow_step_text,
)
from shared import (
    _WORKFLOW_LOCK,
    SCHEDULES_LOCK,
    clamp_int,
    cron_field_ok,
)
from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export

# 流程运行互斥标志：模块级初始化（run_workflow 在函数内 global 声明并置位，
# 若缺少此处初始化，首次调用在检查 `if _WORKFLOW_RUNNING` 时抛 NameError，
# 被外层 except 吞掉变成"读取流程失败"）
_WORKFLOW_RUNNING = False

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
                with open(_dc.KNOWLEDGE_INDEX_FILE, encoding="utf-8") as f:
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
                with open(full, encoding="utf-8", errors="replace") as f:
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
        with open(_dc.KNOWLEDGE_INDEX_FILE, encoding="utf-8") as f:
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
    en = enabled if isinstance(enabled, bool) else str(enabled).strip().lower() not in ("false", "0", "no", "")
    s = {"enabled": en, "action": str(action or "message"), "last": "", "last_run": 0}
    et = str(expr_type or "cron").strip().lower() or "cron"
    if et not in ("cron", "time", "every"):
        return "错误：expr_type 仅支持 cron / time / every"
    if et == "time":
        if not re.match(r"^\d{1,2}:\d{2}$", expr):
            return "错误：time 格式应为 HH:MM（如 09:00）"
        hh, _, mm = expr.partition(":")
        if not 0 <= int(hh) <= 23 or not 0 <= int(mm) <= 59:
            return "错误：time 时间非法（小时 0-23，分钟 0-59）"
        s["time"] = expr
    elif et == "every":
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
        if not _save_schedules_plain(schedules):
            return "错误：定时任务保存失败（磁盘写入失败或锁超时），任务未创建"
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
        if not _save_schedules_plain(kept):
            return "错误：定时任务保存失败（磁盘写入失败或锁超时），任务未取消"
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
        with open(_dc.CHECKPOINT_FILE, encoding="utf-8") as f:
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
                "name": "task_step_done",
                "description": "长任务模式：标记计划中的某一步完成/失败，并提交验收证据；没有证据不算完成",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "plan_id": {"type": "string", "description": "任务计划 ID（系统提示里给出）"},
                        "step": {"type": "integer", "description": "步骤序号（从 1 开始）"},
                        "status": {"type": "string", "enum": ["done", "failed"], "description": "done=完成，failed=失败"},
                        "evidence": {"type": "string", "description": "可核验的证据：文件路径、命令输出、URL 或验收结论"},
                        "verify_path": {"type": "string", "description": "可选：产物路径；给出后会检查文件/目录是否存在"},
                        "verification": {
                            "type": "object",
                            "description": "可选：结构化验收规则。不传时只要求文字证据",
                            "properties": {
                                "path": {"type": "string", "description": "产物路径（等价于 verify_path）"},
                                "expect_type": {"type": "string", "enum": ["file", "dir"], "description": "期望产物类型"},
                                "expect_contains": {"type": "array", "items": {"type": "string"}, "description": "产物文本必须包含的片段"},
                                "expect_not_contains": {"type": "array", "items": {"type": "string"}, "description": "产物文本不得包含的片段"},
                                "min_size": {"type": "integer", "description": "文件最小字节数"},
                                "json_keys": {"type": "array", "items": {"type": "string"}, "description": "JSON 必须包含的字段（支持 a.b 点路径）"},
                                "min_len": {"type": "integer", "description": "JSON 列表/对象最小条目数"},
                            },
                        },
                    },
                    "required": ["plan_id", "step", "evidence"],
                },
            },
        },
    groups=['⏰ 定时与任务'],
    phrases='标记任务步骤完成',
    preactivate=(('任务计划', '第几步', '步骤完成', '验收'),),
)
def task_step_done(plan_id="", step=0, status="done", evidence="", verify_path="", verification=None):
    """长任务逐步验收：证据 + 结构化校验，验收失败会自动记为失败/阻塞。"""
    if str(status or "").lower() not in ("done", "failed"):
        return "错误：status 仅支持 done / failed"
    try:
        step_no = int(step)
    except (TypeError, ValueError):
        return "错误：step 必须是整数"
    ev = str(evidence or "").strip()
    if not ev:
        return "错误：必须提供 evidence（文件路径/命令输出/URL/验收结论），没有证据不算完成"
    spec = verification
    if isinstance(spec, dict):
        spec = dict(spec)
        if verify_path and not spec.get("path"):
            spec["path"] = str(verify_path)
    elif not spec:
        spec = {"path": str(verify_path)} if verify_path else {}
    try:
        plan, msg = task_plan.complete_step(plan_id, step_no, status, ev[:500], spec, str(verify_path or ""))
    except Exception as e:  # noqa: BLE001
        return f"错误：计划保存失败：{e}"
    if plan is None:
        return msg
    permissions.audit("task_step_done", f"{plan.plan_id}#{step_no}", str(status))
    progress = f"进度 {plan.done_count()}/{len(plan.steps)}"
    return f"{msg}；{progress}" + ("，计划已完成" if plan.all_done() else "")


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
        with open(_dc.WORKFLOWS_FILE, encoding="utf-8") as f:
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
                for _i, t in enumerate(texts, 1):
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


__all__ = ['knowledge_index', 'knowledge_search', 'schedule_task', 'list_schedules', 'cancel_schedule', 'task_checkpoint_save', 'task_checkpoint_load', 'task_step_done', 'run_workflow', 'failure_memory']
