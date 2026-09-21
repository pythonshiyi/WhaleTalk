"""Profile 多账号配置读写（API Key DPAPI 加密）+ 网关凭据记忆。

从 main.py 中拆出，供 Profile 管理对话框与客户端切换复用。

两份数据，均落在数据目录、api_key 一律 DPAPI 加密：
- ``profiles.json``：用户显式保存的「配置方案」（name → api_key/base_url/model）。
- ``gateway_keys.json``：按**网关地址**记住的凭据（url → api_key/model），
  用于「切换网关不必重填 Key」——每次保存配置或应用方案时自动记忆，
  切换到已记住的网关时自动回填。

读文件失败（存在但无法解析）不再静默返回空表——否则下一次保存会
以空表为底覆盖磁盘，造成「方案全部消失」。此时抛 ``ProfileReadError``，
调用方应中止写入；本模块会先尝试从 ``.bak`` 备份恢复。
"""
import json
import logging
import os

import crypto

logger = logging.getLogger("whaletalk.profiles")

DEFAULT_PROFILES_PATH = None
DEFAULT_GATEWAYS_PATH = None


class ProfileReadError(Exception):
    """Profile/网关文件存在但无法解析（区别于「文件不存在」的空表）。"""


def _norm_url(url):
    """网关地址归一（仅用于做记忆键；与 config_utils 的规范化口径一致）。"""
    u = str(url or "").strip()
    if not u:
        return ""
    try:
        import deepseek_client as _dc
        return _dc.normalize_base_url(u) or u
    except Exception:
        return u.rstrip("/")


def _load_json(path):
    """读取 JSON：文件不存在/为空返回 None；存在但无法解析抛 ProfileReadError。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        raise ProfileReadError(f"读取失败：{path}（{e}）") from e
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except Exception as e:
        raise ProfileReadError(f"文件损坏，无法解析：{path}（{e}）") from e


def _try_bak(path):
    """主文件损坏时尝试读回 .bak 备份；返回解析结果或 None。"""
    bak = (path or "") + ".bak"
    if not os.path.exists(bak):
        return None
    try:
        with open(bak, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.exception("Profile .bak 备份也无法解析：%s", bak)
        return None


def _atomic_write(path, data, backup=True):
    """原子写（tmp → os.replace）；backup=True 时先保留上一版为 .bak。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    if backup and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                prev = f.read()
            with open(path + ".bak", "w", encoding="utf-8") as f:
                f.write(prev)
        except OSError:
            logger.warning("写 Profile 备份失败（可降级）：%s", path + ".bak", exc_info=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ── 配置方案（profiles.json）──────────────────────────

def load_profiles(path=None):
    """读取 Profile 列表，返回 {"profiles": {name: cfg}, "current": name}。

    密文为 "dpapi:" 前缀（crypto.decrypt）；旧版明文自动兼容（无前缀原样返回）。
    文件不存在/为空 → 空表；文件损坏 → 先试 .bak，再失败抛 ProfileReadError。
    """
    if path is None:
        path = DEFAULT_PROFILES_PATH
    try:
        data = _load_json(path)
    except ProfileReadError:
        # 主文件损坏：先尝试从 .bak 备份恢复，仍失败才向上抛（调用方会中止写入）
        logger.exception("Profile 主文件损坏，尝试从 .bak 恢复：%s", path)
        data = _try_bak(path)
        if data is None:
            raise
        _atomic_write(path, data, backup=False)  # 用备份修回主文件（不覆盖好备份）
    if data is None:
        return {"profiles": {}, "current": ""}
    if not isinstance(data, dict):
        data = _try_bak(path)
        if not isinstance(data, dict):
            raise ProfileReadError(f"Profile 数据格式非法：{path}")
    try:
        profiles = data.get("profiles")
        if not isinstance(profiles, dict):
            profiles = {}
        cleaned = {}
        for name, p in profiles.items():
            if isinstance(p, dict) and name:
                cleaned[str(name)] = {
                    "api_key": crypto.decrypt(str(p.get("api_key", "") or "")),
                    "base_url": str(p.get("base_url", "") or ""),
                    "model": str(p.get("model", "") or ""),
                }
        return {"profiles": cleaned, "current": str(data.get("current") or "")}
    except ProfileReadError:
        raise
    except Exception as e:
        raise ProfileReadError(f"解析 Profile 失败：{path}（{e}）") from e


def save_profiles(data, path=None):
    """保存 Profile 列表；api_key 一律经 DPAPI 加密，绝不明文落盘。

    加密失败（CryptError）时整次保存失败（fail-closed），磁盘保持旧文件。
    覆盖前自动把旧文件留一份 `.bak`（可回滚）。
    """
    if path is None:
        path = DEFAULT_PROFILES_PATH
    try:
        encrypted = {}
        for name, p in (data.get("profiles") or {}).items():
            encrypted[str(name)] = {
                "api_key": crypto.encrypt(str(p.get("api_key", "") or "")),
                "base_url": str(p.get("base_url", "") or ""),
                "model": str(p.get("model", "") or ""),
            }
        out = {"profiles": encrypted, "current": str(data.get("current") or "")}
        _atomic_write(path, out)
        return True
    except Exception:
        logger.exception("保存 Profile 失败（api_key 未落盘）")
        return False


# ── 网关凭据记忆（gateway_keys.json）────────────────

def load_gateway_keys(path=None):
    """读取按网关地址记住的凭据：{url: {"api_key", "model"}}（api_key 已解密）。

    文件损坏 → 尽力返回空表（网关记忆是便利功能，不阻断主流程），但留痕。
    """
    if path is None:
        path = DEFAULT_GATEWAYS_PATH
    try:
        data = _load_json(path)
    except ProfileReadError:
        logger.exception("网关凭据文件损坏，已按空表处理：%s", path)
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("gateways")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for url, g in raw.items():
        if isinstance(g, dict) and url:
            out[_norm_url(url)] = {
                "api_key": crypto.decrypt(str(g.get("api_key", "") or "")),
                "model": str(g.get("model", "") or ""),
            }
    return out


def save_gateway_keys(gateways, path=None):
    """保存网关凭据表；失败返回 False（不阻断主流程）。"""
    if path is None:
        path = DEFAULT_GATEWAYS_PATH
    try:
        encrypted = {}
        for url, g in (gateways or {}).items():
            u = _norm_url(url)
            if not u or not isinstance(g, dict):
                continue
            encrypted[u] = {
                "api_key": crypto.encrypt(str(g.get("api_key", "") or "")),
                "model": str(g.get("model", "") or ""),
            }
        _atomic_write(path, {"gateways": encrypted})
        return True
    except Exception:
        logger.exception("保存网关凭据失败（可降级）")
        return False


def remember_gateway_key(base_url, api_key, model="", path=None):
    """把某网关的 Key/模型记入记忆表（api_key 为空则不记）。"""
    u = _norm_url(base_url)
    if not u or not str(api_key or "").strip():
        return False
    keys = load_gateway_keys(path)
    keys[u] = {"api_key": str(api_key).strip(), "model": str(model or "").strip()}
    return save_gateway_keys(keys, path)


def gateway_key_for(base_url, path=None):
    """取某网关已记住的凭据（无则返回 None）。"""
    u = _norm_url(base_url)
    if not u:
        return None
    g = load_gateway_keys(path).get(u)
    if isinstance(g, dict) and str(g.get("api_key") or "").strip():
        return g
    return None
