"""并发原子写回归（persistence.atomic_json_write）。

真实缺陷（并发压测复现）：同进程多线程写**同一路径**时，Windows 的 `os.replace`
抛 `PermissionError: [WinError 5]`——实测 160 次写失败 27 次（17%），调用方未在
外部加锁的落盘路径会静默丢写。修复：按路径进程内串行化 + os.replace 瞬时失败重试。

覆盖：同路径并发不丢写、不同路径仍并行、内容最终完整可解析、失败返回 False 契约。
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from persistence import atomic_json_write  # noqa: E402


def test_same_path_concurrent_no_loss(tmp_path):
    """多线程写同一路径：全部成功，无 PermissionError 丢写。"""
    p = str(tmp_path / "same.json")
    errors = []
    lock = threading.Lock()
    N_THREADS, N_EACH = 8, 25

    def writer(i):
        for k in range(N_EACH):
            ok = atomic_json_write(p, {"i": i, "k": k}, compact=True)
            if not ok:
                with lock:
                    errors.append(f"{i}-{k}")

    ths = [threading.Thread(target=writer, args=(i,)) for i in range(N_THREADS)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert errors == [], f"并发写同路径丢写 {len(errors)} 次: {errors[:5]}"
    # 最终文件必须是合法 JSON（未被并发截断）
    with open(p, encoding="utf-8") as f:
        json.load(f)


def test_distinct_paths_parallel_no_deadlock(tmp_path):
    """不同路径并发写：互不阻塞、全部成功（路径锁不应退化成全局锁）。"""
    errors = []

    def writer(i):
        p = str(tmp_path / f"p{i}.json")
        for k in range(20):
            if not atomic_json_write(p, {"i": i, "k": k}, compact=True):
                errors.append(i)

    ths = [threading.Thread(target=writer, args=(i,)) for i in range(8)]
    t0 = time.monotonic()
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    dt = time.monotonic() - t0
    assert errors == []
    assert dt < 10, f"不同路径并发耗时异常（疑似全局锁串行化）: {dt:.1f}s"


def test_content_integrity_after_concurrent(tmp_path):
    """并发写后内容完整：字段齐全，非半截。"""
    p = str(tmp_path / "c.json")

    def writer(i):
        for k in range(15):
            atomic_json_write(p, {"i": i, "k": k, "blob": "x" * 500}, compact=True)

    ths = [threading.Thread(target=writer, args=(i,)) for i in range(6)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    with open(p, encoding="utf-8") as f:
        d = json.load(f)
    assert set(d.keys()) >= {"i", "k", "blob"}
    assert len(d["blob"]) == 500


def test_creates_parent_dir(tmp_path):
    p = str(tmp_path / "a" / "b" / "c.json")
    assert atomic_json_write(p, {"ok": True}) is True
    assert os.path.isfile(p)


def test_returns_false_on_unserializable(tmp_path):
    """契约：不可序列化的对象返回 False（不抛出）。"""
    p = str(tmp_path / "bad.json")
    assert atomic_json_write(p, {"x": object()}) is False
