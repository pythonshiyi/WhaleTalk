# -*- coding: utf-8 -*-
"""权限模型 v2：默认放行 + 黑名单（自由优先，用户掌权）。

设计理念（覆盖 v2.13+ 全部版本）：
- security_mode = "blacklist"（默认）：AI 默认拥有全部行动能力，
  用户通过黑名单明确禁止；黑名单可以为空。
- security_mode = "whitelist"（旧模式，可回退）：默认拒绝，按白名单放行。
- 完全智能模式（FULL_AUTO）：跳过一切审批/开关，只受黑名单约束。
- 审计日志只记录不拦截（隐私模式下可关闭）。
"""
import json
import logging
import os
import queue
import shlex
import threading
from datetime import datetime

PERMISSIONS_PATH = None
WORKSPACE_DIR = None
AUDIT_LOG_DIR = None
AUDIT_ENABLED = True
FULL_AUTO = False  # 完全智能模式：零审批、零开关；黑名单仍生效

_lock = threading.Lock()
_data = None
_approval_callback = None  # (name, args) -> (allowed, reason)
_whitelist_callback = None  # (action_type, value) -> (allowed, reason)
_dirs_cache = {"blocked": None, "allowed": None, "workspace": None}

DEFAULT_PERMISSIONS = {
    "version": 2,
    "security_mode": "blacklist",  # blacklist（默认放行+黑名单）/ whitelist（旧默认拒绝+白名单）
    "blocklist_enabled": True,     # 黑名单总开关：True=黑名单条目生效（默认空=0 限制）；False=一键全放行（连黑名单也不拦）
    "filesystem": {
        "allow_write": True,       # whitelist 模式开关；blacklist 模式忽略
        "allowed_dirs": [],        # whitelist 模式白名单；blacklist 模式忽略
        "blocked_dirs": [],        # 黑名单生效时命中的路径拒绝（默认空 = 不限制）
        "max_write_size": 50 * 1024 * 1024,
    },
    "shell": {
        "allow_run_command": True, # whitelist 模式开关；blacklist 模式忽略
        "whitelist": ["python", "pip", "pytest", "git"],  # whitelist 模式用
        "blocklist": [],           # 黑名单生效时命中的命令拒绝（默认空 = 不限制）
        "timeout": 120,
    },
    "network": {
        # 出厂默认仅预置云元数据地址（169.254.169.254）：它是唯一几乎所有
        # 合法场景都不会访问的内网地址，作为默认自由下的单点底线；其余内网/回环
        # 一律放行。用户可自行增删（blocklist_enabled=False 一键全放行时整体跳过）。
        "blocklist": ["169.254.169.254"],
    },
    # 审批清单（额外限制，默认空 = 零审批、法无禁止皆可为）。
    # 与黑名单同属「可一键启用的限制」：需要时由用户在权限页/配置中自行添加，
    # 不再由程序默认强加——「给用户给程序选择的机会，而不是粗暴的禁止」。
    "approval_actions": [],
    "approval_mode": "auto",       # whitelist 模式用：auto / confirm / deny
    "approval_timeout": 120,
    "plan_confirm": False,
}

# whitelist 模式下的高风险动作清单（blacklist 模式改用 approval_actions）
ACTION_TOOLS = (
    "write_file",
    "edit_file",
    "run_command",
    "create_doc",
    "write_code_project",
    "publish_draft",
    "start_process",
    "stop_process",
    "run_python",
    "send_email",
    "pip_install",
    "delete_file",
    "restore_snapshot",
    "batch_rename",
    "extract_archive",
    "database_execute",
    "screen_capture",
    "clipboard_get",
    "read_email",
    "image_generate",
    "run_workflow",
    "pdf_create",
    "qrcode",
    "media_ffmpeg",
    "webdav",
    "create_plugin",
    "rpa_click",
    "rpa_type",
    "rpa_hotkey",
    "rpa_move",
    "rpa_scroll",
    "screen_find_click",
    "vision_loop",
)


def init(path, workspace, audit_dir=None):
    """模块初始化：注入配置文件路径与工作目录。"""
    global PERMISSIONS_PATH, WORKSPACE_DIR, AUDIT_LOG_DIR, _data
    PERMISSIONS_PATH = path
    WORKSPACE_DIR = workspace
    AUDIT_LOG_DIR = audit_dir
    _data = _load()
    try:
        os.makedirs(workspace, exist_ok=True)
    except Exception:
        logging.warning("创建工作目录失败: %s", workspace)


def set_audit_enabled(enabled):
    global AUDIT_ENABLED
    AUDIT_ENABLED = bool(enabled)


def set_full_auto(enabled):
    """完全智能模式：零审批、零开关；黑名单仍生效。"""
    global FULL_AUTO
    FULL_AUTO = bool(enabled)


def is_full_auto():
    return FULL_AUTO


def security_mode():
    """当前安全模式：blacklist（默认放行+黑名单）/ whitelist（旧默认拒绝+白名单）。"""
    try:
        return str((_data or {}).get("security_mode", "blacklist"))
    except Exception:
        return "blacklist"


def get_data():
    return _data


def set_data(data):
    global _data
    with _lock:
        _data = data


def _migrate_v1_to_v2(data, disk):
    """旧权限文件迁移：过去的禁止项保留为黑名单，其余全部放行。"""
    try:
        fs = disk.get("filesystem") or {}
        sh = disk.get("shell") or {}
        data["security_mode"] = "blacklist"
        # 旧 blocked_dirs 继续作为黑名单
        old_blocked = [str(d) for d in (fs.get("blocked_dirs") or []) if str(d).strip()]
        data["filesystem"]["blocked_dirs"] = old_blocked
        data["filesystem"]["allowed_dirs"] = [str(d) for d in (fs.get("allowed_dirs") or [])]
        data["filesystem"]["allow_write"] = bool(fs.get("allow_write", False))
        # 旧 shell.blocklist 继续作为黑名单
        old_sh_block = [str(s) for s in (sh.get("blocklist") or []) if str(s).strip()]
        data["shell"]["blocklist"] = old_sh_block
        data["shell"]["whitelist"] = [str(s) for s in (sh.get("whitelist") or [])]
        data["shell"]["allow_run_command"] = bool(sh.get("allow_run_command", False))
        # 旧 SSRF 永远拦截云元数据：迁移为初始网络黑名单
        data["network"]["blocklist"] = ["169.254.169.254"]
        # 默认零审批（黑名单主导）；老用户已有审批清单则保留（在下方 v2 分支整体覆盖）
        data["approval_actions"] = []
        data["blocklist_enabled"] = True
        for key in ("approval_mode", "approval_timeout", "plan_confirm"):
            if key in disk:
                data[key] = disk[key]
    except Exception:
        logging.exception("权限配置迁移失败，使用默认黑名单模式")


def _load():
    data = json.loads(json.dumps(DEFAULT_PERMISSIONS))
    if PERMISSIONS_PATH and os.path.exists(PERMISSIONS_PATH):
        try:
            with open(PERMISSIONS_PATH, "r", encoding="utf-8") as f:
                disk = json.load(f)
            if int(disk.get("version", 1) or 1) < 2:
                _migrate_v1_to_v2(data, disk)
            else:
                for section in ("filesystem", "shell", "network"):
                    if isinstance(disk.get(section), dict):
                        data[section].update(disk[section])
                for key in (
                    "security_mode",
                    "blocklist_enabled",
                    "approval_actions",
                    "approval_mode",
                    "approval_timeout",
                    "plan_confirm",
                ):
                    if key in disk:
                        data[key] = disk[key]
                if str(data.get("security_mode")) not in ("blacklist", "whitelist"):
                    data["security_mode"] = "blacklist"
        except Exception:
            logging.exception("读取权限配置失败，使用默认值")
    if WORKSPACE_DIR and WORKSPACE_DIR not in data["filesystem"]["allowed_dirs"]:
        data["filesystem"]["allowed_dirs"].append(WORKSPACE_DIR)
    return data


def save():
    """保存权限配置（原子写）。"""
    if not PERMISSIONS_PATH:
        return False
    try:
        data = json.loads(json.dumps(_data))
        from persistence import atomic_json_write
        return atomic_json_write(PERMISSIONS_PATH, data, indent=2)
    except Exception:
        logging.exception("保存权限配置失败")
        return False


def resolve(path):
    """规范化路径：展开 ~、绝对化、去 .. 、realpath 解析链接。非法返回 None。"""
    try:
        p = str(path or "").strip()
        if not p:
            return None
        p = os.path.expanduser(p)
        if not os.path.isabs(p) and WORKSPACE_DIR:
            p = os.path.join(WORKSPACE_DIR, p)
        p = os.path.realpath(os.path.abspath(os.path.normpath(p)))
        return os.path.normcase(p)
    except Exception:
        return None


def _under(path, base):
    """路径是否位于 base 之下（两者均已 resolve 归一化）。"""
    base = base.rstrip("\\/")
    return path == base or path.startswith(base + os.sep)


def _dirs(dirs):
    out = []
    for d in dirs or []:
        p = resolve(d)
        if p:
            out.append(p)
    return out


def _cached_dirs(key, dirs):
    sig = tuple(str(d) for d in (dirs or []))
    cache = _dirs_cache.get(key)
    if cache is None or cache[0] != sig:
        _dirs_cache[key] = (sig, _dirs(dirs))
    return _dirs_cache[key][1]


def check_filesystem(path, write=False):
    """文件系统访问判定。

    blacklist 模式：除 blocked_dirs 外全部允许。
    whitelist 模式：保持旧行为（写开关 + 允许目录白名单 + blocked_dirs）。
    blocklist_enabled=False（一键全放行）时跳过黑名单检查。
    """
    if not _data:
        return False, "权限模块未初始化"
    p = resolve(path)
    if p is None:
        return False, f"权限拒绝：路径无效：{path}"
    if bool(_data.get("blocklist_enabled", True)):
        blocked = _cached_dirs("blocked", _data["filesystem"].get("blocked_dirs"))
        for b in blocked:
            if _under(p, b):
                return False, f"权限拒绝：路径在黑名单内：{p}"
    if security_mode() == "blacklist":
        return True, ""
    # ---- 旧 whitelist 模式 ----
    if write and not _data["filesystem"].get("allow_write", False) and not FULL_AUTO:
        return (
            False,
            "权限拒绝：写文件未开启（🛠 工具中心 → 权限 → filesystem.allow_write）。"
            "如需授权，可调用 request_permission(action_type='write') 一键开启",
        )
    allowed = _cached_dirs("allowed", _data["filesystem"].get("allowed_dirs"))
    if not allowed or not any(_under(p, a) for a in allowed):
        return (
            False,
            f"权限拒绝：路径不在允许目录内：{p}（允许目录：{_data['filesystem'].get('allowed_dirs')}）。"
            "如需授权该目录，可调用 request_permission(action_type='dir', value='<路径>') 请求加入白名单",
        )
    return True, ""


def max_write_size():
    try:
        return int(_data["filesystem"].get("max_write_size", 50 * 1024 * 1024))
    except (TypeError, ValueError):
        return 50 * 1024 * 1024


def _cmd_key(name):
    """命令名规范化键：取 basename、小写、去 Windows 可执行扩展名。

    使黑名单/白名单条目「powershell」能命中实际命令「powershell.exe」，
    反之亦然（Windows 用户常混写带不带 .exe）。
    """
    n = os.path.basename(str(name or "")).strip().lower()
    for ext in (".exe", ".com", ".bat", ".cmd"):
        if n.endswith(ext):
            return n[: -len(ext)]
    return n


_SHELL_SEPARATORS = ("|", "||", "&&", "&", ";")


def _shell_command_tokens(argv):
    """从 token 流提取「命令位置」token：首 token + 紧随 shell 连接符的 token。

    使 `a | b`、`a && b` 中后续命令同样接受黑名单检查（blacklist 模式用），
    避免黑名单被管道/链式写法绕过；引号内连接符不会被 shlex 拆成独立 token，
    不会误判。
    """
    cmds = []
    expect_cmd = True
    for tok in argv:
        if expect_cmd:
            cmds.append(tok)
            expect_cmd = False
        elif tok in _SHELL_SEPARATORS:
            expect_cmd = True
    return cmds


def check_shell(command):
    """命令判定：解析 argv，按模式执行黑名单或白名单。返回 (allowed, reason, argv)。

    blacklist 模式（默认自由）：仅当用户显式配置了 shell.blocklist 且命中时拒绝；
    默认空黑名单 = 零限制；blocklist_enabled=False（一键全放行）跳过黑名单检查。
    解析失败（如未闭合引号）在 blacklist 模式放行——自由优先，由 shell 自行报错；
    旧 whitelist 模式（默认拒绝）则仍拒绝。
    """
    if not _data:
        return False, "权限模块未初始化", None
    try:
        argv = shlex.split(str(command or ""), posix=(os.name != "nt"))
    except ValueError as e:
        if security_mode() == "blacklist":
            return True, "", None  # 默认自由：无法解析即无法证明命中黑名单，放行
        return False, f"命令解析失败：{e}", None
    if not argv:
        return False, "命令为空", None
    if bool(_data.get("blocklist_enabled", True)):
        blocklist = [_cmd_key(b) for b in _data["shell"].get("blocklist", []) if str(b).strip()]
        # blacklist 模式检查每个「命令位置」（含管道/链式后命令），防 `a | 禁命令` 绕过；
        # whitelist 旧模式保持首命令语义（下方单独判定）
        for tok in _shell_command_tokens(argv) if security_mode() == "blacklist" else argv[:1]:
            if _cmd_key(tok) in blocklist:
                return False, f"权限拒绝：命令在黑名单：{tok}", None
    if security_mode() == "blacklist":
        return True, "", argv
    # ---- 旧 whitelist 模式 ----
    if not _data["shell"].get("allow_run_command", False) and not FULL_AUTO:
        return (
            False,
            "权限拒绝：终端执行未开启（🛠 工具中心 → 权限 → shell.allow_run_command）。"
            "如需授权，可调用 request_permission(action_type='command') 请求开启",
            None,
        )
    whitelist = [_cmd_key(w) for w in _data["shell"].get("whitelist", [])]
    if _cmd_key(argv[0]) not in whitelist:
        return (
            False,
            f"权限拒绝：命令不在白名单：{argv[0]}（白名单：{_data['shell'].get('whitelist')}）。"
            "如需授权，可调用 request_permission(action_type='command', value='<命令名>') 请求加入白名单",
            None,
        )
    return True, "", argv


def shell_timeout():
    try:
        return int(_data["shell"].get("timeout", 120))
    except (TypeError, ValueError):
        return 120


def _host_blocked(host, entries):
    """主机是否命中黑名单：支持精确 IP/主机名、CIDR 网段、*.domain 后缀。"""
    host = (host or "").strip().lower()
    if not host:
        return False
    import ipaddress
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    for item in entries or []:
        item = str(item).strip().lower()
        if not item:
            continue
        if item == host:
            return True
        if ip is not None and "/" in item:
            try:
                if ip in ipaddress.ip_network(item, strict=False):
                    return True
            except ValueError:
                pass
        elif item.startswith("*.") and host.endswith(item[1:]):
            return True
    return False


def check_network_host(host):
    """网络主机判定。blacklist 模式：只拦 network.blocklist；whitelist 模式：放行（由 SSRF 旧逻辑另行处理）。

    blocklist_enabled=False（一键全放行）时跳过网络黑名单检查。
    """
    if not _data:
        return True, ""
    if security_mode() != "blacklist":
        return True, ""
    if not bool(_data.get("blocklist_enabled", True)):
        return True, ""
    blocked = _data.get("network", {}).get("blocklist", [])
    if _host_blocked(host, blocked):
        return False, f"权限拒绝：主机在网络黑名单：{host}"
    return True, ""


def check_network_url(url):
    """URL 网络判定（仅 http/https）。返回 (allowed, reason)。"""
    try:
        from urllib.parse import urlparse
        u = urlparse(str(url or ""))
        if u.scheme not in ("http", "https"):
            return False, f"权限拒绝：URL 协议不允许：{u.scheme or '（空）'}"
        host = (u.hostname or "").lower()
        if not host:
            return False, f"权限拒绝：URL 主机解析失败：{url[:80]}"
        return check_network_host(host)
    except Exception as e:
        return False, f"权限拒绝：URL 解析失败：{e}"


def approval_mode():
    try:
        return str(_data.get("approval_mode", "auto"))
    except Exception:
        return "auto"


def approval_timeout():
    try:
        return float(_data.get("approval_timeout", 120))
    except (TypeError, ValueError):
        return 120.0


def set_approval_callback(cb):
    global _approval_callback
    _approval_callback = cb


def set_whitelist_callback(cb):
    global _whitelist_callback
    _whitelist_callback = cb


def add_to_whitelist(action_type, value):
    """把操作加入白名单（旧 whitelist 模式用）。blacklist 模式下无需白名单。"""
    if security_mode() == "blacklist":
        return False, "当前为黑名单模式：默认全部放行，无需加入白名单。如要禁止操作，请在权限页添加黑名单。"
    atype = str(action_type or "").strip().lower()
    value = str(value or "").strip()
    if atype == "write":
        _data["filesystem"]["allow_write"] = True
    elif atype == "dir":
        if not value:
            return False, "目录路径不能为空"
        p = resolve(value)
        if p is None:
            return False, f"目录路径无效：{value}"
        if p not in _data["filesystem"]["allowed_dirs"]:
            _data["filesystem"]["allowed_dirs"].append(p)
    elif atype == "command":
        if not value:
            return False, "命令不能为空"
        try:
            base = os.path.basename(
                shlex.split(value, posix=(os.name != "nt"))[0]
            ).lower()
        except (ValueError, IndexError):
            base = ""
        if not base:
            return False, "命令名无效"
        blocklist = [str(b).lower() for b in _data["shell"].get("blocklist", [])]
        if base in blocklist:
            return False, f"命令 {value} 在阻止列表内，禁止加入白名单"
        whitelist = [str(w).lower() for w in _data["shell"].get("whitelist", [])]
        if base not in whitelist:
            _data["shell"]["whitelist"].append(base)
        _data["shell"]["allow_run_command"] = True
    else:
        return False, f"不支持的白名单类型：{action_type}（支持 dir / command / write）"
    save()
    return True, "已加入白名单"


def request_whitelist(action_type, value):
    """request_permission 工具入口：blacklist 模式直接提示无需授权。"""
    if FULL_AUTO or security_mode() == "blacklist":
        if security_mode() == "blacklist":
            return False, "黑名单模式默认全部放行，无需授权；如要禁止操作请添加黑名单。"
        return add_to_whitelist(action_type, value)
    if _whitelist_callback is None:
        return False, "白名单请求通道不可用"
    try:
        return _whitelist_callback(action_type, value)
    except Exception:
        logging.exception("白名单请求回调异常")
        return False, "白名单请求通道异常"


def request_approval(name, args):
    """审批闸门。

    blacklist 模式：仅 approval_actions 列表中的动作需要用户确认。
    whitelist 模式：保持旧行为（ACTION_TOOLS + approval_mode）。
    完全智能模式：直接放行（黑名单仍生效）。
    """
    if FULL_AUTO:
        return True, ""
    if security_mode() == "blacklist":
        actions = _data.get("approval_actions") or []
        if str(name) not in actions:
            return True, ""
        if _approval_callback is None:
            return False, "权限拒绝：审批通道不可用"
        try:
            return _approval_callback(name, args)
        except Exception:
            logging.exception("审批回调异常")
            return False, "审批通道异常"
    # ---- 旧 whitelist 模式 ----
    if str(name) not in ACTION_TOOLS:
        return True, ""
    mode = approval_mode()
    if mode == "deny":
        return False, "权限拒绝：审批模式为 deny"
    if mode == "auto":
        return True, ""
    if _approval_callback is None:
        return False, "权限拒绝：审批通道不可用"
    try:
        return _approval_callback(name, args)
    except Exception:
        logging.exception("审批回调异常")
        return False, "审批通道异常"


def _audit_sanitize(s, limit=200):
    if not isinstance(s, str):
        s = str(s)
    s = s.replace("\r", "\\r").replace("\n", "\\n")
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


def audit(action, target, detail="", result="ok"):
    """写审计日志（只记录不拦截；隐私模式下跳过）。"""
    if not AUDIT_ENABLED or not AUDIT_LOG_DIR:
        return
    try:
        os.makedirs(AUDIT_LOG_DIR, exist_ok=True)
        line = (
            f"{datetime.now():%Y-%m-%d %H:%M:%S} [{_audit_sanitize(action, 40)}] "
            f"{_audit_sanitize(target)} | {_audit_sanitize(detail)} | "
            f"{_audit_sanitize(result, 40)}\n"
        )
        path = os.path.join(AUDIT_LOG_DIR, "actions.log")
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        try:
            if os.path.getsize(path) > 10 * 1024 * 1024:
                os.replace(path, path + ".1")
        except OSError:
            pass
    except Exception:
        logging.exception("审计日志写入失败")


# 敏感字段：参数摘要中打码，防止密钥/口令落盘
_SENSITIVE_ARG_KEYS = ("password", "token", "secret", "key", "api_key", "apikey", "auth", "cookie", "value")


# ── 工具留痕异步写入（P2：不在工具调用线程同步写盘）───────────────────
# 高频工具循环（100 轮 × 每轮并行多工具）下，每工具一次 open/append/close 会把
# 磁盘 I/O 压在调用线程上；改为有界队列 + 后台工作线程批量 drain 聚合写，
# 调用线程只做格式化与 put_nowait（非阻塞，命中上限丢弃而非阻塞/堆积）。
# 文件轮转（>10MB → .1）只发生在 worker 线程内，单写者天然无竞态。
# actions.log（低频 audit()）保持同步写，保证 /v1/audit 等按需读取端点的即时性。
_TRACE_QUEUE_MAX = 4096          # 有界队列上限：病理循环下丢弃而非吃掉内存
_TRACE_ROTATE_BYTES = 10 * 1024 * 1024
_TRACE_QUEUE = queue.Queue(maxsize=_TRACE_QUEUE_MAX)
_TRACE_DROPPED = {"n": 0}
_TRACE_WORKER = None
_TRACE_WORKER_LOCK = threading.Lock()


class _FlushMarker:
    """flush 哨兵：worker 处理到它时，其之前入队的行已全部落盘并置位事件。"""

    __slots__ = ("event",)

    def __init__(self):
        self.event = threading.Event()


def _trace_drain_batch():
    """取空当前队列：返回 (待写行, 顺带取到的 flush 哨兵)。

    哨兵绝不在此置位——其语义是「排在我之前的行全部落盘」后才可返回，
    drain 阶段置位会让 tool_trace_flush() 在本批真正写盘前虚假返回。
    置位统一推迟到 worker 写完本批之后（见 _trace_worker）。
    """
    batch = []
    markers = []
    while True:
        try:
            nxt = _TRACE_QUEUE.get_nowait()
        except queue.Empty:
            return batch, markers
        if isinstance(nxt, _FlushMarker):
            markers.append(nxt)
        else:
            batch.append(nxt)


def _trace_fire_markers(markers):
    """批量置位 flush 哨兵事件（仅供 worker 在批写盘完成后调用）。"""
    for m in markers:
        m.event.set()


def _trace_write_dir(batch, last_dir):
    """解析当前 AUDIT_LOG_DIR 并写一批；目录变化时重建。返回最新 last_dir。"""
    log_dir = AUDIT_LOG_DIR
    if log_dir != last_dir:
        try:
            if log_dir:
                os.makedirs(log_dir, exist_ok=True)
        except Exception:
            log_dir = None
        last_dir = log_dir
    if log_dir and batch:
        _trace_write_batch(log_dir, batch)
    return last_dir


def _trace_worker():
    """后台写线程：聚合 drain 队列后一次性 append（减少 syscall），随后轮转检查。

    每批读取当前 AUDIT_LOG_DIR（不固化启动时目录）——init 可能换目录（测试/重配），
    始终写到「此刻生效」的日志目录。flush 哨兵：先落盘本批，再置位其事件
    （保证 flush 返回时，排在其前的行已全部落盘）。
    """
    last_dir = None
    while True:
        try:
            item = _TRACE_QUEUE.get(timeout=0.5)
        except queue.Empty:
            continue
        if item is None:  # 保留：None 为硬停止哨兵（当前未用，预留）
            break
        if isinstance(item, _FlushMarker):
            # 到哨兵时，其前序行已在上几轮写完；再顺带清空其后积压，随后置位
            batch, markers = _trace_drain_batch()
            last_dir = _trace_write_dir(batch, last_dir)
            _trace_fire_markers(markers)
            item.event.set()
            continue
        batch, markers = _trace_drain_batch()
        batch.insert(0, item)
        last_dir = _trace_write_dir(batch, last_dir)
        _trace_fire_markers(markers)


def _trace_write_batch(log_dir, lines):
    path = os.path.join(log_dir, "tools.log")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.writelines(lines)
        try:
            if os.path.getsize(path) > _TRACE_ROTATE_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
    except Exception:
        logging.exception("工具留痕异步写入失败")


def _ensure_trace_worker():
    global _TRACE_WORKER
    if _TRACE_WORKER is not None and _TRACE_WORKER.is_alive():
        return
    with _TRACE_WORKER_LOCK:
        if _TRACE_WORKER is not None and _TRACE_WORKER.is_alive():
            return
        _TRACE_WORKER = threading.Thread(
            target=_trace_worker, name="wt-tools-log", daemon=True
        )
        _TRACE_WORKER.start()


def tool_trace_flush(timeout=5.0):
    """排空待写留痕（服务停止 / 需要读取 logs/tools.log 前调用）。

    无工作线程或队列满等异常下静默返回（留痕属旁路，不允许影响主流程）。
    """
    worker = _TRACE_WORKER
    if worker is None or not worker.is_alive():
        return
    try:
        marker = _FlushMarker()
        _TRACE_QUEUE.put(marker)
        marker.event.wait(timeout)
    except Exception:
        pass


def _arg_summary(args, limit=120):
    """工具参数摘要：只保留非敏感键值 + 打码敏感键，单行截断。"""
    if not isinstance(args, dict):
        return _audit_sanitize(str(args), limit)
    parts = []
    for k, v in args.items():
        ks = str(k).lower()
        if any(s in ks for s in _SENSITIVE_ARG_KEYS):
            parts.append(f"{k}=***")
        elif isinstance(v, (list, tuple)):
            parts.append(f"{k}=[{len(v)}项]")
        else:
            parts.append(f"{k}={_audit_sanitize(str(v), 60)}")
    s = " ".join(parts)
    return s if len(s) <= limit else s[:limit] + "…"


def tool_trace(name, args, result, duration):
    """统一工具调用留痕（D2）：读/写/查一律记录——输入摘要 + 输出截断 + 耗时。
    补齐 fetch_url/search_web 等读操作此前零留痕的排障盲区。
    P2：只格式化 + 入队（非阻塞），实际写盘交给后台工作线程批量聚合。"""
    if not AUDIT_ENABLED or not AUDIT_LOG_DIR:
        return
    try:
        res = _audit_sanitize(result, 200)
        line = (
            f"{datetime.now():%Y-%m-%d %H:%M:%S} [tool:{_audit_sanitize(name, 40)}] "
            f"{_arg_summary(args)} | {res} | {duration:.2f}s\n"
        )
        try:
            _ensure_trace_worker()
            _TRACE_QUEUE.put_nowait(line)
        except queue.Full:
            # 队列满：丢弃并计数（低频告警），绝不让留痕拖慢或阻塞工具调用
            _TRACE_DROPPED["n"] += 1
            n = _TRACE_DROPPED["n"]
            if n <= 3 or n % 1000 == 0:
                logging.warning("工具留痕队列已满，丢弃日志（累计 %s 条）", n)
    except Exception:
        # 格式化异常属旁路：静默丢弃（写 debug 而非 logging.exception 避免二次 IO）
        logging.debug("工具留痕格式化失败", exc_info=True)
