"""12 个 chat 特征化场景。

每个场景只依赖假模型脚本与真实 chat 编排；工具执行使用 get_date（零参数、
无副作用、结果可归一化）。计划/审批拒绝走 chat 的回调参数，不触碰真实 UI。
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx

from .harness import (
    Scenario,
    chunk,
    text_stream,
    tool_stream,
    usage_chunk,
)

SYSTEM = {"role": "system", "content": "你是鲸语特征化测试助手，回答简短。"}


def _messages(text: str) -> List[Dict[str, Any]]:
    return [dict(SYSTEM), {"role": "user", "content": text}]


def _pure_text(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [[
        chunk(reasoning="先想一下"),
        chunk(content="你好"),
        chunk(content="，我是鲸语。"),
        chunk(finish="stop"),
        usage_chunk(),
    ]]
    return _messages("你好"), {"tools_enabled": False}, script


def _single_tool(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [
        tool_stream([("call_date", "get_date", "{}")]),
        text_stream(["已获取日期。"]),
    ]
    return _messages("今天几号？"), {"tools_enabled": True, "max_tool_rounds": 4}, script


def _parallel_tools(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [
        tool_stream([
            ("call_a", "get_date", "{}"),
            ("call_b", "get_date", "{}"),
        ]),
        text_stream(["两个日期都拿到了。"]),
    ]
    return _messages("同时取两次当前时间。"), {"tools_enabled": True, "max_tool_rounds": 4}, script


def _smart_tools(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [
        tool_stream([("call_act", "activate_tools", '{"tools":["get_date"]}')]),
        text_stream(["已激活日期工具。"]),
    ]
    return (
        _messages("帮我处理一下。"),
        {"tools_enabled": True, "smart_tools": True, "max_tool_rounds": 4},
        script,
    )


def _stop(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    stop_event = threading.Event()

    def stream():
        yield chunk(content="开始生成")
        stop_event.set()
        yield chunk(content="这段不应出现")
        yield chunk(finish="stop")
        yield usage_chunk()

    return (
        _messages("说一段很长的话。"),
        {"tools_enabled": False, "stop_event": stop_event},
        [stream()],
    )


def _disconnect_retry(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [
        httpx.ConnectError("模拟断线"),
        text_stream(["重连成功。"]),
    ]
    return _messages("你好。"), {"tools_enabled": False}, script


def _plan_reject(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [
        tool_stream([("call_plan", "get_date", "{}")]),
        text_stream(["已按你的意思取消。"]),
    ]
    return (
        _messages("先给我一个计划再执行。"),
        {
            "tools_enabled": True,
            "max_tool_rounds": 4,
            "on_plan": lambda calls: (False, "用户拒绝计划"),
        },
        script,
    )


def _approval_reject(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [
        tool_stream([("call_appr", "get_date", "{}")]),
        text_stream(["操作未获批准，已停止。"]),
    ]
    return (
        _messages("执行这个操作。"),
        {
            "tools_enabled": True,
            "max_tool_rounds": 4,
            "on_approval": lambda name, args: (False, "用户拒绝审批"),
        },
        script,
    )


def _image_input(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    src = Path(__file__).resolve().parents[1] / "eval" / "fixtures" / "logo.png"
    dst = ws / "chat_input.png"
    shutil.copy2(src, dst)
    messages = [
        dict(SYSTEM),
        {"role": "user", "content": "这张图是什么？", "images": [str(dst)]},
    ]
    return messages, {"tools_enabled": False}, [text_stream(["收到图片。"])]


def _json_output(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [text_stream(['{"ok": true, "n": 1}'])]
    return (
        _messages('请只输出 JSON：{"ok": true, "n": 1}'),
        {"tools_enabled": False, "json_output": True},
        script,
    )


def _empty_retry(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [
        [],  # 第一次空响应
        text_stream(["重试后成功。"]),
    ]
    return _messages("你好。"), {"tools_enabled": False}, script


def _loop_guard(ws: Path, rec) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]:
    script = [
        tool_stream([("call_l1", "get_date", "{}")]),
        tool_stream([("call_l2", "get_date", "{}")]),
        tool_stream([("call_l3", "get_date", "{}")]),
        text_stream(["检测到重复调用，已换策略。"]),
    ]
    return (
        _messages("反复取时间，直到我让你停。"),
        {"tools_enabled": True, "max_tool_rounds": 6},
        script,
    )


SCENARIOS: Dict[str, Scenario] = {
    "pure_text": Scenario("pure_text", _pure_text),
    "single_tool": Scenario("single_tool", _single_tool),
    "parallel_tools": Scenario("parallel_tools", _parallel_tools, sort_tool_events=True),
    "smart_tools": Scenario("smart_tools", _smart_tools),
    "stop": Scenario("stop", _stop),
    "disconnect_retry": Scenario("disconnect_retry", _disconnect_retry),
    "plan_reject": Scenario("plan_reject", _plan_reject),
    "approval_reject": Scenario("approval_reject", _approval_reject),
    "image_input": Scenario("image_input", _image_input),
    "json_output": Scenario("json_output", _json_output),
    "empty_retry": Scenario("empty_retry", _empty_retry),
    "loop_guard": Scenario("loop_guard", _loop_guard),
}
