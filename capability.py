"""能力注册（Capability Registry）—— 让「插件工具」与内置工具走同一条主干。

## 为什么需要它

v3.16 之前，插件工具和内置工具走的是两条完全不相交的路：

    内置：@tool() → toolkit._REGISTRY → 六层构建 → tool_hooks 包装 → 分发
    插件：user_tools.json → chat(custom_tools=…) → _run_custom_tool → 只发一次 HTTP

后果不是「功能少」，而是**插件工具永远无法成为一等公民**：没有分组（模型在
smart_tools 的能力地图里看不到它）、没有预激活、没有钩子（快照/信任声明/出网账本）、
没有权限强制、没有健康度、没有失败记忆。卸载也只能靠改写 data 下的 JSON 文件。

本模块把这条路并回主干：插件工具通过 `toolkit.register_tool(source="capability")`
注册进**同一张注册表**，六层、钩子、分发、健康度全部复用；「插件」退化为一个
**装载与分发的单位**，而不是一种二等能力。

## 标准接入（一份能力声明）

每项能力必须在 manifest 里写清七件事，缺一不予装载：

    name         带 p_ 前缀的唯一名（禁止遮蔽内置工具）
    description  给模型看的触发场景（不是给人看的宣传语）
    parameters   OpenAI function parameters
    exec         http / python / builtin_ref / cli 四种执行体之一
    io           返回契约：最大字符数（失败一律返回「错误：…」字符串）
    budget       超时、是否写操作（决定是否自动快照）
    perms        最小权限声明；调用时**强制**校验，不是装饰
    examples     1-3 条金样（既喂模型，也做契约自检）

## 边界与取舍（v1 有意为之）

- **进程内执行，不做沙箱**：与 run_python/run_command 同一安全模型。因此 `python`
  执行体必须显式声明 `perms.python=true`——它在代码里等价于本机任意执行。
- **超时用线程而非子进程**：Python 无法中断线程，超时后工作线程仍会跑完（结果被
  丢弃）。这是为了不做子进程 RPC 而付出的代价，写在这里以免后人误以为是 bug。
- **熔断按工具计数**：连续失败 N 次后进入冷却期，期间直接返回错误而不真的执行——
  一个坏插件不该在每轮对话里反复消耗超时预算。
- **卸载是「温和失效」**：epoch 递增使旧句柄失效（返回「已卸载」），但**正在执行**
  的调用不打断；六层视图在卸载后立即重建，下一轮对话即看不到该能力。

## 与旧插件格式的关系

`.wtplugin` v2（`contents.tools` 的 HTTP 工具）仍可安装，但它们走的是旧旁路。
`adapt_legacy()` 提供单向桥：把 v2 的 tools 条目转成 v3 能力声明，迁移期用它把
存量插件搬上主干，搬完即可删除旧字段。
"""
from __future__ import annotations

import inspect
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import degrade
import toolkit

# ── 协议常量 ────────────────────────────────────────────────────────────
API_VERSION = "whaletalk.tool/1"
NAME_PREFIX = "p_"
FAIL_PREFIX = "错误"
DEFAULT_TIMEOUT = 60.0
MAX_TIMEOUT = 3600.0        # 上限放宽到 1 小时：MV 成片这类长任务的声明不该被静默压到 600s
DEFAULT_MAX_CHARS = 20000
BREAKER_FAILS = 3           # 连续失败多少次进入熔断
BREAKER_COOLDOWN = 60.0     # 熔断冷却秒数
EXEC_TYPES = ("http", "python", "builtin_ref", "cli")
LEGACY_GROUP = "🧩 扩展能力"   # 旧插件迁移时的默认分组（能力地图里要有位置）
_BUILTIN_DIR = ""   # 仓库内 capabilities/（随包分发，不可卸载）
_USER_DIR = ""      # data/capabilities（用户自建）

_NAME_RE = re.compile(r"^p_[a-z][a-z0-9_]{0,60}$")
_SLUG_RE = re.compile(r"[^0-9a-z]+")
# 会被当作「文件路径」校验的参数名（与 tool_hooks.PATH_KEYS 同口径）
_PATH_KEYS = ("path", "file", "filename", "target", "dst", "dest", "src", "output", "dir")

_lock = threading.RLock()
_PLUGINS: dict = {}     # plugin_id -> {manifest, epoch, tools, base_dir, path}
_TOOLS: dict = {}       # tool name -> 运行时状态（预算/权限/健康度/熔断）
_EPOCH = 0              # 全局单调递增：卸载即 +1，旧句柄据此失效
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wt-cap")


class CapabilityTimeout(Exception):
    """执行体超时（不向外抛，统一转成「错误：…」文案）。"""


def _fail(msg: str) -> str:
    """构造符合全仓工具返回契约的失败文案（以「错误：」开头）。"""
    return toolkit.fail(msg)


def _now() -> float:
    return time.monotonic()


def _next_epoch() -> int:
    global _EPOCH
    with _lock:
        _EPOCH += 1
        return _EPOCH


def set_dirs(builtin_dir: str = "", user_dir: str = "") -> None:
    """注入内置/用户能力包目录（api_server 启动时调用一次）。"""
    global _BUILTIN_DIR, _USER_DIR
    if builtin_dir:
        _BUILTIN_DIR = str(builtin_dir)
    if user_dir:
        _USER_DIR = str(user_dir)


def user_dir() -> str:
    """用户能力包目录（create_capability 工具落盘处）。"""
    return _USER_DIR


def builtin_dir() -> str:
    """随包分发的内置能力包目录。"""
    return _BUILTIN_DIR


def slugify(text: str) -> str:
    """能力包 id 安全化（小写字母数字与 -_）。

    中文名会被 `_SLUG_RE` 全部剔除（如「公众号写作」→ 空）。此前统一回落成 "cap"，
    导致**同一目录下多个中文名能力包 id 相同**，后装的把先装的当"热替换"顶掉——
    实测 3 个内置包只剩 2 个。故剥离后为空时按原名取哈希后缀，保证一一对应。
    """
    raw = str(text or "").strip()
    s = _SLUG_RE.sub("-", raw.lower()).strip("-")[:40]
    if s:
        return s
    return "cap-" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:6]


# ── 校验 ────────────────────────────────────────────────────────────────

def _norm_perms(perms) -> dict:
    """规范化权限声明。**缺省即无权限**（保守：不假设任何授权）。"""
    p = perms if isinstance(perms, dict) else {}
    return {
        "files": [str(x).strip() for x in (p.get("files") or []) if str(x).strip()],
        "net": bool(p.get("net")),
        "tools": [str(x).strip() for x in (p.get("tools") or []) if str(x).strip()],
        "cli": [str(x).strip() for x in (p.get("cli") or []) if str(x).strip()],
        "python": bool(p.get("python")),
    }


def _norm_budget(budget) -> dict:
    b = budget if isinstance(budget, dict) else {}
    try:
        timeout = float(b.get("timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return {
        "timeout": max(1.0, min(timeout, MAX_TIMEOUT)),
        "idempotent": bool(b.get("idempotent", True)),
        "write": bool(b.get("write", False)),
    }


def validate_manifest(manifest) -> tuple:
    """校验能力 manifest。返回 (ok, errors:list[str])。

    校验在**注册之前**跑，且拒绝任何"部分合法"的装载——半个能力包装上了比装不上
    更难排查（模型会看到 schema 但一调就错）。
    """
    errs = []
    if not isinstance(manifest, dict):
        return False, ["manifest 必须是对象"]
    api = str(manifest.get("api") or "")
    if api != API_VERSION:
        errs.append(f"api 必须为 {API_VERSION}（收到 {api or '空'}）")
    caps = manifest.get("capabilities")
    if not isinstance(caps, list) or not caps:
        return False, errs + ["capabilities 必须是非空数组"]
    names = set()
    for i, cap in enumerate(caps):
        tag = f"capabilities[{i}]"
        if not isinstance(cap, dict):
            errs.append(f"{tag} 必须是对象")
            continue
        name = str(cap.get("name") or "").strip()
        if not name:
            errs.append(f"{tag}.name 必填")
        elif not _NAME_RE.match(name):
            errs.append(f"{tag}.name 必须形如 {NAME_PREFIX}xxx（小写字母数字下划线，"
                        f"以 {NAME_PREFIX} 开头，避免遮蔽内置工具）：{name}")
        elif name in names:
            errs.append(f"{tag}.name 重复：{name}")
        else:
            names.add(name)
        if not str(cap.get("description") or "").strip():
            errs.append(f"{tag}.description 必填（模型靠它决定是否调用）")
        params = cap.get("parameters")
        if not isinstance(params, dict) or params.get("type") != "object":
            errs.append(f"{tag}.parameters 必须是 type=object 的 JSON Schema")
        ex = cap.get("exec")
        if not isinstance(ex, dict):
            errs.append(f"{tag}.exec 必填（对象）")
            continue
        etype = str(ex.get("type") or "")
        if etype not in EXEC_TYPES:
            errs.append(f"{tag}.exec.type 必须是 {'/'.join(EXEC_TYPES)} 之一（收到 {etype or '空'}）")
            continue
        if etype == "http":
            url = str(ex.get("url") or "")
            if not url.startswith(("http://", "https://")):
                errs.append(f"{tag}.exec.url 必须是 http(s) 地址")
        elif etype == "builtin_ref":
            if not str(ex.get("tool") or "").strip():
                errs.append(f"{tag}.exec.tool 必填（被复用的内置工具名）")
        elif etype == "cli":
            if not str(ex.get("command") or "").strip():
                errs.append(f"{tag}.exec.command 必填")
        elif etype == "python":
            entry = str(ex.get("entry") or "")
            if entry.count(":") != 1:
                errs.append(f"{tag}.exec.entry 必须形如 module:func")
        perms = _norm_perms(cap.get("perms"))
        if etype == "http" and not perms["net"]:
            errs.append(f"{tag}.perms.net 必须为 true（http 执行体要出网）")
        if etype == "python" and not perms["python"]:
            errs.append(f"{tag}.perms.python 必须为 true（python 执行体等价本机执行，")
        if etype == "cli" and not perms["cli"]:
            errs.append(f"{tag}.perms.cli 必须声明允许的命令名")
        if etype == "builtin_ref":
            target = str(ex.get("tool") or "").strip()
            if target and target not in perms["tools"]:
                errs.append(f"{tag}.perms.tools 必须包含 {target}（复用内置工具需显式授权）")
        io = cap.get("io") if isinstance(cap.get("io"), dict) else {}
        if io.get("max_chars") is not None:
            try:
                int(io["max_chars"])
            except (TypeError, ValueError):
                errs.append(f"{tag}.io.max_chars 必须是整数")
        groups = cap.get("groups")
        if groups is not None and not isinstance(groups, (list, tuple)):
            errs.append(f"{tag}.groups 必须是数组")
        elif not [g for g in (groups or []) if str(g).strip()]:
            # 不声明分组 = 能力地图里看不到它 = 装了等于没装。这不是风格问题：
            # smart_tools 模式下模型只能靠「组名 + 代表作」发现能力，没有分组的能力
            # 永远进不了那张地图（旧插件工具正是这样"有实现但没人用"）。
            errs.append(f"{tag}.groups 必填（至少一个分组名；可复用已有组如「🔧 系统与基础」）")
        examples = cap.get("examples")
        if examples is not None and not isinstance(examples, (list, tuple)):
            errs.append(f"{tag}.examples 必须是数组")
    return (not errs), errs


# ── 执行体 ──────────────────────────────────────────────────────────────

def _exec_http(spec: dict, args: dict, timeout: float) -> str:
    """HTTP 执行体：参数按 query（GET）或 JSON body（POST）发出。"""
    import urllib.parse
    import urllib.request

    import security
    ex = spec["exec"]
    url = str(ex.get("url") or "")
    reason = security._safe_url(url)
    if reason:
        return _fail(reason)
    method = str(ex.get("method") or "GET").upper()
    headers = dict(ex.get("headers") or {})
    data = None
    if method == "GET":
        if args:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode({k: v for k, v in args.items()}, doseq=True)
    else:
        data = json.dumps(args, ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("gbk", errors="replace")
    return text


def _exec_builtin(ex: dict, args: dict) -> str:
    """内置复用执行体：调用另一个已注册工具（插件只做「重组」，不复制实现）。

    args_map 语义：{目标参数名: 本能力参数名}；未给 args_map 时原样透传。
    """
    target = str(ex.get("tool") or "").strip()
    exec_ = toolkit.executor_for(target)
    if exec_ is None:
        return _fail(f"内置工具不存在或不可调用：{target}")
    amap = ex.get("args_map")
    if isinstance(amap, dict) and amap:
        passed = {str(k): args.get(str(v)) for k, v in amap.items()}
        passed = {k: v for k, v in passed.items() if v is not None}
    else:
        passed = dict(args)
    out = exec_(**passed)
    return "" if out is None else str(out)


def _exec_cli(ex: dict, args: dict, timeout: float) -> str:
    """命令行执行体：命令必须在 perms.cli 白名单里，且过用户的 shell 黑名单。"""
    import permissions

    cmd = str(ex.get("command") or "").strip()
    fixed = [str(a) for a in (ex.get("args") or [])]
    var = ex.get("args_from")
    extra = []
    if isinstance(var, str) and var:
        v = args.get(var)
        if isinstance(v, (list, tuple)):
            extra = [str(x) for x in v]
        elif v not in (None, ""):
            extra = [str(v)]
    argv = [cmd] + fixed + extra
    if permissions.get_data():
        allowed, reason, _argv = permissions.check_shell(" ".join(argv))
        if not allowed:
            return _fail(reason or "命令被权限策略拒绝")
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, text=True,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return _fail(f"命令不存在：{cmd}")
    except subprocess.TimeoutExpired:
        raise CapabilityTimeout(f"命令超时（>{timeout:.0f}s）：{cmd}")
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    if proc.returncode != 0 and not out.strip():
        return _fail(f"{cmd} 退出码 {proc.returncode}")
    return out.strip()


_PY_CACHE: dict = {}


def _load_py_module(base_dir: str, module: str):
    """按文件加载插件模块（不污染 sys.path；mtime 变化即重载）。"""
    rel = module.replace(".", os.sep) + ".py"
    base = os.path.normpath(base_dir or ".")
    path = os.path.normpath(os.path.join(base, rel))
    if path != base and not path.startswith(base + os.sep):
        raise ValueError(f"非法模块路径：{module}")
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    mtime = os.path.getmtime(path)
    key = (path, mtime)
    hit = _PY_CACHE.get(key)
    if hit is not None:
        return hit
    import importlib.util

    modname = "_wtcap_" + re.sub(r"[^0-9A-Za-z_]", "_", os.path.basename(path)) + \
        "_" + str(abs(hash(path)) % 100000)
    spec = importlib.util.spec_from_file_location(modname, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    added = base not in sys.path
    if added:
        sys.path.insert(0, base)
    try:
        spec.loader.exec_module(mod)
    finally:
        if added:
            try:
                sys.path.remove(base)
            except ValueError:  # noqa: silent-except: 并发下已被其它线程移除，属正常竞态
                pass
    _PY_CACHE.clear()          # 只保留最新版本，防长期驻留旧代码
    _PY_CACHE[key] = mod
    return mod


def _exec_python(spec: dict, args: dict, state: dict) -> str:
    """Python 执行体：def func(**kwargs) -> str（也兼容 def func(args: dict)）。"""
    ex = spec["exec"]
    module, func_name = str(ex.get("entry") or "").split(":", 1)
    mod = _load_py_module(state.get("base_dir") or "", module)
    fn = getattr(mod, func_name, None)
    if not callable(fn):
        return _fail(f"执行体不可调用：{ex.get('entry')}")
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        sig = None
    has_var_kw = any(p.kind == p.VAR_KEYWORD for p in (sig.parameters.values() if sig else []))
    out = fn(**args) if has_var_kw else fn(args)
    return "" if out is None else str(out)


# ── 权限强制 ────────────────────────────────────────────────────────────

def _check_perms(spec: dict, args: dict) -> str:
    """调用期权限校验。返回拒绝原因（空串=放行）。

    与「声明」的分工：声明决定**能不能做这一类事**；这里在每个具体参数上再校验
    一次（路径落在声明根内、命令在名单内），因为声明无法表达"哪个参数值"。
    """
    perms = spec.get("perms") or {}
    etype = (spec.get("exec") or {}).get("type")
    if etype == "http" and not perms.get("net"):
        return "该能力未声明 net 权限"
    if etype == "python" and not perms.get("python"):
        return "该能力未声明 python 权限"
    if etype == "cli":
        cmd = str((spec.get("exec") or {}).get("command") or "").strip()
        base = os.path.basename(cmd).lower()
        allow = [str(x).lower() for x in (perms.get("cli") or [])]
        if base not in allow and cmd.lower() not in allow:
            return f"命令不在声明的 cli 权限内：{cmd}"
    roots = [os.path.normpath(os.path.abspath(os.path.expanduser(str(r)))) for r in (perms.get("files") or [])]
    for key in _PATH_KEYS:
        v = args.get(key)
        if not isinstance(v, str) or not v.strip() or "\n" in v:
            continue
        try:
            import permissions
            # 权限模块未初始化（测试直调/未启动）：不拦——声明式权限仍由上面的
            # net/python/cli 判定负责，这里只是"叠加用户黑名单"这一层。
            if not permissions.get_data():
                allowed, reason = True, ""
            else:
                allowed, reason = permissions.check_filesystem(
                    v, write=bool((spec.get("budget") or {}).get("write")))
        except Exception as e:  # noqa: BLE001
            degrade.degrade("capability.perm_check", e, "路径权限未校验（已放行）", critical=False)
            allowed, reason = True, ""
        if not allowed:
            return reason
        if roots:
            p = os.path.normpath(os.path.abspath(v))
            if not any(p == r or p.startswith(r + os.sep) for r in roots):
                return f"路径不在声明的 files 权限内：{v}"
    return ""


# ── 守卫执行体（超时 / 熔断 / 健康度 / 降级记账）────────────────────────

def _state_of(name: str, epoch: int):
    st = _TOOLS.get(name)
    if st is None or st["epoch"] != epoch:
        return None
    return st


def _guard(name: str, spec: dict, epoch: int):
    """返回带守卫的执行体（注册进 toolkit 的那个函数）。"""
    budget = spec.get("budget") or {}
    timeout = float(budget.get("timeout") or DEFAULT_TIMEOUT)
    max_chars = int((spec.get("io") or {}).get("max_chars") or DEFAULT_MAX_CHARS)

    def run(**kwargs):
        st = _state_of(name, epoch)
        if st is None:
            return _fail(f"能力 {name} 已卸载（本次调用来自过期句柄）")
        with _lock:
            if st["breaker_until"] and _now() < st["breaker_until"]:
                left = int(st["breaker_until"] - _now())
                return _fail(f"能力 {name} 已熔断（连续失败 {st['fails']} 次），约 {left}s 后自动恢复")
            st["calls"] += 1
        reason = _check_perms(spec, kwargs)
        if reason:
            with _lock:
                st["fail"] += 1
            degrade.degrade(f"capability.{name}.perm", None,
                            f"能力 {name} 的调用被权限拒绝：{reason}", critical=False)
            return _fail(reason)
        t0 = _now()
        ok = False
        try:
            etype = (spec.get("exec") or {}).get("type")
            if etype == "builtin_ref":
                out = _exec_builtin(spec["exec"], kwargs)
            elif etype == "http":
                out = _exec_http(spec, kwargs, timeout)
            elif etype == "cli":
                out = _exec_cli(spec["exec"], kwargs, timeout)
            else:
                fut = _POOL.submit(_exec_python, spec, kwargs, {"base_dir": st.get("base_dir")})
                try:
                    out = fut.result(timeout=timeout)
                except FutureTimeout:
                    raise CapabilityTimeout(f"执行超时（>{timeout:.0f}s）")
            ok = not str(out).startswith(FAIL_PREFIX)
        except CapabilityTimeout as e:
            out = _fail(str(e))
            degrade.degrade(f"capability.{name}.timeout", e,
                            f"能力 {name} 超时，本次调用未完成", critical=False)
        except Exception as e:  # noqa: BLE001 —— 插件异常绝不外抛，统一转失败文案
            out = _fail(f"{type(e).__name__}: {e}")
            degrade.degrade(f"capability.{name}.exec", e,
                            f"能力 {name} 执行异常", critical=False)
        dur = _now() - t0
        text = "" if out is None else str(out)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n…（输出已截断，共 {len(text)} 字符，上限 {max_chars}）"
        with _lock:
            st["total_ms"] += dur * 1000.0
            if ok:
                st["ok"] += 1
                st["fails"] = 0
                st["breaker_until"] = 0.0
            else:
                st["fail"] += 1
                st["fails"] += 1
                st["last_error"] = text[:200]
                if st["fails"] >= BREAKER_FAILS:
                    st["breaker_until"] = _now() + BREAKER_COOLDOWN
        return text

    run.__name__ = name
    return run


# ── 注册 / 卸载 ─────────────────────────────────────────────────────────

def _refresh_layers() -> None:
    """让六层视图感知运行时能力变化（core 层不动）。

    失败只降级不抛：装载成功但视图没刷新，表现是「模型看不到新能力」，
    这比让一次安装直接失败更好排查（能力仍在注册表里，重载一次即可）。
    """
    try:
        import deepseek_client
        deepseek_client.refresh_capability_layers()
    except Exception as e:  # noqa: BLE001
        degrade.degrade("capability.refresh", e,
                        "能力已装载但能力地图未刷新，模型本轮看不到它（重启或重载可恢复）",
                        critical=True)


def register_manifest(manifest: dict, *, base_dir: str = "", path: str = "",
                      plugin_id: str = "", bundled: bool = False) -> dict:
    """装载一个能力包。返回 {ok, plugin_id, tools, errors}。

    原子性：先整体校验（含重名冲突），全部通过才逐个注册；任一注册失败则回滚已装部分。
    同名能力包重复装载 = 热替换（先卸旧再装新，epoch 递增）。
    """
    ok, errs = validate_manifest(manifest)
    if not ok:
        return {"ok": False, "errors": errs, "plugin_id": "", "tools": []}
    meta = manifest.get("meta") or {}
    pid = str(plugin_id or slugify(meta.get("name") or manifest.get("id") or "cap"))
    caps = [c for c in manifest.get("capabilities") or [] if isinstance(c, dict)]

    # 热替换：同名能力包先卸旧，再按新声明装（否则自己的工具会被当成"名字冲突"）。
    with _lock:
        if pid in _PLUGINS:
            _unregister_locked(pid, reason="热替换")

    # 冲突检查：不得与内置/其它能力包重名
    for cap in caps:
        name = str(cap.get("name") or "").strip()
        if toolkit.is_registered(name):
            _refresh_layers()
            return {"ok": False, "plugin_id": pid, "tools": [],
                    "errors": [f"工具名已被占用：{name}（内置工具不可被能力遮蔽）"]}
    with _lock:
        epoch = _next_epoch()

    registered = []
    try:
        for cap in caps:
            name = str(cap["name"]).strip()
            spec = {
                "name": name,
                "description": str(cap.get("description") or ""),
                "exec": dict(cap.get("exec") or {}),
                "io": dict(cap.get("io") or {}),
                "budget": _norm_budget(cap.get("budget")),
                "perms": _norm_perms(cap.get("perms")),
                "groups": tuple(cap.get("groups") or ()),
                "phrases": cap.get("phrases"),
                "preactivate": tuple(tuple(k) for k in (cap.get("preactivate") or ()) if k),
                "examples": list(cap.get("examples") or []),
            }
            schema = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec["description"],
                    "parameters": cap.get("parameters") or {"type": "object", "properties": {}},
                },
            }
            hooks = []
            if spec["budget"]["write"]:
                hooks.append("snapshot")
            if spec["perms"]["net"]:
                hooks.append("egress")
            with _lock:
                _TOOLS[name] = {
                    "plugin_id": pid, "epoch": epoch, "spec": spec,
                    "base_dir": base_dir or "", "groups": spec["groups"],
                    "calls": 0, "ok": 0, "fail": 0, "total_ms": 0.0,
                    "fails": 0, "breaker_until": 0.0, "last_error": "",
                }
            toolkit.register_tool(
                schema,
                groups=list(spec["groups"]),
                phrases=spec["phrases"],
                preactivate=[list(k) for k in spec["preactivate"]] or None,
                executor=_guard(name, spec, epoch),
                hooks=tuple(hooks),
                source=toolkit.SOURCE_CAPABILITY,
                plugin_id=pid,
            )
            registered.append(name)
    except Exception as e:  # noqa: BLE001
        for name in registered:
            try:
                toolkit.unregister(name)
            except Exception:  # noqa: silent-except: 回滚尽力而为，失败不得掩盖原始错误
                pass
            with _lock:
                _TOOLS.pop(name, None)
        degrade.degrade("capability.register", e, f"能力包装载失败并已回滚：{pid}", critical=False)
        return {"ok": False, "plugin_id": pid, "tools": [], "errors": [f"装载失败：{e}"]}

    with _lock:
        _PLUGINS[pid] = {
            "manifest": manifest, "epoch": epoch, "tools": list(registered),
            "base_dir": base_dir or "", "path": path or "",
            "bundled": bool(bundled),
            "loaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
    _refresh_layers()
    return {"ok": True, "plugin_id": pid, "errors": [], "tools": registered}


def _unregister_locked(pid: str, *, reason: str = "") -> dict:
    """（持锁调用）撤销一个能力包的所有工具。"""
    info = _PLUGINS.pop(pid, None)
    if not info:
        return {"ok": False, "error": f"未装载的能力包：{pid}", "removed": []}
    names = list(info.get("tools") or [])
    with _lock:
        for name in names:
            st = _TOOLS.get(name)
            if st is not None:
                st["epoch"] = -1        # 旧句柄立即失效（温和失效：已进入执行的调用不打断）
            _TOOLS.pop(name, None)
    removed = []
    for name in names:
        try:
            if toolkit.unregister(name):
                removed.append(name)
        except Exception as e:  # noqa: BLE001 - 单个工具撤销失败不应阻断其余
            degrade.degrade("capability.unregister", e, f"能力 {name} 未能撤销，可能仍可被调用",
                            critical=True)
    return {"ok": True, "removed": removed, "reason": reason}


def unregister_plugin(pid: str, *, reason: str = "", force: bool = False) -> dict:
    """卸载一个能力包（温和失效）。返回 {ok, removed, error}。

    **内置包（bundled）不允许卸载**：它是产品的一部分，卸载等于能力面缺失，
    用户会当成 bug 报（"公众号写作怎么没了"）。要关就用停用，别删。
    `force=True` 仅供内部热替换/重启使用。
    """
    with _lock:
        info = _PLUGINS.get(pid)
        if info is not None and info.get("bundled") and not force:
            return {"ok": False, "removed": [],
                    "error": "内置能力包只允许停用，不允许卸载"}
        res = _unregister_locked(pid, reason=reason)
    if res.get("ok"):
        _refresh_layers()
    return res


def unregister_all(*, reason: str = "reset") -> int:
    """卸载全部运行时能力（测试/重启用）。返回卸载的工具数。"""
    with _lock:
        pids = list(_PLUGINS.keys())
        total = 0
        for pid in pids:
            total += len(_unregister_locked(pid, reason=reason).get("removed") or [])
    if pids:
        _refresh_layers()
    return total


# ── 查询（工具管理面/诊断用）──────────────────────────────────────────────

def health(name: str) -> dict:
    """单个能力的健康度。"""
    st = _TOOLS.get(name)
    if st is None:
        return {}
    calls = int(st["calls"] or 0)
    return {
        "name": name,
        "plugin_id": st["plugin_id"],
        "calls": calls,
        "ok": st["ok"],
        "fail": st["fail"],
        "fail_rate": round(st["fail"] / calls, 3) if calls else 0.0,
        "avg_ms": round(st["total_ms"] / calls, 1) if calls else 0.0,
        "consecutive_fails": st["fails"],
        "breaker_open": bool(st["breaker_until"] and _now() < st["breaker_until"]),
        "last_error": st["last_error"],
    }


def health_all() -> dict:
    """全部能力健康度 + 汇总（供 /v1/status 与工具管理面）。"""
    items = [health(n) for n in list(_TOOLS.keys())]
    return {
        "count": len(items),
        "breaker_open": len([i for i in items if i.get("breaker_open")]),
        "broken": len([i for i in items if i.get("fail_rate", 0) > 0]),
        "items": items,
    }


def list_capabilities() -> list:
    """已装载能力的清单（含来源与状态），供「工具管理」页。"""
    out = []
    for name, st in list(_TOOLS.items()):
        spec = st.get("spec") or {}
        h = health(name)
        out.append({
            "name": name,
            "plugin_id": st["plugin_id"],
            "source": "capability",
            "description": spec.get("description", ""),
            "exec": (spec.get("exec") or {}).get("type", ""),
            "groups": list(spec.get("groups") or ()),
            "perms": dict(spec.get("perms") or {}),
            "budget": dict(spec.get("budget") or {}),
            "calls": h["calls"],
            "fail_rate": h["fail_rate"],
            "avg_ms": h["avg_ms"],
            "breaker_open": h["breaker_open"],
        })
    return out


def list_plugins() -> list:
    """已装载的能力包。"""
    with _lock:
        return [
            {"plugin_id": pid, "epoch": info["epoch"], "tools": list(info["tools"]),
             "loaded_at": info.get("loaded_at", ""), "path": info.get("path", ""),
             "bundled": bool(info.get("bundled")),
             "name": (info.get("manifest") or {}).get("meta", {}).get("name", pid)}
            for pid, info in _PLUGINS.items()
        ]


def epoch_of(pid: str) -> int:
    with _lock:
        info = _PLUGINS.get(pid)
        return int(info["epoch"]) if info else 0


def load_file(path: str, *, plugin_id: str = "", bundled: bool = False) -> dict:
    """从单个 manifest 文件装载能力包。返回 register_manifest 的结果。

    plugin_id 缺省用**文件名**（不含扩展名）：文件名在同目录内天然唯一，
    比 meta.name 更可靠（中文名/重名都取不到稳定的英文 id）。
    """
    try:
        with open(path, encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "plugin_id": plugin_id, "tools": [],
                "errors": [f"manifest 解析失败：{type(e).__name__}: {e}"]}
    pid = plugin_id or os.path.splitext(os.path.basename(path))[0]
    return register_manifest(manifest, base_dir=os.path.dirname(os.path.abspath(path)),
                             path=path, plugin_id=pid, bundled=bundled)


def load_dir(directory: str, *, bundled: bool = False) -> dict:
    """装载目录下全部 `*.json` 能力包。**单个包失败不影响其余**。

    启动时由 api_server 调用；返回汇总供日志与 /v1/capabilities 展示。
    失败进 errors 而不抛：一个坏包不该让整个能力主干起不来。
    """
    out = {"ok": True, "loaded": 0, "tools": 0, "errors": [], "packages": [], "skipped": []}
    if not directory or not os.path.isdir(directory):
        return out
    import glob
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        # meta.default_enabled=false：装但不启用（社区这类需要配置才有意义的能力）
        try:
            with open(path, encoding="utf-8") as f:
                _meta = (json.load(f) or {}).get("meta") or {}
            if _meta.get("default_enabled") is False:
                out["skipped"].append(os.path.basename(path))
                continue
        except Exception:  # noqa: silent-except: 解析失败不在这里报，交给 load_file 给具体错误
            pass
        res = load_file(path, bundled=bundled)
        base = os.path.basename(path)
        if res.get("ok"):
            out["loaded"] += 1
            out["tools"] += len(res.get("tools") or [])
            out["packages"].append(res.get("plugin_id"))
        else:
            detail = "; ".join(res.get("errors") or ["未知错误"])
            out["errors"].append(f"{base}: {detail}")
            degrade.degrade("capability.load_file", None,
                            f"能力包 {base} 未装载：{detail[:120]}", critical=False)
    out["ok"] = not out["errors"]
    return out


def reset_for_tests() -> None:
    """清空全部运行时能力（仅测试用）。"""
    unregister_all(reason="tests")


# ── 旧格式桥（迁移期用）──────────────────────────────────────────────────

def adapt_legacy(plugin: dict) -> dict:
    """把 `.wtplugin` v2 的 `contents.tools`（HTTP 工具）转成 v3 能力 manifest。

    迁移期单向桥：让存量插件能被新注册表装载。转换后**旧字段不再生效**——
    装上主干的是同一批工具，只是换了入口。没人调用的旧条目可安全删除。
    """
    meta = plugin.get("meta") or {}
    slug = slugify(meta.get("name") or "cap")
    caps = []
    for t in (plugin.get("contents") or {}).get("tools") or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function") or t
        raw = str(fn.get("name") or "").strip()
        if not raw:
            continue
        name = raw if raw.startswith(NAME_PREFIX) else NAME_PREFIX + slug.replace("-", "_")[:20] + "_" + raw
        name = re.sub(r"[^a-z0-9_]", "_", name.lower())[:64]
        if not _NAME_RE.match(name):
            name = (NAME_PREFIX + re.sub(r"[^a-z0-9_]", "_", raw.lower()))[:64]
        params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {
            "type": "object", "properties": {}}
        cap = {
            "name": name,
            "description": str(fn.get("description") or raw),
            "parameters": params,
            "exec": {"type": "http", "url": str(fn.get("endpoint") or ""),
                     "method": str(fn.get("method") or "GET")},
            "perms": {"net": True},
            # 旧插件通常没有分组声明；迁到主干时给一个默认组，否则能力地图里没有位置
            "groups": [str(g) for g in (t.get("groups") or [])] or [LEGACY_GROUP],
            "examples": [],
        }
        if fn.get("headers"):
            cap["exec"]["headers"] = dict(fn["headers"])
        caps.append(cap)
    return {"api": API_VERSION, "meta": meta, "capabilities": caps}
