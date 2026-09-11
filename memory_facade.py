# -*- coding: utf-8 -*-
"""记忆单一门面（Memory Facade）：让长期记忆带上「血缘」与「作废」语义。

## 为什么需要它

`memory.json` 的 facts 此前有 **4 个写入方**（`write_memory` 工具、
`api_server._chat_harvest` 自动提炼、`update_memory` / `delete_memory` 工具、
以及大脑同步侧写），字段只有 `{key, value, type, ts}`。由此产生两类**已经发生过**
的问题：

1. **无血缘**：`_chat_harvest` 每次对话后自动提炼 0-3 条写入长期记忆，此后注入
   每一轮上下文。而外部网页内容只靠 `_wrap_external` 的文本标记隔离——一条
   「网页 → 我复述 → harvest 提炼 → 变成永久记忆」的链就此成立。**根本缺陷是
   模型无法区分「用户明说的」与「我自己推断的」**，于是把自己推断的东西当作用户前提。
2. **无作废**：矛盾的旧事实不会被取代——写入 `write_memory` 时的"近重复合并"
   只并 `tags/type/entities`，**新说法反而被丢弃**（旧 `value` 保留）。于是
   "用户偏好中文回复" 之后写 "用户偏好英文回复"，生效的仍是旧的那条。

## 设计

- **origin（血缘）**：`user` / `agent` / `web` / `system` / `unknown`（旧记录）。
  注入时对非 user 来源加标注，让模型知道这条是**自己推断的**，不得当作用户前提。
- **supersede（作废）**：新事实取代旧事实时，旧记录标 `status="superseded"` 并双向
  链接（`supersedes` / `superseded_by`）——**可回溯、可恢复**，绝不静默删除。
  **只按显式 `key`（语义槽位）触发**，不按相似度——理由见下。
- **confidence**：`user`=1.0 / `system`=1.0 / `agent`=0.5 / `web`=0.3。
- **持久化**：沿用 `memory.json` 的 `{enabled, facts: [...]}` 结构与既有字段名
  （`key`/`value`/`type`/`ts`/`entities`/`relations`），只**新增**字段；
  旧数据读时补默认值（惰性、不改写文件），因此**向后兼容、可回退**。
- 用 `origin` 而非 `source`：`memory_store.py` 的 canonical schema 里 `source`
  已经是「数据来源」（memory.json / brain / knowledge），不能占用。

## 明确不做

- **不删除**：作废是标记 + 链接，用户可 `restore` 回来。
- **不按相似度自动作废**——这是本项目实测后的结论，不是保守取舍。
  实测（本模块 `similarity()` 在真实记忆对上的得分）：

  | 记忆对 | 相似度 | 期望 |
  |---|---|---|
  | 偏好中文回复 ↔ 偏好英文回复 | 0.556 | 应作废 |
  | 每周五备份 ↔ 每周五备份（改为周四） | 0.636 | 应作废 |
  | 纯静态架构 ↔ 改用 Next.js 架构 | **0.154** | 应作废 |
  | 项目**A**用 PG ↔ 项目**B**用 PG | **0.600** | **绝不能**作废 |

  **相似度无法区分「应作废」与「绝不能作废」**：0.556 要作废而 0.600 不能；
  而真正该作废的「架构迭代」只有 0.154（几乎不含共同词）。
  因此自动作废只认**显式 `supersede_key`**；相似度仅用于**冲突提示**（把判断交还给具备
  判断能力的角色，而不是替它猜）。要真正做语义级作废需要向量语义信号，超出本层范围。
- 不接管大脑记忆（`brain/memories/memory.jsonl`）：它已有 `source/importance/
  supersedes` 体系，本门面只管 `memory.json` 这一层。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("whaletalk.memory")

MEMORY_PATH = None
CONFLICT_SIM = 0.55            # 冲突提示阈值（**只提示，不动作**；实测见模块文档）
MAX_ITEMS = 2000               # 与既有 MEMORY_MAX_ITEMS 同量级

_lock = threading.RLock()

VALID_ORIGINS = ("user", "agent", "web", "system", "unknown")
ORIGIN_CONFIDENCE = {"user": 1.0, "system": 1.0, "agent": 0.5,
                     "web": 0.3, "unknown": 0.5}
# 注入时的来源标注（user 不标注 = 最高可信）
ORIGIN_LABEL = {"agent": "〔推断〕", "web": "〔来自外部内容〕", "system": "〔系统〕"}

_WORD_RE = re.compile(r"[A-Za-z0-9_]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def init(memory_path=None, *, conflict_sim=None, reset=False):
    """注入 memory.json 路径（api_server 启动时调用；可重复）。"""
    global MEMORY_PATH, CONFLICT_SIM
    with _lock:
        if memory_path:
            MEMORY_PATH = str(memory_path)
        if conflict_sim is not None:
            CONFLICT_SIM = float(conflict_sim)
        if reset:
            pass


def _now():
    return datetime.now().isoformat(timespec="seconds")


def _make_id(ts, value):
    """稳定 id：同一 (ts, 文本) 在任何时候都得到同一个 id（旧记录也能派生）。"""
    raw = f"{ts or ''}|{value or ''}".encode("utf-8", "ignore")
    return hashlib.sha1(raw).hexdigest()[:12]


# ── 相似度（纯标准库：中文 bigram + 拉丁词，Jaccard）──────────────────────

def _tokens(text):
    s = str(text or "")
    toks = set(w.lower() for w in _WORD_RE.findall(s))
    cjk = _CJK_RE.findall(s)
    toks.update("".join(cjk[i:i + 2]) for i in range(max(0, len(cjk) - 1)))
    if len(cjk) == 1:
        toks.add(cjk[0])
    return toks


def similarity(a, b):
    """Jaccard 相似度（0-1）。空文本返回 1.0（同为空视为同一类）。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


# ── 读取与归一化 ─────────────────────────────────────────────────────────

def _load():
    if not MEMORY_PATH or not os.path.exists(MEMORY_PATH):
        return {"enabled": False, "facts": []}
    try:
        with open(MEMORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"enabled": False, "facts": []}
        data.setdefault("enabled", False)
        data.setdefault("facts", [])
        return data
    except Exception:
        logger.exception("读取长期记忆失败")
        return {"enabled": False, "facts": []}


def _save(data):
    """原子写 + 跨进程文件锁（与既有 _save_memory 同口径）。"""
    if not MEMORY_PATH:
        return False
    try:
        from shared import file_lock
    except Exception:
        file_lock = None
    try:
        if file_lock is not None:
            with file_lock(MEMORY_PATH, timeout=10):
                return _save_impl(data)
        return _save_impl(data)
    except TimeoutError:
        logger.warning("记忆保存等待文件锁超时")
        return False
    except Exception:
        logger.exception("保存长期记忆失败")
        return False


def _save_impl(data):
    try:
        os.makedirs(os.path.dirname(MEMORY_PATH) or ".", exist_ok=True)
        tmp = MEMORY_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, MEMORY_PATH)
        return True
    except Exception:
        logger.exception("写入长期记忆失败")
        return False


def normalize(fact) -> Dict[str, Any]:
    """补默认字段（**不改写文件**，只在内存里补）——旧数据因此向后兼容。"""
    if not isinstance(fact, dict):
        return {}
    out = dict(fact)
    text = str(out.get("value") or out.get("text") or "")
    out["value"] = text
    out.setdefault("key", "")
    out.setdefault("type", "")
    out.setdefault("ts", "")
    out.setdefault("entities", [])
    out.setdefault("relations", [])
    origin = str(out.get("origin") or "").strip().lower()
    out["origin"] = origin if origin in VALID_ORIGINS else "unknown"
    try:
        conf = float(out.get("confidence"))
    except (TypeError, ValueError):
        conf = ORIGIN_CONFIDENCE.get(out["origin"], 0.5)
    out["confidence"] = max(0.0, min(1.0, conf))
    status = str(out.get("status") or "").strip().lower()
    out["status"] = status if status in ("active", "superseded") else "active"
    out.setdefault("supersedes", "")
    out.setdefault("superseded_by", "")
    out.setdefault("superseded_at", "")
    out.setdefault("reason", "")
    out["id"] = str(out.get("id") or _make_id(out.get("ts"), text))
    return out


def all_facts(include_superseded=False) -> List[Dict[str, Any]]:
    """全部（归一化后的）记录。默认只返回 active。"""
    with _lock:
        data = _load()
    out = []
    for f in data.get("facts") or []:
        n = normalize(f)
        if not n:
            continue
        if not include_superseded and n.get("status") != "active":
            continue
        out.append(n)
    return out


def active_facts(limit=None) -> List[Dict[str, Any]]:
    items = all_facts(include_superseded=False)
    return items[-int(limit):] if limit else items


def stats() -> Dict[str, Any]:
    items = all_facts(include_superseded=True)
    by_origin: Dict[str, int] = {}
    for f in items:
        by_origin[f["origin"]] = by_origin.get(f["origin"], 0) + 1
    return {
        "total": len(items),
        "active": len([f for f in items if f["status"] == "active"]),
        "superseded": len([f for f in items if f["status"] == "superseded"]),
        "by_origin": by_origin,
    }


def find(match) -> Optional[Dict[str, Any]]:
    """按 id（精确）或文本子串（首个命中）查找。"""
    m = str(match or "").strip()
    if not m:
        return None
    for f in all_facts(include_superseded=True):
        if f["id"] == m:
            return f
    for f in all_facts(include_superseded=True):
        if m in str(f.get("value") or ""):
            return f
    return None


# ── 写入 ─────────────────────────────────────────────────────────────────

def remember(text, *, origin="agent", type="", tags="", key=None,
             confidence=None, entities=None, relations=None,
             supersede_key=None) -> Dict[str, Any]:
    """写入一条记忆，自动处理去重 / 取代 / 冲突提示。

    返回 ``{"ok", "action", "id", "superseded_id", "conflict", "message"}``；
    action ∈ added / duplicate / superseded / merged。

    - 完全相同 → duplicate（不写）
    - **显式传 `supersede_key`** 才会取代旧条目（按 id / key / 完全相同文本匹配）——
      这是唯一的自动作废路径
    - 否则：相似度 ≥ CONFLICT_SIM 只**提示冲突**（回执带旧条目 id，调用方可据此
      二次调用传入 supersede_key），**绝不自动作废**（理由见模块文档的实测表）

    为什么不能拿 `key`/`tags` 当取代依据：默认 key（"自动记忆"）是兜底值，若用它
    匹配，两条毫不相干、只是都没填 tags 的记忆会互相作废——端到端实测抓到过
    「用户明说的偏好」被一条无关的 agent 推断顶掉。取代必须**显式声明**。
    """
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "action": "error", "message": "错误：记忆内容为空"}
    origin = str(origin or "agent").strip().lower()
    if origin not in VALID_ORIGINS:
        origin = "agent"
    if confidence is None:
        confidence = ORIGIN_CONFIDENCE.get(origin, 0.5)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = ORIGIN_CONFIDENCE.get(origin, 0.5)
    new_type = str(type or "").strip()[:20]
    key_v = str(key or "").strip()[:40]
    sk = str(supersede_key or "").strip()

    with _lock:
        data = _load()
        facts = data.get("facts") or []
        norm = [(i, normalize(f)) for i, f in enumerate(facts)]
        # ① 完全相同 → 不重复写
        for _i, n in norm:
            if n["status"] == "active" and str(n.get("value") or "") == text:
                return {"ok": True, "action": "duplicate", "id": n["id"],
                        "superseded_id": None, "conflict": None,
                        "message": "该内容已存在，未重复写入"}
        # ② 找取代/冲突目标
        #    取代**只在显式传 supersede_key 时**发生（按 id / key / 完全相同文本匹配）；
        #    相似度仅用于冲突提示，把判断交还给具备判断能力的角色（实测见模块文档）。
        target_i, target, conflict = None, None, None
        if sk:
            for i, n in norm:
                if n["status"] != "active":
                    continue
                if sk in (n["id"], str(n.get("key") or "")) or \
                        str(n.get("value") or "") == sk:
                    target_i, target = i, n
                    break
            if target is None:
                return {"ok": False, "action": "error",
                        "message": f"错误：未找到要取代的记忆：{sk}"}
        else:
            for i, n in norm:
                if n["status"] != "active":
                    continue
                sim = similarity(str(n.get("value") or ""), text)
                if sim >= CONFLICT_SIM and conflict is None:
                    conflict = {"id": n["id"], "text": str(n.get("value") or "")[:80],
                                "similarity": round(sim, 3),
                                "key": str(n.get("key") or "")}
        ts = _now()
        entry = {"key": key_v or "自动记忆", "value": text, "ts": ts,
                 "origin": origin, "confidence": confidence, "status": "active",
                 "supersedes": "", "superseded_by": "",
                 "entities": [str(e)[:30] for e in (entities or [])][:20],
                 "relations": list(relations or [])[:20]}
        if new_type:
            entry["type"] = new_type
        entry["id"] = _make_id(ts, text)
        # ③ 按**下标**就地作废（不用派生 id 反查：条目被其它写入方改写后 id 会变，
        #    按下标定位才稳），并固化 id 保证链接可查、可回滚。
        superseded_id = ""
        if target is not None and target_i is not None and target_i < len(facts):
            raw = facts[target_i]
            if isinstance(raw, dict):
                superseded_id = target["id"]
                raw["status"] = "superseded"
                raw["superseded_at"] = ts
                raw["superseded_by"] = entry["id"]
                raw["reason"] = "被新记忆取代"
                raw["id"] = superseded_id
                entry["supersedes"] = superseded_id
        facts.append(entry)
        if len(facts) > MAX_ITEMS:
            # 溢出优先丢最旧的已作废条目（active 一律保留）
            excess = len(facts) - MAX_ITEMS
            keep, dropped = [], 0
            for f in facts:
                if dropped < excess and normalize(f).get("status") == "superseded":
                    dropped += 1
                    continue
                keep.append(f)
            facts = keep[-MAX_ITEMS:]
        data["facts"] = facts
        if not _save(data):
            return {"ok": False, "action": "error", "message": "错误：记忆写入失败"}
    action = "superseded" if superseded_id else "added"
    msg = ""
    if superseded_id:
        msg = "已作废 1 条旧记忆（可 restore 恢复）"
    elif conflict:
        msg = (f"注意：已有相似记忆（相似度 {conflict['similarity']:.0%}）："
               f"{conflict['text']}——如需取代它，请再次写入并传 "
               f"supersede_key=\"{conflict['id']}\"；本门面不会替你猜。")
    return {"ok": True, "action": action, "id": entry["id"],
            "superseded_id": superseded_id or None, "conflict": conflict,
            "message": msg}


def invalidate(match, reason="") -> Tuple[bool, str]:
    """手动作废（可恢复）。"""
    f = find(match)
    if not f:
        return False, f"未找到该记忆：{match}"
    with _lock:
        data = _load()
        for raw in data.get("facts") or []:
            if _make_id(raw.get("ts"), str(raw.get("value") or raw.get("text") or "")) == f["id"]:
                raw["status"] = "superseded"
                raw["superseded_at"] = _now()
                raw["reason"] = str(reason or "手动作废")[:120]
                raw["id"] = f["id"]
                break
        if not _save(data):
            return False, "作废失败：写入错误"
    return True, f"已作废：{str(f.get('value'))[:60]}"


def restore(match) -> Tuple[bool, str]:
    """恢复被作废的记忆。"""
    f = find(match)
    if not f:
        return False, f"未找到该记忆：{match}"
    if f.get("status") == "active":
        return True, "该记忆已是生效状态"
    with _lock:
        data = _load()
        for raw in data.get("facts") or []:
            if _make_id(raw.get("ts"), str(raw.get("value") or raw.get("text") or "")) == f["id"]:
                raw["status"] = "active"
                raw["superseded_at"] = ""
                raw["reason"] = ""
                raw["superseded_by"] = ""
                raw["id"] = f["id"]
                break
        if not _save(data):
            return False, "恢复失败：写入错误"
    return True, f"已恢复：{str(f.get('value'))[:60]}"


def render_for_context(limit=6) -> Optional[str]:
    """渲染成给模型的注入文本（带血缘标注）；无内容返回 None。

    标注规则：`user` 不标注（最高可信）；其余来源加 `〔推断〕` / `〔来自外部内容〕`，
    并在有非 user 条目时补一行说明——这正是打断
    「外部内容 → 自动提炼 → 被当作用户前提」这条链的地方。
    """
    facts = active_facts()
    if not facts:
        return None
    picked = facts[-int(limit):]
    lines = []
    has_non_user = any(f["origin"] != "user" for f in picked)
    header = "[长期记忆]"
    if has_non_user:
        header += "（无标注 = 用户明说或早期记录；〔推断〕= 你自己提炼的，不得当用户前提）"
    lines.append(header)
    for f in picked:
        label = ORIGIN_LABEL.get(f["origin"], "")
        lines.append(f"- {label}{f.get('value')}")
    return "\n".join(lines)


def reset():
    """清空（仅测试用）。"""
    with _lock:
        pass
