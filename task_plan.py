"""长任务计划（P2 垂直切片）：拆步、逐步验收、断点续跑。

设计边界：
- 计划文件落在 DATA_DIR/task_plans/<plan_id>.json，跨重启保留；
- 规划失败不阻断任务：限流/断网时按显式顺序词本地拆步，无强信号则普通回复；
- 步骤只有调用 task_step_done 并给出 evidence 才算完成，避免模型口头宣布完成；
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

PLANNER_PROMPT = (
    "你是任务规划器。只有目标需要多步执行/多次工具调用时才拆步，拆成 3-8 个可验收步骤，每步一句话，按执行顺序。"
    "如果目标只需一次回答、简单查询，或用户明确要求不要调用工具，输出空计划。"
    "步骤要具体到能判断是否完成；不要写“继续处理”这类空话。"
    "只输出 JSON：需要拆步时 {\"steps\":[{\"title\":\"...\"}]}；不需要时 {\"steps\":[]}"
)

NEW_TASK_HINTS = ("新任务", "重新开始", "换个任务", "重开一个任务")
CONTINUE_HINTS = ("继续", "接着", "然后", "下一步", "剩下的", "没做完", "未完成")
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


@dataclass
class TaskPlan:
    plan_id: str
    session_id: str
    goal: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "running"  # running / completed / cancelled
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _now()
        if not self.updated_at:
            self.updated_at = self.created_at
        for i, s in enumerate(self.steps, 1):
            s.setdefault("id", i)
            s.setdefault("status", "pending")
            s.setdefault("evidence", "")

    # -- 查询 --
    def done_count(self) -> int:
        return sum(1 for s in self.steps if s.get("status") == "done")

    def failed_count(self) -> int:
        return sum(1 for s in self.steps if s.get("status") == "failed")

    def all_done(self) -> bool:
        return bool(self.steps) and all(s.get("status") == "done" for s in self.steps)

    def payload(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "goal": self.goal,
            "status": self.status,
            "steps": [
                {
                    "id": s.get("id"),
                    "title": s.get("title", ""),
                    "status": s.get("status", "pending"),
                    "evidence": s.get("evidence", ""),
                }
                for s in self.steps
            ],
            "done": self.done_count(),
            "total": len(self.steps),
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
    def mark(self, step: int, status: str, evidence: str) -> tuple[bool, str]:
        status = "done" if str(status or "").lower() in ("done", "完成", "ok") else "failed"
        target = None
        for s in self.steps:
            if int(s.get("id") or 0) == int(step):
                target = s
                break
        if target is None:
            return False, f"错误：步骤 {step} 不存在（共 {len(self.steps)} 步）"
        target["status"] = status
        target["evidence"] = str(evidence or "").strip()
        self.updated_at = _now()
        if self.all_done():
            self.status = "completed"
        return True, f"步骤 {step} 已标记为{'完成' if status == 'done' else '失败'}"


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


def complete_step(plan_id: str, step: int, status: str,
                  evidence: str) -> tuple[Optional[TaskPlan], str]:
    """原子地 load → mark → save。

    同一轮里模型可能并行调用多个 task_step_done；分开 load/save 会互相覆盖
    （实测 6 步只记成 3 步）。整段持可重入锁，保证进度不丢。
    """
    with _WRITE_LOCK:
        plan = load(plan_id)
        if plan is None:
            return None, "错误：任务计划不存在（plan_id 无效或计划已清理）"
        ok, msg = plan.mark(step, status, evidence)
        if not ok:
            return plan, msg
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


def find_incomplete(session_id: str) -> Optional[TaskPlan]:
    """同一会话最近一个未完成计划（用于断点续跑）。"""
    latest = None
    latest_ts = ""
    try:
        names = [n for n in os.listdir(_dir()) if n.endswith(".json")]
    except OSError:
        return None
    for name in names[:500]:
        p = os.path.join(_dir(), name)
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            if str(data.get("session_id") or "") != str(session_id):
                continue
            if str(data.get("status") or "") != "running":
                continue
            ts = str(data.get("updated_at") or "")
            if latest is None or ts >= latest_ts:
                latest = TaskPlan.from_dict(data)
                latest_ts = ts
        except Exception:
            continue
    return latest


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

    # 1) 用户直接给出编号清单：最可靠
    numbered = []
    for line in text.splitlines():
        m = _NUMBERED_LINE.match(line)
        if m:
            numbered.append(str(m.group(1)))
    packed = _pack(numbered)
    if packed:
        return packed

    # 2) 显式的“先/再/最后”顺序词：按位置切段
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
    merged = []  # 去掉重叠命中，例如“首先”里的“先”
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

    # 3) 分号并列的多动作
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
        # 规划失败不硬造计划：退回普通任务模式，避免给简单请求挂一堆未完成步骤
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
            # 429/断网不应让长任务退化成普通闲聊；本地兜底照常拆步
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
    """判断新消息是在续跑旧计划，还是同会话里的另一个新任务。"""
    text = str(goal or "").strip()
    if any(h in text for h in CONTINUE_HINTS):
        return True
    if not text or not prev_goal:
        return False
    tokens = lambda s: set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", str(s).lower()))
    a, b = tokens(text), tokens(prev_goal)
    if not a or not b:
        return False
    # 主题词有明显重叠才认为是接着做；否则取消旧计划，按新任务重新规划
    return len(a & b) / min(len(a), len(b)) >= 0.5


def prepare(client, session_id: str, goal: str) -> Optional[TaskPlan]:
    """新任务重开；同会话有未完成计划则续跑；不需要拆步时返回 None。"""
    text = str(goal or "")
    prev = None if any(h in text for h in NEW_TASK_HINTS) else find_incomplete(session_id)
    if prev is not None and _looks_like_continuation(text, prev.goal):
        return prev
    if prev is not None:
        # 同会话的新任务：旧计划不再可续跑，避免下一次「继续」又串回去
        prev.status = "cancelled"
        try:
            save(prev)
        except Exception as e:
            logger.warning("取消旧任务计划失败：%s", e)
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
        mark = {"done": "✓", "failed": "✕"}.get(s.get("status"), "○")
        lines.append(f"{mark} {s.get('id')}. {s.get('title')}")
    lines += [
        "",
        "执行规则：",
        f"1. 按顺序执行；每完成一步，必须调用 task_step_done(plan_id=\"{plan.plan_id}\", step=N, "
        "evidence=\"可核验的证据\", verify_path=\"可选产物路径\")。",
        "2. 没有调用 task_step_done 的步骤不算完成，不要提前声称全部完成。",
        "3. 某步失败时调用 task_step_done(status=\"failed\", evidence=\"失败原因\")，说明阻塞点。",
        "4. 全部步骤完成后，汇总产物与验收证据；不要只回复“已完成”。",
    ]
    return "\n".join(lines)
