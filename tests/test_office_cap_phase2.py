# -*- coding: utf-8 -*-
"""阶段二（文档/PPT 能力补齐）回归：S6 pptx_create + S7 markdown→docx 富文本。

覆盖报告《文档表格PPT能力报告_v3.8.5.md》阶段二核心项：
- S6 pptx_create：从 slides 数组或 markdown outline 生成 .pptx（封面/正文/子项
  层级/表格/备注/主题/模板），并完成六层注册 + dc 命名空间导出
- S7 create_doc 的 docx 分支：markdown → Word 富文本（标题样式/加粗/列表/表格/
  代码块/引用/行内），替换原"逐行纯文本段落"
"""
import os

import deepseek_client as dc


def _perm(tmp_path):
    import permissions
    permissions.WORKSPACE_DIR = str(tmp_path)


def _reg(name):
    names = [t["function"]["name"] for t in dc.TOOLS]
    assert name in names, f"{name} 必须注册进 TOOLS"
    assert hasattr(dc, name) and callable(getattr(dc, name)), f"{name} 必须经 dc 导出"
    assert name in dc.TOOL_CALL_MAP, f"{name} 必须在 CALL_MAP"


def test_s6_pptx_create_registered_and_slides(tmp_path):
    _perm(tmp_path)
    _reg("pptx_create")
    from pptx import Presentation
    out = str(tmp_path / "deck.pptx")
    slides = [
        {"title": "项目汇报", "bullets": ["- 完成阶段一", "  - S1 白名单", "- 阶段二"], "notes": "讲3分钟"},
        {"title": "数据", "table": {"headers": ["指标", "值"], "rows": [["营收", "100"], ["毛利", "40"]]}},
    ]
    r = dc.pptx_create(path=out, slides=slides, theme="ocean", title="2026汇报")
    assert r.startswith("已生成"), r
    prs = Presentation(out)
    assert sum(1 for _ in prs.slides) == 3, "封面 + 2 内容页"
    alltxt = ""
    for s in prs.slides:
        for sh in s.shapes:
            if sh.has_text_frame:
                alltxt += sh.text_frame.text + "\n"
    assert "2026汇报" in alltxt and "项目汇报" in alltxt
    assert "S1 白名单" in alltxt, "子项要点应写入"
    has_tbl = any(getattr(sh, "has_table", False) for s in prs.slides for sh in s.shapes)
    assert has_tbl, "表格应写入"


def test_s6_pptx_create_outline_and_validation(tmp_path):
    _perm(tmp_path)
    from pptx import Presentation
    out = str(tmp_path / "o.pptx")
    r = dc.pptx_create(path=out, outline="# 一\n要点\n\n# 二\n- 甲\n- 乙")
    assert r.startswith("已生成"), r
    assert sum(1 for _ in Presentation(out).slides) == 2, "两个一级标题 = 两页"
    # slides 与 outline 都为空应拒绝；都传也应拒绝
    assert "必填" in dc.pptx_create(path=str(tmp_path / "x.pptx"))
    assert "只能二选一" in dc.pptx_create(path=str(tmp_path / "y.pptx"), slides=[{"title": "a"}], outline="# b")


def test_s7_create_doc_docx_rich_markdown(tmp_path):
    _perm(tmp_path)
    dp = str(tmp_path / "rich.docx")
    md = (
        "# 标题\n\n"
        "这是**加粗**和*斜体*及`代码`。\n\n"
        "- 项目一\n- 项目二\n\n"
        "1. 有序一\n2. 有序二\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "```\nprint('hi')\n```\n\n> 引用行\n"
    )
    r = dc.create_doc(dp, md)
    assert r.startswith("已创建"), r
    from docx import Document
    doc = Document(dp)
    heads = [p.text for p in doc.paragraphs if p.style.name.startswith("Heading")]
    assert heads and "标题" in heads[0], "标题应转为 Word 标题样式"
    assert any(rn.bold for p in doc.paragraphs if "加粗" in p.text for rn in p.runs), "加粗应生效"
    assert len(doc.tables) == 1 and doc.tables[0].cell(1, 0).text == "1", "表格应写入"
    styles = {p.style.name for p in doc.paragraphs}
    assert "List Bullet" in styles and "List Number" in styles, "列表样式应应用"
    assert any("print" in p.text for p in doc.paragraphs), "代码块应写入"
