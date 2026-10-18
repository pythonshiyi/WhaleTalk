"""自进化路径策略：按「能否被自动证明」选路，而不是按改动大小或审批习惯。

锁定的承诺：
1. 改主程序的默认路径是 self_evolve（分支 + 四层验证），不是写提案；
2. tier=auto 可以自动合入，但硬边界由代码守（信任内核/权限/网络出口 + 体量上限）；
3. 验证链不完整（缺 ruff/pytest）时**拒绝演进**——宁可不动，也不假装验证过；
4. 边界判定发生在任何 git 操作之前（不合规不留下分支或脏工作区）。
"""
import config_defaults
import deepseek_client  # noqa: F401  —— 先建立注册表；agent_tools 的导入顺序是契约
import agent_tools.tool_system as ts
import toolkit


def _f(path, content=""):
    return {"path": path, "content": content}


# ── 1. 任务纪律：默认路径写清楚 ──────────────────────────────────────

def test_task_guide_prefers_self_evolve_for_provable_changes():
    g = config_defaults.TASK_QUALITY_CORE
    assert "能被编译/测试证明的 → self_evolve" in g
    assert "tier=auto" in g
    assert "create_evolution" in g          # 仍保留给"跑不起来"的决策
    assert "不再一律" in g or "按「能否被自动证明」选路" in g


# ── 2. auto 档硬边界（纯函数，不碰 git）──────────────────────────────

def test_auto_guard_rejects_trust_kernel_files():
    ok, why = ts._evolve_auto_guard([_f("permissions.py", "ALLOW = 1\n")])
    assert not ok
    assert "人合入" in why


def test_auto_guard_rejects_too_many_files():
    ok, why = ts._evolve_auto_guard([_f(f"m{i}.py", "x\n") for i in range(6)])
    assert not ok
    assert "5" in why


def test_auto_guard_rejects_oversized_patch():
    ok, why = ts._evolve_auto_guard([_f("a.py", "x\n" * 401)])
    assert not ok
    assert "400" in why


def test_auto_guard_accepts_small_patch():
    ok, why = ts._evolve_auto_guard([_f("a.py", "x = 1\n"), _f("b.py", "y = 2\n")])
    assert ok and why == ""


# ── 3. 入口校验顺序：非法输入/缺工具链/越界都在 git 之前拦下 ────────────

def test_self_evolve_rejects_unknown_tier():
    out = ts.self_evolve("x", [_f("a.py", "x = 1\n")], tier="yolo")
    assert out.startswith("错误") and "tier" in out


def test_self_evolve_refuses_when_toolchain_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "_evolve_missing_toolchain", lambda: ["ruff"])
    out = ts.self_evolve("x", [_f("a.py", "x = 1\n")], project_dir=str(tmp_path))
    assert "验证链不完整" in out
    assert not (tmp_path / ".git").exists()


def test_self_evolve_auto_guard_blocks_before_any_git(monkeypatch, tmp_path):
    monkeypatch.setattr(ts, "_evolve_missing_toolchain", lambda: [])
    out = ts.self_evolve("x", [_f("permissions.py", "x\n")],
                         project_dir=str(tmp_path), tier="auto")
    assert "auto 档不接受" in out
    assert not (tmp_path / ".git").exists()
    assert (tmp_path / "permissions.py").exists() is False   # 也没写盘


# ── 4. 两个工具的分工写在描述里（模型靠它选路）───────────────────────

def test_tool_descriptions_state_the_split():
    descs = {n: toolkit._REGISTRY[n].schema["function"]["description"]  # noqa: SLF001 - 测试直读注册表
             for n in ("self_evolve", "create_evolution")}
    assert "默认路径" in descs["self_evolve"]
    assert "tier=auto" in descs["self_evolve"]
    assert "无法验证" in descs["create_evolution"]
