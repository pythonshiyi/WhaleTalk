"""长任务计划（任务模式）：拆步、逐步验收、失败自愈、可暂停续跑。

设计边界：
- 计划文件落在 DATA_DIR/task_plans/<plan_id>.json，跨重启保留；
- 规划失败不阻断任务：限流/断网时按显式顺序词本地拆步，无强信号则普通回复；
- 步骤只有调用 task_step_done 并给出 evidence 才算完成；结构化验收失败会转为失败/阻塞；
- 用户可以暂停、继续、取消、单步重试或跳过；状态与证据原子落盘；
- 同一会话存在未完成计划时自动续跑；用户说「新任务/重新开始」则重开。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger("whaletalk")

_PLAN_DIR: Optional[str] = None
_WRITE_LOCK = threading.RLock()
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{4,40}$")

# 同一步自动失败达到该次数后标记 blocked，停止自动续跑，交还用户决定。
MAX_STEP_ATTEMPTS = 3

ACTIVE_STATUSES = ("running", "paused", "blocked")
TERMINAL_STATUSES = ("completed", "cancelled")
STEP_ACTIVE_STATUSES = ("pending", "running", "failed", "blocked")

PLANNER_PROMPT = (
    "你是任务规划器。只有目标需要多步执行/多次工具调用时才拆步，拆成 3-8 个可验收步骤，每步一句话，按执行顺序。"
    "如果目标只需一次回答、简单查询，或用户明确要求不要调用工具，输出空计划。"
    "步骤要具体到能判断是否完成；不要写“继续处理”这类空话。"
    "只输出 JSON：需要拆步时 {\"steps\":[{\"title\":\"...\"}]}；不需要时 {\"steps\":[]}"
)

NEW_TASK_HINTS = ("新任务", "重新开始", "换个任务", "重开一个任务")
CONTINUE_HINTS = ("继续", "接着", "然后", "下一步", "剩下的", "没做完", "未完成", "恢复任务")
# 明确的一次性回答：不调用规划模型，也不生成计划卡
SIMPLE_HINTS = ("只回复", "不要调用工具", "直接回答", "一句话回答", "简单说")

# “先…再…最后…”这类显式顺序词。本地兜底拆步只认强信号，
# 避免把模糊请求也拆成未完成步骤。
_STEP_MARKERS = (
    "首先", "第一步", "第二步", "第三步", "第四步", "第五步",
    "先", "再", "然后", "接着", "最后",
)
_NUMBERED_LINE = re.compile(
    r"^\s*(?:\d+[.、)]|第[一二三四五六七八九十]+步[:：]?|步骤\s*\d+[:：]?)\s*(.+)$"
)
_TRAILING_FILLER = re.compile(r"(?:完成后告诉我|最后告诉我|即可|就好)[。！!]*$")


def configure(plan_dir: str) -> None:
    global _PLAN_DIR
    _PLAN_DIR = str(plan_dir)


def _dir() -> str:
    global _PLAN_DIR
    if not _PLAN_DIR:
        _PLAN_DIR = os.environ.get("WHALETALK_TASK_PLAN_DIR") or os.path.join(
            os.path.expanduser("~"), ".whaletalk", "task_plans"
        )
    os.makedirs(_PLAN_DIR, exist_ok=True)
    return _PLAN_DIR


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _norm_step(step: Any, idx: int) -> Dict[str, Any]:
    if not isinstance(step, dict):
        step = {"title": str(step or "")}
    out = dict(step)
    out.setdefault("id", idx)
    out.setdefault("title", "")
    out.setdefault("status", "pending")
    out.setdefault("evidence", "")
    out.setdefault("verification", {})
    out.setdefault("attempts", 0)
    out.setdefault("last_error", "")
    out.setdefault("updated_at", "")
    try:
        out["id"] = int(out["id"])
    except (TypeError, ValueError):
        out["id"] = idx
    try:
        out["attempts"] = int(out.get("attempts") or 0)
    except (TypeError, ValueError):
        out["attempts"] = 0
    if not isinstance(out.get("verification"), dict):
        out["verification"] = {}
    return out


@dataclass
class TaskPlan:
    plan_id: str
    session_id: str
    goal: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "running"  # running / paused / blocked / completed / cancelled
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _now()
        if not self.updated_at:
            self.updated_at = self.created_at
        self.steps = [_norm_step(s, i) for i, s in enumerate(self.steps, 1)]

    # -- 查询 --
    def done_count(self) -> int:
        return sum(1 for s in self.steps if s.get("status") == "done")

    def failed_count(self) -> int:
        return sum(1 for s in self.steps if s.get("status") in ("failed", "blocked"))

    def skipped_count(self) -> int:
        return sum(1 for s in self.steps if s.get("status") == "skipped")

    def all_done(self) -> bool:
        return bool(self.steps) and all(
            s.get("status") in ("done", "skipped") for s in self.steps
        )

    def current_step(self) -> Optional[Dict[str, Any]]:
        left = pending_steps(self)
        return left[0] if left else None

    def payload(self) -> Dict[str, Any]:
        cur = self.current_step() or {}
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status,
            "steps": [
                {
                    "id": s.get("id"),
                    "title": s.get("title", ""),
                    "status": s.get("status", "pending"),
                    "evidence": s.get("evidence", ""),
                    "verification": s.get("verification", {}),
                    "attempts": int(s.get("attempts") or 0),
                    "last_error": s.get("last_error", ""),
                    "updated_at": s.get("updated_at", ""),
                }
                for s in self.steps
            ],
            "done": self.done_count(),
            "total": len(self.steps),
            "failed": self.failed_count(),
            "skipped": self.skipped_count(),
            "current_step": cur.get("id"),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "steps": self.steps,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskPlan":
        return cls(
            plan_id=str(data.get("plan_id") or ""),
            session_id=str(data.get("session_id") or ""),
            goal=str(data.get("goal") or ""),
            steps=list(data.get("steps") or []),
            status=str(data.get("status") or "running"),
            created_at=str(data.get("created_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    # -- 更新 --
    def _sync_status(self) -> None:
        if self.all_done():
            self.status = "completed"
        elif any(s.get("status") == "blocked" for s in self.steps):
            self.status = "blocked"
        elif self.status not in ("paused", "cancelled"):
            self.status = "running"
        self.updated_at = _now()

    def mark(self, step: int, status: str, evidence: str,
             verification: Optional[Dict[str, Any]] = None) -> tuple[bool, str]:
        raw = str(status or "").lower()
        status = "done" if raw in ("done", "完成", "ok") else "failed"
        target = None
        for s in self.steps:
            if int(s.get("id") or 0) == int(step):
                target = s
                break
        if target is None:
            return False, f"错误：步骤 {step} 不存在（共 {len(self.steps)} 步）"

        target["updated_at"] = _now()
        if verification is not None:
            target["verification"] = verification
        if status == "done":
            target["status"] = "done"
            target["evidence"] = str(evidence or "").strip()
            target["last_error"] = ""
            self._sync_status()
            return True, f"步骤 {step} 已验收完成"

        attempts = int(target.get("attempts") or 0) + 1
        target["attempts"] = attempts
        target["status"] = "blocked" if attempts >= MAX_STEP_ATTEMPTS else "failed"
        target["evidence"] = str(evidence or "").strip()
        target["last_error"] = str(evidence or "").strip()[:300]
        self._sync_status()
        if target["status"] == "blocked":
            return True, (
                f"步骤 {step} 已连续失败 {attempts} 次，标记为阻塞；"
                "请在任务中心重试/跳过，或换一种方法后回复“继续”。"
            )
        return True, (
            f"步骤 {step} 标记为失败（第 {attempts}/{MAX_STEP_ATTEMPTS} 次）；"
            f"失败原因：{target['last_error'][:160]}"
        )


def _path(plan_id: str) -> str:
    if not _SAFE_ID.match(str(plan_id or "")):
        raise ValueError("非法 plan_id")
    return os.path.join(_dir(), f"{plan_id}.json")


def save(plan: TaskPlan) -> str:
    path = _path(plan.plan_id)
    tmp = path + ".tmp"
    with _WRITE_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    return path


def _resolve_verify_path(path: str) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    try:
        import permissions
        return permissions.resolve(text) or os.path.abspath(text)
    except Exception:
        return os.path.abspath(text)


def _read_text(path: str, limit: int = 200000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except Exception:
        return ""


def verify_evidence(evidence: str, verify_path: str = "",
                    spec: Optional[Dict[str, Any]] = None) -> tuple[bool, Dict[str, Any], str]:
    """结构化验收：全部通过后才能把步骤标为 done。

    支持：verify_path 存在性、文本包含/不包含、最小体积、JSON key 列表。
    不执行用户/模型提供的命令，避免把验收变成任意代码执行。
    """
    checks: List[str] = []
    ok = True
    result: Dict[str, Any] = {"ok": True, "checks": []}
    if not str(evidence or "").strip():
        return False, {"ok": False, "checks": ["缺少 evidence"]}, "缺少 evidence"

    if isinstance(spec, str) and str(spec).strip():
        try:
            spec = json.loads(str(spec))
        except Exception as e:
            return False, {"ok": False, "checks": ["verification 不是合法 JSON"]}, f"verification 解析失败：{e}"
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        return False, {"ok": False, "checks": ["verification 必须是对象"]}, "verification 必须是对象"

    full = _resolve_verify_path(verify_path or str(spec.get("path") or ""))
    if full:
        exists = os.path.exists(full)
        result["path"] = full
        checks.append(f"路径存在：{'是' if exists else '否'}（{full}）")
        if not exists:
            ok = False

    expect_file = str(spec.get("expect_type") or "").strip()
    if expect_file and full and os.path.exists(full):
        actual = "dir" if os.path.isdir(full) else "file"
        passed = actual == expect_file
        checks.append(f"类型：{actual}（期望 {expect_file}）{'✓' if passed else '✗'}")
        ok = ok and passed

    text = _read_text(full) if full and os.path.isfile(full) else ""
    for key, want_pass in (("expect_contains", True), ("expect_not_contains", False)):
        needles = spec.get(key) or []
        if isinstance(needles, str):
            needles = [needles]
        for needle in needles[:20]:
            needle = str(needle)
            if not needle:
                continue
            hit = needle in text
            passed = hit if want_pass else not hit
            label = "包含" if want_pass else "不包含"
            checks.append(f"{label}「{needle[:40]}」：{'✓' if passed else '✗'}")
            ok = ok and passed

    min_size = spec.get("min_size")
    if min_size is not None and full and os.path.isfile(full):
        try:
            size = os.path.getsize(full)
            passed = size >= int(min_size)
            checks.append(f"文件大小 {size}B ≥ {int(min_size)}B：{'✓' if passed else '✗'}")
            ok = ok and passed
        except Exception as e:
            ok = False
            checks.append(f"文件大小检查失败：{e}")

    json_keys = spec.get("json_keys") or []
    if json_keys and full and os.path.isfile(full):
        try:
            with open(full, encoding="utf-8") as f:
                data = json.load(f)
            for key in json_keys[:20]:
                cur = data
                passed = True
                for part in str(key).split("."):
                    if isinstance(cur, dict) and part in cur:
                        cur = cur[part]
                    else:
                        passed = False
                        break
                checks.append(f"JSON 字段「{key}」：{'✓' if passed else '✗'}")
                ok = ok and passed
        except Exception as e:
            ok = False
            checks.append(f"JSON 字段检查失败：{e}")

    min_len = spec.get("min_len")
    if min_len is not None and full and os.path.isfile(full):
        try:
            with open(full, encoding="utf-8") as f:
                data = json.load(f)
            length = len(data) if isinstance(data, (list, dict, str)) else 0
            passed = length >= int(min_len)
            checks.append(f"JSON 条目 {length} ≥ {int(min_len)}：{'✓' if passed else '✗'}")
            ok = ok and passed
        except Exception as e:
            ok = False
            checks.append(f"JSON 条目检查失败：{e}")

    result["ok"] = ok
    result["checks"] = checks
    return ok, result, "；".join(checks) if checks else "仅提交文字证据"


def complete_step(plan_id: str, step: int, status: str, evidence: str,
                  verification: Optional[Dict[str, Any]] = None,
                  verify_path: str = "") -> tuple[Optional[TaskPlan], str]:
    """原子地 load → verify → mark → save。

    同一轮里模型可能并行调用多个 task_step_done；分开 load/save 会互相覆盖，
    整段持可重入锁保证进度不丢。验收失败会转为 failed/blocked，不允许口头完成。
    """
    with _WRITE_LOCK:
        plan = load(plan_id)
        if plan is None:
            return None, "错误：任务计划不存在（plan_id 无效或计划已清理）"
        raw = str(status or "").lower()
        is_done = raw in ("done", "完成", "ok")
        check_result: Dict[str, Any] = {"ok": True, "checks": []}
        check_text = ""
        final_status = status
        final_evidence = str(evidence or "").strip()
        if is_done:
            passed, check_result, check_text = verify_evidence(final_evidence, verify_path, verification)
            if not passed:
                final_status = "failed"
                final_evidence = f"{final_evidence}\n[验收失败] {check_text}"[:500]
        ok, msg = plan.mark(step, final_status, final_evidence, check_result)
        if not ok:
            return plan, msg
        save(plan)
        suffix = f"；验收：{check_text}" if check_text else ""
        return plan, f"{msg}；进度 {plan.done_count()}/{len(plan.steps)}{suffix}"


def control(plan_id: str, action: str, step: int = 0,
            reason: str = "") -> tuple[Optional[TaskPlan], str]:
    """用户控制：pause / resume / cancel / retry / skip。"""
    act = str(action or "").strip().lower()
    with _WRITE_LOCK:
        plan = load(plan_id)
        if plan is None:
            return None, "错误：任务计划不存在"
        reason = str(reason or "").strip()[:200]
        if act == "pause":
            if plan.status in TERMINAL_STATUSES:
                return plan, f"计划已{ '完成' if plan.status == 'completed' else '取消'}，无需暂停"
            plan.status = "paused"
            plan.updated_at = _now()
            msg = "已暂停；当前步骤与证据已保留"
        elif act == "resume":
            if plan.status == "completed":
                return plan, "计划已完成，无需恢复"
            plan.status = "running"
            for s in plan.steps:
                if s.get("status") == "blocked":
                    s["status"] = "failed"
                    s["last_error"] = s.get("last_error") or "从阻塞恢复，请换方法重试"
            plan.updated_at = _now()
            msg = "已恢复；回复“继续”即可从当前步骤接着做"
        elif act == "cancel":
            plan.status = "cancelled"
            plan.updated_at = _now()
            msg = "已取消；可随时新建任务"
        elif act in ("retry", "skip"):
            target = None
            for s in plan.steps:
                if int(s.get("id") or 0) == int(step or 0):
                    target = s
                    break
            if target is None:
                return plan, f"错误：步骤 {step} 不存在"
            if act == "retry":
                target.update({
                    "status": "pending",
                    "evidence": "",
                    "verification": {},
                    "attempts": 0,
                    "last_error": "",
                    "updated_at": _now(),
                })
                if plan.status in TERMINAL_STATUSES:
                    plan.status = "running"
                elif plan.status == "blocked":
                    plan.status = "running"
                msg = f"第 {step} 步已重置，可重新执行"
            else:
                if target.get("status") == "skipped":
                    return plan, f"第 {step} 步已跳过"
                target["status"] = "skipped"
                target["evidence"] = reason or "用户跳过"
                target["updated_at"] = _now()
                plan._sync_status()
                msg = f"第 {step} 步已跳过"
        else:
            return plan, "错误：不支持的操作（pause/resume/cancel/retry/skip）"
        save(plan)
        return plan, msg


def load(plan_id: str) -> Optional[TaskPlan]:
    try:
        path = _path(plan_id)
    except ValueError:
        return None
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return TaskPlan.from_dict(data) if isinstance(data, dict) else None
    except Exception:
        return None


def list_plans(status: str = "", session_id: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    """任务中心：按更新时间倒序列出计划（status=active 表示未终止）。"""
    try:
        limit = max(1, min(int(limit or 100), 300))
    except (TypeError, ValueError):
        limit = 100
    want = str(status or "").strip().lower()
    want_session = str(session_id or "").strip()
    out = []
    try:
        names = [n for n in os.listdir(_dir()) if n.endswith(".json")]
    except OSError:
        return []
    for name in names[:1000]:
        try:
            with open(os.path.join(_dir(), name), encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            plan = TaskPlan.from_dict(data)
            if want_session and plan.session_id != want_session:
                continue
            if want == "active" and plan.status not in ACTIVE_STATUSES:
                continue
            if want and want != "active" and plan.status != want:
                continue
            out.append(plan)
        except Exception:
            continue
    out.sort(key=lambda p: p.updated_at, reverse=True)
    return [p.payload() for p in out[:limit]]


def find_incomplete(session_id: str) -> Optional[TaskPlan]:
    """同一会话最近一个未终止计划（用于断点续跑）。"""
    plans = list_plans(status="active", session_id=str(session_id or ""), limit=20)
    return load(str(plans[0].get("plan_id") or "")) if plans else None


def _heuristic_steps(goal: str) -> List[Dict[str, Any]]:
    """模型不可用时的本地拆步：只认显式顺序词/编号列表。

    限流、断网时仍能让「先…再…最后…」这类真实多步任务进入计划模式；
    没有强顺序信号的普通请求返回空列表，退化为普通对话，不挂空计划卡。
    """
    text = str(goal or "").strip()
    if not text or any(h in text for h in SIMPLE_HINTS):
        return []

    def _pack(titles: List[str]) -> List[Dict[str, Any]]:
        out = []
        for t in titles[:8]:
            t = t.strip(" \t\r\n：:，,；;。！!、")
            if len(t) >= 4:
                out.append({"id": len(out) + 1, "title": t[:120]})
        return out if len(out) >= 2 else []

    numbered = []
    for line in text.splitlines():
        m = _NUMBERED_LINE.match(line)
        if m:
            numbered.append(str(m.group(1)))
    packed = _pack(numbered)
    if packed:
        return packed

    hits = []
    for marker in _STEP_MARKERS:
        start = 0
        while True:
            pos = text.find(marker, start)
            if pos < 0:
                break
            hits.append((pos, marker))
            start = pos + len(marker)
    hits.sort()
    merged = []
    for pos, marker in hits:
        if merged and pos < merged[-1][0] + len(merged[-1][1]):
            continue
        merged.append((pos, marker))
    if len(merged) >= 2:
        segments = []
        for i, (pos, marker) in enumerate(merged):
            end = merged[i + 1][0] if i + 1 < len(merged) else len(text)
            seg = text[pos + len(marker):end]
            seg = _TRAILING_FILLER.sub("", seg).strip()
            segments.append(seg)
        packed = _pack(segments)
        if packed:
            return packed

    parts = [p for p in re.split(r"[；;]\s*", text) if p.strip()]
    return _pack(parts)


def _parse_steps(text: str, goal: str) -> List[Dict[str, Any]]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except Exception:
        return []
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list):
        return []
    out = []
    for s in steps[:8]:
        title = s.get("title") if isinstance(s, dict) else s
        title = str(title or "").strip()
        if title:
            out.append({"id": len(out) + 1, "title": title[:120]})
    return out


def create(client, goal: str, session_id: str) -> TaskPlan:
    """优先让模型拆步；限流/错误时用本地顺序词兜底，不阻断主任务。"""
    goal = str(goal or "").strip()
    parts: List[str] = []
    if not any(h in goal for h in SIMPLE_HINTS):
        try:
            client.chat(
                [
                    {"role": "system", "content": PLANNER_PROMPT},
                    {"role": "user", "content": goal[:2000]},
                ],
                tools_enabled=False,
                json_output=True,
                max_tokens=800,
                temperature=0.2,
                on_content=lambda t: parts.append(t),
            )
        except Exception as e:
            logger.warning("任务规划模型不可用，改用本地拆步：%s", e)
            parts = []
    steps = _parse_steps("".join(parts), goal) or _heuristic_steps(goal)
    return TaskPlan(
        plan_id=uuid.uuid4().hex[:12],
        session_id=str(session_id or ""),
        goal=goal[:500],
        steps=steps,
    )


def _looks_like_continuation(goal: str, prev_goal: str) -> bool:
    text = str(goal or "").strip()
    if any(h in text for h in CONTINUE_HINTS):
        return True
    if not text or not prev_goal:
        return False
    tokens = lambda s: set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", str(s).lower()))
    a, b = tokens(text), tokens(prev_goal)
    if not a or not b:
        return False
    return len(a & b) / min(len(a), len(b)) >= 0.5


def prepare(client, session_id: str, goal: str) -> Optional[TaskPlan]:
    """新任务重开；同会话有未完成计划则续跑；不需要拆步时返回 None。"""
    text = str(goal or "")
    prev = None if any(h in text for h in NEW_TASK_HINTS) else find_incomplete(session_id)
    if prev is not None and _looks_like_continuation(text, prev.goal):
        if prev.status in ("paused", "blocked"):
            prev = control(prev.plan_id, "resume")[0] or prev
        return prev
    if prev is not None:
        with _WRITE_LOCK:
            prev.status = "cancelled"
            prev.updated_at = _now()
            save(prev)
    plan = create(client, text, session_id)
    if not plan.steps:
        return None
    save(plan)
    return plan


def render_prompt(plan: TaskPlan) -> str:
    lines = [
        "[任务模式 · 长任务计划]",
        f"计划 ID：{plan.plan_id}",
        f"目标：{plan.goal}",
        "步骤：",
    ]
    for s in plan.steps:
        mark = {"done": "✓", "skipped": "↷", "failed": "✕", "blocked": "!"}.get(s.get("status"), "○")
        error = f"（失败 {s.get('attempts')} 次：{str(s.get('last_error'))[:100]}）" if s.get("last_error") else ""
        lines.append(f"{mark} {s.get('id')}. {s.get('title')}{error}")
    lines += [
        "",
        "执行规则：",
        f"1. 按顺序执行；每完成一步，必须调用 task_step_done(plan_id=\"{plan.plan_id}\", step=N, "
        "evidence=\"可核验的证据\", verify_path=\"可选产物路径\", verification={可选结构化验收})。",
        "2. 没有调用 task_step_done 的步骤不算完成，不要提前声称全部完成。",
        "3. 某步失败时调用 task_step_done(status=\"failed\", evidence=\"失败原因\")；同一错误最多自动重试 "
        f"{MAX_STEP_ATTEMPTS} 次，仍失败会阻塞并交还用户。",
        "4. 完成后汇总产物与验收证据；被用户暂停/取消的计划不得继续执行。",
    ]
    return "\n".join(lines)


def pending_steps(plan: TaskPlan) -> List[Dict[str, Any]]:
    return [s for s in plan.steps if s.get("status") in STEP_ACTIVE_STATUSES]


def current_hint(plan: Optional[TaskPlan]) -> str:
    """每轮工具循环注入的短提醒：始终指向当前必须完成的那一步。"""
    if plan is None or plan.status not in ACTIVE_STATUSES:
        return ""
    left = pending_steps(plan)
    if not left:
        return ""
    step = left[0]
    extra = ""
    if step.get("status") in ("failed", "blocked"):
        extra = (
            f"上一步失败 {step.get('attempts')} 次：{str(step.get('last_error'))[:120]}。"
            "换一种方法后再提交验收证据，不要重复同样的失败路径。"
        )
    return (
        f"[长任务进度 {plan.done_count()}/{len(plan.steps)}] "
        f"当前必须执行第 {step.get('id')} 步：{step.get('title')}。{extra}"
        f"完成后立刻调用 task_step_done(plan_id=\"{plan.plan_id}\", "
        f"step={step.get('id')}, evidence=\"可核验证据\")。"
        f"还剩 {len(left)} 步；未全部验收前不要结束回复。"
    )


def archive_text(plan: TaskPlan) -> str:
    """任务完成后写入长期记忆的文本：目标 + 每步验收证据。"""
    lines = [
        f"[任务归档] 计划 {plan.plan_id}：{plan.goal}",
        f"状态：已完成，共 {len(plan.steps)} 步；完成时间：{_now()}。",
        "步骤与验收证据：",
    ]
    for s in plan.steps:
        ev = str(s.get("evidence") or "无证据").replace("\n", " ")
        v = s.get("verification") or {}
        tag = "已结构化验收" if v.get("ok") else "文字证据"
        lines.append(f"{s.get('id')}. {s.get('title')} → [{tag}] {ev[:220]}")
    return "\n".join(lines)


def resume_prompt(plan: TaskPlan) -> str:
    """模型提前收尾时，自动续跑用的用户消息。"""
    lines = [
        f"[长任务自动续跑] 计划 {plan.plan_id} 还有 {len(pending_steps(plan))} 步未验收：",
    ]
    for s in pending_steps(plan)[:8]:
        err = f"（上次失败：{str(s.get('last_error'))[:120]}）" if s.get("last_error") else ""
        lines.append(f"- 第 {s.get('id')} 步：{s.get('title')}{err}")
    lines += [
        "",
        "请直接执行上述未完成步骤，每步调用 task_step_done 提交证据；"
        "失败的步骤要换方法，不要重复同样路径。全部完成后再汇总。不要重复已完成的步骤。",
    ]
    return "\n".join(lines)
