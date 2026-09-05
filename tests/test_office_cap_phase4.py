# -*- coding: utf-8 -*-
"""P0-P1 前端/预览改造的后端支撑回归：
- api_server._file_preview 对 docx/pptx 返回结构化 markdown 内容（原为 content 空）
- xlsx 预览保留原生数值类型（供前端可编辑表格回写维持类型）
- pdf_create 封面/目录/总页码（P1.2）
"""
import os

import api_server
import deepseek_client as dc


def _boot(tmp_path):
    import permissions
    permissions.init(str(tmp_path / "p.json"), str(tmp_path), audit_dir=None)
    permissions.set_audit_enabled(False)
    permissions.WORKSPACE_DIR = str(tmp_path)


def test_preview_docx_returns_markdown(tmp_path):
    _boot(tmp_path)
    p = str(tmp_path / "a.docx")
    dc.create_doc(p, "# 标题\n\n正文段。\n\n- 列表项\n\n| A | B |\n|---|---|\n| 1 | 2 |")
    res, err = api_server._file_preview(p)
    assert err is None and res
    assert res.get("kind") == "doc" and res.get("docx")
    assert res.get("content"), "docx 预览应返回结构化 markdown（非空）"
    assert "#" in res["content"] and "列表项" in res["content"]


def test_preview_pptx_returns_markdown(tmp_path):
    _boot(tmp_path)
    p = str(tmp_path / "b.pptx")
    dc.pptx_create(path=p, slides=[{"title": "演示", "bullets": ["- 要点A", "- 要点B"]}])
    res, err = api_server._file_preview(p)
    assert err is None and res
    assert res.get("kind") == "doc" and res.get("pptx")
    assert res.get("content") and "演示" in res["content"]


def test_preview_xlsx_preserves_numeric_type(tmp_path):
    _boot(tmp_path)
    from openpyxl import Workbook, load_workbook
    p = str(tmp_path / "n.xlsx")
    wb = Workbook(); ws = wb.active
    ws.append(["名称", "数值"]); ws.append(["甲", 100]); ws.append(["乙", 80])
    wb.save(p)
    res, err = api_server._file_preview(p)
    assert err is None
    assert res.get("kind") == "table"
    # rows[0] 对应数据首行（甲,100），数值应保留为 int 而非 "100"
    first = res["rows"][0]
    assert first[0] == "甲"
    assert first[1] == 100 and isinstance(first[1], int), f"数值应保留类型，实际 {first[1]!r}"


def test_pdf_cover_and_toc_and_total_pages(tmp_path):
    _boot(tmp_path)
    import pymupdf
    p = str(tmp_path / "c.pdf")
    md = "# 第一章\n甲。\n\n# 第二章\n乙。\n\n# 第三章\n丙。"
    r = dc.pdf_create(content=md, output=p, title="年度汇报")
    assert r.startswith("已生成"), r
    doc = pymupdf.open(p)
    assert doc.page_count >= 2, "封面 + 内容应多页"
    assert "年度汇报" in doc[0].get_text(), "封面含标题"
    texts = "".join(doc[i].get_text() for i in range(doc.page_count))
    assert "共" in texts and "页" in texts, "页脚应有总页数"
    doc.close()
