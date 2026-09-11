# -*- coding: utf-8 -*-
"""退化日志（Degradation Journal）：让「静默降级」变成可见事实。

## 为什么需要它

全项目统计有 **326 处 `except …: pass`**（api_server 97 / tool_docs 50 /
deepseek_client 35 …）。它们全是 typed except、纪律并不差，但共同的后果是：
**失效不可见**。某个上下文来源坏了、某次记忆同步失败了、某个调度器挂了——
表现只是「AI 好像变差了」，没有任何信号能让人（或让 AI 自己）定位到原因。

这在早期是合理取舍（不阻断用户），现在到了收益递减点。本模块提供唯一出口：

    try:
        ...
    except Exception as e:
        degrade("context.memory", e, "长期记忆未注入，本轮可能答不出偏好", critical=True)

## 设计要点

- **永不抛出**：它是旁路。任何内部异常都被吞掉并降级为一条 logging 记录——
  记录自身出问题绝不能反过来影响主流程。
- **按 key 归并**：同一处反复失败只累加 `count`，不刷屏（`deque`/`OrderedDict` 上限
  200 条，溢出丢最旧）。排障看 `count` 与 `last_ts` 就够。
- **critical 分级**：只有显式标记 critical 的降级才会进入给模型的注入文本。
  理由：几百条无害降级不该占用 token，也不该让模型以为自己随时在坏掉。
- **给模型看的那一份**（`critical_notice`）：这不是错误日志，而是**自我状态**。
  和信任内核同一立场——智能体应当知道自己的状况并如实告知用户，而不是
  在能力受损时继续自信作答。
- 仅依赖标准库（会被 api_server / 工具层 / 上下文装配层共用，不能有导入顺序风险）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import datetime

logger = logging.getLogger("whaletalk.degrade")

DATA_PATH = None          # init() 注入；None = 只留在内存
MAX_ENTRIES = 200         # 去重后的条目上限（超出丢最旧）
LOG_THROTTLE = 60.0       # 同一 key 的日志节流（秒）；首次必记
FLUSH_MIN_INTERVAL = 20.0  # 落盘最小间隔（秒），防高频写盘

_lock = threading.RLock()
_entries: "OrderedDict[str, dict]" = OrderedDict()
_log_at: dict = {}
_last_flush = 0.0


def init(path=None, *, reset=False):
    """注入落盘路径（api_server 启动时调用；可重复调用）。"""
    global DATA_PATH, _last_flush
    with _lock:
        if path:
            DATA_PATH = str(path)
        if reset:
            _entries.clear()
            _log_at.clear()
        _last_flush = 0.0


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _exc_text(exc):
    if exc is None:
        return ""
    try:
        t = type(exc).__name__
        s = str(exc).strip()
        return f"{t}: {s[:180]}" if s else t
    except Exception:
        return ""


def degrade(component, exc=None, impact="", *, key=None, critical=False, detail=None):
    """记一次降级。**永不抛出**；返回归并后的条目（便于测试断言）。

    component  发生降级的组件名（建议点分：context.memory / sched / trust）
    exc        原始异常（可为 None，用于「静默跳过」这类无异常场景）
    impact     对人类可读的影响描述（"长期记忆未注入，本轮可能答不出偏好"）
    key        归并键；默认 component + 异常类型（同类错误反复发生只累加 count）
    critical   是否属于「影响回答质量」的降级 → 会进入给模型的自我状态提示
    """
    try:
        k = str(key or f"{component}:{type(exc).__name__ if exc else 'silent'}")
        with _lock:
            e = _entries.get(k)
            if e is None:
                e = {
                    "key": k,
                    "component": str(component),
                    "count": 0,
                    "first_ts": _now(),
                    "last_ts": "",
                    "last_impact": "",
                    "critical": False,
                    "sample": "",
                }
                _entries[k] = e
            e["count"] += 1
            e["last_ts"] = _now()
            if impact:
                e["last_impact"] = str(impact)[:200]
            if detail:
                e["sample"] = str(detail)[:300]
            elif exc is not None and not e["sample"]:
                e["sample"] = _exc_text(exc)
            # critical 只升不降：一次判定为关键，后续同类都按关键处理
            e["critical"] = bool(e["critical"] or critical)
            while len(_entries) > MAX_ENTRIES:
                _entries.popitem(last=False)
        _maybe_log(k, e, exc)
        _maybe_flush()
        return dict(e)
    except Exception:
        # 记录自身出问题也不能影响主流程
        return None


def _maybe_log(k, entry, exc):
    try:
        now = time.monotonic()
        last = _log_at.get(k, 0.0)
        if last and now - last < LOG_THROTTLE:
            return
        _log_at[k] = now
        logger.warning(
            "降级[%s] %s（累计 %s 次）%s",
            entry["component"], entry["last_impact"] or "无影响说明",
            entry["count"], f"| {_exc_text(exc)}" if exc is not None else "",
        )
    except Exception:
        pass


def _maybe_flush(force=False):
    """节流落盘：把聚合条目写成 JSON，供跨会话排障。"""
    global _last_flush
    try:
        if not DATA_PATH:
            return False
        now = time.monotonic()
        if not force and _last_flush and now - _last_flush < FLUSH_MIN_INTERVAL:
            return False
        with _lock:
            payload = {"updated_at": _now(), "entries": snapshot(limit=MAX_ENTRIES)}
        tmp = DATA_PATH + ".tmp"
        os.makedirs(os.path.dirname(DATA_PATH) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            import json
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(tmp, DATA_PATH)
        _last_flush = now
        return True
    except Exception:
        return False


def snapshot(limit=50):
    """降级条目（新→旧），按最近发生时间排序。"""
    try:
        with _lock:
            items = [dict(v) for v in _entries.values()]
        items.sort(key=lambda d: str(d.get("last_ts") or ""), reverse=True)
        return items[: max(1, int(limit))]
    except Exception:
        return []


def summary():
    """极简摘要（供 /v1/status 这类高频端点，零成本）。"""
    try:
        with _lock:
            items = list(_entries.values())
        return {
            "count": len(items),
            "critical": len([e for e in items if e.get("critical")]),
            "components": sorted({str(e.get("component")) for e in items})[:8],
        }
    except Exception:
        return {"count": 0, "critical": 0, "components": []}


def critical_notice():
    """给模型的「自我运行状态」提示；无关键降级时返回 ""（零 token）。

    措辞与信任内核一致：不是禁令，而是**请如实告知**。
    """
    try:
        items = [e for e in snapshot(limit=MAX_ENTRIES) if e.get("critical")]
        if not items:
            return ""
        lines = ["[运行状态] 本次会话中你的部分能力处于降级状态："]
        for e in items[:6]:
            impact = str(e.get("last_impact") or e.get("sample") or "影响未知")
            lines.append(f"- {e.get('component')}：{impact}（累计 {e.get('count')} 次）")
        lines.append(
            "这些能力受限时段内的回答可能不完整。请如实告知用户你当前的能力状况，"
            "不要在不确知的情况下给出肯定结论。")
        return "\n".join(lines)
    except Exception:
        return ""


def reset():
    """清空（仅测试用）。"""
    with _lock:
        _entries.clear()
        _log_at.clear()
