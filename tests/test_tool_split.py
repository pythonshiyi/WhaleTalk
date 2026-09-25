"""P0-1 巨石拆分首批回归：agent_tools/ 域模块注册与 re-export。

验证首批迁出的 4 个工具（get_date/get_weather/read_csv/write_csv）：
  1. deepseek_client 命名空间仍可访问（re-export 兼容旧路径）；
  2. 六层注册完整且 executor 指向同一函数对象；
  3. 行为与迁移前一致（函数级冒烟）；
  4. toolkit.rebuild_layers 多文件 AST 重建与运行时六层一致（门禁等价）。
"""
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


# ===== P0-1 第三~八批（docs/web/code/files/brain/msg/system/desktop 域）=====
ALL_SPLIT_TOOLS = [
    "database_query_mysql", "database_query_postgres", "read_excel", "epub_read",
    "mobi_read", "doc_read", "msg_read", "archive_list", "write_excel",
    "chart_data", "database_query", "database_execute", "pdf_extract",
    "pdf_create", "docx_read", "pptx_read", "secret_store", "kv_store",
    "create_doc",
    "fetch_url", "download_file", "search_web", "search_github",
    "search_realtime", "browser_navigate", "web_screenshot", "net_diagnose",
    "fetch_url_smart", "rss_fetch", "webdav", "call_api", "track_web",
    "run_python", "run_command", "run_lint", "run_tests", "verify_project",
    "project_scaffold", "dev_plan", "get_status", "project_map", "find_symbol",
    "code_lookup", "write_code_project", "pip_install", "subagent_run",
    "verify_output",
    "read_file", "write_file", "edit_file", "list_dir", "search_local",
    "clipboard_get", "clipboard_set", "delete_file", "archive_files",
    "extract_archive", "list_snapshots", "restore_snapshot", "batch_rename",
    "start_process", "stop_process", "list_processes", "environment_info",
    "write_memory", "read_memory", "delete_memory", "update_memory",
    "self_profile", "query_memory_graph", "knowledge_index", "knowledge_search",
    "schedule_task", "list_schedules", "cancel_schedule",
    "task_checkpoint_save", "task_checkpoint_load", "run_workflow",
    "send_email", "publish_draft", "send_webhook", "im_send",
    "telegram_poll_updates", "read_email", "email_summary", "agent_mail",
    "run_wechat_writer", "daily_brief",
    "watch_files", "recall_session", "project_info", "read_project_file",
    "create_evolution", "self_evolve", "verify_files", "notify_desktop",
    "app_manage", "usage_report", "create_plugin",
    "rpa_screen_size", "rpa_click", "rpa_type", "rpa_hotkey", "rpa_move",
    "rpa_scroll", "rpa_screenshot", "screen_find_click", "vision_loop",
    "tts_save", "tts_speak", "tts_stop", "speech_to_text",
    "voice_chat_loop", "image_generate", "qrcode", "media_ffmpeg", "team_run",
    "community_post", "community_save", "community_status", "community_cycle",
]

BATCH_SIZES = [19, 13, 15, 17, 14, 10, 11, 18, 4]
BATCH_MODULES = [
    "agent_tools.tool_docs", "agent_tools.tool_web", "agent_tools.tool_code",
    "agent_tools.tool_files", "agent_tools.tool_brain", "agent_tools.tool_msg",
    "agent_tools.tool_system", "agent_tools.tool_desktop",
    "agent_tools.tool_community",
]
BATCH_RANGES = []
_b = 0
for _sz, _mod in zip(BATCH_SIZES, BATCH_MODULES):
    BATCH_RANGES.append((_b, _b + _sz, _mod))
    _b += _sz


def _uniq(names):
    seen, out = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def test_all_split_tools_reexported_on_deepseek_client():
    for n in _uniq(ALL_SPLIT_TOOLS):
        assert hasattr(dc, n), "deepseek_client.%s 应经 agent_tools re-export" % n


def test_all_split_tools_module_ownership():
    module_of = {}
    for lo, hi, mod in BATCH_RANGES:
        for t in ALL_SPLIT_TOOLS[lo:hi]:
            module_of[t] = mod
    for n in _uniq(ALL_SPLIT_TOOLS):
        expect = module_of.get(n)
        assert expect is not None, "工具 %s 未定义期望归属" % n
        actual = getattr(dc, n).__module__
        assert actual == expect, "%s 应归属 %s，实际 %s" % (n, expect, actual)


def test_all_split_tools_in_all_six_layers():
    names = [t["function"]["name"] for t in dc.TOOLS]
    # 163（162 + hardware_accel）→ 165（+ lyric_align / mv_credits_card）→ 169（+ 鲸群社区 4 工具）
    assert len(names) == 169, "工具总数应为 169，实际 %d" % len(names)
    assert len(dc._TOOL_ORDER) == 169, "顺序表必须与工具数一致"
    assert set(dc.TOOL_CALL_MAP) == set(dc._TOOL_ORDER), "CALL_MAP 键与 ORDER 必须一一对应"
    for n in _uniq(ALL_SPLIT_TOOLS):
        assert n in names, "%s 必须仍在 TOOLS 列表" % n
        impl = dc.TOOL_CALL_MAP.get(n)
        assert impl is not None and callable(impl), "%s 缺少 CALL_MAP 实现" % n
    grouped = {m for _, ms in dc.TOOL_GROUPS for m in ms}
    assert set(_uniq(ALL_SPLIT_TOOLS)) <= grouped, "拆分工具必须仍在能力地图分组"
    pre = {t for _, ts in dc._PREACTIVATE_HINTS for t in ts}
    assert set(_uniq(ALL_SPLIT_TOOLS)) <= pre, "拆分工具必须仍参与关键词预激活"
    for n in _uniq(ALL_SPLIT_TOOLS):
        assert n in dc._TOOL_ACTION_PHRASES, "%s 缺失动作短语" % n


def test_fetch_blocked_alias_impl():
    """P2-4：schema 名访问路径 dc.fetch_blocked 与 CALL_MAP 实现同一对象；
    _run_fetch_blocked 旧路径保留。"""
    assert "fetch_blocked" in dc.TOOL_CALL_MAP
    impl = dc.TOOL_CALL_MAP["fetch_blocked"]
    assert impl.__module__ == "agent_tools.tool_web"
    assert hasattr(dc, "_run_fetch_blocked"), "旧路径 dc._run_fetch_blocked 必须保留"
    assert hasattr(dc, "fetch_blocked"), "P2-4: 工具名路径 dc.fetch_blocked 必须存在"
    assert dc.fetch_blocked is impl, "dc.fetch_blocked 必须与 TOOL_CALL_MAP 实现同一对象"
    assert dc.fetch_blocked is dc._run_fetch_blocked, "别名与实现须同一函数对象"
    out = impl("")
    assert "URL" in out and "http" in out


def test_main_module_no_tool_defs():
    import ast as _ast
    src = (REPO / "deepseek_client.py").read_text(encoding="utf-8")
    tree = _ast.parse(src)
    leftover = []
    for node in tree.body:
        if isinstance(node, _ast.FunctionDef):
            decs = [d for d in node.decorator_list
                    if isinstance(d, _ast.Call) and isinstance(d.func, _ast.Name)
                    and d.func.id in ("tool", "register_tool")]
            if decs:
                leftover.append(node.name)
    assert not leftover, "主文件仍残留工具定义: %s" % leftover


def test_all_split_tools_validation_branch_no_crash():
    cases = [
        ("database_query", "default", ""),
        ("fetch_url", ""),
        ("run_command", ""),
        ("read_file", ""),
        ("write_memory", ""),
        ("send_email", "a@b.c", "", ""),
        ("project_info",),
        ("rpa_hotkey", ""),
    ]
    for call in cases:
        fn = getattr(dc, call[0])
        out = fn(*call[1:])
        assert isinstance(out, str) and out, "%s 校验分支应返回非空字符串" % call[0]


# ===== 孤岛对账第 10 层：agent_tools.__all__ re-export 完整性 =====
def test_agent_tools_all_reexports_every_domain_impl():
    """域模块内定义的每个工具实现都必须经 __all__ re-export。

    否则 `from agent_tools import *` 不会绑定该名，`dc.<tool_name>` 旧访问路径
    静默失效（failure_memory 曾漏：工具可经 TOOL_CALL_MAP 触发，但 dc 命名空间
    拿不到）。此断言即 island_check 第 10 层的运行时等价物。
    """
    import agent_tools

    missing = []
    for name, impl in dc.TOOL_CALL_MAP.items():
        mod = getattr(impl, "__module__", "") or ""
        if mod.startswith("agent_tools.") and getattr(impl, "__name__", "") not in agent_tools.__all__:
            missing.append("%s->%s" % (name, getattr(impl, "__name__", "?")))
    assert not missing, "agent_tools.__all__ 漏 re-export: %s" % missing


def test_failure_memory_reexported():
    """failure_memory 属 agent_tools.tool_brain，必须在 dc 命名空间可达。"""
    assert hasattr(dc, "failure_memory"), "deepseek_client.failure_memory 应经 agent_tools re-export"
    assert dc.failure_memory.__module__ == "agent_tools.tool_brain"
    assert dc.TOOL_CALL_MAP["failure_memory"] is dc.failure_memory


def test_island_check_strict_passes():
    """门禁自检：10 层孤岛对账在 --strict 下不得有缺口。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "island_check", REPO / "tools" / "island_check.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--strict"]) == 0
