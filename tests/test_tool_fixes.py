"""工具实现修复回归（只读 SQL / 强制 LIMIT / 表格 / 生图尺寸 / run_python 退出码）。"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import api_server  # noqa: E402  （先于 agent_tools 导入，遵守导入顺序契约）
import db_utils  # noqa: E402


def test_readonly_rejects_explain_analyze():
    assert db_utils.readonly_stmt("EXPLAIN ANALYZE DELETE FROM t") is False
    assert db_utils.readonly_stmt("EXPLAIN ANALYZE UPDATE t SET a=1") is False
    assert db_utils.readonly_stmt("EXPLAIN SELECT 1") is True


def test_force_limit_ignores_string_literal_and_appends_after_line_comment():
    out = db_utils.force_limit("SELECT * FROM t WHERE note='LIMIT'", 20)
    assert "LIMIT 20" in out.upper()
    out2 = db_utils.force_limit("SELECT * FROM t--c", 20)
    # LIMIT 必须在行注释之后另起一行，才不会被注释吞掉
    assert "\nLIMIT 20" in out2.upper()


def test_force_limit_keeps_existing_limit():
    s = "SELECT * FROM t LIMIT 5"
    assert db_utils.force_limit(s, 20).strip() == s


def test_table_to_md_renders_none_as_empty():
    md = db_utils.table_to_md([["a", "b"], [None, "x"]])
    assert "None" not in md
    assert "| a | b |" in md


def test_nearest_size_uses_valid_dall_e_sizes():
    from agent_tools import tool_codegen as tc
    assert tc._nearest_size(800, 800) == "1024x1024"
    assert tc._nearest_size(2000, 1000) == "1536x1024"
    assert tc._nearest_size(1000, 2000) == "1024x1536"


def test_run_python_nonzero_exit_is_error():
    from agent_tools import tool_code as tcode
    out = tcode.run_python("import sys; sys.exit(3)")
    assert out.startswith("错误") and "3" in out


def test_detect_utf32_before_utf16(tmp_path):
    from agent_tools import tool_files as tf
    p = tmp_path / "u32.txt"
    p.write_bytes("hello".encode("utf-32"))
    enc, _fb = tf._detect_text_encoding(str(p))
    assert enc == "utf-32"


def test_ffmpeg_hw_report_shape():
    import deepseek_client as dc
    rep = dc.ffmpeg_hw_report()
    assert set(("preference", "h264", "hevc", "hardware")) <= set(rep)
    assert isinstance(rep["hardware"], bool)


def test_ffmpeg_video_encode_args_cpu_fallback(monkeypatch):
    import deepseek_client as dc
    monkeypatch.setenv("WHALETALK_FFMPEG_HW", "off")
    monkeypatch.setitem(dc._FFMPEG_HW, "done", False)
    monkeypatch.setitem(dc._FFMPEG_HW, "h264", "")
    try:
        assert dc._ffmpeg_video_encode_args("h264")[:2] == ["-c:v", "libx264"]
    finally:
        dc._FFMPEG_HW["done"] = False


def test_lrc_parse_decimal_units():
    import mv_engine as me
    rows = me.parse_lrc("[00:26.6]hello\n[01:02.60]world")
    assert abs(rows[0]["start"] - 26.6) < 0.001
    assert abs(rows[1]["start"] - 62.6) < 0.001
