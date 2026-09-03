# -*- coding: utf-8 -*-
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
            l, _, r = part.partition("-")
            lv, rv = cron_int(l), cron_int(r)
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
                            raise TimeoutError(f"文件锁等待超时：{lock_path}")
                        _sleep(0.05)
            else:
                import fcntl
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if _monotonic() >= deadline:
                            raise TimeoutError(f"文件锁等待超时：{lock_path}")
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
def clamp_int(value, default, lo=None, hi=None):
    """安全整数：非数值/None → default；越界 → 钳制到 [lo, hi]。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        v = int(default)
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def clamp_float(value, default, lo=None, hi=None):
    """安全浮点数：非数值/None → default；越界 → 钳制到 [lo, hi]。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float(default)
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def clamp_str(value, default="", max_len=None):
    """安全字符串：None → default；可选截断上限。"""
    s = str(value) if value is not None else str(default)
    if max_len is not None and len(s) > max_len:
        s = s[:max_len]
    return s


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

WEATHER_TIMEOUT = 5


# ============================ 工具域阈值与锁（编程与执行域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

# ===== run_python 执行模式（v3.9+ 无限制）=====
# run_python 与直接运行 python -c 等价——不隔离、不静态拦截，可加载全部
# 已安装库、访问网络、调用系统能力。信任用户与模型，不再内置任何拦截。

RUN_PY_TIMEOUT = 10

RUN_PY_MAX_CHARS = 8000

RUN_PY_MAX_OUTPUT = 20000

# 工具结果"失败"前缀统一判定（main/taskpanel 共享，防散落魔法字符串漂移）

TOOL_RESULT_FAIL_PREFIXES = ("错误", "权限拒绝", "超时", "（用户停止")


# ============================ 工具域阈值与锁（文件与进程域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

READ_FILE_MAX_BYTES = 102400

_READ_LINE_MAX = 102400  # 按行读取的每行上限（防单行数百 MB 撑爆内存）

EDIT_FILE_MAX_SIZE = 20 * 1024 * 1024  # edit_file 全量读入上限（20MB）

EDIT_FILE_REGEX_MAX = 1000  # 正则长度上限（防灾难性回溯挂死工具线程的粗略防线）

EXTRACT_MAX_ENTRIES = 10000  # 解压条目数上限（防 zip 海量小文件 DoS）

EXTRACT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 解压总字节上限（防磁盘写满）

EXTRACT_MAX_SINGLE_BYTES = 2 * 1024 * 1024 * 1024  # 单文件解压大小上限

MAX_PROCESSES = 8

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

MEMORY_MAX_ITEMS = 2000  # v2.16.2 起扩容：伙伴需要记住的更多

MEMORY_MAX_TEXT = 2000

_MEMORY_LOCK = threading.Lock()  # 并行 write_memory 读-改-写串行化，防丢失更新

SELF_PROFILE_LOCK = threading.Lock()

_SELF_PROFILE_LIST_FIELDS = ("preferences", "goals", "milestones", "user_model", "history", "wishes")

SCHEDULES_LOCK = threading.Lock()  # 与 main 的定时任务面板共享（防并发覆盖）

_WORKFLOW_LOCK = threading.Lock()  # 检查-置位原子化：并行工具调用下防双流程同时启动


# ============================ 工具域阈值与锁（浏览器与网页域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

# ===== 二进制下载（P2）：图片/附件/安装包等任意文件 =====（单文件上限，防全量进内存）

DOWNLOAD_MAX_BYTES = 200 * 1024 * 1024  # 单文件 200MB 上限（与 WebDAV 对齐）

SEARCH_MAX_RESULTS = 5

# 搜索引擎注册表：(名称, 质量权重)。调用方经 globals() 动态查找 _search_<名称>；
# 权重决定聚合输出顺序（数值大的优先展示）。bing/so360 国内稳定；duckduckgo
# 时好时坏（健康度机制自动跳过）；baidu/sogou/yandex 反爬；google 不可达。

_SEARCH_ENGINES = (
    ("bing", 3),
    ("so360", 2),
    ("duckduckgo", 1),
)

CALL_API_MAX_BYTES = 500 * 1024  # 响应体上限 500KB（与 fetch_url 输出对齐）

CALL_API_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD")

CALL_API_MAX_HEADERS = 16

RSS_FETCH_TIMEOUT = 10

RSS_MAX_ITEMS = 20

RSS_SUMMARY_MAX = 300

# 精选 RSS 预置源（action=preset 一键添加）：中文 AI/科技/开发者为主

RSS_PRESET_SOURCES = [
    {"name": "机器之心", "url": "https://www.jiqizhixin.com/rss"},
    {"name": "量子位", "url": "https://www.qbitai.com/feed"},
    {"name": "少数派", "url": "https://sspai.com/feed"},
    {"name": "IT之家", "url": "https://www.ithome.com/rss/"},
    {"name": "开源中国", "url": "https://www.oschina.net/news/rss"},
    {"name": "Hacker News", "url": "https://news.ycombinator.com/rss"},
]

WEBDAV_MAX_SIZE = 200 * 1024 * 1024  # 单文件 200MB 上限（防全量进内存）


# ============================ 工具域阈值与锁（桌面与视觉语音域） ============================
# P1-3 下沉：原 deepseek_client 顶部常量/锁，统一归口 shared（dc 顶部 re-export 兼容旧路径）。
# 新增工具域阈值常量请添加在此处，勿回写主文件。

RPA_FAILSAFE = True  # 鼠标移到屏幕左上角时立即中断 RPA（pyautogui failsafe）

MEDIA_MAX_INPUT = 2 * 1024 * 1024 * 1024   # 输入 2GB 上限

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

PDF_EXTRACT_MAX_OUTPUT = 60000   # pdf_extract 单次输出上限（防撑爆上下文）

DOCX_MAX_DEFAULT = 50000         # docx_read 默认输出上限

# ===== 嵌入式 KV 存储（diskcache 可选依赖；支持 TTL 与模糊检索）=====

KV_VALUE_MAX_BYTES = 1024 * 1024  # value 上限 1MB
