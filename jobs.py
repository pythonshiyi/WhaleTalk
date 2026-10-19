"""执行作业（job）持久化：把「一轮执行」变成可回看、可统计的一等对象。

为什么需要它：会话只记录**对话**，不记录**执行**。于是「昨天那个跑挂了」
只能靠翻聊天记录找，无法列表、无法按状态筛、更无法谈重跑。

这里刻意保持极简：
  · 一轮作业 = 一条 JSON 记录，追加写入 `data/jobs/YYYY-MM-DD.jsonl`（只追加，不改写，
    进程被杀也不会写坏已有记录）；
  · 记录**不含**完整工具结果与完整参数（可能含密钥/隐私），只留长度与前 200 字预览；
  · 所有失败都降级：作业记账坏了绝不能影响生成本身。
"""

import json
import os
import time

_LOCK = None
_DIR = ""
_LOCK_MODULE = None


def _lock():
    """懒初始化线程锁（模块导入期不建锁，避免 fork/重载问题）。"""
    global _LOCK
    if _LOCK is None:
        import threading
        _LOCK = threading.Lock()
    return _LOCK


def set_dir(path):
    """注入作业目录（api_server 启动时调用一次）。"""
    global _DIR
    _DIR = str(path or "")
    return _DIR


def job_dir():
    return _DIR


def _today_path(now=None):
    if not _DIR:
        return ""
    d = time.strftime("%Y-%m-%d", time.localtime(now if now is not None else time.time()))
    return os.path.join(_DIR, f"{d}.jsonl")


# ── 事件折叠 ───────────────────────────────────────────────

def _preview(v, n=200):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    s = (s or "").strip().replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


def fold_events(events):
    """把作业事件流折叠成步骤列表。

    事件形态（来自 _run_chat_job_thread 的 send）：
      tool_start {name,args,id} / tool {name,args,result,id} / tool_duration {name,duration,id}
    按 `id` 对齐（并行调用靠它区分）；无 id 时按「最近一个同名未完成步骤」合并。
    """
    steps = []
    by_id = {}
    last_by_name = {}
    for item in events or []:
        if not isinstance(item, (list, tuple)) or len(item) < 3:
            continue
        _seq, event, data = item[0], item[1], item[2]
        if not isinstance(data, dict):
            continue
        name = str(data.get("name") or "").strip()
        sid = data.get("id") or None
        if event == "tool_start":
            step = {"tool": name, "status": "running", "duration": "", "failed": False,
                    "args_preview": _preview(data.get("args") or {}), "result_chars": 0}
            steps.append(step)
            if sid:
                by_id[sid] = step
            last_by_name[name] = step
        elif event == "tool_duration":
            step = by_id.get(sid) if sid else last_by_name.get(name)
            if step is not None:
                step["duration"] = str(data.get("duration") or "")
        elif event == "tool":
            # 有 id 按 id 对齐；没有 id 时退回「同名最近一个未完成步骤」
            step = by_id.get(sid) if sid else last_by_name.get(name)
            if step is None:
                step = {"tool": name, "status": "done", "duration": "", "failed": False,
                        "args_preview": _preview(data.get("args") or {}), "result_chars": 0}
                steps.append(step)
                if sid:
                    by_id[sid] = step
            result = data.get("result")
            text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
            # 与后端 tool 失败前缀保持一致（shared.TOOL_RESULT_FAIL_PREFIXES），此处不 import
            step["failed"] = bool(text) and (
                text.startswith("错误：") or text.startswith("工具执行失败")
                or text.startswith("工具参数") or text.startswith("权限被拒绝")
            )
            step["status"] = "failed" if step["failed"] else "done"
            step["result_chars"] = len(text or "")
    for s in steps:
        if s.get("status") == "running":
            s["status"] = "failed" if s.get("failed") else "done"
    return steps


def build_record(job, *, user_text="", model="", usage=None, error=""):
    """由作业对象构造一条可持久化的记录。

    对 `job` 只做鸭子类型访问（id/sid/created/finished/status/events），
    便于测试时用轻量替身。
    """
    now = time.time()
    started = float(getattr(job, "created", now) or now)
    ended = float(getattr(job, "finished", None) or now)
    events = list(getattr(job, "events", None) or [])
    steps = fold_events(events)
    failed = sum(1 for s in steps if s.get("status") == "failed")
    status = str(getattr(job, "status", "") or "done")
    if status == "running":
        status = "done"
    u = usage if isinstance(usage, dict) else {}
    return {
        "id": str(getattr(job, "id", "") or ""),
        "session_id": str(getattr(job, "sid", "") or ""),
        "status": status,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)),
        "ended_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ended)),
        "duration_s": round(max(0.0, ended - started), 2),
        "model": str(model or ""),
        "user_text": _preview(user_text, 120),
        "steps": steps,
        "step_count": len(steps),
        "failed_count": failed,
        "usage": {k: u.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens") if u.get(k) is not None},
        "error": _preview(error, 300),
    }


# ── 读写 ───────────────────────────────────────────────────

def append_record(rec):
    """追加一条记录。返回 True/False（失败只返回 False，不抛）。"""
    if not rec or not _DIR or not rec.get("id"):
        return False
    try:
        os.makedirs(_DIR, exist_ok=True)
        path = _today_path()
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        with _lock():
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        return True
    except Exception:  # noqa: BLE001 作业记账失败不得影响生成
        return False


def _read_file(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:  # noqa: BLE001 单行损坏跳过，不影响其它记录
                    continue
    except OSError:
        return []
    return out


def _day_files():
    """按日期倒序返回作业文件（新的在前）。"""
    if not _DIR or not os.path.isdir(_DIR):
        return []
    try:
        names = [n for n in os.listdir(_DIR) if n.endswith(".jsonl")]
    except OSError:
        return []
    names.sort(reverse=True)
    return [os.path.join(_DIR, n) for n in names]


def list_jobs(limit=50, session_id="", status=""):
    """按时间倒序返回作业（跨天）。limit 有上限，避免把整年记录读进内存。"""
    want_status = str(status or "").strip()
    want_sid = str(session_id or "").strip()
    lim = max(1, min(int(limit or 50), 500))
    out = []
    for path in _day_files():
        for rec in _read_file(path):
            if not isinstance(rec, dict):
                continue
            if want_sid and str(rec.get("session_id") or "") != want_sid:
                continue
            if want_status and str(rec.get("status") or "") != want_status:
                continue
            out.append(rec)
        if len(out) >= lim:
            break
    out.sort(key=lambda r: str(r.get("started_at") or ""), reverse=True)
    return out[:lim]


def get_job(job_id):
    want = str(job_id or "").strip()
    if not want:
        return None
    for path in _day_files():
        for rec in _read_file(path):
            if isinstance(rec, dict) and str(rec.get("id") or "") == want:
                return rec
    return None


def summary(records):
    """列表页用的聚合：总数 / 失败 / 总耗时。"""
    rows = [r for r in (records or []) if isinstance(r, dict)]
    return {
        "count": len(rows),
        "failed": sum(1 for r in rows if r.get("status") == "error" or r.get("failed_count")),
        "seconds": round(sum(float(r.get("duration_s") or 0) for r in rows), 1),
    }
