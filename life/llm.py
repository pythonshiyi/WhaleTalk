"""生命循环的最小模型调用层。

只做三件事：拿活动客户端、发一次无工具的低思考请求、解析 JSON。
不在这里写记忆/进化/目标；那些由调用方决定。
"""
from __future__ import annotations

import json
import re


class LLMUnavailable(RuntimeError):
    pass


def available() -> bool:
    try:
        import deepseek_client as dc
        return dc.get_active_client() is not None
    except Exception:
        return False


def call(prompt, max_tokens=700, thinking="low", json_output=True) -> tuple:
    """返回 ``(text, usage, model, error)``；失败时 text 为空、error 为原因。"""
    try:
        import deepseek_client as dc
        client = dc.get_active_client()
    except Exception as e:  # noqa: BLE001
        return "", None, "", f"客户端不可用: {e}"
    if client is None:
        return "", None, "", "未配置 API Key"
    parts, usage = [], []
    try:
        client.chat(
            [{"role": "user", "content": str(prompt)}],
            thinking=str(thinking or "low"),
            max_tokens=max(64, int(max_tokens or 700)),
            tools_enabled=False,
            smart_tools=False,
            json_output=bool(json_output),
            on_content=lambda t: parts.append(t),
            on_usage=lambda u: usage.append(u),
        )
    except Exception as e:  # noqa: BLE001
        return "", None, str(getattr(client, "model", "") or ""), f"{type(e).__name__}: {e}"
    text = "".join(parts).strip()
    u = usage[-1] if usage else None
    return text, u, str(getattr(client, "model", "") or ""), ""


def parse_json(text):
    """从模型输出里取第一个 JSON 对象；失败返回 None。"""
    s = str(text or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        s = s[a:b + 1]
    for candidate in (s, re.sub(r",\s*([}\]])", r"\1", s)):
        try:
            obj = json.loads(candidate)
            return obj if isinstance(obj, dict) else None
        except Exception:
            continue
    return None
