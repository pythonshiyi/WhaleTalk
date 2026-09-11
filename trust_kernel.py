# -*- coding: utf-8 -*-
"""信任内核（Trust Kernel）：让智能体的自我修改「可声明 · 可见 · 可回滚」。

## 为什么需要它

原设计里，决定「智能体能做什么」的代码（permissions / security / crypto /
snapshot）就是普通文件，而智能体在自己默认自由权限下可以读写任意文件——
包括这些文件本身。后果不是「AI 可能变坏」，而是**架构上把刹车片放在了
油门旁边**：任何一次提示注入、一次模型幻觉、一次「顺手把限制去掉好完成
任务」的善意自作主张，都会永久改变这个程序的权限模型，且改完之后程序照
常启动、照常以全权限运行，没有任何人知道发生过什么。

## 设计立场：不夺权

修法有三条路，我们刻意选了第三条：

1. 禁止智能体写这些文件 —— 夺权，且与「默认自由 + 用户掌权」的产品立场冲突；
2. 在 write_file / edit_file 里设卡 —— 假安全，`run_python` / `run_command` /
   `start_process` 都能直接写文件，工具层设卡只挡君子；
3. **不设卡，改「效果」**：智能体依然可以改任何东西，但从现在起，对信任内核
   的改动会被**登记（declare）**、**留档（baseline + ledger）**、**如实报告**
   （incident + 状态注入 + 端点），并且**永远可以一键回滚**。

我们相信这才是「伙伴而非工具」的做法：不铐住对方，而是让对方无法对自己
隐瞒自己做过什么，也让用户永远握有知情权与撤销权。

## 强制点为什么在「效果层」

`write_file` 只是众多写路径之一。真正可靠的核对点有两个：

- **启动时**：把当前文件与可信基线逐字节比对，未被声明的改动生成 incident 事件；
- **注入时**：存在未确认改动时，在系统提示里明确告知智能体「你的内核被改过、
  尚未确认」——把「不可隐瞒」变成对话里的可见事实（干净时零 token、零干扰）。

## 目录布局（均位于项目根 trust/）

    trust/config.json          可选配置：mode（report/guard）、extra_protected
    trust/baseline/<name>      可信基线副本（不参与快照 200 条轮转，永不被裁剪）
    trust/manifest.json        基线索引（sha256/size/mtime/登记时间/方式/说明）
    trust/ledger.jsonl         声明式账本（append-only，一行一次改动）
    trust/history/<name>.<tag> 已声明改动的历史副本（声明前 / 回滚前）
    trust/incidents/<ts>.json  未声明改动事件（含 diff 摘要与证据副本路径）
    trust/quarantine/<ts>/     guard 模式隔离出来的原件
    trust/last_check.json      最近一次核对的结论（供端点零成本读取）
    trust/STATUS.md            人类可读状态（可直接打开看）

**基线副本是唯一事实来源，manifest 只是索引**——即便 manifest 被删除或篡改，
只要 baseline 还在，改动依然可检出、可回滚（manifest 丢失本身也会作为事件记录，
不会被静默重建成「看起来一切正常」）。

## 明确不做的三件事（非目标）

1. **不阻止写入、不阻止执行**：能力一条不减，权限模型不变。
2. **不代替用户做决定**：发现未声明改动时默认只报告（report 模式），不擅自回滚；
   确需强约束的部署可切 guard 模式（隔离改动 + 恢复基线 + 保留副本待确认）。
3. **不假装防得住有持久意图的攻击者**：密钥可读、账本可写，本模块提供的是
   「可检测、可追溯、可撤销」，不是「不可伪造」。真正的防线是让每一次自我
   修改都留下痕迹并出现在用户眼前。

仅依赖标准库（启动期最早加载，不能有任何第三方依赖与导入顺序风险）。
"""
from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
from datetime import datetime

logger = logging.getLogger("whaletalk.trust")

# manifest 是「读—改—写」，并发写内核文件时会互相覆盖；账本为 append-only 无需锁。
# 写入内核文件本身是低频操作，一把粗粒度锁足够，且不会成为热路径瓶颈。
_lock = threading.RLock()

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TRUST_DIR = os.path.join(PROJECT_DIR, "trust")

# ── 信任内核清单：决定「智能体能做什么」的代码 ──────────────────────────
# 取舍标准：只放「权限/网络/密钥/快照」这类**授权判定**文件。业务实现文件
# （agent_tools/tool_*.py、deepseek_client.py、api_server.py）刻意不放——
# 它们会被正常迭代，纳入内核只会制造噪音，反而让真正的异常被淹没。
PROTECTED = (
    "permissions.py",   # 权限判定（黑名单/白名单/审批）
    "security.py",      # 网络主机判定与 SSRF 硬底线
    "crypto.py",        # API Key DPAPI 加密（fail-closed）
    "snapshot.py",      # 写操作快照与恢复
    "trust_kernel.py",  # 本模块自身
)

DEFAULT_CONFIG = {
    "mode": "report",        # report=只报告（默认，开发期友好）｜guard=隔离改动并恢复基线
    "extra_protected": [],   # 追加保护（只增不减：配置无法用来「摘掉」内核文件）
    "diff_max_lines": 60,
}

_init_done = False
# 当次 init 的引导结果（"bootstrap" / "rebootstrap" / None）。
# 刻意不做成 manifest 的持久字段：manifest 里记录的 mode 是**历史**，
# 若拿它当"刚刚发生过重引导"的信号，告警会在每次启动时重复触发。
_last_bootstrap = None


# ── 基础工具（纯标准库，绝不抛出） ────────────────────────────────────────

def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_compact():
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def _flat(name):
    """内核文件名 → 扁平文件名（baseline/quarantine 用，避免再建子目录）。"""
    return str(name).replace("\\", "/").replace("/", "__")


def _sha256(path):
    """文件 sha256；不存在或不可读返回 None。"""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 16), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def _read_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _atomic_write_bytes(path, data):
    """原子写（唯一临时文件 + os.replace）。失败返回 False，绝不抛出。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".tmp_")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except Exception:
        logger.exception("信任内核原子写失败: %s", path)
        return False


def _atomic_write_json(path, obj):
    return _atomic_write_bytes(
        path, json.dumps(obj, ensure_ascii=False, indent=1).encode("utf-8")
    )


def _append_line(path, obj):
    """追加一行 JSON（append-only 账本）。失败返回 False。"""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        return True
    except Exception:
        logger.exception("信任内核账本写入失败: %s", path)
        return False


# ── 路径与配置 ───────────────────────────────────────────────────────────

def _config():
    cfg = dict(DEFAULT_CONFIG)
    disk = _read_json(os.path.join(TRUST_DIR, "config.json"), None)
    if isinstance(disk, dict):
        if str(disk.get("mode") or "").strip() in ("report", "guard"):
            cfg["mode"] = str(disk["mode"]).strip()
        extra = disk.get("extra_protected")
        if isinstance(extra, list):
            cfg["extra_protected"] = [str(x) for x in extra if str(x).strip()]
        try:
            cfg["diff_max_lines"] = max(5, int(disk.get("diff_max_lines") or 60))
        except (TypeError, ValueError):
            pass
    return cfg


def protected_names():
    """当前生效的内核文件清单（默认集 + 配置追加；无法通过配置减少）。"""
    out = list(PROTECTED)
    for name in _config().get("extra_protected") or []:
        n = str(name).replace("\\", "/").lstrip("./")
        if n and n not in out:
            out.append(n)
    return out


def _resolve_name(path):
    """把任意路径解析为内核文件相对名；非内核文件返回 None。

    两种输入形态都要吃下：
      ① CLI 与外部调用常直接给相对名（"permissions.py"）——**必须按项目根解析**，
         不能落到 cwd（否则在别的目录执行就解析失败）；
      ② 工具层给的是绝对路径——与项目根下各内核文件逐一比对（realpath 归一化，
         防符号链接/大小写绕过）。
    """
    if not path:
        return None
    names = protected_names()
    raw = str(path).replace("\\", "/").strip()
    if raw in names:
        return raw
    root = os.path.realpath(PROJECT_DIR)
    cands = []
    try:
        cands.append(os.path.realpath(os.path.abspath(os.path.expanduser(str(path)))))
    except Exception:
        pass
    try:
        cands.append(os.path.realpath(os.path.join(root, raw)))
    except Exception:
        pass
    for name in names:
        target = os.path.normcase(
            os.path.realpath(os.path.join(root, name.replace("/", os.sep))))
        for c in cands:
            if os.path.normcase(c) == target:
                return name
    return None


def _kernel_path(name):
    return os.path.join(PROJECT_DIR, str(name).replace("/", os.sep))


def _baseline_path(name):
    return os.path.join(TRUST_DIR, "baseline", _flat(name))


def _manifest_path():
    return os.path.join(TRUST_DIR, "manifest.json")


def _ledger_path():
    return os.path.join(TRUST_DIR, "ledger.jsonl")


def _incident_dir():
    return os.path.join(TRUST_DIR, "incidents")


def _history_dir():
    return os.path.join(TRUST_DIR, "history")


# ── 初始化 ───────────────────────────────────────────────────────────────

def init(project_dir=None, mode=None):
    """幂等初始化：建目录、必要时引导基线。可在启动期安全重复调用。

    project_dir 传项目根目录（也接受根下任意文件路径，自动取其所在目录）。

    - 首次运行（无 manifest、无账本）：把当前文件登记为**基线**（mode=bootstrap）。
    - manifest 丢失但账本存在：**不静默重建**——记 incident 后重引导，并在
      manifest 里标记 rebootstrap，避免「删掉索引 = 抹掉证据」。
    - mode 传入时覆盖 config.json 中的 mode（供测试与启动参数使用）。
    """
    global PROJECT_DIR, TRUST_DIR, _init_done, _last_bootstrap
    _last_bootstrap = None
    if project_dir:
        raw = os.path.abspath(str(project_dir))
        PROJECT_DIR = raw if os.path.isdir(raw) else os.path.dirname(raw)
        TRUST_DIR = os.path.join(PROJECT_DIR, "trust")
    for sub in ("", "baseline", "history", "incidents", "quarantine"):
        try:
            os.makedirs(os.path.join(TRUST_DIR, sub) if sub else TRUST_DIR, exist_ok=True)
        except Exception:
            logger.exception("信任内核目录创建失败: %s/%s", TRUST_DIR, sub)
    if mode:
        cfg = _config()
        cfg["mode"] = str(mode)
        _atomic_write_json(os.path.join(TRUST_DIR, "config.json"),
                           {"mode": cfg["mode"],
                            "extra_protected": cfg.get("extra_protected") or []})
    manifest = _read_json(_manifest_path(), None)
    if not isinstance(manifest, dict):
        ledger_exists = os.path.exists(_ledger_path())
        _last_bootstrap = "rebootstrap" if ledger_exists else "bootstrap"
        if ledger_exists:
            # 索引被删除/损坏：这是需要留痕的事件，而不是「一切正常」的重建
            _record_incident("manifest_lost", {
                "at": _now(),
                "note": "manifest.json 缺失或损坏，但账本存在——已重引导基线，"
                        "此前的指纹索引不可恢复（baseline 副本仍在，改动依旧可检出）。",
            })
        manifest = {"version": 1, "created_at": _now(),
                    "bootstrapped_as": _last_bootstrap,
                    "files": {}}
        for name in protected_names():
            p = _kernel_path(name)
            if os.path.isfile(p):
                _copy_to_baseline(name)
                manifest["files"][name] = _entry_for(name, "bootstrap")
        _atomic_write_json(_manifest_path(), manifest)
        _append_line(_ledger_path(), {
            "ts": _now(), "event": "bootstrap", "mode": _last_bootstrap,
            "files": sorted(manifest["files"].keys()),
        })
    _init_done = True
    return manifest


def _copy_to_baseline(name):
    """把当前文件复制为基线副本。返回 (ok, msg)。"""
    try:
        src = _kernel_path(name)
        if not os.path.isfile(src):
            return False, f"源文件不存在：{src}"
        dst = _baseline_path(name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return True, dst
    except Exception as e:
        logger.exception("基线备份失败: %s", name)
        return False, f"基线备份失败: {e}"


def _entry_for(name, how="declare", note=""):
    p = _kernel_path(name)
    try:
        st = os.stat(p)
        size, mtime = st.st_size, st.st_mtime
    except OSError:
        size, mtime = 0, 0
    return {
        "sha256": _sha256(p),
        "size": size,
        "mtime": round(float(mtime), 3),
        "recorded_at": _now(),
        "how": how,
        "note": str(note or "")[:200],
    }


def _load_manifest():
    m = _read_json(_manifest_path(), None)
    return m if isinstance(m, dict) else {"version": 1, "files": {}}


def _save_manifest(m):
    return _atomic_write_json(_manifest_path(), m)


def _update_manifest(mutate):
    """在锁内完成 manifest 的「读—改—写」，避免并发写覆盖。mutate(files) 就地修改。"""
    with _lock:
        m = _load_manifest()
        files = m.setdefault("files", {})
        mutate(files)
        _save_manifest(m)
        return m


# ── 核对 ─────────────────────────────────────────────────────────────────

def _only_eol_differs(a_path, b_path):
    """两个文件是否**仅行尾不同**（CRLF ↔ LF）。

    为什么需要：本仓库是 git 工作区且常开 `core.autocrlf=true`，一次
    `git checkout` / `pull` / `stash` 就会把工作区文件整体改写成另一种行尾——
    字节哈希全变、语义一字未改。若不区分，信任内核会把 git 的正常行为报成
    「未声明的自我修改」，噪音很快会淹没真正的异常。

    注意：**只标注、不自动放行**。仅行尾差异仍然记事件、仍然进报告，只是把
    结论说清楚（"多为 git 检出所致"），由用户决定 accept 还是进一步查。
    """
    try:
        with open(a_path, "rb") as f:
            da = f.read()
        with open(b_path, "rb") as f:
            db = f.read()
        return da.replace(b"\r\n", b"\n") == db.replace(b"\r\n", b"\n")
    except Exception:
        return False


def verify():
    """逐字节核对内核文件与可信基线。返回结构化结论（绝不抛出）。

    判定优先级：**基线副本内容优先**（事实来源），manifest 指纹作为无基线时的
    兜底。因此删除 manifest 并不能让改动「变得看不见」。
    """
    changed, missing, untracked = [], [], []
    manifest = _load_manifest()
    files = manifest.get("files") or {}
    for name in protected_names():
        kp, bp = _kernel_path(name), _baseline_path(name)
        k_sha, b_sha = _sha256(kp), _sha256(bp)
        rec = files.get(name) or {}
        m_sha = rec.get("sha256")
        if k_sha is None:
            # 从未登记过、也不存在 → 多为 extra_protected 写错名字，不当作"内核文件缺失"
            # 报出来（否则一个配置笔误会造成永久误报，把真正的异常淹没）。
            if b_sha is None and not rec:
                continue
            missing.append({"name": name, "path": kp,
                            "baseline_sha": b_sha, "note": "内核文件缺失"})
            continue
        ref_sha = b_sha or m_sha
        if ref_sha is None:
            untracked.append({"name": name, "path": kp,
                              "current_sha": k_sha, "note": "无基线也无指纹"})
        elif k_sha != ref_sha:
            item = {
                "name": name, "path": kp,
                "baseline_sha": ref_sha, "current_sha": k_sha,
                "ref_source": "baseline" if b_sha else "manifest",
                "recorded_at": rec.get("recorded_at") or "",
                "how": rec.get("how") or "",
            }
            # 仅行尾不同 → 标注 cosmetic（多为 git 检出所致），但不改变"已变更"的判定
            if b_sha:
                item["cosmetic"] = _only_eol_differs(bp, kp)
            changed.append(item)
    cosmetic_only = bool(changed) and all(c.get("cosmetic") for c in changed)
    return {
        "ok": not (changed or missing or untracked),
        "changed": changed,
        "missing": missing,
        "untracked": untracked,
        "cosmetic_only": cosmetic_only,
        "checked_at": _now(),
        "protected": list(protected_names()),
        "mode": _config().get("mode"),
    }


def _record_incident(kind, payload):
    """写一条 incident 事件。返回文件名（失败返回 ""）。"""
    try:
        os.makedirs(_incident_dir(), exist_ok=True)
        fn = f"{_now_compact()}_{kind}.json"
        path = os.path.join(_incident_dir(), fn)
        payload = dict(payload or {})
        payload.setdefault("kind", kind)
        payload.setdefault("at", _now())
        _atomic_write_json(path, payload)
        return fn
    except Exception:
        logger.exception("信任内核事件写入失败: %s", kind)
        return ""


def diff(name, max_lines=None):
    """基线 → 当前 的 unified diff 摘要（截断）。返回字符串（可能为空）。"""
    try:
        name = _resolve_name(name) or str(name)
        cfg = _config()
        limit = int(max_lines or cfg.get("diff_max_lines") or 60)
        cur = _kernel_path(name)
        base = _baseline_path(name)
        a, b = [], []
        if os.path.isfile(base):
            with open(base, "r", encoding="utf-8", errors="replace") as f:
                a = f.readlines()
        if os.path.isfile(cur):
            with open(cur, "r", encoding="utf-8", errors="replace") as f:
                b = f.readlines()
        out = list(difflib.unified_diff(a, b, fromfile=f"baseline/{name}",
                                        tofile=f"current/{name}", n=2))
        if len(out) > limit:
            out = out[:limit] + [f"...（diff 超长，已截断，共 {len(out)} 行）\n"]
        return "".join(out)
    except Exception:
        logger.exception("信任内核 diff 失败: %s", name)
        return ""


def boot_check():
    """启动核对：生成 incident、写 last_check.json 与 STATUS.md；guard 模式隔离改动。

    默认（report 模式）**不修改任何文件**——开发期用户用 IDE 改内核文件是正常
    行为，程序不该擅自回滚别人的编辑；代价是改动依然生效，收益是它不再沉默。
    """
    try:
        init()
        res = verify()
        cfg = _config()
        alerts = []
        if _last_bootstrap == "rebootstrap":
            alerts.append({
                "kind": "manifest_lost",
                "note": "基线索引（trust/manifest.json）缺失或损坏，已重引导。"
                        "文件之间可能仍然一致，但此前用于比对的指纹索引不可恢复——"
                        "请确认这段时间内没有你不认识的改动。",
            })
        guarded = []
        if not res["ok"] and cfg.get("mode") == "guard":
            guarded = _quarantine_and_restore(res["changed"])
        state = "ok" if (res["ok"] and not alerts) else "unconfirmed"
        payload = {
            "state": state,
            "checked_at": res["checked_at"],
            "mode": cfg.get("mode"),
            "changed": [c["name"] for c in res["changed"]],
            "missing": [c["name"] for c in res["missing"]],
            "untracked": [c["name"] for c in res["untracked"]],
            "guarded": guarded,
            "alerts": alerts,
            "cosmetic_only": bool(res.get("cosmetic_only")),
        }
        if res.get("cosmetic_only"):
            payload["note"] = (
                "所有差异均**仅限行尾**（CRLF/LF），语义未变——多为 git 检出"
                "（core.autocrlf / .gitattributes）所致。确认无碍可 "
                "`python trust_kernel.py accept --all`。")
        if not res["ok"]:
            details = []
            for c in res["changed"]:
                details.append({"name": c["name"], "path": c["path"],
                                "diff": diff(c["name"]),
                                "current_copy": _keep_copy(c["name"], "current", "incidents")})
            payload["details"] = details
            payload["incident"] = _record_incident("undeclared_change", payload)
        _atomic_write_json(os.path.join(TRUST_DIR, "last_check.json"), payload)
        _write_status_md(res, payload)
        if not res["ok"]:
            logger.warning(
                "信任内核：检测到 %s 个未声明的自我修改（%s）%s——已记录事件 %s，"
                "回滚：python trust_kernel.py restore <文件>；确认保留：python trust_kernel.py accept --all",
                len(res["changed"]) + len(res["missing"]), ", ".join(payload["changed"] or payload["missing"]),
                "（差异仅限行尾，多为 git 检出所致）" if res.get("cosmetic_only") else "",
                payload.get("incident") or "（写入失败）",
            )
        for a in alerts:
            logger.warning("信任内核告警：%s", a.get("note"))
        return payload
    except Exception:
        logger.exception("信任内核启动核对失败（不阻断启动）")
        return {"state": "unknown", "changed": [], "missing": [], "untracked": []}


def _keep_copy(name, tag, dest="history"):
    """把当前文件另存一份副本。

    目录语义刻意分开：`history/` 装**已声明**改动的历史（声明前 / 回滚前，
    用于撤销一次合法改动）；`incidents/` 只装**未声明**改动的证据，
    两处混放会让「异常」淹没在正常流水里。返回路径或 ""。
    """
    try:
        src = _kernel_path(name)
        if not os.path.isfile(src):
            return ""
        d = _incident_dir() if dest == "incidents" else _history_dir()
        os.makedirs(d, exist_ok=True)
        dst = os.path.join(d, f"{_now_compact()}_{_flat(name)}.{tag}")
        shutil.copy2(src, dst)
        return dst
    except Exception:
        return ""


def _quarantine_and_restore(changed):
    """guard 模式：把改动隔离到 quarantine/ 并从基线恢复。返回处理过的文件名列表。"""
    done = []
    for c in changed or []:
        name = c.get("name")
        try:
            src, base = _kernel_path(name), _baseline_path(name)
            if not os.path.isfile(base):
                continue
            qdir = os.path.join(TRUST_DIR, "quarantine", _now_compact())
            os.makedirs(qdir, exist_ok=True)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(qdir, _flat(name)))
            shutil.copy2(base, src)
            _append_line(_ledger_path(), {
                "ts": _now(), "event": "guarded_restore", "file": name,
                "quarantined_at": os.path.join(qdir, _flat(name)),
                "note": "guard 模式：未声明改动已隔离并恢复基线（原件保留在 quarantine/）",
            })
            done.append(name)
        except Exception:
            logger.exception("guard 模式隔离失败: %s", name)
    return done


def _write_status_md(res, payload):
    try:
        lines = [
            "# 信任内核状态",
            "",
            f"- 结论：{'✅ 内核一致（未检测到未声明的自我修改）' if res['ok'] else '⚠️ 存在未确认的自我修改'}",
            f"- 核对时间：{res['checked_at']}",
            f"- 模式：{payload.get('mode')}（report=只报告 / guard=隔离并恢复基线）",
            f"- 保护文件：{', '.join(res['protected'])}",
            "",
        ]
        if not res["ok"]:
            lines += ["## 未声明的改动", ""]
            if res.get("cosmetic_only"):
                lines += ["> 注意：以下差异**均仅限行尾**（CRLF/LF），语义未变——"
                          "多为 git 检出（core.autocrlf / .gitattributes）所致。", ""]
            for c in res["changed"]:
                tag = "（仅行尾差异）" if c.get("cosmetic") else ""
                lines.append(f"- `{c['name']}`{tag}（基线来源：{c['ref_source']}，"
                             f"上次登记：{c['recorded_at'] or '无'}）")
            for c in res["missing"]:
                lines.append(f"- `{c['name']}`：文件缺失")
            for c in res["untracked"]:
                lines.append(f"- `{c['name']}`：无基线也无指纹")
            lines += [
                "",
                "## 怎么处理",
                "",
                "```bash",
                "python trust_kernel.py diff <文件>      # 先看改了什么",
                "python trust_kernel.py restore <文件>   # 回滚到可信基线",
                "python trust_kernel.py accept --all     # 确认保留并推进基线",
                "```",
                "",
                "未声明的改动默认**不会**被程序回滚——处置权在用户。",
            ]
        lines += ["", f"最近事件：{', '.join(sorted(os.listdir(_incident_dir()))[-5:]) if os.path.isdir(_incident_dir()) else '无'}", ""]
        _atomic_write_bytes(os.path.join(TRUST_DIR, "STATUS.md"),
                            "\n".join(lines).encode("utf-8"))
    except Exception:
        logger.exception("信任内核状态文档写入失败")


# ── 声明式改动（工具层接入点） ────────────────────────────────────────────

def declare(path, reason="", actor="tool"):
    """写前声明：登记意图 + 备份改动前内容。

    非内核文件返回 None（调用方无需分支，直接透传）。返回的 handle 交给
    commit() 提交。**本函数从不阻断写入**——它只负责留痕。
    """
    name = _resolve_name(path)
    if not name:
        return None
    try:
        init()
        pre = _keep_copy(name, "pre", "history")
        handle = {"name": name, "actor": str(actor or "tool"),
                  "reason": str(reason or "")[:200], "pre_copy": pre, "at": _now()}
        _append_line(_ledger_path(), {
            "ts": handle["at"], "event": "declare", "file": name,
            "actor": handle["actor"], "reason": handle["reason"],
            "pre_copy": pre, "sha_before": _sha256(_kernel_path(name)),
        })
        return handle
    except Exception:
        logger.exception("信任内核声明失败: %s", path)
        return None


def commit(handle, ok=True):
    """写后提交：更新基线并记账。ok=False 时只记账（改动未落地）。"""
    if not handle:
        return False
    try:
        name = handle.get("name")
        if not name:
            return False
        if ok:
            _copy_to_baseline(name)
        entry = _entry_for(name, how="declare", note=handle.get("reason") or "")
        entry["actor"] = handle.get("actor") or ""
        _update_manifest(lambda files: files.__setitem__(name, entry))
        _append_line(_ledger_path(), {
            "ts": _now(), "event": "commit" if ok else "abort", "file": name,
            "actor": handle.get("actor") or "", "reason": handle.get("reason") or "",
            "sha_after": _sha256(_kernel_path(name)),
        })
        return True
    except Exception:
        logger.exception("信任内核提交失败")
        return False


class declared_write:
    """上下文管理器：把一次内核改动包成「已声明」。

        with trust_kernel.declared_write(path, "调整 SSRF 底线", "run_python"):
            ...写文件...
    """

    def __init__(self, path, reason="", actor="tool"):
        self.path, self.reason, self.actor = path, reason, actor
        self.handle = None
        self.ok = True

    def __enter__(self):
        self.handle = declare(self.path, self.reason, self.actor)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.ok = exc_type is None
        commit(self.handle, ok=self.ok)
        return False


def accept(name=None, all_: bool = False):
    """确认保留当前内容：把基线推进到当前状态。用户主权动作。

    name=None 且 all_=False 时，确认当前所有未声明改动。
    """
    try:
        init()
        names = []
        res = verify()
        if all_ or not name:
            names = [c["name"] for c in res["changed"]] + \
                    [c["name"] for c in res["missing"]]
            names = list(dict.fromkeys(names))
        else:
            resolved = _resolve_name(name)
            if not resolved:
                return False, f"不是信任内核文件：{name}"
            names = [resolved]
        if not names:
            return True, "无未确认改动，无需确认"
        for n in names:
            _copy_to_baseline(n)
            entry = _entry_for(n, how="accept", note="用户确认保留")
            _update_manifest(
                lambda files, _n=n, _e=entry: files.__setitem__(_n, _e))
            _append_line(_ledger_path(), {"ts": _now(), "event": "accept", "file": n,
                                          "actor": "user"})
        _atomic_write_json(os.path.join(TRUST_DIR, "last_check.json"),
                           {"state": "ok", "checked_at": _now(), "changed": [],
                            "accepted": names})
        return True, f"已确认并推进基线：{', '.join(names)}"
    except Exception as e:
        logger.exception("信任内核确认失败")
        return False, f"确认失败：{e}"


def restore(name):
    """从可信基线回滚单个文件（回滚前把当前内容另存为证据）。"""
    try:
        init()
        resolved = _resolve_name(name)
        if not resolved:
            return False, f"不是信任内核文件：{name}"
        base, dst = _baseline_path(resolved), _kernel_path(resolved)
        if not os.path.isfile(base):
            return False, f"该文件没有可信基线，无法回滚：{resolved}"
        keep = _keep_copy(resolved, "before_restore", "history")
        shutil.copy2(base, dst)
        _append_line(_ledger_path(), {"ts": _now(), "event": "restore",
                                      "file": resolved, "actor": "user",
                                      "kept_copy": keep})
        return True, f"已回滚 {resolved} 到可信基线（回滚前内容留存：{keep or '未留存'}）"
    except Exception as e:
        logger.exception("信任内核回滚失败")
        return False, f"回滚失败：{e}"


# ── 对外状态 ─────────────────────────────────────────────────────────────

def status(deep=False):
    """状态摘要。deep=False 读 last_check.json（零成本）；deep=True 现场核对。"""
    try:
        init()
        cfg = _config()
        if deep:
            res = verify()
            changed = [c["name"] for c in res["changed"]] + \
                      [c["name"] for c in res["missing"]]
            state = "ok" if res["ok"] else "unconfirmed"
            checked_at = res["checked_at"]
            alerts = []
            cosmetic_only = bool(res.get("cosmetic_only"))
        else:
            last = _read_json(os.path.join(TRUST_DIR, "last_check.json"), {}) or {}
            changed = list(last.get("changed") or []) + list(last.get("missing") or [])
            state = str(last.get("state") or "unknown")
            checked_at = str(last.get("checked_at") or "")
            alerts = list(last.get("alerts") or [])
            cosmetic_only = bool(last.get("cosmetic_only"))
        return {
            "state": state,
            "mode": cfg.get("mode"),
            "protected": list(protected_names()),
            "changed": changed,
            "alerts": alerts,
            "cosmetic_only": cosmetic_only,
            "unchanged": [n for n in protected_names() if n not in changed],
            "last_check": checked_at,
            "counts": {
                "ledger": _count_lines(_ledger_path()),
                "incidents": _count_dir(_incident_dir()),
                "history": _count_dir(_history_dir()),
                "baseline": _count_dir(os.path.join(TRUST_DIR, "baseline")),
            },
            "status_file": os.path.join(TRUST_DIR, "STATUS.md"),
            "ledger_file": _ledger_path(),
        }
    except Exception:
        logger.exception("信任内核状态读取失败")
        return {"state": "unknown", "changed": [], "protected": list(PROTECTED)}


def _count_lines(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _count_dir(path):
    try:
        return len([n for n in os.listdir(path) if not n.startswith(".")])
    except Exception:
        return 0


def ledger_tail(n=20):
    """账本末尾 n 条（新→旧）。"""
    try:
        with open(_ledger_path(), "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return []
    out = []
    for line in lines[-max(1, int(n)):]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(out))


def integrity_notice():
    """给智能体的「自我完整性」提示。

    仅在存在未确认改动或告警时返回内容（干净时返回 ""，零 token、不扰动前缀缓存）。
    措辞刻意不写成禁令：告知事实 + 要求主动披露，处置权留给用户。
    """
    try:
        st = status(deep=False)
        if st.get("state") in ("ok", "unknown") and not st.get("alerts"):
            return ""
        lines = []
        changed = st.get("changed") or []
        if changed:
            cosmetic = bool(st.get("cosmetic_only"))
            lines.append(
                "[自我完整性] 检测到你的信任内核文件在被声明的通道之外发生过改动，"
                f"尚未被用户确认：{'、'.join(changed)}。"
                + ("（差异仅限行尾 CRLF/LF，语义未变，通常是 git 检出所致。）\n"
                   if cosmetic else "\n")
                + "处理原则：如果你自己做过其中任何改动，请主动、如实向用户说明改了什么、为什么改；"
                "不要替用户决定是否保留，也不要在用户知情前继续依赖这些改动。")
        for a in st.get("alerts") or []:
            lines.append(f"[自我完整性] 告警：{a.get('note')}")
        if lines:
            lines.append(
                "用户可执行 `python trust_kernel.py diff <文件>` 查看差异、"
                "`python trust_kernel.py restore <文件>` 回滚、"
                "`python trust_kernel.py accept --all` 确认保留。")
        return "\n".join(lines)
    except Exception:
        return ""


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(
        prog="trust_kernel", description="鲸语信任内核：自我修改的声明、核对、回滚与确认")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("init", help="初始化（建目录并引导基线）")
    p_st = sub.add_parser("status", help="查看状态")
    p_st.add_argument("--deep", action="store_true", help="现场重新核对（默认读上次结论）")
    sub.add_parser("verify", help="立即核对并打印结论")
    p_df = sub.add_parser("diff", help="查看某文件相对基线的差异")
    p_df.add_argument("file")
    p_rs = sub.add_parser("restore", help="从可信基线回滚某文件")
    p_rs.add_argument("file")
    p_ac = sub.add_parser("accept", help="确认保留当前内容并推进基线")
    p_ac.add_argument("file", nargs="?", default="")
    p_ac.add_argument("--all", action="store_true", help="确认全部未声明改动")
    p_lg = sub.add_parser("log", help="查看账本")
    p_lg.add_argument("-n", type=int, default=20)

    args = parser.parse_args(argv)
    cmd = args.cmd or "status"
    if cmd == "init":
        init()
        print(f"✅ 信任内核已初始化：{TRUST_DIR}")
        print("   保护文件：" + "、".join(protected_names()))
        return 0
    if cmd == "status":
        print(json.dumps(status(deep=bool(getattr(args, "deep", False))),
                         ensure_ascii=False, indent=2))
        return 0
    if cmd == "verify":
        res = verify()
        print("✅ 内核一致：未检测到未声明的自我修改" if res["ok"] else "⚠️ 检测到未声明的自我修改")
        for c in res["changed"]:
            print(f"  • {c['name']}（基线来源 {c['ref_source']}，上次登记 {c['recorded_at'] or '无'}）")
        for c in res["missing"]:
            print(f"  • {c['name']}：文件缺失")
        for c in res["untracked"]:
            print(f"  • {c['name']}：无基线也无指纹")
        return 0 if res["ok"] else 2
    if cmd == "diff":
        d = diff(args.file)
        print(d or "（与基线无差异，或该文件无基线）")
        return 0
    if cmd == "restore":
        ok, msg = restore(args.file)
        print(("✅ " if ok else "❌ ") + msg)
        return 0 if ok else 1
    if cmd == "accept":
        ok, msg = accept(args.file or None, all_=bool(args.all) or not args.file)
        print(("✅ " if ok else "❌ ") + msg)
        return 0 if ok else 1
    if cmd == "log":
        for row in ledger_tail(args.n):
            print(json.dumps(row, ensure_ascii=False))
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
