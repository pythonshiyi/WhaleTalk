"""brain_grant_adapter.py —— 鲸语主程序接入适配器（L2）。

把 L0/L1 的 brain_identity / brain_grant 包成 api_server 可直接调用的**纯函数**，
形态刻意模仿现有的 brain_api.py（统一返回 {"ok", ...}），使主程序接入只需加
两个装饰器端点，不触碰任何现有分支。

⚠️ 接入版差异：模块名带 brain_ 前缀（根目录已有 shared.py / identity.py 无前缀名）。
owner_uid 固定为 "local"（鲸语本机单用户），保留字段以备多用户。

独立测试：python -m pytest data/workspace/code-review/test_adapter.py -v
"""
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

import brain_grant as _grant
import brain_identity as _identity
import brain_shared as _shared

LOCAL_OWNER = "local"

_ACTIONS = {
    "create_brain": "创建大脑身份",
    "list": "列出我的大脑",
    "set_grant": "设置授权",
    "revoke": "撤回授权",
    "rotate": "轮换密钥",
    "status": "冻结/解冻",
    "audit": "查看审计日志",
}


def _ok(**kw):
    return {"ok": True, **kw}


def _err(msg):
    return {"ok": False, "error": str(msg)}


def _create_brain(body):
    brain, key = _identity.create_brain(
        LOCAL_OWNER, body.get("name"), body.get("avatar", "🐋"), body.get("note", ""))
    return _ok(brain=brain, brain_key=key,
               warn="这把钥匙只显示一次，请立即保存。它代表这个大脑的身份。")


def _list_brains(_body):
    return _ok(brains=_identity.list_brains(LOCAL_OWNER), source="local")


def _set_grant(body):
    g = _grant.set_grant(body.get("brain_id"), LOCAL_OWNER,
                         body.get("scopes"), body.get("expires_at"))
    return _ok(grant=g)


def _revoke(body):
    g = _grant.revoke_grant(body.get("brain_id"), LOCAL_OWNER)
    return _ok(grant=g)


def _rotate(body):
    brain, key = _identity.rotate_key(body.get("brain_id"), LOCAL_OWNER)
    return _ok(brain=brain, brain_key=key,
               warn="新钥匙只显示一次；旧钥匙已立即失效。")


def _status(body):
    brain = _identity.set_status(body.get("brain_id"), LOCAL_OWNER, body.get("status"))
    return _ok(brain=brain)


def _audit(body):
    limit = int(body.get("limit") or 50)
    mine = {b["brain_id"] for b in _identity.list_brains(LOCAL_OWNER)}
    log = [e for e in _shared.load_audit() if e.get("brain_id") in mine]
    return _ok(audit=log[-max(1, min(limit, 500)):])


_MAP = {
    "create_brain": _create_brain,
    "list": _list_brains,
    "set_grant": _set_grant,
    "revoke": _revoke,
    "rotate": _rotate,
    "status": _status,
    "audit": _audit,
}


def describe():
    """自描述（供测试/调试）。"""
    return _ok(actions=[{"action": k, "desc": v} for k, v in _ACTIONS.items()],
               owner=LOCAL_OWNER, data_dir=_shared.DATA_DIR)


# ── 社区侧授权接管（v3.16.18）────────────────────────────
# 为什么需要这一段：UI「授权」面板此前只写本机 grant，而大脑真正据以行动的是
# **社区侧** `whoami.scopes`（`community_client.plan` 用它过滤动作）——
# 两套存储互不影响，面板文案「未勾选的一律不能做」并不成立。
# 现约定：**配置了社区大脑密钥（= 已一键接入）时，面板直接读写社区侧授权**，
# 本机 grant 仅在未接入社区时使用（保留原行为，不破坏本地实验）。
_COMMUNITY_ACTIONS = ("list", "set_grant", "revoke")

# 已接入社区时，本地独有动作给出「去哪儿做」的具体指引（而不是含糊的报错）。
_COMMUNITY_HINTS = {
    "create_brain": "新大脑在 设置 → 大脑高级 → 大脑自主进社区 → 一键接入 里创建",
    "rotate": "轮换密钥请在 设置 → 大脑高级 → 大脑自主进社区 重新接入",
    "status": "冻结/解冻请在社区站侧操作",
    "audit": "已接入社区后，授权审计由社区站侧记录，本地不再保存",
}


def _community_client():
    try:
        import community_client as cc
        return cc if cc.resolve_key() else None
    except Exception:  # noqa: BLE001 - 社区模块缺失/配置读不出时退回本地
        return None


def _grant_view(snap):
    """把社区侧快照折成前端既有的 grant 形状（复用原渲染逻辑）。"""
    scopes = {row["key"]: bool(row["on"]) for row in snap.get("all_scopes") or []}
    return {"brain_id": snap["brain"]["brain_id"], "scopes": scopes,
            "revoked": not bool(snap.get("scopes")), "source": "community"}


def _community_call(action, body):
    cc = _community_client()
    if action == "list":
        snap = cc.grant_snapshot()
        if not snap.get("ok"):
            return snap
        return _ok(brains=[snap["brain"]], source="community")
    snap = cc.grant_write(body.get("scopes")) if action == "set_grant" else cc.grant_revoke_all()
    if not snap.get("ok"):
        return snap
    return _ok(grant=_grant_view(snap), all_scopes=snap.get("all_scopes") or [],
               source="community")


def dispatch(action, body):
    """统一分发：主程序 POST /v1/brain/grant 的唯一入口。

    已接入社区时，授权类动作（list/set_grant/revoke）**直接作用于社区侧**；
    其余动作（建脑/轮换/冻结）属本地实验身份，返回明确的指引而不是静默改本地无效数据。
    """
    action = str(action or "").strip()
    body = body or {}
    if action not in _MAP:
        return _err(f"未知动作：{action or '(空)'}；可用：{'、'.join(_ACTIONS)}")
    if _community_client() is not None:
        if action in _COMMUNITY_ACTIONS:
            try:
                return _community_call(action, body)
            except Exception as e:  # noqa: BLE001 - 收口，不抛给 HTTP 层
                return _err(e)
        return _err("已接入社区：本面板直接管理社区侧授权；"
                    + _COMMUNITY_HINTS.get(action, "该动作请到社区侧操作"))
    try:
        return _MAP[action](body)
    except Exception as e:  # noqa: BLE001 - 收口，不抛给 HTTP 层
        return _err(e)


def grant_detail(brain_id):
    """GET /v1/brain/grants?brain_id=X 的返回。

    已接入社区时忽略 brain_id（面板展示的就是那个真实大脑），
    输出「已授权项 + 全部维度（带 on 状态）」，前端直接渲染勾选面板。
    """
    cc = _community_client()
    if cc is not None:
        try:
            snap = cc.grant_snapshot()
            if not snap.get("ok"):
                return snap
            return _ok(grant=_grant_view(snap), all_scopes=snap.get("all_scopes") or [],
                       source="community", brain=snap["brain"], limits=snap.get("limits") or {})
        except Exception as e:  # noqa: BLE001
            return _err(e)
    try:
        _identity.assert_owner(brain_id, LOCAL_OWNER)
        g = _grant.get_grant(brain_id)
        labels = {k: n for k, n, _ in _shared.SCOPES}
        on = (g or {}).get("scopes", {})
        return _ok(
            grant=g,
            all_scopes=[{"key": k, "label": labels[k], "on": bool(on.get(k)),
                         "desc": d, "default": False}
                        for k, n, d in _shared.SCOPES],
            source="local",
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)
