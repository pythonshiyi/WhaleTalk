# -*- coding: utf-8 -*-
"""📊 数据与文档 —— 首批拆分工具域（P0-1 巨石拆分）。

共享符号策略：permissions / db_utils 为独立模块（无循环依赖），顶层直接
import；函数体内按需导入标准库（csv 等）保持原样。
"""

import itertools
import os

from shared import clamp_int  # D4: 参数校验辅助
from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import permissions
from db_utils import table_to_md as _table_to_md  # 统一 markdown 表格渲染（含 | 转义）


# GBK/GB18030 回退链：国内 Excel「另存为 CSV」默认 GBK/ANSI，纯 utf-8 读取会整片乱码。
# 探测顺序：utf-8-sig → utf-8 → gb18030（GBK 超集，含全部中文）→ latin-1(errors=replace) 兜底。
def _csv_read_text(path):
    """以编码回退链读取 CSV 文本，返回 (text, used_encoding)。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return f.read(), enc
        except UnicodeDecodeError:
            continue
    with open(path, "r", encoding="latin-1", errors="replace", newline="") as f:
        return f.read(), "latin-1"


@tool(
        {
            "type": "function",
            "function": {
                "name": "read_csv",
                "description": "读取 CSV 文件，返回 markdown 表格（首行默认作表头，has_header=false 时自动补占位表头）；自动识别 utf-8/gbk 编码；可指定分隔符与行数上限",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "CSV 文件绝对路径"},
                        "max_rows": {"type": "integer", "description": "可选：最多返回行数（默认 100）"},
                        "delimiter": {"type": "string", "description": "可选：分隔符（默认逗号）"},
                        "has_header": {"type": "boolean", "description": "可选：首行是否为表头（默认 true；false 时补占位列名 col_1..col_n）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='读 CSV',
    preactivate=(('表格', 'excel', 'csv', '报表'),),
)
def read_csv(path, max_rows=100, delimiter=",", has_header=True):
    """读取 CSV 文件，返回 markdown 表格。首行默认作表头；has_header=false 时补占位表头。"""
    if not path or not str(path).strip():
        return "错误：path 必填"
    p = permissions.resolve(path)
    if not p or not os.path.isfile(p):
        return f"错误：文件不存在：{p}"
    ok, reason = permissions.check_filesystem(p, write=False)
    if not ok:
        return reason
    import csv as _csv

    try:
        limit = clamp_int(max_rows, 100, lo=1, hi=500)
    except (TypeError, ValueError):
        limit = 100
    try:
        delim = str(delimiter or ",")
        if len(delim) != 1:
            return "错误：delimiter 必须是单个字符（如 , ; | \\t）"
        if delim == "\\t":
            delim = "\t"
        text, used_enc = _csv_read_text(path)
        rows = list(itertools.islice(_csv.reader(text.splitlines(), delimiter=delim), limit))
        if not rows:
            return "（空文件）"
        if len(rows) >= limit:
            truncated = True
            rows = rows[:limit]
        else:
            truncated = False
        # 列宽截断交由 _table_to_md 统一处理（含单元格 | 转义）
        if not bool(has_header):
            # 无表头：补占位列名 col_1..col_n，使首行可作为 markdown 表头
            ncols = max(len(r) for r in rows) if rows else 1
            header = [f"col_{i}" for i in range(1, ncols + 1)]
            body = rows
        else:
            header, body = rows[0], rows[1:]
        md = _table_to_md([header] + body) if body else _table_to_md([header])
        tail = f"[前 {limit} 行…]" if truncated else ""
        enc_note = f"[编码: {used_enc}]" if used_enc != "utf-8-sig" else ""
        return f"{md}\n[共 {len(rows)} 行{('（' + tail + '）') if tail else ''}{enc_note}]"
    except Exception as e:
        return f"错误：读取 CSV 失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "write_csv",
                "description": "写入/追加 CSV 文件。rows 传 JSON 数组：[[v,v],...] 或 [{\"列\":值},...]；mode=append 追加到已有文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "输出文件绝对路径"},
                        "rows": {"type": "array", "items": {}, "description": "数据行（数组的数组，或对象数组）"},
                        "headers": {"type": "string", "description": "可选：表头，逗号分隔"},
                        "mode": {"type": "string", "description": "可选：overwrite 覆盖（默认）/ append 追加到已有文件末尾"},
                    },
                    "required": ["path", "rows"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='写 CSV',
    preactivate=(('写csv', '导出csv', '存成csv'),),
)
def write_csv(path, rows, headers="", mode="overwrite"):
    """写入 CSV 文件。rows 为 JSON 数组：[[v,v],...] 或 [{"col":v},...]；headers 逗号分隔。
    mode=append 追加到已有文件（不重复写表头）。"""
    if not path or not str(path).strip():
        return "错误：path 必填"
    if not isinstance(rows, list):
        return "错误：rows 必须是非空数组"
    if mode not in ("overwrite", "append"):
        return "错误：mode 仅支持 overwrite（覆盖）/ append（追加）"
    p = permissions.resolve(path)
    if not p:
        return "错误：路径无效"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    import csv as _csv

    try:
        if mode == "append" and not os.path.isfile(p):
            return f"错误：追加模式要求文件已存在：{p}"
        if rows and isinstance(rows[0], dict):
            cols = [h.strip() for h in str(headers or "").split(",") if h.strip()] or list(rows[0].keys())
            # 混合 dict/非 dict 行健壮化：非 dict 行按空字典处理（防中间夹杂标量崩溃）
            data = [
                [row.get(c, "") for c in cols] if isinstance(row, dict) else [""] * len(cols)
                for row in rows
            ]
        else:
            cols = [h.strip() for h in str(headers or "").split(",") if h.strip()]
            data = [
                [str(x) for x in row] if isinstance(row, (list, tuple)) else [str(row)]
                for row in rows
            ]
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        # utf-8-sig：写入 BOM，Excel 打开中文不乱码；追加时沿用原编码策略
        is_append = mode == "append" and os.path.isfile(p)
        with open(p, "a" if is_append else "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.writer(f)
            if not is_append:
                # 覆盖模式：全新文件，写表头
                if cols:
                    w.writerow(cols)
            elif cols:
                # 追加且文件已存在：检查是否已含表头（首行非空即视为已有内容），避免重复表头
                try:
                    probe = _csv.reader(text_io := open(p, "r", encoding="utf-8-sig", newline=""))
                    first = next(probe, None)
                    text_io.close()
                except Exception:
                    first = None
                if first is None:
                    w.writerow(cols)
            w.writerows(data)
        verb = "追加" if is_append else "写入"
        return f"已{verb} CSV 至 {p}（{len(data)} 行）"
    except Exception as e:
        return f"错误：写入 CSV 失败: {e}"
