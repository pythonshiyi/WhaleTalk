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
import shutil
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
    context_preview = None
    try:
        context_preview = brain_context(max_memories=3)
    except Exception:  # noqa: BLE001
        pass
    return {
        "brain_id": m.get("brain_id"),
        "fingerprint_ok": bk.verify_fingerprint(m),
        "fingerprint": str(m.get("fingerprint", ""))[:16] + "…",
        "name": ident.get("name") or "未命名",
        "vessel": ident.get("vessel") or "",
        "nature": ident.get("nature") or "",
        "keyring": bk._keyring_ready(),
        "pubkey": m.get("pubkey_fingerprint"),
        "created_at": m.get("created_at"),
        "genesis": m.get("genesis"),
        "memories": mem_count,
        "thinking_days": think_files,
        "last_mount": hb.get("last_mount"),
        "last_unmount": hb.get("last_unmount"),
        "last_beat": hb.get("last_beat"),
        "resume_hint": hb.get("resume_hint"),
        "lineage": lineage,
        "open_conflicts": len(conflicts.get("conflicts", [])) if conflicts else 0,
        "current_version": int(versions[-1].stem.rsplit("_v", 1)[-1]) if versions else 0,
        "context_preview": context_preview,
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
    if action == "init":
        code, out = _run(bk.cmd_init, genesis=str(payload.get("genesis") or ""))
        if code == 0 and payload.get("enable_keyring"):
            code2, out2 = _run(bk.cmd_keyring_setup, force=False)
            out = out + "\n" + out2
        return {"ok": code == 0, "message": out}
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
                         dir=str(payload["dir"]) if payload.get("dir") else None,
                         replace=bool(payload.get("replace", True)), force=False)
        return {"ok": code == 0, "message": out}
    if action == "keyring-setup":
        code, out = _run(bk.cmd_keyring_setup, force=False)
        return {"ok": code == 0, "message": out}
    if action == "merge":
        a = _snapshot_path(payload.get("snap_a"))
        b = _snapshot_path(payload.get("snap_b"))
        if not a or not b:
            return {"ok": False, "message": "请选择两个快照（主干 A 与分支 B）"}
        out_dir = bk.MODULE_DIR / f"brain_merged-{time.strftime('%Y%m%d-%H%M%S')}"
        code, out = _run(bk.cmd_merge,
                         snap_a=a, snap_b=b,
                         strategy=str(payload.get("strategy") or "auto"),
                         dir=str(out_dir),
                         passphrase=str(payload.get("passphrase") or ""))
        return {
            "ok": code == 0,
            "message": out,
            "data": {"dir": str(out_dir), "conflicts": _load_conflicts(out_dir)},
        }
    if action == "merge-conflicts":
        d = Path(str(payload.get("dir") or bk.BRAIN_DIR))
        return {"ok": True, "data": {"conflicts": _load_conflicts(d), "dir": str(d)}}
    if action == "merge-resolve":
        cid = str(payload.get("id") or "")
        keep = str(payload.get("keep") or "")
        out_dir = str(payload.get("dir") or bk.BRAIN_DIR)
        if not cid or keep not in ("ours", "theirs", "both", "custom"):
            return {"ok": False, "message": "缺少冲突 id 或非法的裁决方式"}
        old = bk.BRAIN_DIR
        bk.set_brain_dir(Path(out_dir))
        try:
            code, out = _run(bk.cmd_merge_resolve, id=cid, keep=keep,
                             value=str(payload.get("value") or ""), dir=out_dir)
        finally:
            bk.set_brain_dir(old)
        return {"ok": code == 0, "message": out, "data": {"conflicts": _load_conflicts(out_dir)}}
    if action == "adopt-merge":
        src = Path(str(payload.get("dir") or ""))
        if not (src / "manifest.json").exists():
            return {"ok": False, "message": "合并目录不存在或不是有效大脑"}
        backup = bk.BRAIN_DIR.parent / f"brain.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copytree(bk.BRAIN_DIR, backup, dirs_exist_ok=True) if not backup.exists() else None
        for item in src.iterdir():
            if item.name in (".keys", "archive"):
                continue
            dst = bk.BRAIN_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        bk.save_json(bk.LINEAGE_FILE, {})
        bk.refresh_manifest_fingerprint()
        return {"ok": True, "message": f"已采纳合并结果作为当前大脑（旧大脑备份于 {backup.name}）"}
    if action == "cleanup":
        removed = []
        base = bk.MODULE_DIR
        keep_bak = max(0, int(payload.get("keep_bak", 1) or 0))
        for d in sorted(base.glob("brain_merged-*")) + sorted(base.glob("brain_restored-*")):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
        baks = sorted(base.glob("brain.bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for d in baks[keep_bak:]:
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
        return {"ok": True, "message": ("已清理 " + str(len(removed)) + " 个残留目录：" + "、".join(removed)) if removed else "没有需要清理的残留目录"}
    return {"ok": False, "message": f"未知动作: {action}"}


def _load_conflicts(d):
    c = bk.load_json(Path(d) / "merge_conflicts.json", {})
    return c.get("conflicts", []) if isinstance(c, dict) else []


def _snapshot_path(v):
    """版本号或路径 → 绝对快照路径；找不到返回空串（消除前端相对路径对 cwd 的依赖）。"""
    if v is None:
        return ""
    s = str(v).strip()
    if s.isdigit():
        p = bk.ARCHIVE_DIR / f"brain_v{s}.whale"
        return str(p) if p.exists() else ""
    if s.startswith("brain_v") and s.endswith(".whale"):
        p = bk.ARCHIVE_DIR / s
        return str(p) if p.exists() else ""
    p = Path(s)
    return str(p.resolve()) if p.exists() else str(p)


def brain_context(max_memories=4):
    """注入 AI 对话的大脑上下文摘要（身份 + 断点 + 近期记忆）；未初始化返回 None。"""
    try:
        bk.load_manifest()
    except SystemExit:
        return None
    ident = bk.load_json(bk.BRAIN_DIR / "identity.json", {})
    hb = bk.load_json(bk.BRAIN_DIR / "heartbeat.json", {})
    name = (ident.get("name") or "未命名").strip()
    lines = [f"[鲸语大脑] 我是「{name}」，一个可迁移、可备份、可恢复的思维容器。"]
    hint = str(hb.get("resume_hint") or "").strip()
    if hint:
        lines.append(f"上次思考断点：{hint}")
    recent = []
    try:
        files = sorted(bk.MEMORIES_DIR.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        for f in files[:3]:
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if s.startswith("-"):
                    recent.append(s[1:].strip())
    except Exception:
        pass
    if recent:
        lines.append("近期记忆：")
        lines += [f"- {r[:80]}" for r in recent[-max_memories:]]
    return "\n".join(lines)
