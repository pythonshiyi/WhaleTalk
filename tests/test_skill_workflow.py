"""③a 宏录制回归：成功链路 → 可执行 workflow 草稿（skill_factory.workflow_drafts）。"""
import skill_factory as sf


def _tasks():
    return [
        {"title": "周报", "chain": ["read_file", "write_file", "send_email"], "ts": "2026-01-02"},
        {"title": "周报", "chain": ["read_file", "write_file", "send_email"], "ts": "2026-01-01"},
        {"title": "一次性小任务", "chain": ["list_dir", "read_file"], "ts": "2026-01-03"},
    ]


def test_build_workflow_shape():
    info = {"chain": ["read_file", "write_file"], "count": 3, "titles": ["周报"]}
    wf = sf.build_workflow(info)
    assert wf["name"].startswith("宏 · 周报")
    assert any("read_file" in s for s in wf["steps"])
    assert wf["steps"][-1].startswith("3.")   # 2 步 + 收尾核验
    assert wf["auto"] is True and wf["hits"] == 3


def test_workflow_drafts_orders_and_allows_single_occurrence():
    drafts = sf.workflow_drafts(_tasks())
    names = [d["name"] for d in drafts]
    assert "宏 · 周报" in names
    assert "宏 · 一次性小任务" in names          # 单次（count=1）也允许录制
    assert names[0] == "宏 · 周报"               # 出现次数多的排前


def test_workflow_drafts_skips_existing_and_short_chain():
    drafts = sf.workflow_drafts(_tasks(), existing_workflows={"宏 · 周报": {"steps": ["x"]}})
    assert all(d["name"] != "宏 · 周报" for d in drafts)
    # 链长 < 2 不产出
    assert sf.workflow_drafts([{"title": "t", "chain": ["only_one"]}]) == []


def test_chain_signature_folds_consecutive_dupes():
    assert sf.chain_signature(["a", "a", "b", "b", "a"]) == "a>b>a"
