# -*- coding: utf-8 -*-
"""🔧 系统与基础 —— 首批拆分工具域（P0-1 巨石拆分）。

从 deepseek_client.py 原样迁出的工具（装饰器 + 函数体），注册进 toolkit
模块级注册表（跨模块共享单例）。共享符号策略（加载顺序契约见
deepseek_client.py 中 `from agent_tools import *` 处的注释）：

  - 无循环的独立模块（net_utils）顶层直接 import；
  - deepseek_client 内部常量（WEATHER_TIMEOUT）在 deepseek_client 执行到
    `from agent_tools import *` 时已定义，可安全 from-import。
"""

import logging
from datetime import datetime
from urllib.parse import quote

from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
from net_utils import _http_client
from deepseek_client import WEATHER_TIMEOUT  # 契约：主文件共享基建先于本包导入完成


@tool(
        {
            "type": "function",
            "function": {
                "name": "get_date",
                "description": "获取当前日期、具体时间与本地时区（如 2026-08-03 15:30:00 CST）",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    groups=['🔧 系统与基础'],
    phrases='获取当前日期/时间',
    preactivate=(('几号', '现在几点', '日期时间', '今天是几号'),),
)
def get_date():
    """获取当前日期、具体时间与本地时区。"""
    now = datetime.now().astimezone()
    tz_name = now.tzinfo.tzname(now) if now.tzinfo else "?"
    return f"{now:%Y-%m-%d %H:%M:%S} {tz_name}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "获取指定城市某日期的天气（date 仅支持今天与近 3 天预报）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {"type": "string", "description": "城市名称"},
                        "date": {"type": "string", "description": "日期，格式 YYYY-mm-dd（今天或未来 3 天内；留空为今天）"},
                    },
                    "required": ["location", "date"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='查询天气',
    preactivate=(('天气', '气温', '台风', '预报'),),
)
def get_weather(location, date):
    """查询城市天气。date（YYYY-mm-dd）传给 wttr.in（仅支持今天与近 3 天预报，
    更早/更晚的日期返回错误，避免把过期预报当真）。"""
    loc = str(location or "").strip()
    if not loc:
        return "错误：location 必填（城市名称）"
    d = str(date or "").strip()
    base = f"{loc} {d}" if d else loc
    url = f"https://wttr.in/{quote(loc)}?format=j1&lang=zh"
    if d:
        url += f"&date={quote(d)}"
    try:
        resp = _http_client().get(url, timeout=WEATHER_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        cur = (data.get("current_condition") or [{}])[0]
        temp = cur.get("temp_C") or "?"
        desc = cur.get("lang_zh") or []
        if desc and desc[0].get("value"):
            text = desc[0]["value"]
        else:
            text = ((cur.get("weatherDesc") or [{}])[0].get("value") or "未知")
        return f"{base} 天气：{text}，气温 {temp}°C"
    except Exception as e:
        # 不返回编造的"模拟数据"——AI 会把假天气当真用于决策
        logging.warning("天气查询失败: %s", e)
        return f"错误：天气查询失败（{e}），请稍后重试或改用其他方式获取天气"
