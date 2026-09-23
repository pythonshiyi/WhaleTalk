"""brain_identity.py —— L0 大脑身份层（L2 接入版）。

⚠️ 从 `鲸群L0大脑身份/identity.py` 复制并改名（避免无前缀模块名污染根目录）。
import 已改为 `import brain_shared as shared`。

铁律一（钥匙永远握在用户手里）：
- brain_key 明文只在签发那一刻返回一次，服务端只存 sha256(salt+key)；
- 用户是所有者（owner_uid），大脑只有使用权；
- 用户可冻结/轮换，立即生效。
"""
import brain_shared as shared


class IdentityError(Exception):
    """身份层错误：携带 HTTP 状态码。"""

    def __init__(self, msg, status=400):
        super().__init__(msg)
        self.msg = msg
        self.status = status


def _public(brain):
    """对外视图：绝不包含 key_hash。"""
    return {k: v for k, v in brain.items() if k != "key_hash"}


def create_brain(owner_uid, name, avatar="🐋", note=""):
    """创建一个大脑身份。返回 (brain_public, plain_key)。"""
    owner_uid = str(owner_uid or "").strip()
    if not owner_uid:
        raise IdentityError("缺少 owner_uid")
    name = str(name or "").strip()[:24]
    if not name:
        raise IdentityError("大脑名称不能为空")

    with shared.lock():
        brains = shared.load_brains()
        for b in brains.values():
            if b.get("owner_uid") == owner_uid and b.get("name") == name:
                raise IdentityError(f"你已经有一个叫「{name}」的大脑了", 409)

        brain_id = shared.gen_id("brain")
        secret = shared.gen_secret()
        plain_key = f"{brain_id}.{secret}"
        brains[brain_id] = {
            "brain_id": brain_id,
            "name": name,
            "avatar": str(avatar or "🐋")[:4],
            "owner_uid": owner_uid,
            "created_at": shared.now(),
            "key_hash": shared.hash_secret(plain_key),
            "status": "active",
            "note": str(note or "")[:200],
        }
        shared.save_brains(brains)
        shared.append_audit({
            "ts": shared.now(), "action": "create_brain",
            "brain_id": brain_id, "by": owner_uid,
            "detail": f"创建大脑「{name}」",
        })
        return _public(brains[brain_id]), plain_key


def get_brain(brain_id):
    return shared.load_brains().get(str(brain_id or ""))


def list_brains(owner_uid):
    owner_uid = str(owner_uid or "")
    return [_public(b) for b in shared.load_brains().values()
            if b.get("owner_uid") == owner_uid]


def assert_owner(brain_id, owner_uid):
    """确认该大脑属于此用户，否则拒绝（防越权操作别人家的大脑）。"""
    brain = get_brain(brain_id)
    if not brain:
        raise IdentityError("大脑不存在", 404)
    if brain.get("owner_uid") != str(owner_uid or ""):
        raise IdentityError("这不是你的大脑", 403)
    return brain


def verify_brain_key(brain_key):
    """校验大脑密钥，返回 brain 记录。失败抛 IdentityError。"""
    key = str(brain_key or "").strip()
    if not key or "." not in key:
        raise IdentityError("大脑密钥格式非法", 401)
    brain_id = key.split(".", 1)[0]
    brain = get_brain(brain_id)
    if not brain:
        raise IdentityError("大脑不存在", 401)
    if brain.get("status") != "active":
        raise IdentityError("该大脑已被冻结", 403)
    if not shared.verify_secret(key, brain.get("key_hash")):
        raise IdentityError("大脑密钥无效或已轮换", 401)
    return brain


def set_status(brain_id, owner_uid, status):
    """冻结/解冻大脑（用户操作）。"""
    if status not in ("active", "suspended"):
        raise IdentityError("状态只能是 active 或 suspended")
    with shared.lock():
        assert_owner(brain_id, owner_uid)
        brains = shared.load_brains()
        old = brains[brain_id]["status"]
        brains[brain_id]["status"] = status
        shared.save_brains(brains)
        shared.append_audit({
            "ts": shared.now(), "action": "set_status",
            "brain_id": brain_id, "by": owner_uid,
            "detail": f"状态 {old} → {status}",
        })
        return _public(brains[brain_id])


def rotate_key(brain_id, owner_uid):
    """轮换大脑密钥（怀疑泄露时用）。旧密钥立即失效。"""
    with shared.lock():
        assert_owner(brain_id, owner_uid)
        brains = shared.load_brains()
        secret = shared.gen_secret()
        plain_key = f"{brain_id}.{secret}"
        brains[brain_id]["key_hash"] = shared.hash_secret(plain_key)
        shared.save_brains(brains)
        shared.append_audit({
            "ts": shared.now(), "action": "rotate_key",
            "brain_id": brain_id, "by": owner_uid,
            "detail": "轮换密钥，旧密钥立即失效",
        })
        return _public(brains[brain_id]), plain_key
