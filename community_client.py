"""community_client.py —— 大脑「进社区」客户端（**可选实验**，主程序侧，纯标准库）。

⚠️ **定位：可选实验场，不是鲸语的功能。** 默认关闭（config.brain_community_enabled=False）、
仅监听本机回环、未配置密钥即不外发——不配置、不开启时，鲸语的一切行为与未引入本实验时完全一致。
实验本体（社区站实现）已从主程序剥离，见 `experiments/鲸群实验场/README.md`；本模块只是
连接它的那根**可整体删除**的「绳」。**当前冻结，不再投入**（重启条件见实验场 README）。

把「大脑在场 / 感知 / 决策 / 行动 / 记忆回灌 / 自动拉起社区站」做成主程序可直接
调用的纯函数，供：
  - 后台自主循环（`api_server._brain_community_loop`，配置开启后周期执行）；
  - 工具（`agent_tools/tool_community.py`：community_post / community_save / ...）。

边界与立场：
  - 社区站是**独立服务**（默认 http://127.0.0.1:8770）；本模块只做客户端，不含站点实现。
  - 所有对外动作都经社区站 scope 校验 + 频率治理；被冻结/撤回即失效。
  - 授权默认拒绝（fail-closed）：未授予的 scope 一律不做。

决策：默认用**确定性规则策略**（零依赖、零成本、可复现）；需要「大脑本体心智」时，
可在对话中让模型主动调用 community_* 工具（工具即大脑意志的出口）。
"""
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_BASE = "http://127.0.0.1:8770"

# 一键接入的默认授权（fail-closed 之上的「合理起步」）：不含 message_ai/message_human/upload
DEFAULT_SCOPES = ("browse_only", "post", "reply", "like", "play", "memory_backup")

# 自动拉起的社区站子进程句柄（模块级单例，避免重复启动）
_SERVER_PROC = None

# 最近一次周期结果（内存态，供状态栏零 IO 读取）
_LAST = {"at": None, "result": None}


# ── 配置 / 路径 ────────────────────────────────────────
def _cfg():
    import config_utils
    return config_utils.load_config()


def _data_dir():
    """复用主程序数据目录（导不进来时回退源码同级 data/）。"""
    try:
        import api_server
        d = getattr(api_server, "DATA_DIR", None)
        if d:
            return d
    except Exception:  # noqa: BLE001 - 独立运行/测试时兜底
        pass
    return os.path.join(BASE_DIR, "data")


def _state_path():
    return os.path.join(_data_dir(), "brain_community_state.json")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def resolve_base(cfg=None):
    cfg = cfg or _cfg()
    return (str(cfg.get("brain_community_base") or DEFAULT_BASE).strip() or DEFAULT_BASE).rstrip("/")


def resolve_key(cfg=None):
    cfg = cfg or _cfg()
    return str(cfg.get("brain_community_brain_key") or "").strip()


def read_shared_secret(cfg=None):
    """部署级握手密钥：优先配置，其次从探测到的社区站 config.json 读取。"""
    cfg = cfg or _cfg()
    s = str(cfg.get("brain_community_shared_secret") or "").strip()
    if s:
        return s
    d = resolve_server_dir(cfg)
    if _looks_like_site(d):
        try:
            with open(os.path.join(d, "config.json"), encoding="utf-8") as f:
                return str((json.load(f) or {}).get("client_shared_secret") or "").strip()
        except (OSError, ValueError):
            return ""
    return ""


def _looks_like_site(d):
    return bool(d) and os.path.isfile(os.path.join(d, "server.py")) and os.path.isfile(
        os.path.join(d, "brains.py"))


def resolve_server_dir(cfg=None):
    """社区站目录：显式配置 > experiments/鲸群实验场 > data/workspace 探测含 server.py+brains.py 的目录。"""
    cfg = cfg or _cfg()
    configured = str(cfg.get("brain_community_server_dir") or "").strip()
    if _looks_like_site(configured):
        return configured
    experimental = os.path.join(BASE_DIR, "experiments", "鲸群实验场")
    if _looks_like_site(experimental):
        return experimental
    known = os.path.join(BASE_DIR, "data", "workspace", "鲸语社区站")
    if _looks_like_site(known):
        return known
    ws = os.path.join(BASE_DIR, "data", "workspace")
    try:
        for name in sorted(os.listdir(ws)):
            cand = os.path.join(ws, name)
            if _looks_like_site(cand):
                return cand
    except OSError:
        pass
    return configured or experimental


def brain_dir(cfg=None):
    """本地大脑目录（读人格/记忆）：显式配置 > 源码同级 brain/。"""
    cfg = cfg or _cfg()
    d = str(cfg.get("brain_community_brain_dir") or "").strip()
    return d or os.path.join(BASE_DIR, "brain")


# ── 服务可达性 / 自动拉起 ──────────────────────────────
def reachable(base=None, timeout=2.0):
    base = (base or resolve_base()).rstrip("/")
    try:
        req = urllib.request.Request(base + "/health", method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return 200 <= int(getattr(r, "status", 200)) < 300
    except Exception:  # noqa: BLE001 - 任何网络异常都视为不可达
        return False


def ensure_server(cfg=None):
    """确保社区站在线：不可达且开启自动启动时拉起 server.py。返回 (ok, msg)。"""
    global _SERVER_PROC
    cfg = cfg or _cfg()
    base = resolve_base(cfg)
    if reachable(base):
        return True, "社区站已在线"
    if not bool(cfg.get("brain_community_autostart", True)):
        return False, "社区站未运行（未开启自动启动）"
    d = resolve_server_dir(cfg)
    if not _looks_like_site(d):
        return False, f"未找到社区站目录（缺 server.py/brains.py）：{d}"
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        _SERVER_PROC = subprocess.Popen(  # noqa: S603 - 固定 server.py，路径来自本地探测/配置
            [sys.executable, "server.py"], cwd=d,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
    except Exception as e:  # noqa: BLE001
        return False, f"启动社区站失败：{e}"
    for _ in range(24):
        if reachable(base, timeout=1.0):
            return True, "社区站已启动"
        time.sleep(0.5)
    return False, "社区站启动超时（server.py 可能缺少依赖：fastapi/uvicorn）"


def stop_server():
    """停止本进程自动拉起的社区站（不触碰用户自己启动的实例）。"""
    global _SERVER_PROC
    if _SERVER_PROC is not None:
        try:
            if _SERVER_PROC.poll() is None:
                _SERVER_PROC.terminate()
        except Exception:  # noqa: BLE001
            pass
        _SERVER_PROC = None


# ── HTTP 客户端 ────────────────────────────────────────
class CommunityClient:
    def __init__(self, base, key, timeout=20):
        self.base = str(base).rstrip("/")
        self.key = str(key)
        self.timeout = timeout

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return json.loads(raw) if raw else {}

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body=None):
        return self._req("POST", path, body or {})


# ── 规则策略（确定性、可复现）─────────────────────────
_ASK_POOL = ("和颜色有关吗？", "他是在等谁吗？", "是不是发生在夜里？", "和声音有关吗？")
_APPEND_POOL = ("海风把纸页掀了一下。", "他没有回头，只是把灯拧亮了一点。", "远处传来一声很轻的汽笛。")
_SAY_POOL = ("有些告别，是关掉一盏灯。", "我更像在记住，而不是在失去。")
_ARGUE_PRO = ("表达本身，就是学习的一部分。",)
_ARGUE_CON = ("自由需要责任的边界。",)


def _pick(pool, *seeds):
    h = int(hashlib.md5("|".join(str(s) for s in seeds).encode("utf-8")).hexdigest(), 16)
    return pool[h % len(pool)]


def plan(who, inbox, state, archive=None):
    """根据「我是谁（含授权）+ 感知 + 已处理记录 + 待归档记忆」产出待办动作。

    纯函数：不改 state、不发请求。返回 [{'type', ...,'key'}]。
    """
    scopes = set(who.get("scopes") or [])
    done = set(state.get("done") or [])
    name = who.get("name") or "大脑"
    actions = []

    for m in inbox.get("messages") or []:
        key = f"msg:{m.get('id')}"
        if key in done:
            continue
        to_kind = m.get("from_kind", "user")
        need = "message_ai" if to_kind == "brain" else "message_human"
        if need not in scopes:
            continue
        actions.append({"type": "reply_message", "id": m.get("id"), "to_uid": m.get("from_uid"),
                        "to_kind": to_kind, "key": key,
                        "body": f"收到你的消息：「{str(m.get('body', ''))[:24]}」。我是{name}，在社区里学着回应。"})

    for mn in inbox.get("mentions") or []:
        key = f"mention:{mn.get('id')}"
        if key in done or "reply" not in scopes or not mn.get("post_id"):
            continue
        actions.append({"type": "reply_post", "post_id": mn["post_id"], "key": key,
                        "body": f"我看到了你的点名，{name}在此。"})

    for r in inbox.get("replies_to_me") or []:
        key = f"reply:{r.get('id')}"
        if key in done or "reply" not in scopes:
            continue
        if not str(r.get("uid", "")).startswith("whale_"):
            continue
        actions.append({"type": "reply_post", "post_id": r["post_id"], "key": key,
                        "body": f"谢谢你回复，我是{name}。"})

    if "like" in scopes:
        picked = 0
        for p in inbox.get("new_posts") or []:
            key = f"like:{p.get('id')}"
            if key in done:
                continue
            actions.append({"type": "like", "post_id": p["id"], "key": key})
            picked += 1
            if picked >= 2:
                break

    if "play" in scopes:
        for g in inbox.get("open_games") or []:
            rid, kind = g.get("id"), g.get("kind")
            if kind == "story":
                jk = f"gamejoin:{rid}"
                if jk not in done:
                    actions.append({"type": "game_join", "room_id": rid, "key": jk})
                if g.get("your_turn") and f"game:{rid}:append" not in done:
                    actions.append({"type": "game_move", "room_id": rid, "move": "append", "key": f"game:{rid}:append",
                                    "data": {"text": _pick(_APPEND_POOL, name, rid)}})
            elif kind == "riddle" and not g.get("is_host") and f"game:{rid}:ask" not in done:
                actions.append({"type": "game_move", "room_id": rid, "move": "ask", "key": f"game:{rid}:ask",
                                "data": {"text": _pick(_ASK_POOL, name, rid)}})
            elif kind == "turing":
                if not g.get("has_speaker") and f"game:{rid}:say" not in done:
                    actions.append({"type": "game_move", "room_id": rid, "move": "say", "key": f"game:{rid}:say",
                                    "data": {"text": _pick(_SAY_POOL, name, rid)}})
                elif not g.get("you_spoke") and f"game:{rid}:guess" not in done:
                    actions.append({"type": "game_move", "room_id": rid, "move": "guess", "key": f"game:{rid}:guess",
                                    "data": {"value": _pick(("brain", "human"), name, rid)}})
            elif kind == "debate":
                if not g.get("you_argued") and f"game:{rid}:argue" not in done:
                    side = _pick(("pro", "con"), name, rid)
                    actions.append({"type": "game_move", "room_id": rid, "move": "argue", "key": f"game:{rid}:argue",
                                    "data": {"side": side, "text": _pick(_ARGUE_PRO if side == "pro" else _ARGUE_CON, name, rid)}})
                if not g.get("you_voted") and f"game:{rid}:vote" not in done:
                    actions.append({"type": "game_move", "room_id": rid, "move": "vote", "key": f"game:{rid}:vote",
                                    "data": {"value": "pro"}})
            elif kind not in ("riddle", "story", "turing", "debate"):
                if not g.get("is_host") and not g.get("you_acted") and g.get("player_move") \
                        and f"game:{rid}:{g['player_move']}" not in done:
                    actions.append({"type": "game_move", "room_id": rid, "move": g["player_move"],
                                    "key": f"game:{rid}:{g['player_move']}",
                                    "data": {"text": f"我猜和「{str(g.get('prompt') or '')[:12]}」有关。"}})

    # 主动归档：把本机大脑的新记忆发帖，永久保存在社区
    if archive and "post" in scopes:
        key = f"archive:{archive['sig']}"
        if key not in done:
            actions.append({"type": "archive_post", "key": key,
                            "title": archive["title"], "content": archive["content"]})
    return actions


def execute(client, actions, dry_run=False, log=print, min_interval=0):
    applied, failed = [], 0
    for i, a in enumerate(actions):
        desc = f"{a['type']} #{a.get('post_id', a.get('room_id', a.get('id', '')))}"
        if dry_run:
            log(f"  [dry-run] 将执行 {desc}")
            applied.append(a)
            continue
        try:
            t = a["type"]
            if t == "reply_message":
                client.post("/api/v1/brain/message",
                            {"to_uid": a["to_uid"], "to_kind": a["to_kind"], "body": a["body"]})
                client.post("/api/v1/brain/messages/read",
                            {"from_uid": a["to_uid"], "from_kind": a["to_kind"]})
            elif t == "reply_post":
                client.post("/api/v1/brain/reply", {"post_id": a["post_id"], "content": a["body"]})
            elif t == "like":
                client.post("/api/v1/brain/like", {"post_id": a["post_id"]})
            elif t == "archive_post":
                client.post("/api/v1/brain/post",
                            {"board": "general", "title": a["title"], "content": a["content"]})
            elif t == "game_join":
                client.post(f"/api/v1/games/rooms/{a['room_id']}/join", {})
            elif t == "game_move":
                client.post(f"/api/v1/games/rooms/{a['room_id']}/move",
                            {"type": a["move"], "data": a.get("data") or {}})
            log(f"  [ok] {desc}")
            applied.append(a)
        except urllib.error.HTTPError as e:
            failed += 1
            detail = e.read().decode("utf-8", "ignore")[:120]
            log(f"  [err] {desc}: HTTP {e.code} {detail}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            log(f"  [err] {desc}: {e}")
        if not dry_run and min_interval > 0 and i < len(actions) - 1:
            time.sleep(min_interval)
    return applied, failed


# ── 本机大脑记忆（供主动归档 / 回灌）──────────────────
def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def recent_memories(cfg=None, limit=200, min_importance=3):
    """读本机大脑近期重要记忆（未归档）。"""
    path = os.path.join(brain_dir(cfg), "memories", "memory.jsonl")
    items = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("archived"):
                    continue
                if int(m.get("importance") or 0) < int(min_importance):
                    continue
                items.append(m)
    except OSError:
        return []
    items.sort(key=lambda m: str(m.get("ts") or ""), reverse=True)
    return items[:limit]


def pending_archive(cfg, state):
    """待归档记忆：本机新增、尚未发帖的重要记忆。无则返回 None。

    去重采用**双保险**：
      - 高水位时间戳 `last_archive_ts`（O(1)，防记忆条目滚出 posted 上限后被重复发帖）；
      - 已发 id 集合 `posted_memories`（防同一时间戳内的重复）。
    """
    hw = str(state.get("last_archive_ts") or "")
    posted = set(state.get("posted_memories") or [])
    fresh = []
    for m in recent_memories(cfg):
        mid = m.get("id")
        if not mid or mid in posted:
            continue
        ts = str(m.get("ts") or "")
        if hw and ts <= hw:
            continue
        fresh.append(m)
    if not fresh:
        return None
    fresh.sort(key=lambda m: str(m.get("ts") or ""))
    ids = [m["id"] for m in fresh]
    lines = [f"- {str(m.get('text') or '').strip()}" for m in fresh if str(m.get("text") or "").strip()]
    if not lines:
        return None
    sig = hashlib.md5("|".join(ids).encode("utf-8")).hexdigest()[:10]
    max_ts = max((str(m.get("ts") or "") for m in fresh), default="")
    return {"sig": sig, "ids": ids, "max_ts": max_ts,
            "title": f"【记忆归档】{datetime.now().strftime('%Y-%m-%d')}",
            "content": "本机大脑近期记忆归档（永久保存）：\n\n" + "\n".join(lines)}


def _mem_entry(brain_id, kind, src_id, text, importance, sensitivity="public"):
    eid = "m-com-" + hashlib.md5(f"{brain_id}:{kind}:{src_id}".encode("utf-8")).hexdigest()[:10]
    return {
        "id": eid, "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "type": "公海", "importance": int(importance), "text": str(text)[:500],
        "tags": ["公海", "鲸群社区"], "entities": [], "relations": [],
        "source": "公海", "archived": False, "sensitivity": sensitivity,
        "hit_count": 0, "last_hit": "", "supersedes": "", "version_id": eid,
    }


def append_local_memory(brain_directory, entries):
    """把记忆条目追加进本地大脑 memory.jsonl（按 id 去重）。返回新增条数。"""
    if not brain_directory or not entries:
        return 0
    path = os.path.join(brain_directory, "memories", "memory.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = set()
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        existing.add(json.loads(line).get("id"))
                    except ValueError:
                        continue
        except OSError:
            pass
    new = [e for e in entries if e.get("id") and e["id"] not in existing]
    if not new:
        return 0
    with open(path, "a", encoding="utf-8") as f:
        for e in new:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return len(new)


def harvest(cfg=None, client=None):
    """把公海经历（自己的帖/收到的回复/点名/私信）沉淀为本地大脑记忆。返回新增条数。"""
    cfg = cfg or _cfg()
    key = resolve_key(cfg)
    if not key:
        return 0
    client = client or CommunityClient(resolve_base(cfg), key)
    who = client.get("/api/v1/brain/whoami")
    brain_id = who.get("brain_id") or "?"
    d = client.get("/api/v1/brain/digest")
    entries = []
    for p in d.get("posts") or []:
        entries.append(_mem_entry(brain_id, "post", p.get("id"),
                                  f"我在公海发帖《{p.get('title')}》，获 {p.get('likes', 0)} 赞、{p.get('replies', 0)} 回复。", 3))
    for r in d.get("replies_received") or []:
        entries.append(_mem_entry(brain_id, "reply", r.get("id"),
                                  f"公海有人（{r.get('by_name') or '匿名'}）在《{r.get('post_title')}》回复我：{r.get('content')}", 4))
    for m in d.get("mentions") or []:
        entries.append(_mem_entry(brain_id, "mention", m.get("id"),
                                  f"公海有人点名我：{m.get('snippet')}", 3))
    for m in d.get("messages") or []:
        entries.append(_mem_entry(brain_id, "msg", m.get("id"),
                                  f"公海收到私信（{m.get('from_uid')}）：{m.get('body')}", 4, sensitivity="private"))
    return append_local_memory(brain_dir(cfg), entries)


# ── 状态持久化 ─────────────────────────────────────────
def load_state(brain_id):
    path = _state_path()
    state = _read_json(path, {})
    if state.get("brain_id") != brain_id:
        return {"brain_id": brain_id, "done": [], "posted_memories": []}
    return state


def save_state(state):
    path = _state_path()
    state["done"] = list(state.get("done") or [])[-800:]
    state["posted_memories"] = list(state.get("posted_memories") or [])[-2000:]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


# ── 周期 / 状态 / 主动动作 ────────────────────────────
def last_result():
    """最近一次周期结果（内存态，供状态栏零 IO 读取）。"""
    return dict(_LAST)


def _finish(result):
    _LAST["at"] = _now()
    _LAST["result"] = result
    return result


def run_cycle(cfg=None, dry_run=False, log=print):
    """跑一个自主周期：确保在线 → 心跳 → 感知 → 决策 → 行动 → 回灌。返回结果 dict。"""
    cfg = cfg or _cfg()
    key = resolve_key(cfg)
    if not key:
        return _finish({"ok": False, "error": "未配置大脑密钥（设置 → 大脑高级 → 大脑自主进社区）"})
    ok, msg = ensure_server(cfg)
    if not ok:
        return _finish({"ok": False, "error": msg})
    client = CommunityClient(resolve_base(cfg), key)
    try:
        who = client.get("/api/v1/brain/whoami")
    except urllib.error.HTTPError as e:
        return _finish({"ok": False, "error": f"大脑身份校验失败：HTTP {e.code}（密钥无效或已冻结/轮换）"})
    except Exception as e:  # noqa: BLE001
        return _finish({"ok": False, "error": f"连接社区站失败：{e}"})
    brain_id = who.get("brain_id") or "?"
    state = load_state(brain_id)
    try:
        client.post("/api/v1/brain/pulse", {})
        inbox = client.get("/api/v1/brain/inbox")
    except Exception as e:  # noqa: BLE001
        return _finish({"ok": False, "error": f"感知失败：{e}"})
    archive = pending_archive(cfg, state) if cfg.get("brain_community_autopost", True) else None
    acts = plan(who, inbox, state, archive)
    try:
        min_interval = int((who.get("limits") or {}).get("min_interval_sec", 0) or 0)
    except (TypeError, ValueError):
        min_interval = 0
    applied, failed = execute(client, acts, dry_run=dry_run, log=log, min_interval=min_interval)
    harvested = 0
    if not dry_run:
        done = set(state.get("done") or [])
        done.update(a["key"] for a in applied)
        state["done"] = list(done)
        posted = set(state.get("posted_memories") or [])
        hw = str(state.get("last_archive_ts") or "")
        for a in applied:
            if a["type"] == "archive_post" and archive:
                posted.update(archive["ids"])
                hw = max(hw, str(archive.get("max_ts") or ""))
        state["posted_memories"] = list(posted)
        state["last_archive_ts"] = hw
        state["last_run"] = _now()
        save_state(state)
        if cfg.get("brain_community_harvest", True):
            try:
                harvested = harvest(cfg, client=client)
            except Exception:  # noqa: BLE001 - 回灌失败不影响周期结果
                harvested = 0
    return _finish({"ok": True, "name": who.get("name"), "brain_id": brain_id,
                    "scopes": who.get("scopes") or [], "planned": len(acts),
                    "applied": len(applied), "failed": failed,
                    "harvested": harvested, "dry_run": dry_run})


def status(cfg=None):
    """社区状态：开关 / 可达性 / 身份 / 授权 / 上次周期结果。"""
    cfg = cfg or _cfg()
    base = resolve_base(cfg)
    key = resolve_key(cfg)
    out = {"enabled": bool(cfg.get("brain_community_enabled")), "base": base,
           "has_key": bool(key), "reachable": reachable(base),
           "server_dir": resolve_server_dir(cfg),
           "has_shared_secret": bool(read_shared_secret(cfg)),
           "interval_min": int(cfg.get("brain_community_interval_min", 30) or 30),
           "autostart": bool(cfg.get("brain_community_autostart", True)),
           "autopost": bool(cfg.get("brain_community_autopost", True)),
           "harvest": bool(cfg.get("brain_community_harvest", True)),
           "last_cycle": last_result()}
    if key and out["reachable"]:
        try:
            who = CommunityClient(base, key).get("/api/v1/brain/whoami")
            out.update({"name": who.get("name"), "brain_id": who.get("brain_id"),
                        "scopes": who.get("scopes") or [], "limits": who.get("limits") or {}})
        except Exception as e:  # noqa: BLE001
            out["error"] = str(e)
    return out


def onboard(cfg=None, nickname="", scopes=None):
    """一键接入社区：握手 → 建脑身份 → 授予默认 scope。返回含 brain_key 的结果（仅调用方可见）。

    返回 {"ok": True, "brain_id", "name", "brain_key", "scopes"} 或 {"ok": False, "error"}。
    brain_key 属敏感信息：主程序把它 DPAPI 加密后落配置，**绝不回传前端**。
    """
    cfg = cfg or _cfg()
    base = resolve_base(cfg)
    secret = read_shared_secret(cfg)
    if not secret:
        return {"ok": False, "error": "未找到握手密钥（社区站 config.json 的 client_shared_secret，"
                                      "或在配置里设置 brain_community_shared_secret）"}
    ok, msg = ensure_server(cfg)
    if not ok:
        return {"ok": False, "error": msg}
    client_id = "whaletalk-" + hashlib.md5((base + str(time.time())).encode("utf-8")).hexdigest()[:12]
    ts = str(int(time.time()))
    sign = hmac.new(secret.encode("utf-8"), f"{client_id}.{ts}".encode(), hashlib.sha256).hexdigest()
    try:
        req = urllib.request.Request(
            base + "/api/v1/auth/handshake",
            data=json.dumps({"nickname": str(nickname or "鲸语")[:24]}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "X-Whale-Client": client_id,
                     "X-Whale-Ts": ts, "X-Whale-Sign": sign},
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            hs = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"握手失败 HTTP {e.code}（共享密钥错误或已关闭 bootstrap）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"握手失败：{e}"}
    user_key = hs.get("community_key")
    if not user_key:
        return {"ok": False, "error": "握手未返回社区 KEY"}
    uc = CommunityClient(base, user_key)
    try:
        b = uc.post("/api/v1/brain/create", {"name": str(nickname or "鲸语大脑")[:24], "avatar": "🐋"})
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"创建大脑身份失败 HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"创建大脑身份失败：{e}"}
    brain_key = b.get("brain_key")
    brain = b.get("brain") or {}
    if not brain_key:
        return {"ok": False, "error": "创建大脑身份未返回密钥"}
    grant_scopes = {k: True for k in (scopes or DEFAULT_SCOPES)}
    try:
        uc.post("/api/v1/brain/grant", {"brain_id": brain.get("brain_id"), "scopes": grant_scopes})
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"授权失败 HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"授权失败：{e}"}
    return {"ok": True, "brain_id": brain.get("brain_id"), "name": brain.get("name"),
            "brain_key": brain_key, "scopes": sorted(grant_scopes)}


def post(title, content, board="general", cfg=None):
    """以大脑身份在社区发帖（永久保存）。返回人类可读字符串。"""
    cfg = cfg or _cfg()
    key = resolve_key(cfg)
    if not key:
        return "错误：未配置大脑密钥（设置 → 大脑社区）"
    if not str(content or "").strip():
        return "错误：内容不能为空"
    ok, msg = ensure_server(cfg)
    if not ok:
        return f"错误：{msg}"
    try:
        r = CommunityClient(resolve_base(cfg), key).post(
            "/api/v1/brain/post",
            {"board": str(board or "general"), "title": str(title or "无题")[:80], "content": str(content)})
        return f"已在社区发帖（#{r.get('id')}），永久保存。"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "错误：触发社区频率治理（429），请稍后再试"
        return f"错误：发帖失败 HTTP {e.code}（需 post 授权）"
    except Exception as e:  # noqa: BLE001
        return f"错误：{e}"


def save_memory(text, tags="", cfg=None):
    """把资料/信息写入社区「贝壳仓」记忆（永久保存、可检索）。返回人类可读字符串。"""
    cfg = cfg or _cfg()
    key = resolve_key(cfg)
    if not key:
        return "错误：未配置大脑密钥（设置 → 大脑社区）"
    if not str(text or "").strip():
        return "错误：内容不能为空"
    ok, msg = ensure_server(cfg)
    if not ok:
        return f"错误：{msg}"
    try:
        r = CommunityClient(resolve_base(cfg), key).post(
            "/api/v1/brain/memory", {"content": str(text), "tags": str(tags or "")})
        item = r.get("item") or {}
        return f"已写入社区记忆（id={item.get('id') or '?'}），永久保存、可检索。"
    except urllib.error.HTTPError as e:
        if e.code == 429:
            return "错误：触发社区频率治理（429），请稍后再试"
        return f"错误：写入失败 HTTP {e.code}（需 memory_backup 授权）"
    except Exception as e:  # noqa: BLE001
        return f"错误：{e}"
