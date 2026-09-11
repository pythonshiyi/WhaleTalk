# -*- coding: utf-8 -*-
"""工具钩子管线（Tool Hooks）：把横切关注点从 148 个工具函数里收回到一处。

## 为什么需要它

改造前，横切逻辑半集中半分散：

    已集中：tool_trace / _record_failure / request_approval（在分发点）
    仍分散：snapshot_before 散在 tool_files(3) + deepseek_client(1)
            clamp_int 散在 6 个模块 17 处
            信任内核 declare 只能一个个工具手接

**每加一个横切保障，都要改 N 个文件**——这正是 `audit_tools.py` 这类门禁不得不
存在的原因。本模块提供接缝：钩子在 `@tool()` 注册处（toolkit）统一包装，
工具函数只留业务逻辑。

## 为什么包装在「注册处」而不是「分发处」

分发点（`deepseek_client.execute_tool`）只是众多调用路径之一——`/v1/tools/<x>/invoke`、
工作流、子智能体、测试直调都绕得过去。而 `@tool()` 是**唯一的声明确认点**
（toolkit 的六层全部由它生成），在这里包装意味着：

    任何调用路径都逃不掉钩子；新增横切保障只需注册一个钩子。

## 契约与边界

- **绝不改变工具语义**：钩子抛错一律吞掉并 `degrade()` 留痕；工具自身的异常
  原样向上抛（分发层仍按原逻辑格式化为"工具执行失败: …"）。
- **参数绑定**：包装器用 `inspect.signature` 把位置/关键字参数统一绑定成 dict
  交给钩子；`functools.wraps` 保证 `__name__` / `__doc__` / `inspect.signature`
  对调用方不变（外部按签名自省的行为不受影响）。
- 钩子按 `priority` 升序执行；`ctx.data` 用于钩子间传递（如信任内核的 handle）。
- 只依赖标准库 + 惰性导入项目模块（本模块在 toolkit 导入链最上游）。
"""
from __future__ import annotations

import functools
import inspect
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("whaletalk.tool_hooks")

# 会被当作「文件路径」处理的参数名（快照与信任声明的共同判定面）。
# 刻意用白名单而非「扫描所有字符串参数」：后者会把 write_file 的 content、
# edit_file 的 old/new 也拿去 realpath，既慢又可能误判。
PATH_KEYS = ("path", "file", "filename", "target", "dst", "dest", "src", "output")

# 信任内核文件 basename 缓存（避免每次工具调用都读 trust/config.json）
_KERNEL_CACHE: Dict[str, Any] = {"at": 0.0, "names": frozenset()}
_KERNEL_TTL = 30.0
_cache_lock = threading.Lock()


@dataclass
class CallContext:
    """一次工具调用的上下文（钩子之间共享）。"""

    tool: str
    args: Dict[str, Any] = field(default_factory=dict)
    declared: Tuple[str, ...] = ()
    result: Any = None
    ok: bool = False
    error: Optional[BaseException] = None
    duration: float = 0.0
    data: Dict[str, Any] = field(default_factory=dict)

    def path_args(self):
        """按 PATH_KEYS 提取的 (键, 路径) 列表（跳过空值/超长文本）。"""
        out = []
        for k in PATH_KEYS:
            v = self.args.get(k)
            if isinstance(v, str) and v.strip() and len(v) <= 512 and "\n" not in v:
                out.append((k, v.strip()))
        return out


_HOOKS: Dict[str, List[Tuple[str, int, Callable]]] = {"pre": [], "post": []}
# 必须是 RLock：_ensure_builtins() 持锁期间会调用 hook()，而 hook() 也要持锁——
# 用普通 Lock 会自死锁（首次工具调用即挂住）。
_builtins_lock = threading.RLock()
_builtins_done = False


def hook(kind: str, name: str, fn: Callable, priority: int = 50) -> Callable:
    """注册一个钩子（kind: "pre" | "post"）。同名钩子重复注册只保留先注册者。"""
    if kind not in _HOOKS:
        raise ValueError(f"[tool_hooks] 未知钩子类型: {kind!r}（仅 pre/post）")
    with _builtins_lock:
        for n, _p, _f in _HOOKS[kind]:
            if n == name:
                return fn
        _HOOKS[kind].append((str(name), int(priority), fn))
        _HOOKS[kind].sort(key=lambda t: t[1])
    return fn


def pre_hook(name: str, priority: int = 50) -> Callable:
    def deco(f):
        hook("pre", name, f, priority)
        return f
    return deco


def post_hook(name: str, priority: int = 50) -> Callable:
    def deco(f):
        hook("post", name, f, priority)
        return f
    return deco


def _run(kind: str, ctx: CallContext):
    """按序执行钩子。任

    一钩子抛错都被吞掉 + degrade 留痕——横切保障绝不能反过来弄坏工具调用。
    """
    for name, _p, fn in list(_HOOKS[kind]):
        try:
            fn(ctx)
        except Exception as e:  # noqa: BLE001
            try:
                import degrade
                degrade.degrade(
                    f"toolhook.{kind}.{name}", e,
                    f"{kind} 钩子未生效（工具 {ctx.tool} 的横切保障缺失）",
                    critical=False)
            except Exception:
                logger.debug("钩子失败且降级记录不可用: %s", name, exc_info=True)


# ── 内置钩子 ─────────────────────────────────────────────────────────────

def _kernel_basenames() -> frozenset:
    now = time.monotonic()
    with _cache_lock:
        if _KERNEL_CACHE["names"] and now - _KERNEL_CACHE["at"] < _KERNEL_TTL:
            return _KERNEL_CACHE["names"]
    names = frozenset()
    try:
        import trust_kernel
        names = frozenset(os.path.basename(str(n).replace("\\", "/"))
                          for n in trust_kernel.protected_names())
    except Exception:
        names = frozenset()
    with _cache_lock:
        _KERNEL_CACHE["at"] = now
        _KERNEL_CACHE["names"] = names
    return names


def _hook_trust_declare(ctx: CallContext):
    """信任内核：命中内核文件的路径参数 → 登记为「已声明改动」。

    注意立场：**这不是闸门**（不阻断写入），只是让智能体无法悄悄改自己的护栏。
    真正可靠的检出仍在 trust_kernel.verify()（效果层），因为 run_python 等
    路径本来就绕得过工具层。
    """
    basenames = _kernel_basenames()
    if not basenames:
        return
    import trust_kernel
    handles = ctx.data.setdefault("trust_handles", [])
    for key, path in ctx.path_args():
        try:
            if os.path.basename(path.replace("\\", "/")) not in basenames:
                continue
            h = trust_kernel.declare(path, f"{ctx.tool}({key})", actor="tool")
            if h:
                handles.append(h)
        except Exception as e:  # noqa: BLE001
            try:
                import degrade
                degrade.degrade("toolhook.trust_declare", e,
                                "内核文件改动未登记（该次修改会显示为「未声明」）")
            except Exception:
                pass


def _hook_trust_commit(ctx: CallContext):
    handles = ctx.data.get("trust_handles") or []
    if not handles:
        return
    try:
        import trust_kernel
        for h in handles:
            trust_kernel.commit(h, ok=bool(ctx.ok))
    except Exception as e:  # noqa: BLE001
        try:
            import degrade
            degrade.degrade("toolhook.trust_commit", e,
                            "内核改动未提交基线（下次核对会显示为未声明）")
        except Exception:
            pass
        return
    # 结果补充：让**模型**也知道这次改动被登记了（透明性，且对所有工具通用，
    # 不必在每个工具里手写一句提示）。只对字符串结果追加，不动结构化返回值。
    if ctx.ok and isinstance(ctx.result, str):
        names = "、".join(sorted({str(h.get("name")) for h in handles if h.get("name")}))
        ctx.result = ctx.result.rstrip() + (
            f"（已登记为信任内核声明改动：{names}，基线已推进、可回滚）")


def _hook_snapshot(ctx: CallContext):
    """写前快照（**仅对声明了 hooks=("snapshot",) 的工具生效**）。

    全局注册的钩子会对每个工具调用执行，因此"是否适用"必须由钩子自己按
    ctx.declared 判定——否则所有带 path 参数的工具都会被无差别快照。
    """
    if "snapshot" not in (ctx.declared or ()):
        return
    import snapshot as snapshot_mod
    for key, path in ctx.path_args():
        try:
            if os.path.isfile(path):
                snapshot_mod.snapshot_before(ctx.tool, path)
        except Exception as e:  # noqa: BLE001
            try:
                import degrade
                degrade.degrade(f"toolhook.snapshot.{ctx.tool}", e,
                                "写前快照失败，该次写入将无法一键恢复")
            except Exception:
                pass


def _ensure_builtins():
    """惰性注册内置钩子（首次工具调用时）。

    惰性而非模块导入时：本模块在 toolkit 导入链最上游，此处的 `import
    trust_kernel / snapshot / degrade` 若提前执行会带来导入顺序风险。
    """
    global _builtins_done
    if _builtins_done:
        return
    with _builtins_lock:
        if _builtins_done:
            return
        hook("pre", "trust_declare", _hook_trust_declare, 20)
        hook("pre", "snapshot", _hook_snapshot, 30)
        hook("post", "trust_commit", _hook_trust_commit, 20)
        _builtins_done = True


# ── 包装器 ───────────────────────────────────────────────────────────────

def declared_names(fn) -> Tuple[str, ...]:
    """取出函数上声明的钩子名（@tool(hooks=…) 经 toolkit 传递）。"""
    return tuple(getattr(fn, "__wt_hooks__", ()) or ())


def wrap(tool_name: str, fn: Optional[Callable],
         declared: Sequence[str] = ()) -> Optional[Callable]:
    """返回带钩子的包装函数。fn 非可调用时原样返回（特殊回调工具）。"""
    if fn is None or not callable(fn):
        return fn
    declared = tuple(str(x) for x in (declared or ()))
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        sig = None

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _ensure_builtins()
        bound: Dict[str, Any] = dict(kwargs)
        if sig is not None:
            try:
                bound = dict(sig.bind_partial(*args, **kwargs).arguments)
            except TypeError:
                bound = dict(kwargs)
        ctx = CallContext(tool=tool_name, args=bound, declared=declared)
        _run("pre", ctx)
        t0 = time.monotonic()
        try:
            ctx.result = fn(*args, **kwargs)
            ctx.ok = True
        except BaseException as e:  # noqa: BLE001 —— 原样上抛，钩子不得吞异常
            ctx.ok = False
            ctx.error = e
            ctx.duration = time.monotonic() - t0
            _run("post", ctx)
            raise
        # 注意：post 必须跑完再取 ctx.result——若写在 finally 里，`return` 的表达式
        # 会先求值，post 钩子对结果的补充（如信任声明回执）就落不到返回值上。
        ctx.duration = time.monotonic() - t0
        _run("post", ctx)
        return ctx.result

    wrapper.__wt_tool__ = tool_name
    wrapper.__wt_hooks__ = declared
    return wrapper


def clear_kernel_cache():
    """清空内核 basename 缓存（仅测试用；生产靠 30s TTL 自然过期）。"""
    _KERNEL_CACHE["at"] = 0.0
    _KERNEL_CACHE["names"] = frozenset()


def reset_for_tests():
    """清空钩子（仅测试用）。"""
    global _builtins_done
    with _builtins_lock:
        _HOOKS["pre"].clear()
        _HOOKS["post"].clear()
        _builtins_done = False
    clear_kernel_cache()


def hook_names(kind: str) -> List[str]:
    return [n for n, _p, _f in _HOOKS.get(kind, [])]
