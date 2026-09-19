"""数据库工具：只读校验、SQL 预览、表格格式化。

从 deepseek_client.py 中拆出的纯函数/常量，供数据库查询/执行工具复用。
"""
import re

from shared import TABLE_CELL_MAX, _env_int  # 常量单一来源（shared 归口）

# 单元格显示截断上限（防超长单元格撑爆上下文）；WHALETALK_TABLE_CELL_MAX=0 → 不限
# 注：TABLE_CELL_MAX 由 shared 统一定义，工具输出与前端渲染共用同一来源。

# 单次数据库写操作影响行数上限（超限拒绝，防误伤全表）；WHALETALK_DB_EXECUTE_MAX_ROWS=0 → 不限
DB_EXECUTE_MAX_ROWS = _env_int("DB_EXECUTE_MAX_ROWS", 10000)

# 只读查询禁止的服务器端功能关键字（前缀白名单可被其绕过，读写服务器文件 / DoS）：
# MySQL: SELECT ... INTO OUTFILE/DUMPFILE、LOAD_FILE、LOAD DATA、SLEEP、BENCHMARK
# PostgreSQL: lo_export / lo_import、pg_read_file / pg_write_file、pg_ls_dir /
#             pg_stat_file（列目录/读文件）、pg_terminate_backend / pg_cancel_backend（杀会话）
# 说明：标识类关键字走子串匹配（SQL 已归一为 upper）；**函数调用类**另见
# DB_FORBIDDEN_CALLS——用「名字 + 可选空白 + 左括号」的正则匹配，防 `SLEEP (1)`
# 这类插空格绕过子串匹配的写法。
DB_FORBIDDEN_KEYWORDS = (
    "INTO OUTFILE",
    "INTO DUMPFILE",
    "LOAD_FILE",
    "LOAD DATA",
    "LO_EXPORT",
    "LO_IMPORT",
    "PG_READ_FILE",
    "PG_WRITE_FILE",
    "PG_READ_BINARY_FILE",
    "PG_READ_SERVER_FILES",
    "PG_WRITE_SERVER_FILES",
    "PG_LS_DIR",
    "PG_STAT_FILE",
    "PG_TERMINATE_BACKEND",
    "PG_CANCEL_BACKEND",
    "PG_DATABASE_SIZE",
    "DEFAULT_TABLESPACE",
)

# 函数调用类禁止项（名字后允许任意空白再跟左括号）
DB_FORBIDDEN_CALLS = (
    "SLEEP",
    "BENCHMARK",
    "PG_SLEEP",
    "PG_READ_FILE",
    "PG_WRITE_FILE",
    "PG_READ_BINARY_FILE",
    "PG_LS_DIR",
    "PG_STAT_FILE",
)
_DB_FORBIDDEN_CALL_RE = re.compile(
    r"\b(?:" + "|".join(DB_FORBIDDEN_CALLS) + r")\s*\(", re.I)


def readonly_stmt(sql):
    """判断 SQL 是否为安全的只读语句（SELECT/SHOW/DESC/PRAGMA/EXPLAIN）。"""
    stmt = str(sql or "").strip()
    if not stmt:
        return False
    upper = stmt.upper()
    if not upper.startswith(("SELECT", "SHOW", "DESC", "PRAGMA", "EXPLAIN")):
        return False
    # EXPLAIN ANALYZE 在 MySQL 8.0.18+ / PG 会**真实执行**被分析的语句（含 DELETE/
    # UPDATE/INSERT），绝不能当只读放行（否则只读工具可执行写操作）。
    if upper.startswith("EXPLAIN") and re.search(r"\bANALYZE\b", upper):
        return False
    # 分号隔离的附加语句（SELECT 1; DROP TABLE ...）：带内部分号的整句拒绝
    if ";" in stmt.rstrip(";"):
        return False
    # 服务器端功能关键字过滤：用户可关（WHALETALK_DB_READ_FILTER=0 → 只按前缀判定）
    if _env_int("DB_READ_FILTER", 1) <= 0:
        return True
    for kw in DB_FORBIDDEN_KEYWORDS:
        if kw in upper:
            return False
    # 函数调用类：名字与左括号间允许任意空白（防 `SLEEP (1)` 绕过子串匹配）
    return not _DB_FORBIDDEN_CALL_RE.search(upper)


def force_limit(stmt, limit):
    """给只读 SELECT 强制追加 LIMIT n（防止无界查询全量执行撑爆内存/拖慢库）。

    仅对 SELECT 生效；SHOW/DESC/PRAGMA/EXPLAIN 不追加。语句本身已含 LIMIT
    （含注释内出现 limit 字样）时跳过，避免重复限制或破坏子查询语义。尾部
    行/块注释在拼接前剥除，避免 LIMIT 被注释吞掉。
    是否强制由用户配置：WHALETALK_DB_FORCE_LIMIT=0 → 完全不追加（返回原语句）。"""
    if _env_int("DB_FORCE_LIMIT", 1) <= 0:
        return str(stmt or "").strip()
    s = str(stmt or "").strip().rstrip().rstrip(";").rstrip()
    if not s.upper().startswith("SELECT"):
        return s
    # 判定已有 LIMIT 时先剥离字符串字面量与注释，避免把 'LIMIT' 字面量误当已有 LIMIT；
    # 追加用**换行**，避免行注释 `-- ...` 把 LIMIT 吞掉（`SELECT * FROM t--c`）。
    core = re.sub(r"'(?:''|[^'])*'", "''", s)
    core = re.sub(r'"(?:""|[^"])*"', '""', core)
    core = re.sub(r"--[^\n]*", "", core)
    core = re.sub(r"/\*.*?\*/", "", core, flags=re.S)
    if re.search(r"\bLIMIT\b", core, re.I):
        return s
    return s + f"\nLIMIT {max(1, int(limit))}"


def db_preview_sql(stmt):
    """把 UPDATE/DELETE 改写为等价的 SELECT（用于变更行数预览）。"""
    m = re.match(r"(UPDATE|DELETE)\s+", stmt, re.I)
    wm = re.search(r"\bWHERE\b", stmt, re.I)
    if not m or not wm:
        return None
    if m.group(1).upper() == "UPDATE":
        sm = re.search(r"\bSET\b", stmt, re.I)
        table_part = stmt[m.end():sm.start()] if sm else stmt[m.end():wm.start()]
        return "SELECT * FROM " + table_part + stmt[wm.start():]
    return "SELECT * FROM " + stmt[m.end():wm.start()] + stmt[wm.start():]


def table_to_md(rows, cell_max=TABLE_CELL_MAX):
    """把 list[list] 转 Markdown 表格（含单元格截断与空行过滤）。

    markdown 表格以 | 定界，单元格内出现 | 会把一行切成多列、破坏结构
    （excel/csv 单元格内容常见），故统一转义为 \\|（渲染时仍显示单 |）。
    新增空行整行过滤：全空行（如 CSV 中间空行）不留 —— 与 docx/pptx 读取
    （先剔除空文本段落再转表）行为对齐，避免表格中出现空行噪声。
    """
    rows = [[("" if c is None else str(c)).strip() for c in r] for r in rows]
    if cell_max and cell_max > 0:  # cell_max<=0 = 不截断
        rows = [[c[:cell_max] + ("…" if len(c) > cell_max else "") for c in r] for r in rows]
    rows = [[c.replace("|", "\\|") for c in r] for r in rows]
    if not rows:
        return "（空表格）"
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "| " + " | ".join("---" for _ in rows[0]) + " |"]
    for r in rows[1:]:
        if any(c.strip() for c in r):
            lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)
