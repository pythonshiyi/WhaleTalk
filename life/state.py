"""生命状态与单实例租约。

``brain/life.json`` 是当前状态快照（可随时由 timeline 重建）；
``brain/life.lock`` 是单实例租约：同一份大脑同时只允许一个生活循环写入。

租约不是权限闸门，而是数据完整性：两个进程同时 tick/睡眠会互相覆盖
``intentions.json``、``metabolism.json`` 与 ``heartbeat.json``。
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from ._atomic import write_json

_LOCK = threading.RLock()

DEFAULT_STATE = {
    "state": "dormant",          # dormant/sleeping/waking/awake/idle/reflecting/acting/observer/paused
    "epoch": 1,
    "pid": 0,
    "host": "",
    "boot_count": 0,
    "tick_count": 0,
    "last_tick": None,
    "last_wake": None,
    "last_sleep": None,
    "last_sleep_day": "",
    "last_user_activity": None,
    "last_reflect": None,
    "paused": False,
    "pause_reason": "",
    "updated_at": None,
}

LEASE_STALE_SECONDS = 90


def _brain_dir() -> Path:
    try:
        import brainkit as bk
        return Path(bk.BRAIN_DIR)
    except Exception:
        return Path(__file__).resolve().parent.parent / "brain"


def life_path() -> Path:
    return _brain_dir() / "life.json"


def lease_path() -> Path:
    return _brain_dir() / "life.lock"


def _now_iso(ts=None) -> str:
    if ts is None:
        ts = time.time()
    try:
        return datetime.fromtimestamp(float(ts)).astimezone().isoformat(timespec="seconds")
    except Exception:
        return datetime.now().astimezone().isoformat(timespec="seconds")


def _hostname() -> str:
    try:
        return socket.gethostname() or ""
    except Exception:
        return ""


def load() -> dict:
    data = dict(DEFAULT_STATE)
    with _LOCK:
        try:
            raw = json.loads(life_path().read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update({k: v for k, v in raw.items() if k in DEFAULT_STATE or k.startswith("_")})
        # noqa: silent-except: 状态文件损坏时回退默认值，由下一次 save 重写
        except Exception:
            pass
    return data


def save(patch=None) -> dict:
    """原子写 life.json；patch 为 None 时只重写当前快照。"""
    with _LOCK:
        data = load()
        if isinstance(patch, dict):
            data.update(patch)
        data["updated_at"] = _now_iso()
        write_json(life_path(), data)
        return data


def _pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        import psutil
        return bool(psutil.pid_exists(pid))
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    if os.name == "nt":
        # Windows 上不拿 os.kill 做存在性探测；交给心跳时间判断。
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False
    except Exception:
        return False


def lease_status() -> dict:
    """返回租约内容；无租约返回 {}。"""
    try:
        info = json.loads(lease_path().read_text(encoding="utf-8"))
        return info if isinstance(info, dict) else {}
    except Exception:
        return {}


def _lease_stale(info, stale_seconds=LEASE_STALE_SECONDS) -> bool:
    if not info:
        return True
    if info.get("host") and _hostname() and info.get("host") != _hostname():
        # 跨主机（同步盘/共享目录）无法判断进程，只看心跳时间。
        pass
    elif not _pid_alive(info.get("pid")):
        return True
    try:
        beat = float(info.get("beat_at") or 0)
    except (TypeError, ValueError):
        beat = 0
    return (time.time() - beat) > max(5.0, float(stale_seconds))


def _lease_payload(reason="", token="") -> dict:
    now = time.time()
    return {
        "token": token or uuid.uuid4().hex,
        "pid": os.getpid(),
        "host": _hostname(),
        "reason": str(reason or "")[:80],
        "acquired_at": _now_iso(now),
        "beat_at": now,
        "beat_iso": _now_iso(now),
    }


def lease_acquire(reason="", stale_seconds=LEASE_STALE_SECONDS, force=False):
    """获取租约，返回 ``(ok, info)``。

    同进程重复调用视为续租；别的活进程持有且未过期则返回 False。
    """
    p = lease_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cur = lease_status()
    if cur and int(cur.get("pid") or 0) == os.getpid():
        return True, lease_renew(reason=reason)
    if cur and not force and not _lease_stale(cur, stale_seconds):
        return False, cur
    payload = _lease_payload(reason=reason, token=(cur.get("token") if cur and int(cur.get("pid") or 0) == os.getpid() else ""))
    for _ in range(8):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            os.close(fd)
            return True, payload
        except FileExistsError:
            cur = lease_status()
            if cur and int(cur.get("pid") or 0) == os.getpid():
                return True, lease_renew(reason=reason)
            if cur and not force and not _lease_stale(cur, stale_seconds):
                return False, cur
            try:
                p.unlink()
            # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
            except OSError:
                pass
            time.sleep(0.01)
        except OSError:
            return False, lease_status()
    return False, lease_status()


def lease_renew(reason="") -> dict:
    """续租；调用方应已持有租约（或同进程）。"""
    cur = lease_status()
    token = str(cur.get("token") or "")
    payload = _lease_payload(reason=reason or cur.get("reason") or "", token=token)
    write_json(lease_path(), payload, indent=0)
    return payload


def lease_release() -> bool:
    cur = lease_status()
    if cur and int(cur.get("pid") or 0) not in (0, os.getpid()):
        return False
    try:
        lease_path().unlink(missing_ok=True)
        return True
    except OSError:
        return False


def is_owner() -> bool:
    cur = lease_status()
    return bool(cur) and int(cur.get("pid") or 0) == os.getpid() and not _lease_stale(cur)


def status() -> dict:
    data = load()
    lease = lease_status()
    pub_lease = {k: v for k, v in lease.items() if k != "token"} if lease else {}
    return {
        "state": data,
        "lease": pub_lease,
        "owner": bool(lease) and int(lease.get("pid") or 0) == os.getpid(),
        "pid_alive": _pid_alive(lease.get("pid")) if lease else False,
    }


def set_state(value, **patch) -> dict:
    patch = dict(patch)
    patch["state"] = str(value or "awake")
    return save(patch)


def note_activity(source="chat", at=None) -> dict:
    """用户活动：把睡眠中的个体叫醒。不调用模型、不写 timeline（由 loop 节流记录）。"""
    patch = {"last_user_activity": _now_iso(at), "last_activity_source": str(source or "chat")[:20]}
    cur = load()
    if cur.get("state") in ("sleeping", "dormant", "observer"):
        patch["state"] = "awake"
        patch["last_wake"] = _now_iso(at)
    return save(patch)


def bump_epoch(reason="", note="", parent_epoch=None) -> int:
    """产生新 epoch（合并/恢复/切换大脑时调用），并写 timeline。"""
    cur = load()
    old = int(parent_epoch if parent_epoch is not None else (cur.get("epoch") or 1))
    new = old + 1
    save({"epoch": new, "epoch_reason": str(reason or "")[:80], "epoch_at": _now_iso()})
    try:
        from . import timeline
        timeline.append(
            "epoch",
            summary=f"epoch {old} → {new}: {str(reason or '')[:60]}",
            data={"from": old, "to": new, "reason": str(reason or ""), "note": str(note or "")[:300]},
            actor="system",
        )
    # noqa: silent-except: 生命循环状态读取/记录尽力而为，不阻塞主流程
    except Exception:
        pass
    return new
