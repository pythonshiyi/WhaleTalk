# -*- coding: utf-8 -*-
"""技能结晶（Skill Factory）：把「成功工具链」固化为可复用资产（G14）。

现状：`patterns.json` 已在记录成功调用（工具/参数/结果），`tasklog.json` 已在记录
任务链（标题 + 工具序列）——但两者都只是流水账，没有工厂把**重复出现**的链变成
可复用技能。结果是 AI 每次从零推导同样的步骤，学不到"上次我是这么做的"。

本模块是纯函数层（仅标准库，不依赖 deepseek_client / api_server），便于单测与复用：
    chain_signature   链签名（连续重复折叠）——判重与统计的键
    collect_chains    从 tasklog 汇总「签名 → 出现次数 / 代表标题 / 最近时间」
    is_crystallizable 是否达到结晶阈值
    crystallize       产出参数化技能草稿（写入指令库 prompts.json）
"""
import re
from datetime import datetime

SKILL_MIN_CHAIN = 3      # 链至少 3 步才值得固化（1-2 步的工具调用不值得成为技能）
SKILL_MIN_REPEATS = 2    # 至少重复出现 2 次才算"模式"而非偶发
SKILL_MAX_DRAFTS = 20    # 单次最多产出草稿数
SKILL_CATEGORY = "自动技能"
SKILL_TAG = "自动技能"

_PATH_RULES = (
    (re.compile(r"[A-Za-z]:\\[^\s\"'<>|,}]*"), "<本地路径>"),
    (re.compile(r"(?<![:/\w])/(?:[\w.\-]+/)+[\w.\-]*"), "<路径>"),
)


def mask_paths(text):
    """脱敏：把绝对路径替换为占位符（技能模板会被复用，不该烧进本机路径）。"""
    out = str(text or "")
    for rx, repl in _PATH_RULES:
        out = rx.sub(repl, out)
    return out


def chain_signature(chain):
    """链签名：工具名序列，**连续重复折叠**（A>A>B → A>B）。

    折叠是关键：模型偶尔会因重试把同一工具连调两次，那属于抖动而非模式，
    不应让 A>A>B 与 A>B 被判成两个不同技能。
    """
    out = []
    for raw in chain or []:
        t = str(raw or "").strip()
        if not t:
            continue
        if out and out[-1] == t:
            continue
        out.append(t)
    return ">".join(out)


def collect_chains(tasks):
    """汇总任务链：{签名: {chain, count, titles, last_ts}}。"""
    agg = {}
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        chain = [str(c).strip() for c in (task.get("chain") or []) if str(c or "").strip()]
        sig = chain_signature(chain)
        if not sig:
            continue
        item = agg.setdefault(sig, {"chain": [], "count": 0, "titles": [], "last_ts": ""})
        if not item["chain"]:
            item["chain"] = chain
        item["count"] += 1
        title = str(task.get("title") or "").strip()[:60]
        if title and title not in item["titles"]:
            item["titles"].append(title)
        ts = str(task.get("ts") or "")
        if ts > item["last_ts"]:
            item["last_ts"] = ts
    return agg


def is_crystallizable(info, min_chain=SKILL_MIN_CHAIN, min_repeats=SKILL_MIN_REPEATS):
    """是否达到结晶阈值：链够长且重复出现过。"""
    if not isinstance(info, dict):
        return False
    return len(info.get("chain") or []) >= int(min_chain) and int(info.get("count") or 0) >= int(min_repeats)


def _arg_hint(tool, patterns):
    """从成功模式里取该工具的最近一次参数作为示例（脱敏、限长）。"""
    for p in reversed(list(patterns or [])):
        if not isinstance(p, dict) or p.get("tool") != tool:
            continue
        args = mask_paths(p.get("args"))
        args = re.sub(r"\s+", " ", args).strip()
        if args and args not in ("", "{}", "None", "()"):
            return args[:120]
    return ""


def build_skill(info, patterns=None, now=None):
    """把一条可结晶链渲染成指令库草稿（参数化：任务目标由 {{TEXT}} 注入）。"""
    steps = [str(s) for s in (info.get("chain") or [])]
    sig = chain_signature(steps)
    title = (info.get("titles") or ["同类任务"])[-1]
    lines = [
        f"按已验证的工具链完成同类任务（该链历史上已成功 {int(info.get('count') or 1)} 次）：",
        "",
        "任务目标：{{TEXT}}",
        "",
        "执行步骤（顺序可按实际情况调整，但不要跳过核验）：",
    ]
    for i, t in enumerate(steps, 1):
        hint = _arg_hint(t, patterns)
        lines.append(f"{i}. `{t}`" + (f" —— 参考参数：{hint}" if hint else ""))
    lines += [
        "",
        "要求：",
        "1. 先输出执行计划（做什么/用什么工具/预期结果）再动手。",
        "2. 每一步完成后核验产物真实存在，缺失立即修正，不要带着缺口往下走。",
        "3. 某步失败不要用同参数盲目重试——先诊断，或改用替代工具。",
        "4. 结束时自检并说明完成情况；产物写入工作区的独立子目录。",
    ]
    return {
        "name": f"自动技能 · {title}"[:40],
        "text": "\n".join(lines),
        "desc": f"自动结晶：{' → '.join(steps[:6])}（历史成功 {int(info.get('count') or 1)} 次）",
        "category": SKILL_CATEGORY,
        "icon": "🧩",
        "tags": [SKILL_TAG, "工具链"],
        "shortcut": "",
        "enabled": False,          # 草稿：默认不启用，由用户/AI 审阅后再开启
        "auto_skill": True,
        "source_chain": steps,
        "source_sig": sig,
        "hits": int(info.get("count") or 1),
        "created": now or datetime.now().isoformat(timespec="seconds"),
    }


def crystallize(tasks, patterns=None, existing=None,
                min_chain=SKILL_MIN_CHAIN, min_repeats=SKILL_MIN_REPEATS,
                max_drafts=SKILL_MAX_DRAFTS, now=None):
    """产出新的技能草稿（跳过已存在的同链技能）。返回草稿列表。

    existing 传现有指令库条目（含 auto_skill/source_sig 的草稿与用户自建指令）；
    按 source_sig 与 name 双重去重——用户手动改过名字也不重复产出。
    """
    taken_sigs = set()
    taken_names = set()
    for p in existing or []:
        if not isinstance(p, dict):
            continue
        if p.get("source_sig"):
            taken_sigs.add(str(p["source_sig"]))
        if p.get("name"):
            taken_names.add(str(p["name"]).strip())
    out = []
    agg = collect_chains(tasks)
    # 出现次数多的优先（更有价值），次数相同则链更长的优先
    ordered = sorted(agg.items(), key=lambda kv: (-int(kv[1]["count"]), -len(kv[1]["chain"])))
    for sig, info in ordered:
        if len(out) >= int(max_drafts):
            break
        if sig in taken_sigs or not is_crystallizable(info, min_chain, min_repeats):
            continue
        draft = build_skill(info, patterns, now)
        if draft["name"] in taken_names:
            continue
        taken_sigs.add(sig)
        taken_names.add(draft["name"])
        out.append(draft)
    return out
