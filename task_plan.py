"""长任务计划（任务模式）：拆步、验收、自愈、恢复、产物、队列、依赖图。

设计边界：
- 计划文件落在 DATA_DIR/task_plans/<plan_id>.json，跨重启保留；
- 规划失败不阻断任务：限流/断网时按显式顺序词本地拆步，无强信号则普通回复；
- 步骤只有调用 task_step_done 并给出 evidence 才算完成；结构化验收失败会转为失败/阻塞；
- P2：用户可暂停、继续、取消、单步重试或跳过；状态与证据原子落盘；
- P3-1：作业与计划双向绑定，刷新/断线可回放，限流退避，服务重启后显示可恢复任务；
- P3-2：任务开始前对工作区做轻量快照，步骤验收产物记录 hash/diff，可单文件回滚；
- P3-3：全局队列、并发上限、优先级、同文件冲突避让、会话隔离；
- P3-4：步骤依赖 DAG、并行组、策略模板、失败分支隔离与剩余步骤自动重规划。
- P4-1：任务配方——完成过的任务沉淀为可复用步骤模板，命中即直接套用，跳过重复规划；
- P4-2：证据即完成——最后一步 task_step_done 验收通过即 completed，无事后复核轮；
- P4-3：开工前避坑清单——历史失败结构化，开工时注入提示，让模型第一次就走对路。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("whaletalk")

_PLAN_DIR: Optional[str] = None
_WORKSPACE_DIR: Optional[str] = None
_WRITE_LOCK = threading.RLock()
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{4,40}$")

# 同一步自动失败达到该次数后标记 blocked，停止自动续跑，交还用户决定。
MAX_STEP_ATTEMPTS = 3
# 生成层连续异常（429/超时/断网）达到该次数后阻塞，避免无限烧 token。
MAX_RUNTIME_RETRIES = 3
QUEUE_WAIT_TIMEOUT = 600.0
_SNAPSHOT_FILE_LIMIT = 300
_SNAPSHOT_FILE_MAX = 512 * 1024
_SNAPSHOT_TOTAL_MAX = 24 * 1024 * 1024
# P4-1：配方匹配阈值（token Jaccard），命中即复用已完成任务的步骤模板，跳过重复规划。
RECIPE_MATCH_THRESHOLD = 0.55
MAX_RECIPES = 200
# P4-3：开工前注入的避坑条数上限（避免提示过长反而干扰模型）。
MAX_PITFALL_HINTS = 5
# P5-1：确定性技能——只有精确匹配的已验证配方才允许跳过模型直接重放工具轨迹。
# 阈值明显低于配方复用：宁可少命中，也不能跑错（跑错=返工，违背用户硬约束）。
SKILL_MATCH_THRESHOLD = 0.92
MIN_SKILL_SUCCESSES = 2  # 配方至少真实成功 2 次才升级为技能
# 可确定性重放的工具白名单：仅限无副作用歧义、纯本地、参数可完全复现的操作。
# 交互/审批/网络/生成类工具一律不进白名单——它们需要模型判断或用户参与。
SKILL_REPLAYABLE_TOOLS = frozenset({
    "write_file", "edit_file", "read_file", "verify_files",
    "write_excel", "write_csv",
})
MAX_SKILL_CALLS_PER_STEP = 30

ACTIVE_STATUSES = ("running", "queued", "paused", "blocked")
TERMINAL_STATUSES = ("completed", "cancelled")
STEP_ACTIVE_STATUSES = ("pending", "running", "failed", "blocked", "waiting")
READY_STATUSES = ("pending", "failed")  # blocked 需用户/自动重规划介入，不算自动可执行
DONE_STATUSES = ("done", "skipped")

# 任务队列：条件变量保护，等待槽位时不持 _WRITE_LOCK（避免与落盘互锁）。
_QUEUE_COND = threading.Condition(threading.RLock())
_QUEUE: List[Dict[str, Any]] = []
_ACTIVE: Dict[str, Dict[str, Any]] = {}

PLANNER_PROMPT = (
    "你是任务规划器。只有目标需要多步执行/多次工具调用时才拆步，拆成 3-8 个可验收步骤，每步一句话，按执行顺序。"
    "如果目标只需一次回答、简单查询，或用户明确要求不要调用工具，输出空计划。"
    "步骤要具体到能判断是否完成；不要写“继续处理”这类空话。"
    "可并行、互不依赖的步骤给同一个 parallel_group；确实要等前一步结果的，用 depends_on 写前置步骤编号（从 1 开始）。"
    "同时给出 strategy：code/docs/data/research/general 之一。"
    "只输出 JSON：{\"strategy\":\"...\",\"steps\":[{\"title\":\"...\",\"depends_on\":[],\"parallel_group\":\"\"}]}；"
    "不需要时 {\"steps\":[]}"
)

REPLAN_PROMPT = (
    "任务是长任务执行器。下面计划的前面步骤已完成，后面步骤连续失败。只重新规划尚未完成的步骤，"
    "给出 2-6 个换方法的可执行步骤，保留原有编号顺序，并说明依赖关系。"
    "只输出 JSON：{\"steps\":[{\"title\":\"...\",\"depends_on\":[],\"parallel_group\":\"\"}]}"
)

NEW_TASK_HINTS = ("新任务", "重新开始", "换个任务", "重开一个任务")
CONTINUE_HINTS = ("继续", "接着", "然后", "下一步", "剩下的", "没做完", "未完成", "恢复任务")
SIMPLE_HINTS = ("只回复", "不要调用工具", "直接回答", "一句话回答", "简单说")

_STEP_MARKERS = (
    "首先", "第一步", "第二步", "第三步", "第四步", "第五步",
    "先", "再", "然后", "接着", "最后",
)
_NUMBERED_LINE = re.compile(
    r"^\s*(?:\d+[.、)]|第[一二三四五六七八九十]+步[:：]?|步骤\s*\d+[:：]?)\s*(.+)$"
)
_TRAILING_FILLER = re.compile(r"(?:完成后告诉我|最后告诉我|即可|就好)[。！!]*$")
_PATH_HINT = re.compile(
    r"(?:[A-Za-z]:\\[^\s，,；;]+|[\w.\-]+/[\w.\-/]+)"
    r"\.(?:py|md|txt|csv|json|yaml|yml|js|jsx|ts|tsx|css|html|docx|pptx|xlsx|png|jpe?g|gif)",
    re.IGNORECASE,
)

_SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".workbuddy", "task_plans", "snapshots", "data", ".idea", ".vscode",
}


def configure(plan_dir: str, workspace_dir: str = "") -> None:
    global _PLAN_DIR, _WORKSPACE_DIR
    _PLAN_DIR = str(plan_dir)
    if workspace_dir:
        _WORKSPACE_DIR = str(workspace_dir)


def _dir() -> str:
    global _PLAN_DIR
    if not _PLAN_DIR:
        _PLAN_DIR = os.environ.get("WHALETALK_TASK_PLAN_DIR") or os.path.join(
            os.path.expanduser("~"), ".whaletalk", "task_plans"
        )
    os.makedirs(_PLAN_DIR, exist_ok=True)
    return _PLAN_DIR


def _workspace() -> str:
    global _WORKSPACE_DIR
    if not _WORKSPACE_DIR:
        _WORKSPACE_DIR = os.environ.get("WHALETALK_WORKSPACE_DIR") or os.getcwd()
        try:
            import permissions
            _WORKSPACE_DIR = str(getattr(permissions, "WORKSPACE_DIR", "") or _WORKSPACE_DIR)
        # noqa: silent-except: 权限模块不可用时回退到进程工作目录，产物仍可正常登记
        except Exception:
            pass
    return os.path.abspath(_WORKSPACE_DIR or os.getcwd())


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _norm_ids(value: Any) -> List[int]:
    if isinstance(value, (int, str)):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out: List[int] = []
    for v in value:
        try:
            n = int(v)
            if n > 0 and n not in out:
                out.append(n)
        except (TypeError, ValueError):
            continue
    return out[:20]


def _norm_step(step: Any, idx: int) -> Dict[str, Any]:
    if not isinstance(step, dict):
        step = {"title": str(step or "")}
    out = dict(step)
    out.setdefault("id", idx)
    out.setdefault("title", "")
    out.setdefault("status", "pending")
    out.setdefault("evidence", "")
    out.setdefault("verification", {})
    out.setdefault("artifacts", [])
    out.setdefault("attempts", 0)
    out.setdefault("last_error", "")
    out.setdefault("updated_at", "")
    out["depends_on"] = _norm_ids(out.get("depends_on") or [])
    out["parallel_group"] = str(out.get("parallel_group") or "")[:60]
    if not isinstance(out.get("artifacts"), list):
        out["artifacts"] = []
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
    if str(out.get("status") or "") not in STEP_ACTIVE_STATUSES + DONE_STATUSES:
        out["status"] = "pending"
    return out


def _norm_runtime(value: Any) -> Dict[str, Any]:
    src = value if isinstance(value, dict) else {}
    out = {
        "state": str(src.get("state") or "ok"),
        "kind": str(src.get("kind") or ""),
        "message": str(src.get("message") or "")[:300],
        "retries": 0,
        "next_retry_at": 0.0,
        "stream_id": str(src.get("stream_id") or ""),
    }
    try:
        out["retries"] = max(0, int(src.get("retries") or 0))
    # noqa: silent-except: 磁盘上的旧计划字段损坏时回退默认值，不阻断任务恢复
    except (TypeError, ValueError):
        pass
    try:
        out["next_retry_at"] = float(src.get("next_retry_at") or 0)
    # noqa: silent-except: 同上，退避时间戳损坏时按 0 处理
    except (TypeError, ValueError):
        pass
    return out


@dataclass
class TaskPlan:
    plan_id: str
    session_id: str
    goal: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "running"  # queued / running / paused / blocked / completed / cancelled
    created_at: str = ""
    updated_at: str = ""
    strategy: str = "general"  # code / docs / data / research / general
    priority: int = 0
    runtime: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    rollbacks: List[Dict[str, Any]] = field(default_factory=list)
    touched_paths: List[str] = field(default_factory=list)
    recipe_id: str = ""  # P4-1：复用的配方 ID（若命中）
    recipe_name: str = ""  # P4-1：复用的配方名称（展示用）
    pitfalls_applied: List[str] = field(default_factory=list)  # P4-3：本次开工套用的避坑条目 ID
    finalized: bool = False  # P4-2：收尾是否已执行（配方沉淀+记忆归档）；与 status 解耦，避免提前 completed 误判
    # P5-1：每步的真实工具调用轨迹 {step_id: [{"name","args"}]}；完成后沉淀为可执行技能，
    # 同类目标精确命中后直接重放，跳过模型调用（不做事后复核）。
    step_traces: Dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "model"  # model / deterministic（确定性重放）

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _now()
        if not self.updated_at:
            self.updated_at = self.created_at
        self.steps = [_norm_step(s, i) for i, s in enumerate(self.steps, 1)]
        self.strategy = str(self.strategy or "general")[:30]
        try:
            self.priority = int(self.priority or 0)
        except (TypeError, ValueError):
            self.priority = 0
        self.runtime = _norm_runtime(self.runtime)
        self.artifacts = [a for a in (self.artifacts or []) if isinstance(a, dict)][:200]
        self.rollbacks = [r for r in (self.rollbacks or []) if isinstance(r, dict)][:100]
        self.touched_paths = [str(p) for p in (self.touched_paths or []) if p][:200]
        self.recipe_id = str(self.recipe_id or "")[:60]
        self.recipe_name = str(self.recipe_name or "")[:120]
        self.pitfalls_applied = [str(p) for p in (self.pitfalls_applied or []) if p][:30]
        self.step_traces = self._norm_traces(self.step_traces)
        self.execution_mode = str(self.execution_mode or "model")[:20]
        if self.execution_mode not in ("model", "deterministic"):
            self.execution_mode = "model"
        self.refresh_dependencies()

    @staticmethod
    def _norm_traces(raw: Any) -> Dict[str, Any]:
        """轨迹归一化：只保留可信工具调用（白名单由 execute_skill 校验），限量防膨胀。"""
        out: Dict[str, Any] = {}
        if not isinstance(raw, dict):
            return out
        total = 0
        for step_id, calls in raw.items():
            if total >= 200:
                break
            clean = []
            for c in (calls if isinstance(calls, list) else [])[:30]:
                if not isinstance(c, dict):
                    continue
                name = str(c.get("name") or "")[:60]
                args = c.get("args") if isinstance(c.get("args"), dict) else {}
                if not name:
                    continue
                clean.append({"name": name, "args": dict(args)})
                total += 1
            if clean:
                out[str(step_id)] = clean
        return out

    # -- 查询 --
    def done_count(self) -> int:
        return sum(1 for s in self.steps if s.get("status") == "done")

    def failed_count(self) -> int:
        return sum(1 for s in self.steps if s.get("status") in ("failed", "blocked"))

    def skipped_count(self) -> int:
        return sum(1 for s in self.steps if s.get("status") == "skipped")

    def all_done(self) -> bool:
        return bool(self.steps) and all(s.get("status") in DONE_STATUSES for s in self.steps)

    def refresh_dependencies(self) -> None:
        """依赖图：前置未完成时把 pending 步骤标成 waiting；满足后恢复 pending。"""
        done_ids = {int(s.get("id") or 0) for s in self.steps if s.get("status") in DONE_STATUSES}
        for s in self.steps:
            if s.get("status") not in ("pending", "waiting"):
                continue
            deps = _norm_ids(s.get("depends_on") or [])
            unmet = [d for d in deps if d not in done_ids]
            want = "waiting" if unmet else "pending"
            if s.get("status") != want:
                s["status"] = want
                s["updated_at"] = _now()

    def ready_steps(self) -> List[Dict[str, Any]]:
        self.refresh_dependencies()
        return [s for s in self.steps if s.get("status") in READY_STATUSES]

    def pending_steps(self) -> List[Dict[str, Any]]:
        return [s for s in self.steps if s.get("status") in STEP_ACTIVE_STATUSES]

    def current_step(self) -> Optional[Dict[str, Any]]:
        left = self.ready_steps() or self.pending_steps()
        return left[0] if left else None

    def payload(self) -> Dict[str, Any]:
        cur = self.current_step() or {}
        ready = self.ready_steps()
        return {
            "plan_id": self.plan_id,
            "session_id": self.session_id,
            "goal": self.goal,
            "status": self.status,
            "strategy": self.strategy,
            "priority": self.priority,
            "runtime": dict(self.runtime),
            "steps": [
                {
                    "id": s.get("id"),
                    "title": s.get("title", ""),
                    "status": s.get("status", "pending"),
                    "depends_on": s.get("depends_on", []),
                    "parallel_group": s.get("parallel_group", ""),
                    "evidence": s.get("evidence", ""),
                    "verification": s.get("verification", {}),
                    "artifacts": [a.get("path") for a in (s.get("artifacts") or []) if isinstance(a, dict)],
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
            "ready_steps": [s.get("id") for s in ready],
            "artifacts": list(self.artifacts),
            "rollbacks": list(self.rollbacks),
            "touched_paths": list(self.touched_paths),
            "recipe_id": self.recipe_id,
            "recipe_name": self.recipe_name,
            "pitfalls_applied": list(self.pitfalls_applied),
            "finalized": bool(self.finalized),
            "execution_mode": self.execution_mode,
            "step_traces": {k: [dict(c) for c in v] for k, v in self.step_traces.items()},
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
            "strategy": self.strategy,
            "priority": self.priority,
            "runtime": self.runtime,
            "artifacts": self.artifacts,
            "rollbacks": self.rollbacks,
            "touched_paths": self.touched_paths,
            "recipe_id": self.recipe_id,
            "recipe_name": self.recipe_name,
            "pitfalls_applied": self.pitfalls_applied,
            "finalized": bool(self.finalized),
            "execution_mode": self.execution_mode,
            "step_traces": self.step_traces,
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
            strategy=str(data.get("strategy") or "general"),
            priority=data.get("priority") or 0,
            runtime=data.get("runtime") or {},
            artifacts=list(data.get("artifacts") or []),
            rollbacks=list(data.get("rollbacks") or []),
            touched_paths=list(data.get("touched_paths") or []),
            recipe_id=str(data.get("recipe_id") or ""),
            recipe_name=str(data.get("recipe_name") or ""),
            pitfalls_applied=list(data.get("pitfalls_applied") or []),
            finalized=bool(data.get("finalized", False)),
            step_traces=data.get("step_traces") or {},
            execution_mode=str(data.get("execution_mode") or "model"),
        )

    # -- 更新 --
    def _sync_status(self) -> None:
        self.refresh_dependencies()
        if self.all_done():
            self.status = "completed"
        elif any(s.get("status") == "blocked" for s in self.steps) and not self.ready_steps():
            self.status = "blocked"
        elif self.status not in ("paused", "cancelled", "queued"):
            self.status = "running"
        self.updated_at = _now()

    def mark(self, step: int, status: str, evidence: str,
             verification: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
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
            blocked_msg = (
                f"步骤 {step} 已连续失败 {attempts} 次，标记为阻塞；"
                "请在任务中心重试/跳过，或换一种方法后回复“继续”。"
            )
            if self.ready_steps():
                blocked_msg += " 其余无依赖步骤仍可继续执行。"
            return True, blocked_msg
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


def _is_text_file(path: str) -> bool:
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)
        if not chunk:
            return True
        return b"\x00" not in chunk
    except Exception:
        return False


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(65536), b""):
                h.update(block)
        return h.hexdigest()
    except Exception:
        return ""


def _relpath(path: str) -> str:
    root = _workspace()
    full = os.path.abspath(path)
    try:
        if os.path.commonpath([root, full]) != root:
            return ""
    except ValueError:
        return ""
    rel = os.path.relpath(full, root)
    return rel if rel and not rel.startswith("..") else ""


def _snapshot_root(plan_id: str) -> str:
    return os.path.join(_dir(), "snapshots", plan_id)


def _snapshot_baseline(plan_id: str) -> str:
    return os.path.join(_snapshot_root(plan_id), "baseline")


def capture_workspace_baseline(plan_id: str) -> List[Dict[str, Any]]:
    """任务开始前轻量快照工作区文本文件，供后续产物 diff / 回滚使用。"""
    root = _workspace()
    base = _snapshot_baseline(plan_id)
    os.makedirs(base, exist_ok=True)
    manifest: List[Dict[str, Any]] = []
    count = 0
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if count >= _SNAPSHOT_FILE_LIMIT or total >= _SNAPSHOT_TOTAL_MAX:
                break
            src = os.path.join(dirpath, name)
            rel = _relpath(src)
            if not rel:
                continue
            try:
                size = os.path.getsize(src)
                if size > _SNAPSHOT_FILE_MAX or size == 0:
                    continue
                if not _is_text_file(src):
                    continue
                dst = os.path.join(base, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                manifest.append({
                    "path": os.path.abspath(src),
                    "relpath": rel,
                    "size": size,
                    "sha256": _sha256_file(src),
                    "mtime": os.path.getmtime(src),
                })
                count += 1
                total += size
            except Exception:
                continue
    try:
        with open(os.path.join(_snapshot_root(plan_id), "baseline.json"), "w", encoding="utf-8") as f:
            json.dump({"created_at": _now(), "files": manifest}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("任务基线快照写入失败：%s", e)
    return manifest


def _baseline_entry(plan_id: str, rel: str) -> Optional[Dict[str, Any]]:
    if not rel:
        return None
    try:
        with open(os.path.join(_snapshot_root(plan_id), "baseline.json"), encoding="utf-8") as f:
            data = json.load(f)
        for item in data.get("files") or []:
            if isinstance(item, dict) and item.get("relpath") == rel:
                return item
    # noqa: silent-except: 基线清单缺失/损坏时该产物按「新增」登记，仍可回滚删除
    except Exception:
        pass
    return None


def _meta(path: str) -> Dict[str, Any]:
    is_file = os.path.isfile(path)
    is_dir = os.path.isdir(path)
    meta: Dict[str, Any] = {
        "path": os.path.abspath(path),
        "relpath": _relpath(path),
        "type": "dir" if is_dir else "file" if is_file else "missing",
        "size": os.path.getsize(path) if is_file else 0,
        "sha256": _sha256_file(path) if is_file else "",
        "mtime": os.path.getmtime(path) if (is_file or is_dir) else 0,
    }
    if is_file and _is_text_file(path):
        meta["text"] = _read_text(path)
    return meta


def register_artifact(plan_id: str, step: int, path: str,
                      evidence: str = "", verification: Optional[Dict[str, Any]] = None) -> Tuple[Optional[Dict[str, Any]], str]:
    """把已验收产物登记到计划：记录 hash、diff，并标记可回滚状态。"""
    full = _resolve_verify_path(path)
    if not full or not os.path.exists(full):
        return None, "产物路径不存在，未登记"
    plan = load(plan_id)
    if plan is None:
        return None, "任务计划不存在"
    rel = _relpath(full)
    after = _meta(full)
    before_entry = _baseline_entry(plan_id, rel)
    before: Dict[str, Any] = {}
    if before_entry:
        before = dict(before_entry)
        snap_file = os.path.join(_snapshot_baseline(plan_id), rel)
        if _is_text_file(snap_file):
            before["text"] = _read_text(snap_file)
    before_hash = str(before.get("sha256") or "")
    after_hash = str(after.get("sha256") or "")
    if before_hash and before_hash == after_hash:
        state = "unchanged"
    elif before_hash:
        state = "modified"
    else:
        state = "created"
    diff = ""
    if before.get("text") is not None and after.get("text") is not None:
        diff = "".join(difflib.unified_diff(
            str(before.get("text") or "").splitlines(True),
            str(after.get("text") or "").splitlines(True),
            fromfile=f"before/{rel or os.path.basename(full)}",
            tofile=f"after/{rel or os.path.basename(full)}",
            n=2,
        ))[:12000]
    artifact = {
        "path": after["path"],
        "relpath": rel,
        "step": int(step or 0),
        "evidence": str(evidence or "")[:400],
        "state": state,
        "before": {k: v for k, v in before.items() if k != "text"},
        "after": {k: v for k, v in after.items() if k != "text"},
        "diff": diff,
        "rollback_available": bool(rel),
        "verification": verification or {},
        "created_at": _now(),
    }
    with _WRITE_LOCK:
        plan = load(plan_id) or plan
        plan.artifacts = [a for a in plan.artifacts
                          if a.get("path") != artifact["path"]]
        plan.artifacts.append(artifact)
        plan.artifacts = plan.artifacts[-200:]
        if rel and rel not in plan.touched_paths:
            plan.touched_paths.append(rel)
            plan.touched_paths = plan.touched_paths[-200:]
        for s in plan.steps:
            if int(s.get("id") or 0) == int(step or 0):
                s.setdefault("artifacts", [])
                s["artifacts"] = [a for a in s["artifacts"] if a.get("path") != artifact["path"]]
                s["artifacts"].append({"path": artifact["path"], "state": state})
                s["updated_at"] = _now()
        plan.updated_at = _now()
        save(plan)
    return artifact, f"已登记产物（{state}）：{artifact['path']}"


def rollback(plan_id: str, paths: Optional[List[str]] = None,
             dry_run: bool = False) -> Tuple[Optional[TaskPlan], List[Dict[str, Any]], str]:
    """把本任务产物回滚到任务开始前：新增则删除，修改则从基线快照恢复。"""
    plan = load(plan_id)
    if plan is None:
        return None, [], "任务计划不存在"
    wanted = {str(p) for p in (paths or []) if p}
    results: List[Dict[str, Any]] = []
    with _WRITE_LOCK:
        plan = load(plan_id) or plan
        for art in list(plan.artifacts):
            rel = str(art.get("relpath") or "")
            if wanted and art.get("path") not in wanted and rel not in wanted:
                continue
            if not rel or not art.get("rollback_available"):
                results.append({"path": art.get("path"), "ok": False, "reason": "不在工作区基线内，不能安全回滚"})
                continue
            target = _resolve_verify_path(art.get("path") or "")
            expect = str((art.get("after") or {}).get("sha256") or "")
            current = _sha256_file(target) if os.path.isfile(target) else ""
            if expect and current and current != expect:
                results.append({"path": target, "ok": False, "reason": "文件已被后续修改，跳过以避免覆盖"})
                continue
            before = art.get("before") or {}
            if dry_run:
                results.append({"path": target, "ok": True,
                                "action": "restore" if before else "delete", "dry_run": True})
                continue
            try:
                if before:
                    src = os.path.join(_snapshot_baseline(plan_id), rel)
                    if not os.path.isfile(src):
                        results.append({"path": target, "ok": False, "reason": "基线快照缺失"})
                        continue
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copy2(src, target)
                    results.append({"path": target, "ok": True, "action": "restore"})
                else:
                    if os.path.isfile(target):
                        os.remove(target)
                    results.append({"path": target, "ok": True, "action": "delete"})
            except Exception as e:
                results.append({"path": target, "ok": False, "reason": str(e)[:200]})
        if not dry_run:
            ok_n = sum(1 for r in results if r.get("ok"))
            plan.rollbacks.append({"at": _now(), "results": results[:50],
                                   "summary": f"{ok_n}/{len(results)} 成功"})
            plan.rollbacks = plan.rollbacks[-50:]
            plan.updated_at = _now()
            save(plan)
    msg = f"回滚完成：{sum(1 for r in results if r.get('ok'))}/{len(results)} 个产物已处理"
    return plan, results, msg


def verify_evidence(evidence: str, verify_path: str = "",
                    spec: Optional[Dict[str, Any]] = None) -> Tuple[bool, Dict[str, Any], str]:
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
                  verify_path: str = "") -> Tuple[Optional[TaskPlan], str]:
    """原子地 load → verify → mark → 登记产物 → save。"""
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
        # P4-3：步骤失败即记录避坑经验，供同类任务开工前规避（不做事后复核）
        if str(final_status).lower() not in ("done", "完成", "ok"):
            try:
                failed_step = next((s for s in plan.steps if int(s.get("id") or 0) == int(step)), None)
                if failed_step is not None:
                    record_pitfall(plan, failed_step)
            except Exception as e:
                logger.warning("避坑经验记录失败（不影响步骤验收）：%s", e)
        save(plan)
        suffix = f"；验收：{check_text}" if check_text else ""
        art_msg = ""
        artifact_path = str(verify_path or (verification or {}).get("path") or "") if isinstance(verification, dict) else str(verify_path or "")
        if is_done and check_result.get("ok") and artifact_path:
            try:
                _art, art_note = register_artifact(plan_id, step, artifact_path, final_evidence, check_result)
                if art_note:
                    art_msg = f"；{art_note}"
                    plan = load(plan_id) or plan
            except Exception as e:
                logger.warning("产物登记失败（不影响步骤验收）：%s", e)
        return plan, f"{msg}；进度 {plan.done_count()}/{len(plan.steps)}{suffix}{art_msg}"


def control(plan_id: str, action: str, step: int = 0,
            reason: str = "") -> Tuple[Optional[TaskPlan], str]:
    """用户控制：pause / resume / cancel / retry / skip / replan。"""
    act = str(action or "").strip().lower()
    with _WRITE_LOCK:
        plan = load(plan_id)
        if plan is None:
            return None, "错误：任务计划不存在"
        reason = str(reason or "").strip()[:200]
        if act == "pause":
            if plan.status in TERMINAL_STATUSES:
                return plan, f"计划已{'完成' if plan.status == 'completed' else '取消'}，无需暂停"
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
            plan.runtime = _norm_runtime({"stream_id": plan.runtime.get("stream_id", "")})
            plan.updated_at = _now()
            msg = "已恢复；回复“继续”即可从当前步骤接着做"
        elif act == "replan":
            if plan.status in TERMINAL_STATUSES:
                return plan, "计划已终止，无法重新规划"
            plan.status = "running"
            for s in plan.steps:
                if s.get("status") == "blocked":
                    s["status"] = "failed"
            plan.runtime = _norm_runtime({"stream_id": plan.runtime.get("stream_id", "")})
            plan.updated_at = _now()
            msg = "已解除阻塞；下次续跑会尝试重新规划剩余步骤"
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
                if plan.status in TERMINAL_STATUSES or plan.status == "blocked":
                    plan.status = "running"
                msg = f"第 {step} 步已重置，可重新执行"
            else:
                if target.get("status") == "skipped":
                    return plan, f"第 {step} 步已跳过"
                target["status"] = "skipped"
                target["evidence"] = reason or "用户跳过"
                target["updated_at"] = _now()
                msg = f"第 {step} 步已跳过"
            plan._sync_status()
        else:
            return plan, "错误：不支持的操作（pause/resume/cancel/retry/skip/replan）"
        save(plan)
        return plan, msg


# ── P3-1：作业绑定、限流退避、自动恢复 ──────────────────

def bind_stream(plan_id: str, stream_id: str) -> None:
    with _WRITE_LOCK:
        plan = load(plan_id)
        if plan is None:
            return
        plan.runtime["stream_id"] = str(stream_id or "")
        plan.updated_at = _now()
        save(plan)


def note_runtime_error(plan_id: str, kind: str, message: str) -> Tuple[Optional[TaskPlan], float, bool]:
    """记录一次生成层异常；返回（计划, 建议等待秒数, 是否已阻塞）。"""
    with _WRITE_LOCK:
        plan = load(plan_id)
        if plan is None:
            return None, 0.0, False
        rt = plan.runtime
        retries = int(rt.get("retries") or 0) + 1
        wait = float(min(2 ** retries, 30))
        rt.update({
            "state": "waiting" if retries < MAX_RUNTIME_RETRIES else "error",
            "kind": str(kind or "runtime")[:40],
            "message": str(message or "")[:300],
            "retries": retries,
            "next_retry_at": time.time() + wait,
        })
        blocked = retries >= MAX_RUNTIME_RETRIES
        if blocked:
            plan.status = "blocked"
            for s in plan.ready_steps()[:1]:
                s["last_error"] = str(message or "生成异常")[:300]
                s["updated_at"] = _now()
        plan.updated_at = _now()
        save(plan)
        return plan, wait if not blocked else 0.0, blocked


def clear_runtime_error(plan_id: str) -> None:
    with _WRITE_LOCK:
        plan = load(plan_id)
        if plan is None:
            return
        rt = plan.runtime
        if rt.get("state") == "ok" and not rt.get("retries"):
            return
        plan.runtime = _norm_runtime({"stream_id": rt.get("stream_id", "")})
        plan.updated_at = _now()
        save(plan)


def runtime_wait_seconds(plan_id: str) -> float:
    plan = load(plan_id)
    if plan is None:
        return 0.0
    return max(0.0, float(plan.runtime.get("next_retry_at") or 0) - time.time())


# ── P3-3：任务队列 / 并发 / 冲突避让 ────────────────────

def max_concurrent() -> int:
    try:
        return max(1, min(int(os.environ.get("WHALETALK_TASK_CONCURRENCY") or 2), 16))
    except (TypeError, ValueError):
        return 2


def priority_from_goal(goal: str) -> int:
    text = str(goal or "")
    if any(k in text for k in ("紧急", "优先", "尽快", "P0")):
        return 2
    if any(k in text for k in ("低优先级", "有空再", "不急")):
        return -1
    return 0


def _path_hints(goal: str) -> List[str]:
    out = []
    for m in _PATH_HINT.finditer(str(goal or "")):
        val = m.group(0).replace("\\", "/").lower()
        if val not in out:
            out.append(val)
    return out[:20]


def _plan_paths(plan: TaskPlan) -> List[str]:
    out = list(plan.touched_paths or [])
    for p in _path_hints(plan.goal):
        if p not in out:
            out.append(p)
    return out[:50]


def enqueue(plan_id: str, priority: Optional[int] = None) -> int:
    plan = load(plan_id)
    if plan is None:
        return 0
    with _QUEUE_COND:
        for i, item in enumerate(_QUEUE):
            if item.get("plan_id") == plan_id:
                return i + 1
        pr = plan.priority if priority is None else int(priority or 0)
        _QUEUE.append({
            "plan_id": plan_id,
            "session_id": plan.session_id,
            "priority": pr,
            "created_at": plan.created_at,
            "paths": _plan_paths(plan),
        })
        _QUEUE.sort(key=lambda x: (-int(x.get("priority") or 0), str(x.get("created_at") or "")))
        for i, item in enumerate(_QUEUE):
            if item.get("plan_id") == plan_id:
                return i + 1
    return 1


def dequeue(plan_id: str) -> bool:
    with _QUEUE_COND:
        before = len(_QUEUE)
        _QUEUE[:] = [x for x in _QUEUE if x.get("plan_id") != plan_id]
        changed = len(_QUEUE) != before
        if changed:
            _QUEUE_COND.notify_all()
        return changed


def queue_position(plan_id: str) -> int:
    with _QUEUE_COND:
        for i, item in enumerate(_QUEUE):
            if item.get("plan_id") == plan_id:
                return i + 1
    return 0


def queue_snapshot() -> List[Dict[str, Any]]:
    with _QUEUE_COND:
        return [dict(x) for x in _QUEUE]


def release_slot(plan_id: str) -> None:
    with _QUEUE_COND:
        _ACTIVE.pop(plan_id, None)
        _QUEUE_COND.notify_all()


def _overlap(a: List[str], b: List[str]) -> bool:
    sa, sb = {str(x).lower() for x in a}, {str(x).lower() for x in b}
    if not sa or not sb:
        return False
    if sa & sb:
        return True
    return any(x in y or y in x for x in sa for y in sb if len(x) >= 6 and len(y) >= 6)


def wait_for_slot(plan_id: str, stop_event: Any = None,
                  timeout: float = QUEUE_WAIT_TIMEOUT) -> bool:
    """等待并发槽位/文件锁。超时或用户停止时自动出队。"""
    plan = load(plan_id)
    if plan is None:
        return False
    if queue_position(plan_id) == 0:
        enqueue(plan_id)
    deadline = time.time() + max(1.0, float(timeout or QUEUE_WAIT_TIMEOUT))
    timed_out = False
    with _QUEUE_COND:
        while True:
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                break
            current = next((x for x in _QUEUE if x.get("plan_id") == plan_id), None)
            first = bool(_QUEUE and _QUEUE[0].get("plan_id") == plan_id)
            paths = current.get("paths") if current else _plan_paths(plan)
            conflict = any(
                _overlap(paths, state.get("paths") or [])
                for pid, state in _ACTIVE.items() if pid != plan_id
            )
            same_session = any(
                state.get("session_id") == plan.session_id
                for pid, state in _ACTIVE.items() if pid != plan_id
            )
            if first and not conflict and not same_session and len(_ACTIVE) < max_concurrent():
                dequeue(plan_id)
                _ACTIVE[plan_id] = {
                    "session_id": plan.session_id,
                    "paths": paths,
                    "acquired_at": time.time(),
                }
                plan = load(plan_id) or plan
                if plan.status != "cancelled":
                    plan.status = "running"
                    plan.updated_at = _now()
                    save(plan)
                _QUEUE_COND.notify_all()
                return True
            left = deadline - time.time()
            if left <= 0:
                timed_out = True
                break
            _QUEUE_COND.wait(min(0.5, left))
    dequeue(plan_id)
    if timed_out:
        try:
            note_runtime_error(plan_id, "queue_timeout", "等待执行槽位超时，计划已保留在任务中心")
        except Exception:
            logger.exception("队列超时状态写入失败")
    return False


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


# ── P4：配方复用 + 避坑经验 + 证据即完成 ─────────────

def _goal_tokens(goal: str) -> set:
    """从目标抽取中文/英文词元，用于配方与避坑的相似度匹配。"""
    return set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", str(goal or "").lower()))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(len(a | b))


def _recipes_path() -> str:
    return os.path.join(_dir(), "recipes", "recipes.json")


def _pitfalls_path() -> str:
    return os.path.join(_dir(), "experience", "pitfalls.json")


def _load_store(path: str) -> List[Dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return [x for x in (data or []) if isinstance(x, dict)] if isinstance(data, list) else []
    # noqa: silent-except: 配方/经验库首次不存在或损坏时按空库处理，不阻断任务
    except Exception:
        return []


def _save_store(path: str, rows: List[Dict[str, Any]]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rows[:MAX_RECIPES * 10], f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _step_templates(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        item = {
            "title": str(s.get("title") or "")[:120],
            "depends_on": _norm_ids(s.get("depends_on") or []),
            "parallel_group": str(s.get("parallel_group") or "")[:60],
        }
        if item["title"]:
            out.append(item)
    return out[:12]


def save_recipe(plan: TaskPlan) -> Tuple[Optional[Dict[str, Any]], str]:
    """任务完成后沉淀配方：同名目标的步骤模板 + 成功证据，下次同类任务直接复用。"""
    if plan is None or not plan.all_done():
        return None, "任务未完成，不沉淀配方"
    tokens = sorted(_goal_tokens(plan.goal))
    if len(tokens) < 2:
        return None, "目标信息过少，不沉淀配方"
    with _WRITE_LOCK:
        rows = _load_store(_recipes_path())
        hit = None
        best = 0.0
        for row in rows:
            score = _jaccard(set(tokens), set(row.get("tokens") or []))
            if score > best:
                best, hit = score, row
        now = _now()
        if hit is not None and best >= RECIPE_MATCH_THRESHOLD:
            # 同类任务再次成功：强化这份配方（更新步骤模板 + 成功次数）
            hit["steps"] = _step_templates(plan.steps)
            hit["strategy"] = plan.strategy
            if plan.step_traces:
                hit["traces"] = {k: [dict(c) for c in v] for k, v in plan.step_traces.items()}
            hit["touched_paths"] = list(plan.touched_paths)[:50]
            hit["successes"] = int(hit.get("successes") or 0) + 1
            hit["source_plan_id"] = plan.plan_id
            hit["updated_at"] = now
            recipe = hit
        else:
            recipe = {
                "recipe_id": uuid.uuid4().hex[:10],
                "name": str(plan.goal)[:60],
                "tokens": tokens[:40],
                "strategy": plan.strategy,
                "steps": _step_templates(plan.steps),
                "traces": {k: [dict(c) for c in v] for k, v in (plan.step_traces or {}).items()},
                "touched_paths": list(plan.touched_paths)[:50],
                "source_plan_id": plan.plan_id,
                "uses": 0,
                "successes": 1,
                "created_at": now,
                "updated_at": now,
            }
            rows.append(recipe)
        rows = sorted(rows, key=lambda r: (int(r.get("successes") or 0), int(r.get("uses") or 0)), reverse=True)[:MAX_RECIPES]
        _save_store(_recipes_path(), rows)
        return recipe, f"已沉淀配方：{recipe.get('name', '')}"


def list_recipes(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        limit = max(1, min(int(limit or 50), MAX_RECIPES))
    except (TypeError, ValueError):
        limit = 50
    rows = _load_store(_recipes_path())
    rows.sort(key=lambda r: (int(r.get("successes") or 0), int(r.get("uses") or 0)), reverse=True)
    return rows[:limit]


def delete_recipe(recipe_id: str) -> bool:
    rid = str(recipe_id or "")
    with _WRITE_LOCK:
        rows = _load_store(_recipes_path())
        kept = [r for r in rows if str(r.get("recipe_id") or "") != rid]
        if len(kept) == len(rows):
            return False
        _save_store(_recipes_path(), kept)
        return True


def match_recipe(goal: str) -> Optional[Dict[str, Any]]:
    """命中历史配方则直接套用已验证步骤模板，跳过重复的模型规划调用。"""
    tokens = _goal_tokens(goal)
    if len(tokens) < 2:
        return None
    with _WRITE_LOCK:
        rows = _load_store(_recipes_path())
        best, hit = 0.0, None
        for row in rows:
            if not row.get("steps"):
                continue
            score = _jaccard(tokens, set(row.get("tokens") or []))
            if score > best:
                best, hit = score, row
        if hit is None or best < RECIPE_MATCH_THRESHOLD:
            return None
        hit["uses"] = int(hit.get("uses") or 0) + 1
        hit["updated_at"] = _now()
        _save_store(_recipes_path(), rows)
        return dict(hit)


# ── P5-1：确定性技能（工具轨迹沉淀 + 零模型重放）────────────────────────
# 每步的工具调用先累积在 buffer，遇到 task_step_done(step=N) 时归到步骤 N。
_SKILL_BUFFERS: Dict[str, List[Dict[str, Any]]] = {}
_SKILL_BUF_LOCK = threading.RLock()


def buffer_tool_call(plan_id: str, name: str, args: Any) -> None:
    """由 SSE on_tool 回调收集一次真实工具调用，等待归属到当前验收步骤。"""
    if not plan_id or name in ("task_step_done",):
        return
    if name not in SKILL_REPLAYABLE_TOOLS:
        return  # 交互/网络/生成类工具不入轨迹，避免重放时产生副作用或依赖模型判断
    clean = dict(args) if isinstance(args, dict) else {}
    with _SKILL_BUF_LOCK:
        buf = _SKILL_BUFFERS.setdefault(str(plan_id), [])
        if len(buf) < MAX_SKILL_CALLS_PER_STEP * 8:
            buf.append({"name": str(name), "args": clean})


def flush_tool_calls(plan_id: str, step: int) -> None:
    """把累积的调用归到指定步骤（task_step_done 触发时调用）。"""
    pid = str(plan_id or "")
    with _SKILL_BUF_LOCK:
        calls = _SKILL_BUFFERS.pop(pid, [])
    if not calls or not pid:
        return
    with _WRITE_LOCK:
        plan = load(pid)
        if plan is None:
            return
        traces = dict(plan.step_traces or {})
        merged = list(traces.get(str(step), [])) + calls
        traces[str(step)] = merged[-MAX_SKILL_CALLS_PER_STEP:]
        plan.step_traces = traces
        save(plan)


def drop_tool_buffer(plan_id: str) -> None:
    with _SKILL_BUF_LOCK:
        _SKILL_BUFFERS.pop(str(plan_id or ""), None)


def match_skill(goal: str) -> Optional[Dict[str, Any]]:
    """精确匹配已验证 2 次以上的技能配方；命中才允许跳过模型直接重放。

    比配方复用更严格：必须带完整 traces 且 successes 达标，宁可不命中也不跑错。
    """
    tokens = _goal_tokens(goal)
    if len(tokens) < 2:
        return None
    with _WRITE_LOCK:
        rows = _load_store(_recipes_path())
        best, hit = 0.0, None
        for row in rows:
            if not row.get("traces"):
                continue
            if int(row.get("successes") or 0) < MIN_SKILL_SUCCESSES:
                continue
            score = _jaccard(tokens, set(row.get("tokens") or []))
            if score > best:
                best, hit = score, row
        if hit is None or best < SKILL_MATCH_THRESHOLD:
            return None
        return dict(hit)


def _skill_trace_for_step(recipe: Dict[str, Any], step: int,
                          titles: List[str]) -> List[Dict[str, Any]]:
    """按步骤标题对齐配方轨迹：优先按标题精确匹配，退回同序号（步骤模板顺序稳定）。"""
    traces = recipe.get("traces") or {}
    if not isinstance(traces, dict):
        return []
    key = None
    if 0 <= step - 1 < len(titles):
        title = str(titles[step - 1] or "").strip()
        for k, v in traces.items():
            if str(k).strip() == title:
                key = k
                break
    if key is None:
        # 序号回退：配方 traces 的 key 允许是 "1"/"2" 数字字符串
        if str(step) in traces:
            key = str(step)
    if key is None:
        return []
    out = []
    for c in (traces.get(key) or []):
        if not isinstance(c, dict):
            continue
        name = str(c.get("name") or "")
        if name not in SKILL_REPLAYABLE_TOOLS:
            return []  # 含不可重放工具 → 整个技能作废，交给模型
        out.append({"name": name, "args": dict(c.get("args") or {})})
    return out


def _ordered_skill_calls(recipe: Dict[str, Any]) -> List[Dict[str, Any]]:
    """把配方轨迹按步骤号排序后展平成全局有序调用列表。

    模型可能在一轮里并行调用多个工具 + 多个 task_step_done，导致按步骤归属时
    buffer 被首个验收清空（轨迹只剩部分步骤）。全局有序重放规避这种错配。
    """
    traces = recipe.get("traces") or {}
    if not isinstance(traces, dict):
        return []
    def _sk(k):
        try:
            return int(k)
        except (TypeError, ValueError):
            return 9999
    out: List[Dict[str, Any]] = []
    for key in sorted(traces.keys(), key=_sk):
        for c in (traces.get(key) or []):
            if not isinstance(c, dict):
                continue
            name = str(c.get("name") or "")
            if name not in SKILL_REPLAYABLE_TOOLS:
                return []  # 含不可重放工具 → 整段作废
            out.append({"name": name, "args": dict(c.get("args") or {})})
    return out[:MAX_SKILL_CALLS_PER_STEP * 8]


def execute_skill(plan: TaskPlan, recipe: Dict[str, Any], emit=None) -> Tuple[bool, str]:
    """确定性重放技能：不调用模型，按全局有序轨迹重放工具并逐步验收。

    参数已随配方持久化为字面量，命中要求目标 token Jaccard ≥ SKILL_MATCH_THRESHOLD，
    因此这些参数对新目标同样有效。任一前置校验或执行失败 → (False, 原因)，
    由上层降级回模型路径；绝不假装完成、绝不做事后复核（用户硬约束）。
    """
    import toolkit as _tk  # 延迟导入：避免与工具注册建立顺序耦合
    # 防御：确保内置工具已注册（单独调用本模块、未走 api_server 导入链时也能重放）。
    if _tk.executor_for("write_file") is None:
        try:
            import deepseek_client  # noqa: F401  触发工具注册链
            import agent_tools  # noqa: F401
        except Exception as e:
            logger.warning("确定性技能：工具注册链加载失败：%s", e)

    calls = _ordered_skill_calls(recipe)
    if not calls:
        return False, "配方无可重放轨迹（或含不可重放工具），降级模型执行"
    emit = emit or (lambda _ev, _data: None)
    paths: List[str] = []
    last_result = ""
    workspace = _workspace()
    ws_abs = os.path.abspath(workspace) if workspace else ""
    for c in calls:
        name, args = c["name"], dict(c["args"])
        fn = _tk.executor_for(name)
        if fn is None:
            return False, f"轨迹工具 {name} 当前不可用，降级模型执行"
        # 路径护栏：必须在工作区内，防止旧技能写到环境外
        p = str(args.get("path") or "")
        if p:
            abs_p = p if os.path.isabs(p) else os.path.join(workspace, p)
            abs_p = os.path.abspath(abs_p)
            if ws_abs and not (abs_p.startswith(ws_abs + os.sep) or abs_p == ws_abs):
                return False, f"轨迹路径越界（{p}），降级模型执行"
            paths.append(p)
        emit("tool_start", {"name": name, "args": args})
        try:
            result = fn(**args)
        except Exception as e:
            return False, f"轨迹工具 {name} 执行异常：{e}；降级模型执行"
        if not isinstance(result, str):
            try:
                result = json.dumps(result, ensure_ascii=False, default=str)
            except Exception:
                result = str(result)
        last_result = str(result)
        emit("tool", {"name": name, "args": args, "result": last_result[:2000]})

    # 全局重放完成 → 按步骤依次验收。技能是已验证配方，产物与参数对新目标一致，
    # 这里直接以重放结果登记完成，不追加任何复核轮。
    steps = [s for s in (plan.steps or []) if s.get("status") not in DONE_STATUSES]
    for i, step in enumerate(steps):
        sid = int(step.get("id") or 0)
        vpath = paths[i] if i < len(paths) else (paths[0] if paths else "")
        evidence = f"[确定性技能] 全局重放 {len(calls)} 个已验证工具调用；输出：{last_result[:160]}"
        complete_step(plan.plan_id, sid, "done", evidence, verify_path=vpath)
        fresh = load(plan.plan_id)
        step_now = next((s for s in (fresh.steps if fresh else []) if int(s.get("id") or 0) == sid), None)
        if not step_now or step_now.get("status") != "done":
            return False, f"步骤 {sid} 确定性验收未通过，降级模型执行"
    return True, f"确定性技能执行完成：重放 {len(calls)} 个工具调用，零模型调用"


def record_pitfall(plan: TaskPlan, step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """步骤失败/阻塞时记录一条避坑经验：目标签名 + 失败步骤 + 原因。"""
    if plan is None or not isinstance(step, dict):
        return None
    reason = str(step.get("last_error") or step.get("evidence") or "").strip()
    if not reason:
        return None
    tokens = sorted(_goal_tokens(plan.goal))
    if len(tokens) < 2:
        return None
    with _WRITE_LOCK:
        rows = _load_store(_pitfalls_path())
        title = str(step.get("title") or "")[:120]
        hit = None
        best = 0.0
        for row in rows:
            score = _jaccard(set(tokens), set(row.get("tokens") or []))
            if score > best:
                best, hit = score, row
        if hit is not None and best >= RECIPE_MATCH_THRESHOLD and str(hit.get("step_title") or "") == title:
            hit["occurrences"] = int(hit.get("occurrences") or 0) + 1
            hit["reason"] = reason[:300]
            hit["updated_at"] = _now()
            pitfall = hit
        else:
            pitfall = {
                "pitfall_id": uuid.uuid4().hex[:10],
                "tokens": tokens[:40],
                "strategy": plan.strategy,
                "step_title": title,
                "reason": reason[:300],
                "avoid": "",
                "occurrences": 1,
                "created_at": _now(),
                "updated_at": _now(),
            }
            rows.append(pitfall)
        rows = rows[-MAX_RECIPES * 5:]
        _save_store(_pitfalls_path(), rows)
        return pitfall


def record_pitfall_avoid(plan: TaskPlan, changed_steps: List[Dict[str, Any]]) -> None:
    """重规划成功后，把新步骤标题写成"换个方法"的避坑建议。"""
    if plan is None or not changed_steps:
        return
    avoid = "；".join(str(s.get("title") or "")[:80] for s in changed_steps[:3])
    if not avoid:
        return
    tokens = _goal_tokens(plan.goal)
    with _WRITE_LOCK:
        rows = _load_store(_pitfalls_path())
        for row in rows:
            if _jaccard(tokens, set(row.get("tokens") or [])) >= RECIPE_MATCH_THRESHOLD and not row.get("avoid"):
                row["avoid"] = avoid[:300]
                row["updated_at"] = _now()
        _save_store(_pitfalls_path(), rows)


def match_pitfalls(goal: str) -> List[Dict[str, Any]]:
    """开工前匹配历史失败，返回需要提前规避的条目（含换方法建议）。"""
    tokens = _goal_tokens(goal)
    if len(tokens) < 2:
        return []
    scored = []
    for row in _load_store(_pitfalls_path()):
        score = _jaccard(tokens, set(row.get("tokens") or []))
        if score >= RECIPE_MATCH_THRESHOLD:
            scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    return [dict(r) for _s, r in scored[:MAX_PITFALL_HINTS]]


def list_pitfalls(limit: int = 50) -> List[Dict[str, Any]]:
    try:
        limit = max(1, min(int(limit or 50), MAX_RECIPES))
    except (TypeError, ValueError):
        limit = 50
    rows = _load_store(_pitfalls_path())
    rows.sort(key=lambda r: int(r.get("occurrences") or 0), reverse=True)
    return rows[:limit]


def finalize(plan_id: str) -> Tuple[Optional[TaskPlan], bool]:
    """证据即完成：所有步骤验收通过即一次性置 completed，无事后复核轮。

    返回 (plan, just_completed)；just_completed 仅在本次调用首次完成时为真。
    """
    with _WRITE_LOCK:
        plan = load(plan_id)
        if plan is None:
            return None, False
        # 用 finalized 标记而非 status 判定「首次收尾」：mark 的 _sync_status 会提前把
        # status 置为 completed，若按 status 判断会让配方沉淀/记忆归档被整段跳过。
        if plan.all_done() and not plan.finalized and plan.status != "cancelled":
            plan.status = "completed"
            plan.finalized = True
            plan.updated_at = _now()
            save(plan)
            return plan, True
        return plan, False


def _heuristic_steps(goal: str) -> List[Dict[str, Any]]:
    """模型不可用时的本地拆步：只认显式顺序词/编号列表。"""
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


def _guess_strategy(goal: str) -> str:
    text = str(goal or "")
    if any(k in text for k in ("代码", "重构", "测试", "修复", "函数", "脚本", "接口")):
        return "code"
    if any(k in text for k in ("文档", "报告", "说明", "总结", "写作", "文章")):
        return "docs"
    if any(k in text for k in ("表格", "数据", "CSV", "csv", "Excel", "excel", "统计")):
        return "data"
    if any(k in text for k in ("调研", "搜索", "资料", "检索", "网页")):
        return "research"
    return "general"


def _parse_steps(text: str, goal: str) -> Tuple[List[Dict[str, Any]], str]:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        data = json.loads(raw)
    except Exception:
        return [], _guess_strategy(goal)
    if not isinstance(data, dict):
        return [], _guess_strategy(goal)
    strategy = str(data.get("strategy") or _guess_strategy(goal))[:30]
    steps = data.get("steps")
    if not isinstance(steps, list):
        return [], strategy
    out = []
    for s in steps[:8]:
        title = s.get("title") if isinstance(s, dict) else s
        title = str(title or "").strip()
        if title:
            item: Dict[str, Any] = {"id": len(out) + 1, "title": title[:120]}
            if isinstance(s, dict):
                item["depends_on"] = _norm_ids(s.get("depends_on") or [])
                item["parallel_group"] = str(s.get("parallel_group") or "")[:60]
            out.append(item)
    return out, strategy


def create(client, goal: str, session_id: str, priority: Optional[int] = None) -> TaskPlan:
    """配方命中直接套用已验证步骤模板（省一次模型规划）；否则模型拆步/本地兜底。"""
    goal = str(goal or "").strip()
    parts: List[str] = []
    recipe = match_recipe(goal)  # P4-1：同类任务复用，跳过规划调用，也不撞 429
    skill = None
    # P5-1：只有精确匹配 + successes 达标的已验证配方才升级为零模型确定性技能。
    # 命中技能时直接套用技能的步骤模板（与配方一致），并记录 execution_mode。
    if recipe is not None:
        skill = match_skill(goal)
    steps: List[Dict[str, Any]] = []
    strategy = ""
    if recipe is not None:
        steps = [dict(s) for s in (recipe.get("steps") or [])]
        strategy = str(recipe.get("strategy") or "general")
    elif not any(h in goal for h in SIMPLE_HINTS):
        try:
            client.chat(
                [
                    {"role": "system", "content": PLANNER_PROMPT},
                    {"role": "user", "content": goal[:2000]},
                ],
                tools_enabled=False,
                json_output=True,
                max_tokens=1000,
                temperature=0.2,
                on_content=lambda t: parts.append(t),
            )
        except Exception as e:
            logger.warning("任务规划模型不可用，改用本地拆步：%s", e)
            parts = []
        steps, strategy = _parse_steps("".join(parts), goal)
    if not steps:
        steps = _heuristic_steps(goal)
    plan_id = uuid.uuid4().hex[:12]
    if steps:
        capture_workspace_baseline(plan_id)
    return TaskPlan(
        plan_id=plan_id,
        session_id=str(session_id or ""),
        goal=goal[:500],
        steps=steps,
        strategy=strategy or "general",
        priority=priority if priority is not None else priority_from_goal(goal),
        recipe_id=str((recipe or {}).get("recipe_id") or ""),
        recipe_name=str((recipe or {}).get("name") or ""),
        pitfalls_applied=[str(p.get("pitfall_id") or "") for p in match_pitfalls(goal)],
        execution_mode="deterministic" if skill is not None else "model",
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


def prepare(client, session_id: str, goal: str, priority: Optional[int] = None) -> Optional[TaskPlan]:
    """新任务重开；同会话有未完成计划则续跑；不需要拆步时返回 None。"""
    text = str(goal or "")
    prev = None if any(h in text for h in NEW_TASK_HINTS) else find_incomplete(session_id)
    if prev is not None and _looks_like_continuation(text, prev.goal):
        if prev.status in ("paused", "blocked", "queued"):
            prev = control(prev.plan_id, "resume")[0] or prev
        if priority is not None and prev.priority != int(priority):
            with _WRITE_LOCK:
                prev.priority = int(priority)
                prev.updated_at = _now()
                save(prev)
        return prev
    if prev is not None:
        with _WRITE_LOCK:
            prev.status = "cancelled"
            prev.updated_at = _now()
            save(prev)
    plan = create(client, text, session_id, priority=priority)
    if not plan.steps:
        return None
    save(plan)
    return plan


def replan_remaining(client, plan: TaskPlan) -> Tuple[Optional[TaskPlan], bool]:
    """连续失败后，让模型换方法重规划未完成步骤；失败则保持原计划。"""
    undone = [s for s in plan.steps if s.get("status") not in DONE_STATUSES]
    if not undone:
        return plan, False
    kept = [s for s in plan.steps if s.get("status") in DONE_STATUSES]
    lines = [f"目标：{plan.goal}", "已完成步骤："]
    for s in kept:
        lines.append(f"- {s.get('title')}")
    lines.append("失败/未完成步骤：")
    for s in undone:
        lines.append(f"- {s.get('title')}（失败原因：{str(s.get('last_error') or '')[:160]}）")
    parts: List[str] = []
    try:
        client.chat(
            [
                {"role": "system", "content": REPLAN_PROMPT},
                {"role": "user", "content": "\n".join(lines)[:3000]},
            ],
            tools_enabled=False,
            json_output=True,
            max_tokens=800,
            temperature=0.3,
            on_content=lambda t: parts.append(t),
        )
    except Exception as e:
        logger.warning("剩余步骤重规划失败：%s", e)
        return plan, False
    fresh, _strategy = _parse_steps("".join(parts), plan.goal)
    if not fresh:
        return plan, False
    ids = [int(s.get("id") or 0) for s in undone]
    new_steps = []
    for i, item in enumerate(fresh[: len(ids)]):
        item = dict(item)
        item["id"] = ids[i]
        item["status"] = "pending"
        item["attempts"] = 0
        item["last_error"] = ""
        item["evidence"] = ""
        new_steps.append(item)
    if not new_steps:
        return plan, False
    with _WRITE_LOCK:
        plan = load(plan.plan_id) or plan
        plan.steps = kept + new_steps
        plan.status = "running"
        plan.runtime = _norm_runtime({"stream_id": plan.runtime.get("stream_id", "")})
        plan.updated_at = _now()
        plan.refresh_dependencies()
        save(plan)
    # P4-3：把换方法后的步骤写成同类任务的避坑建议
    try:
        record_pitfall_avoid(plan, new_steps)
    except Exception as e:
        logger.warning("避坑建议记录失败：%s", e)
    return plan, True


def render_prompt(plan: TaskPlan) -> str:
    lines = [
        "[任务模式 · 长任务计划]",
        f"计划 ID：{plan.plan_id}",
        f"目标：{plan.goal}",
        f"执行策略：{plan.strategy}；优先级：{plan.priority}",
    ]
    # P4-1：命中配方时明确告知，模型直接照已验证模板走，不要重新设计路径
    if plan.recipe_id:
        lines.append(f"复用配方「{plan.recipe_name}」：以下步骤已验证可行，直接照做，不要另起思路。")
    # P4-3：开工前避坑——把历史失败转换成"第一次就走对"的前置提示，不做事后返工
    if plan.pitfalls_applied:
        applied = set(plan.pitfalls_applied)
        avoid_lines = []
        for row in match_pitfalls(plan.goal):
            if str(row.get("pitfall_id") or "") not in applied:
                continue
            note = f"- 「{row.get('step_title', '')}」上次因 {str(row.get('reason', ''))[:80]} 失败"
            if row.get("avoid"):
                note += f"；改用：{str(row.get('avoid', ''))[:80]}"
            else:
                note += "；这次换一种方法，别重复同一路径"
            avoid_lines.append(note)
        if avoid_lines:
            lines.append("[开工前避坑清单 · 历史经验，第一次就走对，不要事后返工]")
            lines.extend(avoid_lines[:MAX_PITFALL_HINTS])
    lines.append("步骤（依赖未满足的标为 waiting，暂时不要执行）：")
    for s in plan.steps:
        mark = {"done": "✓", "skipped": "↷", "failed": "✕", "blocked": "!", "waiting": "⧗"}.get(s.get("status"), "○")
        deps = _norm_ids(s.get("depends_on") or [])
        dep_text = f"，依赖 {','.join(str(d) for d in deps)}" if deps else ""
        group = str(s.get("parallel_group") or "")
        group_text = f"，并行组 {group}" if group else ""
        error = f"（失败 {s.get('attempts')} 次：{str(s.get('last_error'))[:100]}）" if s.get("last_error") else ""
        lines.append(f"{mark} {s.get('id')}. {s.get('title')}{dep_text}{group_text}{error}")
    lines += [
        "",
        "执行规则：",
        f"1. 按依赖顺序执行；每完成一步，必须调用 task_step_done(plan_id=\"{plan.plan_id}\", step=N, "
        "evidence=\"可核验的证据\", verify_path=\"可选产物路径\", verification={可选结构化验收})。",
        "2. 没有调用 task_step_done 的步骤不算完成，不要提前声称全部完成。",
        "3. 同一个 parallel_group 且无依赖关系的步骤可以在同一轮里批量执行，但每一步都要分别调用 task_step_done 验收。",
        "4. waiting 步骤要等它依赖的步骤 done/skipped 后再执行；某步失败只影响依赖它的步骤，其余分支继续。",
        "5. 某步失败时调用 task_step_done(status=\"failed\", evidence=\"失败原因\")；同一错误最多自动重试 "
        f"{MAX_STEP_ATTEMPTS} 次，仍失败会阻塞该分支并可能触发剩余步骤重规划。",
        "6. 完成后汇总产物与验收证据；被用户暂停/取消的计划不得继续执行。",
    ]
    return "\n".join(lines)


def pending_steps(plan: TaskPlan) -> List[Dict[str, Any]]:
    return plan.pending_steps()


def current_hint(plan: Optional[TaskPlan]) -> str:
    """每轮工具循环注入的短提醒：始终指向当前可执行的那一步。"""
    if plan is None or plan.status not in ACTIVE_STATUSES:
        return ""
    left = plan.ready_steps()
    if not left:
        waiting = plan.pending_steps()
        stuck = [s for s in waiting if s.get("status") in ("blocked", "failed")]
        if stuck:
            step = stuck[0]
            return (
                f"[长任务进度 {plan.done_count()}/{len(plan.steps)}] "
                f"第 {step.get('id')} 步连续失败已阻塞：{str(step.get('last_error'))[:100]}。"
                f"换一种方法后调用 task_step_done(plan_id=\"{plan.plan_id}\", step={step.get('id')}, "
                "evidence=\"新证据\")；依赖它的步骤暂时不要执行。"
            )
        if waiting:
            return (
                f"[长任务进度 {plan.done_count()}/{len(plan.steps)}] "
                f"剩余 {len(waiting)} 步都在等待前置步骤完成；请先完成已就绪步骤，不要抢跑。"
            )
        return ""
    group = []
    for s in left:
        if str(s.get("parallel_group") or "") and str(s.get("parallel_group")) == str(left[0].get("parallel_group") or ""):
            group.append(int(s.get("id") or 0))
    ids = group if len(group) > 1 else [int(left[0].get("id") or 0)]
    step = left[0]
    extra = ""
    if step.get("status") in ("failed", "blocked"):
        extra = (
            f"上一步失败 {step.get('attempts')} 次：{str(step.get('last_error'))[:120]}。"
            "换一种方法后再提交验收证据，不要重复同样的失败路径。"
        )
    rt = plan.runtime or {}
    rt_text = ""
    if rt.get("state") == "waiting":
        rt_text = f"上次生成异常（{rt.get('kind')}）：{str(rt.get('message'))[:100]}。换策略执行，避免重复触发限流。"
    id_text = "、".join(str(i) for i in ids)
    return (
        f"[长任务进度 {plan.done_count()}/{len(plan.steps)}] "
        f"当前必须执行第 {id_text} 步：{step.get('title')}。{extra}{rt_text}"
        f"完成后立刻调用 task_step_done(plan_id=\"{plan.plan_id}\", "
        f"step={ids[0]}, evidence=\"可核验证据\")。"
        f"还剩 {len(plan.pending_steps())} 步；未全部验收前不要结束回复。"
    )


def recall_tasks(query: str, limit: int = 3, min_score: float = 0.18) -> List[Dict[str, Any]]:
    """P5-2 跨会话任务召回：从已完成计划中找相关任务的结构化摘要。

    不依赖向量模型：目标 token Jaccard + 查询串/产物路径命中，轻量且可离线。
    返回项含 goal / 产物路径 / 每步证据 / 完成时间，供注入提示让模型直接答复
    「昨天那份报告在哪、结论是什么」这类跨会话问题。
    """
    tokens = _goal_tokens(query)
    text = str(query or "").lower()
    # 中文 bigram：_goal_tokens 把整段汉字当一个词，对自然语言问句几乎无重叠。
    # 用 2-gram 重合度补足，让「上次的销售报告」能匹配「生成季度销售报告」。
    def _bigrams(s: str) -> set:
        han = re.findall(r"[\u4e00-\u9fff]+", str(s or ""))
        out = set()
        for seg in han:
            out.update(seg[i:i + 2] for i in range(len(seg) - 1))
        return out
    q_bg = _bigrams(query)
    if not tokens and not q_bg and not text.strip():
        return []
    try:
        limit = max(1, min(int(limit or 3), 10))
    except (TypeError, ValueError):
        limit = 3
    scored: List[Tuple[float, str, Dict[str, Any]]] = []
    try:
        names = [n for n in os.listdir(_dir()) if n.endswith(".json")]
    except OSError:
        return []
    for name in names[:1000]:
        try:
            with open(os.path.join(_dir(), name), encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or str(data.get("status") or "") != "completed":
                continue
            plan = TaskPlan.from_dict(data)
            goal = plan.goal
            score = _jaccard(tokens, _goal_tokens(goal))
            # 中文问句：bigram 重合度（按查询覆盖率计，避免长目标稀释）
            if q_bg:
                g_bg = _bigrams(goal)
                if q_bg and g_bg:
                    overlap = len(q_bg & g_bg) / float(len(q_bg))
                    score = max(score, overlap * 0.9)
            # 产物路径命中（用户常按文件名追问）
            paths = [str(a.get("relpath") or a.get("path") or "") for a in (plan.artifacts or [])]
            for p in paths:
                if p and p.lower() in text:
                    score = max(score, 0.75)
            if score < min_score:
                continue
            summary = {
                "plan_id": plan.plan_id,
                "goal": goal[:200],
                "completed_at": plan.updated_at,
                "strategy": plan.strategy,
                "artifacts": paths[:20],
                "steps": [
                    {"title": str(s.get("title") or "")[:120],
                     "evidence": str(s.get("evidence") or "")[:200]}
                    for s in (plan.steps or []) if s.get("status") == "done"
                ][:12],
            }
            scored.append((score, plan.updated_at, summary))
        except Exception:
            continue
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [row[2] for row in scored[:limit]]


def recall_text(query: str, limit: int = 3) -> str:
    """把召回结果渲染成可注入的紧凑文本；无命中返回空串（不污染提示）。"""
    rows = recall_tasks(query, limit=limit)
    if not rows:
        return ""
    lines = ["[历史任务召回] 以下是与本问题相关的已完成任务，产物与结论可直接引用："]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. 任务：{r['goal']}（完成于 {r.get('completed_at','')}）")
        if r.get("artifacts"):
            lines.append(f"   产物：{', '.join(r['artifacts'][:8])}")
        for s in (r.get("steps") or [])[:6]:
            ev = str(s.get("evidence") or "").strip()
            if ev:
                lines.append(f"   · {s.get('title','')}：{ev[:160]}")
    return "\n".join(lines)


def archive_text(plan: TaskPlan) -> str:
    """任务完成后写入长期记忆的文本：目标 + 每步验收证据 + 产物摘要。"""
    lines = [
        f"[任务归档] 计划 {plan.plan_id}：{plan.goal}",
        f"状态：已完成，共 {len(plan.steps)} 步；策略：{plan.strategy}；完成时间：{_now()}。",
    ]
    if plan.recipe_id:
        lines.append(f"复用配方：{plan.recipe_name}（{plan.recipe_id}）")
    lines.append("步骤与验收证据：")
    for s in plan.steps:
        ev = str(s.get("evidence") or "无证据").replace("\n", " ")
        v = s.get("verification") or {}
        tag = "已结构化验收" if v.get("ok") else "文字证据"
        lines.append(f"{s.get('id')}. {s.get('title')} → [{tag}] {ev[:220]}")
    if plan.artifacts:
        lines.append("产物：")
        for a in plan.artifacts[:20]:
            lines.append(f"- {a.get('relpath') or a.get('path')}（{a.get('state')}）")
    return "\n".join(lines)


def resume_prompt(plan: TaskPlan) -> str:
    """模型提前收尾时，自动续跑用的用户消息。"""
    left = plan.ready_steps() or plan.pending_steps()
    lines = [f"[长任务自动续跑] 计划 {plan.plan_id} 还有 {len(plan.pending_steps())} 步未验收："]
    for s in left[:8]:
        err = f"（上次失败：{str(s.get('last_error'))[:120]}）" if s.get("last_error") else ""
        dep = _norm_ids(s.get("depends_on") or [])
        dep_text = f"，前置 {','.join(str(d) for d in dep)}" if dep else ""
        lines.append(f"- 第 {s.get('id')} 步：{s.get('title')}{dep_text}{err}")
    lines += [
        "",
        "请直接执行上述已就绪步骤，每步调用 task_step_done 提交证据；"
        "waiting 步骤等前置完成后再执行，失败的步骤要换方法。不要重复已完成的步骤。",
    ]
    return "\n".join(lines)
