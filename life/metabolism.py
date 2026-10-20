"""代谢：每日预算、疲劳与能量。

不是"成本告警"，而是内感受：
- 预算耗尽 → 不再发起新的模型反思，只能睡眠/维护。
- 疲劳升高 → 反思频率下降，直到睡眠恢复。
- 高峰时段 → 优先把昂贵动作推迟到低谷（可配置关闭）。

数据源：``brain/metabolism.json``（疲劳/动作数）+ 现有 ``stats.json``（token/费用）。
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime
from pathlib import Path

from ._atomic import write_json

_LOCK = threading.RLock()

DEFAULT_METABOLISM = {
    "date": "",
    "fatigue": 0.0,
    "energy": 100.0,
    "actions": 0,
    "reflections": 0,
    "tokens_charged": 0,
    "cost_charged": 0.0,
    "last_action": None,
    "last_recover": None,
    "updated_at": None,
}


def _brain_dir() -> Path:
    try:
        import brainkit as bk
        return Path(bk.BRAIN_DIR)
    except Exception:
        return Path(__file__).resolve().parent.parent / "brain"


def path() -> Path:
    return _brain_dir() / "metabolism.json"


def _now_iso(ts=None) -> str:
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _cfg() -> dict:
    try:
        import config_utils
        return config_utils.load_config()
    except Exception:
        try:
            from config_defaults import DEFAULT_CONFIG
            return dict(DEFAULT_CONFIG)
        except Exception:
            return {}


def _reset_if_new_day(data) -> bool:
    if str(data.get("date") or "") != _today():
        data.update({
            "date": _today(), "fatigue": 0.0, "energy": 100.0,
            "actions": 0, "reflections": 0, "tokens_charged": 0,
            "cost_charged": 0.0, "last_action": None, "last_recover": None,
        })
        return True
    return False


def load() -> dict:
    data = dict(DEFAULT_METABOLISM)
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update({k: raw[k] for k in DEFAULT_METABOLISM if k in raw})
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    changed = _reset_if_new_day(data)
    if changed:
        try:
            save(data)
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except Exception:
            pass
    try:
        data["fatigue"] = max(0.0, min(100.0, float(data.get("fatigue") or 0)))
        data["energy"] = max(0.0, min(100.0, 100.0 - data["fatigue"]))
    except (TypeError, ValueError):
        data["fatigue"], data["energy"] = 0.0, 100.0
    return data


def save(data) -> dict:
    data = dict(data or {})
    data["updated_at"] = _now_iso()
    with _LOCK:
        write_json(path(), data)
    return data


def _stats_usage(cfg):
    """读取今日 token/费用；stats 不可用时返回零值。"""
    try:
        import deepseek_client as dc
        import stats
        stats_path = getattr(dc, "STATS_FILE", None)
        if not stats_path:
            return stats.empty_day(), 0.0
        data = stats.load_stats(stats_path)
        usage = stats.day_total(data)
        model = str(cfg.get("model") or getattr(dc, "DEFAULT_MODEL", "deepseek-flash"))
        cost = float(stats.estimate_cost(usage, model))
        return usage, cost
    except Exception:
        try:
            import stats
            return stats.empty_day(), 0.0
        except Exception:
            return {"prompt": 0, "completion": 0, "cache_hit": 0, "cache_miss": 0}, 0.0


def _num(cfg, key, default):
    try:
        return float(cfg.get(key, default) or default)
    except (TypeError, ValueError):
        return float(default)


def snapshot(cfg=None) -> dict:
    cfg = cfg if isinstance(cfg, dict) else _cfg()
    data = load()
    usage, total_cost = _stats_usage(cfg)
    # token 预算按“未命中缓存的输入 + 输出”计；缓存命中很便宜，单独由费用预算约束。
    input_tokens = usage.get("cache_miss")
    if input_tokens is None:
        input_tokens = usage.get("prompt", 0)
    total_tokens = int(input_tokens or 0) + int(usage.get("completion", 0) or 0)
    self_tokens = int(data.get("tokens_charged") or 0)
    self_cost = float(data.get("cost_charged") or 0)
    # 预算范围：
    #   self  = 只算生命循环自己发起的反思/睡眠（默认，避免用户聊天把自主预算吃光）
    #   total = 当日所有对话 + 生命循环（传统全局预算）
    scope = str(cfg.get("life_budget_scope") or "self").strip().lower()
    if scope not in ("self", "total"):
        scope = "self"
    used_tokens = self_tokens if scope == "self" else total_tokens
    cost = self_cost if scope == "self" else total_cost
    budget_tokens = max(0, int(_num(cfg, "life_daily_token_budget", 1000000)))
    budget_cost = max(0.0, _num(cfg, "life_daily_cost_budget", 5.0))
    remaining_tokens = max(0, budget_tokens - used_tokens) if budget_tokens else 0
    remaining_cost = max(0.0, budget_cost - cost) if budget_cost else 0.0
    try:
        peak = bool(__import__("shared").is_peak_hour())
    except Exception:
        peak = False
    can = True
    reason = ""
    if budget_tokens and used_tokens >= budget_tokens:
        can, reason = False, "今日 token 预算已用尽"
    elif budget_cost and cost >= budget_cost:
        can, reason = False, "今日费用预算已用尽"
    elif float(data.get("energy") or 0) < 20:
        can, reason = False, "疲劳过高，需要睡眠"
    elif peak and bool(cfg.get("life_off_peak_only", False)):
        can, reason = False, "高峰时段，昂贵动作推迟"
    return {
        "date": _today(),
        "budget_tokens": budget_tokens,
        "used_tokens": used_tokens,
        "remaining_tokens": remaining_tokens,
        "tokens_ratio": (used_tokens / budget_tokens) if budget_tokens else 0.0,
        "budget_cost": budget_cost,
        "used_cost": round(cost, 4),
        "remaining_cost": round(remaining_cost, 4),
        "cost_ratio": (cost / budget_cost) if budget_cost else 0.0,
        "fatigue": round(float(data.get("fatigue") or 0), 1),
        "energy": round(float(data.get("energy") or 0), 1),
        "actions": int(data.get("actions") or 0),
        "reflections": int(data.get("reflections") or 0),
        "peak": peak,
        "can_think": can,
        "reason": reason,
        "scope": scope,
        "self_tokens": self_tokens,
        "self_cost": round(self_cost, 4),
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 4),
        "usage": usage,
    }


def can_think(cfg=None) -> tuple:
    snap = snapshot(cfg)
    return bool(snap.get("can_think")), str(snap.get("reason") or ""), snap


def charge(usage=None, model="", cfg=None, reflection=False) -> dict:
    """记录一次模型消耗并增加疲劳。usage 传给 stats.record_usage。"""
    cfg = cfg if isinstance(cfg, dict) else _cfg()
    model = str(model or cfg.get("model") or "deepseek-flash")
    usage = usage if isinstance(usage, dict) else {}
    try:
        import deepseek_client as dc
        import stats
        stats_path = getattr(dc, "STATS_FILE", None)
        if stats_path and usage:
            stats.record_usage(stats_path, model, usage)
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    with _LOCK:
        data = load()
        miss = usage.get("cache_miss")
        if miss is None:
            miss = usage.get("prompt", 0)
        used = int(miss or 0) + int(usage.get("completion", 0) or 0)
        try:
            data["tokens_charged"] = int(data.get("tokens_charged") or 0) + used
        except (TypeError, ValueError):
            data["tokens_charged"] = used
        try:
            data["cost_charged"] = round(float(data.get("cost_charged") or 0) + _usage_cost(usage, model), 4)
        # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
        except (TypeError, ValueError):
            pass
        data["actions"] = int(data.get("actions") or 0) + 1
        if reflection:
            data["reflections"] = int(data.get("reflections") or 0) + 1
        per = max(0.0, _num(cfg, "life_fatigue_per_action", 8.0))
        # 一次反思的 token 越多，疲劳越高（上限 20），避免小额调用把能量瞬间打空。
        extra = 0.0
        if used:
            extra = min(12.0, used / 10000.0)
        data["fatigue"] = max(0.0, min(100.0, float(data.get("fatigue") or 0) + per + extra))
        data["energy"] = max(0.0, 100.0 - data["fatigue"])
        data["last_action"] = _now_iso()
        save(data)
        return data


def _usage_cost(usage, model) -> float:
    try:
        import stats
        return float(stats.estimate_cost(usage, model))
    except Exception:
        return 0.0


def recover(amount=100.0) -> dict:
    with _LOCK:
        data = load()
        data["fatigue"] = max(0.0, float(data.get("fatigue") or 0) - max(0.0, float(amount)))
        data["energy"] = max(0.0, 100.0 - data["fatigue"])
        data["last_recover"] = _now_iso()
        save(data)
        return data
