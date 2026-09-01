# -*- coding: utf-8 -*-
"""跨模块共享的纯函数与常量（无 GUI / 无 API 依赖）。

从 main.py / deepseek_client.py / taskpanel.py 中抽取，消除重复实现漂移：
- cron 5 字段表达式引擎（校验 / 匹配）
- 本地绝对路径正则 PATH_RE
- Windows OCR PowerShell 脚本（占位符统一 @PATH@，避免与 $ 变量名冲突）
"""
import re
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
