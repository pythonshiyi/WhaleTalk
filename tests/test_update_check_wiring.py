"""接线级回归：/v1/update/check 是否真的用了 update_verify（而不只是模块存在）。"""
import base64
import json


import api_server
import config_utils
import update_verify


def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    sk = Ed25519PrivateKey.generate()
    pub = base64.b64encode(
        sk.public_key().public_bytes(serialization.Encoding.Raw,
                                     serialization.PublicFormat.Raw)
    ).decode()
    return sk, pub


def _signed(payload, sk):
    body = update_verify.canonical_bytes(payload)
    return {**payload, "signature": base64.b64encode(sk.sign(body)).decode()}


class _Resp:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _patch_check(monkeypatch, payload, public_key):
    monkeypatch.setattr(config_utils, "load_config",
                        lambda *a, **k: {"update_url": "https://example.invalid/latest.json",
                                         "update_public_key": public_key})
    monkeypatch.setattr(api_server.urllib.request, "urlopen",
                        lambda *a, **k: _Resp(payload))


def test_update_check_accepts_signed_manifest(monkeypatch):
    sk, pub = _keypair()
    payload = _signed({"tag_name": "v9.9.9", "body": "notes"}, sk)
    _patch_check(monkeypatch, payload, pub)
    r = api_server._update_check()
    assert r["has_update"] is True
    assert r["verified"] is True
    assert r["latest"] == "9.9.9"


def test_update_check_rejects_tampered_manifest(monkeypatch):
    sk, pub = _keypair()
    payload = _signed({"tag_name": "v9.9.9"}, sk)
    payload["tag_name"] = "v8.8.8"          # 签名后篡改版本
    _patch_check(monkeypatch, payload, pub)
    r = api_server._update_check()
    assert r["has_update"] is False
    assert r["verified"] is False
    assert "校验失败" in r["error"]


def test_update_check_without_key_stays_compatible(monkeypatch):
    """未配置公钥时不验签（老用户行为不变），但 verified 明确为 False。"""
    _patch_check(monkeypatch, {"tag_name": "v9.9.9"}, "")
    r = api_server._update_check()
    assert r["has_update"] is True
    assert r["verified"] is False
