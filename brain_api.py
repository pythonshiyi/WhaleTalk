# -*- coding: utf-8 -*-
"""鲸语大脑 · 前端 API 适配层。

把 brainkit 的 CLI 命令包装为 api_server 可调用的纯函数：
- brain_status()      → 状态 dict（前端展示用）
- brain_action(...)   → 执行 mount/unmount/heartbeat/archive/export-key/import-key/restore
返回统一结构 {"ok": bool, "message": str, "data": ...}。
"""
import argparse
import base64
import io
import os
import sys
import tempfile
import time
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import brainkit as bk  # noqa: E402


def _run(func, **kwargs):
    """执行 brainkit 命令函数，捕获其打印输出，返回 (exit_code, output)。"""
    buf = io.StringIO()
    ns = argparse.Namespace(**kwargs)
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            code = func(ns)
        return (code if isinstance(code, int) else 0), buf.getvalue().strip()
    except SystemExit as e:
        return (e.code if isinstance(e.code, int) else 1), buf.getvalue().strip()
    except Exception as e:  # noqa: BLE001
        return 1, (buf.getvalue() + f"\n[异常] {e}").strip()


def brain_status():
    """大脑状态 dict；未初始化返回 None。"""
    try:
        m = bk.load_manifest()
    except SystemExit:
        return None
    hb = bk.load_json(bk.BRAIN_DIR / "heartbeat.json", {})
    ident = bk.load_json(bk.BRAIN_DIR / "identity.json", {})
    mem_count = sum(1 for _ in bk.MEMORIES_DIR.glob("*.md")) if bk.MEMORIES_DIR.exists() else 0
    think_files = sum(1 for _ in bk.THINKING_DIR.glob("*.md")) if bk.THINKING_DIR.exists() else 0
    versions = sorted(bk.ARCHIVE_DIR.glob("brain_v*.whale")) if bk.ARCHIVE_DIR.exists() else []
    conflicts = bk.load_json(bk.MERGE_CONFLICT_FILE, {})
    lineage = bk.load_json(bk.LINEAGE_FILE, {})
    return {
        "brain_id": m.get("brain_id"),
        "fingerprint_ok": bk.verify_fingerprint(m),
        "fingerprint": str(m.get("fingerprint", ""))[:16] + "…",
        "name": ident.get("name") or "未命名",
        "vessel": ident.get("vessel") or "",
        "keyring": bk._keyring_ready(),
        "pubkey": m.get("pubkey_fingerprint"),
        "memories": mem_count,
        "thinking_days": think_files,
        "last_mount": hb.get("last_mount"),
        "last_unmount": hb.get("last_unmount"),
        "resume_hint": hb.get("resume_hint"),
        "lineage": lineage,
        "open_conflicts": len(conflicts.get("conflicts", [])) if conflicts else 0,
        "snapshots": [
            {
                "name": v.name,
                "version": int(v.stem.rsplit("_v", 1)[-1]),
                "size_kb": round(v.stat().st_size / 1024, 1),
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(v.stat().st_mtime)),
            }
            for v in versions
        ],
        "dir": str(bk.BRAIN_DIR),
    }


def brain_action(action, payload=None):
    """执行大脑管理动作。payload 为前端传入的 dict。"""
    payload = payload or {}
    if action == "mount":
        code, out = _run(bk.cmd_mount, force=False)
        return {"ok": code == 0, "message": out}
    if action == "unmount":
        code, out = _run(bk.cmd_unmount,
                         thought=str(payload.get("thought") or ""),
                         archive=False, passphrase="", keep=bk.DEFAULT_KEEP)
        return {"ok": code == 0, "message": out}
    if action == "heartbeat":
        code, out = _run(bk.cmd_heartbeat, thought=str(payload.get("thought") or ""))
        return {"ok": code == 0, "message": out}
    if action == "archive":
        code, out = _run(bk.cmd_archive, passphrase=str(payload.get("passphrase") or ""), keep=bk.DEFAULT_KEEP)
        return {"ok": code == 0, "message": out}
    if action == "export-key":
        pw = str(payload.get("passphrase") or "").strip()
        auto_pw = ""
        if not pw:
            auto_pw = base64.b64encode(os.urandom(6)).decode("ascii")[:8]
            pw = auto_pw
        fd, tmp_name = tempfile.mkstemp(suffix=".whale")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            code, out = _run(bk.cmd_export_key, out=str(tmp), passphrase=pw)
            data_b64 = ""
            if tmp.exists():
                data_b64 = base64.b64encode(tmp.read_bytes()).decode("ascii")
            return {
                "ok": code == 0,
                "message": out,
                "data": {
                    "download": {"filename": "brain_seed.whale", "data_b64": data_b64},
                    "auto_passphrase": auto_pw,
                },
            }
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    if action == "import-key":
        file_b64 = str(payload.get("file_b64") or "")
        pw = str(payload.get("passphrase") or "")
        if not file_b64 or not pw:
            return {"ok": False, "message": "缺少密钥包文件或一次性口令"}
        fd, tmp_name = tempfile.mkstemp(suffix=".whale")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            tmp.write_bytes(base64.b64decode(file_b64))
            code, out = _run(bk.cmd_import_key, seed=str(tmp), passphrase=pw)
            return {"ok": code == 0, "message": out}
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    if action == "restore":
        version = payload.get("version")
        snap = bk.ARCHIVE_DIR / f"brain_v{version}.whale"
        if not snap.exists():
            return {"ok": False, "message": f"找不到快照 brain_v{version}.whale"}
        code, out = _run(bk.cmd_restore,
                         whale=str(snap), passphrase=str(payload.get("passphrase") or ""),
                         dir=None, replace=bool(payload.get("replace", True)), force=False)
        return {"ok": code == 0, "message": out}
    return {"ok": False, "message": f"未知动作: {action}"}
