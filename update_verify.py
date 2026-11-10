"""更新发布清单的签名与完整性校验。

契约
----
发布清单（`latest.json` / GitHub Releases 响应）可选携带两个字段：

    "signature": "<base64 的 Ed25519 签名>"
    "sha256":    "<发布产物的 SHA-256 十六进制>"

`signature` 的签名对象是**去掉 signature 字段后的清单规范化 JSON**
（`sort_keys=True`、无多余空格、UTF-8 编码）。这样清单里的版本号、下载地址、
说明文字都被一次签名覆盖，改动任何一个字节都会验签失败。

配置 `update_public_key` 后，清单缺签名或验签失败一律 **fail-closed**
（不报告可更新，并在响应里给出原因）——否则被篡改的清单会把用户引向任意产物。

`sha256` 只校验「下载到的字节」与清单声明值一致；本模块不负责下载，
调用方在落盘后调用 `verify_sha256()` 即可。两者配合才是完整链路：
先验清单（可信来源），再验产物（传输完整性）。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from typing import Any, Dict, Tuple

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_bytes(payload: Dict[str, Any], *, drop: Tuple[str, ...] = ("signature",)) -> bytes:
    """清单的规范化字节（签名与验签必须用同一份字节）。"""
    if not isinstance(payload, dict):
        raise ValueError("清单必须是 JSON 对象")
    body = {k: v for k, v in payload.items() if k not in drop}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _b64decode(text: str) -> bytes:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("空 base64 内容")
    # 兼容无 padding 的 base64（发布页手填常见）
    pad = (-len(raw)) % 4
    try:
        return base64.b64decode(raw + "=" * pad, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"base64 解析失败：{e}") from e


def load_public_key(text: str):
    """解析 Ed25519 公钥：支持 base64 的 32 字节原始公钥，或 PEM。"""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    raw = str(text or "").strip()
    if not raw:
        raise ValueError("公钥为空")
    if "BEGIN PUBLIC KEY" in raw:
        key = serialization.load_pem_public_key(raw.encode("utf-8"))
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("该 PEM 不是 Ed25519 公钥")
        return key
    blob = _b64decode(raw)
    if len(blob) != 32:
        raise ValueError(f"原始公钥必须是 32 字节（收到 {len(blob)}）")
    return Ed25519PublicKey.from_public_bytes(blob)


def verify_sha256(data: bytes, expected: str) -> Tuple[bool, str]:
    """校验产物字节的 SHA-256。"""
    exp = str(expected or "").strip().lower()
    if not exp:
        return False, "清单未提供 sha256"
    if not _SHA256_RE.match(exp):
        return False, "sha256 必须是 64 位十六进制"
    got = hashlib.sha256(data).hexdigest()
    if hmac.compare_digest(got, exp):
        return True, ""
    return False, f"sha256 不匹配（期望 {exp[:12]}…，实际 {got[:12]}…）"


def verify_ed25519(data: bytes, signature_b64: str, public_key: str) -> Tuple[bool, str]:
    """校验 data 的 Ed25519 签名。"""
    from cryptography.exceptions import InvalidSignature

    try:
        key = load_public_key(public_key)
    except Exception as e:  # noqa: BLE001 - 公钥配置错误要给用户可读原因
        return False, f"公钥不可用：{e}"
    try:
        sig = _b64decode(signature_b64)
    except ValueError as e:
        return False, f"签名不可用：{e}"
    if len(sig) != 64:
        return False, f"Ed25519 签名必须是 64 字节（收到 {len(sig)}）"
    try:
        key.verify(sig, data)
    except InvalidSignature:
        return False, "签名校验失败（内容与签名不匹配）"
    except Exception as e:  # noqa: BLE001
        return False, f"签名校验异常：{e}"
    return True, ""


def verify_release(payload: Dict[str, Any], public_key: str = "") -> Tuple[bool, str]:
    """校验发布清单。返回 ``(ok, reason)``。

    未配置公钥时直接放行（保持「可选安全」的既有语义）；一旦配置了公钥，
    清单必须带合法签名，否则拒绝——这是 fail-closed 的关键。
    """
    if not isinstance(payload, dict):
        return False, "清单必须是 JSON 对象"
    key = str(public_key or "").strip()
    if not key:
        return True, ""
    sig = str(payload.get("signature") or "").strip()
    if not sig:
        return False, "已配置 update_public_key，但清单缺少 signature 字段"
    try:
        body = canonical_bytes(payload)
    except ValueError as e:
        return False, str(e)
    return verify_ed25519(body, sig, key)


def _cli() -> int:
    """命令行自检：python update_verify.py --file 包.zip --sha256 <hex> [--key <b64> --signature <b64>]"""
    import argparse

    ap = argparse.ArgumentParser(description="校验更新包/清单的 SHA-256 与 Ed25519 签名")
    ap.add_argument("--file", required=True, help="待校验文件")
    ap.add_argument("--sha256", default="", help="清单声明的 sha256")
    ap.add_argument("--key", default="", help="Ed25519 公钥（base64 或 PEM）")
    ap.add_argument("--signature", default="", help="清单 signature（base64）")
    args = ap.parse_args()
    with open(args.file, "rb") as f:
        data = f.read()
    ok, why = (True, "")
    if args.sha256:
        ok, why = verify_sha256(data, args.sha256)
    if ok and args.key:
        payload = {"sha256": args.sha256, "signature": args.signature}
        ok, why = verify_release(payload, args.key)
    print("通过" if ok else f"失败：{why}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
