"""chat 特征化测试的假模型与事件记录器。

目标：在**不访问真实模型**的前提下，把 DeepSeekClient.chat 的可观察行为
（请求布局、回调事件、工具调用、最终消息、usage）固化成 JSON 快照。
拆分 chat 前后逐字节对比；任何差异都必须解释。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx


# ---------------------------------------------------------------------------
# OpenAI 流式 chunk 的最小仿真
# ---------------------------------------------------------------------------

def chunk(content=None, reasoning=None, tool_calls=None, finish=None):
    delta = SimpleNamespace(
        content=content,
        reasoning_content=reasoning,
        tool_calls=tool_calls,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish)],
        usage=None,
    )


def usage_chunk(prompt=100, completion=10, cache_hit=0, cache_miss=0):
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        prompt_cache_hit_tokens=cache_hit,
        prompt_cache_miss_tokens=cache_miss,
    )
    return SimpleNamespace(choices=[], usage=usage)


def tool_delta(index: int, call_id: str, name: str, args: str):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


def text_stream(parts: List[str], finish: str = "stop",
                usage: bool = True) -> List[Any]:
    out = [chunk(content=p) for p in parts]
    out.append(chunk(finish=finish))
    if usage:
        out.append(usage_chunk())
    return out


def tool_stream(calls: List[Tuple[str, str, str]], finish: str = "tool_calls",
                usage: bool = True) -> List[Any]:
    """calls: [(call_id, name, args_json)]，同一轮并行。"""
    deltas = [
        tool_delta(i, cid, name, args)
        for i, (cid, name, args) in enumerate(calls)
    ]
    out = [chunk(tool_calls=deltas, finish=finish)]
    if usage:
        out.append(usage_chunk())
    return out


class ScriptedModel:
    """按脚本依次返回流；脚本项可以是异常（模拟断线）。"""

    def __init__(self, script: List[Any], recorder: "Recorder"):
        self.script = list(script)
        self.recorder = recorder
        self.requests: List[Dict[str, Any]] = []

    def create(self, **kwargs):
        self.requests.append(self.recorder.snapshot_request(kwargs))
        if not self.script:
            raise RuntimeError("特征化脚本已耗尽：chat 发起了比预期更多的模型请求")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeOpenAI:
    def __init__(self, model: ScriptedModel):
        self.chat = SimpleNamespace(completions=model)


# ---------------------------------------------------------------------------
# 事件记录与归一化
# ---------------------------------------------------------------------------

class Recorder:
    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)
        self.events: List[Dict[str, Any]] = []

    # -- 文本归一化：去掉时间/路径/解释器版本等天然漂移 --
    def norm(self, value: Any, limit: int = 400) -> str:
        text = value if isinstance(value, str) else str(value)
        variants = {
            str(self.workspace),
            str(self.workspace).replace("\\", "/"),
            str(self.workspace).replace("/", "\\"),
        }
        for v in sorted(variants, key=len, reverse=True):
            if v:
                text = text.replace(v, "{ws}")
        text = re.sub(r"\d{4}-\d{2}-\d{2}", "{date}", text)
        text = re.sub(r"\d{2}:\d{2}:\d{2}", "{time}", text)
        text = re.sub(r"Python:\s*\d+\.\d+\.\d+", "Python: {py}", text)
        text = re.sub(r"\b\d{1,3}(?:\.\d+)?\s*(?:GB|MB|KB)\b", "{size}", text)
        return text[:limit]

    def sha(self, value: Any) -> str:
        return hashlib.sha1(str(value).encode("utf-8", "replace")).hexdigest()[:12]

    def add(self, kind: str, **data) -> None:
        self.events.append({"type": kind, **data})

    # -- chat 回调 --
    def on_reasoning(self, text):
        self.add("reasoning", text=self.norm(text))

    def on_content(self, text):
        self.add("content", text=self.norm(text))

    def on_tool_start(self, name, args, cid=None):
        self.add("tool_start", name=name, args=self.norm(args), id=cid)

    def on_tool(self, name, args, result, cid=None):
        self.add("tool", name=name, args=self.norm(args, 120),
                 result=self.norm(result), id=cid)

    def on_tool_duration(self, name, duration, cid=None):
        self.add("tool_duration", name=name, duration="num", id=cid)

    def on_usage(self, usage):
        self.add("usage", **{k: int(usage.get(k, 0) or 0)
                             for k in ("prompt", "completion", "cache_hit", "cache_miss")})

    def on_metrics(self, metrics):
        payload = {
            "rounds": int(metrics.get("rounds", 0) or 0),
            "prompt": int(metrics.get("prompt", 0) or 0),
            "completion": int(metrics.get("completion", 0) or 0),
            "cache_hit": int(metrics.get("cache_hit", 0) or 0),
            "cache_miss": int(metrics.get("cache_miss", 0) or 0),
            "interrupted": bool(metrics.get("interrupted")),
        }
        self.add("metrics", **payload)

    def on_truncated(self, text):
        self.add("notice", text=self.norm(text), level="warn")

    def on_loop_guard(self, name, repeats):
        self.add("loop_guard", name=name, repeats=int(repeats))

    # -- 请求/消息快照 --
    def snapshot_message(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        content = msg.get("content")
        has_image = isinstance(content, list)
        if isinstance(content, list):
            text = " ".join(
                str(b.get("text") or "") for b in content if isinstance(b, dict)
            )
        else:
            text = str(content or "")
        norm_text = self.norm(text)
        out = {
            "role": msg.get("role"),
            "content": norm_text[:200],
            "content_sha": self.sha(norm_text),
            "content_len": len(norm_text),
            "has_image": has_image,
        }
        tcs = msg.get("tool_calls") or []
        if tcs:
            out["tool_calls"] = [
                {
                    "id": tc.get("id"),
                    "name": (tc.get("function") or {}).get("name"),
                    "args": self.norm((tc.get("function") or {}).get("arguments") or "", 120),
                }
                for tc in tcs
            ]
        if msg.get("tool_call_id"):
            out["tool_call_id"] = msg["tool_call_id"]
        if msg.get("reasoning_content"):
            out["has_reasoning"] = True
        return out

    def snapshot_request(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        names = [
            (t.get("function") or {}).get("name")
            for t in (kwargs.get("tools") or [])
            if isinstance(t, dict)
        ]
        tools = (
            sorted(n for n in names if n)
            if len(names) <= 12
            else {"count": len(names),
                  "has": [n for n in ("get_date", "activate_tools", "write_file")
                          if n in names]}
        )
        return {
            "model": kwargs.get("model"),
            "messages": [self.snapshot_message(m) for m in kwargs.get("messages") or []],
            "tools": tools,
            "stream": bool(kwargs.get("stream")),
            "max_tokens": kwargs.get("max_tokens"),
            "temperature": kwargs.get("temperature"),
            "top_p": kwargs.get("top_p"),
            "reasoning_effort": kwargs.get("reasoning_effort"),
            "seed": kwargs.get("seed"),
            "stop": kwargs.get("stop"),
            "logprobs": kwargs.get("logprobs"),
            "tool_choice": kwargs.get("tool_choice"),
            "extra_body": kwargs.get("extra_body"),
            "response_format": kwargs.get("response_format"),
        }


# ---------------------------------------------------------------------------
# 场景执行
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    name: str
    build: Callable[[Path, Recorder], Tuple[List[Dict[str, Any]], Dict[str, Any], List[Any]]]
    sort_tool_events: bool = False


def canonical(result: Dict[str, Any], sort_tools: bool = False) -> Dict[str, Any]:
    """并行工具回调按 id 排序，消除线程完成顺序带来的非语义差异。"""
    data = json.loads(json.dumps(result, ensure_ascii=False))
    if sort_tools:
        events = data.get("events") or []
        tool_events = [e for e in events if e.get("type") in
                       ("tool_start", "tool", "tool_duration")]
        if tool_events:
            others = [e for e in events if e not in tool_events]
            order = {"tool_start": 0, "tool": 1, "tool_duration": 2}
            tool_events.sort(key=lambda e: (str(e.get("id") or ""),
                                            order.get(e.get("type"), 9),
                                            e.get("name") or ""))
            # 并行场景：工具事件整体前后没有内容交错，直接拼回
            data["events"] = tool_events + others
    return data


def run_scenario(scenario: Scenario):
    import deepseek_client as dc
    import permissions

    workspace = Path(permissions.WORKSPACE_DIR)
    workspace.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(workspace)
    messages, flags, script = scenario.build(workspace, recorder)
    model = ScriptedModel(script, recorder)
    client = dc.DeepSeekClient(
        api_key="test-key",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
    )
    client.client = FakeOpenAI(model)
    client._fallback_client = lambda: None

    generated: List[Dict[str, Any]] = []
    kwargs: Dict[str, Any] = {
        # 固定输出预算，隔离其它测试对全局 config 的修改
        "max_tokens": 16384,
        "on_reasoning": recorder.on_reasoning,
        "on_content": recorder.on_content,
        "on_tool_start": recorder.on_tool_start,
        "on_tool": recorder.on_tool,
        "on_tool_duration": recorder.on_tool_duration,
        "on_usage": recorder.on_usage,
        "on_metrics": recorder.on_metrics,
        "on_truncated": recorder.on_truncated,
        "on_loop_guard": recorder.on_loop_guard,
        "new_messages_out": generated,
    }
    kwargs.update(flags)
    ok = client.chat(messages, **kwargs)
    result = {
        "scenario": scenario.name,
        "ok": bool(ok),
        "events": recorder.events,
        "requests": model.requests,
        "new_messages": [recorder.snapshot_message(m) for m in generated],
    }
    return canonical(result, scenario.sort_tool_events)


def golden_dir() -> Path:
    return Path(__file__).resolve().parent / "golden"


def load_golden(name: str) -> Optional[Dict[str, Any]]:
    p = golden_dir() / f"{name}.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def save_golden(name: str, data: Dict[str, Any]) -> Path:
    golden_dir().mkdir(parents=True, exist_ok=True)
    p = golden_dir() / f"{name}.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def record_enabled() -> bool:
    return os.environ.get("CHAT_RECORD") == "1"
