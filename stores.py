"""本地 JSON 数据存取（最近产物/模式/失败/任务日志/记忆/调度）。

从 main.py 中拆出，统一使用 persistence.atomic_json_write 保证原子写。
"""
import functools
import hashlib
import json
import logging
import os
import re
import threading
import time

from persistence import atomic_json_write

logger = logging.getLogger("whaletalk.stores")

# 读—改—写复合操作串行化：原子写只保证文件不写坏，不防「两个并发写各自基于旧快照
# 覆盖对方的更新」（丢更新）。这里用可重入锁把所有变更操作串起来；纯读路径不加锁
# （原子写保证读者只会看到完整旧/新文件）。_append_archive 等在锁内调用，RLock 可重入。
_STORE_LOCK = threading.RLock()


def _locked(fn):
    @functools.wraps(fn)
    def _w(*a, **k):
        with _STORE_LOCK:
            return fn(*a, **k)
    return _w


def load_recent(path):
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(x) for x in data if str(x).strip()]
    except Exception:
        logger.exception("读取最近产物失败")
    return []


def save_recent(path, recent):
    return atomic_json_write(path, recent)


def load_favs(path):
    """读取文件收藏（路径列表）。"""
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return [str(x) for x in data if str(x).strip()]
    except Exception:
        logger.exception("读取文件收藏失败")
    return []


def save_favs(path, favs):
    return atomic_json_write(path, favs)


@_locked
def toggle_fav(path, favs_path, max_favs=100):
    """收藏/取消收藏一个路径，返回 (是否已收藏, 新收藏列表)。"""
    favs = load_favs(favs_path)
    p = str(path or "").strip()
    if not p:
        return False, favs
    try:
        p = os.path.abspath(p)
    except Exception:
        pass
    if p in favs:
        favs = [x for x in favs if x != p]
    else:
        favs.append(p)
        if len(favs) > max_favs:
            favs = favs[-max_favs:]
    save_favs(favs_path, favs)
    return (p in favs), favs



def load_patterns(path):
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        logger.exception("读取成功模式失败")
    return []


def save_patterns(path, pats):
    return atomic_json_write(path, pats)


def load_failures(path):
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        logger.exception("读取失败模式失败")
    return []


def save_failures(path, items):
    return atomic_json_write(path, items, indent=1)


def append_failures(path, new_items, max_failures=None, archive_path=None, now=None):
    """追加失败记录（兼容旧调用路径，语义 = record_failures）。"""
    return record_failures(path, new_items, max_failures=max_failures,
                           archive_path=archive_path, now=now)


# ── 失败模式生命周期（记录 → 复现计数 → 修复验证 → 归档）──────────────────
# 失败记忆必须能"过期"。历史案例：某次 list_dir 因 evolutions 目录不存在而失败，
# 该条被永久写入失败模式并持续注入上下文；目录补建后它仍在注入——AI 被自己的
# 过期记忆反复误导。故失败条目需要指纹（同类归并）、复现计数、消解状态与归档。

FAILURES_MAX = 50            # 活跃条目上限（超出优先归档已消解项）
FAILURES_ARCHIVE_MAX = 200   # 归档上限
FAILURE_ERR_CAP = 120        # 错误文本保留长度
FAILURE_FP_LEN = 16          # 指纹长度（SHA-1 前 16 位）
FAILURE_OK_STREAK = 2        # 连续成功次数达标才自动消解（防偶发成功掩盖真故障）

# 归一化规则：抹掉随环境/时间漂移的噪声，保留语义。
# 注意顺序——先去路径/时间，再折叠空白；数字只归一 "line N"，
# 其余数字（HTTP 404 / 500、端口）必须保留，否则不同错误会被错误归并。
_FP_RULES = (
    (re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?"), "<ts>"),
    (re.compile(r"[A-Za-z]:\\[^\s\"'<>|]*"), "<path>"),           # Windows 绝对路径
    (re.compile(r"(?<![:/\w])/(?:[\w.\-]+/)+[\w.\-]*"), "<path>"),  # POSIX 路径（排除 URL）
    (re.compile(r"\b(?:line|行)\s*\d+", re.I), "line <n>"),
    (re.compile(r"\b[0-9a-f]{16,}\b", re.I), "<id>"),
    (re.compile(r"0x[0-9a-f]+", re.I), "<hex>"),
    (re.compile(r"[\"'`][^\"'`\r\n]{0,120}[\"'`]"), "<q>"),
    (re.compile(r"\s+"), " "),
)


def _now(now=None):
    return now or time.strftime("%Y-%m-%d %H:%M:%S")


def failure_fingerprint(tool, error):
    """失败指纹：归一化后的 (工具 + 错误文本) 的 SHA-1 前 16 位。

    同类错误换了路径/行号/临时目录后仍归一到同一指纹——复现计数才有意义。
    """
    text = f"{str(tool or '?').strip().lower()}|{str(error or '')}"
    for rx, repl in _FP_RULES:
        text = rx.sub(repl, text)
    text = text.strip().lower()
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:FAILURE_FP_LEN]


def normalize_failure(item):
    """补全条目字段（兼容旧格式：仅 tool/error/ts 的历史数据）。"""
    if not isinstance(item, dict):
        return None
    tool = str(item.get("tool") or "?")
    err = str(item.get("error") or "")[:FAILURE_ERR_CAP]
    ts = str(item.get("last_ts") or item.get("ts") or "")
    diag = item.get("diagnosis")
    out = {
        "fingerprint": str(item.get("fingerprint") or failure_fingerprint(tool, err)),
        "tool": tool,
        "error": err,
        "hits": int(item.get("hits") or 1),
        "ok_streak": int(item.get("ok_streak") or 0),
        "first_ts": str(item.get("first_ts") or ts),
        "last_ts": ts,
        "resolved": bool(item.get("resolved")),
        "resolved_ts": item.get("resolved_ts"),
        "resolved_by": item.get("resolved_by"),
        "note": item.get("note"),
        # 观测层（可选）：错误分类 + 参数误用；旧数据无此字段时按需现算一次
        "diagnosis": diag if isinstance(diag, dict) else diagnose_failure(err),
    }
    return out


# ── 失败诊断（观测层）──────────────────────────────────────────────────
# 目标：让失败记录从「失败了什么」升级为「模型错在哪、怎么错的」——
# 供持续观测（哪些工具高频失败、哪类错、常见误传），让后续可靠性优化有数据支撑。
# 设计：**只加信息，不改指纹与生命周期语义**；分类纯文本判定、无副作用、不抛异常。
_DIAG_PATTERNS = (
    # (分类, 正则, 说明)
    ("arg_mismatch", re.compile(r"unexpected keyword argument|missing \d+ required positional|"
                                r"工具参数|received unexpected|got an unexpected"), "参数名/必填不匹配"),
    ("exit_nonzero", re.compile(r"以退出码\s*\d+|退出码\s*\d+|exit(?:ed)? (?:code|status)|command exited"), "命令非零退出"),
    ("timeout", re.compile(r"超时|timed? ?out|TimeoutExpired"), "执行超时"),
    ("memory", re.compile(r"内存超限|memory|MemoryError"), "内存超限"),
    ("not_found", re.compile(r"不是内部或外部命令|not found|No such file|找不到|does not exist|"
                             r"不存在|未被识别"), "命令/路径/依赖不存在"),
    ("network", re.compile(r"ConnectError|Connection|HTTP|SSLError|超时连接|拒绝连接|SSL"), "网络/接口错误"),
    ("permission", re.compile(r"权限拒绝|Permission denied|Access is denied|denied"), "权限拒绝"),
    ("syntax", re.compile(r"SyntaxError|IndentationError|invalid syntax|语法"), "语法错误"),
    ("runtime", re.compile(r"Traceback|Exception|Error:"), "运行期异常"),
)
_DIAG_ARG_RE = re.compile(r"unexpected keyword argument '([^']+)'")
_DIAG_MISSING_RE = re.compile(r"missing \d+ required positional argument[s]?: (.+)")


def diagnose_failure(error, extra=None):
    """把失败文本归类为结构化诊断（观测用）。**永不抛出**。

    返回 {kind, label, misused_args, summary}；extra 可带执行层采集的信息
    （如参数对齐时被丢弃/被映射的键），有则并入。
    """
    err = str(error or "")
    kind, label = "unknown", "未分类"
    for k, rx, lab in _DIAG_PATTERNS:
        if rx.search(err):
            kind, label = k, lab
            break
    misused = sorted(set(_DIAG_ARG_RE.findall(err)))
    if not misused:
        m = _DIAG_MISSING_RE.search(err)
        if m:
            misused = [m.group(1).strip()[:80]]
    out = {
        "kind": kind,
        "label": label,
        "misused_args": misused[:8],
        "summary": err.splitlines()[0][:120] if err else "",
    }
    if isinstance(extra, dict):
        for key in ("ignored_args", "aliased_args", "received_args"):
            v = extra.get(key)
            if v:
                out[key] = list(v)[:8] if isinstance(v, (list, tuple, set)) else v
    return out


def failure_diagnosis_summary(path):
    """跨条目汇总诊断（供 /v1 接口观测）：按工具、按错误类型、常见误传。返回 dict。"""
    from collections import Counter
    try:
        items = [x for x in (normalize_failure(i) for i in load_failures(path)) if x]
    except Exception:
        items = []
    by_tool = Counter()
    by_kind = Counter()
    misused = Counter()
    for it in items:
        d = it.get("diagnosis") or {}
        w = int(it.get("hits") or 1)
        by_tool[str(it.get("tool") or "?")] += w
        by_kind[str(d.get("kind") or "unknown")] += w
        for a in d.get("misused_args") or []:
            misused[str(a)] += w
    return {
        "total_failures": sum(by_tool.values()),
        "unresolved": len([it for it in items if not it.get("resolved")]),
        "by_tool": by_tool.most_common(20),
        "by_kind": by_kind.most_common(20),
        "misused_args": misused.most_common(20),
    }



@_locked
def record_failures(path, new_items, max_failures=None, archive_path=None, now=None):
    """记录工具失败：按指纹归并 + 复现计数 + 溢出归档。返回活跃列表。

    复现即视为"未修复"——已消解的条目再次出现会被重新打开（清空消解状态）。
    """
    max_failures = FAILURES_MAX if max_failures is None else int(max_failures)
    if not new_items:
        return load_failures(path)
    now = _now(now)
    try:
        items = [x for x in (normalize_failure(i) for i in load_failures(path)) if x]
        index = {it["fingerprint"]: it for it in items}
        for raw in new_items:
            if not isinstance(raw, dict):
                continue
            tool = str(raw.get("tool") or "?")
            err = str(raw.get("error") or "")
            fp = failure_fingerprint(tool, err)
            old = index.get(fp)
            if old is not None:
                old["hits"] = int(old.get("hits") or 1) + 1
                old["last_ts"] = now
                if err:
                    old["error"] = err[:FAILURE_ERR_CAP]
                old["resolved"] = False
                old["resolved_ts"] = None
                old["resolved_by"] = None
                old["ok_streak"] = 0  # 复发清零：连续成功计数重新起算
                # 观测层：刷新诊断（分类可能因错误文本变化而变），并入本次采集的参数误用
                old["diagnosis"] = diagnose_failure(err, raw.get("diagnosis"))
            else:
                entry = {
                    "fingerprint": fp,
                    "tool": tool,
                    "error": err[:FAILURE_ERR_CAP],
                    "hits": 1,
                    "ok_streak": 0,
                    "first_ts": now,
                    "last_ts": now,
                    "resolved": False,
                    "resolved_ts": None,
                    "resolved_by": None,
                    "note": None,
                    "diagnosis": diagnose_failure(err, raw.get("diagnosis")),
                }
                items.append(entry)
                index[fp] = entry
        keep, overflow = _split_overflow(items, max_failures)
        save_failures(path, keep)
        if overflow and archive_path:
            _append_archive(archive_path, overflow)
        return keep
    except Exception:
        logger.exception("记录失败模式失败")
        return load_failures(path)


def _split_overflow(items, max_failures):
    """超上限时优先归档「已消解 + 最旧」的条目；未消解项尽量保留。"""
    if len(items) <= max_failures:
        return items, []
    unresolved = [it for it in items if not it.get("resolved")]
    resolved = sorted((it for it in items if it.get("resolved")),
                      key=lambda x: str(x.get("last_ts") or ""))
    room = max_failures - len(unresolved)
    if room >= 0:
        overflow = resolved[: len(resolved) - room] if room else list(resolved)
        keep = unresolved + (resolved[len(resolved) - room:] if room else [])
        return keep, overflow
    # 连未消解项都超上限：保留最近出现的，最旧的移出
    unresolved.sort(key=lambda x: str(x.get("last_ts") or ""))
    overflow = unresolved[: len(unresolved) - max_failures] + resolved
    return unresolved[len(unresolved) - max_failures:], overflow


def _append_archive(archive_path, items):
    """归档溢出条目（滚动保留 FAILURES_ARCHIVE_MAX 条）。"""
    try:
        arch = []
        if os.path.exists(archive_path):
            with open(archive_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                arch = data
        arch.extend(items)
        if len(arch) > FAILURES_ARCHIVE_MAX:
            del arch[: len(arch) - FAILURES_ARCHIVE_MAX]
        atomic_json_write(archive_path, arch, indent=1)
    except Exception:
        logger.exception("归档失败模式失败")


@_locked
def resolve_failures(path, fingerprint=None, tool=None, note="", by="user",
                     archive_path=None, max_failures=None, now=None):
    """标记失败已解决（修复验证通过）。返回 (消解条数, 活跃列表)。

    两种定位方式：fingerprint 精确命中；tool 命中该工具的**全部未消解**记录
    （用于"这个工具已经能用了"的整体消解）。消解后按上限自然归档。
    """
    now = _now(now)
    fp = str(fingerprint or "").strip()
    tl = str(tool or "").strip()
    if not fp and not tl:
        return 0, load_failures(path)
    try:
        items = [x for x in (normalize_failure(i) for i in load_failures(path)) if x]
        hit = 0
        for it in items:
            if it.get("resolved"):
                continue
            if (fp and it.get("fingerprint") == fp) or (tl and it.get("tool") == tl):
                it["resolved"] = True
                it["resolved_ts"] = now
                it["resolved_by"] = str(by or "user")
                it["note"] = str(note or "")[:200] or it.get("note")
                hit += 1
        if hit:
            keep, overflow = _split_overflow(items, FAILURES_MAX if max_failures is None else int(max_failures))
            save_failures(path, keep)
            if overflow and archive_path:
                _append_archive(archive_path, overflow)
            return hit, keep
        return 0, items
    except Exception:
        logger.exception("消解失败模式失败")
        return 0, load_failures(path)


@_locked
def reopen_failures(path, fingerprint=None, tool=None, now=None):
    """撤销消解（判定"其实没修好"）。返回 (重开条数, 活跃列表)。"""
    fp = str(fingerprint or "").strip()
    tl = str(tool or "").strip()
    if not fp and not tl:
        return 0, load_failures(path)
    try:
        items = [x for x in (normalize_failure(i) for i in load_failures(path)) if x]
        hit = 0
        for it in items:
            if not it.get("resolved"):
                continue
            if (fp and it.get("fingerprint") == fp) or (tl and it.get("tool") == tl):
                it["resolved"] = False
                it["resolved_ts"] = None
                it["resolved_by"] = None
                hit += 1
        if hit:
            save_failures(path, items)
        return hit, items
    except Exception:
        logger.exception("重开失败模式失败")
        return 0, load_failures(path)


@_locked
def forget_failures(path, fingerprint=None, tool=None, now=None):
    """彻底移除失败记录（无论是否已消解）。返回 (移除条数, 活跃列表)。"""
    fp = str(fingerprint or "").strip()
    tl = str(tool or "").strip()
    try:
        items = [x for x in (normalize_failure(i) for i in load_failures(path)) if x]
        if not fp and not tl:
            return 0, items
        keep = [
            it for it in items
            if not ((fp and it.get("fingerprint") == fp) or (tl and it.get("tool") == tl))
        ]
        removed = len(items) - len(keep)
        if removed:
            save_failures(path, keep)
        return removed, keep
    except Exception:
        logger.exception("移除失败模式失败")
        return 0, load_failures(path)


@_locked
def auto_resolve_on_success(path, tool, archive_path=None, now=None, streak=None):
    """修复验证（自动）：某工具随后**连续**成功调用 → 其未消解的失败视为已修复。

    这是"失败记忆能过期"的关键一环：AI 不必记得回收，成功本身即为证据。
    默认要求连续 FAILURE_OK_STREAK 次成功（防一次偶发成功掩盖真故障）；
    期间任一次失败会把计数清零（见 record_failures）。
    返回本次真正消解的条数。
    """
    tl = str(tool or "").strip()
    if not tl:
        return 0
    need = FAILURE_OK_STREAK if streak is None else max(1, int(streak))
    now = _now(now)
    try:
        items = [x for x in (normalize_failure(i) for i in load_failures(path)) if x]
        changed = 0
        resolved = 0
        for it in items:
            if it.get("resolved") or it.get("tool") != tl:
                continue
            it["ok_streak"] = int(it.get("ok_streak") or 0) + 1
            changed += 1
            if it["ok_streak"] >= need:
                it["resolved"] = True
                it["resolved_ts"] = now
                it["resolved_by"] = "auto:success"
                it["note"] = f"工具连续 {it['ok_streak']} 次调用成功（自动消解）"
                resolved += 1
        if changed:
            keep, overflow = _split_overflow(items, FAILURES_MAX)
            save_failures(path, keep)
            if overflow and archive_path:
                _append_archive(archive_path, overflow)
        return resolved
    except Exception:
        logger.exception("自动消解失败模式失败")
        return 0


def failure_stats(path):
    """活跃失败统计（供 /v1/failures 与工具输出）。"""
    items = [x for x in (normalize_failure(i) for i in load_failures(path)) if x]
    unresolved = [it for it in items if not it.get("resolved")]
    return {
        "total": len(items),
        "unresolved": len(unresolved),
        "resolved": len(items) - len(unresolved),
        "recurring": len([it for it in unresolved if int(it.get("hits") or 1) > 1]),
    }


def failure_dashboard(path, limit=10):
    """失败模式看板（③d）：聚合复现次数 / 工具分布 / 消解率，供前端可视化。

    只读现有 failures.json，不改动记录（failure_stats 保持原契约不变）。
    """
    items = [x for x in (normalize_failure(i) for i in load_failures(path)) if x]
    unresolved = [it for it in items if not it.get("resolved")]
    resolved = [it for it in items if it.get("resolved")]
    total = len(items)
    by_tool = {}
    for it in unresolved:
        t = str(it.get("tool") or "?")
        by_tool[t] = by_tool.get(t, 0) + 1
    top = sorted(unresolved, key=lambda x: (-int(x.get("hits") or 1), str(x.get("last_ts") or "")))
    return {
        "total": total,
        "unresolved": len(unresolved),
        "resolved": len(resolved),
        "resolution_rate": round(len(resolved) / total, 3) if total else 0.0,
        "auto_resolved": sum(1 for it in resolved if str(it.get("resolved_by") or "").startswith("auto")),
        "recurring": len([it for it in unresolved if int(it.get("hits") or 1) > 1]),
        "unresolved_hits": sum(int(it.get("hits") or 1) for it in unresolved),
        "by_tool": sorted(({"tool": t, "count": c} for t, c in by_tool.items()),
                          key=lambda x: (-x["count"], x["tool"]))[:limit],
        "top": [{"tool": it.get("tool"), "error": str(it.get("error") or "")[:160],
                 "hits": int(it.get("hits") or 1), "last_ts": it.get("last_ts") or "",
                 "fingerprint": it.get("fingerprint") or ""} for it in top[:limit]],
    }


def failure_patterns_text(path, limit=3):
    """已知失败模式注入：**只注入未消解项**（按最近出现排序），并带复现次数。

    已消解的失败不再注入——这正是"过期记忆持续误导"的修复点。
    """
    try:
        items = [x for x in (normalize_failure(i) for i in load_failures(path)) if x]
        live = [it for it in items if not it.get("resolved")]
        if not live:
            return ""
        live.sort(key=lambda x: str(x.get("last_ts") or ""))
        lines = ["[已知失败模式] 以下工具调用曾失败且尚未确认修复（遇到同类情况请规避或改用其他方式）："]
        for it in live[-limit:]:
            tool = str(it.get("tool") or "?")
            err = str(it.get("error") or "")[:100]
            hits = int(it.get("hits") or 1)
            last = str(it.get("last_ts") or "")[5:16]
            tail = f"（复现 {hits} 次，最近 {last}）" if hits > 1 else (f"（最近 {last}）" if last else "")
            if err:
                lines.append(f"- {tool}{tail}：{err}")
        return "\n".join(lines) if len(lines) > 1 else ""
    except Exception:
        logger.exception("构建失败模式文本失败")
        return ""


def load_tasklog(path):
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("tasks", [])
                return data
    except Exception:
        logger.exception("读取项目任务记录失败")
    return {"tasks": []}


def save_tasklog(path, data):
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        return atomic_json_write(path, data, indent=2)
    except Exception:
        logger.exception("保存项目任务记录失败")
        return False


def load_memory(path):
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                data.setdefault("enabled", False)
                data.setdefault("facts", [])
                return data
    except Exception:
        logger.exception("读取长期记忆失败")
    return {"enabled": False, "facts": []}


def save_memory(path, data):
    return atomic_json_write(path, data, indent=2)


def load_schedules(path):
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        logger.exception("读取定时任务失败")
    return []


def save_schedules(path, schedules):
    return atomic_json_write(path, schedules, indent=2)
