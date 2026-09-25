"""跨模块共享的纯函数与常量（无 GUI / 无 API 依赖）。

从 main.py / deepseek_client.py / taskpanel.py 中抽取，消除重复实现漂移：
- cron 5 字段表达式引擎（校验 / 匹配）
- 本地绝对路径正则 PATH_RE
- Windows OCR PowerShell 脚本（占位符统一 @PATH@，避免与 $ 变量名冲突）
"""
import os
import re
import threading
from datetime import datetime, timedelta


def _env_int(name, default):
    """数值上限的环境覆盖：WHALETALK_<NAME> 可设；返回 int，0/负 表示「不限」（由调用方解释）。

    目的：上限属**用户配置**而非程序硬编码——不因某次实验就替用户/模型定死边界。
    """
    try:
        raw = os.environ.get("WHALETALK_" + name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(raw)
    except Exception:
        return default

# ============================ 峰谷定价 ============================
# DeepSeek 峰谷定价：工作日高峰时段（北京时间 9:00-12:00 / 14:00-18:00），
# 其余为低谷；周六、周日全天统一按低谷计费（2026-08-23 起生效的规则）
PEAK_HOURS = ((9, 12), (14, 18))


def is_peak_hour(now=None):
    """判断当前是否为 DeepSeek 高峰计费时段。

    规则（2026-08-23 起）：工作日高峰 9:00-12:00、14:00-18:00；
    周六、周日全天低谷（不区分峰谷）。
    """
    try:
        now = now or datetime.now()
        if now.weekday() >= 5:  # 周六(5)/周日(6) 全天低谷
            return False
        h = now.hour
        return any(a <= h < b for a, b in PEAK_HOURS)
    except Exception:
        return False

# ============================ cron 引擎 ============================
# cron 5 字段的值域（分/时/日/月/周），weekday 1=周一…7=周日
CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (1, 7))


def cron_int(s):
    """字符串安全转 int；失败返回 None。"""
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def cron_field_match(field, value):
    """匹配 cron 单字段（* / 逗号 / 连字符 / 步进）。"""
    field = str(field).strip()
    if field in ("*", "?"):
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            base, _, step = part.partition("/")
            try:
                step = int(step)
            except ValueError:
                continue
            start = 0 if base in ("*", "?") else cron_int(base)
            if start is None:
                continue
            if value >= start and (value - start) % step == 0:
                return True
        elif "-" in part:
            lo, _, hi = part.partition("-")
            lo_v, hi_v = cron_int(lo), cron_int(hi)
            if lo_v is not None and hi_v is not None and lo_v <= value <= hi_v:
                return True
        else:
            v = cron_int(part)
            if v is not None and v == value:
                return True
    return False


def cron_field_ok(field, pos=0):
    """cron 字段语法校验：仅允许数字、*、,、-、/，且值域合法（添加定时任务时防止非法表达式）。"""
    field = str(field or "").strip()
    if not field:
        return False
    if field in ("*", "?"):
        return True
    lo, hi = CRON_RANGES[pos]
    for part in field.split(","):
        part = part.strip()
        if not part:
            return False
        if "/" in part:
            base, _, step = part.partition("/")
            if cron_int(step) is None or not (1 <= cron_int(step) <= hi):
                return False
            if base not in ("*", "?", "") and not (
                cron_int(base) is not None and lo <= cron_int(base) <= hi
            ):
                return False
        elif "-" in part:
            lo_s, _, hi_s = part.partition("-")
            lv, rv = cron_int(lo_s), cron_int(hi_s)
            if lv is None or rv is None or not (lo <= lv <= hi and lo <= rv <= hi and lv <= rv):
                return False
        else:
            v = cron_int(part)
            if v is None or not (lo <= v <= hi):
                return False
    return True


def defer_until(now):
    """高峰错峰顺延目标时刻：最近空闲时段开始（12:00 / 18:00；已过则次日 0:00）。

    仅在高峰时段调用：9-12 高峰 → 12:00；14-18 高峰 → 18:00；
    若对应空闲开始时刻已过（极端情况）→ 次日 0:00。
    """
    h = now.hour
    defer_h = 12 if h < 14 else 18
    ts = now.replace(hour=defer_h, minute=0, second=0, microsecond=0).timestamp()
    if ts <= now.timestamp():
        ts = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return ts


def budget_thinking(budget, cost, thinking):
    """预算感知思考降档：接近月度预算 80% 时，auto/max 档自动降为 high。

    返回 (effective_thinking, near_budget)。用户显式选择的 low/medium/high
    档位不干预（尊重手动选择）；仅对智能路由与最高档做降级。
    """
    if budget > 0 and cost >= budget * 0.8:
        if thinking in ("auto", "max"):
            return "high", True
        return thinking, True
    return thinking, False


def cron_match(expr, now):
    """匹配 5 字段 cron 表达式（分 时 日 月 周，周 1=周一…7=周日）。"""
    try:
        fields = str(expr).strip().split()
        if len(fields) != 5:
            return False
        minute, hour, day, month, weekday = fields
        return (
            cron_field_match(minute, now.minute)
            and cron_field_match(hour, now.hour)
            and cron_field_match(day, now.day)
            and cron_field_match(month, now.month)
            and cron_field_match(weekday, now.isoweekday())
        )
    except Exception:
        return False


# ============================ 跨进程文件锁（D3） ============================
# web_app（服务端）与 CLI 双进程可能并发读写同一数据文件（记忆/密钥/KV），
# 进程内 threading.Lock 无法互斥。用 OS 级文件锁：Windows 走 msvcrt.locking，
# POSIX 走 fcntl.flock。锁文件为 <目标路径>.lock（占用极小，残留无害）。
def file_lock(target_path, timeout=10.0):
    """跨进程文件锁上下文管理器：对 target_path 的写临界区加 OS 级排它锁。

    用法：
        with file_lock(MEMORY_FILE):
            ...读写文件...
    超时抛 TimeoutError（调用方可捕获后重试/报错）。锁粒度按文件，
    同一进程内重复进入（嵌套）会死锁——临界区要最小化。
    """
    import contextlib
    import os as _os

    @contextlib.contextmanager
    def _ctx():
        lock_path = str(target_path) + ".lock"
        try:
            _os.makedirs(_os.path.dirname(_os.path.abspath(lock_path)) or ".", exist_ok=True)
        except OSError:
            pass
        fd = _os.open(lock_path, _os.O_CREAT | _os.O_RDWR)
        try:
            deadline = _monotonic() + timeout
            if _os.name == "nt":
                import msvcrt
                while True:
                    try:
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if _monotonic() >= deadline:
                            raise TimeoutError(f"文件锁等待超时：{lock_path}") from None
                        _sleep(0.05)
            else:
                import fcntl
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if _monotonic() >= deadline:
                            raise TimeoutError(f"文件锁等待超时：{lock_path}") from None
                        _sleep(0.05)
            yield
        finally:
            try:
                if _os.name == "nt":
                    import msvcrt
                    _os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            _os.close(fd)
    return _ctx()


def _monotonic():
    import time as _t
    return _t.monotonic()


def _sleep(sec):
    import time as _t
    _t.sleep(sec)


# ============================ 参数校验辅助（D4） ============================
# 收敛各工具手写的 try:int/except 三段式，统一语义：
# 非法/缺省 → 默认值；显式合法 → 生效。返回类型稳定。
def _env_flag(name, default=False):
    """布尔开关：WHALETALK_<NAME>；'0/false/no/off/空' = False。"""
    try:
        raw = os.environ.get("WHALETALK_" + name)
        if raw is None or str(raw).strip() == "":
            return default
        return str(raw).strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        return default


def clamp_int(value, default, lo=None, hi=None):
    """安全整数：非法值/None → default；越界 → 钳制到 [lo, hi]。

    WHALETALK_NO_CLAMP=1 时**跳过上下限钳制**（仍做类型归一，非法值回 default）——
    是否设限由用户决定，不因个别实验替模型定死范围。
    """
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = int(default)
    if _env_flag("NO_CLAMP"):
        return v
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def clamp_float(value, default, lo=None, hi=None):
    """安全浮点：非法值/None → default；越界 → 钳制到 [lo, hi]。WHALETALK_NO_CLAMP=1 时不钳制。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float(default)
    if _env_flag("NO_CLAMP"):
        return v
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def clamp_str(value, default="", max_len=None):
    """安全字符串：None → default；可选截断上限。WHALETALK_NO_CLAMP=1 时不截断。"""
    s = str(value) if value is not None else str(default)
    if not _env_flag("NO_CLAMP") and max_len is not None and len(s) > max_len:
        s = s[:max_len]
    return s


def over_limit(value, limit):
    """统一「上限」判定：limit<=0/None 表示**不限**，恒返回 False。

    把「0=不限」契约落到实处——避免各工具写成 `x > LIMIT` 导致 0 反而全拦死。
    """
    try:
        return bool(limit and limit > 0 and value > limit)
    except TypeError:
        return False


def split_list(value, sep=",", dedup=False):
    """安全列表：字符串按分隔符拆分 / 已有列表直通 / None → []，strip 空项。"""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = [str(x).strip() for x in value]
    else:
        items = [x.strip() for x in str(value).split(sep)]
    items = [x for x in items if x]
    if dedup:
        seen, out = set(), []
        for x in items:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out
    return items


# ============================ 本地路径正则 ============================
# 形如 C:\path\file 的 Windows 绝对路径（文件/目录均可命中）
PATH_RE = re.compile(
    r"[A-Za-z]:[\\/][^\s'\"()<>|,;（）【】《》，。、；：？！]+"
)

# ============================ Windows OCR 脚本 ============================
# 占位符用 @PATH@ 而非 $path：用户路径若含字面 "$path" 会被顺序 replace 二次替换污染
OCR_IMAGE_PS = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync('@PATH@')) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { '当前系统语言不支持 OCR' } else {
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $result.Text
}
""".lstrip()

# ============================ 工具域阈值与锁（基础工具域（日期/天气）） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

WEATHER_TIMEOUT = _env_int("WEATHER_TIMEOUT", 5)


# ============================ 工具域阈值与锁（编程与执行域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

# ===== run_python 执行模式（v3.9+ 无限制）=====
# run_python 与直接运行 python -c 等价——不隔离、不静态拦截，可加载全部
# 已安装库、访问网络、调用系统能力。信任用户与模型，不再内置任何拦截。

# 同步执行超时（秒）。10s 只够"片段计算"，装包/下载/跑测试/启浏览器等真实
# 编程主体都会超时被 kill。调大到 60s 覆盖多数交互需求（< 并行批总超时 300s 留余量）；
# 更长的任务不应靠调大同步超时（会占线程/撞总超时），而应走后台通道：
# 用 start_process 无超时启动 + list_processes 轮询 / stop_process 停止。
RUN_PY_TIMEOUT = _env_int("RUN_PY_TIMEOUT", 60)

# 不再对 run_python 源码长度设人为上限（旧值 8000 会逼迫 AI 分块写入）。
# 真实上限由模型上下文与请求体大小（api_server.MAX_BODY=1MB）决定；
# 执行改走临时文件（python <file>），规避 Windows 命令行 ~32K 的隐性截断。

RUN_PY_MAX_OUTPUT = _env_int("RUN_PY_MAX_OUTPUT", 20000)

# run_python 内存上限（MB）：超限杀进程树并如实报错。这不是沙箱（策略仍是默认自由、
# 不做静态拦截），而是防误伤的兜底——一次失控分配（死循环里 append / 读超大文件进内存）
# 会连带影响用户整机体验。2GB 足够常见数据分析；更重的任务请走 start_process 后台通道。
RUN_PY_MEMORY_MB = _env_int("RUN_PY_MEMORY_MB", 2048)

# 内存看门狗轮询间隔（秒）：越小越及时、开销越大
RUN_MEM_POLL_SEC = _env_int("RUN_MEM_POLL_SEC", 0.5)

# 工具结果"失败"前缀统一判定（main/taskpanel 共享，防散落魔法字符串漂移）
# 注意：deepseek_client / api_server 对工具异常会包成「工具执行失败:」「工具参数错误:」
# 「工具参数解析失败:」——必须一并纳入，否则这些失败会被失败记忆/记账判成成功。
TOOL_RESULT_FAIL_PREFIXES = (
    "错误", "权限拒绝", "超时", "（用户停止", "工具执行失败", "工具参数",
    # 工具「未达成目标」的口径（此前这些返回被判成成功，失败记忆/自动消解失真）：
    "未能", "未识别", "未听到", "未检测到", "未能定位",
)

# ── 进程退出码的「语义」判定（run_python / run_command 共用）──────────────
# 问题（真实失败记录）：run_command 此前把**任何非零退出码**都报成
# `错误：命令以退出码 N 结束（执行失败）`。但很多命令用非零退出表达**正常语义**：
#   ruff / pytest / grep / diff / git diff --exit-code 等，非零 = "发现了问题"，
# 命令本身跑得好好的。一律报「错误」既误导模型（以为工具坏了、改道重试），
# 又因前缀命中 TOOL_RESULT_FAIL_PREFIXES 而污染失败记忆与自动消解。
#
# 这里给出统一口径：
#   - 有可识别输出 → 命令**跑起来了**，按「退出码 N」中性报告（不冠「错误：」），
#     让模型读输出自行判断；这既符合 Unix 惯例，也不再污染失败记账。
#   - 空输出 **且** 非零退出 → 才可能是真失败（命令不存在/路径错/语法错），
#     给中性前缀 + 可操作诊断（而不是干巴巴的「（无输出）」）。
# 全角/半角冒号都接受，调用方用 startswith 判定。
_EXIT_SEMANTIC_PREFIX = "命令退出码"


def is_tool_failure(result):
    """统一判定工具结果是否算「失败」（供失败记忆 / 自动消解 / 记账）。

    与旧逻辑一致：命中 TOOL_RESULT_FAIL_PREFIXES 即失败。集中一处便于演进。
    """
    s = str(result or "").lstrip()
    return any(s.startswith(p) for p in TOOL_RESULT_FAIL_PREFIXES)


def format_process_result(rc, output, *, kind="命令", workspace=None, timeout=None):
    """把子进程执行结果格式化为给模型看的文本（语义化，避免误报失败）。

    kind: "命令"（run_command）/ "脚本"（run_python）。
    返回文本规则：
      - 成功空输出        → "执行成功（无输出），退出码 0"
      - 成功有输出        → "退出码 0\\n<输出>"
      - 非零 + 有输出     → "命令退出码 N（命令已执行；非零常表示「发现了问题」，请读输出判断）\\n<输出>"
      - 非零 + 空输出     → "命令退出码 N 且无输出（可能未真正执行）" + 可操作诊断
    workspace 非空时附工作目录；timeout 非空时提示超时设置。
    """
    body = str(output or "").strip()
    suffix = f"\n[工作目录：{workspace or '（当前目录）'}]" if workspace else ""
    if rc in (0, None):
        if not body:
            return f"执行成功（无输出），退出码 {rc}{suffix}"
        return f"退出码 {rc}\n{body}{suffix}"
    if body:
        return (f"{_EXIT_SEMANTIC_PREFIX} {rc}（{kind}已执行；非零退出常表示「发现了问题」"
                f"——如 ruff/pytest/grep/diff，请读输出判断，未必是执行失败）\n{body}{suffix}")
    if kind == "脚本":
        # 脚本非零退出且无输出：多半是脚本自身调用 sys.exit(非0) 或异常被吞；
        # 不能说"没真正执行"（脚本确实跑了），只提示核对用途。
        return (f"{_EXIT_SEMANTIC_PREFIX} {rc}（脚本已执行但无输出；若脚本本应打印结果，"
                f"请检查是否提前 sys.exit、异常被 try 吞掉、或 stdout 未 flush）{suffix}")
    hints = [
        f"{_EXIT_SEMANTIC_PREFIX} {rc} 且**无输出**：{kind}很可能没有真正执行成功。",
        "常见原因与处置：",
        "  · 命令/程序不存在或不在 PATH（如 Windows 无 tail/head/sed、无 ls 的 -l 风格）"
        "→ 改用跨平台写法或本机存在的命令；",
        "  · 路径不存在或含未转义空格/中文 → 用引号包裹完整路径；",
        "  · shell 语法差异（Windows 是 cmd，不是 bash）：&&/|| 可用，"
        "但 $VAR、反引号、单引号语义不同；",
        "  · 依赖缺失 / 编码问题 → 先跑该命令的 --version 确认可用。",
        "若确实需要查看原因，可先 `python -c \"import shutil;print(shutil.which('<程序名>'))\"` 确认程序是否存在。",
    ]
    return "\n".join(hints) + suffix


# 长任务自动打点（G15）：工具链够长就自动落一个断点，别让结论只活在对话里
# （崩溃/断电即失）。阈值内不写盘，避免每步都做 IO。
AUTO_CHECKPOINT_TOOLS = 8    # 链长达到该值后开始自动打点
AUTO_CHECKPOINT_EVERY = 5    # 之后每 N 步补一次（覆盖更长的任务）


# ============================ 工具域阈值与锁（文件与进程域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

READ_FILE_MAX_BYTES = _env_int("READ_FILE_MAX_BYTES", 102400)

READ_LINE_MAX = _env_int("READ_LINE_MAX", 102400)  # 按行读取的每行上限（防单行数百 MB 撑爆内存）

EDIT_FILE_MAX_SIZE = _env_int("EDIT_FILE_MAX_SIZE", 20 * 1024 * 1024)  # edit_file 全量读入上限（20MB）

EDIT_FILE_REGEX_MAX = _env_int("EDIT_FILE_REGEX_MAX", 1000)  # 正则长度上限（防灾难性回溯挂死工具线程的粗略防线）

# ── 编码护栏：代码文件里的「省略占位」检测（write_file / write_code_project 共用）──
# 只看强信号，宁可少报也不误伤散文/日志里的 "..."；仅对代码类扩展名生效。
CODE_EXTS = frozenset({
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".vue", ".svelte",
    ".java", ".kt", ".go", ".rs", ".c", ".cc", ".cpp", ".h", ".hpp", ".cs", ".rb",
    ".php", ".swift", ".scala", ".lua", ".r", ".m", ".sql", ".sh", ".bash", ".ps1",
    ".bat", ".cmd", ".html", ".htm", ".css", ".scss", ".less", ".json", ".yaml", ".yml",
})

# 注释/中文省略话术：所有代码文件都判（"# ... 省略"、"// 其余不变" 等）
_PLACEHOLDER_COMMENT_PATTERNS = (
    re.compile(r"^[ \t]*(#|//|/\*|\*|<!--)[ \t]*(\.\.\.|…|省略|其余不变|其余代码不变|此处省略)", re.M),
    re.compile(r"(省略若干|其余代码不变|其余不变|此处省略|（略|\(略)"),
)
# 裸省略行：Python 的 `...` 是合法 Ellipsis（stub/Protocol 常用），不判；
# 其余语言里孤零零一行 .../… 基本就是"省略"，判。
_PLACEHOLDER_BARE_LINE = re.compile(r"^[ \t]*(\.\.\.|…)[ \t]*$", re.M)


def find_code_placeholder(path, content):
    """代码文件内容里的省略占位检测，返回命中的片段（无则空串）。"""
    try:
        ext = os.path.splitext(str(path))[1].lower()
    except Exception:
        return ""
    if ext not in CODE_EXTS:
        return ""
    s = str(content)
    for pat in _PLACEHOLDER_COMMENT_PATTERNS:
        m = pat.search(s)
        if m:
            return m.group(0).strip()[:40]
    if ext not in (".py", ".pyi"):
        m = _PLACEHOLDER_BARE_LINE.search(s)
        if m:
            return m.group(0).strip()[:40]
    return ""

EXTRACT_MAX_ENTRIES = _env_int("EXTRACT_MAX_ENTRIES", 10000)  # 解压条目数上限（防 zip 海量小文件 DoS）

EXTRACT_MAX_TOTAL_BYTES = _env_int("EXTRACT_MAX_TOTAL_BYTES", 2 * 1024 * 1024 * 1024)  # 解压总字节上限（防磁盘写满）

EXTRACT_MAX_SINGLE_BYTES = _env_int("EXTRACT_MAX_SINGLE_BYTES", 2 * 1024 * 1024 * 1024)  # 单文件解压大小上限

MAX_PROCESSES = _env_int("MAX_PROCESSES", 8)

_COMMON_PACKAGES = (
    "flask", "django", "fastapi", "uvicorn", "requests", "bs4", "pandas",
    "numpy", "matplotlib", "playwright", "docx", "pytest", "httpx",
    "openai", "tiktoken", "pillow", "tqdm", "yaml", "jinja2",
)

_ARCHIVE_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "build"}

_SEARCH_EXTS = (
    ".py", ".md", ".txt", ".json", ".html", ".css", ".js", ".ts",
    ".yaml", ".yml", ".csv", ".log", ".ini", ".cfg", ".toml",
)

_SEARCH_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", "dist", "build"}


# ============================ 工具域阈值与锁（记忆与定时任务域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

MEMORY_MAX_ITEMS = _env_int("MEMORY_MAX_ITEMS", 2000)  # v2.16.2 起扩容：伙伴需要记住的更多

MEMORY_MAX_TEXT = _env_int("MEMORY_MAX_TEXT", 2000)

_MEMORY_LOCK = threading.Lock()  # 并行 write_memory 读-改-写串行化，防丢失更新

SELF_PROFILE_LOCK = threading.Lock()

_SELF_PROFILE_LIST_FIELDS = ("preferences", "goals", "milestones", "user_model", "history", "wishes")

SCHEDULES_LOCK = threading.Lock()  # 与 main 的定时任务面板共享（防并发覆盖）

_WORKFLOW_LOCK = threading.Lock()  # 检查-置位原子化：并行工具调用下防双流程同时启动


# ============================ 工具域阈值与锁（浏览器与网页域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

# ===== 二进制下载（P2）：图片/附件/安装包等任意文件 =====（单文件上限，防全量进内存）

DOWNLOAD_MAX_BYTES = _env_int("DOWNLOAD_MAX_BYTES", 200 * 1024 * 1024)  # 单文件 200MB 上限（与 WebDAV 对齐）

SEARCH_MAX_RESULTS = _env_int("SEARCH_MAX_RESULTS", 5)

# 搜索聚合整体软超时（秒）：并行等引擎时，慢引擎（如 DDG 挂起）不得拖垮整次
# 搜索——到点即返回已返回的引擎结果，未返回的按超时记入健康电路。
SEARCH_SOFT_DEADLINE = 5.0

# 搜索引擎注册表：(名称, 质量权重)。调用方经 globals() 动态查找 _search_<名称>；
# 权重决定聚合输出顺序（数值大的优先展示）。bing/so360 国内稳定；duckduckgo
# 时好时坏（健康度机制自动跳过）；baidu/sogou/yandex 反爬；google 不可达。

_SEARCH_ENGINES = (
    ("bing", 3),
    ("so360", 2),
    ("duckduckgo", 1),
)

CALL_API_MAX_BYTES = _env_int("CALL_API_MAX_BYTES", 500 * 1024)  # 响应体上限 500KB（与 fetch_url 输出对齐）

CALL_API_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD")

CALL_API_MAX_HEADERS = _env_int("CALL_API_MAX_HEADERS", 16)

RSS_FETCH_TIMEOUT = _env_int("RSS_FETCH_TIMEOUT", 10)

RSS_MAX_ITEMS = _env_int("RSS_MAX_ITEMS", 20)

RSS_SUMMARY_MAX = _env_int("RSS_SUMMARY_MAX", 300)

# 精选 RSS 预置源（action=preset 一键添加）：中文 AI/科技/开发者为主

RSS_PRESET_SOURCES = [
    {"name": "机器之心", "url": "https://www.jiqizhixin.com/rss"},
    {"name": "量子位", "url": "https://www.qbitai.com/feed"},
    {"name": "少数派", "url": "https://sspai.com/feed"},
    {"name": "IT之家", "url": "https://www.ithome.com/rss/"},
    {"name": "开源中国", "url": "https://www.oschina.net/news/rss"},
    {"name": "Hacker News", "url": "https://news.ycombinator.com/rss"},
]

WEBDAV_MAX_SIZE = _env_int("WEBDAV_MAX_SIZE", 200 * 1024 * 1024)  # 单文件 200MB 上限（防全量进内存）


# ============================ 工具域阈值与锁（桌面与视觉语音域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

RPA_FAILSAFE = True  # 鼠标移到屏幕左上角时立即中断 RPA（pyautogui failsafe）

MEDIA_MAX_INPUT = _env_int("MEDIA_MAX_INPUT", 2 * 1024 * 1024 * 1024)  # 输入 2GB 上限

MEDIA_FORMATS = {"mp4", "mp3", "webm", "mkv", "avi", "mov", "ogg", "flac", "wav", "gif", "png", "jpg"}

_VISION_LOOP_ACTIONS = ("done", "click", "type", "scroll", "describe")

_BYE_PAT = ("再见", "拜拜", "停止对话", "结束对话", "退下吧", "goodbye", "bye-bye")

_TEAM_ROLE_PRESETS = {
    "研究员": "资料搜集与事实核查专家：给出结论时尽量带依据与出处线索。",
    "工程师": "资深工程师：给出可直接落地的方案、代码或命令，注重边界情况。",
    "评审": "苛刻的技术评审：找漏洞、提风险、给改进清单。",
    "设计师": "体验设计师：关注交互、可用性与呈现结构，给出具体设计建议。",
    "分析师": "数据/商业分析师：拆解量化指标，给出决策建议。",
}


# ============================ 工具域阈值与锁（系统与项目域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))

PROJECT_READ_EXTS = (".py", ".md", ".json", ".txt", ".bat", ".html")

EVO_WRITE_EXTS = (".py", ".md", ".json", ".txt", ".html")

# Windows Toast 脚本：占位符 @TITLE@/@BODY@（非 $title/$body，防用户内容含字面
# "$body" 被顺序 replace 二次污染）；@DURATION@/@SILENT@ 由 notify_desktop 注入。

_NOTIFY_PS = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$textNodes = $template.GetElementsByTagName("text")
$textNodes.Item(0).AppendChild($template.CreateTextNode('@TITLE@')) | Out-Null
$textNodes.Item(1).AppendChild($template.CreateTextNode('@BODY@')) | Out-Null
$template.DocumentElement.SetAttribute('duration', '@DURATION@') | Out-Null
$audio = $template.CreateElement('audio')
$audio.SetAttribute('silent', '@SILENT@')
$template.DocumentElement.AppendChild($audio) | Out-Null
$toast = New-Object Windows.UI.Notifications.ToastNotification $template
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("鲸语 WhaleTalk").Show($toast)
"""


# ============================ 工具域阈值与锁（数据与文档域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

# ===== 文档处理域：PDF 提取 / PDF 生成 / Word 读取 / PPT 读取（可选依赖模式）=====
# S14：Office 读出工具统一长度上限族。目标：长文档/大表一次性灌入上下文前先截断，
# 由各工具在返回末尾追加 "[已截断前 N 字符/行]" 提示，保证 AI 不会被单文件撑爆。

PDF_EXTRACT_MAX_OUTPUT = _env_int("PDF_EXTRACT_MAX_OUTPUT", 60000)  # pdf_extract 单次输出上限（防撑爆上下文）

DOCX_MAX_DEFAULT = _env_int("DOCX_MAX_DEFAULT", 50000)  # docx_read 默认输出上限（clamp 200..500000）

PPTX_MAX_DEFAULT = _env_int("PPTX_MAX_DEFAULT", 50000)  # pptx_read 默认输出上限（S14 补齐：此前无全局上限）
PPTX_MAX_PAGE_BODY = _env_int("PPTX_MAX_PAGE_BODY", 40)  # pptx_read 每页正文行数上限
PPTX_MAX_NOTES = _env_int("PPTX_MAX_NOTES", 500)  # pptx_read 每页备注字符上限

TABLE_READ_MAX_ROWS = _env_int("TABLE_READ_MAX_ROWS", 500)  # read_excel/read_csv 行数上限（clamp_int hi）

# 表格单元格显示/输出截断上限（工具输出与前端渲染共用同一来源）；0 = 不限
TABLE_CELL_MAX = _env_int("TABLE_CELL_MAX", 100)

# ===== 嵌入式 KV 存储（diskcache 可选依赖；支持 TTL 与模糊检索）=====

KV_VALUE_MAX_BYTES = _env_int("KV_VALUE_MAX_BYTES", 1024 * 1024)  # value 上限 1MB
