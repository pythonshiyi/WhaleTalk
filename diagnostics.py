"""diagnostics.py —— 一键诊断包：把「哪里坏了」变成一份可读的报告。

## 为什么需要它

项目里已经有三套观测设施：`degrade.py`（降级账本）、`tool_trace`（工具调用轨迹）、
出网账本（外发审计）。但它们各自为政，**没有一个对用户可用的出口**——
用户遇到「AI 变笨 / 工具没反应 / 搜不到东西」时，只能让你翻日志。

本模块把这些数据 + 环境事实收成**一份 JSON**（`export()` 落盘，`/v1/diagnostics` 返回摘要）。
它不新增任何监控，只是「已有的信号，第一次能被取出来」。

## 设计要点

- **永不抛出**：诊断自己坏掉不能影响主流程（每一段都独立 try，失败即降级成一行说明）。
- **默认脱敏**：配置只输出**与默认值不同的键**，键名含 password/token/key/secret 的值一律打码，
  长字符串截断。诊断包是给人（或客服）看的，不应成为凭据泄露渠道。
- **只读**：不改配置、不写业务数据；唯一的写操作是 `export()` 落一个 JSON 到
  `DATA_DIR/diagnostics/`。
- 仅依赖标准库 + 项目内既有模块（degrade / config_utils），无第三方依赖。
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import platform
import sys
import time
import traceback
from datetime import datetime

SECRET_HINTS = ("password", "passwd", "token", "secret", "api_key", "apikey",
                "brain_key", "webhook", "credential")
MAX_STR = 120          # 单个字符串值的最大长度
MAX_LOG_LINES = 80     # 错误日志尾部行数
MAX_LOG_BYTES = 256 * 1024


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe(fn, default, errors, label):
    """执行一段采集；失败只记录一行，绝不影响其它段。"""
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - 诊断的采集必须是全隔离的
        errors.append(f"{label}: {type(e).__name__}: {e}")
        return default


def _data_dir():
    try:
        import api_server
        d = getattr(api_server, "DATA_DIR", None)
        if d:
            return str(d)
    except Exception:  # noqa: BLE001 - 独立运行/测试时兜底
        pass
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _version():
    try:
        from config_defaults import VERSION
        return str(VERSION)
    except Exception:  # noqa: BLE001
        return "unknown"


def _is_secret(key):
    low = str(key).lower()
    return any(h in low for h in SECRET_HINTS)


def _redact(value, key=""):
    """把配置值处理成可外发的形态：敏感打码、长串截断、容器只留类型与规模。"""
    if _is_secret(key):
        return "<redacted>"
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_STR else value[:MAX_STR] + f"…(+{len(value) - MAX_STR})"
    if isinstance(value, dict):
        return {f"<dict len={len(value)}>": sorted(str(k) for k in list(value)[:12])}
    if isinstance(value, (list, tuple, set)):
        return [f"<{type(value).__name__} len={len(value)}>"]
    return f"<{type(value).__name__}>"


def config_state():
    """配置事实：键数 + **只列与默认值不同的键**（脱敏后）。"""
    import config_defaults
    import config_utils

    cfg = config_utils.load_config() or {}
    defaults = getattr(config_defaults, "DEFAULT_CONFIG", {}) or {}
    overrides = {}
    for k, v in cfg.items():
        if k not in defaults:
            overrides[k] = {"value": _redact(v, k), "why": "不在 DEFAULT_CONFIG"}
            continue
        try:
            same = (v == defaults[k])
        except Exception:  # noqa: BLE001
            same = False
        if not same:
            overrides[k] = {"value": _redact(v, k)}
    return {
        "keys_total": len(cfg),
        "keys_default": len(defaults),
        "overrides": overrides,
        "extra_keys": sorted(k for k in cfg if k not in defaults)[:40],
    }


def degradations(limit=20):
    import degrade
    return {"summary": degrade.summary(), "entries": degrade.snapshot(limit=limit)}


def recent_errors():
    """最近一次对话/动作失败的原文（只读日志尾部，不挂 handler、不改日志配置）。"""
    out = {}
    log_dir = os.path.join(_data_dir(), "logs")
    for name in ("chat_errors.log", "actions.log"):
        path = os.path.join(log_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            with open(path, "rb") as f:
                if size > MAX_LOG_BYTES:
                    f.seek(size - MAX_LOG_BYTES)
                    f.readline()  # 丢掉被截断的半行
                tail = f.read().decode("utf-8", "replace")
            lines = [ln for ln in tail.splitlines() if ln.strip()][-MAX_LOG_LINES:]
            out[name] = {"path": path, "bytes": size, "lines": lines}
        except Exception as e:  # noqa: BLE001
            out[name] = {"path": path, "error": f"{type(e).__name__}: {e}"}
    return out


def registry():
    """工具/路由规模。

    注意口径：这里数的是**路由表条目（matcher）**，而文档里的「106 个 /v1 路由」数的是
    **端点装饰器**——一个 `set` matcher 可登记多个路径，所以 matcher 数会比端点数多。
    两个数都对，只是口径不同，故这里显式命名 `route_matchers_*`，避免看报告的人误判。
    """
    info = {}
    try:
        import toolkit
        names = toolkit.registry_names() if hasattr(toolkit, "registry_names") else []
        info["tools"] = len(names)
    except Exception as e:  # noqa: BLE001
        info["tools_error"] = f"{type(e).__name__}: {e}"
    try:
        import api_server
        info["route_matchers_post"] = len(getattr(api_server, "_POST_ROUTES", []))
        info["route_matchers_get"] = len(getattr(api_server, "_GET_ROUTES", []))
    except Exception as e:  # noqa: BLE001
        info["routes_error"] = f"{type(e).__name__}: {e}"
    return info


def capabilities():
    """可选能力的可用性（缺哪个，用户会在「设置 → 可选能力」里看到）。"""
    mods = {
        "faster_whisper": "语音转写",
        "pyzbar": "二维码识别",
        "playwright": "浏览器内核",
        "piper": "本地语音合成",
        "rarfile": "RAR 解压",
    }
    out = {}
    for mod, label in mods.items():
        try:
            out[mod] = {"label": label, "ok": importlib.util.find_spec(mod) is not None}
        except Exception:  # noqa: BLE001
            out[mod] = {"label": label, "ok": False}
    return out


def data_usage(top=10):
    """数据目录占用 Top-N（谁在长胖，一看即知）。"""
    root = _data_dir()
    items = []
    if os.path.isdir(root):
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("diagnostics", "node_modules", ".git")]
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    items.append((os.path.getsize(fp), os.path.relpath(fp, root)))
                except OSError:
                    continue
    items.sort(reverse=True)
    return {"root": root, "total_bytes": sum(s for s, _ in items),
            "top": [{"bytes": s, "path": p} for s, p in items[:top]]}


def collect(degrade_limit=20):
    """采集一份完整诊断（不落盘）。任何一段失败都只影响该段。"""
    errors = []
    started = time.time()
    doc = {
        "schema": 1,
        "generated_at": _now(),
        "meta": {
            "version": _version(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "pid": os.getpid(),
            "cwd": os.getcwd(),
            "data_dir": _safe(_data_dir, "", errors, "meta.data_dir"),
        },
        "config": _safe(config_state, {}, errors, "config"),
        "degradations": _safe(lambda: degradations(degrade_limit), {}, errors, "degradations"),
        "errors": _safe(recent_errors, {}, errors, "errors"),
        "registry": _safe(registry, {}, errors, "registry"),
        "capabilities": _safe(capabilities, {}, errors, "capabilities"),
        "data": _safe(data_usage, {}, errors, "data"),
        "collection_errors": errors,
    }
    doc["collect_ms"] = int((time.time() - started) * 1000)
    return doc


def summary():
    """给 `/v1/status` 这类高频端点用的极简摘要（不含日志正文）。"""
    out = {"degrade": {}, "tools": None, "route_matchers": None, "errors_log": 0}
    try:
        import degrade
        out["degrade"] = degrade.summary()
    except Exception:  # noqa: BLE001
        pass
    try:
        reg = registry()
        out["tools"] = reg.get("tools")
        out["route_matchers"] = (reg.get("route_matchers_post") or 0) + \
                                (reg.get("route_matchers_get") or 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        out["errors_log"] = len((recent_errors().get("chat_errors.log") or {}).get("lines") or [])
    except Exception:  # noqa: BLE001
        pass
    return out


def _out_dir():
    d = os.path.join(_data_dir(), "diagnostics")
    os.makedirs(d, exist_ok=True)
    return d


def export(payload=None):
    """把诊断包写成 JSON，返回 {ok, path, bytes}。失败返回 {ok: False, error}。"""
    try:
        doc = payload if isinstance(payload, dict) else collect()
        out_dir = _out_dir()
        name = "whaletalk-diag-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
        path = os.path.join(out_dir, name)
        text = _dumps(doc)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return {"ok": True, "path": path, "bytes": len(text.encode("utf-8")),
                "version": doc.get("meta", {}).get("version"),
                "generated_at": doc.get("generated_at")}
    except Exception as e:  # noqa: BLE001 - 导出失败要如实说，而不是抛给 HTTP 层
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "trace": traceback.format_exc(limit=3)}


def _dumps(doc):
    import json
    return json.dumps(doc, ensure_ascii=False, indent=1, default=str)


def fingerprint(doc=None):
    """诊断包指纹（用于「两份报告是不是同一环境」的快速比对）。"""
    doc = doc or collect()
    basis = {
        "version": doc.get("meta", {}).get("version"),
        "python": doc.get("meta", {}).get("python"),
        "platform": doc.get("meta", {}).get("platform"),
        "config_keys": doc.get("config", {}).get("keys_total"),
        "overrides": sorted((doc.get("config", {}).get("overrides") or {}).keys()),
        "tools": doc.get("registry", {}).get("tools"),
        "routes": [doc.get("registry", {}).get("route_matchers_post"),
                   doc.get("registry", {}).get("route_matchers_get")],
    }
    return hashlib.md5(_dumps(basis).encode("utf-8")).hexdigest()[:12]


def main(argv=None):
    """命令行入口：`python diagnostics.py` 打印；`--export` 落盘并打印路径。

    给「用户/客服拿不到 UI」的场景留一条最小通道——不依赖服务已在运行。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--export" in argv:
        out = export()
        print(_dumps(out))
        return 0 if out.get("ok") else 1
    doc = collect()
    doc["fingerprint"] = fingerprint(doc)
    print(_dumps(doc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
