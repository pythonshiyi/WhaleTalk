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
    return _ok(brains=_identity.list_brains(LOCAL_OWNER))


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


def dispatch(action, body):
    """统一分发：主程序 POST /v1/brain/grant 的唯一入口。

    返回 {"ok": bool, ...}；未知 action 或异常均返回 {"ok": False, "error": ...}。
    """
    action = str(action or "").strip()
    if action not in _MAP:
        return _err(f"未知动作：{action or '(空)'}；可用：{'、'.join(_ACTIONS)}")
    try:
        return _MAP[action](body or {})
    except Exception as e:  # noqa: BLE001 - 收口，不抛给 HTTP 层
        return _err(e)


def grant_detail(brain_id):
    """GET /v1/brain/grants?brain_id=X 的返回。

    输出「已授权项 + 全部维度（带 on 状态）」，前端直接渲染勾选面板。
    """
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
        )
    except Exception as e:  # noqa: BLE001
        return _err(e)


def describe():
    """自描述（供测试/调试）。"""
    return _ok(actions=[{"action": k, "desc": v} for k, v in _ACTIONS.items()],
               owner=LOCAL_OWNER, data_dir=_shared.DATA_DIR)
