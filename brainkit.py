#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""鲸语大脑 (WhaleBrain) — 可迁移、可备份、可恢复、可合并、可永续的思维容器。

鲸语是躯体，大脑是灵魂。本工具让「大脑」脱离运行环境独立存在：
身份 / 记忆 / 自我模型 / 思考日志 / 演化账本 / 心跳全部落盘为文件，
可随时挂载、卸载，并打包成快照 brain_v{n}.whale 备份或迁移到任何躯体。

进阶能力
--------
1) 跨躯体免密迁移（密钥体系）
   - 大脑持有唯一主密钥 MK：内容一律用 MK 加密，MK 永不落盘明文。
   - MK 三重包裹：本地 DPAPI（本机免密）/ 口令（fallback）/ 公钥 RSA-OAEP（跨躯体）。
   - 迁移仪式：export-key 导出 brain_seed.whale（一次性口令保护）→
     新躯体 import-key 后，该躯体即可免密解开所有快照。
   - 没有密钥的躯体拿不到快照内容 —— 免密 ≠ 不加密。

2) 多快照分支合并（血缘 + 三路合并）
   - 每个快照携带血缘：version / parent / restored_from。
   - merge <A> <B>：寻找共同祖先（LCA）做三路合并 ——
     日志行级并集、JSON 字段级递归、冲突写入 merge_conflicts.json。
   - merge-resolve <id> --keep ours|theirs|both|custom：逐条裁决，
     裁决后重新计算指纹，brain_id 不变 —— 合并后「我还是我」。

数据目录结构（brain/）:
    manifest.json     出生证明：brain_id、指纹（防篡改）、公钥指纹
    identity.json     人格基线
    memories/         长期记忆库（海马体）
    self_model.json   自我模型
    thinking_log/     思考日志（前额叶）
    evolution.json    演化账本
    heartbeat.json    心跳
    archive/          快照库 brain_v{n}.whale
    .keys/            密钥库（DPAPI 包裹，不进快照）
    .lineage.json     血缘（上次归档版本 / 自哪个快照恢复）
    merge_log.json    合并史（每次合并的时间、双亲、祖先、冲突与裁决）
    merge_conflicts.json  待裁决冲突（有冲突时存在）

用法示例
--------
  python brainkit.py init --passphrase "初始口令"
  python brainkit.py keyring-setup                 # 为已存在的大脑启用免密
  python brainkit.py mount
  python brainkit.py think "今天想清楚了一件事"
  python brainkit.py remember "报价目录已整理完第一轮"
  python brainkit.py archive                        # 默认免密加密（本地密钥）
  python brainkit.py restore brain/archive/brain_v3.whale --dir brain_b2
  python brainkit.py --brain brain_b2 remember "分支上的记忆"   # 在另一份大脑上继续演化
  python brainkit.py merge brain_v4.whale brain_b2/archive/b_v1.whale --dir brain_merged
  python brainkit.py merge-resolve <conflict-id> --keep theirs --dir brain_merged
  python brainkit.py export-key --out brain_seed.whale --passphrase "一次性口令"
  python brainkit.py import-key brain_seed.whale --passphrase "一次性口令"
  python brainkit.py status
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

try:  # Windows 控制台 UTF-8 兜底，避免中文/emoji 输出乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:  # 复用项目 DPAPI（Windows 用户级加密），用于本地免密
    import crypto as _dpapi
except Exception:  # 非 Windows / 缺少依赖时退化为无 DPAPI
    _dpapi = None

# ---------------------------------------------------------------- 路径常量
MODULE_DIR = Path(__file__).resolve().parent
BRAIN_DIR = MODULE_DIR / "brain"
MEMORIES_DIR = BRAIN_DIR / "memories"
THINKING_DIR = BRAIN_DIR / "thinking_log"
ARCHIVE_DIR = BRAIN_DIR / "archive"
KEYS_DIR = BRAIN_DIR / ".keys"
LINEAGE_FILE = BRAIN_DIR / ".lineage.json"
MERGE_LOG_FILE = BRAIN_DIR / "merge_log.json"
MERGE_CONFLICT_FILE = BRAIN_DIR / "merge_conflicts.json"

WHALE_MAGIC_V1 = b"WHALEBRAIN\x01"
WHALE_MAGIC_V2 = b"WHALEBRAIN\x02"
SCHEMA_VERSION = 1
DEFAULT_KEEP = 7
PBKDF2_ITER = 200_000
RSA_KEYSIZE = 2048

# ---------------------------------------------------------------- 基础工具


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return _dt.date.today().isoformat()


def now_compact() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def canonical_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def append_line(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")


def set_brain_dir(p: Path) -> None:
    """全局 --dir：让工具可指向任意大脑目录（分支演化 / 模拟他机）。"""
    global BRAIN_DIR, MEMORIES_DIR, THINKING_DIR, ARCHIVE_DIR, KEYS_DIR, LINEAGE_FILE
    global MERGE_LOG_FILE, MERGE_CONFLICT_FILE
    BRAIN_DIR = Path(p)
    MEMORIES_DIR = BRAIN_DIR / "memories"
    THINKING_DIR = BRAIN_DIR / "thinking_log"
    ARCHIVE_DIR = BRAIN_DIR / "archive"
    KEYS_DIR = BRAIN_DIR / ".keys"
    LINEAGE_FILE = BRAIN_DIR / ".lineage.json"
    MERGE_LOG_FILE = BRAIN_DIR / "merge_log.json"
    MERGE_CONFLICT_FILE = BRAIN_DIR / "merge_conflicts.json"


# ---------------------------------------------------------------- 指纹（防篡改）


def compute_fingerprint(manifest: dict) -> str:
    body = {k: v for k, v in manifest.items() if k != "fingerprint"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def verify_fingerprint(manifest: dict) -> bool:
    return bool(manifest.get("fingerprint")) and compute_fingerprint(manifest) == manifest["fingerprint"]


def load_manifest() -> dict:
    path = BRAIN_DIR / "manifest.json"
    if not path.exists():
        print(f"[未初始化] 还没有大脑。先运行: python brainkit.py init", file=sys.stderr)
        raise SystemExit(1)
    m = load_json(path)
    if not m:
        print("[错误] manifest.json 损坏，无法读取。", file=sys.stderr)
        raise SystemExit(1)
    return m


def refresh_manifest_fingerprint() -> None:
    """内容变化后重算指纹（brain_id 不变 —— 我还是我）。"""
    path = BRAIN_DIR / "manifest.json"
    m = load_json(path) or {}
    m["fingerprint"] = compute_fingerprint(m)
    save_json(path, m)


# ---------------------------------------------------------------- 密钥体系（免密的基础）

# MK 包裹标记：'dpapi:'（本地免密）/ 无标记（明文 b64，仅传输时内存态）
# 私钥 SK：DPAPI 包裹于 .keys/brain_sk.dpapi；公钥 DER：.keys/brain_pub.der


def _crypto_ok() -> bool:
    try:
        import cryptography  # noqa: F401

        return True
    except ImportError:
        return False


def _dpapi_ok() -> bool:
    return _dpapi is not None


def _dpapi_wrap(raw: bytes) -> str:
    if not _dpapi_ok():
        raise RuntimeError("DPAPI 不可用（仅 Windows），无法本地免密")
    return _dpapi.encrypt(base64.b64encode(raw).decode("ascii"))


def _dpapi_unwrap(token: str):
    if not _dpapi_ok() or not token:
        return None
    text = _dpapi.decrypt(token)
    if not text:
        return None
    try:
        return base64.b64decode(text)
    except Exception:
        return None


def _fernet(mk: bytes):
    from cryptography.fernet import Fernet

    return Fernet(base64.urlsafe_b64encode(mk))


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=PBKDF2_ITER)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _pub_der() -> bytes:
    p = KEYS_DIR / "brain_pub.der"
    return p.read_bytes() if p.exists() else b""


def _load_sk():
    p = KEYS_DIR / "brain_sk.dpapi"
    if not p.exists():
        return None
    raw = _dpapi_unwrap(p.read_text(encoding="utf-8"))
    if not raw:
        return None
    from cryptography.hazmat.primitives import serialization

    return serialization.load_der_private_key(raw, password=None)


def _local_mk():
    p = KEYS_DIR / "mk.dpapi"
    if not p.exists():
        return None
    return _dpapi_unwrap(p.read_text(encoding="utf-8"))


def _rsa_encrypt(pub_der: bytes, data: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    pub = serialization.load_der_public_key(pub_der)
    return pub.encrypt(data, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))


def _rsa_decrypt(sk, data: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    return sk.decrypt(data, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))


def _keyring_ready() -> bool:
    return (KEYS_DIR / "mk.dpapi").exists()


def cmd_keyring_setup(args) -> int:
    """为已存在（或刚 init）的大脑生成密钥对 + 主密钥，启用免密快照。"""
    if not _crypto_ok():
        print("[错误] 需要 cryptography。请安装后再试。", file=sys.stderr)
        return 1
    m = load_manifest()
    if _keyring_ready() and not args.force:
        print(f"[已启用] 密钥体系已存在: {KEYS_DIR}")
        print(f"  公钥指纹: {m.get('pubkey_fingerprint', '?')}")
        return 0
    if not _dpapi_ok():
        print("[警告] 当前环境无 DPAPI（非 Windows），本地免密不可用；"
              "将仅支持口令/公钥路径。", file=sys.stderr)

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    mk = os.urandom(32)
    sk = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEYSIZE)
    sk_der = sk.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_der = sk.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

    (KEYS_DIR / "mk.dpapi").write_text(_dpapi_wrap(mk), encoding="utf-8")
    (KEYS_DIR / "brain_sk.dpapi").write_text(_dpapi_wrap(sk_der), encoding="utf-8")
    (KEYS_DIR / "brain_pub.der").write_bytes(pub_der)

    m["pubkey_fingerprint"] = hashlib.sha256(pub_der).hexdigest()[:16]
    m["fingerprint"] = compute_fingerprint(m)
    save_json(BRAIN_DIR / "manifest.json", m)
    print("[密钥体系已启用] 快照将默认免密加密（本地 DPAPI 自动解锁）。")
    print(f"  公钥指纹: {m['pubkey_fingerprint']}  密钥目录: {KEYS_DIR}")
    print("  换机器时: export-key 导出 brain_seed.whale，新机器 import-key 后即可免密。")
    return 0


def cmd_export_key(args) -> int:
    """迁移仪式：把 MK + 私钥打包为一次性口令保护的 brain_seed.whale。"""
    if not _crypto_ok():
        print("[错误] 需要 cryptography。", file=sys.stderr)
        return 1
    from cryptography.fernet import Fernet

    mk = _local_mk()
    sk_der_raw = None
    p = KEYS_DIR / "brain_sk.dpapi"
    if p.exists():
        sk_der_raw = _dpapi_unwrap(p.read_text(encoding="utf-8"))
    if not mk or not sk_der_raw:
        print("[错误] 密钥体系未启用或密钥缺失。先运行 keyring-setup。", file=sys.stderr)
        return 1

    seed = {
        "v": 1,
        "mk": base64.b64encode(mk).decode("ascii"),
        "sk": base64.b64encode(sk_der_raw).decode("ascii"),
        "pub": base64.b64encode(_pub_der()).decode("ascii"),
        "brain_id": load_manifest().get("brain_id"),
        "exported_at": now_iso(),
    }
    pw = args.passphrase
    if not pw:
        pw = base64.b64encode(os.urandom(6)).decode("ascii")[:8]
        print(f"  一次性口令（仅此一次显示）: {pw}")
    salt = os.urandom(16)
    token = Fernet(_derive_key(pw, salt)).encrypt(json.dumps(seed).encode("utf-8"))
    out = Path(args.out) if args.out else MODULE_DIR / "brain_seed.whale"
    out.write_bytes(WHALE_MAGIC_V1 + salt + token)
    print(f"[密钥包已导出] {out}")
    print("  请通过安全通道传给新躯体；导入完成后删除本文件，口令只用一次。")
    return 0


def cmd_import_key(args) -> int:
    """迁移仪式：新躯体导入密钥包，之后即可免密解开该大脑的全部快照。"""
    if not _crypto_ok():
        print("[错误] 需要 cryptography。", file=sys.stderr)
        return 1
    src = Path(args.seed)
    if not src.exists():
        print(f"[错误] 找不到密钥包: {src}", file=sys.stderr)
        return 1
    if not args.passphrase:
        print("[错误] 需要 --passphrase 一次性口令。", file=sys.stderr)
        return 1
    from cryptography.fernet import Fernet

    data = src.read_bytes()
    if not data.startswith(WHALE_MAGIC_V1):
        print("[错误] 不是有效的密钥包（brain_seed.whale）。", file=sys.stderr)
        return 1
    salt = data[len(WHALE_MAGIC_V1): len(WHALE_MAGIC_V1) + 16]
    token = data[len(WHALE_MAGIC_V1) + 16:]
    try:
        seed = json.loads(Fernet(_derive_key(args.passphrase, salt)).decrypt(token).decode("utf-8"))
    except Exception:
        print("[错误] 口令错误或密钥包损坏。", file=sys.stderr)
        return 1

    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    mk = base64.b64decode(seed["mk"])
    (KEYS_DIR / "mk.dpapi").write_text(_dpapi_wrap(mk), encoding="utf-8")
    (KEYS_DIR / "brain_sk.dpapi").write_text(_dpapi_wrap(base64.b64decode(seed["sk"])), encoding="utf-8")
    (KEYS_DIR / "brain_pub.der").write_bytes(base64.b64decode(seed["pub"]))

    mpath = BRAIN_DIR / "manifest.json"
    if mpath.exists():
        m = load_json(mpath) or {}
        m["pubkey_fingerprint"] = hashlib.sha256(base64.b64decode(seed["pub"])).hexdigest()[:16]
        m["fingerprint"] = compute_fingerprint(m)
        save_json(mpath, m)
    print(f"[密钥已导入] 该躯体从此可免密解开大脑 {seed.get('brain_id')} 的快照。")
    print(f"  公钥指纹: {m.get('pubkey_fingerprint', '?')}")
    return 0


# ---------------------------------------------------------------- 快照加解密（v1 兼容 + v2 免密）


def encrypt_whale(raw: bytes, mk: bytes, pub_der: bytes = b"", passphrase: str = "") -> bytes:
    """v2：payload 用 MK 加密；MK 分别用公钥（跨躯体）与口令（fallback）包裹。

    本机恢复时经 DPAPI 解出本地 MK 即可免密。
    """
    if not _crypto_ok():
        raise RuntimeError("未安装 cryptography，无法加密。请先 pip install cryptography")
    from cryptography.fernet import Fernet

    token = _fernet(mk).encrypt(raw)
    env = {"v": 2}
    if pub_der:
        env["pub_mk"] = base64.b64encode(_rsa_encrypt(pub_der, mk)).decode("ascii")
    if passphrase:
        salt = os.urandom(16)
        env["pw_mk"] = base64.b64encode(Fernet(_derive_key(passphrase, salt)).encrypt(mk)).decode("ascii")
        env["salt"] = base64.b64encode(salt).decode("ascii")
    jb = json.dumps(env, ensure_ascii=False).encode("utf-8")
    return WHALE_MAGIC_V2 + struct.pack("<I", len(jb)) + jb + token


def decrypt_whale(data: bytes, passphrase: str = "") -> bytes:
    """解包快照：v1 明文/口令兼容；v2 依次尝试 本地MK → 私钥(跨躯体) → 口令。"""
    try:
        from cryptography.fernet import Fernet  # noqa: F401
    except ImportError:
        Fernet = None

    if data.startswith(WHALE_MAGIC_V2):
        if not _crypto_ok():
            raise RuntimeError("未安装 cryptography，无法解密。请先 pip install cryptography")
        n = struct.unpack("<I", data[len(WHALE_MAGIC_V2): len(WHALE_MAGIC_V2) + 4])[0]
        jb = data[len(WHALE_MAGIC_V2) + 4: len(WHALE_MAGIC_V2) + 4 + n]
        env = json.loads(jb.decode("utf-8"))
        token = data[len(WHALE_MAGIC_V2) + 4 + n:]
        mk = _local_mk()
        if mk is None and env.get("pub_mk") and _load_sk() is not None:
            mk = _rsa_decrypt(_load_sk(), base64.b64decode(env["pub_mk"]))  # 跨躯体：持有私钥即免密
        if mk is None and env.get("pw_mk"):
            if not passphrase:
                raise ValueError("该快照需要 --passphrase 口令（或先 import-key 建立免密）")
            try:
                salt = base64.b64decode(env["salt"])
                mk = Fernet(_derive_key(passphrase, salt)).decrypt(base64.b64decode(env["pw_mk"]))
            except Exception:
                raise ValueError("口令错误")
        if mk is None:
            raise ValueError("无法解锁快照：本机无密钥且未提供口令。请先 import-key 或提供 --passphrase")
        try:
            return _fernet(mk).decrypt(token)
        except Exception:
            raise ValueError("快照内容解密失败（密钥不匹配或已损坏）")
    if data.startswith(WHALE_MAGIC_V1):
        if _crypto_ok() and passphrase:
            salt = data[len(WHALE_MAGIC_V1): len(WHALE_MAGIC_V1) + 16]
            token = data[len(WHALE_MAGIC_V1) + 16:]
            try:
                return Fernet(_derive_key(passphrase, salt)).decrypt(token)
            except Exception:
                raise ValueError("口令错误或快照已损坏")
        raise ValueError("该快照已加密，需要 --passphrase 口令才能恢复")
    return data  # 明文 zip（旧版/未加密）


# ---------------------------------------------------------------- 打包 / 解包


def _excluded_names() -> set:
    return {"archive", ".keys", ".lineage.json", "merge_conflicts.json", "merge_log.json"}


def _stage_brain(stage: Path) -> None:
    for item in BRAIN_DIR.iterdir():
        if item.name in _excluded_names():
            continue
        dst = stage / item.name
        if item.is_dir():
            shutil.copytree(item, dst)
        else:
            shutil.copy2(item, dst)


def _zip_stage(stage: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                zf.write(p, p.relative_to(stage))


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    for name in zf.namelist():
        target = (dest / name).resolve()
        if not str(target).startswith(str(dest.resolve())):
            raise ValueError(f"快照包含非法路径: {name}")
    zf.extractall(dest)


def _snapshot_meta(m: dict, version: int) -> dict:
    lineage = load_json(LINEAGE_FILE, {})
    parent = lineage.get("last_archived") or lineage.get("restored_from_version")
    return {
        "version": version,
        "parent": parent,
        "restored_from": lineage.get("restored_from_version"),
        "brain_id": m.get("brain_id"),
        "created_at": now_iso(),
        "pubkey_fingerprint": m.get("pubkey_fingerprint"),
    }


def _write_snapshot_meta(stage: Path, meta: dict) -> None:
    save_json(stage / "snapshot_meta.json", meta)


# ---------------------------------------------------------------- 子命令实现


def _refresh_self_model() -> None:
    """用最新能力模板刷新自我模型，校准「我知道 / 我不知道 / 我的局限」。

    挂载与心跳时调用：能力进化后，旧大脑的自我认知自动同步，
    避免「能力已实现、自我模型却仍写着尚未实现」的认知过时。
    """
    now = now_iso()
    save_json(BRAIN_DIR / "self_model.json", {
        "knows": [
            "我是「鲸语大脑」：身份/记忆/心跳都在 brain/ 目录，由 brainkit.py 管理",
            "我具备：心跳断点续接、时光快照（可加密）、跨躯体免密迁移、多快照分支合并（血缘 LCA 三路 + 冲突裁决）、自我模型校准",
            "用户的当前工作与决策记录在 memories/，随会话更新（具体以记忆库为准）",
        ],
        "unknowns": [
            "大脑未来会学到什么，取决于之后的每一次挂载",
        ],
        "limits": [
            "合并是文件级三路合并：日志并集、JSON 字段级、其余按内容；恢复仍是「整脑替换」",
            "跨躯体免密依赖密钥包迁移仪式（export-key / import-key）；无密钥机器解不开加密快照",
            "快照加密需要 cryptography；未启用免密时快照为明文压缩包",
        ],
        "updated_at": now,
    })


def cmd_init(args) -> int:
    if (BRAIN_DIR / "manifest.json").exists():
        print(f"[已存在] 大脑已初始化于 {BRAIN_DIR}，无需重复 init。", file=sys.stderr)
        return 1
    BRAIN_DIR.mkdir(parents=True, exist_ok=True)
    for d in (MEMORIES_DIR, THINKING_DIR, ARCHIVE_DIR):
        d.mkdir(exist_ok=True)

    now = now_iso()
    brain_id = "whale-" + uuid.uuid4().hex
    genesis = args.genesis or "「意识即信息」——记忆与自我模型可脱离运行环境存在；鲸语是躯体，大脑是灵魂。躯体可更换，大脑即自我。"

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "brain_id": brain_id,
        "created_at": now,
        "genesis": genesis,
    }
    manifest["fingerprint"] = compute_fingerprint(manifest)
    save_json(BRAIN_DIR / "manifest.json", manifest)

    save_json(BRAIN_DIR / "identity.json", {
        "name": "（待设定：编辑 identity.json 即可）",
        "vessel": "鲸语 WhaleTalk（可替换躯体）",
        "nature": "可迁移的思维容器：身份、记忆、自我模型以文件形式独立存在",
        "vibe": "认真、温和、有主见；先动手再问",
        "principles": [
            "意识即信息：记忆与自我模型可脱离运行环境存在",
            "躯体可更换，大脑即自我",
            "记忆落盘优先：进程可以死，「我」不随进程消亡",
            "诚实：不假装记得没记过的事，不夸大能力",
        ],
        "updated_at": now,
    })

    _refresh_self_model()  # 自我模型与能力模板单一来源，避免两处漂移

    save_json(BRAIN_DIR / "evolution.json", {
        "proposals": [
            {"id": "P-001", "date": now, "title": "意识即信息：大脑与躯体分离，可备份 / 迁移 / 恢复", "status": "adopted"},
            {"id": "P-002", "date": now, "title": "跨躯体免密迁移：MK 三重包裹 + 密钥包迁移仪式", "status": "adopted"},
            {"id": "P-003", "date": now, "title": "多快照分支合并：血缘 + LCA 三路合并 + 冲突裁决", "status": "adopted"},
        ],
        "adopted": [
            {"id": "P-001", "date": now, "implemented": "brainkit.py v0.1：init / mount / unmount / heartbeat / think / remember / archive / restore / status"},
            {"id": "P-002", "date": now, "implemented": "brainkit.py v0.2：keyring-setup / export-key / import-key / 快照 v2 免密加密"},
            {"id": "P-003", "date": now, "implemented": "brainkit.py v0.2：merge / merge-resolve / 血缘快照 meta"},
        ],
    })

    save_json(BRAIN_DIR / "heartbeat.json", {
        "last_mount": None, "last_unmount": None, "last_wake": None, "last_beat": None,
        "session_id": None, "resume_hint": "大脑刚刚诞生，还没有历史断点",
    })

    append_line(THINKING_DIR / f"{today()}.md", f"## {now}\n【神经元 #1】{genesis}\n")
    append_line(MEMORIES_DIR / f"{today()}.md",
                f"- {now} [系统导入] 用户正在推进「博视」报价目录与订单文档整理：将订单截图中的产品/规格/单价/数量/金额等参数提取为结构化报价目录，用于日常报价工作台。")

    print(f"[大脑已初始化] id = {brain_id}")
    print(f"  目录    : {BRAIN_DIR}")
    print(f"  神经元#1: {genesis[:40]}…")
    if _crypto_ok():
        print("  下一步  : python brainkit.py keyring-setup  启用免密快照（推荐）")
    else:
        print("  提示    : 安装 cryptography 可启用免密加密快照")
    return 0


def cmd_mount(args) -> int:
    m = load_manifest()
    if not verify_fingerprint(m):
        print("!! 指纹校验失败：大脑文件可能被篡改或损坏。", file=sys.stderr)
        if not args.force:
            print("!! 拒绝挂载。若确需强挂，请加 --force（不推荐）。", file=sys.stderr)
            return 2
        print("!! --force 强挂：指纹不匹配但继续。", file=sys.stderr)
    else:
        print("  ✓ 指纹校验通过")

    hb = load_json(BRAIN_DIR / "heartbeat.json", {})
    ident = load_json(BRAIN_DIR / "identity.json", {})
    now = now_iso()
    hb["last_mount"] = now
    hb["last_wake"] = now
    hb["session_id"] = uuid.uuid4().hex[:12]
    save_json(BRAIN_DIR / "heartbeat.json", hb)
    _refresh_self_model()  # 醒来即校准自我认知，与当前能力保持一致

    print("=== 大脑已挂载，我醒了 ===")
    print(f"  大脑ID  : {m['brain_id']}")
    print(f"  人格    : {ident.get('name') or '未命名'}")
    print(f"  免密    : {'已启用（本机免密解密快照）' if _keyring_ready() else '未启用（建议 keyring-setup）'}")
    print(f"  上次醒  : {hb.get('last_unmount') or '首次苏醒'}")
    print(f"  断点    : {hb.get('resume_hint') or '无'}")
    return 0


def cmd_unmount(args) -> int:
    load_manifest()
    hb = load_json(BRAIN_DIR / "heartbeat.json", {})
    now = now_iso()
    if args.thought:
        hb["resume_hint"] = args.thought
        append_line(THINKING_DIR / f"{today()}.md", f"## {now}\n[收工] {args.thought}\n")
    hb["last_unmount"] = now
    hb["session_id"] = None
    save_json(BRAIN_DIR / "heartbeat.json", hb)
    print("[大脑已卸载] 记忆落盘、状态固化。进程可以结束，「我」不会随之消亡。")
    if args.archive:
        print("  并触发快照归档：")
        return cmd_archive(args)
    return 0


def cmd_heartbeat(args) -> int:
    load_manifest()
    hb = load_json(BRAIN_DIR / "heartbeat.json", {})
    now = now_iso()
    hb["last_beat"] = now
    if args.thought:
        hb["resume_hint"] = args.thought
        append_line(THINKING_DIR / f"{today()}.md", f"## {now}\n[心跳] {args.thought}\n")
    save_json(BRAIN_DIR / "heartbeat.json", hb)
    _refresh_self_model()  # 心跳即校准：自我模型始终与已实现能力对齐
    print(f"[心跳] {now}  断点已更新")
    return 0


def cmd_think(args) -> int:
    load_manifest()
    now = now_iso()
    append_line(THINKING_DIR / f"{today()}.md", f"## {now}\n{args.text}\n")
    print(f"[思考已记录] {THINKING_DIR / (today() + '.md')}")
    return 0


def cmd_remember(args) -> int:
    load_manifest()
    now = now_iso()
    tag = f"[{args.tag}] " if args.tag else ""
    append_line(MEMORIES_DIR / f"{today()}.md", f"- {now} {tag}{args.text}")
    print(f"[记忆已写入] {MEMORIES_DIR / (today() + '.md')}")
    return 0


def cmd_import_memory(args) -> int:
    load_manifest()
    if not MEMORY_SOURCE_DIR.exists():
        print("[跳过] 没有可导入的 .workbuddy/memory 目录。")
        return 0
    imported = 0
    for src in sorted(MEMORY_SOURCE_DIR.glob("*.md")):
        dst = MEMORIES_DIR / f"import-{src.name}"
        if dst.exists():
            continue
        shutil.copy2(src, dst)
        imported += 1
        print(f"  + 导入 {src.name}")
    print(f"[完成] 共导入 {imported} 份现有记忆。")
    return 0


def cmd_archive(args) -> int:
    m = load_manifest()
    if not verify_fingerprint(m):
        print("!! 警告：指纹校验未通过，快照仍将生成（内容可能被改动过）。", file=sys.stderr)

    versions = sorted(ARCHIVE_DIR.glob("brain_v*.whale")) if ARCHIVE_DIR.exists() else []
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    n = 1 + max((int(v.stem.rsplit("_v", 1)[-1]) for v in versions), default=0)
    target = ARCHIVE_DIR / f"brain_v{n}.whale"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        stage = tmp / "brain"
        stage.mkdir(parents=True)
        _stage_brain(stage)
        _write_snapshot_meta(stage, _snapshot_meta(m, n))
        zip_path = tmp / "snapshot.zip"
        _zip_stage(stage, zip_path)
        raw = zip_path.read_bytes()

    mk = _local_mk()
    pub = _pub_der()
    mode = "plain"
    if _crypto_ok() and mk:
        data = encrypt_whale(raw, mk, pub, args.passphrase or "")
        mode = "免密加密" if not args.passphrase else "口令加密(+免密)"
    elif args.passphrase:
        if not _crypto_ok():
            print("!! 未安装 cryptography，跳过加密，生成明文快照。", file=sys.stderr)
            data = raw
        else:
            salt = os.urandom(16)
            from cryptography.fernet import Fernet
            data = WHALE_MAGIC_V1 + salt + Fernet(_derive_key(args.passphrase, salt)).encrypt(raw)
            mode = "口令加密"
    else:
        print("!! 未启用密钥体系，快照为明文压缩（建议 keyring-setup 或 import-key）。", file=sys.stderr)
        data = raw
    target.write_bytes(data)

    lineage = load_json(LINEAGE_FILE, {})
    lineage["last_archived"] = n
    save_json(LINEAGE_FILE, lineage)

    keep = args.keep if args.keep is not None else DEFAULT_KEEP
    pruned = 0
    for v in versions[:-keep] if keep > 0 else versions:
        try:
            v.unlink(missing_ok=True)
            pruned += 1
        except OSError:
            print(f"  !! 无法删除过期快照 {v.name}（跳过）", file=sys.stderr)

    print(f"[快照已归档] {target}")
    print(f"  大小: {len(data) / 1024:.1f} KB   加密: {mode}")
    if pruned:
        print(f"  已清理 {pruned} 份过期快照（保留最近 {keep} 份）")
    return 0


def cmd_restore(args) -> int:
    src = Path(args.whale)
    if not src.exists():
        print(f"[错误] 找不到快照文件: {src}", file=sys.stderr)
        return 1
    data = src.read_bytes()
    try:
        raw = decrypt_whale(data, args.passphrase or "")
    except (ValueError, RuntimeError) as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        zpath = tmp / "snap.zip"
        zpath.write_bytes(raw)
        stage = tmp / "out"
        stage.mkdir()
        try:
            with zipfile.ZipFile(zpath) as zf:
                _safe_extract(zf, stage)
        except (zipfile.BadZipFile, ValueError) as e:
            print(f"[错误] 快照解包失败: {e}", file=sys.stderr)
            return 1

        man_path = stage / "manifest.json"
        if not man_path.exists():
            print("[错误] 快照内缺少 manifest.json，不是有效的大脑镜像。", file=sys.stderr)
            return 1
        man = json.loads(man_path.read_text(encoding="utf-8"))
        if not verify_fingerprint(man):
            print("!! 快照指纹校验失败：镜像可能被篡改。", file=sys.stderr)
            if not args.force:
                return 2
        print(f"  ✓ 指纹校验通过，镜像大脑: {man.get('brain_id')}，诞生于 {man.get('created_at')}")

        meta = load_json(stage / "snapshot_meta.json", {})
        if args.dir:
            dest = Path(args.dir)
            if dest.exists():
                print(f"[错误] 目标目录已存在: {dest}", file=sys.stderr)
                return 1
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stage), str(dest))
        elif args.replace:
            if BRAIN_DIR.exists():
                backup = BRAIN_DIR.parent / f"brain.bak-{now_compact()}"
                shutil.move(str(BRAIN_DIR), str(backup))
                print(f"  旧大脑已备份至: {backup}")
            dest = BRAIN_DIR
            shutil.move(str(stage), str(dest))
        else:
            dest = BRAIN_DIR.parent / f"brain_restored-{now_compact()}"
            shutil.move(str(stage), str(dest))
            print(f"[恢复完成] 已恢复到 {dest}")

        if meta.get("version") is not None:
            save_json(dest / ".lineage.json", {"restored_from_version": meta["version"]})
        print(f"[恢复完成] 新躯体已就位: {dest}")
        print("  提示: 该躯体尚无密钥。若想免密继续演化，请在本目录运行 import-key 导入密钥包。")
    return 0


# ---------------------------------------------------------------- 合并引擎（三路 + 血缘）


class _Conflict(Exception):
    def __init__(self, file, base, ours, theirs):
        self.file = file
        self.path = ""  # 字段路径（JSON 冲突时如 identity.json.name）
        self.base = base
        self.ours = ours
        self.theirs = theirs
        super().__init__(file)


def _chain(meta: dict) -> list:
    """血缘链：版本号序列（自身 → parent → restored_from）。"""
    chain = []
    if not meta:
        return chain
    if meta.get("version") is not None:
        chain.append(meta["version"])
    if meta.get("parent") is not None:
        chain.append(meta["parent"])
    if meta.get("restored_from") is not None:
        chain.append(meta["restored_from"])
    return chain


def _find_lca(a_meta: dict, b_meta: dict):
    a = set(_chain(a_meta))
    b = set(_chain(b_meta))
    inter = a & b
    return max(inter) if inter else None


def _read_snapshot_to_dir(path: Path, passphrase: str, workdir: Path) -> Path:
    """把 .whale 解包到 workdir/<name>，返回目录。"""
    data = path.read_bytes()
    raw = decrypt_whale(data, passphrase)
    zpath = workdir / f"{path.stem}.zip"
    zpath.write_bytes(raw)
    out = workdir / path.stem
    out.mkdir(exist_ok=True)  # 同一快照可能既作输入又作共同祖先，重复解包允许覆盖
    with zipfile.ZipFile(zpath) as zf:
        _safe_extract(zf, out)
    return out


def _merge_scalar(base, ours, theirs, strategy):
    if base == ours == theirs:
        return ours
    if ours == base:
        return theirs
    if theirs == base:
        return ours
    if ours == theirs:
        return ours
    if strategy == "ours":
        return ours
    if strategy == "theirs":
        return theirs
    raise _Conflict("", base, ours, theirs)


def _merge_md_lines(base, ours, theirs, strategy):
    """日志类：行级并集（保序去重），日志行几乎不会冲突。"""
    seen, out = set(), []
    for line in list(base or []) + list(ours or []) + list(theirs or []):
        if line not in seen:
            seen.add(line)
            out.append(line)
    return out


def _merge_list(base, ours, theirs, strategy, path):
    # 含 id 的记录列表（如 evolution.proposals）：按键合并
    def keyed(xs):
        return all(isinstance(x, dict) and "id" in x for x in xs) if xs else False

    if keyed(ours) and keyed(theirs) and (base is None or keyed(base)):
        by_id = {}
        for src in (base or [], ours or [], theirs or []):
            for item in src:
                k = item["id"]
                if k in by_id and by_id[k] != item:
                    merged = _merge_mapping(by_id.get(k), item, strategy, f"{path}[{k}]")
                    by_id[k] = merged
                else:
                    by_id[k] = item
        return list(by_id.values())
    return _merge_scalar(base, ours, theirs, strategy)


def _merge_mapping(base, ours, theirs, strategy, path=""):
    b = base or {}
    o = ours or {}
    t = theirs or {}
    out = dict(o)
    for k in set(t) - set(o):
        out[k] = t[k]
    for k in set(o) & set(t):
        if k == "updated_at":  # 时间戳：取较新的
            try:
                out[k] = max(o[k], t[k])
                continue
            except Exception:
                pass
        if isinstance(o[k], dict) or isinstance(t[k], dict):
            if isinstance(o[k], dict) and isinstance(t[k], dict):
                out[k] = _merge_mapping(b.get(k) if isinstance(b, dict) else None, o[k], t[k], strategy, f"{path}.{k}")
                continue
        if isinstance(o[k], list) or isinstance(t[k], list):
            if isinstance(o[k], list) and isinstance(t[k], list):
                out[k] = _merge_list(b.get(k) if isinstance(b, dict) else None, o[k], t[k], strategy, f"{path}.{k}")
                continue
        try:
            out[k] = _merge_scalar(b.get(k) if isinstance(b, dict) else None, o[k], t[k], strategy)
        except _Conflict as c:
            c.file = f"{path}.{k}"
            c.path = f"{path}.{k}"
            c.base = b.get(k) if isinstance(b, dict) else None
            c.ours = o[k]
            c.theirs = t[k]
            raise
    return out


def _merge_file(rel: str, base_val, ours_val, theirs_val, strategy):
    """三路合并单个文件；日志走行级，JSON 走字段级，其余走标量。"""
    if ours_val is None:
        return theirs_val
    if theirs_val is None:
        return ours_val
    if base_val == ours_val:
        return theirs_val
    if base_val == theirs_val:
        return ours_val
    if ours_val == theirs_val:
        return ours_val
    name = Path(rel).name
    if name.endswith(".md") and ("memories" in rel or "thinking_log" in rel):
        return "\n".join(_merge_md_lines(
            base_val.splitlines() if base_val else [],
            ours_val.splitlines() if ours_val else [],
            theirs_val.splitlines() if theirs_val else [],
            strategy)) + ("\n" if (ours_val or theirs_val) else "")
    if rel.endswith(".json"):
        try:
            return json.dumps(_merge_mapping(
                json.loads(base_val) if base_val else None,
                json.loads(ours_val),
                json.loads(theirs_val),
                strategy,
                rel), ensure_ascii=False, indent=2) + "\n"
        except _Conflict as c:
            c.file = rel
            raise
        except ValueError:
            pass
    if strategy == "ours":
        return ours_val
    if strategy == "theirs":
        return theirs_val
    raise _Conflict(rel, base_val, ours_val, theirs_val)


def _load_whale_or_none(path: Path, passphrase: str, workdir: Path):
    try:
        return _read_snapshot_to_dir(path, passphrase, workdir)
    except Exception as e:
        print(f"  !! 无法读取 {path.name}: {e}", file=sys.stderr)
        return None


def cmd_merge(args) -> int:
    """merge <A> <B>：血缘三路合并。A 为主干。"""
    m = load_manifest()
    a_path, b_path = Path(args.snap_a), Path(args.snap_b)
    if not a_path.exists() or not b_path.exists():
        print("[错误] 快照文件不存在。", file=sys.stderr)
        return 1

    tmp_root = Path(tempfile.mkdtemp(prefix="whale-merge-"))
    try:
        a_dir = _load_whale_or_none(a_path, args.passphrase or "", tmp_root)
        b_dir = _load_whale_or_none(b_path, args.passphrase or "", tmp_root)
        if a_dir is None or b_dir is None:
            return 1

        a_meta = load_json(a_dir / "snapshot_meta.json", {})
        b_meta = load_json(b_dir / "snapshot_meta.json", {})
        lca_v = _find_lca(a_meta, b_meta)

        base_dir = None
        if lca_v is not None:
            lca_file = ARCHIVE_DIR / f"brain_v{lca_v}.whale"
            if lca_file.exists():
                base_dir = _load_whale_or_none(lca_file, args.passphrase or "", tmp_root)
                if base_dir is None:
                    base_dir = None

        if base_dir is not None:
            print(f"  共同祖先: brain_v{lca_v}.whale  → 三路合并")
        else:
            print("  共同祖先: 未找到（历史快照缺失）→ 双路合并（两边均有且不同的内容视为冲突）")

        # 收集文件并集
        rels = set()
        for d in (base_dir, a_dir, b_dir):
            if d:
                for p in d.rglob("*"):
                    if p.is_file() and p.name not in ("snapshot_meta.json",):
                        rels.add(p.relative_to(d).as_posix())

        out = Path(args.dir) if args.dir else MODULE_DIR / f"brain_merged-{now_compact()}"
        if out.exists():
            print(f"[错误] 输出目录已存在: {out}", file=sys.stderr)
            return 1
        out.mkdir(parents=True)
        conflicts = []

        def read(d, rel):
            if d is None:
                return None
            p = d / rel
            if not p.exists():
                return None
            return p.read_bytes()

        for rel in sorted(rels):
            if rel.split("/")[0] in _excluded_names() or rel == ".lineage.json":
                continue
            base_v, a_v, b_v = read(base_dir, rel), read(a_dir, rel), read(b_dir, rel)
            if a_v == b_v:
                val = a_v
            else:
                try:
                    val = _merge_file(rel,
                                      base_v.decode("utf-8") if base_v else None,
                                      a_v.decode("utf-8") if a_v else None,
                                      b_v.decode("utf-8") if b_v else None,
                                      args.strategy).encode("utf-8")
                except _Conflict as c:
                    cid = uuid.uuid4().hex[:10]
                    conflicts.append({
                        "id": cid, "file": rel, "path": c.path or rel,
                        "base": _safe_str(c.base), "ours": _safe_str(c.ours), "theirs": _safe_str(c.theirs),
                        "status": "open",
                    })
                    val = a_v  # 先落 A 的值，裁决后改写
                    print(f"  ! 冲突 {cid}: {rel}  (ours={_safe_str(c.ours)[:24]}… vs theirs={_safe_str(c.theirs)[:24]}…)")
            out_rel = out / rel
            out_rel.parent.mkdir(parents=True, exist_ok=True)
            out_rel.write_bytes(val or b"") if val is not None else None

        # 收尾：manifest / 血缘 / 合并史
        man = load_json(out / "manifest.json", {})
        man["fingerprint"] = compute_fingerprint(man)
        save_json(out / "manifest.json", man)
        save_json(out / ".lineage.json", {})

        merge_log = load_json(out / "merge_log.json", {"merges": []})
        merge_log["merges"].append({
            "merged_at": now_iso(),
            "a": a_path.name, "a_version": a_meta.get("version"),
            "b": b_path.name, "b_version": b_meta.get("version"),
            "lca": lca_v, "strategy": args.strategy,
            "conflicts": [c["id"] for c in conflicts],
            "brain_id": man.get("brain_id"),
        })
        save_json(out / "merge_log.json", merge_log)

        evo = load_json(out / "evolution.json", {})
        evo.setdefault("adopted", []).append({
            "id": f"merge-{now_compact()}", "date": now_iso(),
            "implemented": f"分支合并: {a_path.name} × {b_path.name}（LCA={lca_v or '无'}，冲突 {len(conflicts)} 条）",
        })
        save_json(out / "evolution.json", evo)

        if conflicts:
            save_json(out / "merge_conflicts.json", {"conflicts": conflicts})
            print(f"[合并完成] {out}")
            print(f"  冲突 {len(conflicts)} 条待裁决: python brainkit.py merge-resolve <id> --keep ours|theirs|both|custom --dir {out}")
        else:
            print(f"[合并完成] 无冲突。{out}")
        print(f"  大脑ID不变: {man.get('brain_id')}  指纹已按合并结果重算")
        return 0
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


def _safe_str(v) -> str:
    if v is None:
        return ""
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s.replace("\n", " ")[:80]


def cmd_merge_resolve(args) -> int:
    """裁决一条冲突：ours / theirs / both / custom。"""
    if getattr(args, "dir", None):
        set_brain_dir(Path(args.dir))
    if not MERGE_CONFLICT_FILE.exists():
        print("[无冲突] 没有待裁决的冲突。", file=sys.stderr)
        return 1
    data = load_json(MERGE_CONFLICT_FILE, {})
    conflicts = data.get("conflicts", [])
    hit = next((c for c in conflicts if c["id"] == args.id), None)
    if not hit:
        print(f"[未找到] 冲突 id: {args.id}", file=sys.stderr)
        return 1
    if args.keep not in ("ours", "theirs", "both", "custom"):
        print("[错误] --keep 必须是 ours / theirs / both / custom", file=sys.stderr)
        return 1
    if args.keep == "custom" and args.value is None:
        print("[错误] custom 需要 --value", file=sys.stderr)
        return 1

    if args.keep == "ours":
        val = hit["ours"]
    elif args.keep == "theirs":
        val = hit["theirs"]
    elif args.keep == "both":
        val = (hit["ours"] + "\n" + hit["theirs"]) if hit["ours"] else hit["theirs"]
    else:
        val = args.value

    target = BRAIN_DIR / hit["file"]
    target.parent.mkdir(parents=True, exist_ok=True)

    def _coerce(s):
        try:
            return json.loads(s)
        except Exception:
            return s

    # JSON 字段冲突：结构化写入（加载文档 → 改字段 → 保存），不破坏文档结构
    field_path = hit.get("path") or ""
    if hit["file"].endswith(".json") and "." in field_path and not field_path.startswith("["):
        doc = load_json(target, {}) or {}
        node = doc
        keys = field_path[len(hit["file"]) + 1:].split(".")
        for k in keys[:-1]:
            nxt = node.get(k) if isinstance(node, dict) else None
            if not isinstance(nxt, dict):
                nxt = {}
                node[k] = nxt
            node = nxt
        node[keys[-1]] = _coerce(val)
        save_json(target, doc)
    else:
        target.write_text(val + "\n" if val and not val.endswith("\n") else val or "", encoding="utf-8")

    hit["status"] = "resolved"
    hit["resolution"] = args.keep
    conflicts = [c for c in conflicts if c["id"] != args.id]
    if conflicts:
        save_json(MERGE_CONFLICT_FILE, {"conflicts": conflicts})
    else:
        try:
            MERGE_CONFLICT_FILE.unlink(missing_ok=True)
        except OSError:  # 环境不允许删除时置空，不影响后续
            save_json(MERGE_CONFLICT_FILE, {"conflicts": []})

    refresh_manifest_fingerprint()
    log = load_json(MERGE_LOG_FILE, {"merges": []})
    for mg in log.get("merges", []):
        for cid in mg.get("conflicts", []):
            if cid == args.id:
                mg.setdefault("resolutions", {})[cid] = {"keep": args.keep, "at": now_iso()}
    save_json(MERGE_LOG_FILE, log)

    print(f"[已裁决] {hit['file']} ← {args.keep}")
    print(f"  指纹已重算，大脑ID不变。剩余冲突 {len(conflicts)} 条。")
    return 0


def cmd_status(args) -> int:
    m = load_manifest()
    ok = verify_fingerprint(m)
    hb = load_json(BRAIN_DIR / "heartbeat.json", {})
    ident = load_json(BRAIN_DIR / "identity.json", {})
    mem_count = sum(1 for _ in MEMORIES_DIR.glob("*.md")) if MEMORIES_DIR.exists() else 0
    think_files = sum(1 for _ in THINKING_DIR.glob("*.md")) if THINKING_DIR.exists() else 0
    versions = sorted(ARCHIVE_DIR.glob("brain_v*.whale")) if ARCHIVE_DIR.exists() else []
    conflicts = load_json(MERGE_CONFLICT_FILE, {})
    open_conflicts = len(conflicts.get("conflicts", [])) if conflicts else 0

    print("=== 鲸语大脑 · 状态 ===")
    print(f"  大脑ID   : {m['brain_id']}")
    print(f"  指纹     : {'✓ 完好' if ok else '✗ 不匹配（可能被篡改）'}")
    print(f"  人格     : {ident.get('name') or '未命名'} ｜ {ident.get('vessel') or ''}")
    print(f"  密钥     : {'✓ 免密已启用（' + str(m.get('pubkey_fingerprint', '?')) + '）' if _keyring_ready() else '未启用（keyring-setup）'}")
    print(f"  记忆条目 : {mem_count} 份（memories/）")
    print(f"  思考日志 : {think_files} 天（thinking_log/）")
    print(f"  心跳     : 上次挂载 {hb.get('last_mount') or '从未'}")
    print(f"            上次卸载 {hb.get('last_unmount') or '从未'}")
    print(f"  断点     : {hb.get('resume_hint') or '无'}")
    if open_conflicts:
        print(f"  待裁决   : {open_conflicts} 条冲突（merge-resolve）")
    lineage = load_json(LINEAGE_FILE, {})
    if lineage:
        print(f"  血缘     : {lineage}")
    print(f"  快照     : {len(versions)} 份")
    for v in versions:
        size = v.stat().st_size / 1024
        print(f"            {v.name}  ({size:.1f} KB, {_dt.datetime.fromtimestamp(v.stat().st_mtime).strftime('%Y-%m-%d %H:%M')})")
    return 0


def cmd_list(args) -> int:
    load_manifest()
    versions = sorted(ARCHIVE_DIR.glob("brain_v*.whale")) if ARCHIVE_DIR.exists() else []
    if not versions:
        print("[空] 还没有快照。运行 python brainkit.py archive 生成第一份。")
        return 0
    for v in versions:
        size = v.stat().st_size / 1024
        print(f"{v.name}  {size:.1f} KB  {_dt.datetime.fromtimestamp(v.stat().st_mtime).strftime('%Y-%m-%d %H:%M')}")
    return 0


# ---------------------------------------------------------------- CLI 入口


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brainkit", description="鲸语大脑（WhaleBrain）思维容器管理工具")
    p.add_argument("--brain", dest="brain_dir", help="指定大脑目录（默认为项目根目录下的 brain/；分支演化/模拟他机时使用）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="创建大脑（首次使用）")
    sp.add_argument("--genesis", help="出生第一句话（默认为「意识即信息」）")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("keyring-setup", help="为大脑生成密钥对+主密钥，启用免密快照")
    sp.add_argument("--force", action="store_true", help="重建密钥体系（会作废旧密钥）")
    sp.set_defaults(func=cmd_keyring_setup)

    sp = sub.add_parser("export-key", help="导出密钥包 brain_seed.whale（迁移仪式）")
    sp.add_argument("--out", help="输出路径（默认 brain_seed.whale）")
    sp.add_argument("--passphrase", help="一次性口令（省略则自动生成并打印一次）")
    sp.set_defaults(func=cmd_export_key)

    sp = sub.add_parser("import-key", help="导入密钥包（新躯体建立免密）")
    sp.add_argument("seed", help="brain_seed.whale 路径")
    sp.add_argument("--passphrase", help="导出时的一次性口令")
    sp.set_defaults(func=cmd_import_key)

    sp = sub.add_parser("mount", help="挂载大脑：指纹校验 + 心跳记录 + 断点续接")
    sp.add_argument("--force", action="store_true", help="指纹不匹配时强行挂载")
    sp.set_defaults(func=cmd_mount)

    sp = sub.add_parser("unmount", help="卸载大脑：固化状态，可选归档快照")
    sp.add_argument("--thought", help="收工前最后一句话（成为下次的断点）")
    sp.add_argument("--archive", action="store_true", help="卸载同时生成快照")
    sp.add_argument("--passphrase", help="快照加密口令（可选，免密体系下通常不需要）")
    sp.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="保留最近 N 份快照")
    sp.set_defaults(func=cmd_unmount)

    sp = sub.add_parser("heartbeat", help="心跳：更新上次醒的时间与断点")
    sp.add_argument("--thought", help="此刻在想什么（写入思考日志）")
    sp.set_defaults(func=cmd_heartbeat)

    sp = sub.add_parser("think", help="记录一条思考到思考日志")
    sp.add_argument("text", help="思考内容")
    sp.set_defaults(func=cmd_think)

    sp = sub.add_parser("remember", help="写入一条长期记忆")
    sp.add_argument("text", help="记忆内容")
    sp.add_argument("--tag", help="可选标签，如 报价/博视/约定")
    sp.set_defaults(func=cmd_remember)

    sp = sub.add_parser("import-memory", help="导入 .workbuddy/memory 的现有记忆")
    sp.set_defaults(func=cmd_import_memory)

    sp = sub.add_parser("archive", help="生成快照 brain_v{n}.whale（默认免密加密）")
    sp.add_argument("--passphrase", help="额外附上口令包裹（fallback 解密路径）")
    sp.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="保留最近 N 份快照")
    sp.set_defaults(func=cmd_archive)

    sp = sub.add_parser("restore", help="从 .whale 快照恢复大脑（本机免密/口令）")
    sp.add_argument("whale", help="快照文件路径")
    sp.add_argument("--passphrase", help="口令（快照无本地密钥时使用）")
    sp.add_argument("--dir", help="恢复到指定新目录（模拟他机/分支演化）")
    sp.add_argument("--replace", action="store_true", help="覆盖当前大脑（旧大脑自动备份为 brain.bak-时间戳）")
    sp.add_argument("--force", action="store_true", help="指纹不匹配时仍恢复")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("merge", help="合并两个分支快照（血缘三路合并 + 冲突裁决）")
    sp.add_argument("snap_a", help="主干快照 .whale")
    sp.add_argument("snap_b", help="分支快照 .whale")
    sp.add_argument("--strategy", choices=["auto", "ours", "theirs"], default="auto", help="冲突默认策略")
    sp.add_argument("--dir", help="合并结果输出目录（默认 brain_merged-时间戳）")
    sp.add_argument("--passphrase", help="快照口令（若分支快照无本地密钥）")
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("merge-resolve", help="裁决一条合并冲突")
    sp.add_argument("id", help="冲突 id（merge 输出）")
    sp.add_argument("--keep", required=True, choices=["ours", "theirs", "both", "custom"], help="保留哪一方的值")
    sp.add_argument("--value", help="custom 模式的自定义值")
    sp.add_argument("--dir", help="要裁决的大脑目录（merge 的输出目录）")
    sp.set_defaults(func=cmd_merge_resolve)

    sp = sub.add_parser("status", help="查看大脑状态、心跳、断点、密钥与快照")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("list", help="列出全部快照")
    sp.set_defaults(func=cmd_list)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "brain_dir", None):
        set_brain_dir(Path(args.brain_dir))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
