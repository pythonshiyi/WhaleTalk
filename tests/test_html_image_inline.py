# -*- coding: utf-8 -*-
"""HTML 本地图片内联回归：修复 data: URI 载入导致相对图片加载失败的问题。

覆盖 agent_tools.tool_docs._inline_local_images / _prep_html_doc：
- 相对路径按 base_dir 解析并内联为 data URI
- 绝对路径同样内联
- http(s)/data/# 等外链不处理
- 不存在的本地文件保持原样
- CSS url() 同样内联
- 片段会自动补全为完整文档
"""
import os

import deepseek_client as dc  # noqa: F401  # 保证工具注册顺序
from agent_tools import tool_docs as td


def _mk_img(tmp_path, name="pic.png", data=b"\x89PNG\r\n\x1a\nFAKE"):
    p = os.path.join(str(tmp_path), name)
    with open(p, "wb") as f:
        f.write(data)
    return p


def test_relative_src_inlined(tmp_path):
    _mk_img(tmp_path)
    html = '<img src="pic.png" alt="x">'
    out = td._inline_local_images(html, str(tmp_path))
    assert "data:image/png;base64," in out
    assert 'src="pic.png"' not in out


def test_absolute_src_inlined(tmp_path):
    p = _mk_img(tmp_path)
    html = f'<img src="{p}">'
    out = td._inline_local_images(html, str(tmp_path))
    assert "data:image/png;base64," in out


def test_remote_and_data_untouched(tmp_path):
    _mk_img(tmp_path)
    html = ('<img src="http://a.com/x.png">'
            '<img src="https://a.com/x.png">'
            '<img src="data:image/png;base64,AAAA">'
            '<a href="#top">t</a>')
    out = td._inline_local_images(html, str(tmp_path))
    assert out == html


def test_missing_local_untouched(tmp_path):
    html = '<img src="nope.png">'
    out = td._inline_local_images(html, str(tmp_path))
    assert out == html


def test_css_url_inlined(tmp_path):
    _mk_img(tmp_path, "bg.jpg")
    html = '<div style="background-image:url(bg.jpg)"></div>'
    out = td._inline_local_images(html, str(tmp_path))
    assert "data:image/jpeg;base64," in out


def test_non_image_src_not_inlined(tmp_path):
    # 非图片扩展名（如 css/js）不应被内联，避免误改样式表引用
    p = os.path.join(str(tmp_path), "style.css")
    with open(p, "w", encoding="utf-8") as f:
        f.write("body{}")
    html = '<link href="style.css" rel="stylesheet">'
    out = td._inline_local_images(html, str(tmp_path))
    assert out == html


def test_prep_html_wraps_fragment(tmp_path):
    out = td._prep_html_doc('<div>hi</div>', str(tmp_path))
    assert out.lstrip().lower().startswith("<!doctype")
    assert "<div>hi</div>" in out


def test_prep_html_empty():
    assert td._prep_html_doc("", None) == ""


def test_inject_head_before_close():
    out = td._inject_head(
        "<!DOCTYPE html><html><head><title>t</title></head><body></body></html>",
        "<style>x</style>",
    )
    assert out.index("<style>x</style>") < out.index("</head>")


def test_inject_head_no_head_uses_html():
    out = td._inject_head("<html><body>hi</body></html>", "<style>x</style>")
    assert "<head><meta charset='utf-8'><style>x</style></head>" in out


def test_set_title_replaces_existing():
    out = td._set_title("<head><title>old</title></head>", "新标题")
    assert "<title>新标题</title>" in out
    assert "old" not in out


def test_set_title_escapes():
    out = td._set_title("<head></head>", "a<b>c")
    assert "a&lt;b&gt;c" in out


def test_print_baseline_rules():
    css = td._PRINT_BASELINE
    assert "@media print" in css
    assert "break-inside:avoid" in css
    assert "break-after:avoid" in css
    assert "table-header-group" in css

