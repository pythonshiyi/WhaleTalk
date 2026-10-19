"""执行作业（job）记录的折叠与存取。

锁定的承诺：
1. 事件流能正确折叠成步骤（并行调用靠 id 区分，缺 id 时按同名最近一个合并）；
2. 失败判定与后端工具失败前缀一致；
3. 记录只留预览，不落完整参数/结果（可能含密钥或隐私）；
4. 任何 IO 失败都只返回 False，绝不抛错（记账不得影响生成）。
"""
import json

import pytest

import jobs


@pytest.fixture(autouse=True)
def tmp_dir(tmp_path):
    jobs.set_dir(str(tmp_path))
    yield tmp_path
    jobs.set_dir("")


def _ev(seq, event, **data):
    return (seq, event, data)


# ── 事件折叠 ────────────────────────────────────────────────

def test_fold_start_duration_done():
    events = [
        _ev(0, "tool_start", name="read_file", args={"path": "a.md"}, id="c1"),
        _ev(1, "tool_duration", name="read_file", duration="1.2", id="c1"),
        _ev(2, "tool", name="read_file", args={"path": "a.md"}, result="内容" * 10, id="c1"),
    ]
    steps = jobs.fold_events(events)
    assert len(steps) == 1
    s = steps[0]
    assert s["tool"] == "read_file"
    assert s["status"] == "done"
    assert s["duration"] == "1.2"
    assert s["failed"] is False
    assert s["result_chars"] == 20


def test_fold_parallel_calls_use_id():
    """同一工具并发两次：靠 id 区分，不能互相当成同一步。"""
    events = [
        _ev(0, "tool_start", name="web_search", args={"q": "a"}, id="c1"),
        _ev(1, "tool_start", name="web_search", args={"q": "b"}, id="c2"),
        _ev(2, "tool", name="web_search", args={"q": "b"}, result="结果B", id="c2"),
        _ev(3, "tool", name="web_search", args={"q": "a"}, result="结果A", id="c1"),
    ]
    steps = jobs.fold_events(events)
    assert [s["args_preview"] for s in steps] == ['{"q": "a"}', '{"q": "b"}']
    assert all(s["status"] == "done" for s in steps)


def test_fold_without_id_merges_by_name():
    events = [
        _ev(0, "tool_start", name="get_date", args={}),
        _ev(1, "tool", name="get_date", args={}, result="2026-10-19"),
    ]
    steps = jobs.fold_events(events)
    assert len(steps) == 1 and steps[0]["status"] == "done"


def test_fold_marks_failure_by_prefix():
    for prefix in ("错误：权限不足", "工具执行失败：boom", "工具参数不合法"):
        steps = jobs.fold_events([
            _ev(0, "tool_start", name="write_file", args={}, id="c"),
            _ev(1, "tool", name="write_file", args={}, result=prefix, id="c"),
        ])
        assert steps[0]["status"] == "failed", prefix
        assert steps[0]["failed"] is True


def test_fold_unfinished_steps_are_closed_not_left_running():
    """作业异常结束时不能留下 'running' —— 列表页会永远显示「执行中」。"""
    steps = jobs.fold_events([_ev(0, "tool_start", name="x", args={}, id="c")])
    assert steps[0]["status"] == "done"


def test_fold_ignores_garbage():
    assert jobs.fold_events(None) == []
    assert jobs.fold_events([None, "x", (1,), (2, "tool", "not-a-dict")]) == []


# ── 记录构造 ────────────────────────────────────────────────

class _FakeJob:
    def __init__(self, events, status="done", started=1000.0, ended=1012.5):
        self.id = "j1"
        self.sid = "s1"
        self.status = status
        self.events = events
        self.created = started
        self.finished = ended


def test_build_record_fields():
    rec = jobs.build_record(
        _FakeJob([_ev(0, "tool_start", name="a", id="c"), _ev(1, "tool", name="a", result="错误：x", id="c")]),
        user_text="帮我查一下" * 50,
        model="deepseek-chat",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    assert rec["id"] == "j1" and rec["session_id"] == "s1"
    assert rec["duration_s"] == 12.5
    assert rec["step_count"] == 1 and rec["failed_count"] == 1
    assert rec["usage"]["total_tokens"] == 15
    assert len(rec["user_text"]) <= 121          # 预览有上限
    assert rec["ended_at"] and rec["started_at"]


def test_build_record_never_stores_full_result():
    huge = "x" * 50000
    rec = jobs.build_record(_FakeJob([
        _ev(0, "tool_start", name="read_file", args={"content": huge}, id="c"),
        _ev(1, "tool", name="read_file", args={"content": huge}, result=huge, id="c"),
    ]))
    blob = json.dumps(rec, ensure_ascii=False)
    assert huge not in blob, "完整参数/结果不得落盘"
    assert rec["steps"][0]["result_chars"] == 50000   # 只记长度


# ── 存取 ───────────────────────────────────────────────────

def test_append_then_list_and_get():
    a = {"id": "j1", "session_id": "s1", "status": "done", "started_at": "2026-10-19 10:00:00"}
    b = {"id": "j2", "session_id": "s1", "status": "error", "started_at": "2026-10-19 10:05:00"}
    assert jobs.append_record(a) and jobs.append_record(b)

    rows = jobs.list_jobs()
    assert [r["id"] for r in rows] == ["j2", "j1"], "应按时间倒序"
    assert [r["id"] for r in jobs.list_jobs(status="error")] == ["j2"]
    assert [r["id"] for r in jobs.list_jobs(session_id="other")] == []
    assert jobs.get_job("j1")["status"] == "done"
    assert jobs.get_job("nope") is None


def test_append_rejects_record_without_id():
    assert jobs.append_record({"session_id": "s1"}) is False


def test_append_survives_unwritable_dir(tmp_path):
    jobs.set_dir(str(tmp_path / "file.txt"))     # 目录路径指向一个普通文件 → 必然失败
    (tmp_path / "file.txt").write_text("x", encoding="utf-8")
    assert jobs.append_record({"id": "j1"}) is False       # 不抛异常


def test_corrupted_line_is_skipped(tmp_path):
    jobs.set_dir(str(tmp_path))
    path = jobs._today_path()
    with open(path, "w", encoding="utf-8") as f:
        f.write('{"id":"ok","status":"done"}\n')
        f.write("{broken json\n")
    rows = jobs.list_jobs()
    assert [r["id"] for r in rows] == ["ok"]


def test_summary_counts_failures():
    s = jobs.summary([
        {"status": "done", "duration_s": 10, "failed_count": 0},
        {"status": "error", "duration_s": 5, "failed_count": 0},
        {"status": "done", "duration_s": 2.5, "failed_count": 1},
    ])
    assert s == {"count": 3, "failed": 2, "seconds": 17.5}
