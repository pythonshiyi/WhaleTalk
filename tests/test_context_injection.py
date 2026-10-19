"""上下文注入：注入端必须输出**可判定**的信息，采样端必须抗分布倾斜。

背景（一份实测诊断，data/workspace/上下文注入问题诊断/）：系统提示里的
`[已验证工具链]` 与 `[项目任务记录]` 机制本身是好的，但注入的信息无效——
`args` / `result` / `chain` 都存了，注入时全扔了，只留工具名与标题。

锁定的承诺：
1. 工具链每行必须含**参数 + 结果摘要**，光有工具名等于没法判定（`- run_python`
   既可能是 print('hi') 也可能是"生成 docx 表格"）；
2. 通用逃生舱（run_python/run_command）必须限额，否则按"最近 N 条"取窗口必然被
   它们占满——这是采样策略与数据分布不匹配，不是偶发；
3. 参数与结果双空的记录（测试残留 tool0–tool7）不得出现在注入里；
4. 任务记录要有信息量门槛，且带上 chain；凑不满就少给，不填位；
5. 记账侧要有准入（空参数/无信息量结果/测试名不入库）+ 哈希去重 + degrade 留痕。
"""
import context_providers as cp


def _ctx(**deps):
    return cp.Context(deps=deps)


def _stores():
    """stores 由 _record_success_pattern 在函数内 import —— patch 模块本身。"""
    import stores
    return stores


def _pat(tool, args="", result="", ts="2026-10-19 13:00:00"):
    return {"tool": tool, "args": args, "result": result, "ts": ts}


# ── 1. 注入内容可判定 ───────────────────────────────────────────────

def test_pattern_line_carries_args_and_result():
    data = [_pat("image_process", "{'ops':'resize=900x383'}", "已处理图像并保存（187710 字节）")]
    out = cp._provide_patterns(_ctx(patterns_load=lambda: data))
    assert "image_process" in out
    assert "resize=900x383" in out, "参数摘要必须出现，否则无法判定能否复用"
    assert "187710" in out, "结果摘要必须出现"


def test_pattern_signature_is_stable_across_same_tool():
    """同一工具多次出现只留最新一次——窗口不该被同一个工具占满。"""
    data = [
        _pat("read_file", "{'path':'/a'}", "A 内容", "2026-10-19 13:00:01"),
        _pat("read_file", "{'path':'/b'}", "B 内容", "2026-10-19 13:00:02"),
        _pat("write_file", "{'path':'/c'}", "已写入", "2026-10-19 13:00:03"),
    ]
    out = cp._provide_patterns(_ctx(patterns_load=lambda: data))
    assert out.count("read_file") == 1
    assert "/b" in out and "/a" not in out, "应保留最新的那次"


# ── 2. 通用逃生舱限额 ───────────────────────────────────────────────

def test_generic_escape_hatch_is_capped():
    """实测数据里 run_python 占 11/20。不限额的话专业工具永远露不了脸。"""
    data = [_pat("run_python", "{'code':'x%d'}" % i, "退出码 0") for i in range(6)]
    data.append(_pat("image_process", "{'ops':'resize'}", "已处理图像"))
    out = cp._provide_patterns(_ctx(patterns_load=lambda: data))
    assert out.count("run_python") <= 1, "通用工具最多占 1 席"
    assert "image_process" in out, "专业工具必须能被看到"


def test_specialized_tools_survive_generic_flood():
    data = [_pat("run_python", "{'code':'y'}", "ok 结果", "2026-10-19 13:00:%02d" % i)
            for i in range(10)]
    data += [
        _pat("publish_draft", "{'id':1}", "已发布草稿", "2026-10-19 12:00:00"),
        _pat("tts_save", "{'path':'a.wav'}", "已生成 wav", "2026-10-19 12:00:01"),
    ]
    out = cp._provide_patterns(_ctx(patterns_load=lambda: data))
    assert "publish_draft" in out and "tts_save" in out


# ── 3. 脏数据不得污染注入 ───────────────────────────────────────────

def test_test_residue_is_filtered_out():
    """曾出现 tool0–tool7：args 为空、result 恒 'ok'，占了 40% 的窗口。"""
    data = [_pat("tool%d" % i, "", "ok") for i in range(8)]
    data.append(_pat("image_process", "{'ops':'resize'}", "已处理图像"))
    out = cp._provide_patterns(_ctx(patterns_load=lambda: data))
    assert "tool0" not in out and "tool7" not in out
    assert "image_process" in out


def test_records_without_args_and_result_are_skipped():
    out = cp._provide_patterns(_ctx(patterns_load=lambda: [_pat("x", "", "")]))
    assert out is None


def test_empty_patterns_returns_none():
    assert cp._provide_patterns(_ctx(patterns_load=lambda: [])) is None


def test_garbage_entries_do_not_crash():
    out = cp._provide_patterns(_ctx(patterns_load=lambda: [None, "x", 42, {}]))
    assert out is None


# ── 4. 任务记录的信息量门槛 ─────────────────────────────────────────

def _tasklog_ctx(tasks, tmp_path):
    import context_providers as _cp
    import json
    d = tmp_path / ".whaletalk"
    d.mkdir(exist_ok=True)
    (d / "tasklog.json").write_text(
        json.dumps({"tasks": tasks}, ensure_ascii=False), encoding="utf-8")
    return _cp.Context(deps={"active_dir": str(tmp_path)})


def test_tasklog_filters_low_information_titles(tmp_path):
    tasks = [
        {"title": "激活", "chain": []},
        {"title": "是否真的落盘了", "chain": []},
        {"title": "公众号封面图（用 HTML 渲染，不调用接口）", "chain": ["write_code_project", "verify_files"]},
    ]
    out = cp._provide_tasklog(_tasklog_ctx(tasks, tmp_path))
    assert "激活" not in out
    assert "是否真的落盘了" not in out
    assert "公众号封面图" in out, "带决策约束的标题才有交接价值"


def test_tasklog_carries_chain(tmp_path):
    tasks = [{"title": "生成公众号封面图并核验落盘", "chain": ["tts_speak", "tts_save", "list_dir"]}]
    out = cp._provide_tasklog(_tasklog_ctx(tasks, tmp_path))
    assert "tts_speak" in out, "chain 是「上次怎么做的」的证据，不能丢"
    assert "步骤" in out


def test_tasklog_prefers_less_over_filling(tmp_path):
    """凑不满就少给，不要用低质量条目填位。"""
    tasks = [{"title": "激活", "chain": []}, {"title": "继续", "chain": []}]
    assert cp._provide_tasklog(_tasklog_ctx(tasks, tmp_path)) is None


def test_tasklog_empty_returns_none(tmp_path):
    assert cp._provide_tasklog(_tasklog_ctx([], tmp_path)) is None


# ── 5. 记账侧准入（防数据源持续劣化）────────────────────────────────

def test_record_rejects_empty_args(monkeypatch, tmp_path):
    """print('1') 这类探路调用没有复用价值，不该入库。"""
    import api_server as A
    saved = []
    monkeypatch.setattr(A, "PATTERNS_PATH", str(tmp_path / "p.json"))
    monkeypatch.setattr(_stores(), "save_patterns", lambda p, v: saved.append(list(v)))
    A._record_success_pattern("run_python", {}, "ok")
    assert not saved, "空参数不该记账"


def test_record_rejects_test_residue_tool_names(monkeypatch, tmp_path):
    import api_server as A
    saved = []
    monkeypatch.setattr(A, "PATTERNS_PATH", str(tmp_path / "p.json"))
    monkeypatch.setattr(_stores(), "save_patterns", lambda p, v: saved.append(list(v)))
    A._record_success_pattern("tool3", {"a": 1}, "ok 结果足够长")
    assert not saved, "toolN 是测试残留，不该记账"


def test_record_accepts_real_call_and_hashes_args(monkeypatch, tmp_path):
    import api_server as A
    saved = []
    monkeypatch.setattr(A, "PATTERNS_PATH", str(tmp_path / "p.json"))
    monkeypatch.setattr(_stores(), "load_patterns", lambda p: [])
    monkeypatch.setattr(_stores(), "save_patterns", lambda p, v: saved.append(list(v)))
    A._record_success_pattern("image_process", {"ops": "resize=900x383"}, "已处理图像并保存")
    assert saved and saved[0][0]["tool"] == "image_process"
    assert saved[0][0].get("args_sig"), "必须用完整参数的哈希做去重键"


def test_record_dedupes_by_full_args_not_truncated(monkeypatch, tmp_path):
    """前 200 字符相同但后续不同 → 两条不同调用，不能误合并。"""
    import api_server as A
    prefix = "x" * 200
    existing = [{"tool": "run_python", "args": prefix + "AAA",
                 "args_sig": __import__("hashlib").md5(
                     (prefix + "AAA").encode("utf-8")).hexdigest()[:12],
                 "result": "r", "ts": "2026-10-19 13:00:00"}]
    saved = []
    monkeypatch.setattr(A, "PATTERNS_PATH", str(tmp_path / "p.json"))
    monkeypatch.setattr(_stores(), "load_patterns", lambda p: list(existing))
    monkeypatch.setattr(_stores(), "save_patterns", lambda p, v: saved.append(list(v)))
    A._record_success_pattern("run_python", prefix + "BBB", "另一个结果")
    assert len(saved[0]) == 2, "参数不同应视为两条记录"


def test_record_keeps_low_frequency_tools(monkeypatch, tmp_path):
    """通用工具洪水不能把低频专业工具冲掉（每工具保底）。"""
    import api_server as A
    pats = [{"tool": "run_python", "args": "{'code':'%d'}" % i, "args_sig": "s%d" % i,
             "result": "退出码 0", "ts": "2026-10-19 13:%02d:00" % i} for i in range(30)]
    pats.append({"tool": "image_process", "args": "{'ops':'resize'}", "args_sig": "img",
                 "result": "已处理图像", "ts": "2026-10-19 12:00:00"})
    saved = []
    monkeypatch.setattr(A, "PATTERNS_PATH", str(tmp_path / "p.json"))
    monkeypatch.setattr(_stores(), "load_patterns", lambda p: list(pats))
    monkeypatch.setattr(_stores(), "save_patterns", lambda p, v: saved.append(list(v)))
    A._record_success_pattern("write_file", "{'path':'/z'}", "已写入文件")
    tools = {x.get("tool") for x in saved[0]}
    assert "image_process" in tools, f"低频专业工具被冲掉了：{tools}"


def test_record_failure_is_not_silent(monkeypatch, tmp_path):
    """此前 except: pass —— 写盘失败、JSON 损坏全无声，与"失败如实报"相违。"""
    import api_server as A
    monkeypatch.setattr(A, "PATTERNS_PATH", str(tmp_path / "p.json"))
    monkeypatch.setattr(_stores(), "load_patterns", lambda p: (_ for _ in ()).throw(OSError("boom")))
    calls = []
    import degrade
    monkeypatch.setattr(degrade, "degrade",
                        lambda *a, **k: calls.append((a[0] if a else "", k)))
    A._record_success_pattern("run_python", {"code": "x"}, "退出码 0 且结果够长")
    assert calls, "记账失败必须走 degrade 留痕"
    assert "success_pattern" in calls[0][0]
