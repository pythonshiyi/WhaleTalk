"""任务纪律分层注入回归（v3.16.17 提示词优化）。

背景（实测诊断）：改造前 TASK_QUALITY_GUIDE 是**单块 2176 字符 / 21 条编号规则**，
常驻注入到每一个任务模式请求（连「你好」都带）。三个具体问题：
  ① 主题分散：自检/核验产物的要求散在 8 条规则里、最小改动散在 3 条、后台化 3 条
     → 模型每轮都要做「这条算不算那条、哪个优先」的元判断，挤占任务本身的注意力；
  ② 与 DEFAULT_SYSTEM_PROMPT 四处重复表述（最小改动/验证链/后台化/防注入）；
  ③ 否定式堆叠（9 处「不得/严禁」、7 处「必须」）压制主动性。

改法：按「是否每轮都需要」分层——常驻核心 + 按任务形态追加领域规范。
本文件把「分层生效」「不重复」「常驻确实变短」三件事钉死。
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import config_defaults as cd  # noqa: E402
import context_providers as cp  # noqa: E402


def _guide(msg, tools=True):
    return cd.build_task_guide([{"role": "user", "content": msg}], tools_enabled=tools)


# ── 分层行为 ─────────────────────────────────────────────

def test_core_rules_are_always_present():
    """核心纪律必须每轮都在——这些是「缺了就会做错事」的。"""
    for msg in ("你好", "北京有哪些博物馆", "帮我写个脚本", "帮我渲染视频"):
        g = _guide(msg)
        assert "[任务纪律]" in g, msg
        # 防注入与产物核验是最高优先级的常驻项
        assert "防注入" in g, msg
        assert "verify_files" in g or "核验" in g, msg


def test_coding_section_only_on_coding_tasks():
    """编码规范只在编码类任务追加（省 token 的关键）。"""
    assert "[编码规范]" in _guide("帮我写个 Python 脚本读取 CSV")
    assert "[编码规范]" in _guide("重构一下这个模块")
    assert "[编码规范]" in _guide("看看这段\n```py\nx=1\n```")   # 带代码块
    assert "[编码规范]" in _guide("修复这个 bug")
    # 纯寒暄/普通问答不该带编码规范
    assert "[编码规范]" not in _guide("你好")
    assert "[编码规范]" not in _guide("北京有哪些博物馆")


def test_longrun_section_only_on_long_tasks():
    assert "[长任务后台化]" in _guide("帮我渲染一个视频")
    assert "[长任务后台化]" in _guide("批量下载一批图片")
    assert "[长任务后台化]" in _guide("帮我编译这个项目")
    assert "[长任务后台化]" not in _guide("你好")
    assert "[长任务后台化]" not in _guide("改个变量名")


def test_toolargs_section_follows_tools_enabled():
    """有工具可调时给参数规范；纯对话（无工具）时不给——否则是噪声。"""
    assert "[工具参数]" in _guide("你好", tools=True)
    assert "[工具参数]" not in _guide("你好", tools=False)


def test_layering_is_additive_never_drops_core():
    """追加领域规范时核心必须仍在（分层不能变成替换）。"""
    g = _guide("重构这个模块并渲染视频")
    assert "[任务纪律]" in g
    assert "[编码规范]" in g
    assert "[长任务后台化]" in g
    assert "[工具参数]" in g


# ── 体量回归：常驻必须显著短于旧的单块 ──────────────────────

def test_always_on_guide_is_much_shorter_than_legacy_block():
    """常驻核心必须远小于历史单块（2176 字符）。

    这是本次优化的核心收益，用数字钉死：旧单块 2176 → 常驻核心 < 900。
    """
    core = _guide("你好")           # 最轻的一轮：只有核心 + 工具参数
    assert len(core) < 900, f"常驻部分仍然过长：{len(core)}"
    # 即使最重的组合也不该超过旧单块太多
    worst = _guide("重构这个模块并批量渲染视频")
    assert len(worst) < 1500, f"最重组合过长：{len(worst)}"


def test_guide_shrinks_for_simple_vs_complex():
    """简单请求的注入量必须**低于**复杂请求（分层确实按需）。"""
    simple = len(_guide("你好"))
    complex_ = len(_guide("重构这个模块并批量渲染视频"))
    assert simple < complex_, f"分层未生效：simple={simple} complex={complex_}"


# ── 去重：同一要求不得同时出现在系统提示词与纪律里 ──────────

def test_no_duplicated_requirements_between_prompts():
    """系统提示词与任务纪律必须**各管一摊**，不重复表述同一要求。

    历史问题：最小改动 / 验证链 / 后台化 / 防注入 四处两边都写了一遍，
    模型要自行判断哪条优先。现在分工：
      系统提示词 = 人格与表达方式（怎么说话）
      任务纪律   = 任务执行规范（怎么做任务）
    """
    sp = cd.DEFAULT_SYSTEM_PROMPT
    allg = "".join([cd.TASK_QUALITY_CORE, cd.TASK_QUALITY_CODING,
                    cd.TASK_QUALITY_LONGRUN, cd.TASK_QUALITY_TOOLARGS])
    # 这四条曾经两边都有；现在必须只在纪律里
    assert "start_process" not in sp, "后台化要求仍在系统提示词里重复"
    assert "edit_file" not in sp, "最小改动要求仍在系统提示词里重复"
    assert "run_lint" not in sp and "verify_project" not in sp, "验证链仍在系统提示词里重复"
    assert "不可信" not in sp, "防注入仍在系统提示词里重复"
    # 反向：纪律侧确实承载了它们
    assert "start_process" in allg
    assert "edit_file" in allg
    assert "不可信" in allg


def test_negative_phrasing_is_reduced():
    """否定式堆叠要收敛（旧版 9 处「不得/严禁/禁止/不要」）。

    避免把规则写成恐吓清单——过密的禁令会压制主动性。
    """
    allg = "".join([cd.TASK_QUALITY_CORE, cd.TASK_QUALITY_CODING,
                    cd.TASK_QUALITY_LONGRUN, cd.TASK_QUALITY_TOOLARGS])
    neg = sum(allg.count(w) for w in ("不得", "严禁", "禁止"))
    assert neg <= 3, f"否定式表述仍过多：{neg}"


# ── 装配接线：Provider 必须真的用了分层构建器 ────────────────

def test_provider_uses_layered_builder_when_available():
    """接线回归：Provider 拿到 task_guide_builder 时必须走分层。"""
    ctx = cp.Context(messages=[{"role": "user", "content": "你好"}],
                     cfg={"memory_enabled": False},
                     deps={"task_quality_guide": "LEGACY-BLOCK",
                           "task_guide_builder": cd.build_task_guide,
                           "tools_on": True})
    got = cp._provide_task_guide(ctx)
    assert got != "LEGACY-BLOCK", "Provider 未使用分层构建器"
    assert "[任务纪律]" in got


def test_provider_falls_back_to_legacy_block_without_builder():
    """兼容回归：没有 builder 时退回单块常量（对外行为不变）。"""
    ctx = cp.Context(messages=[{"role": "user", "content": "你好"}],
                     cfg={},
                     deps={"task_quality_guide": "LEGACY-BLOCK"})
    assert cp._provide_task_guide(ctx) == "LEGACY-BLOCK"


def test_provider_does_not_crash_on_builder_failure():
    """构建器抛错时降级为单块常量，绝不因分层失败而丢掉全部规范。"""

    def _boom(*a, **k):
        raise RuntimeError("boom")

    ctx = cp.Context(messages=[], cfg={},
                     deps={"task_quality_guide": "LEGACY-BLOCK",
                           "task_guide_builder": _boom, "tools_on": True})
    assert cp._provide_task_guide(ctx) == "LEGACY-BLOCK"


# ── 兼容面：TASK_QUALITY_GUIDE 仍存在（外部导入不破） ─────────

def test_legacy_constant_still_exported():
    assert isinstance(cd.TASK_QUALITY_GUIDE, str)
    assert cd.TASK_QUALITY_GUIDE.strip()
