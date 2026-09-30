"""auto 思考档路由回归（v3.16.17）。

背景（实测诊断）：`_auto_effort` 旧词表只有 10 个书面复杂词 + 9 个寒暄词，
实测把大量**真实任务**误判成 none（=完全关闭思考）：

    "帮我把这个项目的测试跑一遍"        -> none
    "数据库连接池泄漏了帮我看看"        -> none
    "把这份报告转成PPT"                 -> none
    "这个函数为什么这么慢"              -> none
    "给这个功能加上单元测试"            -> none
    （6 个真实任务里 5 个被路由到 none）

而 DEFAULT_CONFIG['thinking'] 又恰好是 "none"（全关），叠加起来就是
「用户装了产品 → 无论问什么都几乎不动脑」——这是「任务执行不满意」的直接原因之一。

修法：加入动作意图词 + 强信号词，并把寒暄判定收紧为「整句都是短寒暄」。
本文件把「真实任务不再漏判」与「寒暄仍然省 token」两个方向都钉死。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import deepseek_client as dc  # noqa: E402


def _effort(text):
    return dc._auto_effort([{"role": "user", "content": text}])


# 真实任务：绝不能被判成 none（那是「不动脑直接答」）
REAL_TASKS = [
    "帮我把这个项目的测试跑一遍",
    "数据库连接池泄漏了帮我看看",
    "把这份报告转成PPT",
    "这个函数为什么这么慢",
    "给这个功能加上单元测试",
    "分析一下这段代码的性能问题",
    "重构一下这个模块",
    "报错了帮我看看",
    "把 xlsx 里的数据整理成图表",
    "写个爬虫抓取新闻",
    "帮我写个 Python 脚本读取 CSV",
    "审查一下这段代码有没有安全问题",
]


def test_real_tasks_are_never_routed_to_none():
    """真实任务必须至少 high——误判成 none 就等于关掉思考。"""
    bad = [t for t in REAL_TASKS if _effort(t) == "none"]
    assert not bad, f"这些真实任务被误判为 none（会关掉思考）：{bad}"


def test_complex_tasks_escalate_to_max():
    """高复杂度（重构/性能分析/架构）应升到 max 档。"""
    assert _effort("分析一下这段代码的性能问题") == "max"
    assert _effort("重构一下这个模块") == "max"


def test_pure_greetings_stay_none():
    """纯寒暄仍应走 none——这是省 token 的正当收益，不能被上面的放宽吃掉。"""
    for g in ("你好", "在吗", "谢谢", "好的", "哈哈", "ok", "再见"):
        assert _effort(g) == "none", f"寒暄 {g!r} 被误升档"


def test_greeting_prefixed_requests_still_escalate():
    """「谢谢，再帮我…」这类寒暄开头 + 真实请求，必须升档。

    回归点：旧版只要句中出现寒暄词就 -1，导致「好的，帮我重构一下」被降档；
    新版要求**所有分句都很短**才算纯寒暄，且命中动作词即不降档。
    """
    assert _effort("好的，帮我重构一下这个模块") != "none"
    assert _effort("谢谢，再帮我把测试跑一下") != "none"
    assert _effort("在吗，帮我改个 bug") != "none"


def test_long_text_escalates():
    """长输入（>300 字）至少 high。"""
    assert _effort("请帮我处理一下：" + "细节" * 200) != "none"


def test_code_block_escalates():
    assert _effort("看看这段代码\n```python\nx = 1\n```") != "none"


def test_multistep_escalates():
    text = "步骤一 先读文件\n步骤二 改代码\n步骤三 跑测试"
    assert _effort(text) != "none"


def test_auto_effort_handles_empty_and_odd_input():
    """空/异常输入不得抛错（装配链路里它每轮都会跑）。"""
    assert dc._auto_effort([]) in ("none", "high", "max")
    assert dc._auto_effort([{"role": "user", "content": ""}]) in ("none", "high", "max")
    assert dc._auto_effort([{"role": "assistant", "content": "x"}]) in ("none", "high", "max")
    # 多模态 content 列表形态
    msgs = [{"role": "user", "content": [{"type": "text", "text": "帮我重构这个模块"}]}]
    assert dc._auto_effort(msgs) != "none"


# ── 默认档位回归 ────────────────────────────────────────────

def test_default_thinking_is_not_hard_disabled():
    """默认思考档不得是 none。

    回归的是产品级不一致：前端「均衡/日常推荐」预设是 high、
    SCENARIO_DEFAULT_THINKING['通用'] 也是 high，而默认配置曾是 none
    （彻底关闭思考）——用户装上就是「不动脑」，绝大多数人不会去设置页改。
    """
    import config_defaults as cd
    assert cd.DEFAULT_CONFIG["thinking"] != "none", \
        "默认思考档又变回全关了（与产品推荐值冲突）"
    assert cd.DEFAULT_CONFIG["thinking"] in dc.THINKING_MODES


def test_get_thinking_extra_is_valid_for_every_mode(monkeypatch):
    """直调模型的 extra_body 对每一档都必须合法（含 auto）。

    回归：默认档改为 auto 后，旧实现落到「非 none」分支 → thinking=enabled
    但 `EFFORT_BY_THINKING['auto']` 是 None → 跳过 effort → 漏发 effort。
    """
    class _CU:
        def __init__(self, mode):
            self._mode = mode

        def load_config(self):
            return {"thinking": self._mode}

    for mode in ("none", "low", "medium", "high", "xhigh", "max", "auto"):
        monkeypatch.setitem(sys.modules, "config_utils", _CU(mode))
        extra = dc.get_thinking_extra()
        t = extra["thinking"]["type"]
        assert t in ("enabled", "disabled"), f"{mode}: 非法 thinking {t!r}"
        if t == "enabled":
            assert extra.get("reasoning_effort") in ("low", "high", "max"), \
                f"{mode}: enabled 却缺 effort -> {extra!r}"
        else:
            assert "reasoning_effort" not in extra, f"{mode}: disabled 不该带 effort"
