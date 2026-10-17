"""工具返回契约与数据库查询公共逻辑的回归（toolkit.fail + tool_docs 抽取）。

背景：
  - `toolkit.fail()` 把「失败返回字符串、且以「错误：」开头」这一全仓契约收敛到一处。
    `shared.TOOL_RESULT_FAIL_PREFIXES` 据此判定成功/失败，失败记忆与自动消解都依赖它，
    所以前缀一旦写错，工具会被静默判成「成功」——必须有测试锁死。
  - `database_query_mysql` / `database_query_postgres` 曾近乎逐行复制（相似度 0.88），
    已抽到 `_readonly_query` / `_format_query_rows` / `_truncate_cells`。此处锁住
    「抽取后输出格式不变」，避免以后改动悄悄改变对模型可见的结果文案。

注意导入顺序契约（TECH_NOTES §21.6）：必须先 import deepseek_client 再 import agent_tools。
"""
import shared
import deepseek_client as dc  # noqa: F401  必须早于 agent_tools（循环导入契约）
import agent_tools.tool_docs as td
import toolkit
from toolkit import fail

ERR = "错误"


# ── toolkit.fail() ──────────────────────────────────────────────────

def test_fail_produces_recognized_failure_prefix():
    """fail() 的任何输入都必须命中 TOOL_RESULT_FAIL_PREFIXES（否则被判成功）。"""
    for msg in ["sql 必填", "查询失败: timeout", "", None, "错误：已经带了前缀"]:
        out = fail(msg)
        assert isinstance(out, str)
        assert out.startswith(shared.TOOL_RESULT_FAIL_PREFIXES), out


def test_fail_uses_fullwidth_colon_single_prefix():
    out = fail("缺少参数")
    assert out == "错误：缺少参数", out
    # 不能再出现第二个「错误」标记：以错误开头就是唯一判定信号
    assert out.count(ERR) == 1, out


def test_fail_strips_duplicate_prefix():
    """调用方误把前缀也写进来时，不能产生「错误：错误：…」。"""
    for raw in ("错误：sql 必填", "错误: sql 必填"):
        out = fail(raw)
        assert out == "错误：sql 必填", out
        assert out.count(ERR) == 1, out


def test_fail_empty_message_has_fallback_without_second_marker():
    for raw in ("", None, "   "):
        out = fail(raw)
        assert out.startswith("错误："), out
        assert out.count(ERR) == 1, out
        assert len(out) > len("错误：")


def test_fail_is_exported_from_toolkit():
    assert toolkit.fail is fail


# ── 数据库查询：抽取后的输出格式不变 ─────────────────────────────────

def test_mysql_postgres_tools_still_registered_with_same_signature():
    import inspect

    for name in ("database_query_mysql", "database_query_postgres"):
        fn = dc.TOOL_CALL_MAP[name]
        assert callable(fn)
        params = list(inspect.signature(fn).parameters)
        assert params == ["connection", "sql", "max_rows"], (name, params)


def test_both_databases_reject_empty_sql_identically():
    for fn in (td.database_query_mysql, td.database_query_postgres):
        out = fn(sql="")
        assert out == "错误：sql 必填", (fn.__name__, out)


def test_format_query_rows_layout_unchanged():
    """MySQL/PostgreSQL 共用的结果渲染：表头、分隔符、截断标记、空结果集文案。"""
    out = td._format_query_rows(["a", "b"], [(1, 2), (3, None)], 20)
    assert out == "a | b\n1 | 2\n3 | ", repr(out)

    # 命中行数上限 → 追加截断标记
    assert td._format_query_rows(["a"], [("x",)] * 3, 3) == "a\nx\nx\nx [已截断]"
    # 未达上限 → 不追加
    assert not td._format_query_rows(["a"], [("x",)], 3).endswith("[已截断]")
    # 无结果集
    assert td._format_query_rows([], [], 20) == "执行成功（无结果集）"


def test_truncate_cells_matches_previous_inline_behaviour():
    """_truncate_cells 必须与抽取前内联实现的逐单元格结果完全一致（三种查询共用）。"""
    lim = td._TABLE_CELL_MAX
    rows = [(1, "alice", None), ("y" * 300, "", 3.5)]
    for r in rows:
        expected = ["" if v is None else str(v) for v in r]
        expected = [c[:lim] + ("…" if len(c) > lim else "") for c in expected]
        assert td._truncate_cells(r) == expected, (r, td._truncate_cells(r), expected)
