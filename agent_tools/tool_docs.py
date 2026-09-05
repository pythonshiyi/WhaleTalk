# -*- coding: utf-8 -*-
"""tool_docs —— P0-1 批量拆分（工具域模块）：📊 数据与文档.

共享符号策略：permissions / security / shared / toolkit 为独立模块直接 import；
阈值常量/锁统一从 shared 导入（P1-3 下沉：见 shared.py「工具域阈值与锁」节）；
仅剩余辅助函数仍依赖主文件加载顺序契约（在 `from agent_tools import *` 前已定义）。
"""

import os
import re
import shutil
import subprocess

import permissions

from shared import clamp_int, PDF_EXTRACT_MAX_OUTPUT, DOCX_MAX_DEFAULT, PPTX_MAX_DEFAULT, KV_VALUE_MAX_BYTES  # D4: 参数校验辅助
from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import deepseek_client as _dc  # 可变注入配置动态访问（dc.X 注入后立即生效）
from deepseek_client import (

    _TABLE_CELL_MAX,
    _atomic_write,
    _db_conn,
    _db_execute_mysql,
    _db_execute_postgres,
    _db_execute_sqlite,
    _load_secrets,
    _md_inline_html,
    _md_table_rows,
    _parse_page_range,
    _read_optional_text,
    _readonly_stmt,
    _register_cjk_font,
    _save_secrets,
    _strip_html_tags,
    _table_to_md,
)
from db_utils import force_limit  # L3: SQL 层强制 LIMIT（防无界查询）
from db_utils import table_to_md as _md_table  # 统一 markdown 表格渲染（含 | 转义）

# L3: SQLite 只读查询语句级超时（progress handler 中断慢查询，防占住共享工具线程池）
_SQLITE_QUERY_TIMEOUT_S = 15.0



@tool(
        {
            "type": "function",
            "function": {
                "name": "database_query_mysql",
                "description": "MySQL 只读查询（SELECT/SHOW/DESC）。连接在数据目录 db_config.json 的 mysql.<connection> 配置",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "connection": {"type": "string", "description": "可选：连接名（默认 default）"},
                        "sql": {"type": "string", "description": "只读 SQL 语句"},
                        "max_rows": {"type": "integer", "description": "可选：最多返回行数（默认 20）"},
                    },
                    "required": ["sql"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='MySQL 只读查询',
    preactivate=(('数据库', 'sql', 'mysql', 'postgres'),),
)
def database_query_mysql(connection="default", sql="", max_rows=20):
    """MySQL 只读查询（SELECT/SHOW/DESC；连接在 db_config.json 配置）。"""
    try:
        import pymysql
    except ImportError:
        return "错误：需要 pymysql（pip install pymysql）"
    if not str(sql or "").strip():
        return "错误：sql 必填"
    cfg, err = _db_conn("mysql", connection)
    if cfg is None:
        return err
    if not _readonly_stmt(sql):
        return "错误：仅允许只读查询（SELECT / SHOW / DESC）"
    limit = clamp_int(max_rows, 20, lo=1, hi=200)
    sql = force_limit(sql, limit)  # L3: SQL 层强制 LIMIT
    try:
        conn = pymysql.connect(
            host=str(cfg.get("host") or "127.0.0.1"),
            port=int(cfg.get("port") or 3306),
            user=str(cfg.get("user") or ""),
            password=str(cfg.get("password") or ""),
            database=str(cfg.get("database") or ""),
            charset="utf8mb4", connect_timeout=5, read_timeout=15,
        )
        try:
            cur = conn.cursor()
            try:
                cur.execute("SET SESSION max_execution_time=15000")
            except Exception:
                pass
            cur.execute(str(sql))
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(limit)
            lines = [" | ".join(cols)] if cols else []
            for r in rows:
                cells = [str(x) if x is not None else "" for x in r]
                cells = [c[:_TABLE_CELL_MAX] + ("…" if len(c) > _TABLE_CELL_MAX else "") for c in cells]
                lines.append(" | ".join(cells))
            extra = "" if len(rows) < limit else " [已截断]"
            return "\n".join(lines) + extra if lines else "执行成功（无结果集）"
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        return f"错误：MySQL 查询失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "database_query_postgres",
                "description": "PostgreSQL 只读查询（SELECT/SHOW/DESC）。连接在数据目录 db_config.json 的 postgres.<connection> 配置",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "connection": {"type": "string", "description": "可选：连接名（默认 default）"},
                        "sql": {"type": "string", "description": "只读 SQL 语句"},
                        "max_rows": {"type": "integer", "description": "可选：最多返回行数（默认 20）"},
                    },
                    "required": ["sql"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='PostgreSQL 只读查询',
    preactivate=(('数据库', 'sql', 'mysql', 'postgres'),),
)
def database_query_postgres(connection="default", sql="", max_rows=20):
    """PostgreSQL 只读查询（SELECT/SHOW/DESC；连接在 db_config.json 配置）。"""
    try:
        import psycopg2
    except ImportError:
        return "错误：需要 psycopg2（pip install psycopg2）"
    if not str(sql or "").strip():
        return "错误：sql 必填"
    cfg, err = _db_conn("postgres", connection)
    if cfg is None:
        return err
    if not _readonly_stmt(sql):
        return "错误：仅允许只读查询（SELECT / SHOW / DESC）"
    limit = clamp_int(max_rows, 20, lo=1, hi=200)
    sql = force_limit(sql, limit)  # L3: SQL 层强制 LIMIT
    try:
        conn = psycopg2.connect(
            host=str(cfg.get("host") or "127.0.0.1"),
            port=int(cfg.get("port") or 5432),
            user=str(cfg.get("user") or ""),
            password=str(cfg.get("password") or ""),
            dbname=str(cfg.get("database") or ""),
            connect_timeout=5,
            # 语句超时：只读查询最长 15 秒，防慢查询占住共享工具线程池
            options="-c statement_timeout=15000",
        )
        try:
            cur = conn.cursor()
            cur.execute(str(sql))
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(limit)
            lines = [" | ".join(cols)] if cols else []
            for r in rows:
                cells = [str(x) if x is not None else "" for x in r]
                cells = [c[:_TABLE_CELL_MAX] + ("…" if len(c) > _TABLE_CELL_MAX else "") for c in cells]
                lines.append(" | ".join(cells))
            extra = "" if len(rows) < limit else " [已截断]"
            return "\n".join(lines) + extra if lines else "执行成功（无结果集）"
        finally:
            try:
                conn.close()
            except Exception:
                pass
    except Exception as e:
        return f"错误：PostgreSQL 查询失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "read_excel",
                "description": "读取 Excel 文件（.xlsx，openpyxl），返回 markdown 表格（首行默认作表头，has_header=false 时补占位表头）；公式列读到缓存值，若为空会给出提示",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Excel 文件绝对路径"},
                        "sheet": {"type": "string", "description": "可选：工作表名或序号（默认第一个）"},
                        "max_rows": {"type": "integer", "description": "可选：最多返回行数（默认 100）"},
                        "has_header": {"type": "boolean", "description": "可选：首行是否为表头（默认 true；false 时补占位列名 col_1..col_n）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='读 Excel',
    preactivate=(('表格', 'excel', 'csv', '报表'),),
)
def read_excel(path, sheet=0, max_rows=100, has_header=True):
    """读取 Excel 文件（openpyxl，.xlsx）。返回 markdown 表格；首行默认作表头。"""
    try:
        from openpyxl import load_workbook
    except ImportError:
        return "错误：需要 openpyxl（pip install openpyxl）"
    if not path or not str(path).strip():
        return "错误：path 必填"
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：文件不存在：{p}"
    ok, reason = permissions.check_filesystem(p, write=False)
    if not ok:
        return reason
    try:
        limit = clamp_int(max_rows, 100, lo=1, hi=500)
    except (TypeError, ValueError):
        limit = 100
    try:
        wb = load_workbook(p, read_only=True, data_only=True)
        try:
            if isinstance(sheet, int):
                ws = wb.worksheets[min(sheet, len(wb.worksheets) - 1)] if wb.worksheets else wb.active
            else:
                ws = wb[sheet]
        except KeyError:
            return f"错误：工作表不存在：{sheet}"
        grid = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= limit:
                break
            grid.append([("" if v is None else v) for v in row])
        if not grid:
            return "（空工作表）"
        # 公式无缓存值探测：data_only=True 只能读缓存，程序新写 / 未用 Excel 打开过的
        # 公式单元格值为 None——单独开 data_only=False 检查是否"疑似公式"（值以 = 开头）。
        formula_hit = None
        try:
            if any(v is None or (isinstance(v, str) and not v) for row in grid for v in row):
                wb_f = load_workbook(p, data_only=False)
                ws_f = wb_f[wb_f.sheetnames[wb.sheetnames.index(ws.title)]] if ws.title in wb_f.sheetnames else wb_f.active
                for row in ws_f.iter_rows(min_row=1, max_row=len(grid), max_col=max((len(r) for r in grid), default=1)):
                    for cell in row:
                        if isinstance(cell.value, str) and cell.value.lstrip().startswith("="):
                            formula_hit = cell.coordinate
                            break
                    if formula_hit:
                        break
        except Exception:
            pass
        # 归一化：全为字符串/数字，交给 _md_table 统一截断 + 转义
        body = [[("" if c is None else str(c)) for c in row] for row in grid]
        if not bool(has_header):
            ncols = max(len(r) for r in body) if body else 1
            header = [f"col_{i}" for i in range(1, ncols + 1)]
            data_rows = body
        else:
            header, data_rows = body[0], body[1:]
        md = _md_table([header] + data_rows) if data_rows else _md_table([header])
        note = ""
        if len(grid) >= limit:
            note += f"[前 {limit} 行…]"
        if formula_hit is not None:
            note += f"\n[提示: {formula_hit} 含公式，当前读到的是缓存值/为空；请先用 Excel 打开保存或指定 data_only=False]"
        return f"{md}\n[共 {len(grid)} 行{note}]"
    except Exception as e:
        return f"错误：读取 Excel 失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "epub_read",
                "description": "读取 EPUB 电子书正文为纯文本（依赖 ebooklib，缺失时返回安装指引）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "EPUB 文件绝对路径"},
                        "max_chars": {"type": "integer", "description": "可选：最多返回字符数（默认 20000）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='读取 epub 电子书',
    preactivate=(('电子书', 'epub', 'mobi', 'kindle'),),
)
def epub_read(path, max_chars=20000):
    """读取 EPUB 电子书正文为纯文本（依赖 ebooklib，缺失时返回安装指引）。"""
    p_or_err = _read_optional_text(path, max_chars)
    if p_or_err[0] is None:
        return p_or_err[1]
    p, limit = p_or_err
    try:
        import ebooklib
        from ebooklib import epub
    except ImportError:
        return "错误：需要 ebooklib（pip install ebooklib）"
    try:
        book = epub.read_epub(p)
        parts = []
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
            body = item.get_body_content() or b""
            parts.append(_strip_html_tags(body.decode("utf-8", errors="replace")))
        text = "\n\n".join(x for x in parts if x)
        if not text:
            return "（EPUB 无正文内容）"
        if len(text) > limit:
            text = text[:limit] + f"\n[正文已截断前 {limit} 字符]"
        return text
    except Exception as e:
        return f"错误：读取 EPUB 失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "mobi_read",
                "description": "读取 MOBI 电子书正文为纯文本（依赖 mobi 库，缺失时返回安装指引）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "MOBI 文件绝对路径"},
                        "max_chars": {"type": "integer", "description": "可选：最多返回字符数（默认 20000）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='读取 mobi 电子书',
    preactivate=(('电子书', 'epub', 'mobi', 'kindle'),),
)
def mobi_read(path, max_chars=20000):
    """读取 MOBI 电子书正文为纯文本（依赖 mobi 库，缺失时返回安装指引）。"""
    p_or_err = _read_optional_text(path, max_chars)
    if p_or_err[0] is None:
        return p_or_err[1]
    p, limit = p_or_err
    try:
        from mobi import Mobi
    except ImportError:
        return "错误：需要 mobi（pip install mobi）"
    try:
        book = Mobi(p)
        book.parse()
        text = str(book) if hasattr(book, "__str__") else ""
        if not text:
            text = "\n\n".join(str(getattr(book, field, "")) for field in ("title", "author", "publisher", "description"))
        if len(text) > limit:
            text = text[:limit] + f"\n[正文已截断前 {limit} 字符]"
        return text or "（MOBI 解析无文本）"
    except Exception as e:
        return f"错误：读取 MOBI 失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "doc_read",
                "description": "读取旧版 .doc 二进制文档正文（依赖本机 antiword/catdoc 命令行工具）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": ".doc 文件绝对路径"},
                        "max_chars": {"type": "integer", "description": "可选：最多返回字符数（默认 20000）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='读取 doc/rtf 等旧格式',
    preactivate=(('outlook', 'msg邮件', 'msg文件', '旧版doc', 'rtf'),),
)
def doc_read(path, max_chars=20000):
    """读取旧版 .doc 二进制文档（优先 antiword，其次 catdoc）。"""
    p_or_err = _read_optional_text(path, max_chars)
    if p_or_err[0] is None:
        return p_or_err[1]
    p, limit = p_or_err
    import shutil as _shutil
    for exe in ("antiword", "catdoc"):
        if _shutil.which(exe):
            try:
                proc = subprocess.run(
                    [exe, p], capture_output=True, text=True, timeout=30,
                    errors="replace", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                text = (proc.stdout or "").strip() or (proc.stderr or "").strip()
                if text:
                    if len(text) > limit:
                        text = text[:limit] + f"\n[正文已截断前 {limit} 字符]"
                    return text
            except Exception as e:
                return f"错误：读取 .doc 失败（{exe}）: {e}"
    return "错误：读取 .doc 需要 antiword 或 catdoc（Windows 可安装 antiword 或改用 .docx）"


@tool(
        {
            "type": "function",
            "function": {
                "name": "msg_read",
                "description": "读取 .msg Outlook 邮件（主题/发件人/正文/附件清单；依赖 extract_msg，缺失时返回安装指引）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": ".msg 文件绝对路径"},
                        "max_chars": {"type": "integer", "description": "可选：正文最多返回字符数（默认 20000）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📧 邮件与消息'],
    phrases='读取邮件消息',
    preactivate=(('outlook', 'msg邮件', 'msg文件', '旧版doc', 'rtf'),),
)
def msg_read(path, max_chars=20000):
    """读取 .msg Outlook 邮件（依赖 extract_msg，缺失时返回安装指引），返回主题/发件人/正文/附件清单。"""
    p_or_err = _read_optional_text(path, max_chars)
    if p_or_err[0] is None:
        return p_or_err[1]
    p, limit = p_or_err
    try:
        import extract_msg
    except ImportError:
        return "错误：需要 extract_msg（pip install extract-msg）"
    try:
        msg = extract_msg.Message(p)
        attachments = [a.longFilename or a.shortFilename or "?" for a in (msg.attachments or [])]
        lines = [
            f"主题：{msg.subject}",
            f"发件人：{msg.sender}",
            f"收件人：{msg.to}",
            f"日期：{msg.date}",
        ]
        if attachments:
            lines.append(f"附件（{len(attachments)}）：{', '.join(attachments[:20])}")
        body = str(msg.body or "").strip()
        if body:
            lines.append("正文：")
            if len(body) > limit:
                body = body[:limit] + f"\n[正文已截断前 {limit} 字符]"
            lines.append(body)
        return "\n".join(lines)
    except Exception as e:
        return f"错误：读取 .msg 失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "archive_list",
                "description": "列出压缩包内容：.zip / .tar / .gz / .7z / .rar（7z/rar 依赖 py7zr/rarfile 库）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "压缩包绝对路径"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='列出归档内容',
    preactivate=(('打包', '压缩成', '归档文件', '压缩包'), ('解压', '解包', '解压缩', '解压到')),
)
def archive_list(path):
    """列出压缩包内容：.zip / .tar / .gz / .7z / .rar（7z/rar 依赖 py7zr/rarfile 库）。"""
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：压缩包不存在：{path}"
    ok, reason = permissions.check_filesystem(p, write=False)
    if not ok:
        return reason
    ext = os.path.splitext(p)[1].lower()
    try:
        if ext in (".zip",):
            import zipfile
            with zipfile.ZipFile(p) as zf:
                infos = zf.infolist()
                lines = [f"压缩包 {path} 共 {len(infos)} 个条目："]
                for info in infos[:100]:
                    lines.append(f"· {info.filename}（{info.file_size} 字节）")
                if len(infos) > 100:
                    lines.append(f"… 其余 {len(infos) - 100} 个条目略")
                return "\n".join(lines)
        if ext in (".tar", ".gz", ".tgz"):
            import tarfile
            with tarfile.open(p) as tf:
                members = tf.getmembers()
                lines = [f"压缩包 {path} 共 {len(members)} 个条目："]
                for m in members[:100]:
                    lines.append(f"· {m.name}（{m.size} 字节）")
                if len(members) > 100:
                    lines.append(f"… 其余 {len(members) - 100} 个条目略")
                return "\n".join(lines)
        if ext == ".7z":
            import py7zr
            with py7zr.SevenZipFile(p, "r") as z:
                items = z.getnames()
                lines = [f"压缩包 {path} 共 {len(items)} 个条目："]
                for name in items[:100]:
                    lines.append(f"· {name}")
                if len(items) > 100:
                    lines.append(f"… 其余 {len(items) - 100} 个条目略")
                return "\n".join(lines)
        if ext == ".rar":
            import rarfile
            with rarfile.RarFile(p) as rf:
                infos = rf.infolist()
                lines = [f"压缩包 {path} 共 {len(infos)} 个条目："]
                for info in infos[:100]:
                    lines.append(f"· {info.filename}（{info.file_size} 字节）")
                if len(infos) > 100:
                    lines.append(f"… 其余 {len(infos) - 100} 个条目略")
                return "\n".join(lines)
        return f"错误：不支持的压缩格式 {ext or '（无扩展名）'}（支持 zip/tar/gz/7z/rar）"
    except ImportError as e:
        return f"错误：读取该格式需要额外依赖：{e}"
    except Exception as e:
        return f"错误：读取压缩包失败: {e}"


def _excel_rows_columns(data_rows):
    """C2: 从行数据推断列名：dict 行取首行 keys，否则返回 None（数组行无表头）。"""
    if data_rows and isinstance(data_rows[0], dict):
        return list(data_rows[0].keys())
    return None


def _excel_append_rows(ws, data_rows, cols):
    """C2: 把数据行写入 worksheet；dict 行按 cols 取字段（缺键补空），混合行健壮化。
    数组行保留原始类型（数字不转字符串），与 dict 行行为一致。"""
    if cols is not None:
        for row in data_rows:
            vals = [row.get(c, "") for c in cols] if isinstance(row, dict) else [""] * len(cols)
            ws.append(vals)
    else:
        for row in data_rows:
            ws.append(list(row) if isinstance(row, (list, tuple)) else [row])


@tool(
        {
            "type": "function",
            "function": {
                "name": "write_excel",
                "description": "写入 Excel（.xlsx）。mode=overwrite 覆盖(默认，备份.bak)、append 追加到已有文件末尾、update 覆盖指定 sheet 首块区域(保留其它单元格/格式)。data=行数组或对象数组；sheets={表名:数据行} 多表。可选 header/start_cell/style/charts（见参数）。数值类型保留",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "输出文件绝对路径"},
                        "data": {"type": "array", "items": {}, "description": "数据行（sheets 提供时可传 []）"},
                        "sheet": {"type": "string", "description": "可选：工作表名（默认 Sheet1）"},
                        "mode": {"type": "string", "description": "可选：overwrite 覆盖（默认）/ append 追加到已有文件末尾 / update 覆盖指定 sheet 的起始区域（保留其余单元格与格式）"},
                        "sheets": {"type": "object", "description": "可选：{表名: 数据行} 多表，与 data 二选一"},
                        "header": {"type": "boolean", "description": "可选：是否写入表头行（默认自动：dict 数据写键名；数组数据不写，可用 headers 指定）"},
                        "headers": {"type": "array", "items": {"type": "string"}, "description": "可选：表头数组（数组数据且 header=true 时写第一行）"},
                        "start_cell": {"type": "string", "description": "可选：起始单元格（如 A1/B2），从该处写入（覆盖/update 用）；默认从 A1 顺序写"},
                        "style": {"type": ["boolean", "object"], "description": "可选：true=表头加粗+蓝底+冻结首行+自动筛选+自适应列宽；或 {bold_header,fill,freeze,autofilter,autofit,fill_color} 细调"},
                        "charts": {"type": "array", "items": {}, "description": "可选：在数据旁内嵌原生图表，每项 {type:bar/line/pie, title, categories(类目数组或列引用), values(数值数组/列引用), series_name}"},
                    },
                    "required": ["path", "data"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='写 Excel',
    preactivate=(('表格', 'excel', 'csv', '报表'),),
)
def write_excel(path, data, sheet="Sheet1", mode="overwrite", sheets=None,
                header=None, headers=None, start_cell="", style=None, charts=None):
    """写入 Excel（.xlsx）：overwrite / append / update；支持表头/样式/起始格/原生图表。"""
    try:
        from openpyxl import Workbook, load_workbook
        from openpyxl.utils.cell import coordinate_from_string, column_index_from_string
    except ImportError:
        return "错误：需要 openpyxl（pip install openpyxl）"
    if not path or not str(path).strip():
        return "错误：path 必填"
    if mode not in ("overwrite", "append", "update"):
        return "错误：mode 仅支持 overwrite（覆盖）/ append（追加）/ update（更新指定区域）"
    p = permissions.resolve(path)
    if not p:
        return "错误：路径无效"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    # 起始格解析（A1 / B2 / C3 等）
    start_r = 1
    start_c = 1
    if str(start_cell or "").strip():
        try:
            sc = str(start_cell).strip().upper()
            col_let, row_num = coordinate_from_string(sc)
            start_c = column_index_from_string(col_let)
            start_r = int(row_num)
        except Exception:
            return f"错误：start_cell 格式无效：{start_cell}（应如 A1 / B2）"
    # 归一化写入计划
    if sheets is not None:
        if not isinstance(sheets, dict) or not sheets:
            return "错误：sheets 必须是 {表名: 数据行} 的非空对象"
        plan = {str(k)[:31]: v for k, v in sheets.items()}
        for sname, rows in plan.items():
            if not isinstance(rows, list):
                return f"错误：表 {sname} 的数据必须是数组"
    else:
        if not isinstance(data, list):
            return "错误：data 必须是非空数组"
        plan = {str(sheet or "Sheet1")[:31]: data}
    try:
        if mode == "update":
            if not os.path.isfile(p):
                return f"错误：update 模式要求文件已存在：{p}"
            wb = load_workbook(p)
        elif mode == "append":
            if not os.path.isfile(p):
                return f"错误：append 模式要求文件已存在：{p}"
            wb = load_workbook(p)
        else:  # overwrite
            had_existing = os.path.isfile(p)
            if had_existing:
                try:
                    shutil.copy2(p, p + ".bak")
                except Exception:
                    pass
            wb = Workbook()
            wb.remove(wb.active)
        total = 0
        header_written_rows = []  # (ws, header_row) for style
        for sname, rows in plan.items():
            if sname in wb.sheetnames:
                ws = wb[sname]
                ws_existed = True
            elif mode in ("append", "update") and sname == "Sheet1" and wb.sheetnames and wb.sheetnames == ["Sheet"]:
                # 兼容：默认表名 Sheet1 与 openpyxl 默认活动表 "Sheet" 不一致。
                # append/update 到既有文件且目标恰好是默认名、文件只有一张默认 "Sheet" 时，
                # 落到该活动表（不新建同名空表，避免写进一张用户看不见的 Sheet1）。
                ws = wb[wb.sheetnames[0]]
                ws_existed = True
            else:
                ws = wb.create_sheet(sname)
                ws_existed = False
            cols = _excel_rows_columns(rows)  # dict 数据 → 键名；数组数据 → None
            if cols is None and headers:
                cols = [str(h) for h in headers]
            # 表头开关：显式 header 优先；否则仅对"本调用新建的表"自动写表头
            write_header = bool(header) if header is not None else (cols is not None and not ws_existed)
            # 起始行定位
            if mode == "append" and ws_existed:
                # 已有表：追加到内容末尾（不重写表头）
                write_header = False
                cur_r = (ws.max_row or 0) + 1
                if cur_r < 1:
                    cur_r = 1
            elif mode == "append" and not ws_existed:
                cur_r = 1  # 新建表从 A1 起，含表头
            elif mode == "update":
                # 覆盖指定起始区域（默认 A1），保留该区域外其它单元格
                write_header = bool(header)  # update 只在显式要求时才写表头，避免覆盖既有表头
                cur_r = start_r
            else:  # overwrite
                cur_r = start_r
            if write_header and cols:
                for j, colname in enumerate(cols):
                    ws.cell(row=cur_r, column=start_c + j, value=colname)
                header_written_rows.append((ws, cur_r))
                cur_r += 1
            # 写数据（dict 行按 cols 取字段，缺键补空；数组行保留类型与位置）
            if cols is not None:
                for row in rows:
                    vals = [row.get(c, "") for c in cols] if isinstance(row, dict) else [""] * len(cols)
                    for j, v in enumerate(vals):
                        ws.cell(row=cur_r, column=start_c + j, value=v)
                    cur_r += 1
            else:
                for row in rows:
                    seq = list(row) if isinstance(row, (list, tuple)) else [row]
                    for j, v in enumerate(seq):
                        ws.cell(row=cur_r, column=start_c + j, value=v)
                    cur_r += 1
            total += len(rows)
            # 记录本表实表头行（供样式阶段用），并记录列数
            last_written = (ws, cur_r)
        # 统一应用样式（表头加粗/冻结/筛选/列宽），取各表实际写入的头行
        if style:
            for _sname, _rows in plan.items():
                _ws2 = wb[_sname]
                _ncol = max((len(r) for r in _rows), default=0)
                # 找本表是否写了表头
                _hrow = None
                for _wi, _hr in header_written_rows:
                    if _wi is _ws2:
                        _hrow = _hr
                        break
                _excel_apply_style(_ws2, header_row=_hrow,
                                   ncols=_ncol or (_ws2.max_column or 1),
                                   style_spec=style)
        # 原生图表
        if charts:
            for cspec in (charts if isinstance(charts, list) else [charts]):
                _excel_embed_chart(wb, cspec)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        wb.save(p)
        if mode == "append":
            return f"已追加 Excel 至 {p}（{total} 行，{len(plan)} 个工作表）"
        if mode == "update":
            return f"已更新 Excel {p}（{total} 行写入，保留其余单元格与格式）"
        warn = (
            "（原文件已备份为 .bak；原文件的其它工作表/公式/样式/图表不保留，如需保留请改用 mode=append/update）"
            if had_existing
            else ""
        )
        return f"已写入 Excel 至 {p}（{total} 行，{len(plan)} 个工作表）{warn}"
    except Exception as e:
        return f"错误：写入 Excel 失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "xlsx_edit",
                "description": "就地编辑 .xlsx 指定单元格（保留其它单元格、公式、样式、其它工作表）。cells 传 {\"A1\":值,\"B2\":值,...}，值可为字符串/数字/公式字符串（以 = 开头原样写入公式）；可选 cell_type 数值/文本",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": ".xlsx 文件绝对路径"},
                        "cells": {"type": "object", "description": "必填：{单元格坐标: 值}，如 {\"A1\":\"标题\",\"C5\":\"=SUM(C1:C4)\"}"},
                        "sheet": {"type": "string", "description": "可选：工作表名（默认活动表）"},
                    },
                    "required": ["path", "cells"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='编辑 Excel 单元格',
    preactivate=(('表格', 'excel', 'csv', '报表'),),
)
def xlsx_edit(path, cells, sheet=""):
    """就地编辑 .xlsx 单元格：保留其它内容/格式。值以 = 开头按公式写入。"""
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.cell import coordinate_from_string as _cfs
    except ImportError:
        return "错误：需要 openpyxl（pip install openpyxl）"
    if not str(path or "").strip():
        return "错误：path 必填"
    if not isinstance(cells, dict) or not cells:
        return "错误：cells 必须是 {单元格: 值} 的非空对象"
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：文件不存在：{path}"
    if not p.lower().endswith(".xlsx"):
        return "错误：仅支持 .xlsx"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    try:
        try:
            shutil.copy2(p, p + ".bak")
        except Exception:
            pass
        wb = load_workbook(p)  # 默认保留公式与格式
        if str(sheet or "").strip():
            if sheet not in wb.sheetnames:
                return f"错误：工作表不存在：{sheet}"
            ws = wb[sheet]
        else:
            ws = wb.active
        updated = 0
        for coord, val in cells.items():
            c = str(coord).strip().upper()
            try:
                _cfs(c)
            except Exception:
                return f"错误：非法单元格坐标：{coord}"
            # 值归一化：None 清空；布尔/数字保留；以 = 开头写公式
            if val is None:
                ws[c] = None
            elif isinstance(val, str) and val.lstrip().startswith("="):
                ws[c] = val  # openpyxl 视 = 开头为公式
            elif isinstance(val, bool):
                ws[c] = val
            else:
                ws[c] = val
            updated += 1
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        wb.save(p)
        permissions.audit("xlsx_edit", p, f"{updated} 个单元格")
        return f"已更新 {updated} 个单元格：{p}（保留其它内容/公式/样式，原文件备份 .bak）"
    except Exception as e:
        return f"错误：Excel 编辑失败: {e}"


def _excel_apply_style(ws, header_row, ncols, style_spec):
    """给工作表加样式：表头加粗/填色/冻结/筛选/自适应列宽。
    style_spec=True 用默认；或 dict：{bold_header,fill,freeze,autofilter,autofit,fill_color}。"""
    try:
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter
    except Exception:
        return
    if isinstance(style_spec, dict):
        st = dict(style_spec)
    else:
        st = {}
    fill_color = str(st.get("fill_color") or "2B4C7E").lstrip("#")
    ncols = max(int(ncols or 0), 1)
    if header_row is not None and st.get("bold_header", True):
        try:
            fill = PatternFill(start_color=fill_color, end_color=fill_color, fill_type="solid")
            for j in range(1, ncols + 1):
                c = ws.cell(row=header_row, column=j)
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = fill
        except Exception:
            pass
    if st.get("freeze", True) and header_row is not None:
        try:
            ws.freeze_panes = ws.cell(row=header_row + 1, column=1).coordinate
        except Exception:
            pass
    if st.get("autofilter", True) and header_row is not None:
        try:
            last_col = get_column_letter(ncols)
            ws.auto_filter.ref = f"A{header_row}:{last_col}{max(header_row + 1, ws.max_row or header_row + 1)}"
        except Exception:
            pass
    if st.get("autofit", True):
        try:
            for j in range(1, ncols + 1):
                col_letter = get_column_letter(j)
                maxlen = 8
                for r in range(1, min((ws.max_row or 1) + 1, 200)):
                    v = ws.cell(row=r, column=j).value
                    if v is not None:
                        ln = len(str(v))
                        if ln > maxlen:
                            maxlen = ln
                ws.column_dimensions[col_letter].width = min(maxlen * 1.2 + 2, 50)
        except Exception:
            pass


def _excel_embed_chart(wb, cspec):
    """在工作簿末尾追加一张带原生图表的图表面板（bar/line/pie/scatter）。

    cspec：{type, title, categories:[类目], values:[数值] 或 {系列名:[数值],...}}。
    数组先写入图表面板列，再以 Reference 建图，避免引用错位。
    """
    try:
        from openpyxl.chart import BarChart, LineChart, PieChart, Reference
        from openpyxl.chart.series import SeriesLabel as _SL
        from openpyxl.chart import Series as _CSeries
    except Exception:
        return
    try:
        ctype = str(cspec.get("type") or "bar").lower()
        title = str(cspec.get("title") or "")
        chart_sheet = wb.create_sheet(title or f"{ctype}chart")
        cats = cspec.get("categories") or []
        if not isinstance(cats, (list, tuple)):
            cats = []
        vals = cspec.get("values") or []
        # 归一化为 {系列名: 数值列表}
        if isinstance(vals, dict):
            series = {str(k): v for k, v in vals.items() if isinstance(v, (list, tuple))}
            if not cats:
                cats = list(range(1, max((len(v) for v in series.values()), default=0) + 1))
        elif isinstance(vals, (list, tuple)) and vals and isinstance(vals[0], dict):
            series = {}
            for item in vals:
                name = str(item.get("name") or f"系列{len(series) + 1}")
                d = item.get("data") or []
                if isinstance(d, (list, tuple)):
                    series[name] = d
        else:
            series = {"数据": list(vals) if isinstance(vals, (list, tuple)) else []}
        if not series:
            return
        n = max((len(v) for v in series.values()), default=0)
        if not cats and n:
            cats = list(range(1, n + 1))
        # 写入图表面板列：A=类目，B..=各系列
        for i, c in enumerate(cats):
            chart_sheet.cell(row=2 + i, column=1, value=c)
        col_idx = 2
        for sname, sdata in series.items():
            for i, v in enumerate(sdata):
                chart_sheet.cell(row=2 + i, column=col_idx, value=v)
            col_idx += 1
        # 建图
        if ctype == "pie":
            chart = PieChart()
        elif ctype == "line":
            chart = LineChart()
        elif ctype == "scatter":
            chart = None
        else:
            chart = BarChart()
        cat_ref = Reference(chart_sheet, min_col=1, min_row=2, max_row=2 + max(n - 1, 0))
        if ctype == "scatter":
            from openpyxl.chart import ScatterChart as _SC
            chart = _SC()
            for si, (sname, sdata) in enumerate(series.items()):
                # x 用序号列（复制到 C 之后的空列避免与类目冲突）
                xcol = col_idx + si
                for i in range(len(sdata)):
                    chart_sheet.cell(row=2 + i, column=xcol, value=i + 1)
                yref = Reference(chart_sheet, min_col=2 + si, min_row=2, max_row=2 + len(sdata) - 1)
                xref = Reference(chart_sheet, min_col=xcol, min_row=2, max_row=2 + len(sdata) - 1)
                s = _CSeries(yref, xvalues=xref, title=_SL(v=str(sname)))
                chart.series.append(s)
        else:
            for si, (sname, sdata) in enumerate(series.items()):
                data_ref = Reference(chart_sheet, min_col=2 + si, min_row=1, max_row=1 + max(n, 0))
                # 系列名放第一行以便 add_data from_rows
                chart_sheet.cell(row=1, column=2 + si, value=sname)
            data_ref = Reference(chart_sheet, min_col=2, min_row=1, max_col=2 + len(series) - 1, max_row=1 + n)
            chart.add_data(data_ref, titles_from_data=True)
            if ctype != "pie":
                chart.set_categories(cat_ref)
        if title:
            chart.title = title
        chart_sheet.add_chart(chart, "E2")
        try:
            chart_sheet.sheet_view.showGridLines = False
        except Exception:
            pass
    except Exception:
        pass


def _chart_cjk_fonts():
    """C3: 探测系统可用的中文字体（Windows/macOS/Linux），返回 matplotlib sans-serif 候选。
    硬编码单字体在 Linux/无 YaHei 环境会中文变方块；这里按系统字体表实测筛选。"""
    try:
        from matplotlib import font_manager
        available = {f.name for f in font_manager.fontManager.ttflist}
        candidates = [
            "Microsoft YaHei", "SimHei",  # Windows
            "PingFang SC", "Hiragino Sans GB", "Heiti SC", "STHeiti",  # macOS
            "Noto Sans CJK SC", "Source Han Sans SC", "Source Han Sans CN",  # Linux
            "WenQuanYi Zen Hei", "WenQuanYi Micro Hei", "AR PL UMing CN",
            "DejaVu Sans",  # 兜底（无中文但保证不崩）
        ]
        hits = [c for c in candidates if c in available]
        return hits or ["DejaVu Sans"]
    except Exception:
        return ["DejaVu Sans"]


def _chart_parse_series(data):
    """C3: 把 chart_data 的 data 归一化为 [{"name", "xs", "ys"}] 系列列表（兼容旧单系列格式）。
    多系列：data=[{"name":"A","data":[1,2,3]}, {"name":"B","data":[[x,y],...]}]（data 支持数值数组 /
    [x,y] 对数组 / {"x":[], "y":[]} 对象）。"""
    if isinstance(data, dict) and isinstance(data.get("series"), list):
        data = data["series"]
    if not isinstance(data, list) or not data:
        raise ValueError("data 必须是非空数组")

    def to_float(v):
        try:
            return float(v or 0)
        except (TypeError, ValueError):
            raise ValueError(f"非数值数据：{v!r}（请只传数字）")

    first = data[0]
    if isinstance(first, dict) and "data" in first:
        # 多系列：每项 {"name","data":[...]} 或 {"name","data":{"x":[],"y":[]}}
        series = []
        for item in data:
            if not isinstance(item, dict) or "data" not in item:
                raise ValueError(f"多系列项必须是 {{name, data}} 对象：{item!r}")
            name = str(item.get("name", f"系列{len(series) + 1}"))
            rows = item["data"]
            if isinstance(rows, dict) and "y" in rows:
                xs = [to_float(v) for v in rows.get("x", [])] or list(range(len(rows["y"])))
                ys = [to_float(v) for v in rows["y"]]
            elif rows and isinstance(rows[0], (list, tuple)) and len(rows[0]) >= 2:
                xs = [str(r[0]) for r in rows]
                ys = [to_float(r[1]) for r in rows]
            elif rows and isinstance(rows[0], dict) and "y" in rows[0]:
                xs = [str(d.get("x", "")) for d in rows]
                ys = [to_float(d.get("y", 0)) for d in rows]
            else:
                xs = list(range(len(rows)))
                ys = [to_float(v) for v in rows]
            series.append({"name": name, "xs": xs, "ys": ys})
        return series
    # 旧单系列格式
    if isinstance(first, dict) and "x" in first:
        xs = [str(d.get("x", "")) for d in data]
        ys = [to_float(d.get("y", 0)) for d in data]
    elif isinstance(first, (list, tuple)) and len(first) >= 2:
        xs = [str(r[0]) for r in data]
        ys = [to_float(r[1]) for r in data]
    else:
        xs = list(range(len(data)))
        ys = [to_float(x) for x in data]
    return [{"name": "数据", "xs": xs, "ys": ys}]


@tool(
        {
            "type": "function",
            "function": {
                "name": "chart_data",
                "description": "数据可视化：生成图表 PNG（matplotlib）。单系列 data 传 [x,y] 数组/对象数组/数值数组；多系列传 [{\"name\":\"A\",\"data\":[...]},...]；kind: line/bar/pie/scatter（pie 仅单系列）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "data": {"type": "array", "items": {}, "description": "数据：单系列 [[x,y],...] 或 [{\"x\":..,\"y\":..},...] 或 [数值,...]；多系列 [{\"name\":\"A\",\"data\":[数值 或 [x,y] 或 {\"x\":[],\"y\":[]}]},...]"},
                        "path": {"type": "string", "description": "输出 PNG 绝对路径"},
                        "kind": {"type": "string", "description": "可选：line/bar/pie/scatter（默认 line）"},
                        "title": {"type": "string", "description": "可选：图表标题"},
                        "x_label": {"type": "string", "description": "可选：X 轴标签"},
                        "y_label": {"type": "string", "description": "可选：Y 轴标签"},
                    },
                    "required": ["data", "path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='数据可视化图表（线/柱/饼/散点）',
    preactivate=(('图片', '图像', '截图', '看图', '图表', '视觉执行', '视觉闭环', '屏幕操作'), ('表格', 'excel', 'csv', '报表')),
)
def chart_data(data, path, kind="line", title="", x_label="", y_label=""):
    """数据可视化：生成图表 PNG（matplotlib）。单系列 data 为 [x1,x2,...] / [[x,y],...] /
    [{"x":..,"y":..}]；多系列为 [{"name":"A","data":[...]},...]。kind: line/bar/pie/scatter。"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        # C3: 系统字体探测（Windows/macOS/Linux 通用），避免中文变方块
        plt.rcParams["font.sans-serif"] = _chart_cjk_fonts()
        plt.rcParams["axes.unicode_minus"] = False
    except ImportError:
        return "错误：需要 matplotlib（pip install matplotlib）"
    if not path or not str(path).strip():
        return "错误：path 必填"
    if not isinstance(data, list) or not data:
        return "错误：data 必须是非空数组"
    p = permissions.resolve(path)
    if not p:
        return "错误：路径无效"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    try:
        series_list = _chart_parse_series(data)
        # NaN/inf 无法绘制：matplotlib 静默出空图，先挡掉
        for s in series_list:
            if any(v != v or v in (float("inf"), float("-inf")) for v in s["ys"]):
                return "错误：数据包含 NaN 或无穷值，请清洗后重试"
        k = str(kind or "line").lower()
        if k not in ("line", "bar", "pie", "scatter"):
            return f"错误：kind 非法：{kind}（支持 line/bar/pie/scatter）"
        multi = len(series_list) > 1
        if k == "pie":
            if multi:
                return "错误：饼图仅支持单系列数据，请合并为一份后重试"
            if len(series_list[0]["ys"]) > 20:
                return "错误：饼图最多支持 20 个数据点，请聚合后重试"
            if not any(v > 0 for v in series_list[0]["ys"]):
                return "错误：饼图需要至少一个正值数据"
        if _dc.CHART_THEME == "light":
            face = "#ffffff"
            grid = "#d5e4ec"
            tick = "#5c7a96"
            title_c = "#14283f"
            chart_bg = "#f5f9fc"
            palette = ["#00a3c8", "#ff7f50", "#9acd32", "#9370db", "#ffb800",
                       "#ff6b81", "#5cb85c", "#6c8ebf", "#e07020", "#b554c8"]
        else:
            face = "#0a101f"
            grid = "#14203a"
            tick = "#9db0d1"
            title_c = "#e9f1ff"
            chart_bg = "#0a101f"
            palette = ["#00d4ff", "#ff9e6d", "#b8e986", "#c9a8ff", "#ffd700",
                       "#ff8fa3", "#7ee07e", "#8fb8e8", "#f09a55", "#dd88f0"]
        fig, ax = plt.subplots(figsize=(8, 5), dpi=110, facecolor=face)
        ax.set_facecolor(chart_bg)
        for spine in ax.spines.values():
            spine.set_color(grid)
        ax.tick_params(colors=tick)
        ax.xaxis.label.set_color(tick)
        ax.yaxis.label.set_color(tick)
        ax.title.set_color(title_c)
        ax.grid(color=grid)
        total_pts = 0
        for idx, s in enumerate(series_list):
            color = palette[idx % len(palette)]
            xs, ys = s["xs"], s["ys"]
            total_pts += len(ys)
            if k == "bar":
                ax.bar(xs, ys, color=color, label=s["name"] if multi else None)
            elif k == "pie":
                ax.pie(ys, labels=xs, autopct="%1.1f%%", textprops={"color": title_c})
            elif k == "scatter":
                # 保留用户传入的 x 坐标（此前 range(len(ys)) 会把 x 丢弃成序号）
                ax.scatter(xs, ys, color=color, label=s["name"] if multi else None)
            else:
                ax.plot(xs, ys, color=color, marker="o", markersize=4, label=s["name"] if multi else None)
        if multi:
            ax.legend()
        if title:
            ax.set_title(str(title))
        if x_label:
            ax.set_xlabel(str(x_label))
        if y_label:
            ax.set_ylabel(str(y_label))
        fig.tight_layout()
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        fig.savefig(p)
        plt.close(fig)
        size = os.path.getsize(p) if os.path.exists(p) else 0
        return f"已生成图表至 {p}（{size} 字节，{k} 图，{len(series_list)} 系列，{total_pts} 个数据点）"
    except Exception as e:
        return f"错误：生成图表失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "database_query",
                "description": "对本地 SQLite 数据库执行只读查询（仅 SELECT/PRAGMA，最多 200 行），数据库文件须在允许目录内",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "db_path": {"type": "string", "description": "SQLite 数据库文件绝对路径"},
                        "sql": {"type": "string", "description": "只读 SQL 语句（SELECT / PRAGMA）"},
                        "max_rows": {"type": "integer", "description": "可选：返回行数上限（默认 20）"},
                    },
                    "required": ["db_path", "sql"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='SQLite 只读查询',
    preactivate=(('数据库', 'sql', 'mysql', 'postgres'),),
)
def database_query(db_path, sql, max_rows=20):
    """对本地 SQLite 数据库执行只读查询（SELECT/PRAGMA），路径需在允许目录内。"""
    if not db_path or not str(db_path).strip():
        return "错误：请提供数据库文件路径"
    if not sql or not str(sql).strip():
        return "错误：请提供 SQL 查询语句"
    ok, reason = permissions.check_filesystem(str(db_path), write=False)
    if not ok:
        return reason
    stmt = str(sql).strip()
    if not _readonly_stmt(stmt):
        return "错误：仅允许只读查询（SELECT / PRAGMA）"
    p = permissions.resolve(str(db_path))
    if not p or not os.path.exists(p):
        return f"错误：数据库文件不存在：{p}"
    try:
        import sqlite3
        import time as _t
        import urllib.parse

        # 路径含 ?/# 时按 URI 查询参数解析，需先 percent-encode
        conn = sqlite3.connect(
            f"file:{urllib.parse.quote(p)}?mode=ro", uri=True, timeout=5
        )
        try:
            # L3: SQL 层强制 LIMIT——仅 fetchmany 截断不够，无 LIMIT 的查询仍会全量执行
            limit = 20
            try:
                limit = clamp_int(max_rows, 20, lo=1, hi=200)
            except (TypeError, ValueError):
                pass
            stmt = force_limit(stmt, limit)
            # L3: 语句级超时（progress handler 每 500 条虚拟机指令检查一次，超时中断）
            _q_start = _t.monotonic()
            def _q_timeout():
                return 1 if _t.monotonic() - _q_start > _SQLITE_QUERY_TIMEOUT_S else 0
            conn.set_progress_handler(_q_timeout, 500)
            cur = conn.cursor()
            cur.execute(stmt)
            if cur.description is None:
                return "执行成功（无结果集）"
            cols = [d[0] for d in cur.description]
            rows = cur.fetchmany(limit)
            lines = [f"查询结果（{len(rows)} 行）:", " | ".join(str(c) for c in cols)]
            for r in rows:
                cells = ["" if v is None else str(v) for v in r]
                cells = [c[:_TABLE_CELL_MAX] + ("…" if len(c) > _TABLE_CELL_MAX else "") for c in cells]
                lines.append(" | ".join(cells))
            if len(rows) >= limit:
                lines.append("⚠ 已达行数上限，如需更多请缩小范围后分页查询")
            return "\n".join(lines)
        finally:
            try:
                conn.set_progress_handler(None, 0)
            except Exception:
                pass
            conn.close()
    except Exception as e:
        return f"错误：查询失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "database_execute",
                "description": "数据库写操作（UPDATE/INSERT/DELETE/DDL）。高危：变更前自动备份 + 审计；SQLite 的 connection 为数据库文件路径，mysql/postgres 用 db_config.json 的连接名",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "db_type": {"type": "string", "description": "sqlite / mysql / postgres"},
                        "connection": {"type": "string", "description": "sqlite=数据库文件绝对路径；mysql/postgres=连接名（默认 default）"},
                        "sql": {"type": "string", "description": "写操作 SQL 语句"},
                        "backup": {"type": "boolean", "description": "可选：变更前备份（默认 true）"},
                    },
                    "required": ["db_type", "sql", "connection"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='数据库写操作（SQLite/MySQL/PG，带审批）',
    preactivate=(('数据库写', '插入数据', '改数据库', '删除记录', 'update语句'),),
)
def database_execute(db_type="sqlite", connection="default", sql="", backup=True):
    """数据库写操作（UPDATE/INSERT/DELETE/DDL）。高危工具，走审批流 + 审计。
    db_type: sqlite / mysql / postgres；sqlite 的 connection 为数据库文件绝对路径。"""
    stmt = str(sql or "").strip()
    if not stmt:
        return "错误：sql 必填"
    if not stmt.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE")):
        return "错误：database_execute 仅用于写操作；只读查询请用 database_query*"
    # L4: 无 WHERE 的 UPDATE/DELETE 直接拒绝（防全表误操作；全表清空应分批带条件）
    if stmt.lstrip().upper().startswith(("UPDATE", "DELETE")) and not re.search(r"\bWHERE\b", stmt, re.I):
        return "错误：UPDATE/DELETE 必须带 WHERE 条件（防止全表误操作）；如需清空整表请分步删除并确认"
    dbtype = str(db_type or "sqlite").lower()
    try:
        if dbtype == "sqlite":
            return _db_execute_sqlite(str(connection or "").strip(), stmt, bool(backup))
        if dbtype == "mysql":
            return _db_execute_mysql(str(connection or "default"), stmt, bool(backup))
        if dbtype == "postgres":
            return _db_execute_postgres(str(connection or "default"), stmt, bool(backup))
        return f"错误：不支持的数据库类型：{db_type}（支持 sqlite / mysql / postgres）"
    except Exception as e:
        return f"错误：数据库执行失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "pdf_extract",
                "description": "从 PDF 提取文本（按页）、表格（Markdown 格式）或元数据；支持页码范围（如 1-5）。扫描件会提示改用 ocr_image",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "PDF 文件绝对路径（须在允许目录内）"},
                        "pages": {"type": "string", "description": "可选：页码范围，如 '1-5' / '3' / 'all'（默认 all）"},
                        "mode": {"type": "string", "description": "可选：text（文本，默认）/ table（表格）/ meta（元数据）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='提取 PDF 文本',
    preactivate=(('pdf', '转pdf', 'pdf提取', 'pdf生成', '读pdf'),),
)
def pdf_extract(path, pages="all", mode="text"):
    """从 PDF 提取文本（按页）/ 表格（Markdown）/ 元数据；支持页码范围与扫描件提示。"""
    if not str(path or "").strip():
        return "错误：path 必填"
    ok, reason = permissions.check_filesystem(path, write=False)
    if not ok:
        return reason
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：文件不存在：{path}"
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return "未安装 PyMuPDF，请先执行 pip_install PyMuPDF 后重试"
    m = str(mode or "text").lower()
    if m not in ("text", "table", "meta"):
        return f"错误：mode 仅支持 text / table / meta，收到：{mode}"
    try:
        doc = fitz.open(p)
        try:
            if doc.is_encrypted:
                return "PDF 已加密，暂不支持密码解密，请先移除密码后重试"
            total = doc.page_count
            if total == 0:
                return "PDF 为空文档"
            page_list = _parse_page_range(pages, total)
            if page_list is None:
                return f"错误：页码范围非法（应为 1-{total} 内逗号/连字符组合或 all）：{pages}"
            if m == "meta":
                md = doc.metadata or {}
                return "\n".join([
                    f"文件名: {os.path.basename(p)}",
                    f"页数: {total}",
                    f"标题: {md.get('title') or '（无）'}",
                    f"作者: {md.get('author') or '（无）'}",
                    f"主题: {md.get('subject') or '（无）'}",
                    f"创建时间: {md.get('creationDate') or '（无）'}",
                    f"修改时间: {md.get('modDate') or '（无）'}",
                    f"格式: {md.get('format') or '? '}",
                ])
            out = [f"文件名: {os.path.basename(p)}", f"页数: {total}"]
            out_len = sum(len(x) for x in out)
            truncated_hint = "\n[输出较长已截断，可用 pages= 指定页码范围分段提取]"
            if m == "text":
                for i in page_list:
                    page = doc.load_page(i - 1)
                    seg = f"\n--- 第{i}页 ---\n"
                    text = page.get_text("text").strip()
                    if not text:
                        seg += "（本页无文本层，疑似扫描件；可先用 web_screenshot/pdf 导出页面图片再用 ocr_image 识别）"
                    else:
                        seg += text
                    if out_len + len(seg) > PDF_EXTRACT_MAX_OUTPUT:
                        out.append(truncated_hint)
                        break
                    out.append(seg)
                    out_len += len(seg)
            else:  # table
                if not hasattr(doc.load_page(0), "find_tables"):
                    return "错误：当前 PyMuPDF 版本过低，表格提取需要 PyMuPDF 1.23+（可升级：pip_install PyMuPDF --upgrade，或改用 mode=text）"
                for i in page_list:
                    page = doc.load_page(i - 1)
                    seg_parts = [f"\n--- 第{i}页 表格 ---"]
                    try:
                        tables = page.find_tables()
                        found = False
                        for ti, tb in enumerate(tables.tables, 1):
                            data = tb.extract()
                            if not data:
                                continue
                            found = True
                            seg_parts.append(f"表格 {ti}:")
                            seg_parts.append(_table_to_md(data))
                        if not found:
                            seg_parts.append("（本页未检测到表格）")
                    except Exception:
                        seg_parts.append("（表格提取失败，可改用 mode=text 提取文本）")
                    seg = "\n".join(seg_parts)
                    if out_len + len(seg) > PDF_EXTRACT_MAX_OUTPUT:
                        out.append(truncated_hint)
                        break
                    out.append(seg)
                    out_len += len(seg)
            result = "\n".join(out)
            if len(result) > PDF_EXTRACT_MAX_OUTPUT:
                result = result[:PDF_EXTRACT_MAX_OUTPUT] + truncated_hint
            return result
        finally:
            doc.close()
    except Exception as e:
        return f"错误：PDF 读取失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "pdf_create",
                "description": "把文本或 Markdown 内容生成 PDF 文件（自动嵌入中文字体；支持标题/列表/代码块/表格排版）。长文档请分段生成",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "PDF 正文（文本或 Markdown），与 source_path 二选一"},
                        "source_path": {"type": "string", "description": "可选：从本地 md/txt 文件读取内容（与 content 二选一）"},
                        "output": {"type": "string", "description": "输出 PDF 绝对路径（须在允许目录内）"},
                        "title": {"type": "string", "description": "可选：文档标题（默认取内容首行）"},
                    },
                    "required": ["output"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='生成 PDF',
    preactivate=(('pdf', '转pdf', 'pdf提取', 'pdf生成', '读pdf'),),
)
def pdf_create(content="", source_path="", output="", title=""):
    """把文本/Markdown 内容生成 PDF（中文字体嵌入；支持标题/列表/代码块/表格；可选封面/页码/目录）。"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib import colors
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, Preformatted, Table, TableStyle,
        )
    except ImportError:
        return "未安装 reportlab，请先执行 pip_install reportlab 后重试"
    if not str(output or "").strip():
        return "错误：output 必填"
    if str(content or "").strip() and str(source_path or "").strip():
        return "错误：content 与 source_path 只能二选一"
    if not str(content or "").strip():
        if not str(source_path or "").strip():
            return "错误：content 或 source_path 必填"
        src = permissions.resolve(source_path)
        if not src or not os.path.isfile(src):
            return f"错误：源文件不存在：{source_path}"
        ok, reason = permissions.check_filesystem(src, write=False)
        if not ok:
            return reason
        try:
            with open(src, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(2_000_000)
        except Exception as e:
            return f"错误：读取源文件失败: {e}"
    if not str(content or "").strip():
        return "错误：内容为空"
    out = permissions.resolve(output)
    if not out:
        return "错误：输出路径无效"
    if not out.lower().endswith(".pdf"):
        out += ".pdf"
    ok, reason = permissions.check_filesystem(out, write=True)
    if not ok:
        return reason
    try:
        import mdparse  # 复用项目自有 Markdown 块解析（无第三方依赖）
    except Exception:
        return "错误：mdparse 不可用"
    try:
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        font = _register_cjk_font()
        mono = "Courier" if font == "Helvetica" else font
        styles = {
            "title": ParagraphStyle("title", fontName=font, fontSize=18, leading=24, spaceAfter=14),
            "h1": ParagraphStyle("h1", fontName=font, fontSize=16, leading=20, spaceBefore=10, spaceAfter=6),
            "h2": ParagraphStyle("h2", fontName=font, fontSize=14, leading=18, spaceBefore=8, spaceAfter=5),
            "h3": ParagraphStyle("h3", fontName=font, fontSize=13, leading=17, spaceBefore=6, spaceAfter=4),
            "h4": ParagraphStyle("h4", fontName=font, fontSize=12, leading=16, spaceBefore=5, spaceAfter=3),
            "h5": ParagraphStyle("h5", fontName=font, fontSize=11, leading=15, spaceBefore=4, spaceAfter=2),
            "h6": ParagraphStyle("h6", fontName=font, fontSize=10, leading=14, spaceBefore=4, spaceAfter=2),
            "body": ParagraphStyle("body", fontName=font, fontSize=10.5, leading=16, spaceAfter=6),
            "code": ParagraphStyle("code", fontName=mono, fontSize=8.5, leading=11.5,
                                   backColor=colors.Color(0.95, 0.95, 0.95), borderPadding=6,
                                   spaceBefore=4, spaceAfter=8),
        }
        flow = []
        # 标题：显式 title 优先；否则取内容首行作为文档元数据标题（PRD 规范）
        doc_title = str(title or "").strip()
        if not doc_title:
            first_line = next((ln.strip() for ln in str(content).splitlines() if ln.strip()), "")
            doc_title = first_line[:200]
        if str(title or "").strip():
            flow.append(Paragraph(_md_inline_html(title), styles["title"]))
        blocks = mdparse.parse_blocks(str(content))
        for blk in blocks:
            kind = blk[0]
            body = blk[1]
            if kind == "code":
                flow.append(Preformatted(body, styles["code"]))
            elif kind == "table":
                rows = _md_table_rows(body)
                if len(rows) >= 2:
                    t = Table(rows, repeatRows=1)
                    t.setStyle(TableStyle([
                        ("FONTNAME", (0, 0), (-1, 0), font),
                        ("FONTNAME", (0, 1), (-1, -1), font),
                        ("FONTSIZE", (0, 0), (-1, -1), 8),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.9, 0.93, 0.97)),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]))
                    flow.append(t)
                    flow.append(Spacer(1, 8))
            elif kind == "list":
                for ln in str(body).split("\n"):
                    if ln.strip():
                        flow.append(Paragraph("• " + _md_inline_html(ln), styles["body"]))
            elif kind.startswith("h"):
                level = int(kind[1])
                flow.append(Paragraph(_md_inline_html(body), styles[f"h{min(level, 6)}"]))
            else:
                flow.append(Paragraph(_md_inline_html(body), styles["body"]))
        if not flow:
            return "错误：内容未能解析为可排版元素"
        # P1.2：封面页 + 页脚"第 N 页 / 共 M 页" + 可点目录（multiBuild）
        from reportlab.lib.pagesizes import A4 as _A4
        from reportlab.pdfgen import canvas as _rcanvas
        from reportlab.platypus import PageBreak as _PageBreak
        from reportlab.platypus.tableofcontents import TableOfContents as _TOC

        class _NumberedCanvas(_rcanvas.Canvas):
            """两趟画布：末趟把「第 N 页 / 共 M 页」写进页脚，并画页眉标题/底线。"""
            def __init__(self, *args, **kw):
                super().__init__(*args, **kw)
                self._saved = []

            def showPage(self):
                self._saved.append(dict(self.__dict__))
                self._startPage()

            def save(self):
                total = len(self._saved)
                for state in self._saved:
                    self.__dict__.update(state)
                    self._draw_footer(total)
                    super().showPage()
                super().save()

            def _draw_footer(self, total):
                try:
                    self.saveState()
                    self.setFont(font, 8)
                    self.setFillColor(colors.Color(0.45, 0.45, 0.45))
                    pageno = getattr(self, "_pageNumber", 1)
                    self.drawRightString(_A4[0] - 30, 18, f"第 {pageno} 页 / 共 {total} 页")
                    if doc_title and pageno > 1:
                        self.drawString(30, _A4[1] - 24, str(doc_title)[:80])
                    self.setStrokeColor(colors.Color(0.85, 0.85, 0.85))
                    self.setLineWidth(0.5)
                    self.line(30, 22, _A4[0] - 30, 22)
                    self.restoreState()
                except Exception:
                    pass

        class _TocDoc(SimpleDocTemplate):
            """多趟 doc：拦截 H1/H2 段落写入目录，支持可点书签。"""
            def afterFlowable(self, flowable):
                try:
                    if isinstance(flowable, Paragraph):
                        sty = flowable.style.name if flowable.style else ""
                        if sty.startswith("h") and len(sty) == 2 and sty[1].isdigit():
                            lvl = int(sty[1])
                            if lvl <= 2:
                                txt = (flowable.getPlainText() or "").strip()
                                if txt:
                                    self.notify("TOCEntry", (lvl, txt, self.page))
                except Exception:
                    pass

        body_flow = flow  # 纯正文（含已有的 title 段，若显式给了 title 且走封面则去掉重复）
        _use_cover = bool(str(title or "").strip())
        heading_blocks = [b for b in blocks if b[0] in ("h1", "h2")]
        _use_toc = _use_cover and bool(heading_blocks)

        # 组装最终流：封面(可选) → 目录(可选) → 正文
        final_flow = []
        if _use_cover:
            final_flow.append(Spacer(1, _A4[1] * 0.26))
            final_flow.append(Paragraph(_md_inline_html(str(title)), ParagraphStyle(
                "cover", fontName=font, fontSize=26, leading=34, alignment=1, spaceAfter=14)))
            final_flow.append(Paragraph("WhaleTalk 自动生成", ParagraphStyle(
                "coverSub", fontName=font, fontSize=11, alignment=1,
                textColor=colors.Color(0.5, 0.5, 0.5))))
            final_flow.append(_PageBreak())
            # 去掉正文里因显式 title 而插入的重复标题段（保留首个换行）
            if body_flow and isinstance(body_flow[0], Paragraph):
                body_flow = body_flow[1:]
            if _use_toc:
                toc = _TOC()
                toc.levelStyles = [
                    ParagraphStyle("toc1", fontName=font, fontSize=11, leftIndent=0, spaceAfter=2),
                    ParagraphStyle("toc2", fontName=font, fontSize=10, leftIndent=14, spaceAfter=1),
                ]
                final_flow.append(Paragraph("目录", ParagraphStyle(
                    "tocTitle", fontName=font, fontSize=16, spaceAfter=8)))
                final_flow.append(toc)
                final_flow.append(_PageBreak())
        final_flow += body_flow

        _doc = _TocDoc(out, pagesize=A4,
                       leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=64,
                       title=doc_title, author="WhaleTalk")
        if _use_toc:
            _doc.multiBuild(final_flow, canvasmaker=_NumberedCanvas)
        else:
            _doc.build(final_flow, canvasmaker=_NumberedCanvas)
        size = os.path.getsize(out) if os.path.exists(out) else 0
        permissions.audit("pdf_create", out, f"{size} 字节")
        return f"已生成 PDF: {out}（{size / 1024:.1f} KB，中文字体 {'已嵌入' if font != 'Helvetica' else '未找到（可能乱码，请安装中文字体）'}）"
    except Exception as e:
        return f"错误：PDF 生成失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "docx_read",
                "description": "读取 Word .docx 文档为 Markdown 结构（标题层级/段落/列表/表格），旧版 .doc 会提示转换",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": ".docx 文件绝对路径"},
                        "max_chars": {"type": "integer", "description": "可选：输出字符上限（默认 50000，防超长文档撑爆上下文）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='读取 Word 文档',
    preactivate=(('word', 'docx', '读word', '读取文档'),),
)
def docx_read(path, max_chars=50000):
    """读取 Word .docx 为 Markdown 结构（标题/段落/列表/表格，保持文档顺序）。"""
    try:
        from docx import Document
    except ImportError:
        return "未安装 python-docx，请先执行 pip_install python-docx 后重试"
    if not str(path or "").strip():
        return "错误：path 必填"
    ok, reason = permissions.check_filesystem(path, write=False)
    if not ok:
        return reason
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：文件不存在：{path}"
    if p.lower().endswith(".doc"):
        return "错误：暂不支持旧版 .doc 格式，请先用 Word 另存为 .docx 后重试"
    try:
        limit = max(200, min(500000, int(max_chars or DOCX_MAX_DEFAULT)))
    except (TypeError, ValueError):
        limit = DOCX_MAX_DEFAULT
    try:
        from docx.table import Table as _Table
        from docx.text.paragraph import Paragraph as _Para

        doc = Document(p)
        parts = []
        img_count = 0
        from docx.oxml.ns import qn as _qn
        for child in doc.element.body.iterchildren():
            tag = child.tag.split("}")[-1]
            if tag == "p":
                para = _Para(child, doc)
                text = para.text.strip()
                # 统计段落内图片（w:drawing）
                drawings = child.findall(".//" + _qn("w:drawing"))
                if drawings:
                    img_count += len(drawings)
                if not text:
                    continue
                style = str(para.style.name or "")
                sl = style.lower()
                if sl.startswith("title"):
                    parts.append("# " + text)
                elif sl.startswith("heading"):
                    try:
                        level = int(style[-1])
                    except (TypeError, ValueError):
                        level = 1
                    parts.append("#" * min(level, 6) + " " + text)
                elif "list bullet" in sl:
                    parts.append("- " + text)
                elif "list number" in sl:
                    parts.append("1. " + text)
                else:
                    parts.append(text)
            elif tag == "tbl":
                table = _Table(child, doc)
                rows = [[c.text.strip() for c in r.cells] for r in table.rows]
                if rows and any(any(c for c in r) for r in rows):
                    parts.append(_table_to_md(rows))
        if not parts:
            return "（文档无可见内容）"
        result = "\n\n".join(parts)
        if img_count:
            result += f"\n\n[图片: 共 {img_count} 张（占位标注，未提取图片本身）]"
        if len(result) > limit:
            result = result[:limit] + f"\n[内容较长已截断前 {limit} 字符]"
        return result
    except Exception as e:
        return f"错误：Word 读取失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "docx_edit",
                "description": "就地编辑已有 .docx：action=replace 全文查找替换文本；action=insert 在含 anchor 的段落后插入新段落 text（可带 **加粗** 等行内）；action=append 在文档末尾追加段落 text。保留段落样式与其它内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": ".docx 文件绝对路径"},
                        "action": {"type": "string", "description": "replace / insert / append"},
                        "find": {"type": "string", "description": "replace 必填：要查找的文本（匹配含该文本的段落）"},
                        "replace": {"type": "string", "description": "replace 必填：替换为的文本"},
                        "anchor": {"type": "string", "description": "insert 必填：在其后插入的锚点文本"},
                        "text": {"type": "string", "description": "insert/append 必填：要插入/追加的文本（支持 **加粗** 等行内语法）"},
                    },
                    "required": ["path", "action"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='编辑 Word',
    preactivate=(('word', 'docx', '读word', '读取文档'),),
)
def docx_edit(path, action="", find="", replace="", anchor="", text=""):
    """就地编辑 .docx：replace 查找替换 / insert 段后插入 / append 末尾追加。保留段落样式。"""
    try:
        from docx import Document
    except ImportError:
        return "未安装 python-docx，请先执行 pip_install python-docx 后重试"
    if not str(path or "").strip():
        return "错误：path 必填"
    if action not in ("replace", "insert", "append"):
        return "错误：action 仅支持 replace / insert / append"
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：文件不存在：{path}"
    if not p.lower().endswith(".docx"):
        return "错误：仅支持 .docx"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    try:
        # 备份
        try:
            shutil.copy2(p, p + ".bak")
        except Exception:
            pass
        doc = Document(p)
        # 收集所有段落（正文 + 表格单元格），统一做替换
        all_paras = list(doc.paragraphs)
        for t in doc.tables:
            for row in t.rows:
                for cell in row.cells:
                    all_paras.extend(cell.paragraphs)
        if action == "replace":
            if not str(find or "").strip() or not str(replace or "").strip():
                return "错误：replace 需要 find 与 replace"
            hits = 0
            for para in all_paras:
                full = "".join(run.text for run in para.runs)
                if str(find) in full:
                    new_text = full.replace(str(find), str(replace))
                    # 清除原有 runs，重建（保留段落样式）
                    for run in list(para.runs):
                        run._r.getparent().remove(run._r)
                    _md_inline_to_runs(para, new_text)
                    hits += 1
            if hits == 0:
                return f"未找到包含「{find}」的段落"
            doc.save(p)
            permissions.audit("docx_edit", p, f"replace {hits} 处")
            return f"已替换 {hits} 处「{find}」→「{replace}」（原文件已备份 .bak）"
        elif action == "insert":
            if not str(anchor or "").strip() or not str(text or "").strip():
                return "错误：insert 需要 anchor 与 text"
            target = None
            for para in all_paras:
                if str(anchor) in "".join(r.text for r in para.runs):
                    target = para
                    break
            if target is None:
                return f"未找到锚点段落（含「{anchor}」）"
            # python-docx 无 insert_after；用 XML 在 target 之后插入克隆样式的段落
            from docx.text.paragraph import Paragraph as _Para2
            from docx.oxml.ns import qn as _qn2
            new_el = target._p.makeelement(_qn2("w:p"), {})
            target._p.addnext(new_el)
            new_para = _Para2(new_el, doc)
            _md_inline_to_runs(new_para, str(text))
            doc.save(p)
            permissions.audit("docx_edit", p, "insert after anchor")
            return f"已在「{anchor}」段后插入段落（原文件已备份 .bak）"
        else:  # append
            if not str(text or "").strip():
                return "错误：append 需要 text"
            para = doc.add_paragraph()
            _md_inline_to_runs(para, str(text))
            doc.save(p)
            permissions.audit("docx_edit", p, "append")
            return f"已在文档末尾追加段落（原文件已备份 .bak）"
    except Exception as e:
        return f"错误：Word 编辑失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "pptx_read",
                "description": "提取 PowerPoint .pptx 每页幻灯片的标题、正文要点与演讲者备注；图片以占位标注",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": ".pptx 文件绝对路径"},
                        "include_notes": {"type": "boolean", "description": "可选：是否包含演讲者备注（默认 true）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='读取 PPT',
    preactivate=(('ppt', 'pptx', '演示文稿', '读ppt'),),
)
def pptx_read(path, include_notes=True):
    """提取 PowerPoint 每页幻灯片的标题、正文要点与备注；图片占位标注。"""
    try:
        from pptx import Presentation
    except ImportError:
        return "未安装 python-pptx，请先执行 pip_install python-pptx 后重试"
    if not str(path or "").strip():
        return "错误：path 必填"
    ok, reason = permissions.check_filesystem(path, write=False)
    if not ok:
        return reason
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：文件不存在：{path}"
    if p.lower().endswith(".ppt"):
        return "错误：暂不支持旧版 .ppt 格式，请先用 PowerPoint 另存为 .pptx 后重试"
    try:
        prs = Presentation(p)
        out = [f"幻灯片数: {len(prs.slides)}"]

        def _walk_shapes(shapes, title_holder, body_lines, img_count, table_count, depth=0):
            """递归遍历形状（组合形状 GROUP 内文本/图片不丢），深度上限防畸形文件死循环。"""
            if depth > 10:
                return
            for shape in shapes:
                is_title_ph = (
                    getattr(shape, "is_placeholder", False)
                    and shape.placeholder_format.idx == 0
                    and shape.has_text_frame
                )
                if is_title_ph:
                    t = shape.text_frame.text.strip()
                    if t and not title_holder[0]:
                        title_holder[0] = t
                    continue
                if shape.shape_type == 6:  # MSO_SHAPE_TYPE.GROUP
                    if hasattr(shape, "shapes"):
                        _walk_shapes(shape.shapes, title_holder, body_lines, img_count, table_count, depth + 1)
                elif shape.has_text_frame:
                    t = shape.text_frame.text.strip()
                    if t:
                        body_lines.extend(ln for ln in t.splitlines() if ln.strip())
                elif getattr(shape, "has_table", False):
                    table_count[0] += 1
                    rows = [[c.text.strip() for c in row.cells] for row in shape.table.rows]
                    if rows:
                        body_lines.append(f"[表格 {table_count[0]}]")
                        body_lines.append(_table_to_md(rows))
                elif shape.shape_type == 13:  # MSO_SHAPE_TYPE.PICTURE
                    img_count[0] += 1

        for idx, slide in enumerate(prs.slides, 1):
            out.append(f"\n--- 第{idx}页 ---")
            title_holder = [None]
            body_lines = []
            img_count = [0]
            table_count = [0]
            _walk_shapes(slide.shapes, title_holder, body_lines, img_count, table_count)
            if title_holder[0]:
                out.append(f"标题: {title_holder[0]}")
            if body_lines:
                out.append("\n".join("- " + ln for ln in body_lines[:40]))
            elif not title_holder[0]:
                out.append("（本页无文本）")
            if img_count[0]:
                out.append(f"[图片占位: {img_count[0]} 张]")
            if include_notes and slide.has_notes_slide:
                try:
                    nt = slide.notes_slide.notes_text_frame.text.strip()
                    if nt:
                        out.append(f"备注: {nt[:500]}")
                except Exception:
                    pass
        result = "\n".join(out)
        # S14：全局输出上限（此前 pptx_read 无总长截断，超长 PPT 会整篇灌入上下文）
        if len(result) > PPTX_MAX_DEFAULT:
            result = result[:PPTX_MAX_DEFAULT] + f"\n[内容较长已截断前 {PPTX_MAX_DEFAULT} 字符]"
        return result
    except Exception as e:
        return f"错误：PPT 读取失败: {e}"


# ── S6: PPT 生成（补齐"只能读、不能生成"的最大空白）──────────────────
# 用 python-pptx 从结构化 slides 或 markdown 大纲生成 .pptx。
# 主题 = 标题/正文配色预设；模板 = 复用已有 .pptx 的母版/版式（保留品牌）。
# 中文字体：仅设 run.font.name 对中文无效，必须同时写 XML rPr 的 latin/ea 字体。
# P1：PPT 主题默认内嵌兜底；运行时可被 assets/templates/ppt_themes.json 扩展/覆盖
# （新增键即新增主题，无需改代码）。加载见 _load_ppt_themes()。
_PPTX_THEMES_FALLBACK = {
    "default": {"accent": "2B4C7E", "body": "333333"},
    "ocean": {"accent": "155E95", "body": "1F3B4D"},
    "dark": {"accent": "1F2430", "body": "EDEDED", "invert": True},
    "forest": {"accent": "2F5233", "body": "2C3A2C"},
}


def _load_ppt_themes():
    """合并 assets/templates/ppt_themes.json（可扩展）到内嵌兜底主题表。"""
    merged = {k: dict(v) for k, v in _PPTX_THEMES_FALLBACK.items()}
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "assets", "templates", "ppt_themes.json")
        if os.path.isfile(path):
            import json as _json
            with open(path, "r", encoding="utf-8") as f:
                ext = _json.load(f)
            for k, v in (ext or {}).items():
                if isinstance(v, dict) and not k.startswith("_"):
                    merged[str(k)] = dict(v)
    except Exception:
        pass
    return merged


_PPTX_THEMES = _load_ppt_themes()


def _pptx_cjk_run(run):
    """为 python-pptx run 设置中日韩字体：latin + ea 都需写，否则中文走默认字体。"""
    try:
        from pptx.oxml.ns import qn
    except Exception:
        return
    try:
        rPr = run._r.get_or_add_rPr()
        ea = rPr.find(qn("a:ea"))
        if ea is None:
            ea = rPr.makeelement(qn("a:ea"), {})
            rPr.append(ea)
        ea.set("typeface", "Microsoft YaHei")
        # latin 交给 python-pptx 的 font.name（同时设 latin typeface 以防未设）
        lat = rPr.find(qn("a:latin"))
        if lat is None:
            lat = rPr.makeelement(qn("a:latin"), {})
            rPr.append(lat)
        lat.set("typeface", "Arial")
    except Exception:
        pass


def _pptx_clean_inline(text):
    """清洗单行 markdown 行内标记 → 幻灯片纯文本要点。
    处理 **加粗**、*斜体*、`代码`、[文字](url)、![alt](url)（图保留 alt 作占位说明）、
    残留的 ##/— 等，避免把字面 md 标记写进幻灯片。"""
    t = str(text or "")
    t = t.replace("**", "").replace("__", "")
    # ![alt](url) 图占位 → 保留 alt 说明，去掉 URL 与 ![] 语法
    import re as _re
    t = _re.sub(r"!\[([^\]]*)\]\([^)]*\)", lambda m: (m.group(1).strip() or "图片"), t)
    t = _re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)  # [文字](url) → 文字
    t = _re.sub(r"`([^`]*)`", r"\1", t)              # `code` → code
    t = _re.sub(r"(\*|_)([^*_]+)\1", r"\2", t)        # *斜体* / _斜体_
    t = _re.sub(r"^#{1,6}\s*", "", t)                # 行首标题 #
    t = _re.sub(r"^\s*[-*+]\s+", "", t)              # 行首无序列表
    t = _re.sub(r"^\s*\d+[.)]\s+", "", t)             # 行首有序列表
    t = _re.sub(r"^\s*>+\s?", "", t)                  # 引用
    t = t.strip()
    return t


_IMG_PLACEHOLDER_RE = None  # 惰性构建

def _pptx_image_placeholder_label(line):
    """识别一行是否为图片占位标注，返回说明文字；非占位行返回 None。
    支持形态：`[图片：机器正面]`、`[图片: xx]`、`（图片：xx）`、`图片：xx`、`![xx](url)`、
    `[图]`/`[示意图]` 等。这些行不再写进正文，改为画真实占位框。"""
    global _IMG_PLACEHOLDER_RE
    s = str(line or "").strip()
    if not s:
        return None
    if _IMG_PLACEHOLDER_RE is None:
        import re as _r
        _IMG_PLACEHOLDER_RE = _r.compile(
            r"^\s*(?:\[(?:图片|图|配图|示意图|插图|照片|示例图)\s*[:：]?\s*([^\]]*)\]|"
            r"(?:图片|图|配图|示意图|插图|照片)[：:]\s*([^\n]+)|"
            r"!\[([^\]]*)\]\([^)]*\)|"
            r"\[(?:图片|图|配图|示意图|插图|照片)\])\s*$"
        )
    m = _IMG_PLACEHOLDER_RE.match(s)
    if not m:
        return None
    # 取非空捕获组
    label = next((g for g in m.groups() if g and g.strip()), "")
    return label.strip() or "图片"


def _add_image_placeholder_boxes(slide, labels, body_top_in):
    """在幻灯片底部画一组虚线圆角占位框 + 居中说明（图片位置预留的可见呈现）。
    body_top_in：正文区域顶端英寸（从正文下方开始排框）。"""
    from pptx.util import Inches as _In, Pt as _Pt
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.enum.text import PP_ALIGN
    from pptx.dml.color import RGBColor
    try:
        labels = [l for l in labels if l]
        if not labels:
            return
        left, width = 0.55, 9.0
        top = max(body_top_in + 0.15, 1.35)
        box_h = 1.15
        if len(labels) > 1:
            # 多图占位：并排两列
            per_row = 2
            rows = (len(labels) + per_row - 1) // per_row
            box_h = 0.95
        accent = "9BB7D4"
        for i, lab in enumerate(labels):
            row = i // 2
            col = i % 2
            if len(labels) == 1:
                x, w = left, width
            else:
                w = (width - 0.3) / 2
                x = left + col * (w + 0.3)
            y = top + row * (box_h + 0.18)
            # 若超出可用高度则截断（最多排到页底）
            if y + box_h > 5.6:
                break
            try:
                shp = slide.shapes.add_shape(
                    MSO_SHAPE.ROUNDED_RECTANGLE, _In(x), _In(y), _In(w), _In(box_h))
                shp.fill.solid()
                shp.fill.fore_color.rgb = RGBColor(0xF4, 0xF8, 0xFC)
                shp.line.color.rgb = RGBColor.from_string(accent)
                shp.line.width = _Pt(1.25)
                # 虚线
                try:
                    shp.line.dash_style = 2  # MSO_LINE_DASH_STYLE.DASH
                except Exception:
                    pass
                tf = shp.text_frame
                tf.word_wrap = True
                tf.margin_top = _Pt(4); tf.margin_bottom = _Pt(4)
                from pptx.enum.text import PP_ALIGN as _A
                p = tf.paragraphs[0]
                p.alignment = _A.CENTER
                r = p.add_run()
                r.text = f"🖼 图片占位：{lab}"
                r.font.size = _Pt(12)
                r.font.color.rgb = RGBColor.from_string("6B7A90")
                _pptx_cjk_run(r)
                # 居中纵向
                try:
                    from pptx.oxml.ns import qn as _qn
                    tf.paragraphs[0].alignment = _A.CENTER
                    bodyPr = tf._txBody.find(_qn("a:bodyPr"))
                    if bodyPr is not None:
                        bodyPr.set("anchor", "ctr")
                except Exception:
                    pass
            except Exception:
                pass
    except Exception:
        pass


def _pptx_text_frame_slides_body(tf, lines, body_color):
    """把 body 行写入文本占位符；以缩进表达层级（- 子项 / -- 孙项）。
    每行先做 markdown 行内清洗，避免字面 md 标记（##/**/[..]）写进幻灯片。"""
    from pptx.util import Pt
    body_color = body_color or "333333"
    first = True
    for ln in lines:
        if not str(ln).strip():
            continue
        raw = str(ln)
        indent = 0
        while raw.startswith(("  ", "\t")):
            raw = raw[1:].lstrip(" ")
            indent += 1
        # 去掉列表符号后提取层级；若开头是 # 标题或图占位则 indent=0 当主点
        cleaned = _pptx_clean_inline(raw)
        if not cleaned:
            continue
        # 以首行是否图片占位/无列表符判断是否为子层级（保持既有 2 空格缩进语义）
        if cleaned.startswith(("[图片", "（图片", "图片：")):
            indent = 0  # 图片占位作为页内独立标注，顶格
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.level = min(indent, 4)
        try:
            p.font.size = Pt(18 if indent == 0 else 15)
            if body_color:
                p.font.color.rgb = _pptx_rgb(body_color)
        except Exception:
            pass
        r = p.add_run()
        r.text = cleaned
        _pptx_cjk_run(r)


def _pptx_rgb(hexcolor):
    from pptx.dml.color import RGBColor
    return RGBColor.from_string(str(hexcolor or "333333").lstrip("#"))


@tool(
        {
            "type": "function",
            "function": {
                "name": "pptx_create",
                "description": "生成 PowerPoint .pptx 演示文稿。slides 传 JSON 数组，每页含 title（标题）、body（要点文本，支持 - 子项层级）或 bullets 数组、可选 notes（演讲者备注）、table={headers,rows}、image（本地图片路径）。或传 markdown outline（按 # 章节自动分页）生成。theme 内置 default/ocean/dark/forest；template 可传 .pptx 作为母版。中文字体自动嵌入",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "输出 .pptx 绝对路径（须在允许目录内）"},
                        "slides": {"type": "array", "items": {}, "description": "可选：页面数组，每页 {\"title\":\"..\",\"body\":\"要点多行\"或\"bullets\":[..],\"notes\":\"..\",\"table\":{\"headers\":[..],\"rows\":[[..]]},\"image\":\"绝对路径\"}"},
                        "outline": {"type": "string", "description": "可选：markdown 大纲，一级标题(#)分页"},
                        "theme": {"type": "string", "description": "可选：default / ocean / dark / forest（默认 default）"},
                        "title": {"type": "string", "description": "可选：演示标题（生成首页标题页）"},
                        "template": {"type": "string", "description": "可选：作为母版的 .pptx 绝对路径"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='生成 PPT',
    preactivate=(('ppt', 'pptx', '演示文稿', '读ppt'),),
)
def pptx_create(path, slides=None, outline="", theme="default", title="", template=""):
    """生成 PPT .pptx：slides 数组或 markdown outline；可选 theme/template。"""
    try:
        from pptx import Presentation
    except ImportError:
        return "未安装 python-pptx，请先执行 pip_install python-pptx 后重试"
    if not str(path or "").strip():
        return "错误：path 必填"
    if slides is not None and outline:
        return "错误：slides 与 outline 只能二选一"
    if slides is None and not str(outline or "").strip():
        return "错误：slides 或 outline 必填"
    p = permissions.resolve(path)
    if not p:
        return "错误：输出路径无效"
    if not p.lower().endswith(".pptx"):
        p += ".pptx"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    tpl = ""
    if str(template or "").strip():
        tpl = permissions.resolve(template)
        if not tpl or not os.path.isfile(tpl):
            return f"错误：模板文件不存在：{template}"
    theme = str(theme or "default").strip().lower()
    th = _PPTX_THEMES.get(theme, _PPTX_THEMES["default"])
    try:
        # 解析页面数据
        if isinstance(slides, list) and slides:
            pages = slides
        elif str(outline or "").strip():
            pages = _parse_outline_to_slides(outline)
        else:
            return "错误：slides 为空"
        if not pages:
            return "错误：无有效页面内容"
        # 打开或新建演示
        if tpl:
            prs = Presentation(tpl)
        else:
            prs = Presentation()
            prs.slide_width = prs.slide_width  # 保持默认 16:9（python-pptx 默认即 16:9）
        # 标题页
        need_cover = bool(str(title or "").strip())
        if need_cover:
            _add_title_slide(prs, title, th)
        # 内容页
        added = 0
        for i, page in enumerate(pages):
            if isinstance(page, dict):
                _add_content_slide(prs, page, th)
                added += 1
            else:
                _add_content_slide(prs, {"title": f"第{i + 1}页", "body": str(page)}, th)
        if added == 0 and not need_cover:
            return "错误：未能生成任何内容页"
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        prs.save(p)
        size = os.path.getsize(p)
        permissions.audit("pptx_create", p, f"{len(pages)} 页 {size} 字节")
        return f"已生成 PPT: {p}（{len(pages) + (1 if need_cover else 0)} 页，{size / 1024:.1f} KB）"
    except Exception as e:
        return f"错误：PPT 生成失败: {e}"


def _parse_outline_to_slides(outline):
    """markdown 大纲 → 页面：一级标题(#)开新页，其下正文为该页要点；支持 ### 作为子主题。"""
    pages = []
    cur = None
    for ln in str(outline).splitlines():
        s = ln.rstrip()
        if not s.strip():
            continue
        if s.startswith("## "):
            pages.append(cur)
            cur = {"title": s[3:].strip(), "bullets": []}
        elif s.startswith("### ") and cur is not None:
            cur["bullets"].append("  - " + s[4:].strip())
        elif s.startswith("# "):
            if cur is not None:
                pages.append(cur)
            cur = {"title": s[2:].strip(), "bullets": []}
        else:
            body = s.lstrip("- ").strip()
            if cur is None:
                cur = {"title": "概述", "bullets": []}
            if body:
                cur["bullets"].append(s)
    if cur is not None:
        pages.append(cur)
    return [pg for pg in pages if pg]


def _add_title_slide(prs, title, th):
    from pptx.util import Pt
    try:
        layout = prs.slide_layouts[0]  # Title Slide
    except Exception:
        layout = prs.slide_layouts[6]
    slide = prs.slides.add_slide(layout)
    for ph in slide.placeholders:
        if ph.placeholder_format.idx == 0:
            ph.text = str(title)
            for para in ph.text_frame.paragraphs:
                for run in para.runs:
                    _pptx_cjk_run(run)
                    try:
                        run.font.size = Pt(40)
                        run.font.bold = True
                        run.font.color.rgb = _pptx_rgb(th.get("accent", "2B4C7E"))
                    except Exception:
                        pass
        elif ph.placeholder_format.idx == 1:
            try:
                ph.text = ""
            except Exception:
                pass


def _add_content_slide(prs, page, th):
    from pptx.util import Pt
    body = None
    # body 来源：bullets 数组或 body 字符串（每行一要点）
    raw_lines = []
    if page.get("bullets"):
        raw_lines = [str(b) for b in page["bullets"] if str(b).strip()]
    elif page.get("body"):
        raw_lines = [ln for ln in str(page["body"]).splitlines() if ln.strip()]
    # 拆出图片占位行（画真实占位框）与正文行
    lines = []
    img_labels = []
    for _ln in raw_lines:
        _lab = _pptx_image_placeholder_label(_ln)
        if _lab:
            img_labels.append(_lab)
        elif str(_ln).strip():
            lines.append(_ln)
    has_table = isinstance(page.get("table"), dict) and page["table"].get("rows")
    has_image = str(page.get("image") or "").strip() and os.path.isfile(str(page["image"]).strip())
    # 有真实图片则不再画占位框
    if has_image:
        img_labels = []
    # 版式选择：含表格优先用 Title+Content（可腾出下方空间），否则也尽量用 content
    try:
        layout = prs.slide_layouts[1]  # Title and Content
    except Exception:
        layout = prs.slide_layouts[5]  # Title Only
    slide = prs.slides.add_slide(layout)
    title_set = False
    for ph in slide.placeholders:
        idx = ph.placeholder_format.idx
        if idx == 0 and page.get("title"):
            ph.text = str(page["title"])
            title_set = True
            for para in ph.text_frame.paragraphs:
                for run in para.runs:
                    _pptx_cjk_run(run)
                    try:
                        run.font.bold = True
                        run.font.color.rgb = _pptx_rgb(th.get("accent", "2B4C7E"))
                    except Exception:
                        pass
        elif idx == 1 and hasattr(ph, "text_frame"):
            body = ph
    if not title_set and page.get("title"):
        # 无标题占位符的版式：用文本框补
        from pptx.util import Inches, Pt as _Pt
        tb = slide.shapes.add_textbox(Inches(0.4), Inches(0.2), Inches(9), Inches(0.7))
        tb.text = str(page["title"])
        for para in tb.text_frame.paragraphs:
            for run in para.runs:
                run.font.bold = True
                run.font.size = _Pt(28)
                _pptx_cjk_run(run)
    # 写入正文 / 表格 / 图片
    if has_table:
        _write_slide_table(slide, page["table"], th, body)
        if lines and body is not None:
            _pptx_text_frame_slides_body(body.text_frame, lines, th.get("body", "333333"))
    elif has_image:
        try:
            from pptx.util import Inches as _In
            img = str(page["image"]).strip()
            if body is not None:
                body.text_frame.text = ""
            slide.shapes.add_picture(img, _In(0.6), _In(1.4), width=_In(5))
            if lines and body is not None:
                _pptx_text_frame_slides_body(body.text_frame, lines, th.get("body", "333333"))
        except Exception:
            pass
    elif lines:
        if body is not None:
            _pptx_text_frame_slides_body(body.text_frame, lines, th.get("body", "333333"))
        else:
            # 无正文占位符：加一个文本框（例如 title-only 版式）
            try:
                from pptx.util import Inches as _In2
                tb2 = slide.shapes.add_textbox(_In2(0.4), _In2(1.2), _In2(9), _In2(5))
                _pptx_text_frame_slides_body(tb2.text_frame, lines, th.get("body", "333333"))
            except Exception:
                pass
    # 图片占位：把 [图片：说明] 标注画成虚线占位框（真实图片位置预留的可见呈现）
    if img_labels and not has_image and not has_table:
        # 正文行数估算占位框起始高度（约每行 0.32 英寸，标题下 0.9 起）
        est = 1.0 + len(lines) * 0.34
        _add_image_placeholder_boxes(slide, img_labels, body_top_in=max(est, 1.2))
    # 备注
    if page.get("notes"):
        try:
            slide.notes_slide.notes_text_frame.text = str(page["notes"])[:1000]
        except Exception:
            pass


def _write_slide_table(slide, table_spec, th, body_ph):
    from pptx.util import Inches as _In, Pt as _Pt
    from pptx.oxml.ns import qn as _qn
    headers = list(table_spec.get("headers") or [])
    rows = table_spec.get("rows") or []
    if headers and rows:
        data = [headers] + [[str(c) for c in r] for r in rows]
    else:
        data = [[str(c) for c in r] for r in rows] or [[]]
    nrow = len(data)
    ncol = max((len(r) for r in data), default=1)
    try:
        gt = slide.shapes.add_table(nrow, ncol, _In(0.5), _In(1.4), _In(9), _In(0.4 + 0.4 * nrow)).table
        for i, r in enumerate(data):
            for j in range(ncol):
                cell = gt.cell(i, j)
                cell.text = r[j] if j < len(r) else ""
                for para in cell.text_frame.paragraphs:
                    for run in para.runs:
                        _pptx_cjk_run(run)
                        try:
                            run.font.size = _Pt(12)
                            if i == 0:
                                run.font.bold = True
                                run.font.color.rgb = _pptx_rgb("FFFFFF")
                        except Exception:
                            pass
        # 表头底色
        try:
            from pptx.dml.color import RGBColor as _RC
            from pptx.oxml.ns import qn as _q2
            accent = str(th.get("accent", "2B4C7E")).lstrip("#")
            for j in range(ncol):
                c = gt.cell(0, j)
                tcPr = c._tc.get_or_add_tcPr()
                tcPr.set("fill", "1")
                from lxml import etree as _et
                solid = tcPr.find(_q2("a:solidFill"))
                if solid is None:
                    solid = _et.SubElement(tcPr, _q2("a:solidFill"))
                clr = solid.find(_q2("a:srgbClr"))
                if clr is None:
                    clr = _et.SubElement(solid, _q2("a:srgbClr"))
                clr.set("val", accent)
        except Exception:
            pass
        if body_ph is not None:
            try:
                body_ph.text_frame.text = ""
            except Exception:
                pass
    except Exception:
        pass


@tool(
        {
            "type": "function",
            "function": {
                "name": "secret_store",
                "description": "密钥保险箱：DPAPI 加密托管 API key/令牌等敏感值。action=set 保存（value 只写不显示）/ get 按名取用 / delete 删除 / list 仅列出名称",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "set / get / delete / list"},
                        "name": {"type": "string", "description": "密钥名称（如 openai_key）"},
                        "value": {"type": "string", "description": "set 必填：要托管的敏感值"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='加密密钥存储',
    preactivate=(('密钥', 'api key', '令牌', '保险箱', '托管密码'),),
)
def secret_store(action="get", name="", value=""):
    """密钥保险箱：set / get / delete / list。value 只写不显示；list 只返回名称。"""
    act = str(action or "get").strip().lower()
    name = str(name or "").strip()
    data = _load_secrets()
    if act == "set":
        if not name:
            return "错误：name 必填"
        if not str(value or ""):
            return "错误：value 必填"
        data[name] = str(value)
        if not _save_secrets(data):
            return "错误：保存密钥失败"
        permissions.audit("secret_set", name, "值已加密保存", result="ok")
        return f"密钥「{name}」已加密保存（调用 secret_get(name) 取用）"
    if act == "get":
        if not name:
            return "错误：name 必填"
        if name not in data:
            return f"未找到密钥「{name}」"
        return str(data[name])
    if act == "delete":
        if not name:
            return "错误：name 必填"
        if name not in data:
            return f"未找到密钥「{name}」"
        data.pop(name, None)
        _save_secrets(data)
        permissions.audit("secret_delete", name, "删除")
        return f"已删除密钥「{name}」"
    if act == "list":
        return "已保存密钥：" + ("、".join(sorted(data.keys())) if data else "（空）")
    return "错误：action 仅支持 set / get / delete / list"


@tool(
        {
            "type": "function",
            "function": {
                "name": "kv_store",
                "description": "嵌入式键值存储：set 写入（可选 TTL 过期）/ get 读取 / delete 删除 / keys 列出全部 / search 按键或值模糊检索。适合缓存、配置、轻量状态（Redis 的零部署替代）",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "set / get / delete / keys / search"},
                        "key": {"type": "string", "description": "set/get/delete 必填：键"},
                        "value": {"type": "string", "description": "set 必填：值（上限 1MB）"},
                        "ttl_seconds": {"type": "integer", "description": "可选：set 时有效秒数（0=长期）"},
                        "pattern": {"type": "string", "description": "search 必填：键或值的模糊检索关键词"},
                    },
                    "required": ["action"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='轻量键值存储（缓存/状态）',
    preactivate=(('键值', 'kv存储', '缓存读写', '轻量状态'),),
)
def kv_store(action="get", key="", value="", pattern="", ttl_seconds=0):
    """嵌入式键值存储：set（可选 TTL）/ get / delete / keys / search。"""
    act = str(action or "get").strip().lower()
    if act not in ("set", "get", "delete", "keys", "search"):
        return "错误：action 仅支持 set / get / delete / keys / search"
    try:
        import diskcache
    except ImportError:
        return "未安装 diskcache，请先执行 pip_install diskcache 后重试"
    if not _dc.KV_CACHE_DIR:
        return "错误：KV 存储未初始化"
    try:
        os.makedirs(_dc.KV_CACHE_DIR, exist_ok=True)
    except Exception:
        return "错误：KV 目录创建失败"
    try:
        with diskcache.Cache(_dc.KV_CACHE_DIR) as cache:
            if act == "set":
                k = str(key or "").strip()
                if not k:
                    return "错误：set 需要 key"
                if len(k) > 256:
                    return "错误：key 过长（上限 256 字符）"
                # 区分 None 与 0/False：None 存空串，0/False 保留字面值
                v = str(value if value is not None else "")
                if len(v.encode("utf-8", "replace")) > KV_VALUE_MAX_BYTES:
                    return "错误：value 超过 1MB 上限"
                try:
                    ttl = max(0, min(365 * 24 * 3600, int(ttl_seconds or 0)))
                except (TypeError, ValueError):
                    ttl = 0
                cache.set(k, v, expire=ttl or None)
                n = len(cache)
                ttl_txt = f"TTL {ttl}s" if ttl else "长期有效"
                return f"已写入 key={k}（{ttl_txt}，当前共 {n} 个键）"
            if act == "get":
                k = str(key or "").strip()
                if not k:
                    return "错误：get 需要 key"
                v = cache.get(k)
                if v is None:
                    return f"key={k} 不存在或已过期"
                return f"key={k}: {v}"
            if act == "delete":
                k = str(key or "").strip()
                if not k:
                    return "错误：delete 需要 key"
                if k in cache:
                    del cache[k]
                    return f"已删除 key={k}"
                return f"key={k} 不存在"
            if act == "keys":
                keys = [k for k in cache.iterkeys() if not k.startswith("_")]
                if not keys:
                    return "KV 存储为空"
                lines = [f"共 {len(keys)} 个键："]
                for i, k in enumerate(sorted(keys), 1):
                    try:
                        v = cache.get(k) or ""
                        lines.append(f"{i}. {k} = {str(v)[:80]}")
                    except Exception:
                        lines.append(f"{i}. {k} = ?")
                return "\n".join(lines)
            # search
            pat = str(pattern or "").strip().lower()
            if not pat:
                return "错误：search 需要 pattern"
            hits = []
            for k in cache.iterkeys():
                if k.startswith("_"):
                    continue
                try:
                    v = str(cache.get(k) or "")
                except Exception:
                    continue
                if pat in k.lower() or pat in v.lower():
                    hits.append(f"{k}: {v[:120]}")
                    if len(hits) >= 50:
                        break
            if not hits:
                return f"未找到包含「{pattern}」的键或值"
            return f"命中 {len(hits)} 项：\n" + "\n".join(hits)
    except Exception as e:
        return f"错误：KV 操作失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "create_doc",
                "description": "创建文档。.md/.html 原生支持；.docx 由 python-docx 渲染 Markdown 富文本（标题层级/加粗/斜体/行内代码/链接/有序与无序列表/表格/代码块/引用/水平线），中文字体自动处理。docx 可选 style 预设（default/formal/blueprint，控制标题色/正文字体字号）。注意：不支持 .pptx/.pdf——PPT 请用 pptx_create，PDF 请用 pdf_create",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文档绝对路径"},
                        "content": {"type": "string", "description": "文档内容（Markdown 或 HTML 文本）"},
                        "doc_type": {"type": "string", "description": "md / html / docx，默认按扩展名推断"},
                        "style": {"type": "string", "description": "docx 可选：样式预设 default/formal/blueprint（默认 default）"},
                    },
                    "required": ["path", "content"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='创建文档（md/html/docx）',
    preactivate=(('写', '保存', '创建', '生成'),),
)
def create_doc(path, content, doc_type="", style="default"):
    """创建文档：.md/.html 原生；.docx 用 markdown→Word 富文本（标题/加粗/列表/表格/代码/图片）。
    style 为 docx 样式预设名（default/formal/blueprint 等，可扩展 assets/templates/docx_styles.json）。"""
    if not str(path or "").strip():
        return "错误：path 必填"
    ok, reason = permissions.check_filesystem(path, write=True)
    if not ok:
        return reason
    p = permissions.resolve(path)
    ext = (doc_type or "").lower() or os.path.splitext(p)[1].lstrip(".").lower()
    if ext not in ("md", "html", "htm", "docx"):
        return (
            f"错误：create_doc 不支持 .{ext or '(无扩展名)'} 格式。"
            "支持 md / html / docx。若需生成 .pptx 请用 pptx_create（或 run_python 写 python-pptx 脚本），"
            "若需生成 PDF 请使用 pdf_create 工具。"
        )
    if ext == "htm":
        ext = "html"
    try:
        if ext == "docx":
            created = not os.path.exists(p)
            if not created:
                try:
                    shutil.copy2(p, p + ".bak")
                except Exception:
                    pass
            _build_docx_markdown(p, content or "", style_name=str(style or "default"))
        else:
            body = content or ""
            if ext == "html" and not body.lstrip().startswith("<"):
                body = (
                    "<!DOCTYPE html><html lang='zh'><head><meta charset='utf-8'>"
                    "<title>鲸语文档</title></head><body><pre>"
                    + body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                    + "</pre></body></html>"
                )
            created, _ = _atomic_write(p, body)
        permissions.audit("create_doc", p, f"{ext} {len(content or '')} 字符")
        return f"已创建文档 {p}（{'新建' if created else '覆盖并备份 .bak'}，{ext} 格式）"
    except Exception as e:
        return f"错误：文档创建失败: {e}"


# ── S7: markdown → docx 富文本渲染辅助 ─────────────────────────────────
_INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`|\[[^\]\n]+]\([^)\s]+\))")

def _md_inline_to_runs(paragraph, text):
    """把 markdown 行内语法写到 docx paragraph 上：**bold** / *italic* / `code` / [t](url)。"""
    def emit(seg, bold=False, italic=False, code=False, url=None):
        if not seg:
            return
        if url:
            # 真超链接：w:hyperlink 包裹 run（带 r:id 关系）
            _add_docx_hyperlink(paragraph, seg, url, bold=bold, italic=italic)
            return
        run = paragraph.add_run(seg)
        run.bold = bold
        run.italic = italic
        if code:
            run.font.name = "Consolas"
            try:
                rPr = run._element.get_or_add_rPr()
                from docx.oxml.ns import qn
                rFonts = rPr.find(qn("w:rFonts"))
                if rFonts is None:
                    from docx.oxml import OxmlElement
                    rFonts = OxmlElement("w:rFonts")
                    rPr.append(rFonts)
                rFonts.set(qn("w:ascii"), "Consolas")
                rFonts.set(qn("w:hAnsi"), "Consolas")
                rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
            except Exception:
                pass
        _set_run_cjk(run)
    # 分割加粗/斜体/代码/链接
    pos = 0
    for m in _INLINE_RE.finditer(str(text or "")):
        if m.start() > pos:
            emit(text[pos:m.start()])
        tok = m.group(1)
        if tok.startswith("**") and tok.endswith("**") and len(tok) > 4:
            emit(tok[2:-2], bold=True)
        elif tok.startswith("*") and tok.endswith("*") and len(tok) > 2:
            emit(tok[1:-1], italic=True)
        elif tok.startswith("`") and tok.endswith("`"):
            emit(tok[1:-1], code=True)
        elif tok.startswith("[") and "](" in tok:
            seg, _, url = tok[1:].partition("](")
            emit(seg, url=url.rstrip(")"))
        pos = m.end()
    if pos < len(str(text or "")):
        emit(text[pos:])
    return paragraph


def _add_docx_hyperlink(paragraph, text, url, bold=False, italic=False):
    """在 docx paragraph 上追加一个可点击超链接 run。"""
    try:
        from docx.opc.constants import RELATIONSHIP_TYPE
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import RGBColor
        part = paragraph.part
        r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), r_id)
        new_run = OxmlElement("w:r")
        rPr = OxmlElement("w:rPr")
        # 样式：蓝色 + 下划线
        c = OxmlElement("w:color")
        c.set(qn("w:val"), "0563C1")
        rPr.append(c)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rPr.append(u)
        if bold:
            b = OxmlElement("w:b")
            rPr.append(b)
        if italic:
            i = OxmlElement("w:i")
            rPr.append(i)
        new_run.append(rPr)
        t = OxmlElement("w:t")
        t.text = text
        new_run.append(t)
        hyperlink.append(new_run)
        paragraph._p.append(hyperlink)
        _set_run_cjk_xml(new_run)
    except Exception:
        # 降级：无关系挂接，仅下划线视觉链接
        run = paragraph.add_run(text)
        run.underline = True
        _set_run_cjk(run)


def _set_run_cjk_xml(run_el):
    """给 w:r XML 元素补 rFonts eastAsia（超链接 run 无法走 python-docx run 对象）。"""
    try:
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        rPr = run_el.find(qn("w:rPr"))
        if rPr is None:
            rPr = OxmlElement("w:rPr")
            run_el.insert(0, rPr)
        rf = rPr.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts")
            rPr.insert(0, rf)
        rf.set(qn("w:eastAsia"), "Microsoft YaHei")
    except Exception:
        pass


def _set_run_cjk(run):
    """docx run 设置中日韩字体（eastAsia），否则中文走默认西文字体易变方块。"""
    try:
        from docx.oxml.ns import qn
        rPr = run._element.get_or_add_rPr()
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is None:
            from docx.oxml import OxmlElement
            rFonts = OxmlElement("w:rFonts")
            rPr.insert(0, rFonts)
        rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        if run.font.name is None:
            rFonts.set(qn("w:ascii"), "Calibri")
            rFonts.set(qn("w:hAnsi"), "Calibri")
    except Exception:
        pass


def _load_docx_styles():
    """加载 docx 样式预设（assets/templates/docx_styles.json，可扩展），缺省回退 default。"""
    _fallback = {
        "title_color": "2B4C7E", "body_font": "Microsoft YaHei", "body_size": 11,
        "heading_font": "Microsoft YaHei",
        "heading_color_level": ["17365D", "2B4C7E", "3A6EA5", "5B7B9A"],
    }
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "assets", "templates", "docx_styles.json")
        if os.path.isfile(path):
            import json as _json
            with open(path, "r", encoding="utf-8") as f:
                data = _json.load(f)
            return dict(data)
    except Exception:
        pass
    return {"default": _fallback}


def _build_docx_markdown(out_path, content, style_name="default"):
    """把 Markdown 渲染为富 .docx（标题/加粗/列表/表格/代码/引用/图片）。style_name 取自 docx_styles.json。"""
    from docx import Document
    from docx.shared import Pt, RGBColor
    try:
        import mdparse
    except Exception:
        mdparse = None
    doc = Document()
    # P1：应用样式预设（正文字体/字号、标题色）
    presets = _load_docx_styles()
    preset = presets.get(str(style_name or "default")) or presets.get("default") or {}
    body_font = str(preset.get("body_font") or "Microsoft YaHei")
    body_size = float(preset.get("body_size") or 11)
    title_color = str(preset.get("title_color") or "2B4C7E")
    heading_font = str(preset.get("heading_font") or body_font)
    hcolors = preset.get("heading_color_level") or []
    try:
        from docx.oxml.ns import qn
        style = doc.styles["Normal"]
        style.font.name = "Calibri"
        style.font.size = Pt(body_size)
        style.element.rPr.rFonts.set(qn("w:eastAsia"), body_font)
    except Exception:
        pass

    def add_para(text="", style=None):
        return doc.add_paragraph(text, style=style)

    blocks = mdparse.parse_blocks(content) if mdparse else _fallback_blocks(content)
    for blk in blocks:
        kind = blk[0]
        body = blk[1]
        try:
            if kind == "code":
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Pt(18)
                p.paragraph_format.space_after = Pt(6)
                for line in str(body).split("\n"):
                    r = p.add_run((line if not p.runs else "\n" + line))
                    r.font.name = "Consolas"
                    r.font.size = Pt(9.5)
                    _set_run_cjk(r)
                # 浅灰底纹（单段浅色）
                try:
                    from docx.oxml import OxmlElement
                    from docx.oxml.ns import qn as _qn
                    shd = OxmlElement("w:shd")
                    shd.set(_qn("w:val"), "clear")
                    shd.set(_qn("w:fill"), "F2F2F2")
                    p._p.get_or_add_pPr().append(shd)
                except Exception:
                    pass
            elif kind == "table":
                rows = _md_table_rows(body)
                if len(rows) >= 1:
                    ncol = max(len(r) for r in rows)
                    t = doc.add_table(rows=len(rows), cols=ncol)
                    t.style = "Light Grid Accent 1"
                    for i, r in enumerate(rows):
                        for j in range(ncol):
                            cell = t.cell(i, j)
                            cell.text = ""
                            para = cell.paragraphs[0]
                            _md_inline_to_runs(para, r[j] if j < len(r) else "")
                            if i == 0:
                                for run in para.runs:
                                    run.bold = True
            elif kind == "list":
                ordered = False
                for ln in str(body).split("\n"):
                    s = ln.strip()
                    if not s:
                        continue
                    if s[:1].isdigit() and s[1:2] in (".", ")"):
                        ordered = True
                for ln in str(body).split("\n"):
                    s = ln.strip()
                    if not s:
                        continue
                    is_ordered = s[:1].isdigit() and s[1:2] in (".", ")")
                    style_name = "List Number" if (ordered or is_ordered) else "List Bullet"
                    if is_ordered:
                        s = s.split(".", 1)[1] if "." in s[:4] else s
                    elif s.startswith("- "):
                        s = s[2:]
                    elif s.startswith("* "):
                        s = s[2:]
                    p = doc.add_paragraph(style=style_name)
                    _md_inline_to_runs(p, s)
            elif kind.startswith("h"):
                try:
                    level = min(int(kind[1]), 4)
                except ValueError:
                    level = 1
                htext = str(body).strip()
                p = doc.add_heading("", level=level)
                _md_inline_to_runs(p, htext)
                # P1：标题配色（取自样式预设 heading_color_level / title_color）
                try:
                    hc = None
                    if hcolors and level <= len(hcolors):
                        hc = str(hcolors[level - 1])
                    if not hc:
                        hc = title_color
                    hc = hc.lstrip("#")
                    for run in p.runs:
                        run.font.color.rgb = RGBColor.from_string(hc)
                        run.font.name = heading_font
                        _set_run_cjk(run)
                except Exception:
                    pass
            elif kind == "quote":
                for ln in str(body).split("\n"):
                    p = doc.add_paragraph()
                    p.paragraph_format.left_indent = Pt(18)
                    _md_inline_to_runs(p, ln)
                    for run in p.runs:
                        run.italic = True
            elif kind == "hr":
                p = doc.add_paragraph()
                p.add_run("――――――――――――")
            else:  # plain
                for para_txt in str(body).split("\n"):
                    if not para_txt.strip():
                        continue
                    img_path = _docx_image_path_from_line(para_txt)
                    if img_path is not None:
                        _docx_add_picture(doc, img_path)
                        continue
                    p = doc.add_paragraph()
                    _md_inline_to_runs(p, para_txt)
        except Exception:
            # 单块失败不中断整文档（降级为纯文本行）
            try:
                doc.add_paragraph(str(body))
            except Exception:
                pass
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    doc.save(out_path)


def _docx_image_path_from_line(line):
    """从 markdown 图片行 `![alt](path)` 提取本地图片路径；非图片行返回 None。"""
    s = str(line or "").strip()
    if not (s.startswith("![") and "](" in s):
        return None
    alt_end = s.find("](")
    rest = s[alt_end + 2:]
    url = rest.split(")")[0].strip()
    if not url:
        return None
    url = url.split(" ")[0]
    if url.startswith(("http://", "https://", "data:")):
        return None  # 远程/内联不支持，跳过（docx 需本地文件）
    return url


def _docx_add_picture(doc, path):
    """把本地图片插入 docx 末尾；失败静默（路径无效/格式不支持时降级为占位文本）。"""
    try:
        p = permissions.resolve(path)
        if p and os.path.isfile(p):
            from docx.shared import Inches
            doc.add_picture(p, width=Inches(5.5))
            return
        doc.add_paragraph(f"[图片未能插入: {path}]")
    except Exception:
        try:
            doc.add_paragraph(f"[图片未能插入: {path}]")
        except Exception:
            pass


def _fallback_blocks(content):
    """mdparse 不可用时：纯文本退化为逐行 plain 块，保底可生成。"""
    return [("plain", ln) for ln in str(content or "").splitlines() if ln.strip()]


__all__ = ['database_query_mysql', 'database_query_postgres', 'read_excel', 'epub_read', 'mobi_read', 'doc_read', 'msg_read', 'archive_list', 'write_excel', 'xlsx_edit', 'chart_data', 'database_query', 'database_execute', 'pdf_extract', 'pdf_create', 'docx_read', 'docx_edit', 'pptx_read', 'pptx_create', 'secret_store', 'kv_store', 'create_doc']
