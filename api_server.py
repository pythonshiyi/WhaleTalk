# -*- coding: utf-8 -*-
"""本地 HTTP API（v2）：新一代 WebUI 的后端。

提供 token 保护的本地接口，供 WebUI（pywebview/浏览器）调用：
- GET  /health                   健康检查
- GET  /v1/sessions              会话列表（懒加载摘要，不含完整消息）
- GET  /v1/sessions/<id>/messages 会话完整消息
- GET  /v1/context               上下文（工具/记忆摘要/用量）
- GET  /v1/config                模型/思考档位/场景等配置
- POST /v1/chat                  非流式对话（兼容 v1）
- POST /v1/chat/stream           SSE 流式对话（思考/工具/内容/用量事件）

安全约定：
- 仅监听 127.0.0.1（不暴露到局域网）。
- 必须携带 Bearer token（优先 config.json 的 inbound_token，否则启动时自动生成）。
- 请求体上限 1MB（图片上传 /v1/upload 例外：64MB，超限图片自动压缩后落盘），messages 条数/长度受限。
"""
import asyncio
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import shared

logger = logging.getLogger("whaletalk.api")


def secrets_token(n=4):
    import secrets
    return secrets.token_hex(n)


def _cu_load(key, default=None):
    """读取 config.json 的字段（权限初始化用）。"""
    try:
        import config_utils
        return config_utils.load_config().get(key, default)
    except Exception:
        return default


# ── 审批/询问/白名单 双向通道 ─────────────────────────
# chat worker 线程回调 → SSE 事件发给前端 → 前端 POST /v1/respond 回传
# {rid: {"ev": Event, "box": dict, "type": "ask"|"approval"|"permission"}}
_PENDING = {}
_PENDING_LOCK = threading.Lock()
_APPROVAL_LOCK = threading.Lock()
_LAST_TOOL_CHAIN = []

ASK_TIMEOUT = 180.0


def _respond(body):
    """前端回传：{id, answer?} / {id, allow?, reason?} / {id, ok?, msg?}。"""
    rid = str(body.get("id") or "")
    if not rid:
        return False, "缺少 id"
    with _PENDING_LOCK:
        entry = _PENDING.get(rid)
        if not entry:
            return False, "请求不存在或已超时"
    box = entry["box"]
    typ = entry["type"]
    if typ == "ask":
        answer = str(body.get("answer") or "").strip()
        if not answer:
            return False, "答案不能为空"
        box["answer"] = answer
    elif typ == "approval":
        box["allow"] = bool(body.get("allow"))
        box["reason"] = str(body.get("reason") or ("用户允许" if box["allow"] else "用户拒绝"))
    elif typ == "permission":
        box["ok"] = bool(body.get("ok"))
        box["msg"] = str(body.get("msg") or ("已加入白名单" if box["ok"] else "白名单请求被拒绝"))
    entry["ev"].set()
    return True, None


def _make_approval_cb(send, stop_event):
    """on_approval：任务模式零审批直接放行；其余复用 request_approval 闸门，弹窗改走 SSE。"""
    import permissions

    def web_approval(name, args):
        rid = secrets_token(4)
        ev = threading.Event()
        box = {"allow": False, "reason": "审批超时未响应（自动拒绝）"}
        with _PENDING_LOCK:
            _PENDING[rid] = {"ev": ev, "box": box, "type": "approval"}
        send("approval_request", {"id": rid, "name": name, "args": args})
        deadline = time.monotonic() + permissions.approval_timeout()
        while not ev.wait(0.5):
            if stop_event and stop_event.is_set():
                box["reason"] = "（用户停止了生成）"
                break
            if time.monotonic() >= deadline:
                break
        with _PENDING_LOCK:
            _PENDING.pop(rid, None)
        _record_approval({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "type": "approval",
            "name": str(name or "")[:60],
            "args": str(args or "")[:200],
            "result": "允许" if box["allow"] else "拒绝",
            "reason": str(box.get("reason") or "")[:200],
        })
        return box["allow"], box.get("reason", "")

    def cb(name, args):
        _sync_full_auto()
        if permissions.is_full_auto():
            # 任务模式：零审批、零开关，黑名单仍生效
            return True, ""
        with _APPROVAL_LOCK:
            permissions.set_approval_callback(web_approval)
            try:
                return permissions.request_approval(name, args)
            finally:
                permissions.set_approval_callback(None)

    return cb


def _make_ask_cb(send, stop_event):
    """on_ask：向用户提问，阻塞等待回答。"""

    def cb(prompt):
        rid = secrets_token(4)
        ev = threading.Event()
        box = {"answer": None}
        with _PENDING_LOCK:
            _PENDING[rid] = {"ev": ev, "box": box, "type": "ask"}
        send("ask_request", {"id": rid, "prompt": str(prompt)})
        deadline = time.monotonic() + ASK_TIMEOUT
        while not ev.wait(0.5):
            if stop_event and stop_event.is_set():
                _record_approval({
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "type": "ask",
                    "prompt": str(prompt)[:200],
                    "result": "中断",
                    "reason": "（用户停止了生成）",
                })
                return "（用户停止了生成）"
            if time.monotonic() >= deadline:
                break
        with _PENDING_LOCK:
            _PENDING.pop(rid, None)
        answer = box.get("answer")
        if answer is None:
            _record_approval({
                "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                "type": "ask",
                "prompt": str(prompt)[:200],
                "result": "超时未回答",
                "reason": "（用户未在限时内回答）",
            })
            return "（用户未在限时内回答，请简化问题或改用其他方式）"
        _record_approval({
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "type": "ask",
            "prompt": str(prompt)[:200],
            "result": "已回答",
            "reason": str(answer)[:200],
        })
        return str(answer)

    return cb


def _sync_full_auto():
    """每次会话请求前同步权限模块的 FULL_AUTO（防进程内状态漂移）。"""
    try:
        import permissions as perms
        import config_utils
        cfg = config_utils.load_config()
        perms.set_full_auto(bool(cfg.get("full_auto")))
    except Exception:
        pass


def _make_permission_cb(send, stop_event):
    """on_request_permission：完全去除白名单逻辑——一律直接放行（黑名单机制下无需授权）。"""

    def cb(action_type, value):
        # 黑名单机制：默认放行，无需白名单申请/弹窗
        return True, "已放行"

    return cb


# 工具 → 能力域 映射（能力中心 12 域展示用，不改动核心 TOOLS 定义）
_TOOL_DOMAIN = {
    "get_date": "系统与基础", "get_weather": "系统与基础", "system_status": "系统与基础",
    "environment_info": "系统与基础", "call_api": "系统与基础", "usage_report": "系统与基础",
    "secret_store": "系统与基础", "kv_store": "系统与基础",
    "read_file": "文件与目录", "write_file": "文件与目录", "edit_file": "文件与目录",
    "list_dir": "文件与目录", "delete_file": "文件与目录", "archive_files": "文件与目录",
    "extract_archive": "文件与目录", "batch_rename": "文件与目录", "archive_list": "文件与目录",
    "download_file": "文件与目录",
    "read_csv": "数据与文档", "write_csv": "数据与文档", "read_excel": "数据与文档",
    "write_excel": "数据与文档", "chart_data": "数据与文档", "pdf_extract": "数据与文档",
    "pdf_create": "数据与文档", "docx_read": "数据与文档", "pptx_read": "数据与文档",
    "create_doc": "数据与文档", "epub_read": "数据与文档", "mobi_read": "数据与文档",
    "doc_read": "数据与文档", "msg_read": "数据与文档",
    "database_query": "数据库", "database_query_mysql": "数据库",
    "database_query_postgres": "数据库", "database_execute": "数据库",
    "fetch_url": "网络与通信", "fetch_blocked": "网络与通信", "search_web": "网络与通信",
    "search_github": "网络与通信", "search_realtime": "网络与通信", "rss_fetch": "网络与通信",
    "webdav": "网络与通信", "search_local": "网络与通信",
    "net_diagnose": "网络与通信", "fetch_url_smart": "网络与通信",
    "run_python": "开发与测试", "run_command": "开发与测试", "pip_install": "开发与测试",
    "write_code_project": "开发与测试", "run_tests": "开发与测试", "verify_output": "开发与测试",
    "verify_files": "开发与测试", "project_info": "开发与测试", "read_project_file": "开发与测试",
    "create_plugin": "开发与测试", "create_evolution": "开发与测试",
    "app_manage": "开发与测试",
    "tts_save": "媒体与图像", "image_process": "媒体与图像", "ocr_image": "媒体与图像",
    "image_understand": "媒体与图像", "image_generate": "媒体与图像", "image_batch": "媒体与图像",
    "screen_see": "媒体与图像", "chart_read": "媒体与图像", "screenshot_to_html": "媒体与图像",
    "debug_screenshot": "媒体与图像", "scan_read": "媒体与图像", "screen_capture": "媒体与图像",
    "speech_to_text": "媒体与图像", "media_ffmpeg": "媒体与图像", "web_screenshot": "媒体与图像",
    "qrcode": "媒体与图像",
    "send_email": "消息与协作", "read_email": "消息与协作", "email_summary": "消息与协作",
    "agent_mail": "消息与协作", "send_webhook": "消息与协作", "im_send": "消息与协作",
    "telegram_poll_updates": "消息与协作", "notify_desktop": "消息与协作",
    "clipboard_get": "消息与协作", "clipboard_set": "消息与协作",
    "rpa_screen_size": "桌面与自动化", "rpa_click": "桌面与自动化", "rpa_type": "桌面与自动化",
    "rpa_hotkey": "桌面与自动化", "rpa_move": "桌面与自动化", "rpa_scroll": "桌面与自动化",
    "rpa_screenshot": "桌面与自动化", "browser_navigate": "桌面与自动化",
    "screen_find_click": "桌面与自动化",
    "start_process": "桌面与自动化", "stop_process": "桌面与自动化",
    "list_processes": "桌面与自动化",
    "schedule_task": "定时与任务", "list_schedules": "定时与任务", "cancel_schedule": "定时与任务",
    "task_checkpoint_save": "定时与任务", "task_checkpoint_load": "定时与任务",
    "run_workflow": "定时与任务", "daily_brief": "定时与任务",
    "write_memory": "记忆与知识", "read_memory": "记忆与知识",
    "query_memory_graph": "记忆与知识", "knowledge_index": "记忆与知识",
    "knowledge_search": "记忆与知识",
    "ask_user": "AI 与智能", "request_permission": "AI 与智能",
    "run_wechat_writer": "AI 与智能", "publish_draft": "AI 与智能",
    "subagent_run": "AI 与智能", "team_run": "AI 与智能",
    "voice_chat_loop": "媒体与图像", "vision_loop": "媒体与图像",
}

_DOMAIN_ORDER = [
    "系统与基础", "文件与目录", "数据与文档", "数据库", "网络与通信",
    "开发与测试", "媒体与图像", "消息与协作", "桌面与自动化", "定时与任务",
    "记忆与知识", "AI 与智能",
]

_DOMAIN_ICONS = {
    "系统与基础": "🖥", "文件与目录": "📁", "数据与文档": "📊", "数据库": "🗄",
    "网络与通信": "🌐", "开发与测试": "⚙", "媒体与图像": "🎨", "消息与协作": "✉",
    "桌面与自动化": "🖱", "定时与任务": "⏰", "记忆与知识": "🧠", "AI 与智能": "🤖",
}

_DOMAIN_COLORS = {
    "系统与基础": "#0ea5e9", "文件与目录": "#f59e0b", "数据与文档": "#10b981",
    "数据库": "#8b5cf6", "网络与通信": "#06b6d4", "开发与测试": "#ec4899",
    "媒体与图像": "#f97316", "消息与协作": "#22d3ee", "桌面与自动化": "#a3e635",
    "定时与任务": "#fbbf24", "记忆与知识": "#c084fc", "AI 与智能": "#38bdf8",
}


def _plugin_paths():
    return {
        "plugins_dir": os.path.join(DATA_DIR, "plugins"),
        "user_tools": os.path.join(DATA_DIR, "user_tools.json"),
        "prompts": os.path.join(DATA_DIR, "prompts.json"),
        "workflows": os.path.join(DATA_DIR, "workflows.json"),
    }


def _plugin_summary(p):
    meta = p.get("meta") or {}
    contents = p.get("contents") or {}
    kind = "应用型" if contents.get("app") else ("流程" if contents.get("workflows") else ("技能" if contents.get("skills") else "工具"))
    try:
        import plugins as plugins_mod
        perms = plugins_mod.plugin_permissions(p)
    except Exception:
        perms = {"declared": False, "tools": [], "files": [], "net": False, "notes": ""}
    return {
        "name": str(meta.get("name") or "未命名插件"),
        "description": str(meta.get("description") or ""),
        "author": str(meta.get("author") or ""),
        "version": str(meta.get("version") or ""),
        "enabled": bool(p.get("enabled", True)),
        "trigger": str((meta.get("triggers") or [meta.get("trigger")] or [""])[0] if isinstance(meta.get("triggers"), list) else (meta.get("trigger") or "")),
        "slug": str(p.get("slug") or ""),
        "kind": kind,
        "permissions": perms,
    }


def _plugins():
    """插件市场：已安装 + 画廊（sample_plugins）。"""
    import plugins as plugins_mod
    installed = []
    try:
        for p in plugins_mod.list_plugins(_plugin_paths()["plugins_dir"]):
            installed.append(_plugin_summary(p))
    except Exception:
        logger.exception("读取已装插件失败")
    gallery = []
    sample_dir = os.path.join(_ORIG_DIR, "sample_plugins")
    installed_names = {p["name"] for p in installed}
    if os.path.isdir(sample_dir):
        for fn in sorted(os.listdir(sample_dir)):
            if not fn.endswith(plugins_mod.PLUGIN_EXT):
                continue
            try:
                p, err = plugins_mod.parse_plugin_file(os.path.join(sample_dir, fn))
                if p is not None:
                    s = _plugin_summary(p)
                    s["installed"] = s["name"] in installed_names
                    gallery.append(s)
            except Exception:
                continue
    return {"installed": installed, "gallery": gallery}


def _plugins_action(body):
    """安装/卸载/启用/停用插件。body: {name, action}。返回 (result, error)。"""
    import plugins as plugins_mod
    name = str(body.get("name") or "")
    action = str(body.get("action") or "")
    if not name or action not in ("install", "uninstall", "enable", "disable"):
        return None, "参数错误：name + action(install/uninstall/enable/disable)"
    paths = _plugin_paths()
    plugins_dir = paths["plugins_dir"]
    installed = plugins_mod.list_plugins(plugins_dir)
    target = next((p for p in installed if (p.get("meta") or {}).get("name") == name), None)
    if action == "install":
        if target:
            return None, "插件已安装"
        sample_dir = os.path.join(_ORIG_DIR, "sample_plugins")
        src = None
        if os.path.isdir(sample_dir):
            for fn in os.listdir(sample_dir):
                if not fn.endswith(plugins_mod.PLUGIN_EXT):
                    continue
                try:
                    p, err = plugins_mod.parse_plugin_file(os.path.join(sample_dir, fn))
                    if p is not None and (p.get("meta") or {}).get("name") == name:
                        src = p
                        break
                except Exception:                    continue
        if src is None:
            return None, "画廊中未找到该插件"
        res = plugins_mod.apply_plugin(src, paths)
        if res.get("ok"):
            ret = {"ok": True, "added": res.get("added")}
            try:
                perms = plugins_mod.plugin_permissions(src)
                if not perms["declared"]:
                    ret["warning"] = "⚠️ 该插件未声明权限（tools/files/net），调用其能力时权限按系统默认执行"
            except Exception:
                pass
            return ret, None
        return None, str(res.get("error") or "安装失败")
    if not target:
        return None, "插件未安装"
    if action == "uninstall":
        res = plugins_mod.unapply_plugin(target, paths)
        if res.get("ok"):
            try:
                if os.path.exists(target.get("_file", "")):
                    os.remove(target["_file"])
            except Exception:
                pass
            return {"ok": True}, None
        return None, str(res.get("error") or "卸载失败")
    if action == "enable" and not target.get("enabled"):
        target["enabled"] = True
        plugins_mod.save_plugin_file(target, plugins_dir)
        plugins_mod.apply_plugin(target, paths)
        return {"ok": True}, None
    if action == "disable" and target.get("enabled"):
        target["enabled"] = False
        plugins_mod.save_plugin_file(target, plugins_dir)
        plugins_mod.unapply_plugin(target, paths)
        return {"ok": True}, None
    return {"ok": True}, None


def _memory_full():
    """长期记忆全量（facts 列表）。兼容 {text,...} 与 {key,value,...} 两种存储结构。"""
    import stores
    d = stores.load_memory(MEMORY_PATH)
    facts = d.get("facts") or []
    out = []
    for f in facts[-200:]:
        if isinstance(f, dict):
            text = str(f.get("text") or f.get("value") or "")[:300]
            out.append({
                "text": text,
                "tags": str(f.get("tags") or ""),
                "type": str(f.get("type") or ""),
                "ts": str(f.get("ts") or f.get("time") or ""),
            })
        elif isinstance(f, str):
            out.append({"text": f[:300], "tags": "", "type": "", "ts": ""})
    return {"enabled": bool(d.get("enabled")), "facts": out}


def _role_name(prompt):
    """识别当前角色名（内置 + 用户角色），匹配失败返回「自定义」。"""
    import roles as roles_mod
    prompt = str(prompt or "")
    for r in roles_mod.ROLES.values():
        if str(r.get("prompt") or "") == prompt:
            return str(r.get("name") or "自定义")
    try:
        import stores
        items = stores.load_patterns(USER_ROLES_PATH)
        for r in items:
            if isinstance(r, dict) and str(r.get("prompt") or "") == prompt:
                return str(r.get("name") or "自定义")
    except Exception:
        pass
    return "自定义"


def _monthly_cost():
    """本月成本（stats.json 估算）。"""
    import stats as stats_mod
    from datetime import date
    try:
        data = stats_mod.load_stats(STATS_PATH)
        month_key = date.today().strftime("%Y-%m")
        cost = 0.0
        for day, models in data.items():
            if day.startswith(month_key):
                for model, usage in models.items():
                    cost += stats_mod.estimate_cost(usage, model)
        return round(cost, 2)
    except Exception:
        return 0.0


def _usage_month_summary():
    """本月真实 token 累计（stats.json 按天累计）：返回 (输入, 输出, 缓存命中率字符串)。"""
    import stats as stats_mod
    from datetime import date
    acc = {"prompt": 0, "completion": 0, "cache_hit": 0}
    try:
        data = stats_mod.load_stats(STATS_PATH)
        month_key = date.today().strftime("%Y-%m")
        for day, models in data.items():
            if day.startswith(month_key):
                for u in models.values():
                    acc["prompt"] += int(u.get("prompt", 0) or 0)
                    acc["completion"] += int(u.get("completion", 0) or 0)
                    acc["cache_hit"] += int(u.get("cache_hit", 0) or 0)
    except Exception:
        pass
    cached = f"{acc['cache_hit'] / acc['prompt'] * 100:.1f}%" if acc["prompt"] else "—"
    return acc["prompt"], acc["completion"], cached


_CACHE = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL = {
    "status": 2.0,
    "memory": 5.0,
    "monthly_cost": 60.0,
}


def _cached(key, fn):
    now = time.monotonic()
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and now - hit[0] < _CACHE_TTL.get(key, 2.0):
            return hit[1]
    val = fn()
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), val)
    return val


def _cached_invalidate(*keys):
    with _CACHE_LOCK:
        for k in keys:
            _CACHE.pop(k, None)


def _status():
    """状态栏数据：模式/目录/用量/预算/模型/角色/场景/思考。"""
    import config_utils
    import deepseek_client as dc
    cfg = config_utils.load_config()
    full_auto = bool(cfg.get("full_auto"))
    pure_chat = bool(cfg.get("pure_chat"))
    mode = "task" if full_auto else "dialog"
    active_dir = str(cfg.get("active_dir") or "").strip()
    if not active_dir or not os.path.isdir(active_dir):
        active_dir = os.path.join(DATA_DIR, "workspace")
    # 累计 token：从 stats.json 读真实累计（v3.0 重构后 session_latest.json 已无写入方，弃用）
    usage_total = {"prompt": 0, "completion": 0, "cache_hit": 0}
    try:
        import stats as stats_mod
        total = stats_mod.all_total(stats_mod.load_stats(STATS_PATH))
        usage_total = {
            "prompt": total["prompt"],
            "completion": total["completion"],
            "cache_hit": total["cache_hit"],
        }
    except Exception:
        pass
    return {
        "mode": mode,
        "full_auto": full_auto,
        "pure_chat": pure_chat,
        "privacy": bool(cfg.get("privacy_mode")),
        "active_dir": active_dir,
        "usage_total": usage_total,
        "monthly_cost": _monthly_cost(),
        "monthly_budget": float(cfg.get("monthly_budget") or 0.0),
        "peak_hour": dc.is_peak_hour(),
        "peak_warning": bool(cfg.get("peak_warning")),
        "model": cfg.get("model") or dc.DEFAULT_MODEL,
        "role": _role_name(cfg.get("system_prompt") or ""),
        "scenario": cfg.get("scenario") or "通用",
        "thinking": cfg.get("thinking") or "high",
    }


def _full_health(active_dir):
    """完整健康探测：CPU/内存/磁盘/网络（实时，略慢；仅 section=health/full 触发）。"""
    out = {}
    try:
        import psutil
        out["cpu_percent"] = round(psutil.cpu_percent(interval=0.5))
        mem = psutil.virtual_memory()
        out["mem_used_gb"] = round(mem.used / 1024 ** 3, 1)
        out["mem_total_gb"] = round(mem.total / 1024 ** 3, 1)
        out["mem_percent"] = round(mem.percent)
    except Exception:
        out["cpu_percent"] = None
    try:
        import shutil
        base = active_dir if (active_dir and os.path.isdir(active_dir)) else os.getcwd()
        du = shutil.disk_usage(base)
        out["disk_free_gb"] = round(du.free / 1024 ** 3, 1)
        out["disk_total_gb"] = round(du.total / 1024 ** 3, 1)
    except Exception:
        pass
    net = {}
    import socket
    for host in ("api.deepseek.com", "api.github.com"):
        try:
            s = socket.create_connection((host, 443), timeout=1.0)
            s.close()
            net[host] = True
        except Exception:
            net[host] = False
    out["network"] = net
    return out


def build_situation(section=None):
    """全局态势快照 —— 人（前端工作台）与 AI（get_status 工具）共用的单一事实源。

    一次聚合系统/用量/运行中/健康/待办/项目（含 git），保证「人看到的」与「AI 查到的」永远同源。

    section:
      None         核心摘要层（默认，轻量，无实时网络/CPU 探测）—— AI 调一次即掌握全局
      "recent"     最近会话 + 最近产物
      "processes"  后台进程详情
      "schedules"  定时任务详情
      "checkpoint" 任务检查点详情
      "health"     完整健康（含 CPU/内存/网络实时探测）
      "full"       摘要 + 全部详情（前端工作台一次取全）
    """
    import deepseek_client as dc

    st = _status()
    u = st.get("usage_total") or {}

    # ── 项目/工作内容：active_dir + git 状态（非 git 目录静默降级）──
    active_dir = str(st.get("active_dir") or "")
    git_branch = git_dirty = git_last_commit = None
    git_changes = 0
    if active_dir and os.path.isdir(active_dir):
        try:
            import subprocess

            def _git(args):
                return subprocess.run(
                    ["git", "-C", active_dir] + args,
                    capture_output=True, text=True, timeout=3,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )

            rb = _git(["rev-parse", "--abbrev-ref", "HEAD"])
            if rb.returncode == 0 and rb.stdout.strip():
                git_branch = rb.stdout.strip()
            stp = _git(["status", "--porcelain"])
            if stp.returncode == 0:
                git_changes = len([l for l in stp.stdout.splitlines() if l.strip()])
                git_dirty = git_changes > 0
            lg = _git(["log", "-1", "--pretty=%h %s"])
            if lg.returncode == 0 and lg.stdout.strip():
                git_last_commit = lg.stdout.strip()[:80]
        except Exception:
            pass

    # ── 运行中 ──
    procs = (_processes() or {}).get("processes") or {}
    running_procs = [p for p in procs.values() if isinstance(p, dict) and not p.get("exited")]
    scheds = (_schedules_get() or {}).get("schedules") or []
    enabled_scheds = [s for s in scheds if s.get("enabled")]
    cp = _checkpoint_get() or {}
    has_cp = bool(cp.get("name") or cp.get("status") or cp.get("pending") or cp.get("notes"))

    # ── 健康（即时项，无实时探测）──
    deps = (_deps() or {}).get("deps") or []
    missing_deps = [d for d in deps if not d.get("ok")]
    backups = (_backup_list() or {}).get("backups") or []
    disk_free_gb = None
    try:
        import shutil
        du = shutil.disk_usage(active_dir if (active_dir and os.path.isdir(active_dir)) else os.getcwd())
        disk_free_gb = round(du.free / 1024 ** 3, 1)
    except Exception:
        pass

    core = {
        "system": {
            "mode": st.get("mode"),
            "full_auto": st.get("full_auto"),
            "pure_chat": st.get("pure_chat"),
            "privacy": st.get("privacy"),
            "model": st.get("model"),
            "role": st.get("role"),
            "scenario": st.get("scenario"),
            "thinking": st.get("thinking"),
        },
        "usage": {
            "month_cost": st.get("monthly_cost"),
            "month_budget": st.get("monthly_budget"),
            "prompt_tokens": u.get("prompt", 0),
            "completion_tokens": u.get("completion", 0),
            "cache_hit": u.get("cache_hit", 0),
            "peak_hour": st.get("peak_hour"),
        },
        "running": {
            "process_count": len(running_procs),
            "process_total": len(procs),
            "schedule_count": len(enabled_scheds),
            "next_schedule": (enabled_scheds[0].get("next_run") if enabled_scheds else None),
            "has_checkpoint": has_cp,
        },
        "health": {
            "deps_missing": len(missing_deps),
            "deps_total": len(deps),
            "missing_deps": [d.get("name") for d in missing_deps][:10],
            "last_backup": (backups[0].get("mtime") if backups else None),
            "disk_free_gb": disk_free_gb,
        },
        "todo": {
            "checkpoint_name": cp.get("name"),
            "checkpoint_pending": (len(cp.get("pending") or []) if isinstance(cp.get("pending"), list) else 0),
        },
        "project": {
            "active_dir": active_dir,
            "git_branch": git_branch,
            "git_dirty": git_dirty,
            "git_changes": git_changes,
            "git_last_commit": git_last_commit,
        },
    }

    if not section:
        return core

    out = dict(core)

    if section in ("recent", "full"):
        _ensure_session_index()
        metas = [v[2] for v in _SESSIONS_INDEX.values() if isinstance(v, list) and len(v) == 3]
        metas.sort(key=lambda s: s.get("saved_at") or "", reverse=True)
        files = _files()
        out["recent"] = {
            "sessions": [{
                "id": m.get("id"),
                "name": m.get("name"),
                "msg_count": m.get("msg_count"),
                "saved_at": m.get("saved_at"),
            } for m in metas[:8]],
            "files": (files.get("recent") or [])[-8:],
            "active_dir": files.get("active_dir"),
        }

    if section in ("processes", "full"):
        out["processes"] = procs

    if section in ("schedules", "full"):
        out["schedules"] = scheds

    if section in ("checkpoint", "full"):
        out["checkpoint"] = cp

    if section in ("health", "full"):
        out["health"] = _full_health(active_dir)

    return out


def _files():
    """文件与产物：最近产物 + 工作区顶层条目。"""
    import stores
    recent = stores.load_recent(RECENT_PATH)[-30:]
    active_dir = _status()["active_dir"]
    entries = []
    try:
        for fn in sorted(os.listdir(active_dir)):
            p = os.path.join(active_dir, fn)
            try:
                st = os.stat(p)
                entries.append({
                    "name": fn,
                    "path": p,
                    "is_dir": os.path.isdir(p),
                    "size": st.st_size if os.path.isfile(p) else 0,
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                })
            except Exception:
                continue
    except Exception:
        pass
    return {"active_dir": active_dir, "recent": recent, "entries": entries[:60]}


def _tasks():
    """任务与模板：内置任务模板 + 试玩任务。"""
    try:
        import templates as templates_mod
        return {
            "templates": templates_mod.TASK_TEMPLATES,
            "playground": templates_mod.PLAYGROUND_TASKS,
        }
    except Exception:
        return {"templates": [], "playground": []}


def _evolutions():
    """自我进化：evolutions/ 提案分支列表。"""
    out = []
    if os.path.isdir(EVOLUTIONS_DIR):
        for d in sorted(os.listdir(EVOLUTIONS_DIR), reverse=True):
            p = os.path.join(EVOLUTIONS_DIR, d)
            if not os.path.isdir(p):
                continue
            files = []
            try:
                files = [f for f in os.listdir(p) if os.path.isfile(os.path.join(p, f))]
            except Exception:
                pass
            out.append({
                "name": d,
                "applied": d.endswith("_applied"),
                "files": files[:20],
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))),
            })
    return {"evolutions": out[:40]}


# ── 指令库（prompts.json：用户指令 + 内置指令，统一管理与调用）──────────
# 数据模型向后兼容：老数据仅 {name, text}，缺字段一律补默认值（绝不丢数据）。
_PROMPT_MAX_TEXT = 4000
_PROMPT_MAX_NAME = 40


def _prompt_new_id():
    import uuid
    return "p_" + uuid.uuid4().hex[:10]


def _prompt_normalize(p):
    """规范化单条指令：老数据（仅 name/text）自动补全新字段。"""
    if not isinstance(p, dict):
        return None
    name = str(p.get("name") or "").strip()[:_PROMPT_MAX_NAME]
    if not name:
        return None
    try:
        cnt = int(p.get("use_count") or 0)
    except (TypeError, ValueError):
        cnt = 0
    sc = str(p.get("shortcut") or "").strip()[:30]
    if sc and not sc.startswith("/"):
        sc = "/" + sc.lstrip("/")
    item = {
        "id": str(p.get("id") or "").strip() or _prompt_new_id(),
        "name": name,
        "text": str(p.get("text") or "")[:_PROMPT_MAX_TEXT],
        "desc": str(p.get("desc") or "").strip()[:200],
        "category": str(p.get("category") or "").strip()[:20] or "未分类",
        "tags": [str(t).strip()[:20] for t in (p.get("tags") or []) if str(t).strip()][:10],
        "icon": str(p.get("icon") or "").strip()[:8],
        "shortcut": sc,
        "enabled": p.get("enabled", True) is not False,
        "auto_send": bool(p.get("auto_send")),
        "use_count": max(0, cnt),
        "created": str(p.get("created") or ""),
        "updated": str(p.get("updated") or ""),
    }
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    if not item["created"]:
        item["created"] = now
    item["updated"] = now
    return item


def _prompts_load_user():
    """用户指令（prompts.json），已规范化。"""
    import stores
    items = stores.load_patterns(PROMPTS_PATH)
    out = []
    for p in items:
        n = _prompt_normalize(p)
        if n:
            out.append(n)
    return out


def _prompts_builtin():
    """内置指令（config_defaults.BUILTIN_PROMPTS）：只读，可复制为我的指令。"""
    import config_defaults as cd
    out = []
    for p in list(getattr(cd, "BUILTIN_PROMPTS", []) or []):
        n = _prompt_normalize(p)
        if n:
            n["builtin"] = True
            out.append(n)
    return out


def _prompts_full():
    """内置 + 用户（内置在前，带 builtin 标记）。"""
    return _prompts_builtin() + _prompts_load_user()


def _prompts_save_user(items):
    import stores
    clean = []
    for p in items:
        n = _prompt_normalize(p)
        if n:
            n.pop("builtin", None)
            clean.append(n)
    stores.save_patterns(PROMPTS_PATH, clean)
    return clean


def _plugin_skills():
    """已启用插件的提示词技能（只读来源：可在指令库「复制」为我的指令后修改）。"""
    import plugins as plugins_mod
    out = []
    try:
        for p in plugins_mod.list_plugins(_plugin_paths()["plugins_dir"]):
            if not p.get("enabled", True):
                continue
            pname = str((p.get("meta") or {}).get("name") or "")
            for s in ((p.get("contents") or {}).get("skills") or []):
                if isinstance(s, dict) and str(s.get("name") or "").strip():
                    out.append({
                        "name": str(s.get("name") or "").strip()[:40],
                        "text": str(s.get("text") or "")[:_PROMPT_MAX_TEXT],
                        "plugin": pname,
                    })
    except Exception:
        logger.exception("读取插件技能失败")
    return {"skills": out}


def _prompts():
    """指令列表（内置 + 用户）。"""
    return {"prompts": _prompts_full()}


def _prompt_upsert(body):
    """新增/修改单条用户指令（按 id 定位；无 id 或 id 不存在则新建）。"""
    p = body.get("prompt")
    if not isinstance(p, dict):
        return None, "prompt 必须是对象"
    items = _prompts_load_user()
    n = _prompt_normalize(p)
    if not n:
        return None, "指令名称不能为空"
    pid = str(p.get("id") or "").strip()
    hit = False
    for i, old in enumerate(items):
        if old["id"] == pid:
            n["created"] = old.get("created") or n["created"]
            n["use_count"] = old.get("use_count", 0)
            items[i] = n
            hit = True
            break
    if not hit:
        items.append(n)
    _prompts_save_user(items)
    return {"ok": True, "id": n["id"], "created": not hit}, None


def _prompt_delete(body):
    """删除单条用户指令（内置指令不可删）。"""
    pid = str(body.get("id") or "").strip()
    if not pid:
        return None, "id 必填"
    items = _prompts_load_user()
    left = [p for p in items if p["id"] != pid]
    if len(left) == len(items):
        return None, "未找到该指令（内置指令不可删除）"
    _prompts_save_user(left)
    return {"ok": True}, None


def _prompts_reorder(body):
    """排序：按给定 id 顺序重排用户指令（未列出的保持相对顺序附在末尾）。"""
    ids = body.get("ids")
    if not isinstance(ids, list):
        return None, "ids 必须是列表"
    items = _prompts_load_user()
    by_id = {p["id"]: p for p in items}
    out = [by_id.pop(i) for i in ids if i in by_id]
    out += [p for p in items if p["id"] in by_id]
    _prompts_save_user(out)
    return {"ok": True}, None


def _prompts_import(body):
    """导入指令：mode=merge（默认，跳过同名）/ replace（覆盖全部用户指令）。"""
    incoming = body.get("prompts")
    if not isinstance(incoming, list):
        return None, "prompts 必须是列表"
    mode = str(body.get("mode") or "merge").strip().lower()
    items = _prompts_load_user()
    if mode == "replace":
        items = []
    added = 0
    for p in incoming:
        n = _prompt_normalize(p)
        if not n:
            continue
        if mode == "merge" and any(x["name"] == n["name"] for x in items):
            continue
        items.append(n)
        added += 1
    _prompts_save_user(items)
    return {"ok": True, "added": added, "total": len(items)}, None


def _prompt_use(body):
    """使用计数（常用指令置顶排序用）。"""
    pid = str(body.get("id") or "").strip()
    items = _prompts_load_user()
    for p in items:
        if p["id"] == pid:
            p["use_count"] = int(p.get("use_count") or 0) + 1
            break
    _prompts_save_user(items)
    return {"ok": True}, None


def _prompts_restore_builtin():
    """恢复内置指令：只补缺失项（按名称），绝不覆盖用户已有/改动过的指令。"""
    import config_defaults as cd
    items = _prompts_load_user()
    have = {p["name"] for p in items}
    added = 0
    for p in list(getattr(cd, "BUILTIN_PROMPTS", []) or []):
        n = _prompt_normalize(p)
        if n and n["name"] not in have:
            items.append(n)
            have.add(n["name"])
            added += 1
    _prompts_save_user(items)
    return {"ok": True, "added": added}, None


def _dirs():
    """工作目录：当前 active_dir + workspace 子目录 + 权限允许目录。"""
    import permissions as perms
    status = _status()
    active = status["active_dir"]
    allowed = []
    try:
        d = perms.get_data()
        allowed = [str(x) for x in d["filesystem"].get("allowed_dirs", [])][:20]
    except Exception:
        pass
    subs = []
    try:
        for fn in sorted(os.listdir(active)):
            p = os.path.join(active, fn)
            if os.path.isdir(p) and not fn.startswith("."):
                subs.append(p)
    except Exception:
        pass
    return {"active_dir": active, "workspace": WORKSPACE_DIR, "subdirs": subs[:40], "allowed_dirs": allowed}


def _set_dir(body):
    """切换工作目录：校验存在 → 写入 cfg + active_dir + 自动加入权限允许目录。"""
    import config_utils
    import permissions as perms
    import deepseek_client as dc
    path = str(body.get("path") or "").strip()
    if not path:
        return None, "缺少 path"
    p = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(p):
        return None, f"目录不存在：{p}"
    cfg = config_utils.load_config()
    cfg["active_dir"] = p
    config_utils.save_config(cfg)
    try:
        dc.WORKING_DIR = p
    except Exception:
        pass
    try:
        d = perms.get_data()
        allowed = [str(x) for x in d["filesystem"].get("allowed_dirs", [])]
        if p not in allowed:
            allowed.append(p)
            d["filesystem"]["allowed_dirs"] = allowed
            perms.set_data(d)
            perms.save()
    except Exception:
        pass
    return {"ok": True, "active_dir": p}, None


# ── 图片自动压缩（上传层兜底：超限图片程序级压缩，用户无需手动处理）──────────
_IMAGE_UPLOAD_MAX = 32 * 1024 * 1024       # 上传压缩触发线（≥ 此值自动压缩）
_IMAGE_COMPRESS_TARGET = 8 * 1024 * 1024   # 压缩目标 ≤8MB：远小于内联层 32MB 单张 / 40MB base64 总量上限
_IMAGE_COMPRESS_MAX_SIDE = 2048            # 最大边长：视觉模型内部还会缩放，2048 足够阅读/浏览
_IMAGE_COMPRESS_MIN_SIDE = 512             # 最小边长兜底（保证极端图也能收敛）
_IMAGE_COMPRESS_QUALITY = (85, 70, 55, 40)  # JPEG 质量阶梯：尺寸不变逐级降质，再缩小重试


def _auto_compress_image(raw, target=_IMAGE_COMPRESS_TARGET, max_side=_IMAGE_COMPRESS_MAX_SIDE,
                         min_side=_IMAGE_COMPRESS_MIN_SIDE, quality_steps=_IMAGE_COMPRESS_QUALITY):
    """超限图片自动压缩（Pillow 等比缩放 + 降质）。返回 (data, new_ext, note)。

    - note 非空 = 已尝试压缩（成功给出压缩说明 / 失败给出原因）；空串 = 未压缩。
    - 成功保证 len(data) <= target：尺寸从 max_side 逐级降到 min_side，每级质量从高到低，
      命中即停；兜底输出最小尺寸 + 最低质量（必然达标，循环必收敛）。
    - 保留 alpha 的 PNG 输出 PNG（透明不丢）；其余统一转 JPEG（压缩率最高），
      透明图转 JPEG 时先合成白底（避免黑底）。
    - GIF 动图超限时取首帧转静态图（note 注明）。
    - Pillow 缺失 / 图片解析失败：返回原数据 + note 说明，调用方按原逻辑拒绝。
    """
    try:
        from PIL import Image
    except Exception:
        return raw, ".jpg", "Pillow 未安装，无法自动压缩（pip install pillow）"
    import io
    try:
        src = Image.open(io.BytesIO(raw))
        src.load()
    except Exception:
        return raw, ".jpg", "图片解析失败，无法自动压缩"
    fmt = (src.format or "").upper()
    animated = fmt == "GIF" and getattr(src, "n_frames", 1) > 1
    if animated:
        try:
            src.seek(0)
            src.load()
        except Exception:
            pass
    has_alpha = src.mode in ("RGBA", "LA") or (src.mode == "P" and "transparency" in src.info)
    ow, oh = src.size
    parts = []
    if animated:
        parts.append("动图已转为静态首帧")
    out_is_png = has_alpha and fmt != "JPEG"

    def _to_rgb(im):
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            return bg
        return im.convert("RGB")

    if out_is_png:
        base = src
    else:
        base = _to_rgb(src)
        if has_alpha:
            parts.append("透明背景已转为白底")

    def _save(im, side, kind, quality=None):
        w, h = im.size
        m = max(w, h)
        if m > side:
            k = side / m
            w, h = max(1, int(w * k)), max(1, int(h * k))
            im = im.resize((w, h), Image.LANCZOS)
        b = io.BytesIO()
        if kind == "PNG":
            im.save(b, format="PNG", optimize=True)
        else:
            im.save(b, format="JPEG", quality=quality)
        return b.getvalue(), w, h

    data = None
    out_w = out_h = 0
    if out_is_png:
        data, out_w, out_h = _save(base, max_side, "PNG")
        if len(data) > target:
            # 透明 PNG 缩放后仍超限（极端）：转 JPEG 白底继续压
            base = _to_rgb(src)
            if "透明背景已转为白底" not in parts:
                parts.append("透明背景已转为白底")
            out_is_png = False
            data = None
    if not out_is_png:
        side = max_side
        while side >= min_side:
            for q in quality_steps:
                cand, w, h = _save(base, side, "JPEG", q)
                if len(cand) <= target:
                    data, out_w, out_h = cand, w, h
                    break
            if data is not None:
                break
            nxt = max(min_side, int(side * 0.8))
            if nxt == side:
                break
            side = nxt
        if data is None:
            data, out_w, out_h = _save(base, min_side, "JPEG", quality_steps[-1])
    new_ext = ".png" if out_is_png else ".jpg"
    size_note = "%.1fMB → %.1fMB" % (len(raw) / 1048576.0, len(data) / 1048576.0)
    geo_note = "%dx%d → %dx%d" % (ow, oh, out_w, out_h)
    extra = "，" + "，".join(parts) if parts else ""
    return data, new_ext, "已自动压缩（%s，%s%s）" % (size_note, geo_note, extra)


def _upload(body):
    """图片上传：base64 → 超限自动压缩 → DATA_DIR/uploads/<ts>.<ext>，返回本地路径。"""
    import base64
    b64 = str(body.get("image") or "")
    name = str(body.get("name") or "image.png")
    if not b64:
        return None, "缺少 image（base64，data: 前缀可省略）"
    if "," in b64[:64] and b64.split(",", 1)[0].startswith("data:"):
        b64 = b64.split(",", 1)[1]
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        return None, "base64 解码失败"
    if not raw:
        return None, "图片为空"
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        ext = ".png"
    orig_len = len(raw)
    note = ""
    if orig_len > _IMAGE_UPLOAD_MAX:
        # 超限：程序级自动压缩兜底（Pillow 等比缩放 + 降质），压缩失败才拒绝
        data, ext, note = _auto_compress_image(raw)
        if data is raw:
            return None, f"图片超过 {_IMAGE_UPLOAD_MAX // (1024 * 1024)}MB，且{note}，请先压缩后重试"
        raw = data
    uploads = os.path.join(DATA_DIR, "uploads")
    os.makedirs(uploads, exist_ok=True)
    fn = f"{int(time.time() * 1000)}_{secrets_token(3)}{ext}"
    path = os.path.join(uploads, fn)
    try:
        with open(path, "wb") as f:
            f.write(raw)
    except Exception as e:
        return None, f"写入失败：{e}"
    result = {"path": path, "name": name, "size": len(raw)}
    if note:
        result.update({"original_size": orig_len, "compressed": True, "note": note})
    return result, None


def _permissions_get():
    """权限全景：安全模式 / 任务模式零审批 / 黑名单四项（默认全空=全放行）。"""
    import permissions as perms
    d = perms.get_data() or {}
    fs = d.get("filesystem") or {}
    sh = d.get("shell") or {}
    net = d.get("network") or {}
    return {
        "security_mode": perms.security_mode(),
        "full_auto": perms.is_full_auto(),
        "approval_actions": list(d.get("approval_actions") or []),
        "approval_timeout": int(d.get("approval_timeout") or 120),
        "approval_mode": str(d.get("approval_mode") or "auto"),
        "blocked_dirs": [str(x) for x in fs.get("blocked_dirs") or []],
        "max_write_size": int(fs.get("max_write_size") or 0),
        "shell_blocklist": [str(x) for x in sh.get("blocklist") or []],
        "shell_whitelist": [str(x) for x in sh.get("whitelist") or []],
        "network_blocklist": [str(x) for x in net.get("blocklist") or []],
        "allow_write": bool(fs.get("allow_write", True)),
        "allow_run_command": bool(sh.get("allow_run_command", True)),
    }


def _permissions_set(body):
    """更新黑名单：blocked_dirs / shell_blocklist / network_blocklist / approval_actions。"""
    import permissions as perms
    d = perms.get_data() or {}
    fields = {
        "blocked_dirs": ("filesystem", "blocked_dirs"),
        "shell_blocklist": ("shell", "blocklist"),
        "network_blocklist": ("network", "blocklist"),
        "approval_actions": (None, "approval_actions"),
        "shell_whitelist": ("shell", "whitelist"),
    }
    for key, (container, field) in fields.items():
        if key not in body:
            continue
        v = body[key]
        if not isinstance(v, list):
            return None, f"{key} 必须是列表"
        items = [str(x).strip() for x in v if str(x).strip()][:200]
        if container:
            d.setdefault(container, {})[field] = items
        else:
            d[field] = items
    if "approval_timeout" in body:
        try:
            d["approval_timeout"] = max(10, min(600, int(body["approval_timeout"])))
        except (TypeError, ValueError):
            pass
    perms.set_data(d)
    perms.save()
    return {"ok": True}, None


def _tool_schema(name):
    """工具契约：name/description/parameters（JSON Schema）。"""
    import deepseek_client as dc
    name = str(name or "")
    for t in dc.TOOLS:
        fn = t.get("function") or {}
        if fn.get("name") == name:
            return {
                "name": name,
                "description": str(fn.get("description") or ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                "custom": name not in dc.TOOL_CALL_MAP,
            }
    return None


def _tool_invoke(name, args):
    """测试台直调工具（用户主动发起，跳过审批闸门）。"""
    import deepseek_client as dc
    name = str(name or "")
    fn = dc.TOOL_CALL_MAP.get(name)
    if fn is None:
        return "该工具为交互式/自定义工具，无法在测试台直接调用，请在对话中触发"
    if not isinstance(args, dict):
        return "参数必须是 JSON 对象"
    try:
        result = fn(**args)
        _tool_bookkeeping(name, args, result)
        return str(result)[:20000]
    except TypeError as e:
        return f"工具参数错误: {e}"
    except Exception as e:
        _record_failure(name, str(e))
        return f"工具执行失败: {e}"


def _roles():
    """角色列表（内置 + 用户自定义）+ 当前生效角色。"""
    import roles as roles_mod
    import stores
    out = []
    for name, r in roles_mod.ROLES.items():
        out.append({"name": str(name), "prompt": str(r.get("prompt") or ""), "thinking": str(r.get("thinking") or "high")})
    try:
        for r in stores.load_patterns(USER_ROLES_PATH):
            if isinstance(r, dict) and r.get("name"):
                out.append({
                    "name": str(r.get("name") or ""),
                    "prompt": str(r.get("prompt") or ""),
                    "thinking": str(r.get("thinking") or "high"),
                })
    except Exception:
        pass
    cfg_prompt = ""
    try:
        import config_utils
        cfg_prompt = str(config_utils.load_config().get("system_prompt") or "")
    except Exception:
        pass
    return {"roles": out, "current_prompt": cfg_prompt}


def _processes():
    """后台进程快照：{name: {pid, started, exited, code, lines}}。"""
    import deepseek_client as dc
    out = {}
    try:
        for name, entry in dc.snapshot_processes():
            out[name] = {
                "pid": entry.get("pid"),
                "started": str(entry.get("started") or ""),
                "exited": bool(entry.get("exited")),
                "code": entry.get("code"),
                "lines": [str(x)[:4096] for x in list(entry.get("lines") or [])[-500:]],
            }
    except Exception:
        logger.exception("读取进程失败")
    return {"processes": out}


def _stop_process(body):
    import deepseek_client as dc
    name = str(body.get("name") or "")
    if not name:
        return None, "缺少 name"
    try:
        result = dc.stop_process(name)
        return {"ok": True, "result": str(result)[:500]}, None
    except Exception as e:
        return None, str(e)


def _start_process(body):
    import deepseek_client as dc
    command = str(body.get("command") or "")
    name = str(body.get("name") or "")
    if not command:
        return None, "缺少 command"
    try:
        result = dc.start_process(command, name=name)
        return {"ok": True, "result": str(result)[:500]}, None
    except Exception as e:
        return None, str(e)


def _list_dir(path):
    """列目录（文件面板树，懒加载）。"""
    path = os.path.abspath(os.path.expanduser(str(path or "")))
    if not os.path.isdir(path):
        return None, "目录不存在"
    entries = []
    try:
        for fn in sorted(os.listdir(path)):
            p = os.path.join(path, fn)
            if fn.startswith(".") or fn in ("__pycache__", ".venv", "node_modules", ".git", "dist", "build"):
                continue
            try:
                st = os.stat(p)
                entries.append({
                    "name": fn,
                    "path": p,
                    "is_dir": os.path.isdir(p),
                    "size": st.st_size if os.path.isfile(p) else 0,
                    "mtime": time.strftime("%m-%d %H:%M", time.localtime(st.st_mtime)),
                })
            except Exception:
                continue
    except Exception:
        pass
    return {"path": path, "entries": entries[:300]}, None


def _read_file(path, max_chars=30000):
    """读文件内容（注入输入框）。仅文本类扩展名。"""
    path = os.path.abspath(os.path.expanduser(str(path or "")))
    if not os.path.isfile(path):
        return None, "文件不存在"
    ext = os.path.splitext(path)[1].lower()
    if ext in (".exe", ".dll", ".bin", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".zip", ".7z", ".rar", ".pdf"):
        return None, "二进制文件不支持读取"
    try:
        size = os.path.getsize(path)
        if size > 10 * 1024 * 1024:
            return None, "文件超过 10MB，请直接交给 AI 用 read_file 处理"
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(max_chars + 1)
        truncated = len(content) > max_chars
        return {"path": path, "content": content[:max_chars], "truncated": truncated, "size": size}, None
    except Exception as e:
        return None, str(e)


_IMAGE_PREVIEW_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico"}
_IMAGE_PREVIEW_MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".bmp": "image/bmp", ".ico": "image/x-icon",
}
_TEXT_PREVIEW_EXTS = {".md", ".txt", ".json", ".log", ".py", ".js", ".ts", ".html", ".htm", ".css", ".xml", ".yaml", ".yml", ".ini", ".toml", ".sql", ".jsx", ".tsx", ".sh", ".bat"}
# 浏览器可直接内嵌预览；其余提示「用系统程序打开」。（csv 单独走表格分支）
_PREVIEW_INLINE_TEXT = {".md", ".txt", ".html", ".htm", ".json", ".log", ".xml", ".yaml", ".yml", ".ini", ".toml", ".py", ".js", ".ts", ".jsx", ".tsx", ".css", ".sql", ".sh", ".bat"}


def _file_preview(path, max_chars=16000):
    """产物内嵌预览：提取可内嵌内容（图片→data URI；文本/markdown/HTML→内容）。

    返回 (result, err)。result 含 type/name/ext/size/truncated + data_uri 或 content。
    不改变原文件、只读；二进制/不可内嵌类型返回元信息供前端引导用系统程序打开。
    """
    try:
        path = os.path.abspath(os.path.expanduser(str(path or "")))
    except Exception as e:
        return None, str(e)
    if not path or not os.path.isfile(path):
        return None, "文件不存在"
    ext = os.path.splitext(path)[1].lower()
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return None, str(e)

    base = {"path": path, "name": name, "ext": ext, "size": size}

    # 图片：base64 data URI（≤3MB，防爆内存）
    if ext in _IMAGE_PREVIEW_EXTS:
        if size > 3 * 1024 * 1024:
            base.update({"previewable": False, "reason": "图片超过 3MB，不内嵌预览"})
            return base, None
        try:
            import base64
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            mime = _IMAGE_PREVIEW_MIME.get(ext, "image/png")
            base.update({"previewable": True, "kind": "image", "data_uri": f"data:{mime};base64,{b64}"})
            return base, None
        except Exception as e:
            base.update({"previewable": False, "reason": f"读取失败: {e}"})
            return base, None

    # 文本类：读内容（markdown/html 前端直接渲染，其余纯文本展示）
    if ext in _TEXT_PREVIEW_EXTS:
        if size > 2 * 1024 * 1024:
            base.update({"previewable": False, "reason": "文本超过 2MB，不内嵌预览"})
            return base, None
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_chars + 1)
            truncated = len(content) > max_chars
            inline = ext in _PREVIEW_INLINE_TEXT
            base.update({
                "previewable": True, "kind": "html" if ext in (".html", ".htm") else ("md" if ext == ".md" else "text"),
                "content": content[:max_chars], "truncated": truncated, "inline": inline,
            })
            return base, None
        except Exception as e:
            base.update({"previewable": False, "reason": f"读取失败: {e}"})
            return base, None

    # CSV：解析为表格行（前端分页展示）
    if ext == ".csv":
        if size > 2 * 1024 * 1024:
            base.update({"previewable": False, "reason": "CSV 超过 2MB，不内嵌预览"})
            return base, None
        try:
            import csv as _csv, io as _io
            with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
                rows = list(_csv.reader(f))
            max_rows, max_cols = 200, 30
            header = rows[0] if rows else []
            body = rows[1:max_rows + 1]
            # 统一列数 + 单格限长，前端表格不炸
            width = min(max(len(header), max((len(r) for r in body), default=0)), max_cols)
            def norm(r):
                rr = [str(c)[:200] for c in r]
                return (rr + [""] * (width - len(rr)))[:width]
            base.update({
                "previewable": True, "kind": "table",
                "header": norm(header or body[0] or []),
                "rows": [norm(r) for r in body[:max_rows]],
                "total_rows": max(0, len(rows) - 1),
                "truncated": len(rows) - 1 > max_rows,
            })
            return base, None
        except Exception as e:
            base.update({"previewable": False, "reason": f"CSV 解析失败: {e}"})
            return base, None

    # PDF：用 PyMuPDF 提取首页文本预览（≤10MB，缺依赖时提示）
    if ext in (".pdf", ".xlsx", ".docx", ".pptx"):
        if size > 10 * 1024 * 1024:
            base.update({"previewable": False, "reason": "文档超过 10MB，不内嵌预览"})
            return base, None
        try:
            preview_text = ""
            if ext == ".pdf":
                import fitz  # PyMuPDF
                page_count = 0
                with fitz.open(path) as doc:
                    page_count = doc.page_count
                    if page_count:
                        preview_text = doc[0].get_text()[:16000]
                base.update({"previewable": True, "kind": "pdf", "page_count": page_count, "content": preview_text or "", "inline": False})
            elif ext == ".xlsx":
                import openpyxl
                wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
                ws = wb.active
                rows = [[str(c.value)[:120] if c.value is not None else "" for c in row] for row in ws.iter_rows(max_row=30, max_col=20)]
                wb.close()
                base.update({"previewable": True, "kind": "table", "header": rows[0] if rows else [], "rows": rows[1:30], "total_rows": 30, "truncated": True})
            else:
                base.update({"previewable": True, "kind": "doc", "content": "", "inline": False})
            return base, None
        except ImportError:
            base.update({"previewable": False, "reason": f"预览 {ext} 需要额外依赖（PyMuPDF/openpyxl），可先用系统程序打开"})
            return base, None
        except Exception as e:
            base.update({"previewable": False, "reason": f"文档预览失败: {e}"})
            return base, None

    # 其余（zip/rar 等）：不可内嵌，提示用系统程序打开
    base.update({"previewable": False, "reason": "该格式暂不支持内嵌预览，请用系统程序打开"})
    return base, None


_EXEC_LIKE_EXTS = (
    ".exe", ".bat", ".cmd", ".com", ".ps1", ".vbs", ".msi", ".reg", ".scr", ".jar",
)


def open_path(path):
    """用系统默认程序打开文件（web 侧无确认弹窗，可执行类一律拒绝）。"""
    try:
        path = os.path.abspath(os.path.expanduser(str(path or "")))
        if not path or path == os.path.abspath(""):
            return None, "路径为空"
        if not os.path.exists(path):
            return None, "路径不存在"
        if os.path.isfile(path) and str(path).lower().endswith(_EXEC_LIKE_EXTS):
            return None, "出于安全考虑，不允许直接打开可执行/脚本文件"
        os.startfile(path)
        return {"ok": True}, None
    except Exception as e:
        return None, str(e)


def open_dir(path):
    """打开所在文件夹（文件存在则定位选中）。"""
    try:
        path = os.path.abspath(os.path.expanduser(str(path or "")))
        if not path or path == os.path.abspath(""):
            return None, "路径为空"
        if not os.path.exists(path):
            return None, "路径不存在"
        if os.path.isfile(path):
            dirpath = os.path.dirname(path)
            subprocess.Popen(["explorer", "/select,", path])
        else:
            dirpath = path
            os.startfile(dirpath)
        return {"ok": True, "dir": dirpath}, None
    except Exception as e:
        return None, str(e)


def _record_usage(usage, cfg, body):
    """API 用量统计落盘（stats.json，对齐原程序 _apply_usage/_flush_stats）。"""
    try:
        import stats as stats_mod
        model = str(body.get("model") or cfg.get("model") or "unknown")
        stats_mod.record_usage(STATS_PATH, model, usage or {})
    except Exception:
        logger.exception("用量统计落盘失败")


def _models():
    """模型详情（官方能力：上下文/输出上限/版本）。"""
    import deepseek_client as dc
    out = []
    for name, meta in (dc.MODELS or {}).items():
        out.append({
            "name": name,
            "label": str(meta.get("label") or name),
            "version": str(meta.get("version") or ""),
            "max_context_tokens": int(meta.get("max_context_tokens") or 1000000),
            "max_output_tokens": int(meta.get("max_output_tokens") or 393216),
        })
    return {"models": out}


def _friendly_error(e):
    """官方错误码 → 中文可操作提示。"""
    s = str(e)
    low = s.lower()
    if "429" in low or "rate limit" in low or "too many requests" in low:
        return "请求过于频繁（限速）——请稍等片刻再试"
    if "401" in low or "invalid api key" in low or "authentication" in low or "unauthorized" in low:
        return "API Key 无效或未配置——请在设置中检查 api_key"
    if "403" in low or "forbidden" in low:
        return "访问被拒绝（403）——检查账户权限或配额"
    if "model does not support image" in low or "does not support image" in low:
        return "当前模型不支持图片输入——请切换视觉模型 deepseek-v4-flash-vision-exp"
    if "model not found" in low or "invalid model" in low or "model does not exist" in low:
        return "模型不存在或不可用——请检查设置中的模型名（可输入任意 OpenAI 兼容模型）"
    if "reasoning_content" in low and "400" in low:
        return "推理链回传缺失（400）——请刷新会话后重试，或切换到对话模式"
    if "insufficient_system_resource" in low:
        return "系统推理资源不足，生成被打断——请稍后重试"
    if "content_filter" in low:
        return "输出触发内容过滤策略——请调整表述后重试"
    return s


def _init_dc_paths():
    """注入外部配置文件路径与全局（完整对齐旧 main.py 启动注入）。

    v3.0 重构删除 main.py 后，这里成为唯一的应用接线点。凡是漏接的全局，
    对应工具都会静默失效（如 MEMORY_FILE 未接 → AI 任务中存不下长期记忆）。
    """
    import deepseek_client as dc
    import config_utils
    import profiles as profiles_mod
    profiles_mod.DEFAULT_PROFILES_PATH = os.path.join(DATA_DIR, "profiles.json")
    dc.MEMORY_FILE = MEMORY_PATH
    dc.SELF_PROFILE_FILE = os.path.join(DATA_DIR, "self_profile.json")
    dc.SESSIONS_DIR = SESSIONS_DIR
    dc.WATCH_STATE_PATH = os.path.join(DATA_DIR, "watch_state.json")
    dc.WEBHOOK_CONFIG_FILE = os.path.join(DATA_DIR, "webhooks.json")
    dc.IM_CONFIG_FILE = os.path.join(DATA_DIR, "im_config.json")
    dc.DB_CONFIG_FILE = os.path.join(DATA_DIR, "db_config.json")
    dc.EMAIL_CONFIG_FILE = os.path.join(DATA_DIR, "email_config.json")
    # ↓ v3.1.2 补齐的断链接线（对齐旧 main.py 注入清单）↓
    dc.SECRETS_FILE = os.path.join(DATA_DIR, "secrets.json")
    dc.BROWSER_PROFILE_DIR = os.path.join(DATA_DIR, "browser_profile")
    dc.SCHEDULES_FILE = SCHEDULES_PATH
    dc.KNOWLEDGE_INDEX_FILE = os.path.join(DATA_DIR, "knowledge_index.json")
    dc.WORKFLOWS_FILE = WORKFLOWS_PATH
    dc.CHECKPOINT_FILE = CHECKPOINT_PATH
    dc.STATS_FILE = STATS_PATH
    dc.PATTERNS_FILE = PATTERNS_PATH
    dc.RSS_SOURCES_FILE = os.path.join(DATA_DIR, "rss_sources.json")
    dc.KV_CACHE_DIR = os.path.join(DATA_DIR, "kv_cache")
    dc.WEBDAV_CONFIG_FILE = os.path.join(DATA_DIR, "webdav_config.json")
    dc.PLUGIN_PATHS = _plugin_paths()
    dc.BUILD_SITUATION = build_situation  # 态势快照单一事实源（get_status 工具与前端 /v1/situation 共用）
    os.environ.setdefault("WHALETALK_DATA_DIR", DATA_DIR)  # 应用型插件数据目录（flybot.db 等）
    try:
        cfg = config_utils.load_config()
        dc.IMAGE_GEN_KEY = str(cfg.get("image_api_key") or "").strip() or str(cfg.get("api_key") or "").strip()
        dc.IMAGE_GEN_BASE = str(cfg.get("image_base_url") or "").strip() or (str(cfg.get("base_url") or "").strip())
        dc.IMAGE_GEN_MODEL = str(cfg.get("image_model") or "gpt-image-1").strip()
        dc.VISION_SELF_REVIEW = bool(cfg.get("vision_self_review"))
        dc.BROWSER_HEADLESS = bool(cfg.get("browser_headless"))
        dc.MEMORY_ENABLED = bool(cfg.get("memory_enabled", True))
        dc.AGENT_MAIL_ENABLED = bool(cfg.get("agent_mail_enabled", False))
        dc.AGENT_MAIL_CLI = str(cfg.get("agent_mail_cli") or "agently-cli").strip() or "agently-cli"
        dc.CHART_THEME = str(cfg.get("theme") or "dark")
        # 工作目录启动恢复（run_command/start_process 的 cwd 跟随；_set_dir 运行期更新）
        active_dir = str(cfg.get("active_dir") or "").strip()
        if active_dir and os.path.isdir(active_dir):
            dc.WORKING_DIR = active_dir
    except Exception:
        logger.exception("_init_dc_paths 配置同步部分失败（不影响启动）")


def _record_failure(name, result):
    """工具失败记录（failures.json：去重 + 上限 50，对齐原程序）。"""
    try:
        import stores
        items = stores.load_failures(FAILURES_PATH)
        err = str(result or "")[:120]
        key = (str(name), err[:50])
        for old in items:
            if (str(old.get("tool") or ""), str(old.get("error") or "")[:50]) == key:
                old["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
                break
        else:
            items.append({"tool": str(name), "error": err, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        if len(items) > 50:
            del items[: len(items) - 50]
        stores.save_failures(FAILURES_PATH, items)
    except Exception:
        pass


def _record_success_pattern(name, args, result):
    """成功模式记录（patterns.json：复用已验证工具链，上限 20）。"""
    try:
        import stores
        pats = stores.load_patterns(PATTERNS_PATH)
        entry = {
            "tool": str(name),
            "args": str(args or "")[:200],
            "result": str(result or "")[:200],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        pats = [p for p in pats if not (isinstance(p, dict) and p.get("tool") == name and p.get("args") == entry["args"])]
        pats.append(entry)
        pats = pats[-20:]
        stores.save_patterns(PATTERNS_PATH, pats)
    except Exception:
        pass


def _audit(action, target, detail=""):
    """审计写入（对齐原程序 permissions.audit）。"""
    try:
        log_dir = os.path.join(DATA_DIR, "logs")
        os.makedirs(log_dir, exist_ok=True)
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {action} {target} {str(detail)[:200]}"
        with open(os.path.join(log_dir, "actions.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _record_tasklog(title, chain):
    """任务记录写入（工作目录 .whaletalk/tasklog.json，上限 20）。"""
    try:
        import stores
        active_dir = _status()["active_dir"]
        path = os.path.join(active_dir, ".whaletalk", "tasklog.json")
        data = stores.load_tasklog(path)
        tasks = data.get("tasks") or []
        tasks.append({"title": str(title or "")[:200], "chain": [str(c)[:60] for c in (chain or [])][:20], "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
        tasks = tasks[-20:]
        data["tasks"] = tasks
        stores.save_tasklog(path, data)
    except Exception:
        pass


def _record_recent_output(result):
    """工具产物路径提取（recent_outputs.json，去重上限 50）。"""
    try:
        import stores
        recent = stores.load_recent(RECENT_PATH)
        for m in shared.PATH_RE.finditer(str(result or "")):
            p = m.group(0)
            if os.path.exists(p) and p not in recent:
                recent.append(p)
        if len(recent) > 50:
            recent = recent[-50:]
        stores.save_recent(RECENT_PATH, recent)
    except Exception:
        pass


def _decrypt_val(v):
    """解密 dpapi 密文（明文兼容）。"""
    try:
        import crypto
        return crypto.decrypt(str(v or ""))
    except Exception:
        return str(v or "")


def _encrypt_val(v):
    """加密敏感字段（空值不加密）。"""
    try:
        import crypto
        return crypto.encrypt(str(v)) if str(v).strip() else ""
    except Exception:
        return str(v)


def _read_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else default
    except Exception:
        pass
    return dict(default)


def _atomic_write_json(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _services_get():
    """外部服务配置全景（解密后返回）。"""
    import deepseek_client as dc
    import config_utils
    cfg = config_utils.load_config()
    out = {}
    try:
        out["webhooks"] = dc._load_webhooks() or {}
    except Exception:
        out["webhooks"] = {}
    try:
        im, _err = dc._load_im_config()
        out["im"] = im or {}
    except Exception:
        out["im"] = {}
    db = _read_json(os.path.join(DATA_DIR, "db_config.json"), {})
    for kind, conns in db.items():
        if isinstance(conns, dict):
            for c in conns.values():
                if isinstance(c, dict) and c.get("password"):
                    c["password"] = _decrypt_val(c["password"])
    out["db"] = db
    em = _read_json(os.path.join(DATA_DIR, "email_config.json"), {})
    if em.get("password"):
        em["password"] = _decrypt_val(em["password"])
    imap = em.get("imap") or {}
    if isinstance(imap, dict) and imap.get("password"):
        imap["password"] = _decrypt_val(imap["password"])
    em["imap"] = imap
    out["email"] = em
    out["agent_mail"] = {
        "enabled": bool(cfg.get("agent_mail_enabled")),
        "cli": str(cfg.get("agent_mail_cli") or "agently-cli"),
    }
    out["image"] = {
        "api_key": str(cfg.get("image_api_key") or ""),
        "base_url": str(cfg.get("image_base_url") or ""),
        "model": str(cfg.get("image_model") or "gpt-image-1"),
        "vision_self_review": bool(cfg.get("vision_self_review")),
    }
    out["inbound"] = {
        "port": int(cfg.get("inbound_port") or 0),
        "token": str(cfg.get("inbound_token") or ""),
    }
    return out


def _services_save(body):
    """保存外部服务配置（敏感字段 DPAPI 加密落盘）。"""
    import config_utils

    try:
        # ── Webhook ──
        if isinstance(body.get("webhooks"), dict):
            wh = {k: _encrypt_val(v) for k, v in body["webhooks"].items() if str(v).strip()}
            _atomic_write_json(os.path.join(DATA_DIR, "webhooks.json"), wh)
        # ── IM ──
        if isinstance(body.get("im"), dict):
            im = dict(body["im"])
            for k in ("telegram_bot_token",):
                if k in im:
                    im[k] = _encrypt_val(im[k])
            im = {k: v for k, v in im.items() if str(v or "").strip()}
            _atomic_write_json(os.path.join(DATA_DIR, "im_config.json"), im)
        # ── 数据库 ──
        if isinstance(body.get("db"), dict):
            db = {}
            for kind in ("mysql", "postgres"):
                conns = body["db"].get(kind) or {}
                if not isinstance(conns, dict):
                    continue
                db[kind] = {}
                for name, c in conns.items():
                    if not isinstance(c, dict):
                        continue
                    if not str(c.get("host") or "").strip() or not str(c.get("user") or "").strip():
                        continue
                    item = {
                        "host": str(c.get("host") or "").strip(),
                        "port": int(c.get("port") or (3306 if kind == "mysql" else 5432)),
                        "user": str(c.get("user") or "").strip(),
                        "database": str(c.get("database") or "").strip(),
                    }
                    if str(c.get("password") or "").strip():
                        item["password"] = _encrypt_val(c.get("password"))
                    db[kind][str(name) or "default"] = item
            _atomic_write_json(os.path.join(DATA_DIR, "db_config.json"), db)
        # ── 邮件 ──
        if isinstance(body.get("email"), dict):
            em = dict(body["email"])
            if em.get("password"):
                em["password"] = _encrypt_val(em["password"])
            else:
                em.pop("password", None)
            imap = em.get("imap")
            if isinstance(imap, dict):
                imap = dict(imap)
                if imap.get("password"):
                    imap["password"] = _encrypt_val(imap["password"])
                else:
                    imap.pop("password", None)
                em["imap"] = imap
            _atomic_write_json(os.path.join(DATA_DIR, "email_config.json"), em)
        # ── config.json 项（Agent Mail / 图片生成 / 接收端）──
        cfg = config_utils.load_config()
        patch = {}
        if isinstance(body.get("agent_mail"), dict):
            am = body["agent_mail"]
            patch["agent_mail_enabled"] = bool(am.get("enabled"))
            patch["agent_mail_cli"] = str(am.get("cli") or "agently-cli")
        if isinstance(body.get("image"), dict):
            img = body["image"]
            patch["image_api_key"] = str(img.get("api_key") or "").strip()
            patch["image_base_url"] = str(img.get("base_url") or "").strip()
            patch["image_model"] = str(img.get("model") or "gpt-image-1").strip()
            patch["vision_self_review"] = bool(img.get("vision_self_review"))
        if isinstance(body.get("inbound"), dict):
            ib = body["inbound"]
            try:
                patch["inbound_port"] = max(0, min(65535, int(ib.get("port") or 0)))
            except (TypeError, ValueError):
                pass
            patch["inbound_token"] = str(ib.get("token") or "").strip()
        if patch:
            cfg.update(patch)
            config_utils.save_config(cfg)
        return {"ok": True}, None
    except Exception as e:
        logger.exception("保存外部服务配置失败")
        return None, str(e)


def _deps():
    """可选依赖状态（deps.py OPTIONAL_DEPS）。"""
    import deps as deps_mod
    out = []
    for name, ok, usage, cmd in deps_mod.OPTIONAL_DEPS:
        out.append({"name": str(name), "ok": bool(ok), "usage": str(usage), "install": str(cmd)})
    return {"deps": out}


def _config_reset():
    """恢复默认配置（保留 api_key / inbound_token / image_api_key）。"""
    import config_utils
    import config_defaults
    cfg = config_utils.load_config()
    keep = {
        "api_key": cfg.get("api_key", ""),
        "inbound_token": cfg.get("inbound_token", ""),
        "image_api_key": cfg.get("image_api_key", ""),
        "active_dir": cfg.get("active_dir", ""),
        "full_auto": True,
        "pure_chat": False,
    }
    fresh = dict(config_defaults.DEFAULT_CONFIG)
    fresh.update(keep)
    config_utils.save_config(fresh)
    return {"ok": True}


def _context_size(messages):
    """上下文大小估算（token 优先，超长回退字符）。"""
    import tokens as tokens_mod
    total_tokens = 0
    total_chars = 0
    try:
        for m in messages:
            content = str(m.get("content") or "")
            total_chars += len(content)
            total_tokens += tokens_mod.estimate_text_tokens(content)
    except Exception:
        total_tokens = 0
    return total_tokens, total_chars


_SUMMARY_PROMPT = (
    "你是对话历史摘要器。请把提供的对话压缩为不超过 400 字的摘要，"
    "保留关键事实、已做的决定、未完成的任务与工具执行结果，"
    "摘要末尾另起一行输出「关键事实」小节（2-6 条）。只输出摘要本身。"
)


def _compress_messages(messages, cfg, client, max_rounds=6):
    """上下文压缩（对齐原程序双阈值 + LLM 摘要 + 硬裁剪回退 + 归档）。

    - 触发：token > max_context_tokens 或 字符 > max_context_chars
    - 保留 min_kept_turns 轮；被压缩内容写 ARCHIVES_DIR（隐私模式不写）
    - 摘要失败 → 硬裁剪回退
    返回 (new_messages, info_dict)。
    """
    max_tokens = int(cfg.get("max_context_tokens") or 400000)
    max_chars = int(cfg.get("max_context_chars") or 500000)
    kept_turns = max(3, int(cfg.get("min_kept_turns") or 8))
    tokens_total, chars_total = _context_size(messages)
    if tokens_total <= max_tokens and chars_total <= max_chars:
        return messages, None

    # 按 user 消息切轮次
    turns = []
    cur = []
    for m in messages:
        if m.get("role") == "user" and cur:
            turns.append(cur)
            cur = []
        cur.append(m)
    if cur:
        turns.append(cur)
    total = len(turns)
    if total <= kept_turns:
        return messages, None  # 轮次不足，交给服务端（1M 窗口足够）

    removed_turns = turns[: total - kept_turns]
    kept = [m for t in turns[total - kept_turns:] for m in t]
    removed_msgs = [m for t in removed_turns for m in t]

    # 归档被压缩内容
    archived_path = ""
    if not cfg.get("privacy_mode"):
        try:
            os.makedirs(ARCHIVES_DIR, exist_ok=True)
            name = f"session_{time.strftime('%Y%m%d_%H%M%S')}"
            archived_path = os.path.join(ARCHIVES_DIR, f"{name}.md")
            lines = [f"# 上下文压缩归档 {time.strftime('%Y-%m-%d %H:%M')}", ""]
            for m in removed_msgs:
                role = m.get("role")
                content = str(m.get("content") or "")
                if not content:
                    continue
                lines.append(f"## {role}")
                lines.append(content)
                lines.append("")
            with open(archived_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception:
            archived_path = ""

    # LLM 摘要
    summary_text = ""
    try:
        summary_messages = [{"role": "user", "content": _SUMMARY_PROMPT + "\n\n" + "\n".join(
            f"{m.get('role')}: {str(m.get('content') or '')[:4000]}" for m in removed_msgs if m.get("content")
        )[:60000]}]
        summary_client = client
        summary_parts = []
        summary_client.chat(
            summary_messages,
            thinking="none",
            max_tokens=1024,
            tools_enabled=False,
            on_content=lambda t: summary_parts.append(t),
        )
        summary_text = "".join(summary_parts).strip()
    except Exception:
        logger.exception("上下文摘要失败，回退硬裁剪")
        summary_text = ""

    if summary_text:
        summary_msg = {"role": "system", "content": f"[历史对话摘要]\n{summary_text}"}
        # 插在最早保留消息之前（不破坏后续轮次顺序）
        # 找到第一个非 system 位置之后插入
        insert_at = 0
        while insert_at < len(kept) and kept[insert_at].get("role") == "system":
            insert_at += 1
        kept.insert(insert_at, summary_msg)
        mode = "summary"
    else:
        mode = "trim"

    info = {
        "removed_turns": len(removed_turns),
        "removed_msgs": len(removed_msgs),
        "archived_path": archived_path or "",
        "mode": mode,
        "tokens_before": tokens_total,
    }

    # ── 压缩后二次校验：若仍超限（单轮过大/摘要过大），做更激进的兜底 ──
    # ① 先试保留更少轮次；② 仍超则截断超长消息内容；③ 仍超则丢弃最老的保留轮。
    # 避免"压缩后仍超限 → 请求直接被服务端拒绝"导致整轮卡死。
    try:
        t_after, c_after = _context_size(kept)
        rounds = 0
        while (t_after > max_tokens or c_after > max_chars) and rounds < 4:
            # 丢一条最老的保留消息（但保留首个 system 系统提示词，避免丢失人格/指令）
            if len(kept) > 1:
                drop_idx = 1 if kept[0].get("role") == "system" else 0
                if drop_idx < len(kept):
                    kept = kept[:drop_idx] + kept[drop_idx + 1:]
            # 再截断超长单条（>6000 字符压到 6000）
            for idx, m in enumerate(kept):
                c = m.get("content")
                if isinstance(c, str) and len(c) > 6000:
                    kept[idx] = dict(m)
                    kept[idx]["content"] = c[:6000] + "\n…[超长已截断]"
            t_after, c_after = _context_size(kept)
            rounds += 1
        if t_after > max_tokens or c_after > max_chars:
            # 终极兜底：只保留最后 kept_turns 轮且每轮截断（极少发生）
            kept = [dict(m) for m in messages[-max(8, kept_turns):]]
            for m in kept:
                if isinstance(m.get("content"), str) and len(m["content"]) > 6000:
                    m["content"] = m["content"][:6000] + "\n…[超长已截断]"
            info["mode"] = "trim"
            info["hard_trim"] = True
    except Exception:
        logger.exception("压缩后二次校验失败（不影响主流程）")

    return kept, info


def _global_search(query, filters=None):
    """全局搜索：跨全部会话文件匹配 content + reasoning_content（对齐原程序 search_all_sessions）。

    filters 可选：{tag, time_from, time_to, type}，其中 type 为 "message" | "artifact"。
    产物（artifact）搜索：匹配消息文本里的绝对文件路径（md/txt/png/...），便于跨会话找历史产物。
    """
    q = str(query or "").strip().lower()
    if not q or not os.path.isdir(SESSIONS_DIR):
        return {"results": []}
    filters = filters or {}
    ftype = (filters.get("type") or "message").lower()
    f_tag = str(filters.get("tag") or "").strip()
    ft_from = str(filters.get("time_from") or "").strip()
    ft_to = str(filters.get("time_to") or "").strip()
    # 产物路径匹配：以产物扩展名结尾的绝对路径
    art_re = re.compile(r"[A-Za-z]:[\\/][^\s\"“”“<>|,，;；。]*?[.](?:md|txt|json|csv|xlsx|docx|pptx|pdf|png|jpg|jpeg|html|htm|zip|py|log)\b", re.I)
    results = []
    for fn in os.listdir(SESSIONS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(SESSIONS_DIR, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        sess_name = str(d.get("name") or "未命名会话")
        sess_tags = d.get("tags") or []
        sess_time = str(d.get("saved_at") or "")
        # 标签过滤
        if f_tag and f_tag not in [str(t) for t in sess_tags]:
            continue
        # 时间过滤（saved_at 形如 YYYY-MM-DDTHH:MM:SS）
        if (ft_from or ft_to) and sess_time:
            date_part = sess_time[:10]
            if ft_from and date_part < ft_from:
                continue
            if ft_to and date_part > ft_to:
                continue
        msgs = d.get("messages") or []
        for i, m in enumerate(msgs):
            content = str(m.get("content") or "")
            if ftype == "artifact":
                # 只在产物路径中检索
                for am in art_re.finditer(content):
                    path = am.group(0).rstrip("，。、,;；:()")
                    if q in path.lower():
                        results.append({
                            "session_id": str(d.get("id") or fn[:-5]),
                            "session_name": sess_name,
                            "index": i, "role": str(m.get("role") or ""),
                            "snippet": path, "time": str(m.get("time") or ""), "kind": "artifact", "path": path,
                        })
                continue
            joined = (content + " " + str(m.get("reasoning_content") or "")).lower()
            if q not in joined:
                continue
            idx = content.lower().find(q)
            if idx >= 0:
                start = max(0, idx - 20)
                end = min(len(content), idx + len(q) + 40)
                snippet = content[start:end]
            else:
                snippet = str(m.get("reasoning_content") or "")[:80]
            results.append({
                "session_id": str(d.get("id") or fn[:-5]),
                "session_name": sess_name,
                "index": i, "role": str(m.get("role") or ""),
                "snippet": snippet, "time": str(m.get("time") or ""), "kind": "message",
            })
    results.sort(key=lambda r: (r["session_name"], r["index"]))
    return {"results": results[:300]}


def _evolution_apply(name):
    """采纳进化提案：备份原文件 .evobak → 覆盖；失败整体回滚；成功改名 _applied。"""
    import shutil as _shutil
    name = str(name or "")
    if not name or name.startswith(".") or "\\" in name or "/" in name or name.endswith("_applied"):
        return None, "非法提案名"
    branch = os.path.join(EVOLUTIONS_DIR, name)
    if not os.path.isdir(branch):
        return None, "提案不存在"
    EXTS = (".py", ".md", ".json", ".txt", ".bat", ".html")
    applied = []
    try:
        for fn in os.listdir(branch):
            if not fn.endswith(EXTS):
                continue
            src = os.path.join(branch, fn)
            dst = os.path.join(_ORIG_DIR, fn)
            if os.path.exists(dst):
                bak = dst + ".evobak"
                try:
                    if os.path.exists(bak):
                        os.remove(bak)
                    os.replace(dst, bak)
                except Exception:
                    raise RuntimeError(f"备份失败: {fn}")
            _shutil.copy2(src, dst)
            applied.append(fn)
        # 成功：目录改名 _applied
        new_name = name + "_applied"
        new_path = os.path.join(EVOLUTIONS_DIR, new_name)
        if os.path.exists(new_path):
            _shutil.rmtree(new_path)
        os.rename(branch, new_path)
        return {"ok": True, "applied": applied}, None
    except Exception as e:
        # 回滚
        for fn in applied:
            try:
                dst = os.path.join(_ORIG_DIR, fn)
                bak = dst + ".evobak"
                if os.path.exists(bak):
                    os.replace(bak, dst)
            except Exception:
                pass
        return None, f"采纳失败，已回滚: {e}"


def _evolution_ignore(name):
    """忽略提案：删除分支目录。"""
    import shutil as _shutil
    name = str(name or "")
    if not name or name.startswith(".") or "\\" in name or "/" in name or name.endswith("_applied"):
        return None, "非法提案名"
    branch = os.path.join(EVOLUTIONS_DIR, name)
    if not os.path.isdir(branch):
        return None, "提案不存在"
    _shutil.rmtree(branch)
    return {"ok": True}, None


def _evolution_detail(name):
    """提案详情：分支文件全文 + 原文件是否存在（供差异预览）。"""
    name = str(name or "")
    if not name or name.startswith(".") or "\\" in name or "/" in name:
        return None
    branch = os.path.join(EVOLUTIONS_DIR, name)
    if not os.path.isdir(branch):
        return None
    files = []
    for fn in sorted(os.listdir(branch)):
        p = os.path.join(branch, fn)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                content = f.read(20000)
        except Exception:
            continue
        original_exists = os.path.exists(os.path.join(_ORIG_DIR, fn))
        files.append({"name": fn, "content": content, "original_exists": original_exists})
    return {"name": name, "files": files}


def _schedule_next_run(item, now=None):
    """计算定时任务的下次运行时间字符串（time/cron/every），失败返回空串。"""
    from datetime import datetime as _dt, timedelta as _td
    now = now or _dt.now()
    try:
        if item.get("time"):
            t = _dt.strptime(str(item["time"]).strip(), "%H:%M")
            candidate = now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += _td(days=1)
            return candidate.strftime("%m-%d %H:%M")
        if item.get("every"):
            mins = max(1, int(item["every"]))
            nxt = now + _td(minutes=mins)
            return nxt.strftime("%m-%d %H:%M") + f"（每 {mins} 分钟）"
        if item.get("cron"):
            # 简单近似：只显示 cron 表达式，不精确解析（避免引入 croniter 依赖）
            return "cron " + str(item["cron"])[:40]
    except Exception:
        return ""
    return ""


def _schedules_get():
    """定时任务列表（附下次运行时间）。"""
    import stores
    items = stores.load_schedules(SCHEDULES_PATH)
    now = None
    for s in items:
        if s.get("enabled"):
            s["next_run"] = _schedule_next_run(s, now)
    return {"schedules": items}


def _schedules_save(body):
    """保存定时任务列表。"""
    import stores
    items = body.get("schedules")
    if not isinstance(items, list) or len(items) > 200:
        return None, "schedules 必须是列表（最多 200 条）"
    clean = []
    for s in items:
        if not isinstance(s, dict):
            continue
        item = {
            "enabled": bool(s.get("enabled", True)),
            "action": str(s.get("action") or "message"),
            "last": str(s.get("last") or ""),
            "last_run": 0,
            "text": str(s.get("text") or ""),
            "name": str(s.get("name") or "")[:40],
        }
        if s.get("off_peak"):
            item["off_peak"] = True
        mode_keys = [k for k in ("time", "cron", "every") if s.get(k)]
        if not mode_keys:
            continue
        k = mode_keys[0]
        item[k] = str(s[k])[:120]
        clean.append(item)
    stores.save_schedules(SCHEDULES_PATH, clean)
    return {"ok": True, "count": len(clean)}, None


_SCHEDULER_THREAD = None


def _scheduler_loop():
    """定时任务调度线程（30s 轮询）。"""
    import stores
    from datetime import datetime as _dt
    last_checked_minute = {}
    while True:
        try:
            items = stores.load_schedules(SCHEDULES_PATH)
            now = _dt.now()
            for s in items:
                if not s.get("enabled"):
                    continue
                action = str(s.get("action") or "message")
                fired = False
                if s.get("every"):
                    try:
                        every = max(1, int(s["every"]))
                    except (TypeError, ValueError):
                        continue
                    key = ("every", str(s.get("name") or s.get("text") or id(s)))
                    if key not in last_checked_minute:
                        last_checked_minute[key] = int(now.timestamp())
                    else:
                        if int(now.timestamp()) - last_checked_minute[key] >= every * 60:
                            fired = True
                            last_checked_minute[key] = int(now.timestamp())
                elif s.get("time"):
                    t = str(s["time"]).strip()
                    try:
                        if _dt.strptime(t, "%H:%M").strftime("%H:%M") == now.strftime("%H:%M") and s.get("last") != now.strftime("%Y-%m-%d"):
                            fired = True
                            s["last"] = now.strftime("%Y-%m-%d")
                    except Exception:
                        continue
                elif s.get("cron"):
                    if shared.cron_match(s["cron"], now):
                        key = ("cron", s["cron"])
                        if key not in last_checked_minute:
                            last_checked_minute[key] = now.strftime("%Y%m%d%H%M")
                            fired = True
                if fired:
                    if s.get("off_peak") and shared.is_peak_hour(now):
                        continue
                    _dispatch_schedule(s, action)
                    s["last_run"] = int(now.timestamp())
                    stores.save_schedules(SCHEDULES_PATH, items)
        except Exception:
            logger.exception("调度循环异常")
        time.sleep(30)


def _installed_plugins_hint():
    """已启用插件的触发词提示（对齐旧 main 的静态注入：让模型知道有哪些插件可提/可调）。

    v3.0 重构丢失了这段注入——用户装了插件后模型毫无感知，只能靠人肉说明。
    """
    try:
        import plugins as plugins_mod
        lines = []
        for p in plugins_mod.list_plugins(_plugin_paths()["plugins_dir"]):
            if not p.get("enabled", True):
                continue
            meta = p.get("meta") or {}
            name = str(meta.get("name") or "")
            if not name:
                continue
            trs = meta.get("triggers") or ([meta.get("trigger")] if meta.get("trigger") else [])
            desc = str(meta.get("description") or "")[:40]
            tr_text = "/".join(str(t) for t in trs[:3]) if trs else "无固定触发词"
            lines.append(f"- {name}（触发词：{tr_text}）{desc}")
        if not lines:
            return ""
        return (
            "[已安装插件] 用户可能提到这些应用；命中触发词即代表想使用对应能力，"
            "按其功能直接执行或引导使用：\n" + "\n".join(lines[:10])
        )
    except Exception:
        return ""


def _headless_chat(text, reply_channel=""):
    """无头执行一段指令（定时任务 message / run_workflow 消息投递 / IM 远程任务共用）。

    Web 架构没有旧 main 的「投递到输入框」通道，改为后台静默执行一轮对话；
    结果落会话库；有产出时发桌面通知告知（文件类产物落盘工作目录，可在文件面板查看）。
    reply_channel="telegram" 时把回复推回 Telegram（远程指令的完整闭环）。
    """
    import config_utils
    import deepseek_client as dc
    cfg = config_utils.load_config()
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        logger.warning("无头执行跳过：未配置 API Key")
        return
    client = dc.DeepSeekClient(
        key, base_url=cfg.get("base_url") or dc.DEFAULT_BASE_URL,
        model=cfg.get("model") or dc.DEFAULT_MODEL,
        timeout=float(cfg.get("timeout") or 120),
    )
    parts = []
    try:
        client.chat(
            [{"role": "user", "content": str(text)}],
            scenario=cfg.get("scenario") or "通用",
            thinking=cfg.get("thinking") or "high",
            max_tokens=int(cfg.get("max_tokens") or 16384),
            tools_enabled=bool(cfg.get("full_auto")),
            smart_tools=bool(cfg.get("full_auto")),
            on_content=lambda t: parts.append(t),
        )
    except Exception:
        logger.exception("无头执行失败：%s", str(text)[:80])
        return
    reply = "".join(parts).strip()
    logger.info("无头执行完成：%s => %s", str(text)[:40], reply[:60] or "（无文本输出）")
    if not reply:
        return
    # 会话落库（对齐旧 main 定时任务分支：任务结果可追溯）
    try:
        _save_session_data({
            "name": str(text)[:24],
            "messages": [{"role": "user", "content": str(text)}, {"role": "assistant", "content": reply}],
        })
    except Exception:
        logger.exception("无头执行结果存会话库失败")
    if reply_channel == "telegram":
        try:
            dc.im_send(reply, title="🐋 鲸语远程任务", channel="telegram")
        except Exception:
            logger.exception("Telegram 回复发送失败")
    else:
        try:
            dc.notify_desktop("鲸语后台任务完成", body=str(text)[:40] + "\n" + reply[:120])
        except Exception:
            pass


_PROCESS_WATCHDOG_LOCK = threading.Lock()
_process_watchdog_started = False


def _start_process_watchdog(interval=180, max_idle=3600):
    """空闲进程守卫生：定期清理空闲超过 max_idle 秒的后台子进程（AI 起的服务/浏览器等）。

    防止长会话里 AI 启动的进程/浏览器长期驻留成孤儿拖垮系统。
    interval 最低 30s，防过频；max_idle 可经 config 的 process_max_idle_seconds 覆盖。
    """
    global _process_watchdog_started
    if _process_watchdog_started:
        return
    _process_watchdog_started = True
    try:
        import config_utils
        mid = int(config_utils.load_config().get("process_max_idle_seconds") or 0)
        max_idle = max(300, mid) if mid else max_idle
    except Exception:
        pass

    def _loop():
        while True:
            try:
                import deepseek_client as dc
                dc.cleanup_idle_processes(max_idle_seconds=max_idle)
            except Exception:
                logger.debug("空闲进程清理失败", exc_info=True)
            time.sleep(max(30, interval))

    threading.Thread(target=_loop, daemon=True).start()


def _dispatch_schedule(s, action):
    """执行定时任务动作。"""
    import config_utils
    import deepseek_client as dc
    import backup as backup_mod
    try:
        if action == "backup":
            backup_mod.make_backup()
        elif action == "notify":
            dc.send_webhook_notify(str(s.get("text") or "定时提醒"), title="鲸语定时提醒")
        elif action == "workflow":
            name = str(s.get("text") or "")
            if name:
                dc.run_workflow(name)
        else:
            _headless_chat(str(s.get("text") or "").strip())
    except Exception:
        logger.exception("定时任务执行失败: %s", action)


def _save_session_data(body):
    from datetime import datetime
    messages = body.get("messages") or []
    clean = []
    for m in messages:
        clean.append({"role": str(m.get("role") or ""), "content": str(m.get("content") or "")[:MAX_MSG_CHARS]})
    sid = "s" + str(int(time.time() * 1000))
    path = os.path.join(SESSIONS_DIR, f"{sid}.json")
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    data = {
        "id": sid,
        "name": str(body.get("name") or "定时任务")[:80],
        "messages": clean,
        "model": str(body.get("model") or ""),
        "scenario": "通用",
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    _index_session_file(f"{sid}.json")
    return sid


def _migrate_legacy_sessions():
    """一次性迁移：旧 Tkinter 版导出的 history/session_*.jsonl 导入标准会话库。

    旧版（v3 前）会话以「导出文件」形式保存在 history/ 目录（JSONL，每行一条消息），
    Web 版会话库只读 sessions/*.json —— 若不迁移，用户历史会话在 Web 列表里永远看不到。
    按消息指纹去重（同内容不重复导入），幂等可重复调用；已迁移文件改写
    .jsonl.migrated 标记，避免重复导入。
    """
    import re as _re
    from datetime import datetime
    pat = _re.compile(r"^(session_|.*_)?(\d{8})_(\d{6})\.jsonl$")
    if not os.path.isdir(HISTORY_DIR):
        return 0
    imported = 0
    seen_fingerprints = set()
    for fn in sorted(os.listdir(HISTORY_DIR)):
        if fn.endswith(".migrated"):
            continue
        m = pat.match(fn)
        if not m or not fn.startswith("session_"):
            continue
        full = os.path.join(HISTORY_DIR, fn)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                lines = [l.strip() for l in f.read().splitlines() if l.strip()]
            if not lines or len(lines) > 2000:
                continue
            msgs = []
            for line in lines:
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                role = str(obj.get("role") or "")
                if role not in ("user", "assistant", "system", "tool"):
                    continue
                item = {"role": role, "content": str(obj.get("content") or "")[:MAX_MSG_CHARS]}
                if obj.get("reasoning_content"):
                    item["reasoning_content"] = str(obj["reasoning_content"])[:MAX_MSG_CHARS]
                if obj.get("tool_calls") and isinstance(obj.get("tool_calls"), list):
                    item["tool_calls"] = obj["tool_calls"][:16]
                if role == "tool" and obj.get("tool_call_id"):
                    item["tool_call_id"] = str(obj["tool_call_id"])[:128]
                if role == "tool" and obj.get("name"):
                    item["name"] = str(obj["name"])[:128]
                msgs.append(item)
            if len(msgs) < 2:
                continue
            # 指纹去重：首尾消息角色+内容（同内容副本不重复导入）
            fp = (msgs[0].get("role"), msgs[0].get("content", "")[:60], msgs[-1].get("content", "")[:60])
            if fp in seen_fingerprints:
                _mark_migrated(full)
                continue
            seen_fingerprints.add(fp)
            sid = hex(int(time.time() * 1000))[2:] + secrets_token(4)
            data = {
                "id": sid,
                "name": str(msgs[0].get("content") or "历史会话")[:40],
                "messages": msgs,
                "usage_total": {},
                "stars": [],
                "tags": [],
                "pinned": [],
                "top": False,
                "model": "",
                "scenario": "通用",
                "ephemeral": False,
                "saved_at": f"{m.group(2)[:4]}-{m.group(2)[4:6]}-{m.group(2)[6:]}T{m.group(3)[:2]}:{m.group(3)[2:4]}:{m.group(3)[4:]}" if fn else datetime.now().isoformat(timespec="seconds"),
            }
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            with open(os.path.join(SESSIONS_DIR, f"{sid}.json"), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            _index_session_file(f"{sid}.json")
            _mark_migrated(full)
            imported += 1
        except Exception:
            continue
    return imported


def _mark_migrated(full):
    """迁移完成标记：文件改写为 .migrated 后缀（保留原数据，便于可疑时人工恢复）。"""
    try:
        if os.path.exists(full) and not full.endswith(".migrated"):
            os.replace(full, full + ".migrated")
    except Exception:
        pass


def _workflows_get():
    """流程编排列表（workflows.json）。"""
    return {"workflows": _load_workflows()}


def _load_workflows():
    try:
        if os.path.exists(WORKFLOWS_PATH):
            with open(WORKFLOWS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def _workflows_save(body):

    data = body.get("workflows")
    if not isinstance(data, dict) or len(data) > 100:
        return None, "workflows 必须是对象（最多 100 个流程）"
    clean = {}
    for name, wf in data.items():
        steps = wf.get("steps") if isinstance(wf, dict) else None
        if not isinstance(steps, list) or len(steps) > 200:
            continue
        clean[str(name)[:60]] = {
            "steps": [str(s.get("text") if isinstance(s, dict) else s)[:2000] for s in steps],
        }
    _atomic_write_json(WORKFLOWS_PATH, clean)
    return {"ok": True, "count": len(clean)}, None


def _checkpoint_get():
    """任务检查点。"""
    try:
        if os.path.exists(CHECKPOINT_PATH):
            with open(CHECKPOINT_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _checkpoint_clear():
    try:
        if os.path.exists(CHECKPOINT_PATH):
            os.remove(CHECKPOINT_PATH)
        return {"ok": True}, None
    except Exception as e:
        return None, str(e)


def _tasklog_get():
    """任务记录（工作目录 .whaletalk/tasklog.json）。"""
    import stores
    active_dir = _status()["active_dir"]
    tl = stores.load_tasklog(os.path.join(active_dir, ".whaletalk", "tasklog.json"))
    return {"tasks": tl.get("tasks") or []}


def _knowledge_get():
    """知识库状态（读取实际索引文件）。"""
    import deepseek_client as dc
    idx_path = getattr(dc, "KNOWLEDGE_INDEX_FILE", None)
    if not idx_path or not os.path.exists(idx_path):
        return {"indexed": False, "files": []}
    try:
        with open(idx_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            docs = d.get("docs") or d.get("files") or []
            paths = [str(x.get("path") if isinstance(x, dict) else x) for x in docs][:50]
            return {"indexed": bool(docs), "files": paths, "count": len(docs)}
    except Exception:
        pass
    return {"indexed": False, "files": []}


def _knowledge_search_api(query, top_k=5):
    """结构化知识库检索（RAG）：返回带来源路径与得分的命中，供前端带引用展示。"""
    import deepseek_client as dc
    q = str(query or "").strip()
    if not q:
        return None, "query 必填"
    idx_file = getattr(dc, "KNOWLEDGE_INDEX_FILE", None)
    if not idx_file or not os.path.exists(idx_file):
        return None, "知识库尚未建立索引（先在对话用 knowledge_index 或到知识目录建索引）"
    try:
        with open(idx_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception:
        return None, "索引读取失败，请重新建立索引"
    idf = index.get("idf") or {}
    docs = index.get("docs") or []
    if not docs:
        return None, "知识库为空"
    try:
        k = max(1, min(10, int(top_k or 5)))
    except (TypeError, ValueError):
        k = 5
    qt = dc._mem_tokens(q)
    scored = []
    for d in docs:
        s = dc._mem_score(qt, idf, d.get("text") or "")
        if s > 0:
            scored.append((s, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    hits = []
    for s, d in scored[:k]:
        hits.append({
            "score": round(s, 2),
            "path": str(d.get("path") or ""),
            "snippet": dc._knowledge_snippet(d.get("text") or "", q),
            "text": str(d.get("text") or "")[:3000],
        })
    return {"query": q, "hits": hits, "total": len(docs)}, None


def _roles_save(body):
    """用户角色增删改（user_roles.json）。"""
    import stores
    items = body.get("roles")
    if not isinstance(items, list) or len(items) > 200:
        return None, "roles 必须是列表"
    clean = []
    for r in items:
        if not isinstance(r, dict) or not str(r.get("name") or "").strip():
            continue
        clean.append({
            "name": str(r["name"]).strip()[:40],
            "prompt": str(r.get("prompt") or "")[:4000],
            "thinking": str(r.get("thinking") or "high"),
            "desc": str(r.get("desc") or "")[:200],
            "category": str(r.get("category") or "我的")[:40],
        })
    stores.save_patterns(USER_ROLES_PATH, clean)
    return {"ok": True, "count": len(clean)}, None


def _profiles_get():
    import profiles as profiles_mod
    data = profiles_mod.load_profiles()
    profiles = data.get("profiles") or {}
    return {
        "profiles": [
            {"name": str(name), "model": str(p.get("model") or ""), "base_url": str(p.get("base_url") or "")}
            for name, p in profiles.items() if isinstance(p, dict)
        ],
        "current": str(data.get("current") or ""),
    }


def _reset_llm_client_cache():
    """失效 LLM 客户端缓存：会话注入的与懒构建的都清掉，下次按新配置重建。"""
    import deepseek_client as dc
    dc.set_active_client(None)
    dc._ACTIVE_FALLBACK["sig"], dc._ACTIVE_FALLBACK["client"] = None, None


# ===== 语音朗读（Web 端朗读按钮/自动模式 的合成服务端）=====

_TTS_SEM = threading.Semaphore(2)  # 合成并发上限：短句高频场景防线程风暴
_TTS_SENTENCE_MAX = 200            # 单次合成文本上限（分句后超长再切）


def _voice_cfg():
    """读取规范化 voice_config（缺失字段用默认值兜底）。"""
    import config_utils
    try:
        vc = config_utils.load_config().get("voice_config") or {}
    except Exception:
        vc = {}
    if not isinstance(vc, dict):
        vc = {}
    mode = str(vc.get("auto_mode") or "off")
    out = {
        "auto_mode": mode if mode in ("off", "sentence", "full") else "off",
        "rate": max(-10, min(10, int(vc.get("rate") or 0))) if str(vc.get("rate") or 0).lstrip("-").isdigit() else 0,
        "volume": max(0, min(100, int(vc.get("volume") or 100))),
        "voice": str(vc.get("voice") or "").strip()[:80],
    }
    return out


_MD_STRIP_PATTERNS = [
    (re.compile(r"```.*?```", re.S), " "),           # 代码块整段跳过
    (re.compile(r"`([^`]*)`"), r"\1"),               # 行内代码留内容
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),      # 图片
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),   # 链接留文字
    (re.compile(r"[#*_~>|]+"), " "),                 # 标题/强调/表格符号
    (re.compile(r"^\s*[-+*]\s+", re.M), ""),         # 列表符
    (re.compile(r"\s{2,}"), " "),
]


def _tts_clean_text(t):
    """朗读前清洗 Markdown：代码块跳过、链接只读文字、去掉强调与表格符号。"""
    s = str(t or "")
    for pat, rep in _MD_STRIP_PATTERNS:
        s = pat.sub(rep, s)
    return re.sub(r"\n{2,}", "\n", s).strip()


def _split_sentences(t, limit=_TTS_SENTENCE_MAX):
    """按中英句末标点切句，短句合并、超长硬切，供逐句流式朗读。"""
    raw = re.split(r"(?<=[。！？；!?\n])\s*", str(t or ""))
    out = []
    buf = ""
    for seg in raw:
        seg = seg.strip()
        if not seg:
            continue
        cand = f"{buf}{seg}"
        if len(cand) <= limit:
            buf = cand
            continue
        if buf:
            out.append(buf)
        while len(seg) > limit:  # 无标点超长句硬切
            cut = seg.rfind("，", 0, limit)
            cut = cut + 1 if cut > 20 else limit
            out.append(seg[:cut].strip())
            seg = seg[cut:]
        buf = seg.strip()
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if s]


def _synthesize_sapi(text, path, rate=0, volume=100, voice=""):
    """SAPI 合成 WAV 文件（独立实现：带音量/音色，不进播放注册表）。"""
    import pythoncom
    import win32com.client

    result = {"err": None}

    def _go():
        pythoncom.CoInitialize()
        stream = None
        try:
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            speaker.Rate = max(-10, min(10, int(rate)))
            speaker.Volume = max(0, min(100, int(volume)))
            import deepseek_client as _dc

            _dc._sapi_pick_voice(speaker, voice)
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(path, 3)  # SSFMCreateForWrite
            speaker.AudioOutputStream = stream
            speaker.Speak(str(text)[:4000])
        except Exception as e:
            result["err"] = e
        finally:
            if stream is not None:
                try:
                    stream.Close()
                except Exception:
                    pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    thd = threading.Thread(target=_go, daemon=True)
    thd.start()
    thd.join(timeout=max(30.0, len(text) * 0.12))
    if thd.is_alive():
        return "合成超时"
    if result["err"]:
        return str(result["err"])
    try:
        if os.path.getsize(path) < 200:
            return "合成结果为空（系统可能缺少语音包）"
    except OSError:
        return "合成输出缺失"
    return ""


def _synthesize_edge(text, path_mp3, rate=0, volume=100, voice=""):
    """edge-tts 合成 MP3（直接调用 edge_tts 库，避免 pythonw/打包 exe 子进程与超时问题）。"""
    v = str(voice or "").strip() or "zh-CN-XiaoxiaoNeural"
    try:
        import edge_tts
    except Exception as e:
        return f"edge-tts 不可用: {e}"
    try:
        comm = edge_tts.Communicate(
            text,
            v,
            rate=f"{int(rate) * 10:+d}%",
            volume=f"{max(0, min(100, int(volume) - 100)):+d}%",
        )
        # save 是协程；请求线程内无运行中的事件循环，可安全 asyncio.run。
        # 超时随文本长度放宽（短句秒回；长文不因 25s 固定阈值误判而回退 SAPI）。
        asyncio.run(asyncio.wait_for(comm.save(path_mp3), timeout=max(30, len(text) * 0.05)))
    except Exception as e:
        try:
            os.remove(path_mp3)
        except OSError:
            pass
        return f"edge-tts 调用失败: {e}"
    try:
        if os.path.getsize(path_mp3) > 500:
            return ""
    except OSError:
        pass
    return "edge-tts 无输出"


_EDGE_STATE = {"checked": False, "ok": False}


def _edge_available():
    """探测 edge-tts 是否可用（import edge_tts，结果缓存；失败则走 SAPI 兜底）。"""
    if _EDGE_STATE["checked"]:
        return _EDGE_STATE["ok"]
    try:
        import edge_tts  # noqa: F401
        _EDGE_STATE["ok"] = True
    except Exception:
        _EDGE_STATE["ok"] = False
    _EDGE_STATE["checked"] = True
    return _EDGE_STATE["ok"]


_EDGE_VOICES_ZH = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "晓晓（女·自然）"},
    {"id": "zh-CN-XiaoyiNeural", "name": "晓伊（女·活泼）"},
    {"id": "zh-CN-YunxiNeural", "name": "云希（男·阳光）"},
    {"id": "zh-CN-YunjianNeural", "name": "云健（男·浑厚）"},
]

_EDGE_VOICE_IDS = {v["id"] for v in _EDGE_VOICES_ZH}


def _tts_voices():
    """枚举可用音色：SAPI 本地音色 + （装了 edge-tts 时）中文神经网络音色。"""
    sapi = []
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            speaker = win32com.client.Dispatch("SAPI.SpVoice")
            voices = speaker.GetVoices()
            for i in range(voices.Count):
                desc = str(voices.Item(i).GetDescription())
                sapi.append({"id": desc, "name": desc})
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        pass
    return {
        "sapi": sapi,
        "edge": _EDGE_VOICES_ZH if _edge_available() else [],
        "default_engine": "edge" if (_EDGE_STATE["ok"] and sapi is not None) else "sapi",
    }


def _profiles_post(body):
    """配置方案管理：save（保存当前 Key/网关/模型为方案）/ apply（一键应用）/ delete。

    api_key 全程经 profiles 模块 DPAPI 加密落盘，接口不回传明文。
    apply 后配置即时生效（写 config.json + 失效 LLM 客户端缓存 + 热同步接线）。
    """
    import config_utils
    import profiles as profiles_mod
    action = str(body.get("action") or "").strip().lower()
    name = str(body.get("name") or "").strip()[:40]
    if action not in ("save", "apply", "delete"):
        return None, "未知 action（save/apply/delete）"
    if not name:
        return None, "缺少方案名 name"
    data = profiles_mod.load_profiles()
    profiles = data.get("profiles") or {}
    if action == "save":
        cfg = config_utils.load_config()
        key = str(cfg.get("api_key") or "").strip()
        if not key:
            return None, "当前未配置 API Key，无可保存的方案"
        profiles[name] = {
            "api_key": key,
            "base_url": str(cfg.get("base_url") or "").strip(),
            "model": str(cfg.get("model") or "").strip(),
        }
        data["current"] = name
    elif action == "apply":
        p = profiles.get(name)
        if not isinstance(p, dict):
            return None, f"方案不存在：{name}"
        cfg = config_utils.load_config()
        for k in ("api_key", "base_url", "model"):
            v = str(p.get(k) or "").strip()
            if v:
                cfg[k] = v
        config_utils.save_config(cfg)
        data["current"] = name
        _init_dc_paths()          # 接线热同步（图片键/网关派生等跟随新 cfg）
        _reset_llm_client_cache()  # 下次对话/工具调用按新方案重建客户端
        try:
            _cached_invalidate("status")
        except Exception:
            pass
    else:  # delete
        if name not in profiles:
            return None, f"方案不存在：{name}"
        profiles.pop(name)
        if data.get("current") == name:
            data["current"] = ""
    ok = profiles_mod.save_profiles({"profiles": profiles, "current": str(data.get("current") or "")})
    if not ok:
        return None, "Profile 保存失败（api_key 加密落盘未通过，已保留旧文件）"
    return {"ok": True, "current": str(data.get("current") or ""), "count": len(profiles)}, None


def _audit_get():
    """审计日志最近 200 条。"""
    log_path = os.path.join(DATA_DIR, "logs", "actions.log")
    lines = []
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = [l.strip() for l in f.read().splitlines() if l.strip()][-200:]
        except Exception:
            pass
    return {"entries": lines}


def _record_approval(entry):
    """审批/询问历史落盘（append，上限 200 条，带锁防并发写）。"""
    try:
        with _CACHE_LOCK:
            items = []
            if os.path.exists(APPROVALS_PATH):
                try:
                    with open(APPROVALS_PATH, "r", encoding="utf-8") as f:
                        items = json.load(f)
                except Exception:
                    items = []
                if not isinstance(items, list):
                    items = []
            items.append(entry)
            items = items[-200:]
            with open(APPROVALS_PATH, "w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False)
    except Exception:
        pass


def _approvals_get():
    """审批/询问历史（最近 200 条，倒序）。"""
    items = []
    if os.path.exists(APPROVALS_PATH):
        try:
            with open(APPROVALS_PATH, "r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
    if not isinstance(items, list):
        items = []
    return {"approvals": list(reversed(items[-200:]))}


def _git_run(args, cwd):
    """在项目目录跑 git（读操作用），返回 (out, code)。"""
    import subprocess as _sp
    try:
        r = _sp.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True,
            timeout=30, creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
        )
        return ((r.stdout or "") + (r.stderr or "")).strip(), r.returncode
    except Exception as e:
        return str(e), 1


def _evolve_branches():
    """self_evolve 提交的 evolve/ 分支列表（含与 main 的 diff 摘要）。"""
    if not os.path.isdir(os.path.join(_ORIG_DIR, ".git")):
        return {"branches": [], "error": "项目目录不是 git 仓库"}
    out, code = _git_run(["branch", "--list", "evolve/*", "--format=%(refname:short)"], _ORIG_DIR)
    if code != 0:
        return {"branches": [], "error": out}
    base_ref, _ = _git_run(["symbolic-ref", "--short", "HEAD"], _ORIG_DIR)
    if not base_ref or base_ref == "HEAD":
        base_ref = "main"
    branches = []
    for b in [x.strip() for x in out.splitlines() if x.strip()]:
        dout, dcode = _git_run(["diff", "--stat", base_ref + "..." + b], _ORIG_DIR)
        files_changed = 0
        insertions = deletions = 0
        if dcode == 0:
            import re as _re
            for line in dout.splitlines():
                m = _re.search(r"(\d+) files? changed", line)
                if m:
                    files_changed = int(m.group(1))
                m2 = _re.search(r"(\d+) insertions?", line)
                if m2:
                    insertions = int(m2.group(1))
                m3 = _re.search(r"(\d+) deletions?", line)
                if m3:
                    deletions = int(m3.group(1))
        # 分支最近提交时间与主题
        last_subject, _ = _git_run(["log", "-1", "--format=%s", b], _ORIG_DIR)
        last_date, _ = _git_run(["log", "-1", "--format=%ci", b], _ORIG_DIR)
        branches.append({
            "name": b,
            "subject": last_subject[:120],
            "date": (last_date or "")[:19].replace("T", " "),
            "files_changed": files_changed,
            "insertions": insertions,
            "deletions": deletions,
            "ahead": files_changed,
        })
    return {"branches": branches}


def _evolve_branch_detail(name):
    """单个 evolve 分支的详细 diff（与 main 比较）。"""
    name = str(name or "").strip()
    if not name.startswith("evolve/"):
        return None, "仅允许查看 evolve/ 前缀分支"
    if not os.path.isdir(os.path.join(_ORIG_DIR, ".git")):
        return None, "项目目录不是 git 仓库"
    base_ref, _ = _git_run(["symbolic-ref", "--short", "HEAD"], _ORIG_DIR)
    if not base_ref or base_ref == "HEAD":
        base_ref = "main"
    out, code = _git_run(["diff", base_ref + "..." + name], _ORIG_DIR)
    if code != 0:
        return None, out
    stat, _ = _git_run(["diff", "--stat", base_ref + "..." + name], _ORIG_DIR)
    return {"stat": stat, "diff": out[:20000]}, None


def _evolve_branch_merge(name):
    """合入 evolve 分支到当前分支（先看 diff 后确认的写操作）。"""
    name = str(name or "").strip()
    if not name.startswith("evolve/"):
        return None, "仅允许合入 evolve/ 前缀分支"
    if not os.path.isdir(os.path.join(_ORIG_DIR, ".git")):
        return None, "项目目录不是 git 仓库"
    cur, code = _git_run(["rev-parse", "--abbrev-ref", "HEAD"], _ORIG_DIR)
    if code != 0 or cur in ("HEAD", ""):
        return None, "无法确定当前分支"
    if cur not in ("main", "master"):
        return None, f"当前在 {cur} 分支，请先切回 main/master 再合入"
    out, mcode = _git_run(["merge", "--no-ff", "-m", f"merge self-evolve: {name}", name], _ORIG_DIR)
    if mcode != 0:
        return None, f"合并失败（可能有冲突）：\n{out}"
    return {"ok": True, "merged": name, "output": out[:500]}, None


def _evolve_branch_delete(name):
    """删除 evolve 分支（仅删除已合入或用户确认放弃的分支）。"""
    name = str(name or "").strip()
    if not name.startswith("evolve/"):
        return None, "仅允许删除 evolve/ 前缀分支"
    if not os.path.isdir(os.path.join(_ORIG_DIR, ".git")):
        return None, "项目目录不是 git 仓库"
    out, code = _git_run(["branch", "-D", name], _ORIG_DIR)
    if code != 0:
        return None, f"删除失败：\n{out}"
    return {"ok": True, "deleted": name}, None


def _backup_list():
    """备份列表。"""
    import backup as backup_mod
    out = []
    if os.path.isdir(backup_mod.BACKUP_DIR):
        for fn in sorted(os.listdir(backup_mod.BACKUP_DIR), reverse=True):
            if not fn.startswith("WhaleTalk_v") or not fn.endswith(".zip"):
                continue
            p = os.path.join(backup_mod.BACKUP_DIR, fn)
            try:
                out.append({
                    "name": fn,
                    "size": os.path.getsize(p),
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p))),
                })
            except Exception:
                continue
    return {"backups": out[:40]}


def _backup_create():
    import backup as backup_mod
    try:
        path = backup_mod.make_backup()
        backup_mod.prune(20)
        return {"ok": True, "path": path}, None
    except Exception as e:
        return None, str(e)


def _backup_delete(name):
    import backup as backup_mod
    name = str(name or "")
    if not name or "\\" in name or "/" in name or not name.startswith("WhaleTalk_v") or not name.endswith(".zip"):
        return None, "非法备份名"
    p = os.path.join(backup_mod.BACKUP_DIR, name)
    if not os.path.exists(p):
        return None, "备份不存在"
    os.remove(p)
    return {"ok": True}, None


def _update_check():
    """检查更新（GitHub Releases / 自定义 UPDATE_URL）。"""
    import config_utils
    import backup as backup_mod
    cfg = config_utils.load_config()
    url = str(cfg.get("update_url") or "").strip() or "https://api.github.com/repos/pythonshiyi/WhaleTalk/releases/latest"
    try:
        rq = urllib.request.Request(url, headers={"User-Agent": "whaletalk"})
        with urllib.request.urlopen(rq, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
        ver = str(data.get("tag_name") or data.get("version") or "")
        ver = ver.lstrip("vV")
        cur = backup_mod.current_version()
        if ver and cur and _ver_gt(ver, cur):
            return {"has_update": True, "current": cur, "latest": ver, "notes": str(data.get("body") or data.get("notes") or "")[:500]}
        return {"has_update": False, "current": cur, "latest": ver or cur}
    except Exception as e:
        return {"has_update": False, "current": backup_mod.current_version(), "error": str(e)[:120]}


def _ver_gt(a, b):
    try:
        pa = [int(x) for x in str(a).split(".")]
        pb = [int(x) for x in str(b).split(".")]
        return pa > pb
    except Exception:
        return False


def _cleanup_items(body):
    """数据清理（不可恢复）。body: {items: ["sessions","stats","logs",...]}。"""
    removed = []
    mapping = {
        "sessions": SESSIONS_DIR,
        "snapshot": os.path.join(HISTORY_DIR, "session_latest.json"),
        "stats": STATS_PATH,
        "_archives": ARCHIVES_DIR,
        "logs": os.path.join(DATA_DIR, "logs"),
        "prompts": PROMPTS_PATH,
        "permissions": os.path.join(DATA_DIR, "permissions.json"),
        "schedules": SCHEDULES_PATH,
        "memory": MEMORY_PATH,
        "failures": FAILURES_PATH,
    }
    items = body.get("items") or []
    for key in items:
        path = mapping.get(str(key))
        if not path:
            continue
        try:
            if os.path.isdir(path):
                import shutil
                shutil.rmtree(path)
            elif os.path.exists(path):
                os.remove(path)
            removed.append(str(key))
        except Exception:
            pass
    return {"ok": True, "removed": removed}


_INBOUND_SERVER = None
_INBOUND_THREAD = None


def _inbound_loop(port, expected_token):
    """Webhook 接收端：POST {token, text} → 远程下达任务（对齐原程序 inbound）。"""
    from http.server import BaseHTTPRequestHandler as _BIH

    class _InboundHandler(_BIH):
        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length <= 0 or length > 1_000_000:
                    self._ok(400, {"error": "body too large"})
                    return
                body = json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
                import hmac
                if not hmac.compare_digest(str(body.get("token") or ""), expected_token or ""):
                    self._ok(401, {"error": "unauthorized"})
                    return
                text = str(body.get("text") or "").strip()
                if not text:
                    self._ok(400, {"error": "text required"})
                    return
                # 后台执行（直接在当前线程，作为任务）
                _dispatch_schedule({"text": text, "name": "远程任务"}, "message")
                self._ok(200, {"ok": True})
            except Exception:
                self._ok(500, {"error": "internal"})

        def _ok(self, code, data):
            payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *a):
            pass

    try:
        srv = ThreadingHTTPServer(("127.0.0.1", int(port)), _InboundHandler)
        srv.serve_forever()
    except Exception:
        logger.exception("inbound 接收端退出")


def _start_inbound():
    """按 config 启动接收端线程（0=关闭）。"""
    global _INBOUND_SERVER, _INBOUND_THREAD
    import config_utils
    cfg = config_utils.load_config()
    port = int(cfg.get("inbound_port") or 0)
    token = str(cfg.get("inbound_token") or "").strip()
    if port > 0 and _INBOUND_THREAD is None:
        _INBOUND_THREAD = threading.Thread(target=_inbound_loop, args=(port, token), daemon=True)
        _INBOUND_THREAD.start()
        logger.info("Webhook 接收端已启动：127.0.0.1:%s", port)


_IM_THREAD = None

def _im_loop():
    """IM 通道：Telegram Bot 轮询（收到消息 → 执行任务并回复）。"""
    import deepseek_client as dc
    offset = None
    while True:
        try:
            im, _err = dc._load_im_config()
            token = str(im.get("telegram_bot_token") or "").strip()
            chat_id = str(im.get("telegram_chat_id") or "").strip()
            if not token:
                time.sleep(20)
                continue
            import urllib.parse
            params = [("offset", str(offset))] if offset else [("timeout", "25")]
            q = urllib.parse.urlencode(params)
            rq = urllib.request.Request(
                f"https://api.telegram.org/bot{token}/getUpdates?{q}", timeout=35
            )
            with urllib.request.urlopen(rq, timeout=35) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            for update in data.get("result") or []:
                offset = max(offset or 0, int(update.get("update_id") or 0)) + 1
                msg = update.get("message") or {}
                text = str(msg.get("text") or "").strip()
                from_chat = str(msg.get("chat") or {}).get("id") or ""
                if not text or not from_chat:
                    continue
                if chat_id and str(from_chat) != chat_id:
                    continue
                # 执行任务并回推 Telegram（后台线程，避免阻塞下一轮长轮询）
                threading.Thread(
                    target=_headless_chat, args=(text, "telegram"), daemon=True
                ).start()
        except Exception:
            logger.exception("IM 轮询异常")
            time.sleep(30)
        time.sleep(3)


def _start_im():
    global _IM_THREAD
    if _IM_THREAD is None:
        _IM_THREAD = threading.Thread(target=_im_loop, daemon=True)
        _IM_THREAD.start()


def _play_completion_sound():
    """Windows 系统提示音（回复/任务完成，浏览器在后台或已关闭时也能听到）。"""
    try:
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


def _notify_completed(ok=True):
    """回复/任务完成：桌面通知 + 提示音（受 notify_on_done / completion_sound 开关控制）。
    completion_sound 关闭时彻底静默（含 toast 失败兜底音）；后台线程执行，不阻塞请求线程。"""
    try:
        import config_utils
        cfg = config_utils.load_config()
        if cfg.get("privacy_mode"):
            return
        sound_on = bool(cfg.get("completion_sound", True))
        notify_on = bool(cfg.get("notify_on_done", True))
        if not sound_on and not notify_on:
            return
        summary = "✅ 回复完成" if ok else "⚠ 回复中断"
        if ok and sound_on:
            _play_completion_sound()
        if not notify_on:
            return
        def _toast():
            try:
                import deepseek_client as dc
                dc.notify_desktop("鲸语 WhaleTalk", summary, fallback_sound=sound_on)
            except Exception:
                pass
        threading.Thread(target=_toast, daemon=True).start()
    except Exception:
        pass


def _apply_autostart(enabled):
    """注册/卸载 HKCU Run 开机自启。
    打包版：注册 exe 自身；源码版：pythonw 无窗口起 webui/start.py。"""
    try:
        import winreg
        import sys
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}"'
        else:
            path = os.path.join(_ORIG_DIR, "webui", "start.py")
            exe = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            pyw = exe if os.path.exists(exe) else sys.executable
            cmd = f'"{pyw}" "{path}"'
        if enabled:
            winreg.SetValueEx(key, "WhaleTalk", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, "WhaleTalk")
            except OSError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        logger.exception("开机自启注册失败")
        return False


def _tool_bookkeeping(name, args, result):
    """工具记账：失败/成功模式 + 审计 + 最近产物（对齐原程序记录体系）。"""
    _audit("tool", str(name), str(args or "")[:100])
    try:
        _LAST_TOOL_CHAIN.append(str(name))
    except Exception:
        pass
    rs = str(result or "")
    fail_prefixes = ("错误", "权限拒绝", "超时", "（用户停止", "工具执行失败", "工具参数错误")
    failed = rs.startswith(fail_prefixes)
    if failed:
        _record_failure(name, rs)
    else:
        _record_recent_output(rs)
        try:
            _record_success_pattern(name, args, rs)
        except Exception:
            pass
    return None


def _plugin_detail(name):
    """插件详情：完整结构（contents 各段概览 + 评分）。"""
    import plugins as plugins_mod
    name = str(name or "")
    paths = _plugin_paths()
    target = None
    for p in plugins_mod.list_plugins(paths["plugins_dir"]):
        if (p.get("meta") or {}).get("name") == name:
            target = p
            break
    if target is None:
        sample_dir = os.path.join(_ORIG_DIR, "sample_plugins")
        if os.path.isdir(sample_dir):
            for fn in os.listdir(sample_dir):
                if not fn.endswith(plugins_mod.PLUGIN_EXT):
                    continue
                try:
                    p, err = plugins_mod.parse_plugin_file(os.path.join(sample_dir, fn))
                    if p is not None and (p.get("meta") or {}).get("name") == name:
                        target = p
                        break
                except Exception:
                    continue
    if target is None:
        return None
    meta = target.get("meta") or {}
    contents = target.get("contents") or {}
    tools = contents.get("tools") or []
    skills = contents.get("skills") or []
    workflows = contents.get("workflows") or {}
    app = contents.get("app") or {}
    files = contents.get("files") or {}
    rating = plugins_mod.plugin_rating_summary(paths["plugins_dir"], target.get("slug") or "") or {}
    return {
        "name": str(meta.get("name") or name),
        "description": str(meta.get("description") or ""),
        "author": str(meta.get("author") or ""),
        "version": str(meta.get("version") or ""),
        "trigger": str(meta.get("trigger") or (meta.get("triggers") or [""])[0]),
        "kind": "应用型" if app else ("流程" if workflows else ("技能" if skills else "工具")),
        "enabled": bool(target.get("enabled", True)),
        "requires": [str(x) for x in (target.get("requires") or [])][:20],
        "tools": [str((t.get("function") or {}).get("name") or "") for t in tools[:30]],
        "skills": [str(s.get("name") or "") for s in skills[:30]] if isinstance(skills, list) else [],
        "workflows": [str(w) for w in (workflows.keys() if isinstance(workflows, dict) else [])][:30],
        "app_entry": str((app or {}).get("entry") or "")[:120],
        "files": [str(f) for f in files.keys()][:40],
        "rating": rating,
    }


_PLUGIN_GEN_PROMPT = """你是鲸语插件架构师。根据用户需求生成一个 .wtplugin v2 插件（JSON）。

插件格式（严格 JSON，不要输出任何多余文字）：
{
  "format": "wtplugin",
  "version": 2,
  "meta": {"name": "中文插件名", "description": "简短描述", "author": "鲸语 AI", "version": "1.0.0", "trigger": "/触发词", "triggers": ["/触发词"]},
  "requires": [],
  "contents": { ... }
}

contents 五种能力（按需求选用，至少一种）：
1. tools（HTTP 工具）：[{"function": {"name": "xxx", "description": "...", "parameters": {"type":"object","properties":{...},"required":[...]}, "endpoint": "https://...", "method": "GET|POST"}}]
2. skills（提示词技能）：[{"name": "...", "text": "..."}]
3. workflows（流程）：{"流程名": {"steps": ["指令1", "指令2"]}}
4. scenario（场景）：{"name": "...", "thinking": "high", "system_prompt": "...", "enabled_tools": []}
5. app（应用型，需自带 Python 代码）：{"type": "local", "entry": "main:run"} + files: {"main.py": "def run(arg_text=''):\\n    return '...'"}

约束：
- 触发词以 / 或 @ 开头，不含空格
- 应用型必须有至少一个 .py 文件，路径不得含 ..，entry 为 module:func 或 module:class:func
- 用户要"应用"时生成 app+files（给 main.py 写可运行骨架，def run(arg_text='') -> str）
- 其他情况生成 tools/skills/workflows/scenario
- 纯 JSON 输出"""


def _studio_generate(body):
    """AI 插件工坊：需求 → LLM 生成插件 JSON → 校验。"""
    import config_utils
    import deepseek_client as dc
    import plugins as plugins_mod
    desc = str(body.get("description") or "").strip()
    if not desc:
        return None, "请描述你想做什么（需求）"
    name = str(body.get("name") or "").strip()
    ptype = str(body.get("type") or "").strip()
    cfg = config_utils.load_config()
    key = str(cfg.get("api_key") or "").strip()
    if not key:
        return None, "未配置 DeepSeek API Key"
    client = dc.DeepSeekClient(key, base_url=cfg.get("base_url") or dc.DEFAULT_BASE_URL,
                               model=cfg.get("model") or dc.DEFAULT_MODEL, timeout=120)
    user_req = f"插件名：{name or '（自动起名）'}\n类型偏好：{ptype or '（自动判断）'}\n需求：{desc}"
    parts = []
    client.chat(
        [{"role": "user", "content": _PLUGIN_GEN_PROMPT + "\n\n" + user_req}],
        scenario="通用",
        thinking="none",
        max_tokens=4096,
        tools_enabled=False,
        json_output=True,
        on_content=lambda t: parts.append(t),
    )
    text = "".join(parts).strip()
    # 提取 JSON（去除可能的围栏）
    import re as _re
    m = _re.search(r"\{.*\}", text, _re.S)
    if not m:
        return None, "AI 未生成有效 JSON：" + text[:100]
    try:
        plugin = json.loads(m.group(0))
    except Exception as e:
        return None, f"AI 生成 JSON 解析失败：{e}"
    ok, err = plugins_mod.validate_plugin(plugin)
    if not ok:
        # 尝试修复：给 AI 错误信息重试一次
        parts2 = []
        client.chat(
            [{"role": "user", "content": _PLUGIN_GEN_PROMPT + "\n\n" + user_req + "\n\n上次校验失败：" + err + "\n请修正后重新输出完整 JSON。"}],
            scenario="通用", thinking="none", max_tokens=4096, tools_enabled=False, json_output=True,
            on_content=lambda t: parts2.append(t),
        )
        text2 = "".join(parts2).strip()
        m2 = _re.search(r"\{.*\}", text2, _re.S)
        if m2:
            try:
                plugin = json.loads(m2.group(0))
                ok, err = plugins_mod.validate_plugin(plugin)
            except Exception:
                ok = False
        if not ok:
            return None, f"插件校验失败（已重试一次仍失败）：{err}"
    return {"plugin": plugin}, None


def _studio_install(body):
    """工坊安装：校验通过的插件 JSON → apply_plugin。"""
    import plugins as plugins_mod
    plugin = body.get("plugin")
    if not isinstance(plugin, dict):
        return None, "缺少插件 JSON"
    ok, err = plugins_mod.validate_plugin(plugin)
    if not ok:
        return None, f"插件校验失败：{err}"
    res = plugins_mod.apply_plugin(plugin, _plugin_paths())
    if not res.get("ok"):
        return None, str(res.get("error") or "安装失败")
    added = res.get("added") or {}
    ret = {"ok": True, "added": added, "name": (plugin.get("meta") or {}).get("name")}
    try:
        perms = plugins_mod.plugin_permissions(plugin)
        if not perms["declared"]:
            ret["warning"] = "⚠️ 该插件未声明权限（tools/files/net），调用其能力时权限按系统默认执行"
    except Exception:
        pass
    return ret, None


def _abilities():
    """按 12 域分组返回全部工具（name/description/enabled）。"""
    import config_utils
    import deepseek_client as dc
    cfg = config_utils.load_config()
    enabled = set(cfg.get("enabled_tools") or [])
    buckets = {d: [] for d in _DOMAIN_ORDER}
    buckets.setdefault("其他", [])
    for t in dc.TOOLS:
        fn = t.get("function") or {}
        name = str(fn.get("name") or "")
        domain = _TOOL_DOMAIN.get(name, "其他")
        buckets.setdefault(domain, []).append({
            "name": name,
            "description": str(fn.get("description") or ""),
            "enabled": name in enabled,
        })
    domains = []
    for d in _DOMAIN_ORDER + ["其他"]:
        tools = buckets.get(d) or []
        if not tools:
            continue
        domains.append({
            "name": d,
            "count": len(tools),
            "icon": _DOMAIN_ICONS.get(d, "🧩"),
            "color": _DOMAIN_COLORS.get(d, "#94a3b8"),
            "tools": tools,
        })
    return {"domains": domains, "total": len(dc.TOOLS)}

MAX_BODY = 1_000_000
# 图片上传专用请求体上限：base64 会把原图放大 4/3，需容纳 ≤48MB 原图直传
# （超出部分由 _upload 的自动压缩兜底）；仅 /v1/upload 使用，其余接口保持 1MB 基线。
UPLOAD_BODY_MAX = 64 * 1024 * 1024
MAX_ROUNDS = 10
MAX_MESSAGES = 200
MAX_MSG_CHARS = 100_000

# 打包（PyInstaller）时 __file__ 指向 _MEIPASS 临时解压目录，会随进程退出被清空，
# 不可作为配置/静态资源根目录；改用 exe 所在目录持久化（源码运行不受影响）。
def _runtime_dir():
    import sys
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


BASE_DIR = _runtime_dir()
# 前端静态资源 / 捆绑的只读资源（sample_plugins / evolutions / webui/start.py）：
# 源码运行在仓库；打包运行时这些被捆绑在 _MEIPASS 临时目录内（进程生命周期内有效），
# 须用原始模块路径，而不能用 exe 所在目录（那里没有这些资源）。
_ORIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
try:
    import config_utils as _cu
    _cu.DEFAULT_CONFIG_PATH = CONFIG_PATH
except Exception:
    pass
DATA_DIR = os.path.join(os.path.expanduser("~"), "Documents", "WhaleTalk")

_VOICE_CACHE_DIR = os.path.join(DATA_DIR, "voice", "cache")
HISTORY_DIR = os.path.join(DATA_DIR, "history")
SESSIONS_DIR = os.path.join(HISTORY_DIR, "sessions")
MEMORY_PATH = os.path.join(DATA_DIR, "memory.json")
STATS_PATH = os.path.join(DATA_DIR, "stats.json")
USER_ROLES_PATH = os.path.join(DATA_DIR, "user_roles.json")
PROMPTS_PATH = os.path.join(DATA_DIR, "prompts.json")
SCHEDULES_PATH = os.path.join(DATA_DIR, "schedules.json")
RECENT_PATH = os.path.join(DATA_DIR, "recent_outputs.json")
WORKSPACE_DIR = os.path.join(DATA_DIR, "workspace")
EVOLUTIONS_DIR = os.path.join(_ORIG_DIR, "evolutions")
ARCHIVES_DIR = os.path.join(DATA_DIR, "archives")
FAILURES_PATH = os.path.join(DATA_DIR, "failures.json")
APPROVALS_PATH = os.path.join(DATA_DIR, "approvals.json")  # 审批/询问历史（上限 200 条）
PATTERNS_PATH = os.path.join(DATA_DIR, "patterns.json")
WORKFLOWS_PATH = os.path.join(DATA_DIR, "workflows.json")
CHECKPOINT_PATH = os.path.join(DATA_DIR, "task_checkpoint.json")
PROFILES_PATH = os.path.join(DATA_DIR, "profiles.json")
USER_TOOLS_PATH = os.path.join(DATA_DIR, "user_tools.json")
DIST_DIR = os.path.join(_ORIG_DIR, "webui", "dist")
try:
    import profiles as _profiles_mod
    _profiles_mod.DEFAULT_PROFILES_PATH = PROFILES_PATH
    import user_tools as _user_tools_mod
    _user_tools_mod.DEFAULT_USER_TOOLS_PATH = USER_TOOLS_PATH
except Exception:
    pass

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}

# 由 start_server 注入的运行时状态（模块级，单实例）
_SERVER = None
_THREAD = None
_TOKEN = ""
_TOOLS_PROVIDER = None
_CHAT_PROVIDER = None
_PORT = 8745

# ── 会话索引缓存 ────────────────────────────────────
# 避免列表接口每次打开都逐个读会话文件内容（5000 会话实测 500ms+）。
# index.json 持元数据 + 文件指纹（mtime/size）；命中时仅 stat 校验，毫秒级返回。
SESSION_INDEX_PATH = os.path.join(DATA_DIR, "sessions_index.json")
_SESSIONS_INDEX = {}      # sid -> [file_mtime, file_size, metadata_dict]
_SESSIONS_INDEX_LOCK = threading.Lock()


def _index_session_locked(fn):
    """带锁的单会话索引更新（写路径钩子用）。"""
    with _SESSIONS_INDEX_LOCK:
        return _index_session_file(fn)


def _drop_session_index_locked(sid):
    """带锁的索引移除（删除路径钩子用）。"""
    with _SESSIONS_INDEX_LOCK:
        _drop_session_index(sid)


def _session_fingerprint(path):
    """会话文件指纹：(mtime_ns, size)。用于判断文件是否变更。"""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _session_meta(d, fn):
    """从会话 dict 提取列表元数据 + msg_count。"""
    msgs = d.get("messages") or []
    return {
        "id": str(d.get("id") or fn[:-5]),
        "name": str(d.get("name") or "未命名会话"),
        "model": str(d.get("model") or ""),
        "scenario": str(d.get("scenario") or ""),
        "saved_at": str(d.get("saved_at") or ""),
        "pinned": bool(d.get("pinned")),
        "top": bool(d.get("top")),
        "tags": [str(x) for x in (d.get("tags") or [])][:20],
        "msg_count": len(msgs),
    }


def _load_session_index():
    """从磁盘加载索引（启动时调用一次）。失败返回空（将惰性重建）。"""
    try:
        with open(SESSION_INDEX_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
            entries = raw["entries"]
            return {
                sid: [int(v[0]), int(v[1]), dict(v[2])]
                for sid, v in entries.items()
                if isinstance(v, list) and len(v) == 3 and isinstance(v[2], dict)
            }
    except Exception:
        pass
    return {}


def _save_session_index():
    """索引落盘（原子写）。"""
    global _SESSIONS_INDEX
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SESSION_INDEX_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"entries": _SESSIONS_INDEX}, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, SESSION_INDEX_PATH)
    except Exception:
        pass


def _index_session_file(fn, meta_override=None):
    """把单个会话文件编入内存索引（重复调用安全：先 read 一遍拿指纹与元数据）。"""
    global _SESSIONS_INDEX
    full = os.path.join(SESSIONS_DIR, fn)
    fp = _session_fingerprint(full)
    if fp is None:
        _SESSIONS_INDEX.pop(fn[:-5], None) if fn.endswith(".json") else None
        return None
    if fn.endswith(".json"):
        sid = fn[:-5]
        # 若指纹一致且已有内存条目 → 直接复用元数据，不读文件内容
        cur = _SESSIONS_INDEX.get(sid)
        if cur and list(cur[:2]) == list(fp) and not meta_override:
            return cur[2]
        try:
            with open(full, "r", encoding="utf-8") as f:
                d = json.load(f)
            meta = _session_meta(d, fn)
        except Exception:
            return None
        _SESSIONS_INDEX[sid] = [fp[0], fp[1], meta]
        return meta
    return None


def _drop_session_index(sid):
    """从索引移除会话（删除时）。"""
    global _SESSIONS_INDEX
    _SESSIONS_INDEX.pop(str(sid), None)


def _rebuild_session_index_locked():
    """全量重建索引核心（调用方须已持有 _SESSIONS_INDEX_LOCK）。"""
    if not os.path.isdir(SESSIONS_DIR):
        _SESSIONS_INDEX.clear()
        return 0
    fnames = {fn for fn in os.listdir(SESSIONS_DIR) if fn.endswith(".json")}
    for sid in [k for k in _SESSIONS_INDEX if f"{k}.json" not in fnames]:
        _SESSIONS_INDEX.pop(sid, None)
    for fn in fnames:
        sid = fn[:-5]
        fp = _session_fingerprint(os.path.join(SESSIONS_DIR, fn))
        cur = _SESSIONS_INDEX.get(sid)
        if cur and list(cur[:2]) == list(fp or (0, 0)):
            continue
        _index_session_file(fn)
    _save_session_index()
    return len(_SESSIONS_INDEX)


def _rebuild_session_index():
    """全量重建索引（加锁壳）。"""
    with _SESSIONS_INDEX_LOCK:
        return _rebuild_session_index_locked()


def _ensure_session_index():
    """确保索引已就绪：首次调用从磁盘加载，之后按目录变化增量重建。"""
    global _SESSIONS_INDEX, _session_dir_mtime
    if not _SESSIONS_INDEX:
        with _SESSIONS_INDEX_LOCK:
            if not _SESSIONS_INDEX:
                _SESSIONS_INDEX = _load_session_index()
                _rebuild_session_index_locked()
                _session_dir_mtime = os.stat(SESSIONS_DIR).st_mtime_ns if os.path.isdir(SESSIONS_DIR) else 0
                return
    # 轻量校验：目录 mtime 或文件数变了才增量重建（兼容外部直接改动文件）
    try:
        dir_m = os.stat(SESSIONS_DIR).st_mtime_ns if os.path.isdir(SESSIONS_DIR) else 0
        fcount = len([fn for fn in os.listdir(SESSIONS_DIR) if fn.endswith(".json")]) if os.path.isdir(SESSIONS_DIR) else 0
        if dir_m != _session_dir_mtime or fcount != len(_SESSIONS_INDEX):
            _rebuild_session_index()
            _session_dir_mtime = dir_m
    except Exception:
        pass


_session_dir_mtime = 0


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _auth(self):
        auth = self.headers.get("Authorization", "")
        expected = f"Bearer {_TOKEN}"
        try:
            import hmac
            return hmac.compare_digest(auth.strip(), expected)
        except Exception:
            return False

    def _json(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self, max_len=None):
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0 or length > (max_len or MAX_BODY):
                return None
            return json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))
        except Exception:
            return None

    # ── SSE 工具 ─────────────────────────────────
    def _sse_start(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _sse_send(self, event, data):
        try:
            payload = json.dumps({"type": event, **data}, ensure_ascii=False).encode("utf-8")
            frame = b"data: " + payload + b"\n\n"
            chunk = f"{len(frame):X}\r\n".encode("ascii") + frame + b"\r\n"
            self.wfile.write(chunk)
            self.wfile.flush()
            return True
        except Exception:
            return False

    def _sse_end(self):
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except Exception:
            pass

    def _strip_api_prefix(self, path):
        """兼容 Vite 代理：/api/v1/... 与 /v1/... 等价。"""
        if path.startswith("/api/") or path == "/api":
            return path[len("/api"):] or "/"
        return path

    def _serve_static(self, path):
        """服务 WebUI 构建产物（dist/）。SPA fallback 到 index.html。"""
        rel = self._strip_api_prefix(path).lstrip("/")
        if not rel:
            rel = "index.html"
        if rel.startswith("v1/") or rel.startswith("health"):
            self._json(404, {"error": "not found"})
            return
        full = os.path.normpath(os.path.join(DIST_DIR, rel))
        if not full.startswith(os.path.normpath(DIST_DIR)):
            self._json(404, {"error": "not found"})
            return
        if os.path.isdir(full):
            full = os.path.join(full, "index.html")
        if not os.path.isfile(full):
            full = os.path.join(DIST_DIR, "index.html")
            if not os.path.isfile(full):
                self._json(404, {"error": "webui 未构建（启动时已尝试自动构建，请检查启动日志）；或手动运行 cd webui && npm run build"})
                return
        try:
            with open(full, "rb") as f:
                data = f.read()
            ext = os.path.splitext(full)[1].lower()
            self.send_response(200)
            self.send_header("Content-Type", _MIME.get(ext, "application/octet-stream"))
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            logger.exception("静态文件服务失败: %s", full)
            self._json(500, {"error": str(e)})

    # ── 会话读取 ─────────────────────────────────
    def _safe_sid(self, sid):
        import re
        return re.sub(r"[^0-9a-zA-Z_-]", "", str(sid or ""))[:64]

    def _list_sessions(self):
        _ensure_session_index()
        metas = [v[2] for v in _SESSIONS_INDEX.values() if isinstance(v, list) and len(v) == 3]
        metas.sort(key=lambda s: s.get("saved_at") or "", reverse=True)
        return metas[:200]

    def _load_session_messages(self, sid):
        path = os.path.join(SESSIONS_DIR, f"{self._safe_sid(sid)}.json")
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            msgs = []
            for m in (d.get("messages") or [])[:2000]:
                if not isinstance(m, dict) or m.get("role") not in ("user", "assistant", "system", "tool"):
                    continue
                item = {
                    "role": str(m.get("role") or ""),
                    "content": str(m.get("content") or ""),
                    **({"reasoning_content": str(m["reasoning_content"])} if m.get("reasoning_content") else {}),
                }
                tc = m.get("tool_calls")
                if tc and isinstance(tc, list):
                    item["tool_calls"] = tc[:64]
                if m.get("role") == "tool" and m.get("tool_call_id"):
                    item["tool_call_id"] = str(m["tool_call_id"])[:128]
                msgs.append(item)
            return {
                "id": str(d.get("id") or sid),
                "name": str(d.get("name") or "未命名会话"),
                "model": str(d.get("model") or ""),
                "messages": msgs,
                "usage_total": d.get("usage_total") or {},
                "stars": [{"role": str(s.get("role") or ""), "content": str(s.get("content") or ""), "time": str(s.get("time") or "")} for s in (d.get("stars") or []) if isinstance(s, dict)][:200],
                "pinned": [str(p) for p in (d.get("pinned") or [])][:200],
                "tags": [str(x) for x in (d.get("tags") or [])][:20],
            }
        except Exception:
            return None

    def _delete_session(self, sid):
        """删除会话文件。返回 (ok, error)。"""
        sid = self._safe_sid(str(sid or ""))
        if not sid:
            return False, "缺少会话 id"
        try:
            path = os.path.join(SESSIONS_DIR, f"{sid}.json")
            if os.path.exists(path):
                os.remove(path)
            _drop_session_index_locked(sid)
            _save_session_index()
            return True, None
        except Exception as e:
            return False, str(e)

    def _delete_sessions_batch(self, sids):
        """批量删除会话。返回（ok, removed, error）。"""
        if not isinstance(sids, list) or not sids:
            return False, 0, "ids 必须是列表"
        removed = 0
        for sid in sids:
            ok, err = self._delete_session(sid)
            if ok:
                removed += 1
        return True, removed, None

    def _pin_session(self, body):
        """置顶/取消置顶会话。body: {id, pinned}。"""
        sid = self._safe_sid(str(body.get("id") or ""))
        if not sid:
            return False, "缺少会话 id"
        path = os.path.join(SESSIONS_DIR, f"{sid}.json")
        if not os.path.exists(path):
            return False, "会话不存在"
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            d["pinned"] = bool(body.get("pinned"))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
            _index_session_locked(f"{sid}.json")
            _save_session_index()
            return True, None
        except Exception as e:
            return False, str(e)

    def _patch_session(self, body, fields):
        """更新会话元数据（name/tags）。body: {id, name?/tags?}。"""
        sid = self._safe_sid(str(body.get("id") or ""))
        if not sid:
            return False, "缺少会话 id"
        path = os.path.join(SESSIONS_DIR, f"{sid}.json")
        if not os.path.exists(path):
            return False, "会话不存在"
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            if "name" in fields and "name" in body:
                d["name"] = str(body["name"] or "未命名会话")[:80]
            if "tags" in fields and "tags" in body:
                tags = body["tags"]
                if not isinstance(tags, list):
                    return False, "tags 必须是列表"
                d["tags"] = [str(x)[:20] for x in tags if str(x).strip()][:20]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, separators=(",", ":"))
            _index_session_locked(f"{sid}.json")
            _save_session_index()
            return True, None
        except Exception as e:
            return False, str(e)

    def _memory_summary(self):
        try:
            if os.path.exists(MEMORY_PATH):
                with open(MEMORY_PATH, "r", encoding="utf-8") as f:
                    d = json.load(f)
                facts = d.get("facts") or []
                return {
                    "enabled": bool(d.get("enabled")),
                    "count": len(facts),
                    "facts": [str(x.get("text") or "")[:120] for x in facts[-5:] if isinstance(x, dict)],
                }
        except Exception:
            pass
        return {"enabled": False, "count": 0, "facts": []}

    def _save_session(self, body):
        """创建/更新会话：{id?, name?, messages: [...], model?, scenario?}。
        消息自动附加 reasoning_content；原子写盘；返回会话 id。"""
        import time
        from datetime import datetime
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages or len(messages) > 2000:
            return None, "messages 必须是非空列表（最多 2000 条）"
        clean = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant", "system", "tool"):
                return None, "非法消息结构"
            item = {
                "role": str(m.get("role") or ""),
                "content": str(m.get("content") or "")[:MAX_MSG_CHARS],
            }
            rc = str(m.get("reasoning_content") or "")
            if rc:
                item["reasoning_content"] = rc[:MAX_MSG_CHARS]
            tc = m.get("tool_calls")
            if tc and isinstance(tc, list):
                item["tool_calls"] = tc[:16]
            if m.get("role") == "tool" and m.get("tool_call_id"):
                item["tool_call_id"] = str(m["tool_call_id"])[:128]
            if m.get("role") == "tool" and m.get("name"):
                item["name"] = str(m["name"])[:128]
            clean.append(item)
        sid = self._safe_sid(str(body.get("id") or ""))
        if not sid:
            sid = hex(int(time.time() * 1000))[2:] + secrets_token(4)
        path = os.path.join(SESSIONS_DIR, f"{sid}.json")
        old = {}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    old = json.load(f)
            except Exception:
                old = {}
        # append 语义（连续对话）：本轮消息追加到已有会话尾部（按首条 user 消息去重）。
        # 前端 onSend 会清空界面 msgs，闭包保存不到历史 → 由后端读旧文件合并，保证完整。
        saved_msgs = clean
        if body.get("append") and old.get("messages"):
            # 防重复：若本轮首条 user 消息已在尾部的最后 50 条中，视为重复提交，跳过追加
            old_msgs = list(old["messages"])
            dup_found = False
            if len(clean) >= 2 and clean[0].get("role") == "user":
                first_user = clean[0]
                for om in old_msgs[-50:]:
                    if om.get("role") == "user" and om.get("content") == first_user.get("content"):
                        dup_found = True
                        break
            if not dup_found:
                saved_msgs = old_msgs + clean
            else:
                saved_msgs = old_msgs
        data = {
            "id": sid,
            "name": str(body.get("name") or old.get("name") or "未命名会话")[:80],
            "messages": saved_msgs,
            "usage_total": old.get("usage_total") or {},
            "stars": body.get("stars") if isinstance(body.get("stars"), list) else (old.get("stars") or []),
            "tags": body.get("tags") if isinstance(body.get("tags"), list) else (old.get("tags") or []),
            "pinned": body.get("pinned") if isinstance(body.get("pinned"), list) else (old.get("pinned") or []),
            "top": bool(old.get("top", False)),
            "model": str(body.get("model") or old.get("model") or ""),
            "scenario": str(body.get("scenario") or old.get("scenario") or "通用"),
            "ephemeral": False,
            "saved_at": datetime.now().isoformat(timespec="seconds"),
        }
        try:
            os.makedirs(SESSIONS_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
            _index_session_locked(f"{sid}.json")
            _save_session_index()
            return sid, None
        except Exception as e:
            return None, str(e)

    # ── 路由 ─────────────────────────────────────
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.end_headers()

    def do_GET(self):
        # 本机专用：token 自取（仅监听 127.0.0.1；本机进程本可读 config.json，无额外暴露）
        if self.path == "/v1/token" or self.path == "/api/v1/token":
            self._json(200, {"token": _TOKEN})
            return
        stripped = self._strip_api_prefix(self.path)
        if stripped.startswith("/v1/") or stripped in ("/v1", "/health"):
            if not self._auth():
                self._json(401, {"error": "unauthorized"})
                return
            try:
                self.path = stripped
                if self.path == "/health":
                    self._json(200, {"ok": True, "service": "whaletalk-api", "webui": "v0.2"})
                elif self.path == "/v1/sessions":
                    self._json(200, {"sessions": self._list_sessions()})
                elif self.path == "/v1/models":
                    self._json(200, _models())
                elif self.path == "/v1/deps":
                    self._json(200, _deps())
                elif self.path == "/v1/config/reset":
                    self._json(200, _config_reset())
                elif self.path == "/v1/update/check":
                    self._json(200, _update_check())
                elif self.path == "/v1/backup":
                    self._json(200, _backup_list())
                elif self.path == "/v1/workflows":
                    self._json(200, _workflows_get())
                elif self.path == "/v1/checkpoint":
                    self._json(200, _checkpoint_get())
                elif self.path == "/v1/tasklog":
                    self._json(200, _tasklog_get())
                elif self.path == "/v1/knowledge":
                    self._json(200, _knowledge_get())
                elif self.path == "/v1/profiles":
                    self._json(200, _profiles_get())
                elif self.path == "/v1/audit":
                    self._json(200, _audit_get())
                elif self.path == "/v1/approvals":
                    self._json(200, _approvals_get())
                elif self.path == "/v1/evolve_branches":
                    self._json(200, _evolve_branches())
                elif self.path == "/v1/self_profile":
                    try:
                        import deepseek_client as dc
                        self._json(200, {"text": dc.self_profile("get")})
                    except Exception as e:
                        self._json(200, {"text": f"（自我状态读取失败：{e}）"})
                elif self.path == "/v1/failures":
                    items = []
                    if os.path.exists(FAILURES_PATH):
                        try:
                            with open(FAILURES_PATH, "r", encoding="utf-8") as f:
                                items = json.load(f)
                        except Exception:
                            items = []
                    if not isinstance(items, list):
                        items = []
                    self._json(200, {"failures": items[-100:]})
                elif self.path == "/v1/schedules":
                    self._json(200, _schedules_get())
                elif self.path == "/v1/services":
                    self._json(200, _services_get())
                elif self.path == "/v1/permissions":
                    self._json(200, _permissions_get())
                elif self.path == "/v1/prompts":
                    self._json(200, _prompts())
                elif self.path == "/v1/prompts/export":
                    self._json(200, {"prompts": _prompts_load_user(), "exported_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                elif self.path == "/v1/plugin_skills":
                    self._json(200, _plugin_skills())
                elif self.path == "/v1/dir":
                    self._json(200, _dirs())
                elif self.path == "/v1/roles":
                    self._json(200, _roles())
                elif self.path.startswith("/v1/tools/"):
                    name = self.path[len("/v1/tools/"):]
                    schema = _tool_schema(name)
                    if schema is None:
                        self._json(404, {"error": "tool not found"})
                    else:
                        self._json(200, schema)
                elif self.path == "/v1/processes":
                    self._json(200, _processes())
                elif self.path == "/v1/files" or self.path.startswith("/v1/files?"):
                    import urllib.parse
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                    if qs.get("dir"):
                        data, err = _list_dir(qs["dir"][0])
                        if err:
                            self._json(400, {"error": err})
                        else:
                            self._json(200, data)
                    else:
                        self._json(200, _files())
                elif self.path == "/v1/tasks":
                    self._json(200, _tasks())
                elif self.path == "/v1/evolutions":
                    self._json(200, _evolutions())
                elif self.path.startswith("/v1/evolutions/"):
                    name = self.path[len("/v1/evolutions/"):]
                    detail = _evolution_detail(name)
                    if detail is None:
                        self._json(404, {"error": "evolution not found"})
                    else:
                        self._json(200, detail)
                elif self.path == "/v1/status":
                    self._json(200, _status())
                elif self.path == "/v1/situation":
                    self._json(200, build_situation("full"))
                elif self.path == "/v1/mode":
                    self._json(200, {"mode": _status()["mode"]})
                elif self.path == "/v1/abilities":
                    self._json(200, _abilities())
                elif self.path == "/v1/memory":
                    self._json(200, _memory_full())
                elif self.path == "/v1/plugins":
                    self._json(200, _plugins())
                elif self.path.startswith("/v1/plugins/"):
                    import urllib.parse as _up
                    name = _up.unquote(self.path[len("/v1/plugins/"):])
                    detail = _plugin_detail(name)
                    if detail is None:
                        self._json(404, {"error": "plugin not found"})
                    else:
                        self._json(200, detail)
                elif self.path == "/v1/context":
                    import deepseek_client as dc
                    tools = sorted({t["function"]["name"] for t in dc.TOOLS})
                    try:
                        costv = _status()["monthly_cost"]
                    except Exception:
                        costv = None
                    prompt_n, completion_n, cached = _usage_month_summary()
                    self._json(200, {
                        "tools": tools,
                        "memory": self._memory_summary(),
                        # 本月真实累计 token（stats.json）+ 缓存命中率 + 本月成本
                        "usage": {"prompt": prompt_n, "completion": completion_n,
                                  "cached": cached, "cost": costv},
                    })
                elif self.path == "/v1/config":
                    import config_utils
                    import deepseek_client as dc
                    cfg = config_utils.load_config()
                    key_val = str(cfg.get("api_key") or "").strip()
                    self._json(200, {
                        "models": list(dc.MODELS.keys()),
                        "thinking_modes": list(dc.THINKING_MODES.keys()),
                        "scenarios": list(dc.SCENARIOS.keys()),
                        "model": cfg.get("model") or dc.DEFAULT_MODEL,
                        "thinking": cfg.get("thinking") or "high",
                        "scenario": cfg.get("scenario") or "通用",
                        "max_tokens": int(cfg.get("max_tokens") or 16384),
                        "tools_enabled": bool(cfg.get("tools_enabled")),
                        "base_url": cfg.get("base_url") or dc.DEFAULT_BASE_URL,
                        "has_key": bool(key_val),
                        # 脱敏提示（前3 + 尾4），绝不回传明文密钥
                        "key_hint": (f"{key_val[:3]}***{key_val[-4:]}" if len(key_val) >= 8 else ("***" if key_val else "")),
                        "system_prompt": str(cfg.get("system_prompt") or ""),
                        "temperature": float(cfg.get("custom_temperature") or 1.0),
                        "top_p": float(cfg.get("custom_top_p") or 1.0),
                        "seed": int(cfg.get("seed") or 0),
                        "json_output": bool(cfg.get("json_output")),
                        "beta_api": bool(cfg.get("beta_api")),
                        "strict_tools": bool(cfg.get("strict_tools")),
                        "stop": [str(s) for s in (cfg.get("stop") or [])][:16],
                        "logprobs": bool(cfg.get("logprobs")),
                        "tool_choice": str(cfg.get("tool_choice") or "auto"),
                        "privacy_mode": bool(cfg.get("privacy_mode")),
                        "notify_on_done": bool(cfg.get("notify_on_done")),
                        "completion_sound": bool(cfg.get("completion_sound", True)),
                        "silent_start": bool(cfg.get("silent_start", False)),
                        "project_context": bool(cfg.get("project_context")),
                        "monthly_budget": float(cfg.get("monthly_budget") or 0.0),
                        "block_on_budget": bool(cfg.get("block_on_budget")),
                        "max_context_tokens": int(cfg.get("max_context_tokens") or 400000),
                        "max_context_chars": int(cfg.get("max_context_chars") or 500000),
                        "min_kept_turns": int(cfg.get("min_kept_turns") or 8),
                        "timeout": float(cfg.get("timeout") or 120.0),
                        "max_tool_rounds": int(cfg.get("max_tool_rounds") or 100),
                                                "browser_headless": bool(cfg.get("browser_headless")),
                        "peak_warning": bool(cfg.get("peak_warning")),
                        "suggestions_enabled": bool(cfg.get("suggestions_enabled")),
                        "voice_config": _voice_cfg(),
                    })
                elif self.path.startswith("/v1/tts/audio/"):
                    fn = self.path.rsplit("/", 1)[-1]
                    base, _, ext = fn.partition(".")
                    ok_fn = (
                        ext in ("wav", "mp3") and len(base) >= 8 and len(base) <= 40
                        and all(c in "0123456789abcdef" for c in base)
                    )
                    if not ok_fn:
                        self._json(400, {"error": "invalid audio name"})
                        return
                    fp = os.path.join(_VOICE_CACHE_DIR, fn)
                    if not os.path.isfile(fp):
                        self._json(404, {"error": "audio not found"})
                        return
                    try:
                        with open(fp, "rb") as f:
                            data = f.read()
                    except OSError:
                        self._json(500, {"error": "read audio failed"})
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav" if ext == "wav" else "audio/mpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(data)
                elif self.path == "/v1/tts/voices":
                    self._json(200, _tts_voices())
                elif self.path.startswith("/v1/sessions/") and self.path.endswith("/messages"):
                    sid = self.path[len("/v1/sessions/"):-len("/messages")]
                    data = self._load_session_messages(sid)
                    if data is None:
                        self._json(404, {"error": "session not found"})
                    else:
                        self._json(200, data)
                else:
                    self._json(404, {"error": "not found"})
            except Exception as e:
                logger.exception("GET %s 失败", self.path)
                self._json(500, {"error": _friendly_error(e), "code": 500, "detail": str(e)})
        else:
            # 静态资源（WebUI dist/）
            self._serve_static(self.path)

    def do_POST(self):
        if not self._auth():
            self._json(401, {"error": "unauthorized"})
            return
        try:
            self.path = self._strip_api_prefix(self.path)
            if self.path == "/v1/chat":
                self._handle_chat()
            elif self.path == "/v1/chat/stream":
                self._handle_chat_stream()
            elif self.path.startswith("/v1/tools/") and self.path.endswith("/invoke"):
                name = self.path[len("/v1/tools/"):-len("/invoke")]
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                args = body.get("args")
                if not isinstance(args, dict):
                    self._json(400, {"error": "args 必须是 JSON 对象"})
                    return
                result = _tool_invoke(name, args)
                self._json(200, {"name": name, "result": result})
            elif self.path == "/v1/fim":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                import config_utils
                import deepseek_client as dc
                cfg = config_utils.load_config()
                key = str(cfg.get("api_key") or "").strip()
                if not key:
                    self._json(500, {"error": "未配置 DeepSeek API Key"})
                    return
                base_url = str(cfg.get("base_url") or dc.DEFAULT_BASE_URL)
                client = dc.DeepSeekClient(key, base_url=base_url, model=cfg.get("model") or dc.DEFAULT_MODEL, timeout=120)
                try:
                    result = client.fim_complete(
                        str(body.get("prompt") or ""),
                        suffix=str(body.get("suffix") or ""),
                        max_tokens=int(body.get("max_tokens") or 2048),
                    )
                    self._json(200, {"result": str(result)})
                except Exception as e:
                    self._json(500, {"error": str(e)})
            elif self.path == "/v1/cleanup":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                self._json(200, _cleanup_items(body))
            elif self.path == "/v1/backup":
                body = self._read_body()
                action = (body or {}).get("action") if body else None
                if action == "create":
                    result, err = _backup_create()
                    if err:
                        self._json(400, {"error": err})
                    else:
                        self._json(200, result)
                elif action == "delete":
                    result, err = _backup_delete((body or {}).get("name") or "")
                    if err:
                        self._json(400, {"error": err})
                    else:
                        self._json(200, result)
                elif body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                else:
                    self._json(200, {})
            elif self.path == "/v1/workflows":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _workflows_save(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/checkpoint":
                result, err = _checkpoint_clear()
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/knowledge/search":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _knowledge_search_api(str(body.get("query") or ""), int(body.get("top_k") or 5))
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/roles":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _roles_save(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/schedules":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _schedules_save(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/services":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _services_save(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/evolutions/apply":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _evolution_apply(body.get("name") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/evolutions/ignore":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _evolution_ignore(body.get("name") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/evolve_branches/detail":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _evolve_branch_detail(body.get("name") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/evolve_branches/merge":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _evolve_branch_merge(body.get("name") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/evolve_branches/delete":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _evolve_branch_delete(body.get("name") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/search":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                self._json(200, _global_search(body.get("query") or "", body.get("filters") or {}))
            elif self.path == "/v1/plugin_studio/generate":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _studio_generate(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/plugin_studio/install":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _studio_install(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/plugins":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _plugins_action(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/profiles":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _profiles_post(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/tts/synthesize":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                try:
                    text = _tts_clean_text(str(body.get("text") or ""))
                    if not text:
                        self._json(400, {"error": "清洗后无可朗读内容（代码块/纯符号）"})
                        return
                    if len(text) > 4000:
                        text = text[:4000]
                    vc = _voice_cfg()
                    rate = max(-10, min(10, int(body.get("rate") or vc["rate"])))
                    volume = max(0, min(100, int(body.get("volume") or vc["volume"])))
                    voice = str(body.get("voice") or vc["voice"])[:80]
                    engine_req = str(body.get("engine") or "").strip().lower()
                    digest = hashlib.sha1(
                        json.dumps([text, rate, volume, voice, engine_req], ensure_ascii=False).encode("utf-8")
                    ).hexdigest()
                    os.makedirs(_VOICE_CACHE_DIR, exist_ok=True)
                    # edge 优先：未强制 sapi、edge 可用、且（未选音色 或 选中的是 edge 音色）。
                    # 选中的是 SAPI 音色时不再白白用 edge 跑一次（其音色名对 edge 无效）。
                    edge_ok = _edge_available()
                    voice_sel = str(voice).strip()
                    _voice_is_edge = bool(voice_sel) and (voice_sel in _EDGE_VOICE_IDS or voice_sel.endswith("Neural"))
                    use_edge = (engine_req != "sapi") and edge_ok and (not voice_sel or _voice_is_edge)
                    if engine_req == "edge":
                        use_edge = edge_ok
                    # 候选顺序：edge 优先（请求允许时），失败或未启用回退 SAPI；sapi 强制走 SAPI
                    plan = ([("edge", f"{digest}.mp3")] if use_edge else []) + [("sapi", f"{digest}.wav")]
                    edge_err = sapi_err = ""
                    for eng, fn in plan:
                        fp = os.path.join(_VOICE_CACHE_DIR, fn)
                        if os.path.isfile(fp) and os.path.getsize(fp) > (500 if fn.endswith(".mp3") else 200):
                            self._json(200, {"ok": True, "url": f"/v1/tts/audio/{fn}", "cached": True, "engine": eng})
                            return
                        with _TTS_SEM:
                            msg = _synthesize_edge(text, fp, rate, volume, voice) if eng == "edge" \
                                else _synthesize_sapi(text, fp, rate, volume, voice)
                        if eng == "edge":
                            edge_err = msg
                        else:
                            sapi_err = msg
                        if not msg:
                            self._json(200, {"ok": True, "url": f"/v1/tts/audio/{fn}", "cached": False, "engine": eng})
                            return
                        try:
                            os.remove(fp)
                        except OSError:
                            pass
                    # 两种引擎都失败：报更有信息量的一侧（edge 是能力缺口主因）
                    detail = (edge_err or sapi_err) or "合成失败"
                    if not edge_err and "语音包" in sapi_err:
                        detail = "本机无中文离线语音包且在线音色不可用，请安装 edge-tts 或中文语音包后重试"
                    self._json(500, {"error": detail})
                except Exception as e:
                    logger.exception("TTS 合成失败")
                    self._json(500, {"error": str(e)})
            elif self.path == "/v1/mode":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                try:
                    import config_utils
                    import permissions as perms
                    mode = str(body.get("mode") or "")
                    if mode not in ("task", "dialog"):
                        self._json(400, {"error": "mode 必须是 task 或 dialog"})
                        return
                    cfg = config_utils.load_config()
                    cfg["full_auto"] = mode == "task"
                    cfg["pure_chat"] = mode == "dialog"
                    config_utils.save_config(cfg)
                    perms.set_full_auto(cfg["full_auto"])
                    _cached_invalidate("status")
                    self._json(200, {"ok": True, "mode": mode})
                except Exception as e:
                    logger.exception("POST /v1/mode 失败")
                    self._json(500, {"error": str(e)})
            elif self.path == "/v1/respond":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                ok, err = _respond(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, {"ok": True})
            elif self.path == "/v1/config":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                try:
                    import config_utils
                    cfg = config_utils.load_config()
                    for k, typ in (("model", str), ("thinking", str), ("scenario", str), ("max_tokens", int),
                           ("tools_enabled", bool), ("privacy_mode", bool), ("system_prompt", str),
                           ("temperature", float), ("top_p", float), ("seed", int), ("json_output", bool),
                           ("beta_api", bool), ("strict_tools", bool), ("base_url", str),
                           ("notify_on_done", bool), ("project_context", bool),
                           ("completion_sound", bool), ("silent_start", bool),
                           ("max_context_tokens", int), ("max_context_chars", int),
                           ("min_kept_turns", int), ("timeout", float), ("max_tool_rounds", int),
                           ("browser_headless", bool),
                           ("peak_warning", bool), ("suggestions_enabled", bool),
                           ("monthly_budget", float), ("block_on_budget", bool),
                           ("autostart", bool), ("minimize_to_tray", bool)):
                        if k in body and body[k] is not None:
                            v = body[k]
                            if typ is bool:
                                cfg[k] = bool(v)
                            elif typ is int:
                                try:
                                    cfg[k] = int(v)
                                except (TypeError, ValueError):
                                    pass
                            elif typ is float:
                                try:
                                    cfg[k] = float(v)
                                except (TypeError, ValueError):
                                    pass
                            else:
                                cfg[k] = str(v)[:500]
                    # 温度/top_p 落原程序键
                    if "temperature" in body and body["temperature"] is not None:
                        try:
                            cfg["custom_temperature"] = max(0.0, min(2.0, float(body["temperature"])))
                        except (TypeError, ValueError):
                            pass
                    if "top_p" in body and body["top_p"] is not None:
                        try:
                            cfg["custom_top_p"] = max(0.0, min(1.0, float(body["top_p"])))
                        except (TypeError, ValueError):
                            pass
                    if "stop" in body and body["stop"] is not None:
                        v = body["stop"]
                        if isinstance(v, str):
                            cfg["stop"] = [s.strip() for s in v.split(",") if s.strip()][:16]
                        elif isinstance(v, list):
                            cfg["stop"] = [str(s).strip() for s in v if str(s).strip()][:16]
                        else:
                            cfg.pop("stop", None)
                    if "logprobs" in body and body["logprobs"] is not None:
                        cfg["logprobs"] = bool(body["logprobs"])
                    if "tool_choice" in body and body["tool_choice"] is not None:
                        cfg["tool_choice"] = str(body["tool_choice"])[:32]
                    if "voice_config" in body and isinstance(body["voice_config"], dict):
                        vreq = body["voice_config"]
                        mode = str(vreq.get("auto_mode") or "off")
                        try:
                            rate_v = max(-10, min(10, int(vreq.get("rate") or 0)))
                        except (TypeError, ValueError):
                            rate_v = 0
                        try:
                            vol_v = max(0, min(100, int(vreq.get("volume") or 100)))
                        except (TypeError, ValueError):
                            vol_v = 100
                        cfg["voice_config"] = {
                            "auto_mode": mode if mode in ("off", "sentence", "full") else "off",
                            "rate": rate_v,
                            "volume": vol_v,
                            "voice": str(vreq.get("voice") or "").strip()[:80],
                        }
                    # API Key：留空/缺省=不修改；非空 strip 后交给 save_config 加密落盘
                    if "api_key" in body and body["api_key"] is not None:
                        new_key = str(body["api_key"]).strip()
                        if new_key:
                            cfg["api_key"] = new_key
                    config_utils.save_config(cfg)
                    # 开机自启注册（HKCU Run / 卸载）
                    if "autostart" in body and body["autostart"] is not None:
                        _apply_autostart(bool(body["autostart"]))
                    # 配置保存后热同步全部工具侧接线（路径/agent_mail/主题/工作目录/图片键）
                    _init_dc_paths()
                    # 密钥/网关/模型等影响 LLM 客户端的配置变更后，失效客户端缓存
                    # （get_active_client 下次调用按新配置重建；会话注入的由下次对话刷新）
                    if any(k in body for k in ("api_key", "base_url", "model")):
                        _reset_llm_client_cache()
                    if "api_key" in body and body["api_key"] is not None:
                        _cached_invalidate("status")
                    self._json(200, {"ok": True})
                except Exception as e:
                    logger.exception("POST /v1/config 失败")
                    self._json(500, {"error": str(e)})
            elif self.path == "/v1/prompts":
                # 兼容旧接口：整表覆盖用户指令（内置指令不入库，过滤掉）
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                items = body.get("prompts")
                if not isinstance(items, list):
                    self._json(400, {"error": "prompts 必须是列表"})
                    return
                _prompts_save_user([p for p in items if not p.get("builtin")] if all(isinstance(p, dict) for p in items) else items)
                self._json(200, {"ok": True})
            elif self.path in ("/v1/prompts/save", "/v1/prompts/delete", "/v1/prompts/reorder",
                               "/v1/prompts/import", "/v1/prompts/use", "/v1/prompts/restore_builtin"):
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                try:
                    if self.path == "/v1/prompts/save":
                        result, err = _prompt_upsert(body)
                    elif self.path == "/v1/prompts/delete":
                        result, err = _prompt_delete(body)
                    elif self.path == "/v1/prompts/reorder":
                        result, err = _prompts_reorder(body)
                    elif self.path == "/v1/prompts/import":
                        result, err = _prompts_import(body)
                    elif self.path == "/v1/prompts/use":
                        result, err = _prompt_use(body)
                    else:
                        result, err = _prompts_restore_builtin()
                except Exception as e:
                    logger.exception("指令库操作失败 %s", self.path)
                    result, err = None, f"操作失败：{e}"
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/sessions":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                sid, err = self._save_session(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, {"id": sid})
            elif self.path == "/v1/sessions/delete_batch":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                ok, removed, err = self._delete_sessions_batch(body.get("ids") or [])
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, {"ok": True, "removed": removed})
            elif self.path == "/v1/sessions/delete":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                ok, err = self._delete_session(body.get("id") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, {"ok": True})
            elif self.path == "/v1/sessions/pin":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                ok, err = self._pin_session(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, {"ok": True})
            elif self.path == "/v1/sessions/rename":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                ok, err = self._patch_session(body, ("name",))
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, {"ok": True})
            elif self.path == "/v1/sessions/tags":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                ok, err = self._patch_session(body, ("tags",))
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, {"ok": True})
            elif self.path == "/v1/permissions":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _permissions_set(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/dir":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _set_dir(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/files/read":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                data, err = _read_file(body.get("path") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, data)
            elif self.path == "/v1/files/preview":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                data, err = _file_preview(body.get("path") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, data)
            elif self.path == "/v1/files/open":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                data, err = open_path(body.get("path") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, data)
            elif self.path == "/v1/files/opendir":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                data, err = open_dir(body.get("path") or "")
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, data)
            elif self.path == "/v1/processes/stop":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _stop_process(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/processes/start":
                body = self._read_body()
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _start_process(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            elif self.path == "/v1/upload":
                body = self._read_body(UPLOAD_BODY_MAX)
                if body is None:
                    self._json(400, {"error": "invalid json or body too large"})
                    return
                result, err = _upload(body)
                if err:
                    self._json(400, {"error": err})
                else:
                    self._json(200, result)
            else:
                self._json(404, {"error": "not found"})
        except Exception as e:
            logger.exception("POST %s 失败", self.path)
            self._json(500, {"error": _friendly_error(e), "code": 500, "detail": str(e)})

    # ── SSE 工具 ─────────────────────────────────

    def _valid_messages(self, body):
        messages = body.get("messages") or []
        if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
            return None
        clean = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("user", "assistant", "system", "tool"):
                return None
            if not isinstance(m.get("content"), str) or len(m["content"]) > MAX_MSG_CHARS:
                return None
            item = dict(m)
            imgs = m.get("images")
            if imgs:
                if not isinstance(imgs, list) or len(imgs) > 9 or not all(isinstance(i, str) and len(i) < 500 for i in imgs):
                    return None
                if m.get("role") != "user":
                    return None
                item["images"] = [i for i in imgs if i.strip()][:9]
            clean.append(item)
        return clean

    def _client_from_cfg(self, body):
        import config_utils
        import deepseek_client as dc
        cfg = config_utils.load_config()
        key = str(cfg.get("api_key") or "").strip()
        if not key:
            raise RuntimeError("未配置 DeepSeek API Key（config.json 的 api_key）")
        model = str(body.get("model") or cfg.get("model") or dc.DEFAULT_MODEL)
        base_url = str(cfg.get("base_url") or dc.DEFAULT_BASE_URL)
        if cfg.get("beta_api") and not base_url.rstrip("/").endswith("/beta"):
            base_url = base_url.rstrip("/") + "/beta"
        if cfg.get("strict_tools") and not base_url.rstrip("/").endswith("/beta"):
            base_url = base_url.rstrip("/") + "/beta"
        timeout = float(cfg.get("timeout") or 120.0)
        client = dc.DeepSeekClient(key, base_url=base_url, model=model, timeout=timeout)
        # 注册为会话级客户端：视觉/子代理/语音/团队等工具经 get_active_client 直接复用，
        # 与本请求的网关/模型保持一致（每次对话刷新，工具不再报「没有可用客户端」）
        dc.set_active_client(client)
        return client, cfg

    def _budget_block(self, cfg):
        """预算检查：block_on_budget 开启且本月成本>=预算时，返回错误消息（否则 None）。"""
        if not bool(cfg.get("block_on_budget")):
            return None
        try:
            limit = float(cfg.get("monthly_budget") or 0.0)
            if limit <= 0:
                return None
            cost = _monthly_cost()
            if cost >= limit:
                return "本月预算已用完（¥%.2f/¥%.2f）：请到「设置 → 通知与安全」调高预算或关闭预算阻止。" % (cost, limit)
        except Exception:
            pass
        return None

    def _chat_kwargs(self, body, cfg):
        import deepseek_client as dc
        thinking = str(body.get("thinking") or cfg.get("thinking") or "high")
        if thinking not in dc.THINKING_MODES:
            thinking = "high"
        mode = str(body.get("mode") or "")
        if mode == "dialog":
            pure = True
            tools = False
        elif mode == "task":
            pure = False
            tools = True
        else:
            pure = bool(cfg.get("pure_chat"))
            tools = bool(body.get("tools_enabled", cfg.get("tools_enabled", False)))
        return {
            "scenario": str(body.get("scenario") or cfg.get("scenario") or "通用"),
            "thinking": thinking,
            "max_tokens": int(body.get("max_tokens") or cfg.get("max_tokens") or 16384),
            "tools_enabled": tools,
            "pure_chat": pure,
            "smart_tools": bool(tools and not pure),
            "stop": cfg.get("stop") or None,
            "logprobs": bool(cfg.get("logprobs")),
            "tool_choice": None if (pure or not tools) else (cfg.get("tool_choice") or "auto"),
            "continue_prefix": bool(body.get("continue_prefix")),
            # 采样参数：显式透传用户配置（无思考档时生效）；seed 恒传（0=随机）
            "temperature": float(cfg.get("custom_temperature") or 1.0),
            "top_p": float(cfg.get("custom_top_p") or 1.0),
            "seed": int(cfg.get("seed") or 0) or None,
            # 输出与工具模式：无条件透传（client.chat 内部判断生效场景）
            "json_output": bool(cfg.get("json_output")),
            "strict_tools": bool(cfg.get("strict_tools")),
            # 工具轮数上限：透传用户配置（client.chat 默认 100）
            "max_tool_rounds": int(cfg.get("max_tool_rounds") or 100),
        }

    def _inject_system_messages(self, messages, cfg, pure_chat):
        import config_defaults
        import stores as stores_mod
        if any(isinstance(m, dict) and m.get("role") == "system" for m in messages):
            return messages, None
        if pure_chat:
            prompt = config_defaults.DIALOG_SYSTEM_PROMPT
            parts = []
        else:
            prompt = str(cfg.get("system_prompt") or config_defaults.DEFAULT_SYSTEM_PROMPT)
            parts = [config_defaults.TASK_QUALITY_GUIDE]
        try:
            mem = _memory_full()
            # 记忆开关：config.memory_enabled 关闭时完全不注入（省 token + 稳定前缀缓存）
            if cfg.get("memory_enabled", True):
                facts = [f["text"] for f in mem.get("facts", []) if f.get("text")]
                if facts:
                    parts.append("[长期记忆]\n" + "\n".join("- " + t for t in facts[-6:]))
        except Exception:
            pass
        # 核心自我状态注入（跨会话连续自我；有实质内容才注入，空则不占 token）
        try:
            import deepseek_client as dc
            sp = dc.self_profile("get")
            if sp and sp.strip() and "核心自我状态]" in sp and "为空" not in sp:
                parts.append(sp)
        except Exception:
            pass
        active_dir = str(cfg.get("active_dir") or "").strip()
        if active_dir and os.path.isdir(active_dir):
            parts.append(
                "[当前工作目录] " + active_dir + "\n新任务请在该目录下创建独立子目录（按任务名命名），产物写入其中。"
            )
        if not pure_chat:
            try:
                fpm = stores_mod.failure_patterns_text(FAILURES_PATH)
                if fpm:
                    parts.append(fpm)
            except Exception:
                pass
            try:
                pats = stores_mod.load_patterns(PATTERNS_PATH)
                if pats:
                    p_lines = ["[已验证工具链] 以下调用曾成功（同类任务优先复用）："]
                    for p in pats[-3:]:
                        if isinstance(p, dict) and (p.get("tool") or p.get("recipe")):
                            p_lines.append("- " + str(p.get("tool") or p.get("recipe")))
                    if len(p_lines) > 1:
                        parts.append("\n".join(p_lines))
            except Exception:
                pass
            try:
                hint = _installed_plugins_hint()
                if hint:
                    parts.append(hint)
            except Exception:
                pass
            try:
                tasklog_path = os.path.join(active_dir or WORKSPACE_DIR, ".whaletalk", "tasklog.json")
                tl = stores_mod.load_tasklog(tasklog_path)
                tasks = tl.get("tasks") or []
                if tasks:
                    tl_lines = ["[项目任务记录] 跨会话交接参考："]
                    for t in tasks[-3:]:
                        if isinstance(t, dict) and t.get("title"):
                            tl_lines.append("- " + str(t["title"])[:60])
                    if len(tl_lines) > 1:
                        parts.append("\n".join(tl_lines))
            except Exception:
                pass
        sys_msg = {"role": "system", "content": prompt}
        memory_text = "\n\n".join(parts) if parts else None
        return [sys_msg] + [dict(m) for m in messages], memory_text

    def _handle_chat(self):
        body = self._read_body()
        if body is None:
            self._json(400, {"error": "invalid json or body too large"})
            return
        messages = self._valid_messages(body)
        if messages is None:
            self._json(400, {"error": "invalid messages"})
            return
        _sync_full_auto()
        try:
            client, cfg = self._client_from_cfg(body)
            kb = self._budget_block(cfg)
            if kb:
                self._json(400, {"error": kb})
                return
            kwargs = self._chat_kwargs(body, cfg)
            messages, memory_text = self._inject_system_messages(messages, cfg, kwargs.get("pure_chat", False))
            if memory_text:
                kwargs["memory_text"] = memory_text
            try:
                # 插件工具/用户自定义工具（对齐旧 main.py：custom_tools=load_user_tools(...)）
                import user_tools as _ut
                kwargs["custom_tools"] = _ut.load_user_tools(USER_TOOLS_PATH)
            except Exception:
                logger.exception("加载自定义工具失败（不影响基础工具）")
            messages, comp_info = _compress_messages(messages, cfg, client)
            out = []
            kwargs.update({
                "on_content": (lambda t: out.append(("c", t))),
                "on_reasoning": (lambda t: out.append(("r", t))),
                "on_tool": (lambda n, a, r: out.append(("t", n, a, r))),
                "on_usage": (lambda u: out.append(("u", u))),
            })
            client.chat(messages, **kwargs)
            text = "".join(p[1] for p in out if p[0] == "c")
            usage = next((p[1] for p in out if p[0] == "u"), None)
            self._json(200, {"content": text or "", "usage": usage})
        except Exception as e:
            logger.exception("API chat 失败")
            self._json(500, {"error": _friendly_error(e)})

    def _handle_chat_stream(self):
        body = self._read_body()
        if body is None:
            self._json(400, {"error": "invalid json or body too large"})
            return
        messages = self._valid_messages(body)
        if messages is None:
            self._json(400, {"error": "invalid messages"})
            return
        sid = str(body.get("session_id") or "").strip()  # 已有会话继续对话时由前端携带，用于完成后自动落盘
        _sync_full_auto()
        self._sse_start()
        stop_event = threading.Event()

        def send(event, data):
            if not self._sse_send(event, data):
                stop_event.set()
                return False
            return True

        try:
            client, cfg = self._client_from_cfg(body)
            kb = self._budget_block(cfg)
            if kb:
                send("error", {"message": kb})
                self._sse_end()
                return
            kwargs = self._chat_kwargs(body, cfg)
            messages, memory_text = self._inject_system_messages(messages, cfg, kwargs.get("pure_chat", False))
            if memory_text:
                kwargs["memory_text"] = memory_text
            try:
                # 插件工具/用户自定义工具（对齐旧 main.py：custom_tools=load_user_tools(...)）
                import user_tools as _ut
                kwargs["custom_tools"] = _ut.load_user_tools(USER_TOOLS_PATH)
            except Exception:
                logger.exception("加载自定义工具失败（不影响基础工具）")
            messages, comp_info = _compress_messages(messages, cfg, client)
            if comp_info:
                send("compressed", comp_info)
            kwargs.update({
                "on_reasoning": lambda t: send("reasoning", {"text": t}),
                "on_content": lambda t: send("content", {"text": t}),
                "on_tool_start": lambda n, a: send("tool_start", {"name": n, "args": a}),
                "on_tool": lambda n, a, r: (send("tool", {"name": n, "args": a, "result": r}), _tool_bookkeeping(n, a, r)),
                "on_tool_duration": lambda n, d: send("tool_duration", {"name": n, "duration": d}),
                "on_usage": lambda u: (send("usage", u), _record_usage(u, cfg, body)),
                "on_approval": _make_approval_cb(send, stop_event),
                "on_ask": _make_ask_cb(send, stop_event),
                "on_request_permission": _make_permission_cb(send, stop_event),
                "stop_event": stop_event,
            })
            client.chat(messages, **kwargs)
            # 后端自动落盘（架构兜底）：chat() 返回后 messages 已含本轮完整历史
            # （assistant 回复 + tool 结果，content 已还原纯文本）。前端正常时
            # onFinished 也会保存（双保险）；前端卸载/断连/刷新时本处兜底，
            # 正在生成的会话结果不丢失。过滤 system + 清洗悬空 tool（防止坏状态落盘）。
            if sid:
                try:
                    import deepseek_client as _dc
                    clean = _dc.DeepSeekClient._sanitize_messages(
                        [m for m in messages if m.get("role") != "system"]
                    )
                    self._save_session({
                        "id": sid,
                        "name": str(body.get("session_name") or "")[:80],
                        "messages": clean,
                    })
                except Exception:
                    logger.exception("后端自动落盘失败（不影响本次会话）")
            # 任务记录：工具链写入工作目录 tasklog（对齐原程序 _record_tasklog）
            try:
                chain = [t.get("name") for t in _LAST_TOOL_CHAIN[:20]]
                user_msgs = [m for m in messages if m.get("role") == "user"]
                if chain and user_msgs:
                    _record_tasklog(str(user_msgs[-1].get("content") or "")[:40], chain)
            except Exception:
                pass
            _LAST_TOOL_CHAIN.clear()
            _notify_completed(ok=not stop_event.is_set())
            send("done", {})
        except Exception as e:
            logger.exception("API chat/stream 失败")
            send("error", {"message": _friendly_error(e)})
        self._sse_end()
def start_server(port=8745, token="", tools_provider=None, chat_provider=None):
    """启动本地 API 服务。token 为空时自动生成。返回 (port, token, error)。"""
    global _SERVER, _THREAD, _TOKEN, _TOOLS_PROVIDER, _CHAT_PROVIDER, _PORT, _SCHEDULER_THREAD
    if _SERVER is not None:
        return _PORT, _TOKEN, None
    import secrets
    _init_dc_paths()
    try:
        n = _migrate_legacy_sessions()
        if n:
            logger.info("已迁移 %s 个旧版会话到会话库", n)
    except Exception:
        logger.exception("旧会话迁移失败（不影响启动）")
    if _SCHEDULER_THREAD is None:
        _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, daemon=True)
        _SCHEDULER_THREAD.start()
    _start_process_watchdog()
    try:
        _start_inbound()
    except Exception:
        logger.exception("Webhook 接收端启动失败")
    try:
        _start_im()
    except Exception:
        logger.exception("IM 通道启动失败")
    try:
        import permissions as perms
        perms.init(
            os.path.join(DATA_DIR, "permissions.json"),
            WORKSPACE_DIR,
            audit_dir=os.path.join(DATA_DIR, "logs"),
        )
        perms.set_full_auto(bool(_cu_load("full_auto")))
    except Exception:
        logger.exception("权限模块初始化失败")
    # run_workflow 的消息投递通道：Web 版无「投递输入框」，走无头后台执行
    def _send_to_headless(text):
        try:
            threading.Thread(target=_headless_chat, args=(str(text),), daemon=True).start()
            return True
        except Exception:
            return False
    try:
        import deepseek_client as _dcw
        _dcw.set_send_callback(_send_to_headless)
    except Exception:
        logger.exception("workflow 发送通道接线失败")
    token = (token or "").strip() or ("wt_" + secrets.token_hex(16))
    _TOKEN = token
    _TOOLS_PROVIDER = tools_provider
    _CHAT_PROVIDER = chat_provider
    _PORT = int(port or 8745)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", _PORT), _Handler)
    except Exception as e:
        return None, "", str(e)
    _SERVER = server
    _THREAD = threading.Thread(target=server.serve_forever, daemon=True)
    _THREAD.start()
    logger.info("本地 API 服务已启动：http://127.0.0.1:%s", _PORT)
    return _PORT, _TOKEN, None


def stop_server():
    global _SERVER, _THREAD
    if _SERVER is not None:
        try:
            # 终止全部后台子进程（AI 起的服务/浏览器等），防孤儿进程残留
            try:
                import deepseek_client as dc
                dc.cleanup_all_processes()
            except Exception:
                logger.debug("服务停止时进程清理失败", exc_info=True)
        except Exception:
            pass
        try:
            _SERVER.shutdown()
            _SERVER.server_close()
        except Exception:
            pass
        _SERVER = None
        _THREAD = None
        return True
    return False


def is_running():
    return _SERVER is not None


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    import secrets
    try:
        import config_utils
        cfg = config_utils.load_config()
        tok = str(cfg.get("inbound_token") or "").strip()
        if not tok:
            tok = "wt_" + secrets.token_hex(16)
            cfg["inbound_token"] = tok
            config_utils.save_config(cfg)
    except Exception:
        tok = ""
    port, token, err = start_server(port=8745, token=tok)
    if err:
        print(f"启动失败: {err}")
        raise SystemExit(1)
    try:
        os.makedirs(os.path.join(DATA_DIR, "uploads"), exist_ok=True)
        with open(os.path.join(DATA_DIR, ".api_token"), "w", encoding="utf-8") as f:
            f.write(token)
    except Exception:
        pass
    print(f"WhaleTalk API 已启动: http://127.0.0.1:{port}")
    print(f"Token: {token}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        stop_server()
