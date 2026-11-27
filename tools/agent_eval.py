#!/usr/bin/env python3
"""鲸语智能体评测台（P0 立尺）。

目标：回答一个单元测试回答不了的问题——「用户让它做一件真实的事，它做成了吗」。

设计要点：
- **真链路**：起真实的本地 API（`web_app.py --server`），走 `/v1/chat/stream`，
  不 mock 模型、不 mock 工具；
- **隔离**：每个任务独立的数据目录 / 配置 / 大脑目录 / 工作区，跑完不污染用户数据；
- **可判定**：验收条件全部是程序可检查的文件/JSON/CSV/工具调用，不靠 LLM 打分；
- **可对照**：输出 `scorecard.json` + `scorecard.md`，支持 `--baseline` 比较。

用法：
    py -3.15 tools/agent_eval.py --list
    py -3.15 tools/agent_eval.py --dry-run
    py -3.15 tools/agent_eval.py --only file_markdown_report
    py -3.15 tools/agent_eval.py --repeat 3 --baseline out/eval/xxx/scorecard.json

任务格式见 `tests/eval/golden/*.json`，验收类型见 `CHECK_TYPES`。
"""

from __future__ import annotations

import argparse
import csv
import functools
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
# 直接 `py tools/agent_eval.py` 时 sys.path[0] 是 tools/，仓库根不在路径上；
# 费用估算（stats）等仓库模块会 import 失败并静默回退 0。先补根目录。
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
TASKS_DIR = REPO / "tests" / "eval" / "golden"
FIXTURES_DIR = REPO / "tests" / "eval" / "fixtures"
DEFAULT_REPORT_DIR = REPO / "out" / "eval"

# Windows 控制台码页编不出中文会让评测以崩代红（与 tools/*.py 既有加固一致）。
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # silent-except: 控制台不支持 reconfigure 时保持默认编码
        pass

# 评测环境统一约束：模型常按任务名/会话名自造子目录（实测踩过两次），
# 这里在每条用户消息后追加一次，保证「相对路径 = 工作目录根」的语义稳定。
EVAL_SUFFIX = (
    "\n\n[评测约束] 所有相对路径以当前工作目录为根，严格使用任务给出的路径，"
    "不要新建子目录；不要询问用户，直接完成。"
)

CHECK_TYPES = {
    "file_exists", "file_not_exists", "file_contains", "file_not_contains",
    "file_regex", "file_min_bytes", "file_max_bytes", "file_number_min",
    "json_field", "json_keys", "json_len", "csv_columns", "csv_row_count",
    "csv_row", "image_size", "image_min_size", "xlsx_cells", "zip_entries",
    "command", "dir_exists", "tool_called", "no_tool_error", "final_contains",
    "final_not_contains", "final_regex",
}


class EvalError(RuntimeError):
    """评测环境/任务定义错误（不是任务失败）。"""


# ---------------------------------------------------------------------------
# 任务定义
# ---------------------------------------------------------------------------

@dataclass
class Task:
    id: str
    category: str
    turns: List[str]
    accept: List[Dict[str, Any]]
    setup: List[str] = field(default_factory=list)
    local_http: List[str] = field(default_factory=list)
    mode: str = "task"
    budget: Dict[str, Any] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    fresh_session_each_turn: bool = False
    fresh_session_at: List[int] = field(default_factory=list)
    notes: str = ""
    path: str = ""


def _load_task(path: Path) -> Task:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise EvalError(f"{path.name}: JSON 解析失败：{e}") from None
    if not isinstance(data, dict):
        raise EvalError(f"{path.name}: 顶层必须是对象")
    tid = str(data.get("id") or "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", tid):
        raise EvalError(f"{path.name}: id 非法（小写字母/数字/-/_，2-64 字符）：{tid!r}")
    turns = data.get("turns")
    if not turns and data.get("prompt"):
        turns = [data["prompt"]]
    if not isinstance(turns, list) or not turns or not all(
        isinstance(t, str) and t.strip() for t in turns
    ):
        raise EvalError(f"{path.name}: 需要非空的 turns 数组（或 prompt）")
    accept = data.get("accept")
    if not isinstance(accept, list) or not accept:
        raise EvalError(f"{path.name}: 需要非空的 accept 数组")
    for i, spec in enumerate(accept):
        if not isinstance(spec, dict) or spec.get("type") not in CHECK_TYPES:
            raise EvalError(
                f"{path.name}: accept[{i}] 类型非法（支持：{sorted(CHECK_TYPES)}）"
            )
    setup = data.get("setup") or []
    if not isinstance(setup, list) or not all(isinstance(s, str) for s in setup):
        raise EvalError(f"{path.name}: setup 必须是字符串数组")
    for name in setup:
        if not (FIXTURES_DIR / name).exists():
            raise EvalError(f"{path.name}: 夹具不存在：{name}")
    local_http = data.get("local_http") or []
    if not isinstance(local_http, list) or not all(isinstance(s, str) for s in local_http):
        raise EvalError(f"{path.name}: local_http 必须是字符串数组")
    for name in local_http:
        if not (FIXTURES_DIR / name).is_file():
            raise EvalError(f"{path.name}: local_http 夹具不存在或不是文件：{name}")
    budget = data.get("budget") or {}
    if not isinstance(budget, dict):
        raise EvalError(f"{path.name}: budget 必须是对象")
    cfg = data.get("config") or {}
    if not isinstance(cfg, dict):
        raise EvalError(f"{path.name}: config 必须是对象")
    fresh_at = data.get("fresh_session_at") or []
    if not isinstance(fresh_at, list) or not all(
        isinstance(x, int) and x >= 1 for x in fresh_at
    ):
        raise EvalError(f"{path.name}: fresh_session_at 必须是正整数数组（轮次从 1 起）")
    return Task(
        id=tid,
        category=str(data.get("category") or "misc").strip(),
        turns=[t.strip() for t in turns],
        accept=accept,
        setup=list(setup),
        local_http=list(local_http),
        mode=str(data.get("mode") or "task"),
        budget=budget,
        config=cfg,
        fresh_session_each_turn=bool(data.get("fresh_session_each_turn")),
        fresh_session_at=list(fresh_at),
        notes=str(data.get("notes") or ""),
        path=str(path),
    )


def load_tasks(only: Optional[List[str]] = None, category: str = "") -> List[Task]:
    tasks: List[Task] = []
    for p in sorted(TASKS_DIR.glob("*.json")):
        tasks.append(_load_task(p))
    if only:
        wanted = set(only)
        missing = wanted - {t.id for t in tasks}
        if missing:
            raise EvalError(f"--only 含未知任务：{sorted(missing)}")
        tasks = [t for t in tasks if t.id in wanted]
    if category:
        tasks = [t for t in tasks if t.category == category]
    if not tasks:
        raise EvalError("没有匹配的评测任务")
    return tasks


# ---------------------------------------------------------------------------
# 结果结构
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    type: str
    ok: bool
    detail: str
    spec: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    name: str
    args: str = ""
    result: str = ""
    duration_s: float = 0.0


@dataclass
class TurnResult:
    index: int
    content: str = ""
    reasoning_chars: int = 0
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    metrics: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    duration_s: float = 0.0
    session_id: str = ""


@dataclass
class TaskResult:
    id: str
    category: str
    ok: bool
    checks: List[CheckResult] = field(default_factory=list)
    turns: List[TurnResult] = field(default_factory=list)
    error: str = ""
    duration_s: float = 0.0
    cost_cny: float = 0.0
    usage: Dict[str, int] = field(default_factory=dict)
    tool_calls: int = 0
    startup_s: float = 0.0
    workdir: str = ""
    server_log: str = ""
    repeat_index: int = 1
    skipped: bool = False
    baseline_status: str = ""


# ---------------------------------------------------------------------------
# HTTP / SSE
# ---------------------------------------------------------------------------

def _http_json(url: str, *, method: str = "GET", payload: Any = None,
               token: str = "", timeout: float = 30.0) -> Dict[str, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {"_raw": body.decode("utf-8", "replace")}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def _tail(path: Path, limit: int = 4000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return text[-limit:]


# ---------------------------------------------------------------------------
# 隔离环境
# ---------------------------------------------------------------------------

def _make_config(task: Task, workdir: Path, model_override: str = "",
                 require_key: bool = True) -> Dict[str, Any]:
    src = REPO / "config.json"
    if not src.exists():
        if require_key:
            raise EvalError("缺少 config.json（评测需要真实 API Key）；先启动一次主程序并配置模型")
        raw: Dict[str, Any] = {}
    else:
        try:
            raw = json.loads(src.read_text(encoding="utf-8"))
        except Exception as e:
            raise EvalError(f"config.json 解析失败：{e}") from None
    if not isinstance(raw, dict):
        raise EvalError("config.json 顶层必须是对象")
    if require_key and not str(raw.get("api_key") or "").strip():
        raise EvalError("config.json 未配置 api_key，无法进行真实模型评测")
    if not str(raw.get("api_key") or "").strip():
        # 仅 boot-only 冒烟会走到这里：服务能启动，但不允许真的发对话请求。
        raw["api_key"] = "eval-placeholder-no-key"

    cfg = dict(raw)
    # 运行时行为：全部隔离到临时目录，关掉会写真实数据/发网络请求的后台能力。
    cfg.update({
        "active_dir": str(workdir),
        "full_auto": True,
        "life_enabled": False,
        "brain_auto_snapshot": False,
        "brain_persona_enabled": False,
        "brain_continuity_enabled": False,
        "brain_community_enabled": False,
        "memory_enabled": bool(task.config.get("memory_enabled", False)),
        "auto_memory": bool(task.config.get("auto_memory", False)),
        "memory_harvest_mode": str(task.config.get("memory_harvest_mode") or "off"),
        "check_update": False,
        "welcomed": True,
        "block_on_budget": False,
        "monthly_budget": 0,
        "confirm_over_cost": 0,
        "project_context": "",
        "browser_headless": True,
        "silent_start": True,
        "notify_on_done": False,
        "completion_sound": False,
        "peak_warning": False,
        "suggestions_enabled": False,
        "agent_mail_enabled": False,
        "json_output": False,
        "strict_tools": False,
        "stop": [],
        "tool_choice": "auto",
        "live_enabled": False,
    })
    if model_override:
        cfg["model"] = model_override
    return cfg


def _copy_setup(task: Task, workdir: Path) -> None:
    for name in task.setup:
        src = FIXTURES_DIR / name
        dst = workdir / name
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


@dataclass
class ServerHandle:
    proc: subprocess.Popen
    port: int
    token: str
    base: str
    log_path: Path
    config_path: Path
    workdir: Path
    startup_s: float = 0.0


class _QuietHTTPHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003 - 覆盖标准库签名
        return


def _start_local_http(task: Task, run_dir: Path):
    """为需要联网的任务起一个只读本地 HTTP 夹具，避免外部站点抖动。

    任务里用 `{base_url}` 占位；只有声明了 `local_http` 的任务才会替换。
    """
    if not task.local_http:
        return None, "", None
    http_dir = run_dir / "http"
    http_dir.mkdir(parents=True, exist_ok=True)
    for name in task.local_http:
        shutil.copy2(FIXTURES_DIR / name, http_dir / name)
    handler = functools.partial(_QuietHTTPHandler, directory=str(http_dir))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    port = int(httpd.server_address[1])
    return httpd, f"http://127.0.0.1:{port}", th


def _stop_local_http(httpd, th) -> None:
    if httpd is not None:
        try:
            httpd.shutdown()
            httpd.server_close()
        except Exception:  # silent-except: 本地夹具服务关闭失败不影响评测结论
            pass
    if th is not None:
        try:
            th.join(timeout=3)
        except Exception:  # silent-except: 线程已退出/无法 join 时忽略
            pass


def _start_server(task: Task, run_dir: Path, model_override: str = "",
                  startup_timeout: float = 90.0,
                  require_key: bool = True) -> ServerHandle:
    data_dir = run_dir / "data"
    brain_dir = run_dir / "brain"
    workdir = data_dir / "workspace"
    for d in (data_dir, brain_dir, workdir):
        d.mkdir(parents=True, exist_ok=True)
    _copy_setup(task, workdir)

    cfg = _make_config(task, workdir, model_override=model_override,
                       require_key=require_key)
    config_path = run_dir / "config.json"
    config_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    port = _free_port()
    log_path = run_dir / "server.log"
    env = os.environ.copy()
    env.update({
        "WHALETALK_DATA_DIR": str(data_dir),
        "WHALETALK_CONFIG": str(config_path),
        "WHALETALK_BRAIN_DIR": str(brain_dir),
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "WHALETALK_EVAL": "1",
        # 评测与用户主程序并存：跳过全局单实例等待（默认仍单实例）。
        "WHALETALK_ALLOW_MULTI_INSTANCE": "1",
    })
    env.pop("PYTHONPATH", None)

    cmd = [
        sys.executable, str(REPO / "web_app.py"), "--server",
        "--port", str(port), "--no-deps-check", "--no-webui-build",
        "--no-shortcuts",
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    with open(log_path, "wb") as log_f:
        proc = subprocess.Popen(
            cmd, cwd=str(REPO), env=env, stdout=log_f, stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )

    base = f"http://127.0.0.1:{port}"
    started = time.monotonic()
    deadline = started + startup_timeout
    last_err = ""
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise EvalError(
                f"服务启动即退出（code={proc.returncode}）：\n{_tail(log_path)}"
            )
        try:
            tok = _http_json(base + "/v1/token", timeout=2.0)
            token = str(tok.get("token") or "")
            if token:
                return ServerHandle(
                    proc, port, token, base, log_path, config_path, workdir,
                    startup_s=round(time.monotonic() - started, 2),
                )
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        time.sleep(0.5)
    _stop_server(proc)
    raise EvalError(f"服务启动超时（{startup_timeout:.0f}s）：{last_err}\n{_tail(log_path)}")


def _stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # silent-except: 停止服务是尽力而为，进程可能已退出
            pass


def _redact_config(path: Path) -> None:
    """跑完把临时配置里的凭据抹掉，避免密文随评测产物长期留存。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return
        for k in list(data.keys()):
            if k.endswith("api_key") or k.endswith("secret") or k.endswith("token"):
                data[k] = "***"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # silent-except: 脱敏是收尾动作，失败不影响评测结论
        pass


# ---------------------------------------------------------------------------
# 单轮对话
# ---------------------------------------------------------------------------

def _accumulate_usage(total: Dict[str, int], usage: Dict[str, Any]) -> None:
    for k in ("prompt", "completion", "cache_hit", "cache_miss"):
        try:
            total[k] = int(total.get(k, 0)) + int(usage.get(k) or 0)
        except (TypeError, ValueError):  # silent-except: 网关可能返回缺失/非数字用量字段
            pass


def _respond(srv: ServerHandle, kind: str, payload: Dict[str, Any]) -> None:
    body: Dict[str, Any] = {"id": payload.get("id")}
    if kind == "ask_request":
        body["answer"] = "请自行判断并继续，不要等待用户。"
    elif kind == "approval_request":
        body["allow"] = True
        body["reason"] = "eval auto-approve"
    elif kind == "plan_request":
        body["approve"] = True
    try:
        _http_json(srv.base + "/v1/respond", method="POST", payload=body,
                   token=srv.token, timeout=5.0)
    except Exception:  # silent-except: 交互回传失败时由服务端超时兜底，评测继续
        pass


def chat_turn(srv: ServerHandle, session_id: str, stream_id: str,
              messages: List[Dict[str, str]], task: Task,
              timeout: float) -> TurnResult:
    max_calls = int(task.budget.get("max_tool_calls") or 20)
    payload = {
        "messages": messages,
        "session_id": session_id,
        "stream_id": stream_id,
        "mode": task.mode,
        "tools_enabled": True,
        "max_tool_rounds": int(task.budget.get("max_tool_rounds") or max(6, max_calls)),
        "session_name": f"eval-{task.id}",
        "cost_confirmed": True,
    }
    req = urllib.request.Request(
        srv.base + "/v1/chat/stream",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {srv.token}")

    result = TurnResult(index=0, session_id=session_id)
    content: List[str] = []
    started = time.monotonic()
    read_timeout = max(5.0, min(60.0, timeout))
    done = False
    buf: List[str] = []
    pending_starts: Dict[str, ToolCall] = {}

    def handle(ev: Dict[str, Any]) -> None:
        nonlocal done
        typ = str(ev.get("type") or "")
        if typ == "content":
            content.append(str(ev.get("text") or ""))
        elif typ == "reasoning":
            result.reasoning_chars += len(str(ev.get("text") or ""))
        elif typ == "session":
            sid = str(ev.get("id") or "")
            if sid:
                result.session_id = sid
        elif typ == "tool_start":
            name = str(ev.get("name") or "")
            pending_starts[name] = ToolCall(name=name, args=str(ev.get("args") or ""))
        elif typ == "tool":
            name = str(ev.get("name") or "")
            call = pending_starts.pop(name, None) or ToolCall(name=name)
            call.args = str(ev.get("args") or call.args)
            call.result = str(ev.get("result") or "")
            result.tool_calls.append(call)
        elif typ == "tool_duration":
            name = str(ev.get("name") or "")
            try:
                dur = float(ev.get("duration") or 0.0)
            except (TypeError, ValueError):
                dur = 0.0
            for call in reversed(result.tool_calls):
                if call.name == name:
                    call.duration_s = dur
                    break
        elif typ == "usage":
            _accumulate_usage(result.usage, ev)
        elif typ == "metrics":
            result.metrics = dict(ev)
        elif typ == "error":
            result.error = str(ev.get("message") or "未知错误")
        elif typ in ("ask_request", "approval_request", "plan_request"):
            _respond(srv, typ, ev)
        elif typ == "needs_confirmation":
            result.error = "触发费用确认（评测配置应关闭 confirm_over_cost）"
        elif typ == "done":
            done = True

    try:
        with urllib.request.urlopen(req, timeout=read_timeout) as resp:
            for raw in resp:
                if time.monotonic() - started > timeout:
                    result.error = f"任务超时（{timeout:.0f}s）"
                    try:
                        _http_json(srv.base + "/v1/chat/stop", method="POST",
                                   payload={"stream_id": stream_id},
                                   token=srv.token, timeout=5.0)
                    except Exception:  # silent-except: 超时后停止失败不改变「已超时」结论
                        pass
                    break
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if not line:
                    if buf:
                        try:
                            handle(json.loads("\n".join(buf)))
                        except Exception:  # silent-except: 单帧坏数据跳过，不中断整条流
                            pass
                        buf = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("data:"):
                    buf.append(line[5:].lstrip())
                if done:
                    break
    except Exception as e:
        result.error = result.error or f"流读取失败：{type(e).__name__}: {e}"

    result.content = "".join(content)
    result.duration_s = round(time.monotonic() - started, 2)
    if not done and not result.error:
        result.error = "流意外结束（未收到 done）"
    # 未收到 tool 完成事件时，保留 start 记录，便于定位「工具卡住」
    if pending_starts and not result.error:
        result.error = "有工具调用未返回：" + ", ".join(sorted(pending_starts))
    return result


# ---------------------------------------------------------------------------
# 验收检查
# ---------------------------------------------------------------------------

class CheckError(ValueError):
    pass


def _resolve_in(workdir: Path, rel: str) -> Path:
    root = workdir.resolve()
    p = (workdir / str(rel)).resolve()
    try:
        p.relative_to(root)
    except ValueError:
        raise CheckError(f"路径越界：{rel}") from None
    return p


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _num_eq(a: Any, b: Any, tol: float = 1e-6) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return str(a).strip() == str(b).strip()


def _get_field(data: Any, field: str) -> Any:
    cur = data
    for part in str(field).split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise CheckError(f"字段不存在：{field}（缺 {part}）")
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise CheckError(f"字段不存在：{field}（列表下标 {part}）") from None
        else:
            raise CheckError(f"字段不存在：{field}（{part} 处不是对象/数组）")
    return cur


def run_check(spec: Dict[str, Any], workdir: Path, task: Task,
              turns: List[TurnResult]) -> CheckResult:
    typ = str(spec.get("type") or "")
    try:
        if typ == "file_exists":
            p = _resolve_in(workdir, spec["path"])
            ok = p.is_file()
            return CheckResult(typ, ok, f"{spec['path']} {'存在' if ok else '不存在'}", spec)
        if typ == "file_not_exists":
            p = _resolve_in(workdir, spec["path"])
            ok = not p.exists()
            return CheckResult(typ, ok, f"{spec['path']} {'不存在' if ok else '仍存在'}", spec)
        if typ == "dir_exists":
            p = _resolve_in(workdir, spec["path"])
            ok = p.is_dir()
            return CheckResult(typ, ok, f"{spec['path']} {'存在' if ok else '不存在'}", spec)
        if typ in ("file_contains", "file_not_contains", "file_regex",
                   "file_min_bytes", "file_max_bytes", "file_number_min"):
            p = _resolve_in(workdir, spec["path"])
            if not p.is_file():
                return CheckResult(typ, False, f"{spec['path']} 不存在", spec)
            if typ == "file_min_bytes":
                size = p.stat().st_size
                ok = size >= int(spec.get("bytes") or 0)
                return CheckResult(typ, ok, f"{size} bytes（要求 ≥{spec.get('bytes')}）", spec)
            if typ == "file_max_bytes":
                size = p.stat().st_size
                ok = size <= int(spec.get("bytes") or 0)
                return CheckResult(typ, ok, f"{size} bytes（要求 ≤{spec.get('bytes')}）", spec)
            text = _read_text(p)
            if typ == "file_number_min":
                m = re.search(r"-?\d+(?:\.\d+)?", text)
                if not m:
                    return CheckResult(typ, False, "文件里没有数字", spec)
                val = float(m.group(0))
                floor = float(spec.get("min") or 0)
                return CheckResult(typ, val >= floor, f"{val}（要求 ≥{floor}）", spec)
            if typ in ("file_contains", "file_not_contains"):
                needle = str(spec.get("text") or "")
                found = needle in text
                ok = found if typ == "file_contains" else not found
                return CheckResult(
                    typ, ok,
                    f"{'找到' if found else '未找到'}：{needle!r}"
                    + ("" if typ == "file_contains" else "（期望不存在）"),
                    spec,
                )
            pattern = str(spec.get("pattern") or "")
            ok = re.search(pattern, text, re.M) is not None
            return CheckResult(typ, ok, f"{'匹配' if ok else '未匹配'}：{pattern!r}", spec)
        if typ == "json_field":
            p = _resolve_in(workdir, spec["path"])
            if not p.is_file():
                return CheckResult(typ, False, f"{spec['path']} 不存在", spec)
            data = json.loads(_read_text(p))
            actual = _get_field(data, str(spec.get("field") or ""))
            expected = spec.get("equals")
            ok = _num_eq(actual, expected)
            return CheckResult(typ, ok, f"{spec.get('field')}={actual!r}（期望 {expected!r}）", spec)
        if typ in ("json_keys", "json_len"):
            p = _resolve_in(workdir, spec["path"])
            if not p.is_file():
                return CheckResult(typ, False, f"{spec['path']} 不存在", spec)
            data = json.loads(_read_text(p))
            if typ == "json_keys":
                if not isinstance(data, dict):
                    return CheckResult(typ, False, "JSON 顶层不是对象", spec)
                missing = [k for k in (spec.get("keys") or []) if k not in data]
                return CheckResult(typ, not missing, f"缺键：{missing}" if missing else "键齐全", spec)
            expected = int(spec.get("length") or 0)
            actual = len(data) if isinstance(data, (list, dict)) else -1
            return CheckResult(typ, actual == expected, f"长度 {actual}（期望 {expected}）", spec)
        if typ in ("csv_columns", "csv_row_count", "csv_row"):
            p = _resolve_in(workdir, spec["path"])
            if not p.is_file():
                return CheckResult(typ, False, f"{spec['path']} 不存在", spec)
            with open(p, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
            if not rows:
                return CheckResult(typ, False, "CSV 为空", spec)
            if typ == "csv_columns":
                expected = [str(x) for x in spec.get("columns") or []]
                actual = [str(x).strip() for x in rows[0]]
                ok = actual == expected
                return CheckResult(typ, ok, f"表头 {actual}（期望 {expected}）", spec)
            data_rows = rows[1:]
            if typ == "csv_row_count":
                expected = spec.get("rows")
                ok = len(data_rows) == int(expected)
                return CheckResult(typ, ok, f"{len(data_rows)} 行（期望 {expected}）", spec)
            idx = int(spec.get("index") or 0)
            expected = [str(x) for x in spec.get("values") or []]
            if idx < 0 or idx >= len(data_rows):
                return CheckResult(typ, False, f"行下标 {idx} 越界（共 {len(data_rows)} 行）", spec)
            actual = [str(x).strip() for x in data_rows[idx]]
            ok = len(actual) == len(expected) and all(
                _num_eq(a, b) for a, b in zip(actual, expected)
            )
            return CheckResult(typ, ok, f"第 {idx} 行 {actual}（期望 {expected}）", spec)
        if typ == "image_size":
            p = _resolve_in(workdir, spec["path"])
            if not p.is_file():
                return CheckResult(typ, False, f"{spec['path']} 不存在", spec)
            try:
                from PIL import Image
            except Exception:
                return CheckResult(typ, False, "缺少 Pillow，无法校验图片尺寸", spec)
            with Image.open(p) as im:
                w, h = im.size
            ok = w == int(spec.get("width") or 0) and h == int(spec.get("height") or 0)
            return CheckResult(typ, ok, f"{w}x{h}（期望 {spec.get('width')}x{spec.get('height')}）", spec)
        if typ == "image_min_size":
            p = _resolve_in(workdir, spec["path"])
            if not p.is_file():
                return CheckResult(typ, False, f"{spec['path']} 不存在", spec)
            try:
                from PIL import Image
            except Exception:
                return CheckResult(typ, False, "缺少 Pillow，无法校验图片尺寸", spec)
            with Image.open(p) as im:
                w, h = im.size
            min_w, min_h = int(spec.get("width") or 0), int(spec.get("height") or 0)
            ok = w >= min_w and h >= min_h
            return CheckResult(typ, ok, f"{w}x{h}（要求 ≥{min_w}x{min_h}）", spec)
        if typ == "xlsx_cells":
            p = _resolve_in(workdir, spec["path"])
            if not p.is_file():
                return CheckResult(typ, False, f"{spec['path']} 不存在", spec)
            try:
                from openpyxl import load_workbook
            except Exception:
                return CheckResult(typ, False, "缺少 openpyxl，无法校验 Excel", spec)
            wb = load_workbook(p, data_only=True, read_only=True)
            try:
                ws = wb[spec.get("sheet")] if spec.get("sheet") else wb[wb.sheetnames[0]]
                bad = []
                for item in spec.get("cells") or []:
                    got = ws[str(item.get("cell") or "A1")].value
                    if not _num_eq(got, item.get("equals")):
                        bad.append(f"{item.get('cell')}={got!r}（期望 {item.get('equals')!r}）")
                return CheckResult(typ, not bad, "；".join(bad) if bad else "单元格匹配", spec)
            finally:
                wb.close()
        if typ == "zip_entries":
            p = _resolve_in(workdir, spec["path"])
            if not p.is_file():
                return CheckResult(typ, False, f"{spec['path']} 不存在", spec)
            try:
                with zipfile.ZipFile(p) as zf:
                    names = set(zf.namelist())
            except Exception as e:
                return CheckResult(typ, False, f"不是有效 zip：{e}", spec)
            expected = [str(x) for x in spec.get("entries") or []]
            missing = [x for x in expected if x not in names]
            return CheckResult(typ, not missing,
                               f"缺条目：{missing}" if missing else f"{len(names)} 个条目齐全", spec)
        if typ == "command":
            argv = [str(x).replace("{python}", sys.executable) for x in spec.get("argv") or []]
            if not argv:
                return CheckResult(typ, False, "command 缺少 argv", spec)
            timeout = float(spec.get("timeout") or 60)
            try:
                proc = subprocess.run(
                    argv, cwd=str(workdir), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=timeout,
                )
            except Exception as e:
                return CheckResult(typ, False, f"命令执行失败：{type(e).__name__}: {e}", spec)
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip()[-300:]
            return CheckResult(typ, proc.returncode == 0,
                               f"exit {proc.returncode}：{tail}", spec)
        if typ == "tool_called":
            name = str(spec.get("name") or "")
            hit = any(c.name == name for t in turns for c in t.tool_calls)
            return CheckResult(typ, hit, f"{name} {'已调用' if hit else '未调用'}", spec)
        if typ == "no_tool_error":
            allow = set(spec.get("allow") or [])
            bad = []
            for t in turns:
                for c in t.tool_calls:
                    r = (c.result or "").lstrip()
                    if c.name in allow:
                        continue
                    if r.startswith("错误：") or r.startswith("错误:"):
                        bad.append(f"{c.name}: {r[:120]}")
            return CheckResult(typ, not bad, "无工具错误" if not bad else "；".join(bad), spec)
        if typ in ("final_contains", "final_not_contains", "final_regex"):
            text = turns[-1].content if turns else ""
            if typ in ("final_contains", "final_not_contains"):
                needle = str(spec.get("text") or "")
                found = needle in text
                ok = found if typ == "final_contains" else not found
                return CheckResult(
                    typ, ok,
                    f"{'找到' if found else '未找到'}：{needle!r}"
                    + ("" if typ == "final_contains" else "（期望不存在）"),
                    spec,
                )
            pattern = str(spec.get("pattern") or "")
            ok = re.search(pattern, text, re.M) is not None
            return CheckResult(typ, ok, f"{'匹配' if ok else '未匹配'}：{pattern!r}", spec)
    except CheckError as e:
        return CheckResult(typ, False, str(e), spec)
    except Exception as e:
        return CheckResult(typ, False, f"检查异常：{type(e).__name__}: {e}", spec)
    return CheckResult(typ, False, f"未知检查类型：{typ}", spec)


# ---------------------------------------------------------------------------
# 执行一个任务
# ---------------------------------------------------------------------------

def _cost(usage: Dict[str, int], model: str) -> float:
    try:
        import stats
        return float(stats.estimate_cost(usage, model))
    except Exception:
        return 0.0


def run_task(task: Task, run_root: Path, args: argparse.Namespace,
             repeat_index: int = 1) -> TaskResult:
    suffix = f"-r{repeat_index}" if repeat_index > 1 else ""
    run_dir = run_root / f"{task.id}{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)
    srv: Optional[ServerHandle] = None
    httpd = None
    http_thread = None
    base_url = ""
    result = TaskResult(id=task.id, category=task.category, ok=False,
                        repeat_index=repeat_index)
    started = time.monotonic()
    session_id = "eval-" + task.id + "-" + str(int(time.time() * 1000))[:8]
    messages: List[Dict[str, str]] = []
    all_usage: Dict[str, int] = {}
    try:
        srv = _start_server(task, run_dir, model_override=args.model,
                            startup_timeout=args.startup_timeout)
        httpd, base_url, http_thread = _start_local_http(task, run_dir)
        result.workdir = str(srv.workdir)
        result.server_log = str(srv.log_path)
        result.startup_s = srv.startup_s
        timeout = float(task.budget.get("max_seconds") or args.timeout)
        for idx, prompt in enumerate(task.turns, 1):
            if (task.fresh_session_each_turn and idx > 1) or idx in set(task.fresh_session_at):
                session_id = "eval-" + task.id + "-t" + str(idx) + "-" + str(int(time.time() * 1000))[:8]
                messages = []
            rendered = prompt.replace("{base_url}", base_url) if base_url else prompt
            messages.append({"role": "user", "content": rendered + EVAL_SUFFIX})
            stream_id = f"{session_id}-{idx}-{int(time.time() * 1000) % 100000}"
            turn = chat_turn(srv, session_id, stream_id, messages, task, timeout)
            turn.index = idx
            result.turns.append(turn)
            _accumulate_usage(all_usage, turn.usage)
            if turn.error:
                result.error = f"第 {idx} 轮：{turn.error}"
                break
            if turn.content.strip():
                messages.append({"role": "assistant", "content": turn.content})
            session_id = turn.session_id or session_id
            if args.verbose:
                print(f"    turn {idx}: {turn.duration_s:.1f}s, "
                      f"{len(turn.tool_calls)} tools, {len(turn.content)} chars")
        result.usage = all_usage
        result.cost_cny = round(_cost(all_usage, args.model or _model_name()), 4)
        result.tool_calls = sum(len(t.tool_calls) for t in result.turns)
        if not result.error:
            result.checks = [run_check(spec, srv.workdir, task, result.turns)
                             for spec in task.accept]
            result.ok = all(c.ok for c in result.checks)
    except EvalError as e:
        result.error = str(e)
    except Exception as e:
        result.error = f"{type(e).__name__}: {e}"
    finally:
        if srv is not None:
            _stop_server(srv.proc)
            _redact_config(srv.config_path)
        _stop_local_http(httpd, http_thread)
        result.duration_s = round(time.monotonic() - started, 2)
    return result


def _model_name() -> str:
    try:
        raw = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
        return str(raw.get("model") or "deepseek-flash")
    except Exception:
        return "deepseek-flash"


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

def _git_rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO),
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
    except Exception:
        return ""


def _summary(results: List[TaskResult]) -> Dict[str, Any]:
    run = [r for r in results if not r.skipped]
    passed = [r for r in run if r.ok]
    by_cat: Dict[str, Dict[str, int]] = {}
    for r in run:
        d = by_cat.setdefault(r.category, {"total": 0, "passed": 0})
        d["total"] += 1
        d["passed"] += 1 if r.ok else 0
    return {
        "total": len(run),
        "passed": len(passed),
        "failed": len(run) - len(passed),
        "skipped": len(results) - len(run),
        "pass_rate": round(len(passed) / len(run), 3) if run else 0.0,
        "cost_cny": round(sum(r.cost_cny for r in run), 4),
        "duration_s": round(sum(r.duration_s for r in run), 1),
        "by_category": by_cat,
    }


def _baseline_status(result: TaskResult, baseline: Optional[Dict[str, Any]]) -> str:
    if not baseline:
        return ""
    base = {str(r.get("id")): bool(r.get("ok")) for r in baseline.get("results") or []}
    if result.id not in base:
        return "NEW"
    if result.ok and not base[result.id]:
        return "FIXED"
    if not result.ok and base[result.id]:
        return "REGRESSED"
    return "SAME"


def write_report(results: List[TaskResult], run_root: Path, args: argparse.Namespace,
                 baseline: Optional[Dict[str, Any]] = None) -> Path:
    summary = _summary(results)
    meta = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "model": args.model or _model_name(),
        "python": sys.version.split()[0],
        "git": _git_rev(),
        "suite": args.suite,
        "repeat": args.repeat,
        "baseline": args.baseline or "",
    }
    payload = {
        "meta": meta,
        "summary": summary,
        "results": [asdict(r) for r in results],
    }
    score_json = run_root / "scorecard.json"
    score_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 智能体评测分数卡",
        "",
        f"- 时间：{meta['generated_at']}",
        f"- 模型：`{meta['model']}`",
        f"- Python：{meta['python']} · git：`{meta['git'] or 'unknown'}`",
        f"- 通过：**{summary['passed']}/{summary['total']}**"
        f"（{summary['pass_rate'] * 100:.1f}%）"
        + (f" · 跳过 {summary['skipped']}" if summary["skipped"] else ""),
        f"- 成本：¥{summary['cost_cny']:.2f} · 耗时：{summary['duration_s']:.1f}s",
        "",
        "| 任务 | 类别 | 结果 | 检查 | 工具 | 成本(¥) | 启动(s) | 耗时(s) | 备注 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        status = "SKIP" if r.skipped else ("PASS" if r.ok else "FAIL")
        if r.baseline_status:
            status += f"/{r.baseline_status}"
        checks = f"{sum(1 for c in r.checks if c.ok)}/{len(r.checks)}"
        note = (r.error or "").replace("\n", " ")[:80]
        lines.append(
            f"| `{r.id}` | {r.category} | {status} | {checks} | {r.tool_calls} | "
            f"{r.cost_cny:.2f} | {r.startup_s:.1f} | {r.duration_s:.1f} | {note} |"
        )
    lines.append("")
    if summary["by_category"]:
        lines.append("## 分类通过率")
        lines.append("")
        lines.append("| 类别 | 通过/总数 |")
        lines.append("|---|---|")
        for cat, d in sorted(summary["by_category"].items()):
            lines.append(f"| {cat} | {d['passed']}/{d['total']} |")
        lines.append("")
    failures = [r for r in results if not r.ok and not r.skipped]
    if failures:
        lines.append("## 失败详情")
        lines.append("")
        for r in failures:
            lines.append(f"### `{r.id}`")
            if r.error:
                lines.append(f"- 错误：{r.error}")
            for c in r.checks:
                if not c.ok:
                    lines.append(f"- {c.type}: {c.detail}")
            if r.server_log:
                lines.append(f"- 服务日志：`{r.server_log}`")
            lines.append("")
    score_md = run_root / "scorecard.md"
    score_md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 便于脚本/人直接找最新一份
    for name in ("scorecard.json", "scorecard.md"):
        try:
            shutil.copy2(run_root / name, DEFAULT_REPORT_DIR / f"latest-{name.split('.')[-1]}")
        except Exception:  # silent-except: latest 快捷方式是便利项，失败不影响分数卡本体
            pass
    return run_root


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_tasks(tasks: List[Task], verbose: bool = False) -> None:
    print(f"共 {len(tasks)} 个任务：")
    for t in tasks:
        print(f"  {t.id:28s} [{t.category}] {len(t.turns)} 轮 / "
              f"{len(t.accept)} 检查 / {len(t.setup)} 夹具")
        if verbose:
            if t.notes:
                print(f"      说明：{t.notes}")
            for i, turn in enumerate(t.turns, 1):
                print(f"      turn {i}: {turn[:100]}{'...' if len(turn) > 100 else ''}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="agent_eval",
        description="鲸语智能体评测台（真实模型 + 真实工具 + 程序化验收）",
    )
    parser.add_argument("--suite", default="golden", help="任务集（默认 golden）")
    parser.add_argument("--only", default="", help="只跑指定任务 id（逗号分隔）")
    parser.add_argument("--category", default="", help="只跑指定类别")
    parser.add_argument("--repeat", type=int, default=1, help="每个任务重复次数")
    parser.add_argument("--list", action="store_true", help="列出任务后退出")
    parser.add_argument("--dry-run", action="store_true", help="只校验任务定义，不调模型")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR), help="报告根目录")
    parser.add_argument("--baseline", default="", help="对照的 scorecard.json")
    parser.add_argument("--timeout", type=float, default=300.0, help="单轮超时秒数（可被任务 budget 覆盖）")
    parser.add_argument("--startup-timeout", type=float, default=90.0, help="服务启动超时秒数")
    parser.add_argument("--max-cny", type=float, default=0.0, help="总成本上限（0=不限）")
    parser.add_argument("--model", default="", help="覆盖模型名（默认读 config.json）")
    parser.add_argument("--verbose", action="store_true", help="打印每轮细节")
    args = parser.parse_args(argv)

    try:
        only = [x.strip() for x in args.only.split(",") if x.strip()] or None
        tasks = load_tasks(only=only, category=args.category)
    except EvalError as e:
        print(f"[任务定义错误] {e}")
        return 2

    if args.list:
        _print_tasks(tasks, verbose=True)
        return 0

    if args.dry_run:
        _print_tasks(tasks)
        cfg_ok = False
        try:
            raw = json.loads((REPO / "config.json").read_text(encoding="utf-8"))
            cfg_ok = bool(str(raw.get("api_key") or "").strip())
        except Exception:  # silent-except: dry-run 只需报告配置是否可读
            pass
        print(f"\nconfig.json：{'存在且已配置 api_key' if cfg_ok else '缺失或未配置 api_key'}")
        print(f"解释器：{sys.executable}")
        print("dry-run 通过：任务定义与夹具均有效（未调用模型）。")
        return 0 if cfg_ok else 2

    if not (REPO / "config.json").exists():
        print("[环境错误] 缺少 config.json；先启动一次主程序并配置模型。")
        return 2

    run_root = Path(args.report_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_root.mkdir(parents=True, exist_ok=True)
    baseline = None
    if args.baseline:
        try:
            baseline = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[警告] 基线读取失败，忽略：{e}")

    print(f"评测开始：{len(tasks)} 个任务 × {max(1, args.repeat)} 次")
    print(f"模型：{args.model or _model_name()} · 解释器：{sys.executable}")
    print(f"输出：{run_root}\n")

    results: List[TaskResult] = []
    spent = 0.0
    aborted = False
    for task in tasks:
        for i in range(1, max(1, args.repeat) + 1):
            if args.max_cny and spent >= args.max_cny:
                results.append(TaskResult(
                    id=task.id, category=task.category, ok=False,
                    skipped=True, repeat_index=i,
                    error=f"总成本已达上限 ¥{args.max_cny:.2f}",
                ))
                aborted = True
                continue
            label = f"{task.id}" + (f" (repeat {i})" if args.repeat > 1 else "")
            print(f"[{len(results) + 1}/{len(tasks) * max(1, args.repeat)}] {label} ...",
                  end="", flush=True)
            r = run_task(task, run_root, args, repeat_index=i)
            r.baseline_status = _baseline_status(r, baseline)
            results.append(r)
            spent += r.cost_cny
            print(f" {'PASS' if r.ok else ('SKIP' if r.skipped else 'FAIL')}"
                  f"  {r.duration_s:.1f}s  ¥{r.cost_cny:.2f}")
            if r.error:
                print(f"    {r.error[:200]}")
            for c in r.checks:
                if not c.ok:
                    print(f"    - {c.type}: {c.detail[:160]}")

    write_report(results, run_root, args, baseline)
    summary = _summary(results)
    print("\n" + "=" * 60)
    print(f"通过 {summary['passed']}/{summary['total']} "
          f"({summary['pass_rate'] * 100:.1f}%) · 成本 ¥{summary['cost_cny']:.2f} "
          f"· 耗时 {summary['duration_s']:.1f}s")
    print(f"分数卡：{run_root / 'scorecard.md'}")
    if aborted:
        print("[注意] 有任务因总成本上限被跳过")
    return 0 if summary["failed"] == 0 and summary["skipped"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
