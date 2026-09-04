#!/usr/bin/env python3
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
  python brainkit.py remember "今天完成了一个重要约定"
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
import threading
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
MEMORY_JSONL = BRAIN_DIR / "memories" / "memory.jsonl"
MEMORY_SOURCE_DIR = MODULE_DIR / ".workbuddy" / "memory"  # import-memory 的来源（Agent 工作日志）

WHALE_MAGIC_V1 = b"WHALEBRAIN\x01"
WHALE_MAGIC_V2 = b"WHALEBRAIN\x02"
SCHEMA_VERSION = 1
DEFAULT_KEEP = 7
PBKDF2_ITER = 200_000
RSA_KEYSIZE = 2048
SIGN_MAGIC = b"WHALEBRAIN-SIG\x00"  # 快照签名块分隔（文件 = data + magic + signature）

# 记忆写路径进程内互斥；跨进程安全由「原子 append / tmp+os.replace」兜底。
# 用 RLock：save_memories/update/delete/version_replace/_record_hits 会嵌套
# 调用（持锁后落盘），普通 Lock 会自锁死，RLock 允许同线程重入。
_MEM_LOCK = threading.RLock()

# ---------------------------------------------------------------- 基础工具


def now_iso() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return _dt.date.today().isoformat()


def now_compact() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _ts_epoch(ts) -> float:
    """任意脑时间戳 → UTC epoch 秒（统一比较口径）。

    历史数据可能混写本地偏移（如 +08:00 与 +01:00 并存）——字符串倒序与
    naive 天数差都会失真；一律解析为带 tz 的绝对时间再比较即彻底消除。
    解析失败返回 0.0（老条目垫底，不干扰排序）。
    """
    try:
        raw = str(ts or "").strip()
        if not raw:
            return 0.0
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


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


def cross_process_lock(target: Path, timeout=10.0) -> bool:
    """L5 跨进程文件锁：以 O_EXCL 独占创建 target.lock 实现；超时返回 False。

    用于 CLI 与常驻 API 并发写 memory.jsonl / 快照版本号竞态。锁文件在
    try/finally 中删除。非 Windows 也适用（基于文件系统原子性）。
    """
    import time as _t
    lock_path = Path(str(target) + ".lock")
    deadline = _t.monotonic() + timeout
    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            if _t.monotonic() > deadline:
                return False
            _t.sleep(0.02)
        except OSError:
            return False


def release_lock(target: Path) -> None:
    lock_path = Path(str(target) + ".lock")
    try:
        lock_path.unlink(missing_ok=True)
    except OSError:
        pass


def set_brain_dir(p: Path) -> None:
    """全局 --dir：让工具可指向任意大脑目录（分支演化 / 模拟他机）。"""
    global BRAIN_DIR, MEMORIES_DIR, THINKING_DIR, ARCHIVE_DIR, KEYS_DIR, LINEAGE_FILE
    global MERGE_LOG_FILE, MERGE_CONFLICT_FILE, MEMORY_JSONL
    BRAIN_DIR = Path(p)
    MEMORIES_DIR = BRAIN_DIR / "memories"
    THINKING_DIR = BRAIN_DIR / "thinking_log"
    ARCHIVE_DIR = BRAIN_DIR / "archive"
    KEYS_DIR = BRAIN_DIR / ".keys"
    LINEAGE_FILE = BRAIN_DIR / ".lineage.json"
    MERGE_LOG_FILE = BRAIN_DIR / "merge_log.json"
    MERGE_CONFLICT_FILE = BRAIN_DIR / "merge_conflicts.json"
    MEMORY_JSONL = BRAIN_DIR / "memories" / "memory.jsonl"


# ---------------------------------------------------------------- 指纹（防篡改）


def compute_fingerprint(manifest: dict) -> str:
    body = {k: v for k, v in manifest.items() if k != "fingerprint"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def verify_fingerprint(manifest: dict) -> bool:
    return bool(manifest.get("fingerprint")) and compute_fingerprint(manifest) == manifest["fingerprint"]


def load_manifest() -> dict:
    path = BRAIN_DIR / "manifest.json"
    if not path.exists():
        print("[未初始化] 还没有大脑。先运行: python brainkit.py init", file=sys.stderr)
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


# ---------------------------------------------------------------- 快照签名（防伪造大脑）


def _sign_bytes(data: bytes):
    """用私钥对快照字节签名（PKCS1v15 + SHA256）；无密钥返回 None。"""
    sk = _load_sk()
    if sk is None:
        return None
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        return sk.sign(data, padding.PKCS1v15(), hashes.SHA256())
    except Exception:
        return None


def verify_bytes_sig(data: bytes, sig, pub_der: bytes = b"") -> bool:
    """用公钥验签；无签名视为未签名（True），有签名但验签失败为 False。"""
    if not sig:
        return True
    pub = pub_der or _pub_der()
    if not pub:
        return True  # 无公钥可验 → 不阻断（迁移机先 import-key 才能解密）
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
        key = serialization.load_der_public_key(pub)
        key.verify(sig, data, padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def _write_snapshot_with_sig(path: Path, data: bytes) -> str:
    """写快照文件（data + 可选签名块）。返回 'signed' / 'plain'。"""
    sig = _sign_bytes(data)
    if sig:
        path.write_bytes(data + SIGN_MAGIC + sig)
        return "signed"
    path.write_bytes(data)
    return "plain"


def _read_snapshot_with_sig(path: Path):
    """读快照文件 → (data, sig)；无签名块 sig=None。"""
    raw = path.read_bytes()
    if SIGN_MAGIC in raw:
        data, _, sig = raw.rpartition(SIGN_MAGIC)
        return data, sig
    return raw, None


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
    base = dest.resolve()
    for name in zf.namelist():
        target = (base / name).resolve()
        # 严格子路径判定：relative_to 要求目标必须在 base 之下（含分隔符边界，
        # 避免旧版 startswith 前缀误判，如 /a/bc 被当作 /a/b 的子路径）
        try:
            target.relative_to(base)
        except ValueError:
            raise ValueError(f"快照包含非法路径: {name}")
    zf.extractall(dest)


def _snapshot_meta(m: dict, version: int) -> dict:
    lineage = load_json(LINEAGE_FILE, {})
    parent = lineage.get("last_archived") or lineage.get("restored_from_version")
    # P2-3：完整血缘图——祖先链（最新在前，不含自身），供 _find_lca 真 LCA 求解；
    # 老快照无该字段时 _find_lca 自动降级为 [self, parent, restored_from]。
    ancestors = list(lineage.get("ancestors") or [])
    for v in (parent, lineage.get("restored_from_version")):
        if v is not None:
            vi = int(v)
            if vi not in ancestors:
                ancestors.append(vi)
    ancestors.sort(reverse=True)  # 最新在前，便于阅读与对账
    return {
        "version": version,
        "parent": parent,
        "restored_from": lineage.get("restored_from_version"),
        "ancestors": ancestors,
        "brain_id": m.get("brain_id"),
        "created_at": now_iso(),
        "pubkey_fingerprint": m.get("pubkey_fingerprint"),
    }


def _write_snapshot_meta(stage: Path, meta: dict) -> None:
    save_json(stage / "snapshot_meta.json", meta)


# ---------------------------------------------------------------- 子命令实现


def _refresh_self_model() -> None:
    """用最新能力模板刷新自我模型基线，校准「我知道 / 我不知道 / 我的局限」。

    挂载与心跳时调用：能力进化后，旧大脑的自我认知自动同步，
    避免「能力已实现、自我模型却仍写着尚未实现」的认知过时。

    P2-4（双来源优先级标记）：self_model.json 有两个写入来源——
    本函数（静态模板）与 brain_api.refresh_self_model()（LLM 动态校准）。
    若现有文件已带 source="llm"，说明 LLM 基于真实工具/记忆校准过，
    这里**只刷新 baseline 模板、保留 LLM 认知**，不再互相覆盖。
    """
    now = now_iso()
    baseline = {
        "knows": [
            "我是「鲸语大脑」：身份/记忆/心跳都在 brain/ 目录，由 brainkit.py 管理",
            "我具备：心跳断点续接、时光快照（可加密）、跨躯体免密迁移、多快照分支合并（血缘 LCA 三路 + 冲突裁决）、自我模型校准",
            "用户的当前工作与决策记录在 memories/，随会话更新（具体以记忆库为准）",
        ],
        "unknowns": [
            "大脑未来会学到什么，取决于之后的每一次挂载",
        ],
        "limits": [
            "合并是文件级三路合并：记忆 jsonl 行级智能合并（同 id 文本冲突自动取新者）、日志并集、JSON 字段级、其余按内容；恢复仍是「整脑替换」",
            "跨躯体免密依赖密钥包迁移仪式（export-key / import-key）；无密钥机器解不开加密快照",
            "快照加密需要 cryptography；未启用免密时快照为明文压缩包",
            "自动快照滚动清理默认保留最近 7 份，但血缘引用的版本（LCA/双亲）会豁免保留",
        ],
    }
    existing = load_json(BRAIN_DIR / "self_model.json")
    sm = {"baseline": baseline, "updated_at": now}
    if existing and isinstance(existing, dict) and existing.get("source") == "llm":
        # LLM 校准优先：保留其认知与校准时间，仅刷新基线模板
        for k in ("knows", "unknowns", "limits"):
            sm[k] = existing.get(k) or []
        sm["source"] = "llm"
        sm["calibrated_at"] = existing.get("calibrated_at")
    else:
        for k in ("knows", "unknowns", "limits"):
            sm[k] = baseline[k]
        sm["source"] = "template"
    save_json(BRAIN_DIR / "self_model.json", sm)


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
    # 新大脑不预置任何具体记忆（诚实原则：不假装记得没记过的事，不给用户塞无关历史）
    remember_structured("大脑初始化完成，近期记忆为空，等待首次记录。", type="系统",
                        importance=1, tags=["系统"], source="系统")

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


# ---------------------------------------------------------------- 结构化记忆（海马体 v2）
# memory.jsonl：每行一条结构化记忆 {id, ts, type, importance, text, tags, entities, relations, source, archived}
# 兼容旧版 memories/*.md 行（读取时自动解析），向后不破坏。


def _mem_id() -> str:
    return "m-" + uuid.uuid4().hex[:10]


def load_memories(include_archived: bool = False) -> list:
    """合并读取全部记忆：memory.jsonl 为主，旧版 memories/*.md 行自动兼容。

    返回 [{id, ts, type, importance, text, tags, entities, relations, source, archived}]。
    """
    out, seen = [], set()
    if MEMORY_JSONL.exists():
        for line in MEMORY_JSONL.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            e.setdefault("id", _mem_id())
            e.setdefault("type", "")
            e.setdefault("importance", 3)
            e.setdefault("tags", [])
            e.setdefault("entities", [])
            e.setdefault("relations", [])
            e.setdefault("source", "手动")
            e.setdefault("archived", False)
            e.setdefault("sensitivity", "public")  # L8 敏感度分级
            e.setdefault("hit_count", 0)  # F3 重要度自学习：命中次数
            e.setdefault("last_hit", "")   # F3 最近命中时间
            e.setdefault("supersedes", "")  # F2 事实版本链：被覆盖的旧记忆 id
            e.setdefault("version_id", "")  # F2 版本链祖先（不变）
            out.append(e)
            seen.add(e["id"])
    if MEMORIES_DIR.exists():
        for f in sorted(MEMORIES_DIR.glob("*.md")):
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if not s.startswith("- "):
                    continue
                body = s[2:].strip()
                ts = ""
                if len(body) > 19 and body[4] == "-" and body[10] == "T" and body[16] == ":":
                    ts = body[:19]
                    body = body[19:].strip()
                tag = ""
                if body.startswith("[") and "]" in body:
                    tag, _, body = body[1:].partition("]")
                    body = body.strip()
                e = {"id": _mem_id(), "ts": ts, "type": tag, "importance": 3, "text": body,
                     "tags": [tag] if tag else [], "entities": [], "relations": [],
                     "source": "旧版", "archived": False}
                if e["id"] not in seen:
                    out.append(e)
                    seen.add(e["id"])
    return [e for e in out if include_archived or not e.get("archived")]


def save_memories(items: list) -> None:
    """全量落盘（update/delete/consolidate 用）：tmp + os.replace 原子写，崩溃不损坏。

    进程内以 _MEM_LOCK 互斥；os.replace 保证读方永远看到完整文件。
    """
    MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
    with _MEM_LOCK:
        tmp = MEMORY_JSONL.with_suffix(".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for e in items:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        try:
            os.replace(tmp, MEMORY_JSONL)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise


def remember_structured(text, type="", importance=3, tags=None, entities=None, relations=None, source="手动",
                        sensitivity="public"):
    """写入一条结构化记忆到 memory.jsonl（同文本去重）。返回条目 dict 或 None。

    写入用「读-查重-原子追加」：单条新增是 O(1) append，不再整文件重写，
    规避记忆量大后的写放大；进程内加锁，跨进程由单行 append 的原子性兜底。

    L8 敏感度分级：sensitivity ∈ public/private/secret，供 share-export 等脱敏用。
    """
    text = str(text or "").strip()
    if not text:
        return None
    if str(sensitivity or "") not in ("public", "private", "secret"):
        sensitivity = "public"
    vid = _mem_id()  # F2 版本链祖先：一次记忆的多个 supersede 版本共享
    entry = {
        "id": _mem_id(), "ts": now_iso(), "type": str(type or "")[:20],
        "importance": max(1, min(5, int(importance or 3))),
        "text": text,
        "tags": [str(t).strip()[:20] for t in (tags or []) if str(t).strip()][:10],
        "entities": [str(e).strip()[:30] for e in (entities or []) if str(e).strip()][:20],
        "relations": [r for r in (relations or []) if isinstance(r, dict)][:20],
        "source": str(source or "手动")[:10], "archived": False,
        "sensitivity": sensitivity, "hit_count": 0, "last_hit": "",
        "supersedes": "", "version_id": vid,
    }
    with _MEM_LOCK:
        # 跨进程防重：CLI 与常驻 API(harvest 后台线程)不同进程并发写时，
        # 仅进程内锁无法互斥去重 → 用 .lock 文件跨进程串行（拿不到则退化为进程内）。
        got_cp = False
        try:
            MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
            got_cp = cross_process_lock(MEMORY_JSONL, timeout=3.0)
        except Exception:
            got_cp = False
        try:
            items = load_memories(include_archived=True)
            for it in items:
                if it.get("text") == entry["text"] and not it.get("archived"):
                    return it
            MEMORIES_DIR.mkdir(parents=True, exist_ok=True)
            append_line(MEMORY_JSONL, json.dumps(entry, ensure_ascii=False))
        finally:
            if got_cp:
                release_lock(MEMORY_JSONL)
    return entry


def update_memory(mid: str, text=None, type=None, importance=None, tags=None, archived=None, sensitivity=None) -> bool:
    """就地更新一条记忆（id 已定位，用于精确同步）。

    F2 事实版本链：工具层的 update_memory（改文本）走版本追加（见 brain_api/`version_replace_memory`，
    本函数保持「同 id 就地改」以兼容旧调用）；此处 text 改动直接覆盖并保留原 id/version_id。
    新增可选 sensitivity 就地更新。
    """
    items = load_memories(include_archived=True)
    hit = next((e for e in items if e["id"] == mid), None)
    if not hit:
        return False
    if text is not None:
        hit["text"] = str(text).strip()
    if type is not None:
        hit["type"] = str(type)[:20]
    if importance is not None:
        hit["importance"] = max(1, min(5, int(importance)))
    if tags is not None:
        hit["tags"] = [str(t).strip() for t in tags if str(t).strip()][:10]
    if archived is not None:
        hit["archived"] = bool(archived)
    if sensitivity is not None and str(sensitivity) in ("public", "private", "secret"):
        hit["sensitivity"] = str(sensitivity)
    if text is not None and not hit.get("version_id"):
        hit["version_id"] = hit["id"]  # 老条目无版本号时以其自身为链祖
    hit["ts"] = now_iso()
    save_memories(items)
    return True


def version_replace_memory(mid: str, text: str, source="对话") -> str:
    """F2 事实版本链：把 id=mid 的记忆替换为新文本——新条目 supersedes=mid，
    旧条目标记 archived 但不删（保留溯源：可查「之前怎么说、为什么改」）。

    返回新条目 id（供后续引用）；找不到原条目则回退 remember_structured。
    """
    text = str(text or "").strip()
    if not text:
        return ""
    with _MEM_LOCK:
        items = load_memories(include_archived=True)
        old = next((e for e in items if e["id"] == mid and not e.get("archived")), None)
        if old is None:
            # 无原条目（或原已归档）→ 直接新写一条
            e = remember_structured(text, type=str(old.get("type") if old else "")[:20],
                                    importance=old.get("importance") if old else 3,
                                    tags=old.get("tags") if old else [],
                                    entities=old.get("entities") if old else [],
                                    relations=old.get("relations") if old else [],
                                    source=str(source)[:10],
                                    sensitivity=old.get("sensitivity") if old else "public")
            return e["id"] if e else ""
        old["archived"] = True
        old["ts"] = now_iso()  # 记录被替换时刻，便于沿时间线追溯
        entry = {
            "id": _mem_id(), "ts": now_iso(),
            "type": str(old.get("type") or "")[:20],
            "importance": int(old.get("importance") or 3),
            "text": text,
            "tags": list(old.get("tags") or [])[:10],
            "entities": list(old.get("entities") or [])[:20],
            "relations": list(old.get("relations") or [])[:20],
            "source": str(source or "对话")[:10], "archived": False,
            "sensitivity": str(old.get("sensitivity") or "public"),
            "hit_count": 0, "last_hit": "",
            "supersedes": old["id"],  # F2：指向被替换的旧版本
            "version_id": old.get("version_id") or old["id"],  # 同一事实链
        }
        save_memories(items + [entry])
        return entry["id"]


def delete_memory(mid: str) -> bool:
    items = load_memories(include_archived=True)
    n = len(items)
    items = [e for e in items if e["id"] != mid]
    if len(items) == n:
        return False
    save_memories(items)
    return True


def _mem_tokens(text: str) -> list:
    """分词：英文/数字按词，中文按单字 + 相邻双字 bigram（保证中文检索命中）。"""
    import re
    s = str(text or "").lower()
    toks = re.findall(r"[a-z0-9]+", s) + re.findall(r"[\u4e00-\u9fff]", s)
    out = list(toks)
    for i in range(len(toks) - 1):
        out.append(toks[i] + toks[i + 1])
    return out


def search_memories(query: str, limit: int = 5, record_hits: bool = False) -> list:
    """轻量相关性检索（IDF 加权余弦 + 覆盖率），无需外部依赖。空查询返回最新。

    L3 检索增强：tags/entities 作为补充语料参与匹配（命中给予小幅加分）；
    record_hits=True 时（对话注入用）把命中条目的 hit_count/last_hit 回写，
    供 F3 重要度自学习。
    """
    items = load_memories()
    if not items:
        return []
    if not str(query or "").strip():
        return sorted(items, key=lambda e: _ts_epoch(e.get("ts")), reverse=True)[:limit]
    import math
    q_toks = _mem_tokens(query)
    # L3：把 tags/entities 拼进文档语料——实体/标签命中显著提升相关性
    doc_corpus = [str(e.get("text") or "") + " " +
                  " ".join(str(t) for t in (e.get("tags") or [])) + " " +
                  " ".join(str(x) for x in (e.get("entities") or [])) for e in items]
    doc_toks = [_mem_tokens(dc) for dc in doc_corpus]
    n = len(items)
    idf = {}
    for t in set(q_toks):
        df = sum(1 for dt in doc_toks if t in dt)
        idf[t] = math.log((n + 1) / (df + 1)) + 1.0
    scored = []
    for e, dt in zip(items, doc_toks):
        common = set(q_toks) & set(dt)
        if not common:
            continue
        w = sum(idf.get(t, 1.0) for t in common)
        cosine = w / (len(set(q_toks)) ** 0.5 * len(set(dt)) ** 0.5)
        recall = len(common) / len(set(q_toks))
        # L3 元数据加权：tags/entities 命中给小幅上调（语义往往承载在实体上）
        ent_hits = set(q_toks) & set(_mem_tokens(" ".join(str(x) for x in (e.get("entities") or []))))
        tag_hits = set(q_toks) & set(_mem_tokens(" ".join(str(t) for t in (e.get("tags") or []))))
        meta_boost = 1.0 + 0.06 * len(ent_hits | tag_hits)
        score = (0.55 * cosine + 0.45 * recall) * meta_boost
        scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    top = [e for _, e in scored[:limit]]
    if record_hits and top:
        _record_hits(top)
    return top


def _record_hits(entries):
    """F3 重要度自学习：命中条目的 hit_count+1、更新 last_hit（就地写回，避免整文件重写）。

    被实际注入/使用的记忆权重随命中上升——记忆"因被用而重要"，而非写时一次性判断。
    """
    if not entries:
        return
    now = now_iso()
    with _MEM_LOCK:
        items = load_memories(include_archived=True)
        ids = {e.get("id") for e in entries}
        changed = False
        for e in items:
            if e.get("id") in ids and not e.get("archived"):
                e["hit_count"] = int(e.get("hit_count") or 0) + 1
                e["last_hit"] = now
                changed = True
        if changed:
            save_memories(items)


def consolidate_memories(min_importance=2, days=30):
    """睡眠巩固（本地版）：低重要度旧记忆归档 + 同类型相似记忆合并。

    返回 {"archived": n, "merged": n, "kept": n}。LLM 增强版见 brain_api.consolidate_with_llm。
    """
    items = load_memories(include_archived=True)
    now_epoch = _dt.datetime.now().timestamp()
    archived = merged = 0
    # 1) 低重要度 + 超过 days 天 → 归档（不删除，保留可查）
    #    天数判定用 UTC epoch 差（_ts_epoch 吸收历史混写时区），naive 墙钟差会失真
    for e in items:
        if e.get("archived"):
            continue
        age_days = (now_epoch - _ts_epoch(e.get("ts"))) / 86400.0
        if age_days < 0:
            continue
        if int(e.get("importance") or 3) < min_importance and age_days > days:
            e["archived"] = True
            archived += 1
    # 2) 同类型相似记忆合并（token Jaccard > 0.6）：保留重要度/时间更高的一方
    active = [e for e in items if not e.get("archived")]
    for i in range(len(active)):
        a = active[i]
        if a.get("archived"):
            continue
        for j in range(i + 1, len(active)):
            b = active[j]
            if b.get("archived"):
                continue
            if str(a.get("type")) != str(b.get("type")):
                continue
            ta, tb = set(_mem_tokens(a["text"])), set(_mem_tokens(b["text"]))
            if not ta or not tb:
                continue
            if len(ta & tb) / len(ta | tb) > 0.6:
                ka = (int(a.get("importance") or 3), str(a.get("ts") or ""))
                kb = (int(b.get("importance") or 3), str(b.get("ts") or ""))
                keep, drop = (a, b) if ka >= kb else (b, a)
                # 修复：近重复记忆的 text 融合只在确有互补信息时进行，且只拼接"对方独有的部分"，
                # 避免把本就相似的标签/结论无限累加成 "X（并入:X…）（并入:X…）" 的递归垃圾。
                keep_txt = str(keep.get("text") or "")
                drop_txt = str(drop.get("text") or "")
                # 对方 text 与 keep 高度重合（Jaccard>0.85）→ 视为同义噪音，只归档不拼接
                dt, kt = set(_mem_tokens(drop_txt)), set(_mem_tokens(keep_txt))
                nearly_same = bool(dt and kt and len(dt & kt) / len(dt | kt) > 0.85)
                if not nearly_same and len(keep_txt) < 160:
                    keep["text"] = keep_txt + "（并入:" + drop_txt[:36] + "…）"
                drop["archived"] = True
                merged += 1
    save_memories(items)
    return {
        "archived": archived, "merged": merged,
        "kept": sum(1 for e in items if not e.get("archived")),
        "total": len(items),
    }


def cmd_consolidate(args) -> int:
    load_manifest()
    r = consolidate_memories(min_importance=args.min_importance or 2, days=args.days or 30)
    print(f"[睡眠巩固完成] 归档 {r['archived']} · 合并 {r['merged']} · 现存 {r['kept']}/{r['total']}")
    return 0


# ---------------------------------------------------------------- 目标系统（goals.json）
GOALS_FILE = BRAIN_DIR / "goals.json"


def _goals_path():
    return BRAIN_DIR / "goals.json"


def load_goals():
    return load_json(_goals_path(), {"goals": []}).get("goals", [])


def save_goals(goals):
    save_json(_goals_path(), {"goals": goals})


def add_goal(title, note=""):
    """新增目标（去重：同名 active 目标不重复创建）。"""
    title = str(title or "").strip()
    if not title:
        return None
    goals = load_goals()
    for g in goals:
        if g.get("title") == title and g.get("status") == "active":
            return g
    g = {"id": "g-" + uuid.uuid4().hex[:8], "title": title[:80],
         "note": str(note or "")[:200], "status": "active",
         "progress": "", "created_at": now_iso(), "updated_at": now_iso()}
    goals.append(g)
    save_goals(goals)
    return g


def update_goal(gid, status=None, progress=None, note=None):
    goals = load_goals()
    hit = next((g for g in goals if g["id"] == gid), None)
    if not hit:
        return False
    if status in ("active", "done", "archived"):
        hit["status"] = status
    if progress is not None:
        hit["progress"] = str(progress)[:40]
    if note is not None:
        hit["note"] = str(note)[:200]
    hit["updated_at"] = now_iso()
    save_goals(goals)
    return True


def delete_goal(gid):
    goals = load_goals()
    n = len(goals)
    goals = [g for g in goals if g["id"] != gid]
    if len(goals) == n:
        return False
    save_goals(goals)
    return True


def cmd_goal(args) -> int:
    load_manifest()
    sub = args.goal_cmd
    if sub == "add":
        g = add_goal(args.title, args.note or "")
        print(f"[目标已添加] {g['id']}  {g['title']}" if g else "[重复] 已有进行中的同名目标")
    elif sub == "list":
        for g in load_goals():
            print(f"  [{g['status']}] {g['id']}  {g['title']}  {g.get('progress') or ''}")
    elif sub == "update":
        ok = update_goal(args.id, status=args.status, progress=args.progress, note=args.note)
        print("[已更新]" if ok else "[未找到]")
    elif sub == "delete":
        ok = delete_goal(args.id)
        print("[已删除]" if ok else "[未找到]")
    return 0


# ---------------------------------------------------------------- 决策日志（前额叶 v2）
DECISIONS_FILE = BRAIN_DIR / "decisions.jsonl"


def _decisions_path():
    return BRAIN_DIR / "decisions.jsonl"


def record_decision(decision, reason="", expected=""):
    """记录一条决策（决策 / 理由 / 预期结果）。返回条目 dict。"""
    decision = str(decision or "").strip()
    if not decision:
        return None
    d = {"id": "d-" + uuid.uuid4().hex[:8], "ts": now_iso(),
         "decision": decision[:200], "reason": str(reason or "")[:200],
         "expected": str(expected or "")[:200], "outcome": "", "status": "open"}
    with open(_decisions_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False) + "\n")
    return d


def list_decisions(limit=20):
    p = _decisions_path()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out[-limit:]


def resolve_decision(did, outcome="", status="kept"):
    """决策回执：记录实际结果与是否维持/反转。"""
    p = _decisions_path()
    if not p.exists():
        return False
    lines = []
    hit = False
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("id") == did:
            e["outcome"] = str(outcome or "")[:200]
            e["status"] = status if status in ("kept", "reversed") else "kept"
            hit = True
        lines.append(json.dumps(e, ensure_ascii=False))
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return hit


def cmd_decision(args) -> int:
    load_manifest()
    sub = args.dec_cmd
    if sub == "add":
        d = record_decision(args.decision, args.reason or "", args.expected or "")
        print(f"[决策已记录] {d['id']}  {d['decision'][:40]}" if d else "[错误] 决策内容为空")
    elif sub == "list":
        for d in list_decisions(args.limit or 20):
            st = "✓" if d.get("status") == "kept" else ("↺" if d.get("status") == "reversed" else "·")
            print(f"  {st} [{d['id']}] {d['decision'][:50]}  {d.get('outcome') or ''}")
    elif sub == "resolve":
        ok = resolve_decision(args.id, args.outcome or "", args.status or "kept")
        print("[已回执]" if ok else "[未找到]")
    return 0


def cmd_remember(args) -> int:
    load_manifest()
    tag = str(args.tag or "").strip()
    ents = [e.strip() for e in str(args.entities or "").split(",") if e.strip()]
    entry = remember_structured(args.text, type=tag, importance=args.importance or 3,
                                tags=[tag] if tag else [], entities=ents, source="手动")
    if entry is None:
        print("[错误] 记忆内容为空", file=sys.stderr)
        return 1
    print(f"[记忆已写入] {entry['id']}  {MEMORY_JSONL}")
    return 0


# ---------------------------------------------------------------- 演化账本（evolution.json）
# 注意：与主程序 self_evolve（能力自举：create_evolution/self_evolve 四层验证闸）
# 是互补双轨——本账本记录「大脑自身能力的演进史」（合并/快照/迁移等设计变更），
# self_evolve 记录「运行时能力提案」。各自留档，不混写。


def _evolution_path():
    return BRAIN_DIR / "evolution.json"


def _next_evo_id(evo: dict) -> str:
    import re
    mx = 0
    for rec in (evo.get("proposals") or []) + (evo.get("adopted") or []):
        mm = re.match(r"^P-(\d+)$", str(rec.get("id") or ""))
        if mm:
            mx = max(mx, int(mm.group(1)))
    return "P-" + str(mx + 1).zfill(3)


def record_evolution(title, kind="adopted", note=""):
    """追加一条演化账本记录（proposed 提议 / adopted 已采纳）。返回记录 dict 或 None。"""
    title = str(title or "").strip()
    if not title:
        return None
    evo = load_json(_evolution_path(), {}) or {}
    rec = {"id": _next_evo_id(evo), "date": now_iso(), "title": title[:120]}
    if kind == "proposed":
        rec["status"] = "proposed"
        evo.setdefault("proposals", []).append(rec)
    else:
        if note:
            rec["implemented"] = str(note)[:200]
        evo.setdefault("adopted", []).append(rec)
    save_json(_evolution_path(), evo)
    return rec


def cmd_evolution(args) -> int:
    load_manifest()
    if args.evo_cmd == "add":
        rec = record_evolution(args.title, args.kind or "adopted", args.note or "")
        print(f"[演化账本] {rec['id']} 已记录（{args.kind or 'adopted'}）: {rec['title'][:40]}" if rec
              else "[错误] 标题为空")
    else:  # list
        evo = load_json(_evolution_path(), {}) or {}
        props = evo.get("proposals") or []
        adps = evo.get("adopted") or []
        if not props and not adps:
            print("（账本为空）")
        for rec in props:
            print(f"  [提议] {rec.get('id')}  {rec.get('title')}")
        for rec in adps:
            title = rec.get('title') or str(rec.get('implemented') or '—')[:48]
            print(f"  [采纳] {rec.get('id')}  {title}  {rec.get('implemented') or ''}")
    return 0


def cmd_import_memory(args) -> int:
    load_manifest()
    if not MEMORY_SOURCE_DIR.exists():
        print("[跳过] 没有可导入的 .workbuddy/memory 目录。")
        return 0
    imported = 0
    for src in sorted(MEMORY_SOURCE_DIR.glob("*.md")):
        for line in src.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if not s.startswith("- ") and not s.startswith("* "):
                continue
            text = s[2:].strip()
            if not text or text.startswith("#"):
                continue
            entry = remember_structured(text, type="导入", source="导入")
            if entry:
                imported += 1
        print(f"  + 导入 {src.name}")
    print(f"[完成] 共导入 {imported} 条现有记忆到 memory.jsonl。")
    return 0


def _archived_versions():
    """归档快照，按**版本号数字序**升序（修复字典序：v10 会被排到 v2 前，导致
    prune/status 误判最新版）。返回 [] 或 [Path,...]。"""
    if not ARCHIVE_DIR.exists():
        return []
    snaps = list(ARCHIVE_DIR.glob("brain_v*.whale"))
    return sorted(snaps, key=lambda v: int(v.stem.rsplit("_v", 1)[-1]) if v.stem.rsplit("_v", 1)[-1].isdigit() else 10**9)


def _protected_snapshot_versions() -> set:
    """血缘引用的版本号集合：prune 不得删除（否则 merge 的 LCA/双亲会消失）。

    来源：lineage（last_archived / restored_from_version / ancestors）+
    merge_log（每次合并的 a_version / b_version / lca）。"""
    protected = set()
    lin = load_json(LINEAGE_FILE, {})
    for k in ("last_archived", "restored_from_version"):
        v = lin.get(k)
        if v is not None and str(v).lstrip("-").isdigit():
            protected.add(int(v))
    for v in (lin.get("ancestors") or []):
        if str(v).lstrip("-").isdigit():
            protected.add(int(v))
    mlog = load_json(MERGE_LOG_FILE, {"merges": []})
    for mg in (mlog.get("merges") or []):
        if not isinstance(mg, dict):
            continue
        for k in ("a_version", "b_version", "lca"):
            v = mg.get(k)
            if v is not None and str(v).lstrip("-").isdigit():
                protected.add(int(v))
    return protected


def _prune_snapshots(versions: list, keep: int, protected=None):
    """删除 keep 之外过期快照；protected（血缘引用版本）一律豁免。

    返回 (已删除文件名列表, 被豁免的版本号列表)。"""
    protected = protected if protected is not None else _protected_snapshot_versions()
    pruned, skipped = [], []
    doomed = versions[:-keep] if keep > 0 else list(versions)
    for v in doomed:
        try:
            ver = int(v.stem.rsplit("_v", 1)[-1])
        except Exception:
            ver = None
        if ver is not None and ver in protected:
            skipped.append(str(ver))
            continue
        try:
            v.unlink(missing_ok=True)
            pruned.append(v.name)
        except OSError:
            print(f"  !! 无法删除过期快照 {v.name}（跳过）", file=sys.stderr)
    return pruned, skipped


# ---------------------------------------------------------------- B5 快照外置备份
# 让快照脱离大脑目录独立留存：`archive` 后镜像到外部备份目录并维护 inventory；
# 恢复端已天然支持从任意路径读取（cmd_restore src=Path）。异地多一份，历史不断链。


def _snapshot_mirror_dir() -> str:
    """读取默认镜像目录：manifest 里若配置了 archive_mirror 用之，否则空（不镜像）。

    也可经 manifest 顶层 'archive_mirror' 持久化，用户无需每次传 --mirror。
    """
    try:
        m = load_json(BRAIN_DIR / "manifest.json", {})
        val = str(m.get("archive_mirror") or "").strip()
        return val if val else ""
    except Exception:
        return ""


def _mirror_manifest_path(brain_id: str, mirror_dir) -> Path:
    safe_id = str(brain_id or "unknown")[:64]
    return Path(mirror_dir) / safe_id / "snapshot_manifest.json"


def _mirror_snapshot_file(snap_path: Path, mirror_dir, brain_id, data: bytes = None):
    """把单个快照 .whale 复制到 mirror_dir/<brain_id>/ 并刷新 snapshot_manifest.json 清单。

    返回镜像后的完整路径；失败返回 None（不影响本机归档成功）。
    """
    try:
        safe_id = str(brain_id or "unknown")[:64]
        dst_dir = Path(mirror_dir) / safe_id
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / snap_path.name
        # 用已加密+签名的 data 直接写（若未提供则从源读）
        if data is not None:
            dst.write_bytes(data)
        else:
            shutil.copyfile(snap_path, dst)
        # 维护清单（inventory）：brain_id 下所有镜像快照
        man = _mirror_manifest_path(brain_id, mirror_dir)
        inv = load_json(man, {"brain_id": safe_id, "snapshots": []})
        snap_list = inv.get("snapshots") or []
        if not any(s.get("file") == snap_path.name for s in snap_list):
            snap_list.append({
                "file": snap_path.name, "archived_at": now_iso(),
                "size_bytes": dst.stat().st_size,
                "version": snap_path.stem.rsplit("_v", 1)[-1] if "_v" in snap_path.stem else "",
            })
            snap_list.sort(key=lambda s: str(s.get("version") or "0"))
            inv["snapshots"] = snap_list
            save_json(man, inv)
        return str(dst)
    except OSError:
        return None
    except Exception:
        return None


def _record_snapshot_index(version: int, data: bytes) -> None:
    """B4 内容寻址清单：记录每份快照的 sha256/体积/记忆数/相对上一份增量。

    写入 `snapshot_index.json`（大脑内）；不改变 .whale 格式（保持单份可独立恢复）。
    """
    import hashlib as _h
    try:
        idx_path = BRAIN_DIR / "snapshot_index.json"
        idx = load_json(idx_path, {"brain_id": load_manifest().get("brain_id"), "snapshots": []})
        snaps = idx.get("snapshots") or []
        digest = _h.sha256(data).hexdigest()
        # 覆盖/去重同名版本（重复归档同 n 时刷新）
        snaps = [s for s in snaps if str(s.get("version")) != str(version)]
        n_mem = len(load_memories())
        prev = snaps[-1] if snaps else None  # 已按 version 升序
        delta_bytes = 0
        if prev is not None and prev.get("size_bytes") is not None:
            delta_bytes = len(data) - int(prev["size_bytes"])
        snaps.append({
            "version": version, "sha256": digest, "size_bytes": len(data),
            "memories": n_mem, "delta_from_prev_bytes": delta_bytes, "at": now_iso(),
        })
        snaps.sort(key=lambda s: int(s.get("version") or 0))
        idx["snapshots"] = snaps
        save_json(idx_path, idx)
    except Exception:
        pass


def cmd_mirror(args) -> int:
    """把 brain/archive/ 下全部快照镜像到外部目录（B5 补录已存在的快照）。

    usage: mirror <dir>  把现有快照复制到 <dir>/<brain_id>/ 并生成清单。
    """
    load_manifest()
    mirror = getattr(args, "dir", None)
    if not mirror:
        print("[错误] 需要镜像目录（brainkit.py mirror <目录>）", file=sys.stderr)
        return 1
    versions = _archived_versions()
    if not versions:
        print("[空] 没有可镜像的快照。")
        return 0
    m = load_manifest()
    mirrored = 0
    for v in versions:
        if _mirror_snapshot_file(v, mirror, m.get("brain_id")):
            mirrored += 1
    print(f"[镜像完成] 已镜像 {mirrored}/{len(versions)} 份快照到 {Path(mirror) / (m.get('brain_id') or 'unknown')[:64]}")
    return 0


def cmd_archive(args) -> int:
    """L5：快照归档跨进程串行化（防版本号竞态），内层完成实际打包。"""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    if not cross_process_lock(ARCHIVE_DIR / "archive.lock", timeout=15.0):
        print("[错误] 另一归档进行中（archive.lock 被占）或超时。", file=sys.stderr)
        return 1
    try:
        return _archive_unlocked(args)
    finally:
        release_lock(ARCHIVE_DIR / "archive.lock")


def _archive_unlocked(args) -> int:
    m = load_manifest()
    if not verify_fingerprint(m):
        print("!! 警告：指纹校验未通过，快照仍将生成（内容可能被改动过）。", file=sys.stderr)

    versions = _archived_versions()
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
    sig_mode = _write_snapshot_with_sig(target, data)
    if sig_mode == "signed":
        mode += "（已签名）"

    # B5 异地备份钩子：把本次快照镜像到外部目录（快照脱离大脑目录独立留存）。
    mirror = _snapshot_mirror_dir() if not getattr(args, "mirror", None) else str(args.mirror)
    if mirror:
        mirrored = _mirror_snapshot_file(target, mirror, m.get("brain_id"), data)
        if mirrored:
            print(f"  已镜像到外部备份: {mirrored}")

    # B4（安全子集）：内容寻址 + 快照增量清单。快照仍为自包含全量 .whale
    # （每份可独立恢复，不因依赖基快照而牺牲可靠性）；这里在 `snapshot_index.json`
    # 记录每份的 sha256 / 体积 / 记忆条目数 / 相对上一份的增量，让 N 份快照的
    # 成长与去重潜质可见。真正的分块存储会破坏"单份可独立恢复"，故不在此改格式。
    _record_snapshot_index(n, data)

    lineage = load_json(LINEAGE_FILE, {})
    # P2-3：维护完整血缘链——新版本 n 的祖先 = 旧祖先链 + 旧 last_archived
    old_last = lineage.get("last_archived")
    lineage["last_archived"] = n
    anc = list(lineage.get("ancestors") or [])
    if old_last is not None:
        oi = int(old_last)
        if oi not in anc:
            anc.append(oi)
    lineage["ancestors"] = sorted(set(anc), reverse=True)
    save_json(LINEAGE_FILE, lineage)

    keep = args.keep if args.keep is not None else DEFAULT_KEEP
    pruned, skipped = _prune_snapshots(versions, keep)

    print(f"[快照已归档] {target}")
    print(f"  大小: {len(data) / 1024:.1f} KB   加密: {mode}")
    if pruned:
        print(f"  已清理 {len(pruned)} 份过期快照（保留最近 {keep} 份）")
    if skipped:
        print(f"  已豁免 {len(skipped)} 份血缘引用快照（merge 依赖）: " + "、".join(sorted(skipped)))
    return 0


def cmd_restore(args) -> int:
    src = Path(args.whale)
    if not src.exists():
        print(f"[错误] 找不到快照文件: {src}", file=sys.stderr)
        return 1
    data, sig = _read_snapshot_with_sig(src)
    # 签名验证：有签名且验签失败 → 拒绝恢复（防伪造/篡改）
    if sig and not verify_bytes_sig(data, sig):
        print("!! 快照签名验证失败：文件可能被篡改或来源不可信。", file=sys.stderr)
        if not args.force:
            print("!! 拒绝恢复。若确需强恢复，请加 --force（不推荐）。", file=sys.stderr)
            return 2
        print("!! --force 强恢复：签名不匹配但继续。", file=sys.stderr)
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

# jsonl 行级合并的自动取舍计数容器（_merge_file → cmd_merge 收尾记录）
_MERGE_AUTO_CTX = {"jsonl_auto": 0, "jsonl_auto_hi": 0}


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


def _ancestors(meta: dict) -> list:
    """完整祖先版本链（最新在前）。优先血缘图 ancestors（P2-3）；
    老快照无该字段时降级为 [version, parent, restored_from]。"""
    chain = []
    if not meta:
        return chain
    anc = meta.get("ancestors")
    if isinstance(anc, list) and anc:
        try:
            chain = [int(x) for x in anc if isinstance(x, (int, str)) and str(x).lstrip("-").isdigit()]
        except (TypeError, ValueError):
            chain = []
    for k in ("version", "parent", "restored_from"):
        v = meta.get(k)
        if v is not None and str(v).lstrip("-").isdigit():
            vi = int(v)
            if vi not in chain:
                chain.append(vi)
    return chain


def _find_lca(a_meta: dict, b_meta: dict):
    """血缘图真 LCA（P2-3）：两条祖先链的公共版本中「最新」者即最近公共祖先。

    修复原实现的启发式缺陷：① 字符串 max 在版本号 ≥10 时字典序错判
    （"9" > "12"）；② 只取单层 parent/restored_from，无法覆盖多级分叉。
    现按版本号数值比较；新快照携带完整 ancestors 链，分叉历史可精确求解。
    """
    a = _ancestors(a_meta)
    b = set(_ancestors(b_meta))
    common = [v for v in a if v in b]
    return max(common) if common else None


def _read_snapshot_to_dir(path: Path, passphrase: str, workdir: Path) -> Path:
    """把 .whale 解包到 workdir/<hash>，返回目录。

    用路径哈希命名，避免同名快照（如两个 brain_v1.whale）解包到同一目录互相覆盖。
    """
    data, _sig = _read_snapshot_with_sig(path)
    raw = decrypt_whale(data, passphrase)
    zpath = workdir / f"{path.stem}.zip"
    zpath.write_bytes(raw)
    out = workdir / f"{path.stem}-{hashlib.sha256(str(path.resolve()).encode('utf-8')).hexdigest()[:8]}"
    out.mkdir(exist_ok=True)
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


def _jsonl_key(e: dict, raw: str) -> str:
    """记忆行主键：优先 id（分支同源记忆 id 相同才能三方比对）；
    无 id 的裸行退回内容哈希，保证并入不丢。"""
    if isinstance(e, dict) and e.get("id"):
        return "id:" + str(e["id"])[:40]
    return "h:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def _parse_jsonl_lines(text) -> list:
    out = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except Exception:
            continue  # 容忍单行损坏，与 load_memories 一致
        if isinstance(e, dict):
            out.append(e)
    return out


def _row_merge(b, o, t, rel, key):
    """同键三路取舍（jsonl 行级，永不抛冲突）。

    并集哲学 + 字段级融合；唯一语义冲突字段 text 双方都改时取 ts 更新者
    （自动取舍，记入返回值 auto），保证合并永不因记忆格式阻塞。
    高重要度(≥4)双方都改会被计为 auto 并留痕在 merge_log.jsonl_auto_hi
    （见 _merge_jsonl_text），供事后人工核对，避免静默丢弃高价值语义。
    """
    if o == t:
        return o, None
    if o is None:
        return t, None
    if t is None:
        return o, None
    if b and o == b:
        return t, None
    if b and t == b:
        return o, None
    merged, auto = dict(o), None
    for f in set(o) | set(t):
        if f not in o:
            merged[f] = t[f]
            continue
        if f not in t:
            continue  # o 已含
        ov, tv = o[f], t[f]
        if ov == tv:
            continue
        bv = b.get(f) if isinstance(b, dict) else None
        if bv is not None and ov == bv:
            merged[f] = tv
            continue
        if bv is not None and tv == bv:
            continue
        if f == "text":
            # 只覆盖 text 字段并继续处理其余字段（tags/entities/relations/ts 的并集/取新）；
            # 旧实现 `merged = t/o` 整行覆盖，且 `break` 提前退出会让 text 之后/之前的
            # 字段级合并被跳过（set 迭代无序 → 结果不确定）。continue 保证所有字段都合。
            merged["text"] = t.get("text") if _ts_epoch(t.get("ts")) > _ts_epoch(o.get("ts")) else o.get("text")
            auto = f
            continue
        if f in ("tags", "entities", "relations") and isinstance(ov, list) and isinstance(tv, list):
            merged[f] = list(dict.fromkeys(list(ov) + list(tv)))  # 元数据并集
            continue
        if f == "ts":
            merged[f] = max(ov, tv)
            continue
        if f in ("id",):
            continue
        try:  # importance 等数值冲突取较高
            merged[f] = ov if int(ov) >= int(tv) else tv
        except Exception:
            merged[f] = ov
    return merged, auto


def _merge_jsonl_text(base, ours, theirs, strategy):
    """memory.jsonl 行级三路合并。

    修复历史撕裂：jsonl 既非 .md 也非 .json，旧实现落入标量比较，
    两分支各自新增过记忆即整文件冲突。现改为：按主键（记忆 id）三方比对——
    任一分支保留的键并入（记忆不丢）；同键仅一方改动取改动方；
    同键双方都改走字段级融合，text 冲突自动取 ts 新者。
    返回 (合并文本, 自动取舍计数)。"""
    def parse(t):
        return {_jsonl_key(e, json.dumps(e, ensure_ascii=False)): e for e in _parse_jsonl_lines(t)}

    bm, om, tm = parse(base), parse(ours), parse(theirs)
    keys, seen = [], set()
    for src in (bm, om, tm):
        for k in src:
            if k not in seen:
                seen.add(k)
                keys.append(k)
    out_lines, auto_n = [], 0
    for k in keys:
        o, t = om.get(k), tm.get(k)
        if o is None and t is None:
            continue  # 两侧都删除
        # L2 留痕：同键 text 双方都相对 base 改且互不相同、重要度≥4 →
        # 记入高重要度自动取舍计数（真正会 auto-resolve 的语义冲突）
        b0 = bm.get(k) or {}
        o_changed = o is not None and str(o.get("text") or "") != str(b0.get("text") or "")
        t_changed = t is not None and str(t.get("text") or "") != str(b0.get("text") or "")
        if (o_changed and t_changed and o is not None and t is not None
                and o.get("text") != t.get("text")):
            imp = max(int(o.get("importance") or 3), int(t.get("importance") or 3))
            if imp >= 4:
                _MERGE_AUTO_CTX["jsonl_auto_hi"] += 1
        row, auto = _row_merge(bm.get(k), o, t, "", k)
        if auto:
            auto_n += 1
        out_lines.append(json.dumps(row, ensure_ascii=False))
    text = "\n".join(out_lines)
    return (text + "\n") if out_lines else "", auto_n


def _merge_file(rel: str, base_val, ours_val, theirs_val, strategy):
    """三路合并单个文件；记忆 jsonl 走行级智能合并，日志(.md)走行级并集，
    JSON 走字段级，其余走标量。"""
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
    if rel.endswith(".jsonl"):
        text, auto_n = _merge_jsonl_text(base_val, ours_val, theirs_val, strategy)
        if auto_n:
            _MERGE_AUTO_CTX["jsonl_auto"] += auto_n
        return text
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


def cmd_diff(args) -> int:
    """diff <A> <B>：对比两个快照（文件级 + 记忆/思考日志行级）。"""
    load_manifest()
    a_path, b_path = Path(args.snap_a), Path(args.snap_b)
    if not a_path.exists() or not b_path.exists():
        print("[错误] 快照文件不存在", file=sys.stderr)
        return 1
    tmp_root = Path(tempfile.mkdtemp(prefix="whale-diff-"))
    try:
        a_dir = _load_whale_or_none(a_path, args.passphrase or "", tmp_root)
        b_dir = _load_whale_or_none(b_path, args.passphrase or "", tmp_root)
        if a_dir is None or b_dir is None:
            return 1
        rels = set()
        for d in (a_dir, b_dir):
            for p in d.rglob("*"):
                if p.is_file() and p.name != "snapshot_meta.json":
                    rels.add(p.relative_to(d).as_posix())
        changed = same = 0
        print(f"=== 快照对比 {a_path.name} ↔ {b_path.name} ===")
        for rel in sorted(rels):
            pa, pb = a_dir / rel, b_dir / rel
            if not pa.exists():
                print(f"  [+新增] {rel}")
                changed += 1
                continue
            if not pb.exists():
                print(f"  [-删除] {rel}")
                changed += 1
                continue
            va, vb = pa.read_bytes(), pb.read_bytes()
            if va == vb:
                same += 1
                continue
            changed += 1
            print(f"  [~修改] {rel}")
            if ("memories" in rel or "thinking_log" in rel) and rel.endswith(".md"):
                la = set(va.decode("utf-8", "ignore").splitlines())
                lb = set(vb.decode("utf-8", "ignore").splitlines())
                for line in sorted(lb - la)[:8]:
                    print(f"      + {line.strip()[:70]}")
                for line in sorted(la - lb)[:8]:
                    print(f"      - {line.strip()[:70]}")
            elif rel.endswith(".json"):
                try:
                    ja, jb = json.loads(va), json.loads(vb)
                    if isinstance(ja, dict) and isinstance(jb, dict):
                        keys = set(ja) | set(jb)
                        for k in sorted(keys):
                            if ja.get(k) != jb.get(k):
                                print(f"      . {k}: {str(ja.get(k))[:40]} → {str(jb.get(k))[:40]}")
                except Exception:
                    pass
        print(f"  结果: 变化 {changed} 处 · 相同 {same} 处")
        return 0
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


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

        dry_run = bool(getattr(args, "dry_run", False))
        if dry_run:
            out = Path(tempfile.mkdtemp(prefix="whale-dryrun-"))  # 已创建，勿再 mkdir
        else:
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

        if dry_run:
            # F8 预演：只在临时目录完成合并计算，不产出正式合并目录
            shutil.rmtree(out, ignore_errors=True)
            print(f"[预演完成(dry-run)] 无副作用。共同祖先: {'brain_v%d' % lca_v if lca_v is not None else '无'}")
            if conflicts:
                print(f"  将产生 {len(conflicts)} 条待裁决冲突（真实合并会写入 merge_conflicts.json）:")
                for c in conflicts[:20]:
                    print(f"    - {c['file']}  (ours={c['ours'][:24]}… vs theirs={c['theirs'][:24]}…)")
            else:
                print("  无冲突，可直接合并。")
            return 0

        # 收尾：manifest / 血缘续链 / 合并史
        man = load_json(out / "manifest.json", {})
        man["fingerprint"] = compute_fingerprint(man)
        save_json(out / "manifest.json", man)
        # 血缘续链（原为空 {} 导致合并结果断链，无法再作为分支继续演化）：
        # 继承双亲及其祖先版本，合并结果后续 archive 时 snapshot_meta 会携带
        # 完整祖先链，再次分叉/合体可继续以本次双亲为 LCA。
        _merge_anc = set()
        for meta in (a_meta, b_meta):
            for x in (meta.get("ancestors") or []):
                if str(x).lstrip("-").isdigit():
                    _merge_anc.add(int(x))
        for v in (a_meta.get("version"), b_meta.get("version")):
            if v is not None and str(v).lstrip("-").isdigit():
                _merge_anc.add(int(v))
        save_json(out / ".lineage.json", {
            "merged_from": {"a": a_meta.get("version"), "b": b_meta.get("version"),
                            "lca": lca_v, "at": now_iso()},
            "ancestors": sorted(_merge_anc, reverse=True),
        })

        merge_log = load_json(out / "merge_log.json", {"merges": []})
        _jsonl_auto = int(_MERGE_AUTO_CTX.get("jsonl_auto") or 0)
        _jsonl_hi = int(_MERGE_AUTO_CTX.get("jsonl_auto_hi") or 0)
        merge_log["merges"].append({
            "merged_at": now_iso(),
            "a": a_path.name, "a_version": a_meta.get("version"),
            "b": b_path.name, "b_version": b_meta.get("version"),
            "lca": lca_v, "strategy": args.strategy,
            "conflicts": [c["id"] for c in conflicts],
            "jsonl_auto": _jsonl_auto,
            "jsonl_auto_hi": _jsonl_hi,  # L2 留痕：重要度≥4 的语义自动取舍，供事后核对
            "brain_id": man.get("brain_id"),
        })
        save_json(out / "merge_log.json", merge_log)

        evo = load_json(out / "evolution.json", {})
        evo.setdefault("adopted", []).append({
            "id": f"merge-{now_compact()}", "date": now_iso(),
            "implemented": f"分支合并: {a_path.name} × {b_path.name}（LCA={lca_v or '无'}，冲突 {len(conflicts)} 条，记忆行自动取舍 {_jsonl_auto} 条{('，含高价值 ' + str(_jsonl_hi) + ' 条待人工核对') if _jsonl_hi else ''}）",
        })
        save_json(out / "evolution.json", evo)

        if conflicts:
            save_json(out / "merge_conflicts.json", {"conflicts": conflicts})
            print(f"[合并完成] {out}")
            print(f"  冲突 {len(conflicts)} 条待裁决: python brainkit.py merge-resolve <id> --keep ours|theirs|both|custom --dir {out}")
        else:
            print(f"[合并完成] 无冲突。{out}")
        if _jsonl_auto:
            print(f"  记忆行自动取舍 {_jsonl_auto} 条（同 id 双方均改文本时取 ts 更新者）")
        if _jsonl_hi:
            print(f"  ⚠ 其中 {_jsonl_hi} 条为高重要度(≥4)语义取舍，建议事后核对（merge_log.jsonl_auto_hi）")
        print(f"  大脑ID不变: {man.get('brain_id')}  指纹已按合并结果重算")
        return 0
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
        _MERGE_AUTO_CTX["jsonl_auto"] = 0
        _MERGE_AUTO_CTX["jsonl_auto_hi"] = 0


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

    # jsonl 整文件冲突只会来自旧版合并引擎（新版走行级智能合并，不再产生）；
    # 冲突样本已被 _safe_str 截断，整文件覆盖会毁掉记忆库 → 拒绝并给出出路。
    if hit["file"].endswith(".jsonl"):
        print("[拒绝] 该冲突是旧版合并遗留的 jsonl 整文件冲突，无法安全裁决。", file=sys.stderr)
        print("       请人工处理该文件；或删除冲突后用新版（行级合并）重新 merge。", file=sys.stderr)
        return 1

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
    mem_items = load_memories()
    mem_count = len(mem_items)
    think_files = sum(1 for _ in THINKING_DIR.glob("*.md")) if THINKING_DIR.exists() else 0
    versions = _archived_versions()
    conflicts = load_json(MERGE_CONFLICT_FILE, {})
    open_conflicts = len(conflicts.get("conflicts", [])) if conflicts else 0
    sm = load_json(BRAIN_DIR / "self_model.json", {})
    open_decs = sum(1 for d in list_decisions(limit=500) if d.get("status") == "open")

    print("=== 鲸语大脑 · 状态 ===")
    print(f"  大脑ID   : {m['brain_id']}")
    print(f"  指纹     : {'✓ 完好' if ok else '✗ 不匹配（可能被篡改）'}")
    print(f"  人格     : {ident.get('name') or '未命名'} ｜ {ident.get('vessel') or ''}")
    print(f"  密钥     : {'✓ 免密已启用（' + str(m.get('pubkey_fingerprint', '?')) + '）' if _keyring_ready() else '未启用（keyring-setup）'}")
    print(f"  记忆条目 : {mem_count} 份（memories/）")
    print(f"  思考日志 : {think_files} 天（thinking_log/）")
    print(f"  自我模型 : {sm.get('source') or 'template'} 源（校准于 {str(sm.get('calibrated_at') or '—')[:16]}）")
    print(f"  心跳     : 上次挂载 {hb.get('last_mount') or '从未'}")
    print(f"            上次卸载 {hb.get('last_unmount') or '从未'}")
    print(f"  断点     : {hb.get('resume_hint') or '无'}")
    if open_conflicts:
        print(f"  待裁决   : {open_conflicts} 条冲突（merge-resolve）")
    if open_decs:
        print(f"  待回执   : {open_decs} 条决策（decision resolve）")
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
    versions = _archived_versions()
    if not versions:
        print("[空] 还没有快照。运行 python brainkit.py archive 生成第一份。")
        return 0
    for v in versions:
        size = v.stat().st_size / 1024
        print(f"{v.name}  {size:.1f} KB  {_dt.datetime.fromtimestamp(v.stat().st_mtime).strftime('%Y-%m-%d %H:%M')}")
    return 0


# ---------------------------------------------------------------- 高级能力（doctor/identity史/图谱/借贷/审计）
# F6 大脑体检 / F7 记忆图谱多跳 / F9 身份演化史 / F10 跨大脑记忆借贷 / L6 操作审计


def audit_op(op: str, detail: str = ""):
    """L6 操作审计：统一把关键操作追加到 brain_ops.log（防篡改留痕、便于回溯）。"""
    try:
        append_line(BRAIN_DIR / "brain_ops.log", json.dumps(
            {"ts": now_iso(), "op": str(op)[:40], "detail": str(detail or "")[:200]},
            ensure_ascii=False))
    except Exception:
        pass


def _brain_health_dict():
    """F6 体检计算（结构化）→ dict：score/问题清单/各项计数，供 CLI 与 API 复用。"""
    load_manifest()
    problems, score = [], 100
    now_epoch = _dt.datetime.now().timestamp()
    items = load_memories()
    stale = [e for e in items if (now_epoch - _ts_epoch(e.get("ts"))) / 86400.0 > 120
             and int(e.get("importance") or 3) <= 2]
    dups = 0
    active = [e for e in items if not e.get("archived")]
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            if str(active[i].get("type")) != str(active[j].get("type")):
                continue
            ta, tb = set(_mem_tokens(active[i]["text"])), set(_mem_tokens(active[j]["text"]))
            if ta and tb and len(ta & tb) / len(ta | tb) > 0.7:
                dups += 1
    open_decs = [d for d in list_decisions(limit=500) if d.get("status") == "open"]
    stale_decs = [d for d in open_decs if (now_epoch - _ts_epoch(d.get("ts"))) / 86400.0 > 7]
    conflicts = load_json(MERGE_CONFLICT_FILE, {})
    open_conflicts = len(conflicts.get("conflicts", [])) if conflicts else 0
    versions = _archived_versions()
    snap_days = None
    if versions:
        snap_days = (now_epoch - versions[-1].stat().st_mtime) / 86400.0
    keyring = _keyring_ready()
    if stale:
        score -= min(15, len(stale) * 3)
        problems.append(f"陈旧低价值记忆 {len(stale)} 条（>120 天且重要度<=2）")
    if dups:
        score -= min(10, dups)
        problems.append(f"疑似重复记忆 {dups} 组")
    if stale_decs:
        score -= min(10, len(stale_decs) * 2)
        problems.append(f"超期未回执决策 {len(stale_decs)} 条（>7 天，decision resolve）")
    if open_conflicts:
        score -= 15
        problems.append(f"待裁决冲突 {open_conflicts} 条")
    if snap_days is not None and snap_days > 10:
        score -= 5
        problems.append(f"快照较旧（{snap_days:.0f} 天前）")
    elif not versions:
        score -= 5
        problems.append("尚无快照（建议 archive）")
    if not keyring:
        score -= 5
        problems.append("未启用免密密钥（keyring-setup）")
    score = max(0, score)
    return {"score": score, "problems": problems,
            "memories": len(items), "stale": len(stale), "dups": dups,
            "open_decisions": len(open_decs), "stale_decisions": len(stale_decs),
            "open_conflicts": open_conflicts, "snapshots": len(versions),
            "snapshot_days": snap_days, "keyring": keyring}


def cmd_doctor(args) -> int:
    """F6 大脑体检：健康度 + 问题清单；--fix 自动归档陈旧低价值记忆。"""
    h = _brain_health_dict()
    print("=== 大脑体检 ===")
    print(f"  健康度: {h['score']}/100")
    print(f"  记忆 {h['memories']} · 陈旧 {h['stale']} · 疑似重复 {h['dups']} · 未回执决策 {h['open_decisions']} · 快照 {h['snapshots']}")
    if h["problems"]:
        print("  问题清单:")
        for pr in h["problems"]:
            print("    - " + pr)
    else:
        print("  状态良好，无需处理。")
    if args.fix and h["stale"]:
        all_items = load_memories(include_archived=True)
        # 精确取陈旧记忆 id（与 _brain_health_dict 同口径）
        _now_epoch = _dt.datetime.now().timestamp()
        _si = {e["id"] for e in load_memories()
               if (_now_epoch - _ts_epoch(e.get("ts"))) / 86400.0 > 120
               and int(e.get("importance") or 3) <= 2}
        archived_n = 0
        for it in all_items:
            if it["id"] in _si and not it.get("archived"):
                it["archived"] = True
                archived_n += 1
        if archived_n:
            save_memories(all_items)
            audit_op("doctor-fix", f"归档陈旧记忆 {archived_n} 条")
            print(f"  已修复: 归档陈旧记忆 {archived_n} 条")
        else:
            print("  无自动可修复项。")
    elif args.fix:
        print("  无自动可修复项。")
    return 0


def cmd_identity_history(args) -> int:
    """F9 身份演化史：读取 identity_history.json（每次改 identity 追加快照），list 展示版本。"""
    hist = load_json(BRAIN_DIR / "identity_history.json", {"versions": []})
    versions = hist.get("versions") or []
    if args.identity_cmd == "record":
        ident = load_json(BRAIN_DIR / "identity.json", {})
        versions.append({"ts": now_iso(), "identity": ident})
        # 只留最近 50 版
        if len(versions) > 50:
            versions = versions[-50:]
        save_json(BRAIN_DIR / "identity_history.json", {"versions": versions})
        print(f"[身份已留痕] 当前第 {len(versions)} 版（共保留最近 {len(versions)} 版）")
    else:  # list
        if not versions:
            print("（尚无身份版本历史）")
        for v in versions[-20:]:
            nm = (v.get("identity") or {}).get("name") or "未命名"
            upd = (v.get("identity") or {}).get("vibe") or ""
            print(f"  {str(v.get('ts') or '')[:16]}  {nm}  {upd}")
    return 0


def query_graph_multi_hop(entity, hops=1, max_items=20):
    """F7 记忆图谱多跳查询：从指定实体出发，沿 entities/relations 逐跳扩散。

    只支持 ≤2 跳，防止关系图爆炸；命中记忆按重要度+深度衰减排序返回。
    """
    entity = str(entity or "").strip()
    if not entity:
        return []
    items = load_memories()
    ents = {e["id"]: set(str(x) for x in (e.get("entities") or [])) for e in items}
    # 一层：直接含 entity 的记忆
    direct = [e for e in items if not e.get("archived") and entity in ents.get(e["id"], set())
              or (entity in str(e.get("text") or ""))]
    results = []
    seen = set()
    for e in direct:
        results.append(e)
        seen.add(e["id"])
    if hops >= 2:
        # 二跳：与直接命中记忆「共享实体」（图闭包）的记忆 —— 覆盖
        # 张三(m1·实体项目A) → 李四(m2·实体项目A) 这类通过共同实体的关联
        seed_ents = set()
        for e in direct:
            seed_ents |= ents.get(e["id"], set())
        hop2 = [e for e in items if not e.get("archived") and e["id"] not in seen
                and (ents.get(e["id"], set()) & seed_ents)]
        for e in hop2:
            results.append(e)
            seen.add(e["id"])
    results.sort(key=lambda e: (-int(e.get("importance") or 3), _ts_epoch(e.get("ts"))))
    return results[:max_items]


def cmd_graph(args) -> int:
    load_manifest()
    res = query_graph_multi_hop(args.entity, hops=int(getattr(args, "hops", 1) or 1))
    if not res:
        print(f"[图谱] 未找到与「{args.entity}」相关的记忆。")
        return 0
    print(f"[图谱] 与「{args.entity}」相关（≤{getattr(args, 'hops', 1)} 跳）{len(res)} 条:")
    for e in res:
        ent = "、".join(str(x) for x in (e.get("entities") or [])[:3])
        print(f"  - [{e.get('type')}·{e.get('importance')}] {str(e.get('text') or '')[:60]}" + (f"  实体:{ent}" if ent else ""))
    return 0


def cmd_borrow(args) -> int:
    """F10 跨大脑记忆借贷：从另一大脑目录导入含关键词的记忆，记录来源 source=借贷。"""
    src = Path(args.src_dir)
    if not (src / "manifest.json").exists():
        print(f"[错误] {args.src_dir} 不是有效大脑目录", file=sys.stderr)
        return 1
    kw = str(args.keyword or "").strip()
    if not kw:
        print("[错误] --keyword 必填（要借贷的记忆关键词）", file=sys.stderr)
        return 1
    # 直接读源大脑文件解析（不切全局 BRAIN_DIR，避免副作用）；secret 记忆不外借
    src_mem = src / "memories" / "memory.jsonl"
    items = []
    if src_mem.exists():
        for line in src_mem.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except Exception:
                continue
    kwl = kw.lower()
    imported = 0
    for e in items:
        if e.get("archived") or str(e.get("sensitivity") or "public") == "secret":
            continue
        if kwl in str(e.get("text") or "").lower() or kwl in " ".join(str(x) for x in (e.get("entities") or [])).lower():
            new = remember_structured(
                str(e.get("text") or "")[:400], type=str(e.get("type") or "借贷")[:20],
                importance=int(e.get("importance") or 3),
                tags=[str(t)[:20] for t in (e.get("tags") or [])][:5],
                entities=[str(x)[:30] for x in (e.get("entities") or [])][:10],
                source="借贷", sensitivity="public")
            if new:
                imported += 1
    if imported:
        audit_op("borrow", f"从 {src.name} 导入 {imported} 条记忆（kw={kw}）")
    print(f"[借贷完成] 从 {src.name} 导入 {imported} 条记忆（已脱敏为 public，关键词: {kw}）")
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

    sp = sub.add_parser("remember", help="写入一条长期记忆（结构化：类型/重要度/实体）")
    sp.add_argument("text", help="记忆内容")
    sp.add_argument("--tag", help="可选标签/类型，如 工作/生活/约定")
    sp.add_argument("--type", help="记忆类型（偏好/事实/项目/联系/规则 等）")
    sp.add_argument("--importance", type=int, default=3, help="重要度 1-5（默认 3）")
    sp.add_argument("--entities", help="涉及实体，逗号分隔（知识图谱节点）")
    sp.set_defaults(func=cmd_remember)

    sp = sub.add_parser("import-memory", help="导入 .workbuddy/memory 的现有记忆")
    sp.set_defaults(func=cmd_import_memory)

    sp = sub.add_parser("consolidate", help="睡眠巩固：归档低价值旧记忆 + 合并相似记忆")
    sp.add_argument("--min-importance", type=int, default=2, help="低于此重要度且超过 --days 天的记忆归档")
    sp.add_argument("--days", type=int, default=30, help="多少天前的低重要度记忆归档")
    sp.set_defaults(func=cmd_consolidate)

    sp = sub.add_parser("goal", help="目标管理：add / list / update / delete")
    sp.add_argument("goal_cmd", choices=["add", "list", "update", "delete"])
    sp.add_argument("title", nargs="?", help="add: 目标标题")
    sp.add_argument("--note", help="目标备注")
    sp.add_argument("--id", help="update/delete: 目标 id")
    sp.add_argument("--status", choices=["active", "done", "archived"], help="update: 新状态")
    sp.add_argument("--progress", help="update: 进度说明")
    sp.set_defaults(func=cmd_goal)

    sp = sub.add_parser("decision", help="决策日志：add / list / resolve")
    sp.add_argument("dec_cmd", choices=["add", "list", "resolve"])
    sp.add_argument("decision", nargs="?", help="add: 决策内容")
    sp.add_argument("--reason", help="add: 决策理由")
    sp.add_argument("--expected", help="add: 预期结果")
    sp.add_argument("--limit", type=int, default=20, help="list: 条数")
    sp.add_argument("--id", help="resolve: 决策 id")
    sp.add_argument("--outcome", help="resolve: 实际结果")
    sp.add_argument("--status", choices=["kept", "reversed"], default="kept", help="resolve: 维持/反转")
    sp.set_defaults(func=cmd_decision)

    sp = sub.add_parser("evolution", help="演化账本：add / list（大脑自身能力演进史；与运行时 self_evolve 双轨）")
    sp.add_argument("evo_cmd", choices=["add", "list"])
    sp.add_argument("title", nargs="?", help="add: 提案/变更标题")
    sp.add_argument("--kind", choices=["adopted", "proposed"], default="adopted", help="add: adopted=已采纳（默认）/ proposed=提议中")
    sp.add_argument("--note", help="add: 实现说明（adopted 时）")
    sp.set_defaults(func=cmd_evolution)

    sp = sub.add_parser("doctor", help="F6 大脑体检：健康度 + 问题清单，--fix 自动安全修复")
    sp.add_argument("--fix", action="store_true", help="自动归档陈旧低价值记忆")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("identity-history", help="F9 身份演化史：record（留痕当前人格）/ list")
    sp.add_argument("identity_cmd", choices=["record", "list"], nargs="?", default="list")
    sp.set_defaults(func=cmd_identity_history)

    sp = sub.add_parser("graph", help="F7 记忆图谱：查询与实体相关的记忆（含多跳）")
    sp.add_argument("entity", help="实体名（如 张三 / 项目A）")
    sp.add_argument("--hops", type=int, default=1, help="关系跳数 1-2（默认 1）")
    sp.set_defaults(func=cmd_graph)

    sp = sub.add_parser("borrow", help="F10 跨大脑记忆借贷：从另一大脑目录导入含关键词的记忆")
    sp.add_argument("src_dir", help="源大脑目录（含 manifest.json）")
    sp.add_argument("--keyword", help="要借贷的记忆关键词")
    sp.set_defaults(func=cmd_borrow)

    sp = sub.add_parser("archive", help="生成快照 brain_v{n}.whale（默认免密加密）；--mirror 外置镜像")
    sp.add_argument("--passphrase", help="额外附上口令包裹（fallback 解密路径）")
    sp.add_argument("--keep", type=int, default=DEFAULT_KEEP, help="保留最近 N 份快照")
    sp.add_argument("--mirror", help="B5：把新快照镜像到外部备份目录（也写 snapshot_manifest.json）")
    sp.set_defaults(func=cmd_archive)

    sp = sub.add_parser("mirror", help="B5：把 brain/archive/ 下全部快照镜像到外部目录 <dir>")
    sp.add_argument("dir", help="外部备份目录")
    sp.set_defaults(func=cmd_mirror)

    sp = sub.add_parser("restore", help="从 .whale 快照恢复大脑（本机免密/口令）")
    sp.add_argument("whale", help="快照文件路径")
    sp.add_argument("--passphrase", help="口令（快照无本地密钥时使用）")
    sp.add_argument("--dir", help="恢复到指定新目录（模拟他机/分支演化）")
    sp.add_argument("--replace", action="store_true", help="覆盖当前大脑（旧大脑自动备份为 brain.bak-时间戳）")
    sp.add_argument("--force", action="store_true", help="指纹不匹配时仍恢复")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("merge", help="合并两个分支快照（血缘三路合并 + 冲突裁决）；--dry-run 预演不产出")
    sp.add_argument("snap_a", help="主干快照 .whale")
    sp.add_argument("snap_b", help="分支快照 .whale")
    sp.add_argument("--strategy", choices=["auto", "ours", "theirs"], default="auto", help="冲突默认策略")
    sp.add_argument("--dir", help="合并结果输出目录（默认 brain_merged-时间戳）")
    sp.add_argument("--dry-run", action="store_true", help="F8 预演：仅报告冲突，不生成合并目录")
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

    sp = sub.add_parser("diff", help="对比两个快照（文件级 + 记忆/日志行级）")
    sp.add_argument("snap_a", help="快照 A .whale")
    sp.add_argument("snap_b", help="快照 B .whale")
    sp.add_argument("--passphrase", help="快照口令（若快照无本地密钥）")
    sp.set_defaults(func=cmd_diff)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "brain_dir", None):
        set_brain_dir(Path(args.brain_dir))
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
