"""对话引擎修复回归（审查收尾批次）。

覆盖：计划编辑回写历史、停止时副作用工具结果提示、active-client 线程隔离。
"""
import os
import sys
import threading
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import deepseek_client as dc  # noqa: E402


def _tool_call_chunk(name, args, cid):
    fn = types.SimpleNamespace(name=name, arguments=args)
    tc = types.SimpleNamespace(index=0, id=cid, function=fn)
    delta = types.SimpleNamespace(content=None, reasoning_content=None, tool_calls=[tc])
    choice = types.SimpleNamespace(delta=delta, finish_reason="tool_calls")
    return types.SimpleNamespace(choices=[choice], usage=None)


def _text_chunk(text, finish="stop"):
    delta = types.SimpleNamespace(content=text, reasoning_content=None, tool_calls=None)
    choice = types.SimpleNamespace(delta=delta, finish_reason=finish)
    return types.SimpleNamespace(choices=[choice], usage=None)


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


def test_loop_guard_recovers_instead_of_silent_stop(monkeypatch):
    """重复调用命中防护时应「拦截 + 回灌换策略提示」继续，而不是静默 return True 结束。"""
    client = dc.DeepSeekClient(api_key="sk-test", base_url=dc.DEFAULT_BASE_URL)
    rounds = [
        [_tool_call_chunk("get_date", "{}", "t1")],
        [_tool_call_chunk("get_date", "{}", "t2")],
        [_tool_call_chunk("get_date", "{}", "t3")],  # 连续第 3 轮相同 → 命中防护、拦截
        [_text_chunk("已换策略完成")],                # 模型纠正后正常收尾
    ]
    calls = {"n": 0}

    def fake_create(kwargs, attempts=3, stop_event=None):
        i = calls["n"]
        calls["n"] += 1
        return iter(rounds[min(i, len(rounds) - 1)])

    monkeypatch.setattr(client, "_create_with_retry", fake_create)
    guard = []
    msgs = [{"role": "user", "content": "做个任务"}]
    ok = client.chat(
        msgs, pure_chat=False, tools_enabled=True,
        on_content=lambda _t: None,
        on_loop_guard=lambda name, n: guard.append((name, n)),
    )
    assert ok is True, "防护命中后应继续让模型换策略，而不是静默结束"
    assert guard and guard[0][0] == "get_date"
    assert calls["n"] == 4, "拦截后应再发起一轮（共 4 轮），而非直接终止"
    assert any("本轮未执行" in str(m.get("content") or "") for m in msgs), \
        "拦截应以工具结果回灌给模型，说明为何未执行"

