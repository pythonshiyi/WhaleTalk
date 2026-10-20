"""原子 JSON 写入（Windows 友好）。

用一个独立的临时文件 + 重试 ``os.replace``：Windows 上目标文件可能正被
另一个线程/防病毒软件短暂打开，固定 `.tmp` 名还会让两个写者互相踩。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


def write_json(path, obj, *, indent=2) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="." + path.stem + "-", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=indent)
            f.write("\n")
        last = None
        for _ in range(8):
            try:
                os.replace(tmp, path)
                return path
            except PermissionError as e:  # Windows: 目标正被短暂打开
                last = e
                time.sleep(0.02)
        if last is not None:
            raise last
        return path
    finally:
        try:
            tmp.unlink(missing_ok=True)
        # noqa: silent-except: 临时文件清理失败不掩盖原始写入异常
        except OSError:
            pass
