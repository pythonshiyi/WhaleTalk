# -*- coding: utf-8 -*-
"""📊 数据与文档 —— 首批拆分工具域（P0-1 巨石拆分）。

共享符号策略：permissions / db_utils 为独立模块（无循环依赖），顶层直接
import；函数体内按需导入标准库（csv 等）保持原样。
"""

import itertools
import os

from toolkit import tool  # noqa: F401  # 装饰器 + 工具名 re-export
import permissions
from db_utils import TABLE_CELL_MAX as _TABLE_CELL_MAX


@tool(
        {
            "type": "function",
            "function": {
                "name": "read_csv",
                "description": "读取 CSV 文件（允许目录内），返回表格文本，可指定分隔符与行数上限",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "CSV 文件绝对路径"},
                        "max_rows": {"type": "integer", "description": "可选：最多返回行数（默认 100）"},
                        "delimiter": {"type": "string", "description": "可选：分隔符（默认逗号）"},
                    },
                    "required": ["path"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='读 CSV',
    preactivate=(('表格', 'excel', 'csv', '报表'),),
)
def read_csv(path, max_rows=100, delimiter=","):
    """读取 CSV 文件（允许目录内），返回表格文本，可指定分隔符与行数上限。"""
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
        limit = max(1, min(500, int(max_rows or 100)))
    except (TypeError, ValueError):
        limit = 100
    try:
        delim = str(delimiter or ",")
        if len(delim) != 1:
            return "错误：delimiter 必须是单个字符（如 , ; | \\t）"
        if delim == "\\t":
            delim = "\t"
        with open(p, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            rows = list(itertools.islice(_csv.reader(f, delimiter=delim), limit))
        if not rows:
            return "（空文件）"
        # 列宽截断：超宽单元格（minified JSON/长 URL）会撑爆上下文，单格限 100 字符
        rows = [
            [str(c)[:_TABLE_CELL_MAX] + ("…" if len(str(c)) > _TABLE_CELL_MAX else "") for c in r]
            for r in rows
        ]
        widths = []
        for c in range(max(len(r) for r in rows)):
            widths.append(max(len(r[c]) if c < len(r) else 0 for r in rows))
        lines = []
        for r in rows:
            cells = [r[c].ljust(widths[c]) if c < len(r) else "" for c in range(len(widths))]
            lines.append(" | ".join(cells))
        total = "…" if len(rows) >= limit else ""
        return "\n".join(lines) + (f"\n[前 {limit} 行{total}]" if len(rows) >= limit else "")
    except Exception as e:
        return f"错误：读取 CSV 失败: {e}"


@tool(
        {
            "type": "function",
            "function": {
                "name": "write_csv",
                "description": "写入 CSV 文件。rows 传 JSON 数组：[[v,v],...] 或 [{\"列\":值},...]",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "输出文件绝对路径"},
                        "rows": {"type": "array", "items": {}, "description": "数据行（数组的数组，或对象数组）"},
                        "headers": {"type": "string", "description": "可选：表头，逗号分隔"},
                    },
                    "required": ["path", "rows"],
                },
            },
        },
    groups=['📊 数据与文档'],
    phrases='写 CSV',
    preactivate=(('写csv', '导出csv', '存成csv'),),
)
def write_csv(path, rows, headers=""):
    """写入 CSV 文件。rows 为 JSON 数组：[[v,v],...] 或 [{"col":v},...]；headers 逗号分隔。"""
    if not path or not str(path).strip():
        return "错误：path 必填"
    if not isinstance(rows, list):
        return "错误：rows 必须是非空数组"
    p = permissions.resolve(path)
    if not p:
        return "错误：路径无效"
    ok, reason = permissions.check_filesystem(p, write=True)
    if not ok:
        return reason
    import csv as _csv

    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
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
        with open(p, "w", encoding="utf-8-sig", newline="") as f:
            w = _csv.writer(f)
            if cols:
                w.writerow(cols)
            w.writerows(data)
        return f"已写入 CSV 至 {p}（{len(data)} 行）"
    except Exception as e:
        return f"错误：写入 CSV 失败: {e}"
