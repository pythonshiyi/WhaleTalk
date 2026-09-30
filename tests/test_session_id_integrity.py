"""会话 id 完整性回归：非法/易碰撞 id 必须被**拒绝**，而不是静默改写。

背景（修复的数据丢失 bug）：
`_safe_sid` 原实现是 `re.sub(r"[^0-9a-zA-Z_-]", "", sid)[:64]`——把非法字符
**剥掉**。于是不同客户端 id 会塌缩到同一个会话文件：

    "sess.1"      -> "sess1"        ┐ 同一个 sess1.json
    "sess 1"      -> "sess1"        ┘
    "my session"  -> "mysession"    ┐ 同一个 mysession.json
    "mysession"   -> "mysession"    ┘

后果是**跨会话覆盖**（B 存盘覆盖 A 的历史）与**跨会话删除**（删 B 连带删掉 A）。
`[:64]` 截断让「前 64 位相同」的任意两个 id 也碰撞。

正确契约：`_safe_sid` 合法返回原 id、非法返回 None（与 `_valid_name` 同一约定），
调用方遇到 None 必须回错，绝不退化成默认 id 或剥字符后落盘。
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import api_server  # noqa: E402


def _stub_index(monkeypatch):
    monkeypatch.setattr(api_server, "_index_session_locked", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_save_session_index", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "_index_session_file", lambda *a, **k: None)


def _handler():
    class H(api_server._Handler):
        def __init__(self):
            pass

    return H()


# ── 契约：合法保留 / 非法拒绝 ─────────────────────────────

def test_safe_sid_keeps_legal_ids_unchanged():
    for good in ("abc", "ABC", "a-b_c", "sess20240101", "x" * 64, "0", "_"):
        assert api_server._Handler._safe_sid(None, good) == good, good


def test_safe_sid_rejects_instead_of_stripping():
    """非法 id 返回 None——绝不返回「剥字符后的变体」。"""
    for bad in ("sess.1", "sess 1", "my session", "a/b", "a\\b", "a..b",
                "../evil", "x" * 65, "", None, "中文会话", "a\nb", "a\x00b"):
        got = api_server._Handler._safe_sid(None, bad)
        assert got is None, f"{bad!r} 应被拒绝，实际得到 {got!r}"


def test_previously_colliding_ids_no_longer_collide():
    """把历史碰撞对逐一钉死：**至多一个**能落到文件名上，碰撞因此消失。

    历史行为：`"my session"` 被剥成 `"mysession"`，与本就合法的 `"mysession"`
    指向同一个文件。修复后非法者返回 None（不落盘），合法者原样保留，两者不再
    可能指向同一文件。`"sess.1"/"sess 1"` 这类**双方都非法**的组合则双双被拒。
    """
    cases = [
        ("sess.1", "sess 1"),            # 双方非法 → 都拒
        ("my session", "mysession"),     # 一非法一合法 → 非法者拒
        ("a/b", "ab"),                   # 一非法一合法
        ("x" * 70, "x" * 80),            # 双方超长非法
    ]
    for a, b in cases:
        sa = api_server._Handler._safe_sid(None, a)
        sb = api_server._Handler._safe_sid(None, b)
        # 关键不变量：绝不允许出现「两个不同的输入 → 同一个合法文件名」
        assert not (sa is not None and sa == sb), \
            f"{a!r}/{b!r} 仍塌缩到同一文件 {sa!r}"
        # 且至少有一方被拒绝（否则说明剥字符行为回来了）
        assert sa is None or sb is None, f"{a!r}/{b!r} 双方都被接受，剥字符行为回归"


# ── 端到端：跨会话覆盖与跨会话删除必须不再发生 ─────────────

def test_invalid_id_is_rejected_not_silently_remapped(tmp_path, monkeypatch):
    """显式给了非法 id 时 `_save_session` 报错，而不是偷偷换一个新 id 落盘。

    旧行为（剥字符 + 静默新 id）：前端以为存到了自己指定的 id，之后按该 id 读回为空。
    """
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    _stub_index(monkeypatch)
    h = _handler()

    sid, err = h._save_session({"id": "sess.1",
                                "messages": [{"role": "user", "content": "victim"}]})
    assert sid is None and err, "非法 id 必须被拒绝"
    assert not list(tmp_path.glob("*.json")), "被拒绝的保存不得写出任何会话文件"


def test_cross_session_overwrite_is_impossible(tmp_path, monkeypatch):
    """两个「原本会碰撞」的 id：第二个不能再覆盖第一个的历史。"""
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    _stub_index(monkeypatch)
    h = _handler()

    # 合法 id 正常落盘
    sid_a, err_a = h._save_session({"id": "sess1",
                                    "messages": [{"role": "user", "content": "A 的历史"}]})
    assert err_a is None and sid_a == "sess1"

    # 易碰撞的非法 id 被拒绝，不得触碰 sess1.json
    sid_b, err_b = h._save_session({"id": "sess 1",
                                    "messages": [{"role": "user", "content": "B 的内容"}]})
    assert sid_b is None and err_b

    data = json.loads((tmp_path / "sess1.json").read_text(encoding="utf-8"))
    assert data["messages"][0]["content"] == "A 的历史", "A 的历史被 B 覆盖了"


def test_cross_session_delete_is_impossible(tmp_path, monkeypatch):
    """删除一个易碰撞的非法 id，不得删除另一个合法会话。"""
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    _stub_index(monkeypatch)
    h = _handler()

    h._save_session({"id": "sess1",
                     "messages": [{"role": "user", "content": "要保住的历史"}]})
    assert (tmp_path / "sess1.json").exists()

    ok, err = h._delete_session("sess 1")
    assert not ok and err, "非法 id 的删除必须失败"
    assert (tmp_path / "sess1.json").exists(), "合法会话被易碰撞的 id 误删了"


def test_load_session_rejects_invalid_id_without_touching_disk(tmp_path, monkeypatch):
    """非法 id 读取返回 None，且不得去拼 `None.json` 之类的假路径。"""
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    h = _handler()
    assert h._load_session_messages("sess.1") is None
    assert h._load_session_messages("../../etc/passwd") is None
    assert not list(tmp_path.glob("*.json"))


def test_legal_server_generated_id_still_round_trips(tmp_path, monkeypatch):
    """回归保护：正常路径（前端每次生成的 hex id，如 `18f3a2b1c4`）必须照常存取。"""
    monkeypatch.setattr(api_server, "SESSIONS_DIR", str(tmp_path))
    _stub_index(monkeypatch)
    h = _handler()

    # 前端 api.js 生成的 id 形态：hex 时间戳 + 随机后缀
    real_id = "18f3a2b1c4ab7d9e"
    sid, err = h._save_session({"id": real_id,
                                "messages": [{"role": "user", "content": "正常会话"}]})
    assert err is None and sid == real_id
    msgs = h._load_session_messages(real_id)
    assert msgs and msgs["messages"][0]["content"] == "正常会话"
