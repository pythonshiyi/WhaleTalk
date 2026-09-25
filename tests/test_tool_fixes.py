"""工具实现修复回归（只读 SQL / 强制 LIMIT / 表格 / 生图尺寸 / run_python 退出码）。"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

import api_server  # noqa: E402  （先于 agent_tools 导入，遵守导入顺序契约）
import db_utils  # noqa: E402
import shared  # noqa: E402


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
    """非零退出：语义化报告（命令已执行，非零常表示「发现了问题」），
    不再一律冠「错误：」——避免误导模型 + 污染失败记忆。详见 shared.format_process_result。"""
    from agent_tools import tool_code as tcode
    out = tcode.run_python("import sys; sys.exit(3)")
    assert "3" in out
    assert not out.startswith("错误"), "非零退出不应一律判为失败"
    assert not shared.is_tool_failure(out)


def test_detect_utf32_before_utf16(tmp_path):
    from agent_tools import tool_files as tf
    p = tmp_path / "u32.txt"
    p.write_bytes("hello".encode("utf-32"))
    enc, _fb = tf._detect_text_encoding(str(p))
    assert enc == "utf-32"


def test_detect_encoding_when_multibyte_split_at_prefix_boundary(tmp_path):
    """回归：编码探测只读前 8192 字节；若第 8192 字节正好切开一个 UTF-8 汉字
    （中=3 字节），严格 decode 会因「结尾不完整」失败 → 误判 latin-1 → 中文乱码。
    修复后应仍判为 utf-8。"""
    from agent_tools import tool_files as tf
    p = tmp_path / "split.txt"
    # 8190 个 ASCII + 1 个三字节汉字：前 8192 字节只含该汉字的 2/3 字节
    p.write_bytes(b"a" * 8190 + "中".encode("utf-8"))
    enc, fb = tf._detect_text_encoding(str(p))
    assert enc == "utf-8" and fb is False


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


def test_gpu_accel_ops_shapes_and_gpu_selftest():
    import numpy as np

    import gpu_accel as g
    a = np.random.default_rng(0).random((16, 24, 4), dtype=np.float32)
    assert g.grade(a, 1.1, 1.05, 0.95, 1.1).shape == a.shape
    assert g.gaussian_blur(a, 2.0).shape == a.shape
    assert g.bloom(a, 0.7, 3.0, 0.5).shape == a.shape
    assert g.composite(a, a[::-1], 0.4).shape == a.shape
    assert g.resize(a, 8, 12).shape == (12, 8, 4)
    info = g.device_info()
    assert "backend" in info and "available" in info
    if g.available():
        ok, detail = g.selftest()
        assert ok, detail


def test_hardware_accel_tool_probe():
    from agent_tools import tool_system as ts
    out = ts.hardware_accel("probe")
    assert "后端" in out
    perf = ts.hardware_accel("perf", top=3)
    assert "CPU" in perf and "内存" in perf
