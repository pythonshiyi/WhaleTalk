"""开放问题账本：把「我不确定什么」从一段散文变成可核销的条目。

为什么需要它：``self_model.json`` 里的 ``unknowns`` 是纯文本列表，写进去就再也
出不来。旧内驱公式 ``mastery = unknowns*14 + ...`` 直接把「未知条数」当驱动力，
于是每核实掉一条，最强的内驱反而掉 14 分 —— 系统被结构性地奖励无知。
2026-10-22 实测：3 条 unknowns 里 2 条当天就已经有答案，却仍挂在自我模型里计债。

本模块把每条未知拆成：断言 + 验证动作 + 状态。可核销才有意义。

状态机::

    unverified --核验通过--> verified
               --核验推翻--> refuted   （下一步必须修正 self_model 里对应的句子）
               --超期未核--> stale     （由 stats() 按时效动态计算，不落盘）

verify_cmd 只写只读动作（命令 / 工具调用）；执行由调用方负责，本模块不做 I/O 之外的事。
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime
from pathlib import Path

DEFAULT_STALE_DAYS = 7.0

# 相对路径便于迁移到别的躯体
def _brain_dir() -> Path:
    try:
        import brainkit as bk
        return Path(bk.BRAIN_DIR)
    except Exception:
        return Path(__file__).resolve().parent.parent / "brain"


def path() -> Path:
    return _brain_dir() / "open_questions.json"


def _now_iso(ts=None) -> str:
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _days_since(value):
    try:
        return max(0.0, (datetime.now().astimezone() - datetime.fromisoformat(str(value)).astimezone()).total_seconds() / 86400.0)
    except Exception:
        return 999.0


def _new_id(existing=None) -> str:
    """时间戳 + 随机尾，保证同一毫秒内连续登记也不撞 id（实测踩过：三条共用一 id，
    mark() 反复只改第一条）。仍撞则递增重试。"""
    base = "q-" + date.today().strftime("%m%d") + "-" + uuid.uuid4().hex[:6]
    taken = {str(q.get("id") or "") for q in (existing or [])}
    cand, n = base, 0
    while cand in taken:
        n += 1
        cand = f"{base}-{n}"
    return cand


def load() -> list:
    """读账本；文件损坏或不存在时返回空列表（账本不该让调用方崩）。"""
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
        items = raw.get("questions") if isinstance(raw, dict) else raw
        return [q for q in (items or []) if isinstance(q, dict)]
    # noqa: silent-except: 账本读取尽力而为，坏文件等同于空账本
    except Exception:
        return []


def save(items) -> list:
    items = list(items or [])
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"version": 1, "updated_at": _now_iso(), "questions": items},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(p)
    return items


def add(claim, verify_cmd="", cost="", tags=None, status="unverified", evidence="") -> dict:
    """登记一条未知。重复的 claim 不新建，直接返回既有条目。"""
    claim = str(claim or "").strip()
    if not claim:
        raise ValueError("claim 不能为空")
    items = load()
    for q in items:
        if str(q.get("claim") or "").strip() == claim:
            return q
    now = _now_iso()
    item = {
        "id": _new_id(items),
        "claim": claim,
        "verify_cmd": str(verify_cmd or "").strip(),
        "cost": str(cost or "").strip(),
        "status": status if status in ("unverified", "verified", "refuted") else "unverified",
        "evidence": str(evidence or "").strip(),
        "tags": list(tags or []),
        "created_at": now,
        "last_checked": now if status != "unverified" else None,
    }
    items.append(item)
    save(items)
    return item


def mark(qid, status, evidence="") -> dict:
    """核销一条：verified / refuted。返回更新后的条目，找不到时返回空 dict。"""
    if status not in ("unverified", "verified", "refuted"):
        raise ValueError("status 必须是 unverified / verified / refuted")
    items = load()
    for q in items:
        if q.get("id") == qid:
            q["status"] = status
            q["last_checked"] = _now_iso()
            if evidence:
                q["evidence"] = str(evidence).strip()
            save(items)
            return q
    return {}


def stats(stale_days=DEFAULT_STALE_DAYS) -> dict:
    """供内驱使用的聚合。stale 按时效动态算，不落盘。"""
    items = load()
    out = {"total": len(items), "verified": 0, "unverified": 0, "refuted": 0, "stale": 0}
    for q in items:
        st = str(q.get("status") or "unverified")
        if st == "verified":
            out["verified"] += 1
        elif st == "refuted":
            out["refuted"] += 1
        else:
            out["unverified"] += 1
            if _days_since(q.get("last_checked") or q.get("created_at")) >= float(stale_days):
                out["stale"] += 1
    return out


def due(limit=1) -> list:
    """最该核的几条：优先久未核，其次成本低（低成本先销账）。"""
    items = [q for q in load() if str(q.get("status") or "unverified") == "unverified"]
    def _key(q):
        cost = str(q.get("cost") or "")
        cheap = 0 if any(u in cost for u in ("s", "秒")) and not any(m in cost for m in ("min", "分")) else 1
        return (-_days_since(q.get("last_checked") or q.get("created_at")), cheap)
    return sorted(items, key=_key)[:max(1, int(limit))]
