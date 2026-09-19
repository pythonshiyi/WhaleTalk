"""🔧 系统与基础 —— 首批拆分工具域（P0-1 巨石拆分）。

从 deepseek_client.py 原样迁出的工具（装饰器 + 函数体），注册进 toolkit
模块级注册表（跨模块共享单例）。共享符号策略：

  - 无循环的独立模块（net_utils / shared）顶层直接 import；
  - 本模块已无任何 deepseek_client 回指（P1-3：阈值常量下沉 shared 后
    最后一个 dc 依赖被消除），可独立导入。
"""

import logging
import re
from datetime import datetime
from urllib.parse import quote

from net_utils import _http_client
from shared import WEATHER_TIMEOUT  # 天气请求超时（工具域阈值，见 shared.py）
from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export


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
                        "date": {"type": "string", "description": "可选：日期 YYYY-mm-dd（今天或未来 3 天内；留空为今天）"},
                    },
                    "required": ["location"],
                },
            },
        },
    groups=['🔧 系统与基础'],
    phrases='查询天气',
    preactivate=(('天气', '气温', '台风', '预报'),),
)
def get_weather(location, date=""):
    """查询城市天气：今天用实时观测，未来 0-3 天用 wttr.in 预报数组。

    注意：wttr.in 的 j1 接口对 `date=` 查询参数一律返回 500，日期筛选必须由本地
    在 weather[] 预报数组里完成（旧实现给 URL 拼 &date= → 传日期必失败）。
    """
    loc = str(location or "").strip()
    if not loc:
        return "错误：location 必填（城市名称）"
    d = str(date or "").strip()
    if d and not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
        return "错误：date 格式应为 YYYY-mm-dd"
    url = f"https://wttr.in/{quote(loc)}?format=j1&lang=zh"
    try:
        resp = _http_client().get(
            url, timeout=(WEATHER_TIMEOUT if (WEATHER_TIMEOUT and WEATHER_TIMEOUT > 0) else None))
        resp.raise_for_status()
        data = resp.json()
        days = data.get("weather") or []
        dates = [str(w.get("date") or "") for w in days if w.get("date")]

        def _desc_of(node):
            zh = node.get("lang_zh") or []
            if zh and zh[0].get("value"):
                return zh[0]["value"]
            return ((node.get("weatherDesc") or [{}])[0].get("value") or "未知")

        if not d or d == (days[0].get("date") if days else ""):
            cur = (data.get("current_condition") or [{}])[0]
            temp = cur.get("temp_C") or "?"
            return f"{loc} {d or (days[0].get('date') if days else '今天')} 天气：{_desc_of(cur)}，气温 {temp}°C"
        match = next((w for w in days if str(w.get("date")) == d), None)
        if not match:
            avail = "、".join(dates) or "（无）"
            return f"错误：wttr.in 仅提供今天起 3 天预报，无法获取 {d}（可选日期：{avail}）"
        hourly = match.get("hourly") or []
        noon = hourly[len(hourly) // 2] if hourly else {}
        return (f"{loc} {d} 天气：{_desc_of(noon)}，"
                f"气温 {match.get('mintempC', '?')}~{match.get('maxtempC', '?')}°C")
    except Exception as e:
        # 不返回编造的"模拟数据"——AI 会把假天气当真用于决策
        logging.warning("天气查询失败: %s", e)
        return f"错误：天气查询失败（{e}），请稍后重试或改用其他方式获取天气"
