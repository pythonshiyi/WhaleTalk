# -*- coding: utf-8 -*-
"""P0-1 巨石拆分首批回归：agent_tools/ 域模块注册与 re-export。

验证首批迁出的 4 个工具（get_date/get_weather/read_csv/write_csv）：
  1. deepseek_client 命名空间仍可访问（re-export 兼容旧路径）；
  2. 六层注册完整且 executor 指向同一函数对象；
  3. 行为与迁移前一致（函数级冒烟）；
  4. toolkit.rebuild_layers 多文件 AST 重建与运行时六层一致（门禁等价）。
"""
import os
import sys
from pathlib import Path

import deepseek_client as dc

REPO = Path(__file__).resolve().parent.parent
SPLIT_TOOLS = ["get_date", "get_weather", "read_csv", "write_csv"]


def test_split_tools_reexported_on_deepseek_client():
    for n in SPLIT_TOOLS:
        assert hasattr(dc, n), f"deepseek_client.{n} 应经 agent_tools re-export"


def test_split_tools_in_all_six_layers():
    names = [t["function"]["name"] for t in dc.TOOLS]
    assert set(SPLIT_TOOLS) <= set(names), "拆分工具必须仍在 TOOLS 列表"
    for n in SPLIT_TOOLS:
        impl = dc.TOOL_CALL_MAP.get(n)
        assert impl is getattr(dc, n), f"{n} 的 CALL_MAP 应指向同一函数对象"
    grouped = {m for _, ms in dc.TOOL_GROUPS for m in ms}
    assert set(SPLIT_TOOLS) <= grouped, "拆分工具必须仍在能力地图分组"
    for n in SPLIT_TOOLS:
        assert n in dc._TOOL_ACTION_PHRASES, f"{n} 缺失动作短语"
    pre = {t for _, ts in dc._PREACTIVATE_HINTS for t in ts}
    assert set(SPLIT_TOOLS) <= pre, "拆分工具必须仍参与关键词预激活"


def test_get_date_behavior_unchanged():
    out = dc.get_date()
    assert len(out) >= 16  # YYYY-MM-DD HH:MM:SS [+ 时区]
    assert ":" in out


def test_get_weather_validation_branch():
    # 不联网：只验证参数校验分支（迁移前后一致）
    assert "必填" in dc.get_weather("", "2026-09-01")


def test_csv_roundtrip(tmp_path):
    p = str(tmp_path / "roundtrip.csv")
    r = dc.write_csv(p, [["a", "b"], [1, 2]], headers="x,y")
    assert "已写入" in r
    out = dc.read_csv(p)
    assert "x" in out and "1" in out


def test_rebuild_layers_multifile_matches_runtime():
    """门禁等价性：rebuild_layers(主文件, *域模块) 与运行时六层一致。"""
    import toolkit

    main = (REPO / "deepseek_client.py").read_text(encoding="utf-8")
    tool_dir = REPO / "agent_tools"
    extra = [
        p.read_text(encoding="utf-8")
        for p in sorted(tool_dir.glob("*.py"))
        if p.name != "__init__.py"
    ]
    layers = toolkit.rebuild_layers(main, *extra)
    rebuilt = [t["function"]["name"] for t in layers["TOOLS"]]
    runtime = [t["function"]["name"] for t in dc.TOOLS]
    assert rebuilt == runtime, "AST 重建工具顺序与运行时不一致（门禁与实跑会分叉）"
    # 拆分的工具也必须出现在重建结果里
    assert set(SPLIT_TOOLS) <= set(rebuilt)


# ===== P0-1 第二批（媒体/文档域）：tool_media.py =====
MEDIA_TOOLS = [
    "image_process", "ocr_image", "image_understand", "screen_capture",
    "screen_see", "chart_read", "screenshot_to_html", "debug_screenshot",
    "scan_read", "image_batch",
]


def test_media_tools_reexported_on_deepseek_client():
    for n in MEDIA_TOOLS:
        assert hasattr(dc, n), f"deepseek_client.{n} 应经 agent_tools re-export"
        assert getattr(dc, n).__module__ == "agent_tools.tool_media", (
            f"{n} 应归属 agent_tools.tool_media"
        )


def test_media_tools_in_all_six_layers():
    names = [t["function"]["name"] for t in dc.TOOLS]
    assert set(MEDIA_TOOLS) <= set(names), "媒体工具必须仍在 TOOLS 列表"
    for n in MEDIA_TOOLS:
        impl = dc.TOOL_CALL_MAP.get(n)
        assert impl is getattr(dc, n), f"{n} 的 CALL_MAP 应指向同一函数对象"
        assert n in dc._TOOL_ORDER, f"{n} 必须保留在 _TOOL_ORDER"
        assert n in dc._TOOL_ACTION_PHRASES, f"{n} 缺失动作短语"
    grouped = {m for _, ms in dc.TOOL_GROUPS for m in ms}
    assert set(MEDIA_TOOLS) <= grouped, "媒体工具必须仍在能力地图分组"
    pre = {t for _, ts in dc._PREACTIVATE_HINTS for t in ts}
    assert set(MEDIA_TOOLS) <= pre, "媒体工具必须仍参与关键词预激活"


def test_media_helpers_kept_in_main_module():
    """视觉闭环辅助符号必须保留在主文件（vision_loop/RPA 直接调用）。"""
    for n in ("_capture_screen_png", "_extract_image_path"):
        fn = getattr(dc, n)
        assert callable(fn) and fn.__module__ == "deepseek_client", (
            f"{n} 应保留在主文件"
        )
    assert isinstance(dc._IMAGE_PRODUCING_TOOLS, frozenset)
    assert "screen_capture" in dc._IMAGE_PRODUCING_TOOLS


def test_media_tool_validation_branch():
    # 不依赖 PIL/网络：只验证参数校验分支（迁移前后一致）
    assert "必填" in dc.image_process("", "")
    assert "不存在" in dc.ocr_image("Z:/definitely/not/exist.png")
    assert "必填" in dc.image_understand("")
    assert "不存在" in dc.image_batch("Z:/definitely/not/a/folder")
