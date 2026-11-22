"""项目备份脚本：每次大版本更新前运行，生成完整源码快照压缩包。

用法：
    python backup.py            # 生成 backups/WhaleTalk_vX.Y.Z_时间戳.zip
    python backup.py --prune N  # 生成备份后，仅保留最近 N 个备份（默认 20）
"""
import argparse
import datetime
import os
import shutil
import tempfile
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
# 备份排除目录。
# `node_modules` 必须排除：它是 npm 依赖树，可由 `webui/package-lock.json` 完全
# 重建，实测占整个备份的 **87.7%**（3283/3743 文件、74.2/84.6 MB）——带上它会让
# 每个备份大 8 倍、且 prune(20) 保留 20 份时白白多占约 1.4 GB。
# 其余缓存目录（.pytest_cache/.ruff_cache/.mypy_cache）同理，都是可重建产物。
EXCLUDE_DIRS = {".venv", "venv", "__pycache__", "dist", "build", "backups", ".git",
                ".idea", ".vscode", "evolutions", "node_modules", ".pytest_cache",
                ".ruff_cache", ".mypy_cache", ".cache"}
EXCLUDE_EXTS = {".pyc", ".pyo", ".log", ".zip"}
EXCLUDE_FILES = {".clean_exit"}

# 浏览器 profile 里的纯缓存目录：可重建，实测占 58 MB 里约 50 MB
# （Cache 12 MB、BrowserMetrics 12 MB、GrShaderCache 4 MB……）。
# 只剪缓存，保留 Cookies / Login Data / Local Storage / Preferences ——
# 恢复后登录态仍在，不必重新登录。
BROWSER_PROFILE_PREFIX = "data/browser_profile"
BROWSER_CACHE_DIRS = {
    "Cache", "Code Cache", "GPUCache", "GrShaderCache", "ShaderCache",
    "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache", "GraphiteDawnCache",
    "BrowserMetrics", "Crashpad", "component_crx_cache", "extensions_crx_cache",
    "Service Worker", "blob_storage", "File System",
}


def _prune_dirs(rel_root, dirs):
    """按目录剪枝：全局缓存目录 + browser_profile 下的纯缓存子目录。"""
    out = [d for d in dirs if d not in EXCLUDE_DIRS]
    if rel_root.replace("\\", "/").startswith(BROWSER_PROFILE_PREFIX):
        out = [d for d in out if d not in BROWSER_CACHE_DIRS]
    return out


def current_version():
    try:
        from config_defaults import VERSION
        return str(VERSION)
    except Exception:
        return "unknown"


def make_backup():
    os.makedirs(BACKUP_DIR, exist_ok=True)
    # 微秒精度：同一秒内重复备份不再静默覆盖（ZipFile "w" 直接截断旧文件）
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    name = f"WhaleTalk_v{current_version()}_{ts}.zip"
    path = os.path.join(BACKUP_DIR, name)
    count = 0
    try:
        # compresslevel=1：源码/文本压缩体积仅增 ~5%，CPU 省 2-3 倍（zip 大头是 I/O）
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for root, dirs, files in os.walk(BASE_DIR):
                dirs[:] = _prune_dirs(os.path.relpath(root, BASE_DIR), dirs)
                for fn in sorted(files):
                    if fn in EXCLUDE_FILES or fn.endswith(tuple(EXCLUDE_EXTS)) or fn.endswith(".pma"):
                        continue
                    full = os.path.join(root, fn)
                    arc = os.path.relpath(full, BASE_DIR)
                    zf.write(full, arc)
                    count += 1
    except Exception:
        # 压缩中途异常：清理半成品 zip，不留损坏备份
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    size = os.path.getsize(path)
    print(f"备份完成：{path}")
    print(f"包含 {count} 个文件，{size / 1024:.1f} KB")
    return path


def prune(keep=20):
    zips = sorted(
        f for f in os.listdir(BACKUP_DIR) if f.startswith("WhaleTalk_v") and f.endswith(".zip")
    )
    removed = 0
    for f in zips[:-keep] if keep > 0 else []:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
            removed += 1
        except OSError:
            pass
    if removed:
        print(f"已清理 {removed} 个旧备份（保留最近 {keep} 个）")


def _safe_member(name):
    """把 zip 条目转成安全的相对路径；拒绝绝对路径/盘符/.. 穿越。"""
    rel = str(name or "").replace("\\", "/").lstrip("/")
    if not rel or rel.endswith("/"):
        return ""
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    if any(p == ".." or ":" in p for p in parts):
        return ""
    return "/".join(parts)


DATA_ROOTS = ("data/", "brain/", "trust/")
DATA_FILES = {"config.json", "permissions.json", "roles.json"}


def _in_scope(rel, scope):
    if scope == "all":
        return True
    return rel in DATA_FILES or rel.startswith(DATA_ROOTS)


def restore_backup(name, scope="data", base_dir=None):
    """从 backups/ 恢复一个 zip。

    scope="data"：只恢复 data/、brain/、trust/ 与根配置文件（推荐，风险最低）。
    scope="all"：恢复备份内全部文件（含程序源码）；恢复后必须重启服务。
    **恢复前自动再备份当前状态**；任何路径穿越条目直接拒绝。
    """
    base_dir = os.path.abspath(base_dir or BASE_DIR)
    scope = "all" if str(scope).lower() == "all" else "data"
    fn = os.path.basename(str(name or "").strip())
    if not fn.endswith(".zip"):
        return {"ok": False, "error": "备份文件名必须以 .zip 结尾"}
    zpath = os.path.join(BACKUP_DIR, fn)
    if not os.path.isfile(zpath):
        return {"ok": False, "error": f"备份不存在：{fn}"}

    try:
        pre_path = make_backup()
        pre_name = os.path.basename(pre_path)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"恢复前备份失败，已中止：{type(e).__name__}: {e}"}

    tmp = tempfile.mkdtemp(prefix=".restore_", dir=BACKUP_DIR)
    restored = skipped = 0
    errors = []
    try:
        with zipfile.ZipFile(zpath) as zf:
            bad = zf.testzip()
            if bad:
                return {"ok": False, "error": f"备份压缩包损坏：{bad}", "pre_backup": pre_name}
            for info in zf.infolist():
                rel = _safe_member(info.filename)
                if not rel or rel.startswith("backups/"):
                    continue
                target = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)

        if scope == "all" and not os.path.isfile(os.path.join(tmp, "api_server.py")):
            return {"ok": False, "error": "备份缺少 api_server.py，拒绝恢复", "pre_backup": pre_name}
        if scope == "data" and not os.path.isdir(os.path.join(tmp, "data")):
            return {"ok": False, "error": "备份缺少 data/，拒绝恢复", "pre_backup": pre_name}

        for root, _dirs, files in os.walk(tmp):
            for f in files:
                src = os.path.join(root, f)
                rel = os.path.relpath(src, tmp).replace("\\", "/")
                if not _in_scope(rel, scope):
                    skipped += 1
                    continue
                dst = os.path.abspath(os.path.join(base_dir, rel))
                try:
                    if os.path.commonpath([base_dir, dst]) != base_dir:
                        skipped += 1
                        continue
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    restored += 1
                except Exception as e:  # noqa: BLE001
                    errors.append(f"{rel}: {type(e).__name__}: {e}")
        return {
            "ok": not errors,
            "scope": scope,
            "restored": restored,
            "skipped": skipped,
            "errors": errors[:20],
            "pre_backup": pre_name,
            "requires_restart": True,
            "message": "恢复完成；请重启 WhaleTalk 以加载恢复后的程序/配置" if scope == "all"
                       else "数据与配置已恢复；建议重启 WhaleTalk 让所有模块重新读取",
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"恢复失败：{type(e).__name__}: {e}", "pre_backup": pre_name}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="鲸语 WhaleTalk 项目备份")
    parser.add_argument("--prune", type=int, default=20, help="保留最近 N 个备份（默认 20）")
    args = parser.parse_args()
    print(f"当前版本：v{current_version()}")
    bpath = make_backup()
    print(f"备份完成：{bpath}")
    prune(args.prune)
    print("提示：备份包含 config.json（含 API Key），请勿外传备份文件。")


if __name__ == "__main__":
    main()
