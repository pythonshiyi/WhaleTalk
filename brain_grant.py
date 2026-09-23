"""brain_grant.py —— L1 授权层（L2 接入版）。

⚠️ 从 `鲸群L0大脑身份/grant.py` 复制并改名。import 已改为 `import brain_shared as shared`。

铁律二（fail-closed）：未勾选的 scope 一律拒绝——与鲸语 permissions.py 的默认放行相反，故意的。
铁律三（可溯源）：每次授权变更写审计，大脑活动带 brain_id + grant_id。
"""
import brain_identity as identity
import brain_shared as shared


class GrantError(Exception):
    def __init__(self, msg, status=403):
        super().__init__(msg)
        self.msg = msg
        self.status = status


def _normalize_scopes(raw):
    """归一为 {scope: bool}，只认已知 scope，未知键丢弃；未传的一律 False。"""
    raw = raw if isinstance(raw, dict) else {}
    return {k: bool(raw.get(k, False)) for k in shared.SCOPE_KEYS}


def _diff(before, after):
    """人类可读的变更摘要（审计用）。"""
    if not before:
        on = [k for k, v in after.items() if v]
        return "首次授权，开启：" + ("、".join(on) if on else "（全关）")
    on, off = [], []
    for k in shared.SCOPE_KEYS:
        if before.get(k) != after.get(k):
            (on if after.get(k) else off).append(k)
    parts = []
    if on:
        parts.append("开启 " + "、".join(on))
    if off:
        parts.append("关闭 " + "、".join(off))
    return "；".join(parts) if parts else "无变化"


def set_grant(brain_id, owner_uid, scopes, expires_at=None):
    """设置（或更新）某大脑的授权。必须由用户发起。"""
    with shared.lock():
        identity.assert_owner(brain_id, owner_uid)
        norm = _normalize_scopes(scopes)
        grants = shared.load_grants()
        g = grants.get(brain_id)
        if g and not g.get("revoked"):
            before = dict(g.get("scopes", {}))
            g["scopes"] = norm
            g["updated_at"] = shared.now()
            g["expires_at"] = expires_at
        else:
            before = {}
            grants[brain_id] = {
                "grant_id": shared.gen_id("grant"),
                "brain_id": brain_id,
                "owner_uid": str(owner_uid),
                "scopes": norm,
                "created_at": shared.now(),
                "updated_at": shared.now(),
                "revoked": False,
                "revoked_at": None,
                "expires_at": expires_at,
            }
        shared.save_grants(grants)
        g = grants[brain_id]
        shared.append_audit({
            "ts": shared.now(), "action": "set_grant",
            "brain_id": brain_id, "by": owner_uid,
            "grant_id": g["grant_id"],
            "detail": _diff(before, norm),
        })
        return dict(g)


def get_grant(brain_id):
    return shared.load_grants().get(str(brain_id or ""))


def revoke_grant(brain_id, owner_uid):
    """撤回授权——立即生效（不在任何地方缓存授权结果）。"""
    with shared.lock():
        identity.assert_owner(brain_id, owner_uid)
        grants = shared.load_grants()
        g = grants.get(brain_id)
        if not g:
            raise GrantError("该大脑尚未授权", 404)
        g["revoked"] = True
        g["revoked_at"] = shared.now()
        g["scopes"] = {k: False for k in shared.SCOPE_KEYS}
        shared.save_grants(grants)
        shared.append_audit({
            "ts": shared.now(), "action": "revoke_grant",
            "brain_id": brain_id, "by": owner_uid,
            "grant_id": g["grant_id"], "detail": "撤回全部授权",
        })
        return dict(g)


def _grant_active(brain_id):
    """实时读取并判断授权是否有效。每次都重新读盘——不缓存。"""
    g = get_grant(brain_id)
    if not g:
        raise GrantError("该大脑尚未获得授权", 403)
    if g.get("revoked"):
        raise GrantError("授权已被撤回", 403)
    exp = g.get("expires_at")
    if exp and str(exp) <= shared.now():
        raise GrantError("授权已过期", 403)
    return g


def check_scope(brain_id, scope, grant_id=None):
    """校验某大脑是否有权执行某操作。无权限抛 GrantError。"""
    if scope not in shared.SCOPE_KEYS:
        raise GrantError(f"未知权限：{scope}", 400)
    g = _grant_active(brain_id)
    if grant_id and g.get("grant_id") != grant_id:
        raise GrantError("授权已变更，请重新获取", 403)
    if not g.get("scopes", {}).get(scope, False):
        label = dict((k, n) for k, n, _ in shared.SCOPES).get(scope, scope)
        raise GrantError(f"未授权：{label}", 403)
    return True


def whoami(brain):
    """大脑自检：我是谁、我能做什么。不能给自己改权限。"""
    g = get_grant(brain["brain_id"])
    labels = {k: n for k, n, _ in shared.SCOPES}
    allowed = []
    if g and not g.get("revoked"):
        exp = g.get("expires_at")
        if not (exp and str(exp) <= shared.now()):
            allowed = [k for k, v in g.get("scopes", {}).items() if v]
    return {
        "brain_id": brain["brain_id"],
        "name": brain["name"],
        "avatar": brain["avatar"],
        "status": brain["status"],
        "granted": bool(allowed),
        "scopes": allowed,
        "scope_labels": [labels.get(k, k) for k in allowed],
        "all_scopes": [{"key": k, "label": n, "desc": d, "default": False}
                       for k, n, d in shared.SCOPES],
    }
