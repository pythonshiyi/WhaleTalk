"""设计/图表/PDF 工具纯函数回归：Markdown→HTML、ECharts option、页范围、颜色、设计自检。"""
import deepseek_client as dc  # noqa: F401  # 保证工具注册顺序
from agent_tools import tool_docs as td


def test_md_to_html_headings_lists_code():
    h = td._md_to_html("# 标题\n\n- a\n- b\n\n```py\nprint(1)\n```")
    assert "<h1>标题</h1>" in h
    assert "<ul>" in h and "<li>a</li>" in h
    assert "<pre><code>" in h and "print(1)" in h


def test_md_to_html_table_and_image():
    h = td._md_to_html("| A | B |\n|---|---|\n| 1 | 2 |\n\n![x](p.png)")
    assert "<table>" in h and "<th>A</th>" in h and "<td>1</td>" in h
    assert '<img src="p.png" alt="x">' in h


def test_md_to_html_escapes_html():
    h = td._md_to_html("a <script> b")
    assert "&lt;script&gt;" in h and "<script>" not in h


def test_chart_option_line_category():
    opt = td._chart_option([1, 2, 3], ["a", "b", "c"], None, "line", "t", "x", "y", "")
    assert opt["xAxis"]["type"] == "category"
    assert opt["xAxis"]["data"] == ["a", "b", "c"]
    assert opt["series"][0]["data"] == [1, 2, 3]
    assert opt["title"]["text"] == "t"


def test_chart_option_pie_has_no_axes():
    opt = td._chart_option([1, 2], ["a", "b"], None, "pie", "", "", "", "")
    assert "xAxis" not in opt and "yAxis" not in opt
    assert opt["series"][0]["type"] == "pie"


def test_parse_page_ranges():
    assert td._parse_page_ranges("1-3,5", 6) == [1, 2, 3, 5]
    assert td._parse_page_ranges("9", 3) == []
    assert td._parse_page_ranges("2-10", 4) == [2, 3, 4]


def test_hex_to_rgb01():
    assert td._hex_to_rgb01("#ff0000") == (1.0, 0.0, 0.0)
    assert td._hex_to_rgb01("#000") == (0.0, 0.0, 0.0)
    assert td._hex_to_rgb01("bogus") == (0.5, 0.5, 0.5)


def test_design_lint_flags_missing_alt_and_many_colors():
    html = ("<html><head></head><body><img src='a.png'>"
            + "".join(f"<span style='color:#{i}{i}{i}'>x</span>" for i in "1234567")
            + "</body></html>")
    rep = td._design_lint_html(html)
    assert "缺 alt" in rep
    assert "主色调数量" in rep
    assert "字号层级" in rep


def test_md_to_html_ordered_list_and_quote():
    h = td._md_to_html("1. 甲\n2. 乙\n\n> 引用")
    assert "<ol>" in h and "<li>甲</li>" in h
    assert "<blockquote>引用</blockquote>" in h
