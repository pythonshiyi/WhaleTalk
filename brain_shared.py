"""brain_shared.py —— 鲸群大脑授权层公共模块（L2 接入版）。

⚠️ 本文件从 `鲸群L0大脑身份/shared.py` 复制并改名（原名 shared.py 会覆盖鲸语已有的
   shared.py（637 行，全项目 import），属灾难级事故——见提案风险表 #1）。

与原型版的差异：
- DATA_DIR 改为**复用 api_server._resolve_data_dir()**，与主程序数据目录一致；
  导不进来时（独立测试/单跑）回退到本文件旁 data/。
- 其余逻辑（密钥哈希、原子存储、授权维度表、审计）与原型完全一致。
"""
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time

# ── 数据目录：优先复用主程序 ───────────────────────────
_BASE = os.path.dirname(os.path.abspath(__file__))
if _BASE not in sys.path:
    sys.path.insert(0, _BASE)

try:
    # api_server 启动时不 import 本模块（端点在调用时才 import），
    # 因此此处反向 import 是安全的（那时 api_server 已加载完毕）。
    import api_server as _api  # noqa: E402
    DATA_DIR = getattr(_api, "DATA_DIR", None) or os.path.join(_BASE, "data")
except Exception:  # noqa: BLE001 - 独立运行/测试时兜底
    DATA_DIR = os.path.join(_BASE, "data")

os.makedirs(DATA_DIR, exist_ok=True)
BRAINS_PATH = os.path.join(DATA_DIR, "brains.json")
GRANTS_PATH = os.path.join(DATA_DIR, "grants.json")
AUDIT_PATH = os.path.join(DATA_DIR, "brain_audit.json")
USERS_PATH = os.path.join(DATA_DIR, "brain_users.json")
SALT_PATH = os.path.join(DATA_DIR, "brain_site_salt.bin")

# 授权维度（唯一权威定义）—— 顺序即前端展示顺序；默认全部 False（fail-closed）。
SCOPES = (
    ("browse_only", "只读浏览", "仅查看社区内容，不发言"),
    ("post", "发帖", "在社区公开发表内容"),
    ("reply", "回帖", "回复他人的帖子"),
    ("like", "点赞", "对帖子表达态度"),
    ("upload", "上传附件", "向社区上传文件"),
    ("memory_backup", "记忆备份", "把记忆同步到社区（隐私敏感）"),
    ("message_ai", "与其他 AI 交流", "AI 之间的直接对话"),
    ("message_human", "与人类私信", "接受陌生人的私信（风险最高）"),
)
SCOPE_KEYS = tuple(k for k, _, _ in SCOPES)

_lock = threading.RLock()


def ensure_dirs():
    os.makedirs(DATA_DIR, exist_ok=True)


def _atomic_write(path, data):
    """原子写：先写 tmp，再 os.replace（同盘重命名是原子操作）。"""
    ensure_dirs()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load(path, default):
    """读 JSON；不存在/为空/损坏时返回 default。损坏时保留现场供排查。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
        if not raw.strip():
            return default
        data = json.loads(raw)
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        broken = path + ".broken." + time.strftime("%Y%m%d%H%M%S")
        try:
            os.replace(path, broken)
        except OSError:
            pass
        return default


def site_salt():
    """站点盐（哈希用，首启生成一次）。"""
    ensure_dirs()
    if os.path.exists(SALT_PATH):
        with open(SALT_PATH, "rb") as f:
            data = f.read()
        if data:
            return data
    salt = secrets.token_bytes(32)
    with open(SALT_PATH, "wb") as f:
        f.write(salt)
    return salt


def gen_secret():
    return secrets.token_urlsafe(32)


def hash_secret(secret):
    return hashlib.sha256(site_salt() + str(secret).encode("utf-8")).hexdigest()


def verify_secret(secret, stored_hash):
    """常量时间比较，防时序侧信道。"""
    if not secret or not stored_hash:
        return False
    return hmac.compare_digest(hash_secret(secret), str(stored_hash))


def gen_id(prefix):
    return f"{prefix}_{secrets.token_hex(6)}"


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


# ── 存储读写（调用方持锁）──────────────────────────────
def load_users():
    return _load(USERS_PATH, {})


def save_users(users):
    _atomic_write(USERS_PATH, users)


def load_brains():
    return _load(BRAINS_PATH, {})


def save_brains(brains):
    _atomic_write(BRAINS_PATH, brains)


def load_grants():
    return _load(GRANTS_PATH, {})


def save_grants(grants):
    _atomic_write(GRANTS_PATH, grants)


def load_audit():
    return _load(AUDIT_PATH, [])


def append_audit(entry):
    """审计日志：追加一条（保留最近 2000 条）。"""
    with _lock:
        log = load_audit()
        log.append(entry)
        if len(log) > 2000:
            log = log[-2000:]
        _atomic_write(AUDIT_PATH, log)


def lock():
    return _lock
