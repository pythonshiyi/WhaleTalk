"""流式对话「后台作业」回归测试：切页 / 关标签 / 多标签页都不打断生成。

背景（修复的 bug）：
- 旧实现把生成绑在 HTTP 请求线程上，SSE 写失败即 stop_event.set()；前端切页卸载
  组件时 abort 请求，且 finish 被 alive=false 短路 → 输出被截断、会话保存失败。
- 现在生成在独立作业线程里跑，事件写入内存缓冲，HTTP 处理器只做订阅者；订阅者
  掉线不影响作业，无在线订阅者时由作业兜底落盘。

覆盖：
- _ChatJob 缓冲 / 终态语义
- _stop_chat 命中作业停止句柄
- _chat_jobs_gc 回收
- 客户端掉线后作业仍跑完（不被打断）
- 同 stream_id 的重复请求订阅同一作业（不重复起生成）
- 兜底落盘：无订阅者才写、只追加本轮、有订阅者时跳过
- chat() 暴露 new_messages_out（断连兜底所需）
"""
import inspect
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402
import deepseek_client  # noqa: E402


class _FakeHandler(api_server._Handler):
    """借壳取用 _Handler 的真实方法（_stream_job_to_client 等），不跑 HTTP 栈。"""

    def __init__(self, body, fail_after=0):
        self._body = body
        self.sent = []
        self.fail_after = fail_after

    def _read_body(self, max_len=None):
        return self._body

    def _valid_messages(self, body):
        return api_server._Handler._valid_messages(self, body)

    def _sse_start(self):
        pass

    def _sse_end(self):
        pass

    def _sse_comment(self):
        return False  # 空转即失败：模拟客户端掉线

    def _sse_send(self, ev, data):
        if len(self.sent) >= self.fail_after:
            return False
        self.sent.append((ev, data))
        return True


def _drop(job_id):
    with api_server._CHAT_JOBS_LOCK:
        api_server._CHAT_JOBS.pop(job_id, None)


# ── 作业本体 ─────────────────────────────────────────

def test_chat_job_buffer_and_terminal_state():
    job = api_server._ChatJob("st1", "sid1", "gw1")
    job.emit("content", {"text": "a"})
    job.emit("content", {"text": "b"})
    with job.cond:
        assert [(s, e) for s, e, _ in job.events] == [(0, "content"), (1, "content")]
    assert job.status == "running"
    job.finish("done")
    assert job.status == "done" and job.finished is not None
    # 终态不可被后续 finish 覆盖（避免 error 盖掉 done）
    job.finish("error")
    assert job.status == "done"


def test_stop_chat_sets_job_stop_event():
    job = api_server._ChatJob("st-stop", "sid-stop", "gw-stop")
    with api_server._CHAT_JOBS_LOCK:
        api_server._CHAT_JOBS["st-stop"] = job
    try:
        r = api_server._stop_chat({"stream_id": "st-stop"})
        assert r["ok"] and r["stopped"] >= 1
        assert job.stop_event.is_set()
    finally:
        _drop("st-stop")


def test_stop_chat_no_match_keeps_job_running():
    job = api_server._ChatJob("st-keep", "sid-keep", "gw-keep")
    with api_server._CHAT_JOBS_LOCK:
        api_server._CHAT_JOBS["st-keep"] = job
    try:
        api_server._stop_chat({"stream_id": "someone-else"})
        assert not job.stop_event.is_set()
    finally:
        _drop("st-keep")


def test_chat_jobs_gc_removes_finished_after_ttl():
    job = api_server._ChatJob("st-gc", "sid", "gw")
    job.finish("done")
    job.finished = time.time() - api_server._CHAT_JOB_TTL - 1
    with api_server._CHAT_JOBS_LOCK:
        api_server._CHAT_JOBS["st-gc"] = job
    api_server._chat_jobs_gc()
    with api_server._CHAT_JOBS_LOCK:
        assert "st-gc" not in api_server._CHAT_JOBS


def test_chat_jobs_gc_reaps_zombie_running_job_with_dead_thread():
    """兜底回收：线程已死但状态仍是 running 的「僵尸作业」必须可回收。

    bug：GC 条件原为 `status != "running"`，作业若在线程体内被 BaseException
    越过 `except Exception` 打断（finish 从未执行），将永远停在
    status="running"/finished=None → 永不可回收；且同 stream_id 的后续请求会被
    当作订阅该作业，`_stream_job_to_client` 见到 running 就无限 cond.wait，
    导致该会话此后每次重发都永久挂起。线程已死时回放缓冲已无意义。
    """
    job = api_server._ChatJob("st-zombie", "sid", "gw")

    class _DeadThread:
        def is_alive(self):
            return False

    job.thread = _DeadThread()
    assert job.status == "running" and job.finished is None  # 构造出僵尸态
    with api_server._CHAT_JOBS_LOCK:
        api_server._CHAT_JOBS["st-zombie"] = job
    api_server._chat_jobs_gc()
    with api_server._CHAT_JOBS_LOCK:
        assert "st-zombie" not in api_server._CHAT_JOBS, "僵尸作业未被兜底回收"


def test_chat_jobs_gc_keeps_running_job_with_live_thread():
    """反向保护：线程仍活着且未超时的 running 作业**不得**被误回收（否则正在
    进行的生成会被并发请求打断/重复起作业）。"""
    job = api_server._ChatJob("st-live", "sid", "gw")

    class _LiveThread:
        def is_alive(self):
            return True

    job.thread = _LiveThread()
    with api_server._CHAT_JOBS_LOCK:
        api_server._CHAT_JOBS["st-live"] = job
    try:
        api_server._chat_jobs_gc()
        with api_server._CHAT_JOBS_LOCK:
            assert "st-live" in api_server._CHAT_JOBS, "活着的作业被误回收"
    finally:
        _drop("st-live")


def test_run_chat_job_thread_finalizes_job_on_base_exception(monkeypatch):
    """`_run_chat_job_thread` 必须保证任何异常路径都收口成终态。

    直接对真实方法注入一个抛 BaseException 的 chat 实现，验证 finally 兜底把
    作业置为终态（而非停留在 running）——这是「stream_id 不再永久挂起」的根因修复。
    """
    job = api_server._ChatJob("st-base", "sid-base", "gw-base")

    class _Boom(BaseException):
        pass

    class _ClientStub:
        def chat(self, *a, **kw):
            raise _Boom("模拟 SystemExit/KeyboardInterrupt 级异常")

    monkeypatch.setattr(api_server._Handler, "_client_from_cfg",
                        lambda self, body: (_ClientStub(), {}))
    monkeypatch.setattr(api_server._Handler, "_budget_block", lambda self, cfg: "")
    monkeypatch.setattr(api_server, "_compress_messages", lambda msgs, *a, **k: msgs)

    h = _FakeHandler({})
    body = {"messages": [{"role": "user", "content": "hi"}], "stream_id": "st-base"}
    try:
        h._run_chat_job_thread(job, body, [{"role": "user", "content": "hi"}])
    except BaseException:
        # 异常可以继续向外抛（线程会打印），但作业状态必须是终态
        pass
    assert job.status != "running", "BaseException 后作业仍停在 running（会永久挂起该 stream_id）"
    assert job.finished is not None


# ── 核心：掉线不打断 ─────────────────────────────────

def test_disconnect_does_not_stop_job(monkeypatch):
    """客户端第一帧就掉线，作业仍跑完全部事件（不被截断）。"""
    finished = threading.Event()
    emitted = []
    seen = {}

    def fake_run(self, job, body, messages):
        try:
            seen["id"] = job.id
            job.emit("content", {"text": "partial"})
            emitted.append("content")
            time.sleep(0.2)  # 客户端在此期间掉线
            job.emit("content", {"text": "rest"})
            emitted.append("content")
            job.emit("done", {})
            emitted.append("done")
            job.finish("done")
        finally:
            finished.set()

    monkeypatch.setattr(api_server._Handler, "_run_chat_job_thread", fake_run)
    body = {"messages": [{"role": "user", "content": "hi"}],
            "stream_id": "st-detach", "gw_session": "gw-detach"}
    h = _FakeHandler(body, fail_after=0)
    api_server._Handler._handle_chat_stream(h)
    try:
        assert finished.wait(3.0), "作业线程未跑完（被掉线打断？）"
        assert seen["id"] == "st-detach"
        assert emitted == ["content", "content", "done"], "作业被掉线截断"
        with api_server._CHAT_JOBS_LOCK:
            job = api_server._CHAT_JOBS.get("st-detach")
        assert job is not None
        assert job.status == "done"
        assert job.subscribers == 0
    finally:
        _drop("st-detach")


def test_chat_job_retains_buffer_for_replay_until_gc():
    """完成后缓冲须保留（供 TTL 内重连回放），由 GC 负责最终回收。"""
    job = api_server._ChatJob("st-trim", "sid", "gw")
    job.emit("content", {"text": "x"})
    job.subscribers = 1
    job.finish("done")
    job.trim_if_unused()
    assert job.events, "仍有订阅者时不得释放缓冲（晚到订阅者会漏事件）"
    job.subscribers = 0
    job.trim_if_unused()
    assert job.events, "完成后须保留缓冲以供 TTL 内回放（旧实现会误清）"


def test_duplicate_stream_id_subscribes_same_job(monkeypatch):
    """同一 stream_id 的第二次请求只订阅，不重复启动生成。"""
    started = []

    def fake_run(self, job, body, messages):
        started.append(job.id)
        job.emit("content", {"text": "x"})  # 让订阅者立刻写失败并掉线（快速返回）
        job.stop_event.wait(30)
        job.finish("done")

    monkeypatch.setattr(api_server._Handler, "_run_chat_job_thread", fake_run)
    body = {"messages": [{"role": "user", "content": "hi"}],
            "stream_id": "st-dup", "gw_session": "gw-dup"}
    api_server._Handler._handle_chat_stream(_FakeHandler(body, fail_after=0))
    api_server._Handler._handle_chat_stream(_FakeHandler(body, fail_after=0))
    try:
        assert started == ["st-dup"], "同名作业被重复启动"
        with api_server._CHAT_JOBS_LOCK:
            job = api_server._CHAT_JOBS.get("st-dup")
        assert job is not None
        job.stop_event.set()
        if job.thread:
            job.thread.join(3)
    finally:
        _drop("st-dup")


# ── 兜底落盘 ─────────────────────────────────────────

def test_save_job_turn_appends_only_new_turn(monkeypatch):
    captured = {}

    def fake_save(body):
        captured.update(body)
        return "sid", None

    monkeypatch.setattr(api_server, "_save_session_detached", fake_save)
    job = api_server._ChatJob("st-save", "sid-save", "gw")
    persist_base = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old-a"},
        {"role": "user", "content": "new"},
    ]
    generated = [{"role": "assistant", "content": "answer"}]
    api_server._save_job_turn(job, {"session_name": ""}, persist_base, generated)
    assert captured["id"] == "sid-save"
    assert captured["append"] is True
    assert captured["messages"] == [
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "answer"},
    ]


class _FakeClient:
    """记录回调触发，并把本轮新增消息放进 new_messages_out。"""

    def chat(self, messages, **kwargs):
        cb = kwargs.get("on_content")
        if cb:
            cb("hello")
        out = kwargs.get("new_messages_out")
        if out is not None:
            out.append({"role": "assistant", "content": "hello"})
        return True


def _stub_pipeline(monkeypatch):
    monkeypatch.setattr(api_server._Handler, "_client_from_cfg",
                        lambda self, body: (_FakeClient(), {}))
    monkeypatch.setattr(api_server._Handler, "_budget_block", lambda self, cfg: None)
    monkeypatch.setattr(api_server._Handler, "_chat_kwargs", lambda self, body, cfg: {})
    monkeypatch.setattr(api_server._Handler, "_quiet_mode", lambda self, body, cfg: False)
    monkeypatch.setattr(api_server._Handler, "_inject_system_messages",
                        lambda self, m, cfg, pure, quiet: (m, ""))
    monkeypatch.setattr(api_server, "_compress_messages", lambda m, cfg, client, *a, **k: (m, None))
    monkeypatch.setattr(api_server, "_record_usage", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_tool_bookkeeping", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_record_tasklog", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_clear_auto_checkpoint", lambda: None)
    monkeypatch.setattr(api_server, "_notify_completed", lambda ok=True: None)
    # 防止污染真实数据目录
    monkeypatch.setattr(api_server, "_set_current_task", lambda title: None)
    monkeypatch.setattr(api_server, "_LAST_TOOL_CHAIN", [])


def test_fallback_save_runs_when_no_subscriber(monkeypatch):
    _stub_pipeline(monkeypatch)
    saved = []
    monkeypatch.setattr(api_server, "_save_job_turn",
                        lambda job, body, base, gen: saved.append(job.id))
    h = _FakeHandler.__new__(_FakeHandler)
    job = api_server._ChatJob("st-fb", "sid-fb", "gw-fb")
    body = {"messages": [{"role": "user", "content": "hi"}]}
    api_server._Handler._run_chat_job_thread(h, job, body, [{"role": "user", "content": "hi"}])
    assert saved == ["st-fb"], "无订阅者时应兜底落盘"
    assert job.status == "done"


def test_fallback_save_skipped_when_subscriber_present(monkeypatch):
    _stub_pipeline(monkeypatch)
    saved = []
    monkeypatch.setattr(api_server, "_save_job_turn",
                        lambda job, body, base, gen: saved.append(job.id))
    h = _FakeHandler.__new__(_FakeHandler)
    job = api_server._ChatJob("st-online", "sid-online", "gw-online")
    job.subscribers = 1  # 有在线订阅者：由前端保存，避免重复
    body = {"messages": [{"role": "user", "content": "hi"}]}
    api_server._Handler._run_chat_job_thread(h, job, body, [{"role": "user", "content": "hi"}])
    assert saved == [], "有订阅者时不应兜底落盘（避免与前端保存重复）"


# ── 引擎契约 ─────────────────────────────────────────

def test_chat_exposes_new_messages_out():
    params = inspect.signature(deepseek_client.DeepSeekClient.chat).parameters
    assert "new_messages_out" in params


def test_stop_chat_without_key_requires_explicit_all():
    """空 body 不得停止全部运行中作业；显式 all:true 才停全部。"""
    jobs = []
    for jid in ("st-stopall-a", "st-stopall-b"):
        job = api_server._ChatJob(jid, "sid-" + jid, "gw-" + jid)
        with api_server._CHAT_JOBS_LOCK:
            api_server._CHAT_JOBS[jid] = job
        jobs.append((jid, job))
    try:
        r = api_server._stop_chat({})
        assert r["stopped"] == 0
        assert all(not j.stop_event.is_set() for _, j in jobs), "无 key 时误停全部"
        r2 = api_server._stop_chat({"all": True})
        assert r2["stopped"] >= 2
        assert all(j.stop_event.is_set() for _, j in jobs)
    finally:
        for jid, _ in jobs:
            _drop(jid)
