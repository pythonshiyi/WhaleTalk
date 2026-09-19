"""对话引擎修复回归（审查收尾批次）。

覆盖：计划编辑回写历史、停止时副作用工具结果提示、active-client 线程隔离。
"""
import os
import sys
import threading

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import deepseek_client as dc  # noqa: E402


def test_apply_plan_edits_syncs_history_arguments():
    """用户改过的计划参数必须同时写回工作 tool_calls 与历史 assistant.tool_calls。"""
    tool_calls = [
        {"id": "a", "name": "write_file", "args": '{"path": "old.txt"}'},
        {"id": "b", "name": "run_command", "args": "echo old"},
    ]
    work_msg = {
        "role": "assistant",
        "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "write_file", "arguments": '{"path": "old.txt"}'}},
            {"id": "b", "type": "function", "function": {"name": "run_command", "arguments": "echo old"}},
        ],
    }
    edits = [
        {"id": "a", "args": {"path": "new.txt"}},
        {"id": "b", "args": "echo new"},
    ]
    dc._apply_plan_edits(tool_calls, edits, work_msg)
    assert tool_calls[0]["args"] == '{"path": "new.txt"}'
    assert tool_calls[1]["args"] == "echo new"
    # 历史 transcript 同步（否则模型下一轮看到的仍是旧参数）
    assert work_msg["tool_calls"][0]["function"]["arguments"] == '{"path": "new.txt"}'
    assert work_msg["tool_calls"][1]["function"]["arguments"] == "echo new"


def test_interrupted_result_warns_against_retry():
    msg = dc._interrupted_result("send_email")
    assert "send_email" in msg
    assert "副作用" in msg and "勿" in msg  # 明确提示不要直接重试


def test_active_client_is_thread_local():
    old = dc.get_active_client()

    class _C:
        pass

    a, b = _C(), _C()
    try:
        dc.set_active_client(a)
        assert dc.get_active_client() is a

        seen = {}

        def worker():
            dc._bind_thread_client(b)
            seen["bound"] = dc.get_active_client()

        t = threading.Thread(target=worker)
        t.start()
        t.join()
        assert seen["bound"] is b, "线程内绑定未生效"
        assert dc.get_active_client() is a, "其它线程的绑定污染了主线程"
    finally:
        dc.set_active_client(old)
