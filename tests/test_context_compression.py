"""上下文压缩 v3.16.4：结构化摘要 + 最近原文 + 长结果外置 + 滚动更新。

对照用户要求：
- 结构化摘要（任务目标/约束/进度/关键线索/待办/原始指令）
- 保留最近若干轮**原文**（min_kept_turns）
- 更早内容归档外置，摘要里附归档路径（可回查）
- 原始任务永不丢（摘要之外再钉一条原文）
- 摘要按会话滚动更新（不重复摘要）
"""
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import api_server  # noqa: E402

SUMMARY_TEXT = (
    "## 任务目标\n完成《三更帖》第三首 MV\n"
    "## 硬性约束\n竖屏 1080x1920 / 30fps\n"
    "## 当前进度\n已完成元素修复，待渲染全片\n"
    "## 关键线索\nD:\\\\work\\\\三更帖\\\\scripts\\\\scenes.py\n"
    "## 待办事项\n渲染 5938 帧\n"
    "## 不可丢失的原文\n全片推进\n"
    "## 关键事实\n- 已修 radial_glow"
)


class _FakeClient:
    """记录每次摘要请求内容，并回吐结构化摘要。"""

    def __init__(self, text=SUMMARY_TEXT, boom=False):
        self.calls = []
        self.text = text
        self.boom = boom

    def chat(self, messages, **kw):
        self.calls.append(messages)
        if self.boom:
            raise RuntimeError("no llm")
        cb = kw.get("on_content")
        if cb:
            cb(self.text)
        return None


def _turns(n, size=2000):
    msgs = []
    for i in range(n):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": "a" * size})
    return msgs


def _cfg(**over):
    cfg = {"max_context_tokens": 10 ** 9, "max_context_chars": 8000,
           "min_kept_turns": 2, "privacy_mode": False}
    cfg.update(over)
    return cfg


def test_structured_summary_pinned_and_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "ARCHIVES_DIR", str(tmp_path / "arch"))
    monkeypatch.setattr(api_server, "SUMMARIES_DIR", str(tmp_path / "sum"))
    client = _FakeClient()
    msgs = [{"role": "system", "content": "BASE"}] + _turns(6)

    out, info = api_server._compress_messages(msgs, _cfg(), client, session_id="struct1")
    assert info and info["mode"] == "summary", info
    assert info["rolling"] is True
    # 基础提示词仍在最前
    assert out[0] == {"role": "system", "content": "BASE"}
    # 原始任务锚点 + 结构化摘要都在（都是 system）
    joined = "\n".join(str(m.get("content") or "") for m in out if m.get("role") == "system")
    assert "[原始任务·不可裁剪]" in joined and "u0" in joined
    assert "[历史对话摘要]" in joined and "## 任务目标" in joined
    # 归档路径写进摘要（长内容外置可回查）
    assert info["archived_path"] and info["archived_path"] in joined
    # 最近 2 轮原文保留
    assert out[-1]["content"] == "a" * 2000
    # 摘要提示词是结构化的
    prompt = client.calls[0][0]["content"]
    for sec in ("## 任务目标", "## 硬性约束", "## 当前进度", "## 关键线索", "## 待办事项", "## 不可丢失的原文"):
        assert sec in prompt
    # 滚动摘要已落盘
    p = Path(api_server._summary_path("struct1"))
    assert p.is_file()
    assert "## 任务目标" in json.loads(p.read_text(encoding="utf-8"))["text"]


def test_rolling_summary_reused_without_new_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "ARCHIVES_DIR", str(tmp_path / "arch"))
    monkeypatch.setattr(api_server, "SUMMARIES_DIR", str(tmp_path / "sum"))
    monkeypatch.setattr(api_server, "_SUMMARY_MEM", {})
    client = _FakeClient()
    msgs = [{"role": "system", "content": "BASE"}] + _turns(6)

    api_server._compress_messages(msgs, _cfg(), client, session_id="roll1")
    n1 = len(client.calls)
    out2, info2 = api_server._compress_messages(msgs, _cfg(), client, session_id="roll1")
    assert len(client.calls) == n1, "无新增被移除轮次时应复用摘要，不再调 LLM"
    assert info2["summary_reused"] is True
    joined2 = "\n".join(str(m.get("content")) for m in out2)
    assert "[历史对话摘要]" in joined2
    # 复用摘要时也沿用上次的归档路径（长结果外置引用不丢）
    assert "[更早的完整历史已归档" in joined2


def test_rolling_summary_incremental_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "ARCHIVES_DIR", str(tmp_path / "arch"))
    monkeypatch.setattr(api_server, "SUMMARIES_DIR", str(tmp_path / "sum"))
    monkeypatch.setattr(api_server, "_SUMMARY_MEM", {})
    client = _FakeClient()
    api_server._compress_messages([{"role": "system", "content": "B"}] + _turns(6), _cfg(), client, session_id="roll2")
    n1 = len(client.calls)
    # 更多轮次 → 有新轮次脱离上下文 → 增量归并，提示词带上「已有摘要」
    out, info = api_server._compress_messages(
        [{"role": "system", "content": "B"}] + _turns(9), _cfg(), client, session_id="roll2")
    assert len(client.calls) == n1 + 1
    assert "已有摘要" in client.calls[-1][0]["content"]
    assert info["mode"] == "summary"


def test_dialog_mode_uses_dialog_summary(tmp_path, monkeypatch):
    """对话模式用对话纪要提示词（不套任务/待办框架），同样结构化 + 锚点。"""
    monkeypatch.setattr(api_server, "ARCHIVES_DIR", str(tmp_path / "arch"))
    monkeypatch.setattr(api_server, "SUMMARIES_DIR", str(tmp_path / "sum"))
    monkeypatch.setattr(api_server, "_SUMMARY_MEM", {})
    dlg_text = ("## 主题与背景\n在聊天气\n## 已确认的事实与结论\n无\n"
                "## 用户偏好与要求\n无\n## 未决问题与待答\n无\n"
                "## 不可丢失的原文\n随便聊聊\n## 关键事实\n- x")
    client = _FakeClient(text=dlg_text)
    out, info = api_server._compress_messages(
        [{"role": "system", "content": "B"}] + _turns(6), _cfg(), client,
        session_id="dlg1", pure_chat=True)
    assert info["mode"] == "summary"
    prompt = client.calls[0][0]["content"]
    assert "## 主题与背景" in prompt and "## 用户偏好与要求" in prompt
    assert "## 待办事项" not in prompt and "## 任务目标" not in prompt
    joined = "\n".join(str(m.get("content") or "") for m in out)
    assert "[历史对话摘要]" in joined and "## 主题与背景" in joined
    assert "[原始任务·不可裁剪]" in joined


def test_pinned_task_survives_summary_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "ARCHIVES_DIR", str(tmp_path / "arch"))
    monkeypatch.setattr(api_server, "SUMMARIES_DIR", str(tmp_path / "sum"))
    monkeypatch.setattr(api_server, "_SUMMARY_MEM", {})
    client = _FakeClient(boom=True)
    out, info = api_server._compress_messages(
        [{"role": "system", "content": "BASE"}] + _turns(6), _cfg(), client, session_id="fail1")
    assert info["mode"] == "trim"
    joined = "\n".join(str(m.get("content") or "") for m in out)
    assert "BASE" in joined
    assert "[原始任务·不可裁剪]" in joined and "u0" in joined, "摘要失败也必须保留原始任务"
