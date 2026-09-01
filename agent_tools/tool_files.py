# -*- coding: utf-8 -*-
"""tool_files —— P0-1 批量拆分（工具域模块）：📁 文件与进程.

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
引用 deepseek_client 的常量与辅助依赖加载顺序契约——主文件在共享基建全部定义后
才执行 `from agent_tools import *`，此处 from-import 可安全解析。
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from collections import deque

import permissions
import snapshot as snapshot_mod

from shared import clamp_int  # D4: 参数校验辅助
from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import deepseek_client as _dc  # 可变注入配置动态访问（dc.X 注入后立即生效）

# ---- L5: search_local 进程内增量索引（避免每次调用全量重扫重读） ----
# 结构：{normcase(root): {relpath: {"mtime": float, "size": int, "lines": [...], "trunc": bool}}}
# 只读"变化"文件（mtime/size 比对），消失文件剔除；不持久化（进程重启后首次调用重建，
# 代价与冷启动一致，符合"增量缓存"语义）。
_SEARCH_IDX_LOCK = threading.Lock()
_SEARCH_INDEX = {}
_SEARCH_REFRESH_BUDGET = 200     # 单次调用最多增量读取的文件数（防超大目录卡调用）
_SEARCH_CACHE_BYTES = 64 * 1024  # 单文件行缓存字节上限（超出标记 trunc，查询时实时补扫全文）
_SEARCH_CACHE_LINES = 400        # 单文件行缓存行数上限
_SEARCH_MAX_FILES = 3000         # 单 root 索引条目上限（防内存膨胀）
_SEARCH_SKIP_BIG = 512 * 1024    # 超过该字节的文件不索引（与原实现一致）
from deepseek_client import (

    EDIT_FILE_MAX_SIZE,
    EDIT_FILE_REGEX_MAX,
    EXTRACT_MAX_ENTRIES,
    EXTRACT_MAX_SINGLE_BYTES,
    EXTRACT_MAX_TOTAL_BYTES,
    MAX_PROCESSES,
    PROCESSES,
    READ_FILE_MAX_BYTES,
    _ARCHIVE_SKIP_DIRS,
    _COMMON_PACKAGES,
    _PROCESSES_LOCK,
    _READ_LINE_MAX,
    _SEARCH_EXTS,
    _SEARCH_SKIP_DIRS,
    _atomic_write,
    _emit_process,
    _kill_tree,
    _process_reader,
    _recycle_path,
    _search_local_result,
    _win_clipboard_get,
    _win_clipboard_set,
    snapshot_processes,
)



def _detect_text_encoding(path):
    """文本编码探测：BOM 优先 -> UTF-8 严格校验 -> GB18030（中文 Windows 最常见）
    -> BIG5 -> latin-1 兜底（单字节永不失败）。返回 (encoding, is_fallback)，
    is_fallback=True 表示非 UTF-8，调用方可在结果中提示编码。"""
    try:
        with open(path, "rb") as f:
            raw = f.read(8192)
    except Exception:
        return "utf-8", False
    if not raw:
        return "utf-8", False
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig", False
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16", False
    if raw.startswith((b"\x00\x00\xfe\xff", b"\xff\xfe\x00\x00")):
        return "utf-32", False
    try:
        raw.decode("utf-8")
        return "utf-8", False
    except UnicodeDecodeError:
        pass
    # GB18030 是 GBK 超集，覆盖中文 Windows 绝大多数文本；BIG5 覆盖繁体
    for enc in ("gb18030", "big5"):
        try:
            raw.decode(enc)
            return enc, True
        except UnicodeDecodeError:
            continue
    return "latin-1", True


@tool(
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "读取本地文本文件（UTF-8，默认前 100KB，须在允许目录内）；可指定 start_line/max_lines 按行读取超大文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件绝对路径（须在允许目录内）"},
                        "start_line": {"type": "integer", "description": "可选：起始行号（从 1 开始，与 max_lines 配合按行读取）"},
                        "max_lines": {"type": "integer", "description": "可选：读取行数上限（默认 200，最大 2000）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='读取文件内容',
    preactivate=(('文件', '读取', '读一下', '打开'), ('代码', '编程', 'python', 'bug', '脚本', '函数')),
)
def read_file(path, start_line=None, max_lines=None):
    if not path or len(str(path)) > 512:
        return "错误：路径为空或过长"
    # 与 list_dir / write_file 等一致：所有路径操作先经权限模型判定
    # （默认仅允许工作区；读取工作区外文件请在「权限设置 → allowed_dirs」加入目录）
    ok, reason = permissions.check_filesystem(path, write=False)
    if not ok:
        return reason
    # L7: 编码探测（BOM/UTF-8/GB18030/BIG5/latin-1），替代固定 utf-8 造成的中文乱码
    enc, is_fallback = _detect_text_encoding(path)
    enc_note = f"\n[编码：{enc}]" if is_fallback else ""
    try:
        if start_line is not None or max_lines is not None:
            # 按行读取（适合超大文件）：start_line 从 1 开始，max_lines 默认 200
            try:
                start = max(1, int(start_line or 1))
                count = clamp_int(max_lines, 200, lo=1, hi=2000)
            except (TypeError, ValueError):
                return "错误：start_line / max_lines 必须是正整数"
            if start > 1_000_000:
                return "错误：start_line 过大（超过 100 万行，请缩小范围）"
            with open(path, "r", encoding=enc, errors="replace") as f:
                for _ in range(start - 1):
                    f.readline()
                # readline(上限)：单行可达数百 MB（minified JSON/日志），
                # 不设上限会一次撑爆内存；超长行截断到 100KB 并标注
                lines = []
                for _ in range(count):
                    ln = f.readline(_READ_LINE_MAX)
                    if ln == "":
                        break
                    if len(ln) >= _READ_LINE_MAX and not ln.endswith("\n"):
                        ln = ln.rstrip("\n") + "…[超长行已截断]\n"
                    lines.append(ln)
            lines = [ln for ln in lines if ln != ""]
            if not lines:
                return f"（第 {start} 行起无内容）"
            body = "".join(lines)
            if not body.endswith("\n"):
                body += "\n"
            prefix = f"[按行读取 {path} 第 {start}-{start + len(lines) - 1} 行]\n"
            return prefix + body + enc_note
        with open(path, "r", encoding=enc, errors="replace") as f:
            content = f.read(READ_FILE_MAX_BYTES)
        if len(content) >= READ_FILE_MAX_BYTES:
            content += "\n[文件较大，已截断前 100KB]"
        return content + enc_note
    except Exception as e:
        return f"错误：无法读取文件 {path}: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "写文件（自动建目录，目录白名单校验 + 大小限制 + 原子写 + 自动 .bak）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "目标文件绝对路径（须在允许目录内）"},
                        "content": {"type": "string", "description": "文件完整内容"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='写入文件',
    preactivate=(('写', '保存', '创建', '生成'), ('修改', '编辑', '改动', '改一下', '改一次', '改成', '改为', '改下', '改改', '改掉', '更新', '替换', '重写', '覆盖', '重命名', '改名', '删掉', '删除')),
)
def write_file(path, content):
    """写文件：目录白名单 + 大小限制（按 UTF-8 字节计）+ 原子写 + 自动 .bak + 写入后真实核验。"""
    ok, reason = permissions.check_filesystem(path, write=True)
    if not ok:
        return reason
    if content is None:
        return "错误：内容为空"
    # 中文按字符数会超限 3 倍：统一按 UTF-8 字节数校验
    content_bytes = len(str(content).encode("utf-8", "ignore"))
    if content_bytes > permissions.max_write_size():
        return (
            f"错误：内容 {content_bytes} 字节超过大小限制 "
            f"{permissions.max_write_size()}"
        )
    p = permissions.resolve(path)
    try:
        # 覆盖已存在文件前自动快照（删除可恢复的安全网；新建无需快照）
        if os.path.exists(p):
            snapshot_mod.snapshot_before("write_file", p)
        created, real_size = _atomic_write(p, str(content))
        if not os.path.exists(p):
            return f"错误：写入后核验失败，文件不存在：{p}"
        permissions.audit("write_file", p, f"{real_size} 字节")
        return (
            f"已写入 {p}（{'新建' if created else '覆盖并备份 .bak'}，"
            f"实际 {real_size} 字节，已核验存在）"
        )
    except Exception as e:
        return f"错误：写入失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "edit_file",
                "description": "编辑文件：按文本替换或正则替换（自动备份 .bak），支持 replacements 一次批量替换多处",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件绝对路径"},
                        "old": {"type": "string", "description": "要替换的原文（与 regex 二选一，至少提供一个）"},
                        "new": {"type": "string", "description": "替换后的新文本"},
                        "regex": {"type": "string", "description": "可选：正则表达式模式（Python re 语法）"},
                        "replacements": {"type": "string", "description": "可选：批量替换列表（JSON 数组，如 [{\"old\":\"旧文本\",\"new\":\"新文本\"},{\"regex\":\"正则\",\"new\":\"替换\"}]，按顺序逐项替换；与 old/regex 互斥）"},
                    },
                    "required": ["path", "new"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='编辑文件（局部修改）',
    preactivate=(('修改', '编辑', '改动', '改一下', '改一次', '改成', '改为', '改下', '改改', '改掉', '更新', '替换', '重写', '覆盖', '重命名', '改名', '删掉', '删除'),),
)
def edit_file(path, old="", new="", regex=None, replacements=None):
    """编辑文件：按文本/正则替换（自动备份 .bak）；replacements 支持一次批量替换多处。"""
    ok, reason = permissions.check_filesystem(path, write=True)
    if not ok:
        return reason
    p = permissions.resolve(path)
    if not os.path.isfile(p):
        return f"错误：文件不存在：{p}"
    if replacements and (old or regex):
        return "错误：replacements 与 old/regex 不能同时使用"
    try:
        # 读入上限：允许目录内也可能有 GB 级文件，全量读入内存会 OOM
        if os.path.getsize(p) > EDIT_FILE_MAX_SIZE:
            return f"错误：文件超过 {EDIT_FILE_MAX_SIZE // 1024 // 1024}MB 上限，请改用其他方式处理"
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception as e:
        return f"错误：读取失败: {e}"
    n = 0
    # L6: 批量替换（JSON 数组，按顺序逐项应用；单项不匹配跳过不视为失败）
    if replacements:
        try:
            reps = json.loads(str(replacements))
        except Exception as e:
            return f"错误：replacements 不是合法 JSON: {e}"
        if not isinstance(reps, list) or not reps:
            return "错误：replacements 需为非空 JSON 数组"
        for i, rep in enumerate(reps):
            if not isinstance(rep, dict):
                return f"错误：replacements[{i}] 需为对象（含 old 或 regex + new）"
            r_old = str(rep.get("old") or "")
            r_regex = str(rep.get("regex") or "")
            r_new = str(rep.get("new") or "")
            if not r_old and not r_regex:
                return f"错误：replacements[{i}] 缺少 old/regex"
            if r_regex:
                if len(r_regex) > EDIT_FILE_REGEX_MAX:
                    return f"错误：replacements[{i}] 正则过长（>{EDIT_FILE_REGEX_MAX} 字符）"
                try:
                    # lambda 返回 new 原样：re.sub 字符串替换会把 \1 /\g<1>/\\ 解释为分组引用，
                    # 模型生成的替换文本含反斜杠时会被静默改写
                    content, k = re.subn(r_regex, lambda m: r_new, content)
                except re.error as e:
                    return f"错误：replacements[{i}] 正则无效: {e}"
                n += k
            else:
                if r_old not in content:
                    continue  # 该项无匹配：跳过，其余项继续
                k = content.count(r_old)
                content = content.replace(r_old, r_new)
                n += k
    elif regex:
        if len(str(regex)) > EDIT_FILE_REGEX_MAX:
            return f"错误：正则过长（>{EDIT_FILE_REGEX_MAX} 字符）"
        try:
            # lambda 返回 new 原样：re.sub 的字符串替换会把 new 中的 \1 / \g<1> /
            # \\ 解释为分组引用与转义，模型生成的替换文本含反斜杠时会被静默改写
            content, n = re.subn(regex, lambda m: new or "", content)
        except re.error as e:
            return f"错误：正则无效: {e}"
    else:
        if not old:
            return "错误：需要提供 old（原文）或 regex（正则）"
        if old not in content:
            return "错误：目标文本未找到"
        n = content.count(old)
        content = content.replace(old, new or "")
    if n == 0:
        return "错误：无匹配内容，未做修改"
    try:
        snapshot_mod.snapshot_before("edit_file", p)
        _atomic_write(p, content)
        if not os.path.exists(p):
            return f"错误：写入后核验失败，文件不存在：{p}"
        permissions.audit("edit_file", p, f"替换 {n} 处")
        return f"已替换 {n} 处，写入 {p}（已备份 .bak，已核验存在）"
    except Exception as e:
        return f"错误：写入失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "list_dir",
                "description": "列出目录内容与文件大小（只读，默认允许，目录须在允许目录内）",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string", "description": "目录绝对路径"}},
                    "required": ["path"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='列出目录',
    preactivate=(('文件', '读取', '读一下', '打开'), ('搜索文件', '检索', '找文件')),
)
def list_dir(path):
    """列目录（只读）。"""
    ok, reason = permissions.check_filesystem(path, write=False)
    if not ok:
        return reason
    p = permissions.resolve(path)
    if not os.path.isdir(p):
        return f"错误：目录不存在：{p}"
    # scandir 惰性迭代 + islice：百万条目目录不再全量 listdir（1-2s + 数百 MB 内存）
    import itertools

    try:
        entries = list(itertools.islice(os.scandir(p), 200))
        entries.sort(key=lambda e: e.name)  # 保持确定性排序（只排前 200 条）
    except Exception as e:
        return f"错误：{e}"
    lines = []
    for e in entries:
        try:
            is_dir = e.is_dir()
            kind = "DIR " if is_dir else "FILE"
            size = ""
            if not is_dir:
                try:
                    size = f" {e.stat().st_size}B"
                except OSError:
                    pass
            lines.append(f"{kind} {e.name}{size}")
        except OSError:
            continue
    # 统计总数需要完整遍历，代价大：仅当目录小才统计（大目录直接显示前 200）
    total = "200+" if len(entries) >= 200 else str(len(entries))
    tail = f"\n[共 {total} 项，仅显示前 200]" if len(entries) >= 200 else f"\n共 {total} 项"
    return "\n".join(lines) + tail


def _search_index_file(full):
    """读取文件前 N 字节/行进缓存（增量索引的"变化文件重建"单元）。超大文件返回 None 不索引。"""
    try:
        size = os.path.getsize(full)
        if size > _SEARCH_SKIP_BIG:
            return None
        lines = []
        total = 0
        truncated = False
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                total += len(ln.encode("utf-8", errors="replace"))
                if total > _SEARCH_CACHE_BYTES or len(lines) >= _SEARCH_CACHE_LINES:
                    truncated = True
                    break
                lines.append(ln)
        return {
            "mtime": os.path.getmtime(full),
            "size": size,
            "lines": lines,
            "trunc": truncated,
        }
    except Exception:
        return None


def _search_refresh_root(root_walk, idx):
    """增量刷新：walk 比对 mtime/size，预算内读入新/变化文件；剔除消失文件。

    返回 (seen, refreshed, pending)：seen=本次看到的文件数（含未变化跳过），
    refreshed=实际重建数，pending=需要索引但超出预算/索引上限未处理数。"""
    seen = set()
    refreshed = 0
    pending = 0
    try:
        for cur_root, dirs, files in os.walk(root_walk):
            dirs[:] = [d for d in dirs if d not in _SEARCH_SKIP_DIRS]
            for fn in files:
                if not fn.lower().endswith(_SEARCH_EXTS):
                    continue
                full = os.path.join(cur_root, fn)
                rel = os.path.relpath(full, root_walk).replace("\\", "/")
                seen.add(rel)
                meta = idx.get(rel)
                try:
                    mtime = os.path.getmtime(full)
                    size = os.path.getsize(full)
                except OSError:
                    continue
                if meta and meta.get("mtime") == mtime and meta.get("size") == size:
                    continue  # 未变化：命中缓存，零 IO
                if len(idx) >= _SEARCH_MAX_FILES and rel not in idx:
                    pending += 1
                    continue  # 索引已满，不新增
                if refreshed >= _SEARCH_REFRESH_BUDGET:
                    pending += 1
                    continue  # 预算耗尽：本次不读，下次调用续建
                new_meta = _search_index_file(full)
                if new_meta is None:
                    continue
                idx[rel] = new_meta
                refreshed += 1
    except Exception:
        pass
    for rel in list(idx):
        if rel not in seen:
            del idx[rel]  # 文件已消失，剔除索引
    return len(seen), refreshed, pending


def _search_match_root(idx, root_walk, q, limit, hits):
    """在增量索引上匹配关键词。trunc 大文件缓存不完整，实时补扫全文（每文件至多 1 条命中）。"""
    for rel, meta in idx.items():
        lines = meta.get("lines") or []
        if not lines:
            continue
        if meta.get("trunc"):
            full = os.path.join(root_walk, *rel.split("/"))
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    for ln in f:
                        if q in ln.lower():
                            hits.append(f"{rel}: {ln.strip()[:150]}")
                            break
            except Exception:
                continue
        else:
            for ln in lines:
                if q in ln.lower():
                    hits.append(f"{rel}: {ln.strip()[:150]}")
                    if len(hits) >= limit:
                        return
            if len(hits) >= limit:
                return


@tool(
        {
            "type": "function",
            "function": {
                "name": "search_local",
                "description": "在允许目录内全文检索文本文件内容（只读，支持常见文本格式，可限量返回）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要检索的目录绝对路径"},
                        "query": {"type": "string", "description": "检索关键词"},
                        "max_results": {"type": "integer", "description": "最多返回条数，默认 20"},
                    },
                    "required": ["path", "query"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='在允许目录内全文检索文件',
    preactivate=(('文件', '读取', '读一下', '打开'), ('搜索文件', '检索', '找文件')),
)
def search_local(path, query, max_results=20):
    """在允许目录内检索文本文件内容（只读，进程内增量索引加速重复检索）。"""
    ok, reason = permissions.check_filesystem(path, write=False)
    if not ok:
        return reason
    p = permissions.resolve(path)
    if not os.path.isdir(p):
        return f"错误：目录不存在：{p}"
    try:
        limit = clamp_int(max_results, 20, lo=1, hi=200)
    except (TypeError, ValueError):
        limit = 20
    q = str(query or "").lower()
    if not q:
        return "错误：查询关键词为空"
    root_key = os.path.normcase(os.path.normpath(p))
    with _SEARCH_IDX_LOCK:
        idx = _SEARCH_INDEX.setdefault(root_key, {})
        seen, refreshed, pending = _search_refresh_root(p, idx)
        hits = []
        _search_match_root(idx, p, q, limit, hits)
    result = _search_local_result(hits, seen, limit, q)
    if pending:
        result += f"\n[索引增量构建中：本次重建 {refreshed} 个文件，另有 {pending} 个新文件待后续检索]"
    return result


@tool(
        {
            "type": "function",
            "function": {
                "name": "clipboard_get",
                "description": "读取用户剪贴板文本（隐私操作：读取用户复制的内容），适合用户复制内容后直接处理",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    groups=['📁 文件与目录'],
    phrases='读取剪贴板',
    preactivate=(('剪贴板', '复制到剪贴板', '粘贴出来', '读剪贴板'),),
)
def clipboard_get():
    """读取用户剪贴板文本（敏感操作，走审批闸门默认需确认）。"""
    text = _win_clipboard_get()
    if text is None:
        return "错误：无法访问剪贴板"
    if not text.strip():
        return "剪贴板为空"
    return f"[剪贴板内容（{len(text)} 字符）]\n{text}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "clipboard_set",
                "description": "把整理好的内容写入系统剪贴板，用户可直接粘贴到任意应用使用（只写不读）",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string", "description": "要写入剪贴板的内容"}},
                    "required": ["text"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='写入剪贴板',
    preactivate=(('剪贴板', '复制到剪贴板', '粘贴出来', '读剪贴板'),),
)
def clipboard_set(text):
    """把内容写入剪贴板（用户可直接粘贴使用）。"""
    if not str(text or "").strip():
        return "错误：text 必填"
    t = str(text)
    if len(t) > 500000:
        t = t[:500000]
    if _win_clipboard_set(t):
        return f"已写入剪贴板（{len(t)} 字符）"
    return "错误：剪贴板写入失败"


@tool(
        {
            "type": "function",
            "function": {
                "name": "delete_file",
                "description": "删除文件或目录（默认移入回收站可恢复；permanent=true 才物理删除）。高危：删除前自动快照可恢复",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "要删除的文件/目录绝对路径（须在允许目录内）"},
                        "permanent": {"type": "boolean", "description": "可选：true 物理删除不可恢复（默认 false 移入回收站）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='删除文件/目录',
    preactivate=(('修改', '编辑', '改动', '改一下', '改一次', '改成', '改为', '改下', '改改', '改掉', '更新', '替换', '重写', '覆盖', '重命名', '改名', '删掉', '删除'),),
)
def delete_file(path, permanent=False):
    """删除文件/目录。默认移入回收站（可恢复）；permanent=True 才物理删除。"""
    p = permissions.resolve(path)
    if not p or not os.path.exists(p):
        return f"错误：路径不存在：{path}"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    try:
        if permanent:
            if os.path.isdir(p):
                shutil.rmtree(p)
            else:
                os.remove(p)
            permissions.audit("delete_file", p, "permanent")
            return f"已物理删除：{p}"
        if os.name == "nt" and _recycle_path(p):
            permissions.audit("delete_file", p, "recycle")
            return f"已移入回收站（可恢复）：{p}"
        if os.path.isdir(p):
            shutil.rmtree(p)
        else:
            os.remove(p)
        permissions.audit("delete_file", p, "permanent(fallback)")
        return f"已删除：{p}"
    except Exception as e:
        return f"错误：删除失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "archive_files",
                "description": "把多个文件/目录打包为 zip 压缩包（工作区内，自动创建目录）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "description": "要打包的文件/目录绝对路径列表", "items": {"type": "string"}},
                        "output": {"type": "string", "description": "输出 zip 文件绝对路径"},
                    },
                    "required": ["paths", "output"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='打包压缩',
    preactivate=(('打包', '压缩成', '归档文件', '压缩包'),),
)
def archive_files(paths, output):
    """把多个文件/目录打包为 zip（工作区内，自动建目录，跳过 .git/__pycache__ 等）。"""
    if not isinstance(paths, list) or not paths:
        return "错误：paths 必须是非空数组（文件/目录路径列表）"
    if not str(output or "").strip():
        return "错误：output 必填"
    import zipfile

    out = permissions.resolve(output)
    if not out:
        return "错误：输出路径无效"
    if not out.lower().endswith(".zip"):
        out += ".zip"
    ok, reason = permissions.check_filesystem(out, write=True)
    if not ok:
        return reason
    resolved = []
    for raw in paths[:50]:
        p = permissions.resolve(raw)
        if not p or not os.path.exists(p):
            return f"错误：路径不存在：{raw}"
        ok, reason = permissions.check_filesystem(p, write=False)
        if not ok:
            return reason
        resolved.append(p)
    try:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        count = 0
        total_bytes = 0
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in resolved:
                if os.path.isdir(p):
                    for root, dirs, files in os.walk(p):
                        dirs[:] = [d for d in dirs if d not in _ARCHIVE_SKIP_DIRS]
                        for fn in files:
                            full = os.path.join(root, fn)
                            if count >= EXTRACT_MAX_ENTRIES:
                                raise ValueError(f"文件数超过上限（{EXTRACT_MAX_ENTRIES}）")
                            fsize = os.path.getsize(full)
                            if total_bytes + fsize > EXTRACT_MAX_TOTAL_BYTES:
                                raise ValueError("总大小超过打包上限")
                            zf.write(full, os.path.relpath(full, os.path.dirname(p)))
                            count += 1
                            total_bytes += fsize
                else:
                    if count >= EXTRACT_MAX_ENTRIES:
                        raise ValueError(f"文件数超过上限（{EXTRACT_MAX_ENTRIES}）")
                    fsize = os.path.getsize(p)
                    if total_bytes + fsize > EXTRACT_MAX_TOTAL_BYTES:
                        raise ValueError("总大小超过打包上限")
                    zf.write(p, os.path.basename(p))
                    count += 1
                    total_bytes += fsize
        size = os.path.getsize(out)
        permissions.audit("archive_files", out, f"{count} 个文件")
        return f"已打包 {count} 个文件到 {out}（{size / 1024:.1f} KB）"
    except Exception as e:
        try:
            if os.path.exists(out):
                os.remove(out)
        except OSError:
            pass
        return f"错误：打包失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "extract_archive",
                "description": "解压 zip 压缩包到目标目录（自动越界防护，防止路径穿越逃逸）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "zip 文件绝对路径"},
                        "dest_dir": {"type": "string", "description": "解压目标目录绝对路径"},
                    },
                    "required": ["path", "dest_dir"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='解压归档',
    preactivate=(('解压', '解包', '解压缩', '解压到'),),
)
def extract_archive(path, dest_dir):
    """解压 zip 到目标目录（zip-slip 越界防护）。"""
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：压缩包不存在：{path}"
    ok, reason = permissions.check_filesystem(p, write=False)
    if not ok:
        return reason
    dest = permissions.resolve(dest_dir) if str(dest_dir or "").strip() else None
    if not dest:
        return "错误：dest_dir 必填"
    ok, reason = permissions.check_filesystem(dest, write=True)
    if not ok:
        return reason
    try:
        os.makedirs(dest, exist_ok=True)
        base = os.path.normpath(dest)
        ext = os.path.splitext(p)[1].lower()
        if ext == ".zip":
            import zipfile
            count = 0
            with zipfile.ZipFile(p) as zf:
                infos = zf.infolist()
                if len(infos) > EXTRACT_MAX_ENTRIES:
                    return f"错误：压缩包条目数超过上限（{EXTRACT_MAX_ENTRIES}），已中止"
                total_size = 0
                for info in infos:
                    target = os.path.normpath(os.path.join(base, info.filename))
                    if not (target == base or target.startswith(base + os.sep)):
                        return f"错误：压缩包含越界条目，已中止：{info.filename}"
                    if info.file_size > EXTRACT_MAX_SINGLE_BYTES:
                        return f"错误：压缩包单文件超过大小上限：{info.filename}"
                    total_size += info.file_size
                    if total_size > EXTRACT_MAX_TOTAL_BYTES:
                        return f"错误：压缩包总解压大小超过上限，已中止"
                for info in infos:
                    zf.extract(info, dest)
                    count += 1
            permissions.audit("extract_archive", dest, f"{count} 个条目")
            return f"已解压 {count} 个条目到 {dest}"
        if ext in (".tar", ".gz", ".tgz"):
            import tarfile
            with tarfile.open(p) as tf:
                members = tf.getmembers()
                if len(members) > EXTRACT_MAX_ENTRIES:
                    return f"错误：压缩包条目数超过上限（{EXTRACT_MAX_ENTRIES}），已中止"
                total_size = sum(m.size for m in members)
                if total_size > EXTRACT_MAX_TOTAL_BYTES:
                    return f"错误：压缩包总解压大小超过上限，已中止"
                for m in members:
                    target = os.path.normpath(os.path.join(base, m.name))
                    if not (target == base or target.startswith(base + os.sep)):
                        return f"错误：压缩包含越界条目，已中止：{m.name}"
                    # tar-slip via 链接：symlink/hardlink 的 linkname 可指向包外
                    # 文件，extractall 会真实创建该链接——解析后必须仍落在解压目录内
                    if m.issym() or m.islnk():
                        ln = (m.linkname or "").replace("\\", "/")
                        if os.path.isabs(ln) or ln.startswith("/"):
                            return f"错误：压缩包含绝对路径链接目标，已中止：{m.name} → {m.linkname}"
                        link_target = os.path.normpath(os.path.join(os.path.dirname(target), ln))
                        if not (link_target == base or link_target.startswith(base + os.sep)):
                            return f"错误：压缩包含越界链接目标，已中止：{m.name} → {m.linkname}"
                try:
                    # Python 3.12+ 内置 data 过滤器（拦越界/绝对路径/外部链接/设备文件）
                    tf.extractall(dest, filter="data")
                except TypeError:
                    # 旧版 Python 不支持 filter 参数：手动校验已覆盖主要攻击面，降级
                    tf.extractall(dest)
            permissions.audit("extract_archive", dest, f"{len(members)} 个条目")
            return f"已解压 {len(members)} 个条目到 {dest}"
        if ext == ".7z":
            import py7zr
            with py7zr.SevenZipFile(p, "r") as z:
                names = z.getnames()
                if len(names) > EXTRACT_MAX_ENTRIES:
                    return f"错误：压缩包条目数超过上限（{EXTRACT_MAX_ENTRIES}），已中止"
                for name in names:
                    target = os.path.normpath(os.path.join(base, name))
                    if not (target == base or target.startswith(base + os.sep)):
                        return f"错误：压缩包含越界条目，已中止：{name}"
                z.extractall(dest)
            permissions.audit("extract_archive", dest, f"{len(names)} 个条目")
            return f"已解压 {len(names)} 个条目到 {dest}"
        if ext == ".rar":
            import rarfile
            with rarfile.RarFile(p) as rf:
                infos = rf.infolist()
                if len(infos) > EXTRACT_MAX_ENTRIES:
                    return f"错误：压缩包条目数超过上限（{EXTRACT_MAX_ENTRIES}），已中止"
                total_size = 0
                for info in infos:
                    target = os.path.normpath(os.path.join(base, info.filename))
                    if not (target == base or target.startswith(base + os.sep)):
                        return f"错误：压缩包含越界条目，已中止：{info.filename}"
                    if info.file_size > EXTRACT_MAX_SINGLE_BYTES:
                        return f"错误：压缩包单文件超过大小上限：{info.filename}"
                    total_size += info.file_size
                    if total_size > EXTRACT_MAX_TOTAL_BYTES:
                        return f"错误：压缩包总解压大小超过上限，已中止"
                rf.extractall(dest)
            permissions.audit("extract_archive", dest, f"{len(infos)} 个条目")
            return f"已解压 {len(infos)} 个条目到 {dest}"
        return f"错误：不支持的压缩格式 {ext or '（无扩展名）'}（支持 zip/tar/gz/7z/rar）"
    except ImportError as e:
        return f"错误：解压该格式需要额外依赖：{e}"
    except Exception as e:
        return f"错误：解压失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "list_snapshots",
                "description": "列出文件/数据库写操作自动快照（删除可恢复：写文件/编辑/重命名/数据库写前自动生成）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "description": "可选：最多列出条数（默认 50）"},
                    },
                    "required": [],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='列出自动快照（写操作前生成，可恢复）',
    preactivate=(('恢复', '撤销', '还原', '回滚文件', '找回', '误删'),),
)
def list_snapshots(limit=50):
    """列出文件/数据库写操作自动快照（删除可恢复：write/edit/rename/execute 前自动生成）。"""
    try:
        items = snapshot_mod.list_snapshots(int(limit or 50))
    except (TypeError, ValueError):
        items = snapshot_mod.list_snapshots(50)
    if not items:
        return "暂无快照（写文件/编辑/重命名/数据库写操作前会自动生成，可在数据目录 undo/ 查看）"
    lines = ["以下操作自动生成了快照，可用 restore_snapshot 恢复（id 为每行开头的编号）："]
    for s in items:
        size = f"{s['size'] / 1024:.1f}KB" if s.get("size") else "?"
        lines.append(f"- {s['id']} | {s.get('op')} | {s.get('ts')} | {s.get('path')} | {size}{(' | ' + s['note']) if s.get('note') else ''}")
    return "\n".join(lines[:60])


@tool(
        {
            "type": "function",
            "function": {
                "name": "restore_snapshot",
                "description": "从自动快照恢复文件原内容（写文件/编辑/重命名/数据库写操作前自动生成；id 来自 list_snapshots）。高危：恢复会覆盖文件当前内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "快照 id（list_snapshots 返回的编号）"},
                    },
                    "required": ["id"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='从快照恢复文件原内容',
    preactivate=(('恢复', '撤销', '还原', '回滚文件', '找回', '误删'),),
)
def restore_snapshot(id):
    """从自动快照恢复文件（写文件/编辑/重命名/数据库操作前的原内容）。id 来自 list_snapshots。"""
    ok, msg = snapshot_mod.restore_snapshot(str(id or "").strip())
    return msg if ok else f"错误：{msg}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "batch_rename",
                "description": "批量重命名：把目录内所有文件名中的 pattern 替换为 replacement（dry_run=true 预览不实际改名）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "directory": {"type": "string", "description": "目录绝对路径"},
                        "pattern": {"type": "string", "description": "要替换的字符串"},
                        "replacement": {"type": "string", "description": "替换后的字符串"},
                        "dry_run": {"type": "boolean", "description": "可选：true 仅预览"},
                    },
                    "required": ["directory", "pattern", "replacement"],
                },
            },
        },
    groups=['📁 文件与目录'],
    phrases='批量重命名',
    preactivate=(('修改', '编辑', '改动', '改一下', '改一次', '改成', '改为', '改下', '改改', '改掉', '更新', '替换', '重写', '覆盖', '重命名', '改名', '删掉', '删除'), ('批量改名', '批量重命名')),
)
def batch_rename(directory, pattern, replacement, dry_run=False):
    """批量重命名：把文件名中的 pattern 全部替换为 replacement（含扩展名）。"""
    d = permissions.resolve(directory)
    if not d or not os.path.isdir(d):
        return f"错误：目录不存在：{directory}"
    ok, reason = permissions.check_filesystem(d, write=True)
    if not ok:
        return reason
    pattern = str(pattern or "")
    if not pattern:
        return "错误：pattern 必填"
    replacement = str(replacement or "")
    try:
        renamed = []
        for fn in sorted(os.listdir(d)):
            if pattern in fn:
                new = fn.replace(pattern, replacement)
                if new == fn:
                    continue
                src = os.path.join(d, fn)
                dst = os.path.join(d, new)
                # 防路径穿越：replacement 若含 ../ 或路径分隔符（/ \）会把文件
                # 移出目标目录，normpath 后必须仍在 d 内才允许
                dst_norm = os.path.normpath(dst)
                if dst_norm != d and not dst_norm.startswith(d.rstrip("\\/") + os.sep):
                    continue
                if os.path.exists(dst):
                    continue
                if not dry_run:
                    snapshot_mod.snapshot_before("batch_rename", src)
                    os.rename(src, dst)
                renamed.append(f"{fn} → {new}")
        if not renamed:
            return f"目录内没有包含「{pattern}」的文件名"
        note = "（预览，未实际重命名）" if dry_run else ""
        if not dry_run:
            permissions.audit("batch_rename", d, f"{len(renamed)} 个文件")
        return f"共 {len(renamed)} 个文件{note}：\n" + "\n".join(renamed[:20])
    except Exception as e:
        return f"错误：批量重命名失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "start_process",
                "description": "在后台启动长驻进程（如网站服务器），实时输出显示在进程终端，返回进程名与 pid",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "完整命令行，如 python -m http.server 8000 或 uvicorn app:app"},
                        "name": {"type": "string", "description": "可选：进程名（便于后续停止/查询）"},
                    },
                    "required": ["command"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='启动后台进程（服务器/长驻任务）',
    preactivate=(('后台进程', '启动服务', '启动服务器', '停止进程', '看进程', '进程列表'),),
)
def start_process(command, name=""):
    """后台启动长驻进程（服务器等），输出实时推送终端面板。"""
    ok, reason, argv = permissions.check_shell(command)
    if not ok:
        return reason
    with _PROCESSES_LOCK:
        for k in [k for k, v in PROCESSES.items() if v.get("exited")]:
            PROCESSES.pop(k, None)
        if len(PROCESSES) >= MAX_PROCESSES:
            return f"错误：后台进程数已达上限（{MAX_PROCESSES} 个），请先 stop_process 停止部分进程"
    try:
        # Python 子进程输出到管道为全缓冲，实时日志需 -u 无缓冲
        if os.path.basename(argv[0]).lower().startswith("python") and "-u" not in argv:
            argv = [argv[0], "-u"] + argv[1:]
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=(
                subprocess.CREATE_NO_WINDOW
                if hasattr(subprocess, "CREATE_NO_WINDOW")
                else 0
            ),
            # 与 run_command 一致：在工作目录启动（服务器/脚本的相对路径引用落地）
            cwd=_dc.WORKING_DIR or permissions.WORKSPACE_DIR or None,
        )
    except Exception as e:
        return f"错误：进程启动失败: {e}"
    # 命名与插入在启动后同一临界区完成：杜绝并发 start 同 base 名互相覆盖
    # （此前查重与插入分开持锁，两并行调用可在间隙抢到同名 → 进程失管无法 stop）
    with _PROCESSES_LOCK:
        for k in [k for k, v in PROCESSES.items() if v.get("exited")]:
            PROCESSES.pop(k, None)
        if len(PROCESSES) >= MAX_PROCESSES:
            _kill_tree(proc)  # 启动后才发现达上限：进程必须回收，不能留孤儿
            return f"错误：后台进程数已达上限（{MAX_PROCESSES} 个），请先 stop_process 停止部分进程"
        base = str(name or "").strip()[:40] or os.path.basename(argv[0]) or f"proc{len(PROCESSES) + 1}"
        proc_name = base
        i = 2
        while proc_name in PROCESSES:
            proc_name = f"{base}_{i}"
            i += 1
        PROCESSES[proc_name] = {
            "proc": proc,
            "pid": proc.pid,
            "name": proc_name,
            "started": datetime.now().strftime("%H:%M:%S"),
            "started_ts": time.time(),
            "exited": False,
            "code": None,
            "lines": deque(maxlen=2000),
        }
    _emit_process(proc_name, f"── 进程启动 pid={proc.pid}（{' '.join(argv)}）──")
    threading.Thread(target=_process_reader, args=(proc_name,), daemon=True).start()
    permissions.audit("start_process", proc_name, " ".join(argv))
    return (
        f"已启动后台进程「{proc_name}」（pid={proc.pid}）\n"
        "实时输出见「工具 → 进程终端」。可用 stop_process 停止，list_processes 查询状态。"
    )


@tool(
        {
            "type": "function",
            "function": {
                "name": "stop_process",
                "description": "停止后台进程（按名称或 pid 定位并终止，进程由 start_process 启动）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "target": {"type": "string", "description": "进程名或 pid，如 http.server 或 12345"},
                    },
                    "required": ["target"],
                },
            },
        },
    groups=['💻 编程与执行'],
    phrases='停止后台进程',
    preactivate=(('后台进程', '启动服务', '启动服务器', '停止进程', '看进程', '进程列表'),),
)
def stop_process(target):
    """停止后台进程（按名称或 pid 定位并终止，进程由 start_process 启动）。"""
    target = str(target or "").strip()
    if not target:
        return "错误：需要进程名或 pid"
    for name, entry in snapshot_processes():
        if name == target or str(entry["pid"]) == target:
            if not entry["exited"]:
                try:
                    _kill_tree(entry["proc"])
                    try:
                        entry["proc"].wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        pass
                except Exception:
                    pass
                with _PROCESSES_LOCK:
                    entry["exited"] = True
                    PROCESSES.pop(name, None)  # 立即回收条目，防失管/重复 stop
                _emit_process(name, "── 已停止 ──")
                return f"已停止进程「{name}」（pid={entry['pid']}）"
            with _PROCESSES_LOCK:
                PROCESSES.pop(name, None)
            return f"进程「{name}」已退出（code={entry['code']}）"
    running = [f"{n}({e['pid']})" for n, e in snapshot_processes()]
    return f"未找到进程：{target}（运行中：{running or '无'}）"


@tool(
        {
            "type": "function",
            "function": {
                "name": "list_processes",
                "description": "列出所有后台进程的运行状态与最近输出（运行中/已退出，可配合停止进程）",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    groups=['💻 编程与执行'],
    phrases='查看后台进程列表',
    preactivate=(('后台进程', '启动服务', '启动服务器', '停止进程', '看进程', '进程列表'),),
)
def list_processes():
    """列出后台进程状态与最近输出。"""
    entries = snapshot_processes()
    if not entries:
        return "当前没有后台进程"
    lines = []
    for name, e in entries:
        status = "运行中" if not e["exited"] else f"已退出(code={e['code']})"
        lines.append(f"· {name}（pid={e['pid']}，{e['started']}，{status}）")
        # 持锁拷贝：reader 线程 extend 与这里迭代并发可能抛 "deque mutated during iteration"
        with _PROCESSES_LOCK:
            recent = list(e["lines"])[-3:]
        for ln in recent:
            lines.append(f"    {ln[:120]}")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "environment_info",
                "description": "获取运行环境信息：Python 版本、已安装的常用包、工作区磁盘空间（避免重复安装已存在的东西）",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    groups=['💻 编程与执行'],
    phrases='环境/依赖信息',
    preactivate=(('环境信息', 'python版本', '已装库', '环境检查', '看环境'),),
)
def environment_info():
    """运行环境信息：避免 AI 重复安装/做无用假设。"""
    import importlib.util

    lines = [f"平台: {sys.platform}", f"Python: {sys.version.split()[0]}"]
    # find_spec 按导入名检测：pillow 的导入名是 PIL（pip 包名 ≠ 导入名）
    _spec_names = {
        "pillow": "PIL", "bs4": "bs4", "yaml": "yaml", "docx": "docx",
        "opencv-python": "cv2", "xlrd": "xlrd", "openpyxl": "openpyxl",
    }
    installed = [
        p if importlib.util.find_spec(_spec_names.get(p, p)) else None
        for p in _COMMON_PACKAGES
    ]
    installed = [p for p in installed if p]
    lines.append(f"已安装常用包: {', '.join(installed) if installed else '（无）'}")
    if permissions.WORKSPACE_DIR:
        try:
            usage = shutil.disk_usage(permissions.WORKSPACE_DIR)
            lines.append(
                f"工作区磁盘: 剩余 {usage.free / 1024 ** 3:.1f}GB / 总 {usage.total / 1024 ** 3:.1f}GB"
            )
        except Exception:
            pass
    return "\n".join(lines)


__all__ = ['read_file', 'write_file', 'edit_file', 'list_dir', 'search_local', 'clipboard_get', 'clipboard_set', 'delete_file', 'archive_files', 'extract_archive', 'list_snapshots', 'restore_snapshot', 'batch_rename', 'start_process', 'stop_process', 'list_processes', 'environment_info']
