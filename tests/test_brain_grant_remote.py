"""授权面板「接管社区侧授权」回归（v3.16.18）。

背景：UI「授权」面板此前只读写**本机** DATA_DIR 的 grant，而大脑真正据以行动的是
**社区侧** `whoami.scopes`（`community_client.plan()` 用它过滤动作）——两套存储互不影响，
面板「未勾选的一律不能做」并不成立。本组用例锁定：
  1. 已接入社区（配置了大脑密钥）时，读取的是社区侧真实授权；
  2. 保存/撤回**写回社区侧**，且写完回读（面板显示 = 事实）；
  3. 面板没显示的远端独有维度不会被静默关掉；
  4. 未接入社区时保持原有本地行为（不破坏本地实验路径）。
"""
import community_client as cc
import brain_grant_adapter as bga

CFG = {"brain_community_base": "http://127.0.0.1:8770",
       "brain_community_brain_key": "brain_abc123.s3cret"}


class FakeCommunity:
    """最小社区站替身：whoami 读、grant 全量覆盖写。"""

    def __init__(self, scopes=(), name="鲸语大脑", fail=None):
        self.scopes = list(scopes)
        self.name = name
        self.fail = fail
        self.posted = []
        self.brain_id = "brain_abc123"

    def get(self, path):
        assert path == "/api/v1/brain/whoami", path
        if self.fail:
            raise self.fail
        return {"brain_id": self.brain_id, "name": self.name, "avatar": "🐋",
                "status": "active", "scopes": list(self.scopes),
                "limits": {"min_interval_sec": 30}}

    def post(self, path, body=None):
        assert path == "/api/v1/brain/grant", path
        self.posted.append(body or {})
        self.scopes = [k for k, v in (body or {}).get("scopes", {}).items() if v]
        return {"ok": True}


def _wire(monkeypatch, fake, key="brain_abc123.s3cret"):
    monkeypatch.setattr(cc, "_cfg", lambda: dict(CFG))
    monkeypatch.setattr(cc, "resolve_key", lambda cfg=None: key)
    monkeypatch.setattr(cc, "CommunityClient", lambda base, k, timeout=20: fake)


def test_snapshot_without_key_explains_how_to_onboard(monkeypatch):
    monkeypatch.setattr(cc, "resolve_key", lambda cfg=None: "")
    snap = cc.grant_snapshot(dict(CFG))
    assert snap["ok"] is False
    assert snap.get("configured") is False
    assert "一键接入" in snap["error"]


def test_snapshot_reads_real_community_scopes(monkeypatch):
    fake = FakeCommunity(scopes=["post", "like"])
    _wire(monkeypatch, fake)
    snap = cc.grant_snapshot(dict(CFG))
    assert snap["ok"] is True and snap["source"] == "community"
    assert snap["brain"]["brain_id"] == "brain_abc123"
    on = {row["key"] for row in snap["all_scopes"] if row["on"]}
    assert on == {"post", "like"}
    # 词表内的维度必须全部出现（否则用户看不到、也就取消不掉）
    keys = {row["key"] for row in snap["all_scopes"]}
    assert {"browse_only", "play", "message_human"}.issubset(keys)


def test_snapshot_surfaces_http_error(monkeypatch):
    import urllib.error
    fake = FakeCommunity(fail=urllib.error.HTTPError("u", 401, "unauthorized", {}, None))
    _wire(monkeypatch, fake)
    snap = cc.grant_snapshot(dict(CFG))
    assert snap["ok"] is False and snap["configured"] is True
    assert "401" in snap["error"]


def test_write_sends_normalized_map_and_reads_back(monkeypatch):
    fake = FakeCommunity(scopes=["post"])
    _wire(monkeypatch, fake)
    snap = cc.grant_write({"reply": True, "message_human": True}, cfg=dict(CFG))
    assert snap["ok"] is True
    sent = fake.posted[-1]
    assert sent["brain_id"] == "brain_abc123"
    assert sent["scopes"]["reply"] is True
    assert sent["scopes"]["message_human"] is True
    assert sent["scopes"]["post"] is False          # 全量覆盖语义
    # 回读一致：面板显示的就是刚写进去的
    assert {r["key"] for r in snap["all_scopes"] if r["on"]} == {"reply", "message_human"}


def test_write_keeps_unknown_remote_scope_on(monkeypatch):
    """面板词表里没有的远端权限（社区侧新增维度）不能被静默关掉。"""
    fake = FakeCommunity(scopes=["post", "teleport"])
    _wire(monkeypatch, fake)
    cc.grant_write({"post": True}, cfg=dict(CFG))
    assert fake.posted[-1]["scopes"]["teleport"] is True
    assert "teleport" in [r["key"] for r in cc.grant_snapshot(dict(CFG))["all_scopes"]]


def test_revoke_all_turns_everything_off(monkeypatch):
    fake = FakeCommunity(scopes=["post", "reply"])
    _wire(monkeypatch, fake)
    snap = cc.grant_revoke_all(dict(CFG))
    assert snap["ok"] is True
    assert fake.scopes == []
    assert not any(r["on"] for r in snap["all_scopes"])


def test_adapter_routes_to_community_when_onboarded(monkeypatch):
    fake = FakeCommunity(scopes=["post"])
    _wire(monkeypatch, fake)

    listed = bga.dispatch("list", {})
    assert listed["ok"] is True and listed["source"] == "community"
    assert listed["brains"][0]["brain_id"] == "brain_abc123"

    saved = bga.dispatch("set_grant", {"scopes": {"reply": True}})
    assert saved["ok"] is True and saved["source"] == "community"
    assert fake.scopes == ["reply"]
    assert saved["grant"]["brain_id"] == "brain_abc123"
    assert saved["grant"]["revoked"] is False

    revoked = bga.dispatch("revoke", {})
    assert revoked["ok"] is True
    assert revoked["grant"]["revoked"] is True

    detail = bga.grant_detail("brain_abc123")
    assert detail["ok"] is True and detail["source"] == "community"
    assert detail["brain"]["name"] == "鲸语大脑"


def test_adapter_refuses_local_only_actions_when_onboarded(monkeypatch):
    _wire(monkeypatch, FakeCommunity())
    out = bga.dispatch("create_brain", {"name": "新大脑"})
    assert out["ok"] is False
    assert "设置" in out["error"]


def test_adapter_falls_back_to_local_when_not_onboarded(monkeypatch):
    monkeypatch.setattr(cc, "resolve_key", lambda cfg=None: "")
    monkeypatch.setattr(cc, "_cfg", lambda: {})
    out = bga.dispatch("list", {})
    assert out["ok"] is True
    assert out.get("source") == "local"
    assert isinstance(out["brains"], list)
