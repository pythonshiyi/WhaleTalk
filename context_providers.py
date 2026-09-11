# -*- coding: utf-8 -*-
"""上下文装配（Context Assembly）：把「往系统提示里塞什么」从一段 120 行的方法
变成一张可枚举、可预算、可回执的 Provider 表。

## 为什么需要它

改造前，装配逻辑全在 `api_server._inject_system_messages` 一个方法里，串起 9 个
来源，**每个都是 `try/except: pass`**。三个具体后果：

1. **失效不可见**：某个来源坏了 → 上下文静默变少 → AI 变差，没有任何信号；
2. **成本不可答**：没有分段 token 账，"这一轮为什么这么贵"无法回答；
3. **改动有风险**：加/删/调顺序都得改同一个方法，且没有回执可核对。

现在每个来源是一个 `Provider`，装配器统一排序、限预算、**记录回执**：
谁注入了、各占多少字符、谁被跳过（为什么）、谁失败了（影响什么）。
失败一律走 `degrade()`，不再静默。

## 契约

- `assemble(ctx)` → `{"prompt", "text", "receipt"}`。**注入内容与顺序与改造前完全一致**
  （仅新增 degrade_status 一项，且它无关键降级时返回空、不占位）。
- 依赖（`_memory_full` / 失败库路径 / 插件提示 …）由调用方经 `ctx.deps` 注入，
  且**必须在调用时按名字解析**——`tests/test_quiet_mode.py` 以「桩 module 属性」的方式
  验证注入行为，提前缓存函数对象会让桩失效。
- 单个 provider 抛错不影响其余 provider（装配器逐项 try/except + degrade）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# ── 回执：最近一次装配结果（供 GET /v1/context 查看）───────────────────────
# 刻意用模块级变量而不是改 _inject_system_messages 的返回值：它返回二元组，
# 且 tests/test_quiet_mode.py 直接解包 2 个值——改 arity 会连带破坏既有契约。
_last_receipt: Dict[str, Any] = {}


def last_receipt() -> Dict[str, Any]:
    return dict(_last_receipt)


@dataclass
class Context:
    """一次装配的输入。deps 里放的是**调用方可解析的依赖**（函数/路径常量）。"""

    messages: List[dict] = field(default_factory=list)
    cfg: Dict[str, Any] = field(default_factory=dict)
    pure_chat: bool = False
    quiet_mode: bool = False
    deps: Dict[str, Any] = field(default_factory=dict)

    def dep(self, name, default=None):
        return self.deps.get(name, default)


@dataclass(frozen=True)
class Provider:
    """一个上下文来源。

    name      回执与 degrade 里的标识（点分命名：context.memory）
    priority  越小越靠前（决定注入顺序）
    provide   (ctx) -> str | None；None/空串 = 本次无内容
    enabled   (ctx) -> bool；False = 本次跳过（回执记原因）
    budget    字符上限（0 = 不限）；超出按预算截断并标记
    critical  失败是否属于「影响回答质量」的降级（会进模型自我状态提示）
    deps      必需依赖名；缺失则按「依赖未注入」跳过（**不计为失败**）——
              否则一次接线疏漏会变成一串永远刷屏的假降级告警
    """

    name: str
    priority: int
    provide: Callable[[Context], Optional[str]]
    enabled: Callable[[Context], bool] = lambda ctx: True
    budget: int = 0
    critical: bool = False
    deps: tuple = ()
    # 被 enabled 否决时写入回执的原因（人类可读）
    skip_reason: str = "条件不满足"


_REGISTRY: List[Provider] = []


def register(p: Provider) -> Provider:
    _REGISTRY.append(p)
    return p


def providers() -> List[Provider]:
    return sorted(_REGISTRY, key=lambda p: (p.priority, p.name))


def _always(ctx):
    return True


def _not_pure(ctx):
    return not ctx.pure_chat


def _not_quiet(ctx):
    return not ctx.quiet_mode


# ── 内置 Provider（顺序与内容对齐改造前的 _inject_system_messages）──────────

def _provide_task_guide(ctx):
    return str(ctx.dep("task_quality_guide") or "") or None


register(Provider(
    name="base.task_quality_guide", priority=0, provide=_provide_task_guide,
    enabled=_not_pure, budget=8000, critical=False,
    deps=("task_quality_guide", "default_prompt", "dialog_prompt"),
))


def _provide_memory(ctx):
    """长期记忆（近 6 条生效条目）。config.memory_enabled 关闭时完全不注入。

    P1-B 起带**血缘标注**：只有 `user` 来源不加前缀（最高可信），其余加
    `〔推断〕`/`〔来自外部内容〕`。这是打断
    「外部网页 → 自动提炼 → 变成持久记忆 → 被当作用户前提」这条链的关键：
    模型必须能区分「用户明说」与「我自己推断的」。
    """
    mem = ctx.dep("memory_full")()
    facts = [f for f in (mem or {}).get("facts", []) if f.get("text")]
    if not facts:
        return None
    picked = facts[-6:]
    try:
        from memory_facade import ORIGIN_LABEL
    except Exception:
        ORIGIN_LABEL = {}
    has_non_user = any(
        str(f.get("origin") or "") in ORIGIN_LABEL for f in picked)
    header = "[长期记忆]"
    if has_non_user:
        header += "（无标注 = 用户明说或早期记录；〔推断〕= 你自己提炼的，不得当用户前提）"
    lines = [header]
    for f in picked:
        label = ORIGIN_LABEL.get(str(f.get("origin") or ""), "")
        lines.append(f"- {label}{f['text']}")
    return "\n".join(lines)


def _enabled_memory(ctx):
    return _not_quiet(ctx) and bool(ctx.cfg.get("memory_enabled", True))


register(Provider(
    name="context.memory", priority=10, provide=_provide_memory,
    enabled=_enabled_memory, budget=4000, critical=True,
    deps=("memory_full",),
    skip_reason="纯净对话已开启，或 memory_enabled=False",
))


def _provide_self_profile(ctx):
    """核心自我状态：有实质内容才注入，空则不占 token。"""
    sp = ctx.dep("self_profile")()
    if sp and str(sp).strip() and "核心自我状态]" in str(sp) and "为空" not in str(sp):
        return str(sp)
    return None


register(Provider(
    name="context.self_profile", priority=20, provide=_provide_self_profile,
    enabled=_not_quiet, budget=8000, critical=True,
    deps=("self_profile",),
    skip_reason="纯净对话已开启",
))


def _provide_brain(ctx):
    """大脑上下文：以最近一条用户消息尾部作 query 走语义检索（话题感知）。

    deps 一律是 thunk，必须调用后取模块对象——这样 `import brain_api` 的失败被
    隔离在本 Provider 内（isolated + degrade），而不是在装配前就把整条链路带崩；
    同时模块属性在调用时解析，测试打桩 `brain_api.brain_context` 依然生效。
    """
    brain_api = ctx.dep("brain_api")()
    q = ""
    for m in reversed(ctx.messages):
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        c = m.get("content")
        if isinstance(c, str) and c.strip():
            q = c.strip()[-60:]
        elif isinstance(c, list):
            txt = "".join(
                str(x.get("text") or "").strip()
                for x in c if isinstance(x, dict) and x.get("type") == "text"
                and str(x.get("text") or "").strip())
            q = txt[-60:] if txt else ""
        break
    try:
        # L1 预算：大脑上下文控制在 ~1500 字符（不挤占其他注入段）
        budget = int(ctx.cfg.get("brain_context_budget") or 1500) or 0
        if budget <= 0:
            bc = brain_api.brain_context(query=q) if q else brain_api.brain_context()
        else:
            bc = (brain_api.brain_context(query=q, budget_chars=budget) if q
                  else brain_api.brain_context(budget_chars=budget))
    except TypeError:  # 兼容旧签名/外部桩（无 query/budget_chars 参数）
        bc = brain_api.brain_context()
    return str(bc) if bc else None


register(Provider(
    name="context.brain", priority=30, provide=_provide_brain,
    enabled=_not_quiet, budget=1600, critical=True,
    deps=("brain_api",),
    skip_reason="纯净对话已开启",
))


def _provide_workspace(ctx):
    """当前工作目录：与 _status() 同口径兜底，避免 AI 瞎猜路径/乱放桌面。"""
    active_dir = str(ctx.cfg.get("active_dir") or "").strip()
    if not active_dir or not os.path.isdir(active_dir):
        active_dir = os.path.join(str(ctx.dep("data_dir") or ""), "workspace")
        try:
            os.makedirs(active_dir, exist_ok=True)
        except Exception:
            pass
    return (
        "[工作区目录] " + active_dir
        + "\n所有新任务的产物都写入该目录下的独立子目录（按任务名新建子目录并写入其中），"
        + "不要写到桌面/临时/系统目录；"
        + "文档/PPT/PDF/图片等给用户看的交付物可放在该子目录或用户指定位置。"
    )


register(Provider(
    name="context.workspace", priority=40, provide=_provide_workspace,
    enabled=_not_pure, budget=800, critical=False,
    deps=("data_dir",),
    skip_reason="对话/纯净模式无工具、不写文件",
))


def _provide_trust(ctx):
    """信任内核自我完整性：仅存在未声明改动/告警时非空（干净时零 token）。"""
    notice = ctx.dep("trust_notice")()
    return str(notice) if notice else None


register(Provider(
    name="context.trust_integrity", priority=45, provide=_provide_trust,
    enabled=_not_pure, budget=2000, critical=False,
    deps=("trust_notice",),
    skip_reason="对话模式不注入",
))


def _provide_degrade(ctx):
    """自我运行状态：仅存在关键降级时非空——让 AI 知道自己当前的能力状况。"""
    notice = ctx.dep("degrade_notice")()
    return str(notice) if notice else None


register(Provider(
    name="context.degrade_status", priority=46, provide=_provide_degrade,
    enabled=_not_pure, budget=1500, critical=False,
    deps=("degrade_notice",),
    skip_reason="对话模式不注入",
))


def _provide_egress(ctx):
    """出网留痕：仅达到阈值（次数/字节）时非空——让 AI 知道自己往外发了什么。"""
    notice = ctx.dep("egress_notice")()
    return str(notice) if notice else None


register(Provider(
    name="context.egress_status", priority=47, provide=_provide_egress,
    enabled=_not_pure, budget=1200, critical=False,
    deps=("egress_notice",),
    skip_reason="对话模式不注入",
))


def _provide_failures(ctx):
    return ctx.dep("failures_text")() or None


register(Provider(
    name="context.failure_patterns", priority=50, provide=_provide_failures,
    enabled=_not_pure, budget=6000, critical=False,
    deps=("failures_text",),
    skip_reason="对话模式不注入",
))


def _provide_patterns(ctx):
    pats = ctx.dep("patterns_load")()
    if not pats:
        return None
    lines = ["[已验证工具链] 以下调用曾成功（同类任务优先复用）："]
    for p in pats[-3:]:
        if isinstance(p, dict) and (p.get("tool") or p.get("recipe")):
            lines.append("- " + str(p.get("tool") or p.get("recipe")))
    return "\n".join(lines) if len(lines) > 1 else None


register(Provider(
    name="context.success_patterns", priority=60, provide=_provide_patterns,
    enabled=_not_pure, budget=1500, critical=False,
    deps=("patterns_load",),
    skip_reason="对话模式不注入",
))


def _provide_plugins(ctx):
    return ctx.dep("plugins_hint")() or None


register(Provider(
    name="context.plugins", priority=70, provide=_provide_plugins,
    enabled=_not_pure, budget=2000, critical=False,
    deps=("plugins_hint",),
    skip_reason="对话模式不注入",
))


def _provide_tasklog(ctx):
    """项目任务记录（跨会话交接参考）。"""
    import stores as stores_mod
    active_dir = str(ctx.dep("active_dir") or "")
    path = os.path.join(active_dir, ".whaletalk", "tasklog.json")
    tl = stores_mod.load_tasklog(path)
    tasks = (tl or {}).get("tasks") or []
    if not tasks:
        return None
    lines = ["[项目任务记录] 跨会话交接参考："]
    for t in tasks[-3:]:
        if isinstance(t, dict) and t.get("title"):
            lines.append("- " + str(t["title"])[:60])
    return "\n".join(lines) if len(lines) > 1 else None


register(Provider(
    name="context.tasklog", priority=80, provide=_provide_tasklog,
    enabled=_not_pure, budget=1500, critical=False,
    deps=("active_dir",),
    skip_reason="对话模式不注入",
))


# ── 装配器 ───────────────────────────────────────────────────────────────

def _base_prompt(ctx: Context) -> str:
    if ctx.pure_chat:
        return str(ctx.dep("dialog_prompt") or "")
    return str(ctx.cfg.get("system_prompt") or ctx.dep("default_prompt") or "")


def assemble(ctx: Context) -> Dict[str, Any]:
    """按 priority 顺序收集各 Provider，返回 prompt / 合并文本 / 回执。

    单个 Provider 失败不会影响其他 Provider；失败经 degrade() 记录（含影响说明），
    并如实出现在回执里——这是本模块存在的意义。
    """
    import degrade

    injected: List[dict] = []
    skipped: List[dict] = []
    failed: List[dict] = []
    parts: List[str] = []

    for p in providers():
        try:
            if not p.enabled(ctx):
                skipped.append({"name": p.name, "reason": p.skip_reason})
                continue
        except Exception as e:  # enabled 自身出错 → 视为跳过，但留痕
            failed.append({"name": p.name, "error": f"{type(e).__name__}: {e}",
                           "critical": p.critical, "stage": "enabled"})
            degrade.degrade(
                p.name, e, "该上下文来源未注入（条件判定失败）", critical=p.critical)
            continue
        # 依赖未注入 = 接线疏漏，不是运行期故障：如实记进回执但不计为失败，
        # 否则一次接线错误会变成永远刷屏的假降级告警。
        missing = [d for d in (p.deps or ()) if d not in ctx.deps]
        if missing:
            skipped.append({"name": p.name,
                            "reason": "依赖未注入：" + "、".join(missing)})
            continue
        try:
            text = p.provide(ctx)
        except Exception as e:
            failed.append({"name": p.name, "error": f"{type(e).__name__}: {e}",
                           "critical": p.critical, "stage": "provide"})
            degrade.degrade(
                p.name, e, f"{p.name} 未注入，本轮回答可能缺少该部分上下文",
                critical=p.critical)
            continue
        if not text:
            skipped.append({"name": p.name, "reason": "本次无内容"})
            continue
        text = str(text)
        truncated = False
        if p.budget and len(text) > p.budget:
            text = text[: p.budget] + "\n…（已按预算截断）"
            truncated = True
        parts.append(text)
        injected.append({"name": p.name, "priority": p.priority,
                         "chars": len(text), "truncated": truncated})

    prompt = _base_prompt(ctx)
    text = "\n\n".join(parts) if parts else None
    receipt = {
        "mode": "dialog" if ctx.pure_chat else "task",
        "pure_chat": bool(ctx.pure_chat),
        "quiet_mode": bool(ctx.quiet_mode),
        "injected": injected,
        "skipped": skipped,
        "failed": failed,
        "total_chars": sum(i["chars"] for i in injected),
        "prompt_chars": len(prompt),
    }
    global _last_receipt
    _last_receipt = receipt
    return {"prompt": prompt, "text": text, "receipt": receipt}


def reset_receipt():
    """清空回执（仅测试用）。"""
    global _last_receipt
    _last_receipt = {}
