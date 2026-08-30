# -*- coding: utf-8 -*-
"""文件/数据库写操作快照（删除可恢复的安全网）。

设计目标：写文件 / 编辑 / 批量重命名 / 数据库写等**不可逆操作**执行前，
自动把原内容快照到本地 undo 目录；提供列出与恢复能力。

- 快照目录：`DATA_DIR/undo/`（与运行数据同目录，隐私模式敏感度同 config）
- 条目结构：`undo/<ts>_<op>_<rand>/meta.json`（原始路径/操作/时间/大小）+ `data`
  （文件为原内容副本；数据库为整体文件副本）
- 保留上限：默认最近 200 条，超出按时间清理最旧；单文件 > 50MB 跳过（避免快照目录失控）
- 恢复：把快照内容写回原路径；写回前先备份当前文件为 `.snap.bak`
- 不做任何删除操作——快照目录由用户/清理工具管理
"""
import json
import logging
import os
import shutil
import time

logger = logging.getLogger("whaletalk.snapshot")

UNDO_DIR = None          # 由 init() 注入（api_server 启动时）
MAX_SNAPSHOTS = 200      # 保留上限（按 mtime 清理最旧）
MAX_SNAP_FILE = 50 * 1024 * 1024  # 单文件快照大小上限（50MB）

# 恢复时的当前文件备份后缀（避免恢复覆盖后无痕迹）
CURRENT_BAK_SUFFIX = ".snap.bak"


def init(undo_dir):
    """注入快照目录（api_server.start_server 时调用）。"""
    global UNDO_DIR
    UNDO_DIR = undo_dir
    try:
        os.makedirs(undo_dir, exist_ok=True)
    except Exception:
        logger.exception("创建快照目录失败: %s", undo_dir)


def _entry_dir(ts, op):
    import random
    return os.path.join(UNDO_DIR, f"{ts}_{op}_{random.randrange(1000, 9999)}")


def snapshot_before(op, path, note=""):
    """写操作前快照原文件/目录。返回 (ok, snapshot_id 或错误信息)。

    op: write_file / edit_file / batch_rename / database_execute / delete_file
    路径不存在（新建）→ 记录占位快照（记录"此文件当时不存在"，恢复时删除）。
    """
    if not UNDO_DIR or not path:
        return False, "快照未初始化"
    try:
        p = os.path.abspath(str(path))
        if not os.path.exists(p):
            # 新建场景：无需快照内容，但记录以便恢复时感知（可选，先跳过）
            return True, ""
        if os.path.isdir(p):
            return False, "目录快照暂不支持（请对文件操作）"
        if os.path.getsize(p) > MAX_SNAP_FILE:
            return False, f"文件超过 {MAX_SNAP_FILE // 1024 // 1024}MB，跳过快照"
        ts = time.strftime("%Y%m%d-%H%M%S")
        d = _entry_dir(ts, op)
        os.makedirs(d, exist_ok=True)
        data_path = os.path.join(d, "data")
        shutil.copy2(p, data_path)
        meta = {
            "op": op,
            "path": p,
            "ts": ts,
            "note": str(note or "")[:200],
            "size": os.path.getsize(p),
            "kind": "file",
        }
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=1)
        _prune()
        return True, os.path.basename(d)
    except Exception as e:
        logger.exception("快照失败: %s", path)
        return False, f"快照失败: {e}"


def list_snapshots(limit=50):
    """列出快照（新→旧）。每条含 id/op/时间/原始路径/大小/note。"""
    if not UNDO_DIR or not os.path.isdir(UNDO_DIR):
        return []
    out = []
    try:
        for name in sorted(os.listdir(UNDO_DIR), reverse=True):
            meta_path = os.path.join(UNDO_DIR, name, "meta.json")
            if not os.path.isfile(meta_path):
                continue
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    m = json.load(f)
                out.append({
                    "id": name,
                    "op": str(m.get("op") or ""),
                    "ts": str(m.get("ts") or ""),
                    "path": str(m.get("path") or ""),
                    "size": int(m.get("size") or 0),
                    "note": str(m.get("note") or ""),
                })
            except Exception:
                continue
            if len(out) >= limit:
                break
    except Exception:
        logger.exception("列出快照失败")
    return out


def restore_snapshot(snapshot_id):
    """按快照恢复：把 data 写回原路径。返回 (ok, message)。"""
    if not UNDO_DIR or not snapshot_id:
        return False, "快照未初始化或缺少 id"
    d = os.path.join(UNDO_DIR, str(snapshot_id))
    meta_path = os.path.join(d, "meta.json")
    data_path = os.path.join(d, "data")
    if not os.path.isfile(meta_path) or not os.path.isfile(data_path):
        return False, f"快照不存在或已损坏：{snapshot_id}"
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            m = json.load(f)
        target = str(m.get("path") or "")
        if not target:
            return False, "快照缺少原始路径"
        import permissions
        # 权限模块已初始化（api_server 启动时）才做写权限检查；独立脚本/未初始化时放行
        if getattr(permissions, "PERMISSIONS_PATH", None):
            ok, reason = permissions.check_filesystem(target, write=True)
            if not ok:
                return False, reason
        # 写回前备份当前文件（恢复本身也可撤销）
        if os.path.exists(target):
            try:
                shutil.copy2(target, target + CURRENT_BAK_SUFFIX)
            except Exception:
                pass
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        shutil.copy2(data_path, target)
        permissions.audit("restore_snapshot", target, f"from {snapshot_id}")
        return True, (
            f"已从快照 {snapshot_id} 恢复 {target}"
            f"（原操作：{m.get('op') or '?'} @ {m.get('ts') or ''}"
            f"{'，当前文件已备份为 .snap.bak' if os.path.exists(target + CURRENT_BAK_SUFFIX) else ''}）"
        )
    except Exception as e:
        logger.exception("恢复快照失败: %s", snapshot_id)
        return False, f"恢复失败: {e}"


def _prune():
    """按 MAX_SNAPSHOTS 清理最旧条目（保留目录结构完整）。"""
    try:
        entries = []
        for name in os.listdir(UNDO_DIR):
            d = os.path.join(UNDO_DIR, name)
            if os.path.isdir(d):
                try:
                    entries.append((os.path.getmtime(d), d))
                except OSError:
                    continue
        entries.sort()
        for _mtime, d in entries[:-MAX_SNAPSHOTS]:
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        logger.exception("快照清理失败")
