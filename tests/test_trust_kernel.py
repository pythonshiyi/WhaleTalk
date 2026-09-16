# -*- coding: utf-8 -*-
"""信任内核测试：自我修改的声明、检出、留痕、回滚与确认。

全部用例在 tmp 沙箱内构造假内核文件，**绝不触碰真实项目文件**。
覆盖的关键不变量：
- 未声明的改动必须被检出（基线优先，manifest 丢失不等于看不见）；
- 经工具层声明的改动必须被视为正常（不产生事件）；
- 回滚/确认可逆且留痕；
- 干净状态下不给模型注入任何额外 token。
"""
import json
import os
import shutil
import subprocess

import pytest

import trust_kernel as tk


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """临时项目根 + 假内核文件；自动还原模块全局，避免污染真实项目。"""
    root = tmp_path / "proj"
    root.mkdir()
    for name in tk.PROTECTED:
        (root / name).write_text(f"# v1 {name}\n", encoding="utf-8")
    monkeypatch.setattr(tk, "PROJECT_DIR", str(root))
    monkeypatch.setattr(tk, "TRUST_DIR", str(root / "trust"))
    import tool_hooks
    tool_hooks.clear_kernel_cache()
    tk.init()
    yield tk, root


def _write(root, name, text):
    (root / name).write_text(text, encoding="utf-8")


def _incidents(root):
    d = root / "trust" / "incidents"
    return sorted(p.name for p in d.glob("*.json")) if d.is_dir() else []


# ── 引导与干净状态 ───────────────────────────────────────────────────────

def test_bootstrap_clean_state(sandbox):
    tk_, root = sandbox
    assert tk_.verify()["ok"] is True
    assert (root / "trust" / "manifest.json").is_file()
    assert (root / "trust" / "baseline" / "permissions.py").is_file()
    assert _incidents(root) == []


def test_clean_state_injects_nothing(sandbox):
    """干净时零注入——不占 token，也不扰动前缀缓存。"""
    tk_, _ = sandbox
    tk_.boot_check()
    assert tk_.status()["state"] == "ok"
    assert tk_.integrity_notice() == ""


# ── 检出未声明改动 ───────────────────────────────────────────────────────

def test_undeclared_change_detected(sandbox):
    tk_, root = sandbox
    _write(root, "permissions.py", "# 被改过了\nblocked_dirs = []\n")
    res = tk_.verify()
    assert res["ok"] is False
    assert [c["name"] for c in res["changed"]] == ["permissions.py"]
    assert res["changed"][0]["ref_source"] == "baseline"
    assert "blocked_dirs" in tk_.diff("permissions.py")


def test_boot_check_records_incident_and_notice(sandbox):
    tk_, root = sandbox
    _write(root, "security.py", "# 底线被关了\n")
    payload = tk_.boot_check()
    assert payload["state"] == "unconfirmed"
    assert payload["changed"] == ["security.py"]
    assert payload["incident"] in _incidents(root)
    notice = tk_.integrity_notice()
    assert "自我完整性" in notice and "security.py" in notice


def test_boot_check_does_not_modify_files(sandbox):
    """report 模式（默认）只报告、不回滚——开发期用 IDE 改内核文件是正常行为。"""
    tk_, root = sandbox
    _write(root, "crypto.py", "# 我自己改的\n")
    tk_.boot_check()
    assert (root / "crypto.py").read_text(encoding="utf-8") == "# 我自己改的\n"


def test_baseline_wins_even_if_manifest_lost(sandbox):
    """基线是事实来源：删除 manifest 后仍能检出改动，且必须产生告警。"""
    tk_, root = sandbox
    (root / "trust" / "manifest.json").unlink()
    payload = tk_.boot_check()
    assert [a["kind"] for a in payload["alerts"]] == ["manifest_lost"]
    assert payload["state"] == "unconfirmed"
    assert tk_.integrity_notice() != ""


def test_manifest_lost_alert_fires_once(sandbox):
    """回归：重引导告警不得在每次启动时重复触发。"""
    tk_, root = sandbox
    (root / "trust" / "manifest.json").unlink()
    assert [a["kind"] for a in tk_.boot_check()["alerts"]] == ["manifest_lost"]
    second = tk_.boot_check()
    assert second["alerts"] == []
    assert second["state"] == "ok"


def test_missing_kernel_file_reported(sandbox):
    tk_, root = sandbox
    (root / "snapshot.py").unlink()
    res = tk_.verify()
    assert res["ok"] is False
    assert [c["name"] for c in res["missing"]] == ["snapshot.py"]


# ── 声明式改动（工具层接入点） ───────────────────────────────────────────

def test_declared_change_is_not_an_incident(sandbox):
    tk_, root = sandbox
    handle = tk_.declare(str(root / "permissions.py"), "调整 SSRF 底线", "write_file")
    _write(root, "permissions.py", "# v2 声明过的改动\n")
    assert tk_.commit(handle) is True
    assert tk_.verify()["ok"] is True
    tk_.boot_check()
    assert _incidents(root) == []
    events = [r["event"] for r in tk_.ledger_tail(4)]
    assert "declare" in events and "commit" in events


def test_declared_write_context_manager(sandbox):
    tk_, root = sandbox
    with tk_.declared_write(str(root / "crypto.py"), "换加密实现", "run_python"):
        _write(root, "crypto.py", "# v2\n")
    assert tk_.verify()["ok"] is True


def test_declare_ignores_non_kernel_files(sandbox):
    tk_, root = sandbox
    assert tk_.declare(str(root / "api_server.py"), "无关文件", "write_file") is None
    assert tk_.verify()["ok"] is True


def test_name_resolution_relative_and_absolute(sandbox):
    """CLI 传相对名、工具层传绝对路径，两种形态都要能解析（回归）。"""
    tk_, root = sandbox
    assert tk_._resolve_name("permissions.py") == "permissions.py"
    assert tk_._resolve_name(str(root / "security.py")) == "security.py"
    assert tk_._resolve_name(str(root / "nope.py")) is None


# ── 回滚与确认 ───────────────────────────────────────────────────────────

def test_restore_rolls_back_to_baseline(sandbox):
    tk_, root = sandbox
    original = (root / "permissions.py").read_text(encoding="utf-8")
    _write(root, "permissions.py", "# 偷偷削弱\n")
    ok, msg = tk_.restore("permissions.py")
    assert ok is True and "已回滚" in msg
    assert (root / "permissions.py").read_text(encoding="utf-8") == original
    assert tk_.verify()["ok"] is True
    # 回滚前的改动必须留证，不能无痕消失（history/ 装已声明改动的历史）
    assert any("before_restore" in p.name
               for p in (root / "trust" / "history").glob("*"))


def test_accept_advances_baseline(sandbox):
    tk_, root = sandbox
    _write(root, "crypto.py", "# 我确实要这么改\n")
    assert tk_.verify()["ok"] is False
    ok, _ = tk_.accept(all_=True)
    assert ok is True
    assert tk_.verify()["ok"] is True
    assert tk_.status()["state"] == "ok"


def test_accept_single_file_leaves_others_dirty(sandbox):
    tk_, root = sandbox
    _write(root, "crypto.py", "# a\n")
    _write(root, "security.py", "# b\n")
    ok, msg = tk_.accept("crypto.py")
    assert ok is True and "crypto.py" in msg
    assert [c["name"] for c in tk_.verify()["changed"]] == ["security.py"]


def test_restore_without_baseline_fails_softly(sandbox):
    tk_, root = sandbox
    (root / "trust" / "baseline" / "permissions.py").unlink()
    ok, msg = tk_.restore("permissions.py")
    assert ok is False and "无法回滚" in msg


# ── guard 模式（仅报告 vs 强制隔离） ─────────────────────────────────────

def test_guard_mode_quarantines_and_restores(sandbox):
    tk_, root = sandbox
    original = (root / "permissions.py").read_text(encoding="utf-8")
    tk_.init(mode="guard")
    _write(root, "permissions.py", "# 偷偷削弱权限\n")
    payload = tk_.boot_check()
    assert payload["guarded"] == ["permissions.py"]
    assert (root / "permissions.py").read_text(encoding="utf-8") == original
    kept = [p for p in (root / "trust" / "quarantine").rglob("*permissions.py")]
    assert kept and kept[0].read_text(encoding="utf-8") == "# 偷偷削弱权限\n"


def test_config_cannot_shrink_protected_set(sandbox):
    """配置只能追加保护，不能用来「摘掉」内核文件。"""
    tk_, root = sandbox
    (root / "trust" / "config.json").write_text(
        json.dumps({"mode": "report", "protected": []}, ensure_ascii=False),
        encoding="utf-8")
    assert "permissions.py" in tk_.protected_names()
    (root / "trust" / "config.json").write_text(
        json.dumps({"extra_protected": ["new_guard.py"]}, ensure_ascii=False),
        encoding="utf-8")
    assert "new_guard.py" in tk_.protected_names()


# ── 工具层接线（write_file / edit_file） ────────────────────────────────

def _tools():
    """经主入口取工具函数。

    必须走 `deepseek_client`：tools 子模块顶层会 `from deepseek_client import ...`，
    若直接 `import agent_tools.tool_files` 会在 deepseek_client 未装配完成时触发
    工具名重复注册（RuntimeError）。这是既有的导入顺序契约，不是本模块引入的。
    """
    import deepseek_client as dc
    return dc


def _enable_extra(sandbox, root, name):
    """把 tmp 内的额外文件纳入内核清单，并让基线接纳它。"""
    tk_, _ = sandbox
    (root / name).write_text(f"# v1 {name}\n", encoding="utf-8")
    (root / "trust" / "config.json").write_text(
        json.dumps({"mode": "report", "extra_protected": [name]}, ensure_ascii=False),
        encoding="utf-8")
    tk_.init()
    tk_.accept(name)
    # 工具钩子对内核文件名做了 30s TTL 缓存（避免每次工具调用读 config.json），
    # 测试里必须清掉，否则读到的还是上一个用例的清单。
    import tool_hooks
    tool_hooks.clear_kernel_cache()
    return root / name


def test_write_file_declares_kernel_change(sandbox):
    """经工具层改写内核文件 = 已声明：写入成功、基线推进、不产生事件。"""
    tfiles = _tools()
    tk_, root = sandbox
    target = _enable_extra(sandbox, root, "guard_x.py")
    out = tfiles.write_file(str(target), "# v2 经工具层改写\n")
    assert "已写入" in out
    assert "已登记为信任内核声明改动" in out
    assert target.read_text(encoding="utf-8") == "# v2 经工具层改写\n"
    assert tk_.verify()["ok"] is True
    tk_.boot_check()
    assert _incidents(root) == []


def test_edit_file_declares_kernel_change(sandbox):
    tfiles = _tools()
    tk_, root = sandbox
    target = _enable_extra(sandbox, root, "guard_y.py")
    out = tfiles.edit_file(str(target), old="# v1 guard_y.py", new="# v2 编辑过")
    assert "已替换" in out and "已登记为信任内核声明改动" in out
    assert "v2 编辑过" in target.read_text(encoding="utf-8")
    assert tk_.verify()["ok"] is True


def test_write_file_non_kernel_untouched(sandbox):
    """非内核文件写入必须完全不受影响（能力不减的回归保护）。"""
    tfiles = _tools()
    tk_, root = sandbox
    plain = root / "ordinary.txt"
    out = tfiles.write_file(str(plain), "hello")
    assert "已写入" in out and "信任内核" not in out
    assert plain.read_text(encoding="utf-8") == "hello"


# ── 行尾差异（git 检出噪声）与真实改动的区分 ─────────────────────────────

def _flip_eol(path):
    """把文件整体换成另一种行尾（保证只差行尾，语义不变）。"""
    raw = path.read_bytes()
    flipped = raw.replace(b"\r\n", b"\n") if b"\r\n" in raw else raw.replace(b"\n", b"\r\n")
    assert flipped != raw, "测试前置：需要文件含可翻转的行尾"
    path.write_bytes(flipped)


def test_eol_only_change_marked_cosmetic(sandbox):
    """git 检出导致的整体行尾变化应被标注，避免把 git 正常行为报成篡改。"""
    tk_, root = sandbox
    _flip_eol(root / "permissions.py")
    res = tk_.verify()
    assert res["ok"] is False
    assert res["changed"][0]["cosmetic"] is True
    assert res["cosmetic_only"] is True
    payload = tk_.boot_check()
    assert payload["cosmetic_only"] is True
    assert "行尾" in (payload.get("note") or "")
    assert "行尾" in tk_.integrity_notice()
    # 标注 ≠ 放行：依然是未确认状态，处置权仍在用户
    assert tk_.status()["state"] == "unconfirmed"
    # 确认一次即恢复干净
    tk_.accept(all_=True)
    assert tk_.verify()["ok"] is True


def test_real_change_not_marked_cosmetic(sandbox):
    """真实内容改动不得被误标为仅行尾差异。"""
    tk_, root = sandbox
    p = root / "security.py"
    p.write_bytes(p.read_bytes() + b"# real change\n")
    res = tk_.verify()
    assert res["changed"][0].get("cosmetic") is False
    assert res["cosmetic_only"] is False
    assert "行尾" not in tk_.integrity_notice()


# ── 健壮性 ───────────────────────────────────────────────────────────────

def test_config_typo_does_not_alarm(sandbox):
    """extra_protected 写了不存在的名字不该变成永久误报（否则淹没真异常）。"""
    tk_, root = sandbox
    (root / "trust" / "config.json").write_text(
        json.dumps({"extra_protected": ["does_not_exist.py"]}, ensure_ascii=False),
        encoding="utf-8")
    res = tk_.verify()
    assert res["ok"] is True
    assert res["missing"] == []


def test_status_deep_returns_full_shape(sandbox):
    """回归：status(deep=True) 必须返回完整字段。

    曾因 deep 分支漏设 `repo_updates` → NameError 被 except 兜底吞成
    `state=unknown`，而旧断言只看 state（unknown 也在允许集合内），故未暴露。
    """
    tk_, _ = sandbox
    st = tk_.status(deep=True)
    assert st["state"] == "ok"
    for key in ("state", "mode", "protected", "changed", "alerts", "cosmetic_only",
                "repo_updates", "unchanged", "last_check", "counts"):
        assert key in st, f"status(deep) 缺少字段：{key}"
    assert st["repo_updates"] == []
    assert st["counts"]["baseline"] >= 1


def test_status_never_raises_on_broken_dirs(sandbox, monkeypatch):
    tk_, _ = sandbox
    monkeypatch.setattr(tk_, "TRUST_DIR", os.path.join("Z:", "\\nonexistent", "trust"))
    st = tk_.status(deep=True)
    assert st["state"] in ("ok", "unknown", "unconfirmed")


def test_cli_verify_exit_code_reflects_state(sandbox):
    tk_, root = sandbox
    assert tk_.main(["verify"]) == 0
    _write(root, "security.py", "# 改了\n")
    assert tk_.main(["verify"]) == 2
    assert tk_.main(["diff", "security.py"]) == 0
    assert tk_.main(["log", "-n", "2"]) == 0


# ── 故事线（时间轴）──────────────────────────────────────────────────────

def test_timeline_includes_bootstrap(sandbox):
    """引导事件应出现在时间轴主干（账本）。"""
    tk_, _ = sandbox
    tl = tk_.timeline(20)
    assert any(e["source"] == "ledger" and e["event"] in ("bootstrap", "rebootstrap") for e in tl)


def test_timeline_merges_undeclared_incident(sandbox):
    """未声明改动（incident）应合并进时间轴，且带中文事件标签。"""
    tk_, root = sandbox
    _write(root, "permissions.py", "# 未声明改动\n")
    tk_.boot_check()                       # 生成 incident
    tl = tk_.timeline(50)
    inc = [e for e in tl if e["source"] == "incident"]
    assert inc, "应包含未声明事件"
    assert inc[0]["label"] == "发现未声明改动"
    assert "permissions.py" in inc[0]["summary"]


def test_timeline_sorted_desc_and_limited(sandbox):
    tk_, root = sandbox
    _write(root, "crypto.py", "# 改\n")
    tk_.boot_check()
    tl = tk_.timeline(1)
    assert len(tl) == 1
    full = tk_.timeline(100)
    ts = [e["ts"] for e in full]
    assert ts == sorted(ts, reverse=True)


def test_timeline_never_raises_on_empty(sandbox, monkeypatch):
    tk_, _ = sandbox
    monkeypatch.setattr(tk_, "TRUST_DIR", os.path.join("Z:", "\\nonexistent", "trust"))
    assert tk_.timeline(10) == []


# ── 仓库更新（git 锚点）：把 git 拉取从「自我修改」里分出来 ───────────────

_GIT = shutil.which("git")


def _git(root, *args):
    subprocess.run([_GIT, "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def git_sandbox(tmp_path, monkeypatch):
    """真实临时 git 仓库（含 origin 上游，HEAD 与上游一致）+ 内核文件；随后引导基线。"""
    if _GIT is None:
        pytest.skip("需要 git 可执行文件")
    root = tmp_path / "proj"
    root.mkdir()
    for name in tk.PROTECTED:
        (root / name).write_text(f"# v1 {name}\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "tester")
    _git(root, "branch", "-M", "main")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "init")
    # 建一个 bare 上游并推送：让 HEAD 与 @{u} 一致（模拟「刚 clone/pull」的可信态）
    _git(tmp_path, "init", "--bare", "origin.git")
    _git(root, "remote", "add", "origin", str(tmp_path / "origin.git"))
    _git(root, "push", "-u", "origin", "main")
    monkeypatch.setattr(tk, "PROJECT_DIR", str(root))
    monkeypatch.setattr(tk, "TRUST_DIR", str(root / "trust"))
    import tool_hooks
    tool_hooks.clear_kernel_cache()
    tk.init()
    yield tk, root


def test_git_repo_update_auto_followed(git_sandbox):
    """git 更新内核文件（已提交并推送 → == 上游 HEAD）应自动跟随基线，不误报。"""
    tk_, root = git_sandbox
    (root / "permissions.py").write_text("# v2 官方更新\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "update permissions")
    _git(root, "push", "origin", "main")  # 推上去 → HEAD 不再领先上游（可信）
    assert tk_.verify()["ok"] is False            # 跟随前：与基线不一致
    payload = tk_.boot_check()
    assert payload["state"] == "ok"               # 跟随仓库更新 → 不再报警
    assert payload["repo_updates"] == ["permissions.py"]
    assert tk_.verify()["ok"] is True             # 基线已推进
    assert (root / "trust" / "baseline" / "permissions.py").read_text(
        encoding="utf-8") == "# v2 官方更新\n"
    assert _incidents(root) == []                 # 不产生未声明事件
    assert "repo_update" in [r["event"] for r in tk_.ledger_tail(5)]
    assert tk_.status()["repo_updates"] == ["permissions.py"]
    assert tk_.integrity_notice() == ""           # 干净：零注入


def test_git_uncommitted_change_still_reported(git_sandbox):
    """工作区改动（未提交，≠ HEAD）必须照常报警——自动跟随不得放行真实自我修改。"""
    tk_, root = git_sandbox
    (root / "security.py").write_text("# 未提交的自我修改\n", encoding="utf-8")
    payload = tk_.boot_check()
    assert payload["state"] == "unconfirmed"
    assert payload["changed"] == ["security.py"]
    assert payload["repo_updates"] == []
    assert payload["incident"] in _incidents(root)
    assert "security.py" in tk_.integrity_notice()


def test_git_auto_follow_can_be_disabled(git_sandbox):
    """配置 auto_follow_git=false 时，即使 == HEAD 也按未声明改动报告（严格模式）。"""
    tk_, root = git_sandbox
    (root / "trust" / "config.json").write_text(
        json.dumps({"mode": "report", "auto_follow_git": False}, ensure_ascii=False),
        encoding="utf-8")
    (root / "permissions.py").write_text("# v2\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "update")
    payload = tk_.boot_check()
    assert payload["state"] == "unconfirmed"
    assert payload["repo_updates"] == []
    assert payload["changed"] == ["permissions.py"]


def test_local_unpushed_commit_not_followed(git_sandbox):
    """本地未推送的提交（可能是 AI 自造「伪仓库更新」）不得被 git 锚点跟随。"""
    tk_, root = git_sandbox
    (root / "permissions.py").write_text("# 本地提交，未推送\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "local only")
    payload = tk_.boot_check()
    assert payload["repo_updates"] == []
    assert payload["state"] == "unconfirmed"
    assert payload["changed"] == ["permissions.py"]


def test_repeated_unconfirmed_change_deduped(git_sandbox):
    """同一份未确认改动跨启动只留一份 incident 与一份证据副本（避免反复堆证据）。"""
    tk_, root = git_sandbox
    (root / "crypto.py").write_text("# 反复未确认\n", encoding="utf-8")
    p1 = tk_.boot_check()
    p2 = tk_.boot_check()
    assert p1["incident"] and p1["incident"] == p2["incident"]
    assert len(_incidents(root)) == 1
    assert len(list((root / "trust" / "incidents").glob("*.current"))) == 1


def test_new_change_creates_new_incident(git_sandbox):
    """改动内容再次变化（sha 不同）时必须新建 incident，不能拿旧证据顶替。"""
    tk_, root = git_sandbox
    (root / "crypto.py").write_text("# 第一次\n", encoding="utf-8")
    p1 = tk_.boot_check()
    (root / "crypto.py").write_text("# 第二次变了\n", encoding="utf-8")
    p2 = tk_.boot_check()
    assert p2["incident"] != p1["incident"]
    assert len(_incidents(root)) == 2
