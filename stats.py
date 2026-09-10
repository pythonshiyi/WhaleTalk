import json
import logging
import os
import threading
from datetime import date

from shared import is_peak_hour
import persistence

# 元 / 百万 tokens。此处均为**高峰时段价格**，空闲时段为高峰的一半，
# estimate_cost 按「用量发生日 + 当前时段」自动处理。
# 官方价格三项：缓存命中输入 / 缓存未命中输入 / 输出。
#
# 分界：北京时间 2026-09-10 12:00 起执行 DeepSeek V4.1 Flash 新价。
# V4.1 Flash 是**统一模型**：官方已下线 V4 Flash / V4 Flash Vision Exp 并将其
# 模型名路由到 V4.1 Flash；V4 Pro 自 2026-09-14 12:00 起同样路由，并按 V4.1
# Flash 单价计费。故当前价表只需一项，其余模型名统一回落到 DEFAULT_PRICE。
PRICING = {
    "deepseek-flash": {"prompt": 2.0, "completion": 8.0, "cache_hit": 0.04},
}

# ── 历史价（V4 正式版，2026-08-17 ~ 2026-09-10 12:00）──
# 仅用于回算该分界之前已产生的用量：价格随官方调整变化，历史记录不应被追溯改价。
# 注意 V4.1 Flash 新价对比：flash 输出 9.0 → 8.0、未命中输入 3.0 → 2.0；
# pro 输出 27.0 → 8.0（降幅最大，官方按其新价路由计费）。
PRICE_ERA_CURRENT = "2026-09-10"
_V4_PRICING = {
    "deepseek-v4-flash": {"prompt": 3.0, "completion": 9.0, "cache_hit": 0.10},
    "deepseek-v4-pro": {"prompt": 9.0, "completion": 27.0, "cache_hit": 0.30},
}
_V4_DEFAULT = {"prompt": 3.0, "completion": 9.0, "cache_hit": 0.10}

DEFAULT_PRICE = {"prompt": 2.0, "completion": 8.0, "cache_hit": 0.04}

_EMPTY_DAY = {"prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0}
_LOCK = threading.Lock()


def empty_day():
    """返回空 usage dict（公共只读接口）。"""
    return dict(_EMPTY_DAY)


def pricing():
    """返回定价表深拷贝（公共只读接口，防外部修改单价）。"""
    return {k: dict(v) for k, v in PRICING.items()}


def load_stats(path):
    """读取统计文件，返回 {day: {model: usage}}。文件损坏时返回空。"""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        cleaned = {}
        for day, models in data.items():
            if not isinstance(models, dict):
                continue
            cleaned[day] = {}
            for model, usage in models.items():
                if not isinstance(usage, dict):
                    continue
                try:
                    cleaned[day][model] = {
                        "prompt": int(usage.get("prompt", 0) or 0),
                        "completion": int(usage.get("completion", 0) or 0),
                        "cache_hit": int(usage.get("cache_hit", 0) or 0),
                        "cache_miss": int(usage.get("cache_miss", 0) or 0),
                    }
                except (TypeError, ValueError):
                    # 单条脏数据跳过，不丢弃整份统计
                    logging.warning("统计条目损坏已跳过: %s/%s", day, model)
                    continue
        return cleaned
    except Exception:
        logging.exception("读取用量统计失败")
        return {}


def save_stats(path, data):
    """保存用量统计（委托 persistence.atomic_json_write：唯一临时文件防并发写截断 + 失败自动清理）。"""
    try:
        persistence.atomic_json_write(path, data, indent=1)
    except Exception:
        logging.exception("保存用量统计失败")


def record_usage(path, model, usage, day=None):
    """记录一次 API 用量，模型与日期维度累计，线程安全（读-改-写全程持锁）。

    说明：load_stats 与 save_stats 之间必须持有 _LOCK，否则并发调用
    （多会话后台线程同时记录）会互相覆盖，造成用量丢失。
    """
    day = day or date.today().isoformat()
    model = model or "unknown"
    with _LOCK:
        data = load_stats(path)
        models = data.setdefault(day, {})
        acc = models.setdefault(model, empty_day())
        for key in ("prompt", "completion", "cache_hit", "cache_miss"):
            acc[key] = acc.get(key, 0) + int(usage.get(key, 0) or 0)
        save_stats(path, data)
    return data


def day_total(data, day=None):
    """某天全部模型合计，返回 usage dict。"""
    day = day or date.today().isoformat()
    total = empty_day()
    for usage in (data.get(day) or {}).values():
        for key in total:
            total[key] += usage.get(key, 0)
    return total


def all_total(data):
    total = empty_day()
    for models in data.values():
        for usage in models.values():
            for key in total:
                total[key] += usage.get(key, 0)
    return total


def model_total(data, model):
    total = empty_day()
    for models in data.values():
        usage = models.get(model)
        if usage:
            for key in total:
                total[key] += usage.get(key, 0)
    return total


def price_for(model, day=None):
    """取单价（元/百万 tokens，高峰价）。

    统一模型后只有一项当前价；未收录的模型名（含旧名 deepseek-v4-flash /
    deepseek-v4-pro）一律回落到当前价——与官方「旧名路由到 V4.1 Flash 并按
    V4.1 Flash 单价计费」的口径一致。

    day 用于历史回算：分界日（2026-09-10，V4.1 Flash 新价生效）之前的用量按
    当时的 V4 价表计算，避免历史记录被追溯改价。
    """
    if day and str(day) < PRICE_ERA_CURRENT:
        return _V4_PRICING.get(str(model), _V4_DEFAULT)
    return PRICING.get(str(model), DEFAULT_PRICE)


def estimate_cost(usage, model, day=None):
    """按定价估算费用（元）。高峰时段按表价，空闲时段（9:00 前/12-14/18:00 后）打 5 折。

    PRICING 存高峰价：官方峰谷定价规定「空闲时段价格为高峰时段价格的一半」。
    day 传入用量发生日时按当时价目回算（历史记录不追溯改价）。
    """
    price = price_for(model, day)
    miss = usage.get("prompt", 0) - usage.get("cache_hit", 0)
    miss = max(0, miss)
    cost = (
        miss * price["prompt"]
        + usage.get("cache_hit", 0) * price["cache_hit"]
        + usage.get("completion", 0) * price["completion"]
    ) / 1_000_000
    if not is_peak_hour():
        cost /= 2
    return cost


def format_cost(cost):
    if cost < 0.01:
        return "0.00"
    return f"{cost:.2f}"
