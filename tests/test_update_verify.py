"""更新清单验签：Ed25519 签名 + SHA-256 完整性。"""
import json


import update_verify


def _keypair():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    sk = Ed25519PrivateKey.generate()
    pub_b64 = __import__("base64").b64encode(
        sk.public_key().public_bytes(serialization.Encoding.Raw,
                                     serialization.PublicFormat.Raw)
    ).decode()
    return sk, pub_b64


def _signed(payload, sk):
    import base64
    body = update_verify.canonical_bytes(payload)
    sig = sk.sign(body)
    return {**payload, "signature": base64.b64encode(sig).decode()}


def test_no_key_configured_passes_through():
    """未配置公钥 = 不验签（保持可选安全的既有语义）。"""
    ok, why = update_verify.verify_release({"tag_name": "v9.9.9"}, "")
    assert ok, why


def test_valid_signature_is_accepted():
    sk, pub = _keypair()
    payload = _signed({"tag_name": "v3.29.0", "assets": ["a.zip"]}, sk)
    ok, why = update_verify.verify_release(payload, pub)
    assert ok, why


def test_tampered_manifest_is_rejected():
    sk, pub = _keypair()
    payload = _signed({"tag_name": "v3.29.0"}, sk)
    payload["tag_name"] = "v9.9.9"           # 签名后改版本号
    ok, why = update_verify.verify_release(payload, pub)
    assert not ok and "签名校验失败" in why


def test_missing_signature_fails_closed():
    _sk, pub = _keypair()
    ok, why = update_verify.verify_release({"tag_name": "v3.29.0"}, pub)
    assert not ok and "缺少 signature" in why


def test_sha256_verifies_artifact_bytes():
    data = b"payload-bytes"
    import hashlib
    digest = hashlib.sha256(data).hexdigest()
    assert update_verify.verify_sha256(data, digest)[0]
    ok, why = update_verify.verify_sha256(data, "0" * 64)
    assert not ok and "不匹配" in why
    assert not update_verify.verify_sha256(data, "")[0]


def test_public_key_accepts_pem():
    from cryptography.hazmat.primitives import serialization

    sk, _ = _keypair()
    pem = sk.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    payload = _signed({"version": "1.2.3"}, sk)
    ok, why = update_verify.verify_release(payload, pem)
    assert ok, why


def test_canonical_bytes_is_order_independent():
    a = update_verify.canonical_bytes({"a": 1, "b": 2})
    b = update_verify.canonical_bytes({"b": 2, "a": 1, "signature": "ignored"})
    assert a == b and json.loads(a) == {"a": 1, "b": 2}


def test_bad_public_key_reports_readable_reason():
    ok, why = update_verify.verify_release({"signature": "AAAA"}, "not-base64!!")
    assert not ok and "公钥不可用" in why


def test_signature_length_is_checked():
    _sk, pub = _keypair()
    ok, why = update_verify.verify_release({"signature": "AAAA"}, pub)
    assert not ok and "64 字节" in why
