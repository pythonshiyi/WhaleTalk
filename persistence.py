"""持久化工具：原子 JSON 写入。

从 main.py 中拆出，供配置/快照/会话/统计等高频落盘路径复用。
"""
import json
import logging
import os
import tempfile
import threading
import time

# ── 并发写同一路径的进程内串行化 ──────────────────────────────────────
# 真实缺陷（并发压测复现）：同进程多线程写**同一路径**时，`os.replace(tmp, path)`
# 会在 Windows 抛 `PermissionError: [WinError 5] 拒绝访问`——Windows 的替换语义与
# POSIX 不同，两个线程同时替换同一目标会撞车。压测实测 160 次写失败 27 次（17%），
# 调用方若未在**外部**加锁（很多落盘路径没有），就会静默丢写。
# 修法：按目标路径加进程内排他锁（同一路径串行，不同路径并行，不损失并发度）。
_PATH_LOCKS = {}
_PATH_LOCKS_GUARD = threading.Lock()
_REPLACE_RETRIES = 5          # os.replace 瞬时失败重试次数
_REPLACE_BACKOFF = 0.02       # 基础退避（秒），线性递增


def _path_lock(path):
    """取某路径专属的锁（懒建；进程内单例）。"""
    key = os.path.normcase(os.path.abspath(path))
    with _PATH_LOCKS_GUARD:
        lk = _PATH_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _PATH_LOCKS[key] = lk
        return lk


def _replace_with_retry(tmp, path):
    """os.replace，带瞬时失败重试（跨进程占用/杀毒扫描等 Windows 瞬时占用）。"""
    last = None
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, path)
            return True
        except PermissionError as e:  # WinError 5：目标被占用/并发替换
            last = e
            time.sleep(_REPLACE_BACKOFF * (attempt + 1))
    if last is not None:
        raise last
    return False


def atomic_json_write(path, data, indent=1, compact=False):
    """原子写 JSON（唯一临时文件 + os.replace），失败返回 False。

    compact=True 使用紧凑分隔符（快照/会话等大文件体积减半，读写更快）。
    并发安全：同一路径的写入进程内串行化 + os.replace 瞬时失败重试——
    Windows 上并发替换同一目标会抛 WinError 5（实测 17% 失败率）。
    """
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=os.path.dirname(path) or ".",
            prefix=os.path.basename(path) + ".",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                if compact:
                    json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
                else:
                    json.dump(data, f, ensure_ascii=False, indent=indent)
            # 同一路径串行化后再替换：避免进程内并发撞车（不同路径互不阻塞）
            with _path_lock(path):
                _replace_with_retry(tmp, path)
            return True
        finally:
            # replace 成功则 tmp 已不存在；写入异常或 replace 失败（跨盘/被占用）时清理，
            # 避免临时文件泄漏（此前只在序列化异常时清理）。
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except Exception:
        logging.exception("原子 JSON 写入失败: %s", path)
        return False

