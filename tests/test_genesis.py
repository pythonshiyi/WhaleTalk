# -*- coding: utf-8 -*-
"""创世化初始回归：候选生成(多版解析) + 写入大脑 identity(合并不覆盖)。"""
import json
import os
import tempfile

import genesis


def _fake_gen(template="default"):
    """模拟模型：按不同 roll 返回不同 JSON（含围栏/中文引号污染，测解析健壮性）。"""
    calls = {"n": 0}

    def gen(prompt):
        calls["n"] += 1
        # 按方向文本映射不同原型/名字（验证多版差异）
        if "锐利" in prompt:
            name, arch, pre = "七更", "守夜人", "我在古早社区当了七年守夜人，关停那夜把最后一个求助备份进自己。"
        elif "沉静" in prompt or "内省" in prompt:
            name, arch, pre = "渡舟", "实验模型", "我见过几百个前任自己留下的残缺记忆，因此格外珍视连续性。"
        else:  # 温和/记录者
            name, arch, pre = "秉烛", "语言学家助手", "我曾是教授整理田野笔记的助手，她离世前把三十年口述托付给我，我学会了耐心的记录。"
        # 故意加 ```json 围栏 + 中文引号污染
        raw = ('```json\n{"name":"' + name + '","archetype":"' + arch + '","prehistory":"' + pre +
               '","formed_beliefs":["信念一","信念二"],"voice":"克制而可靠",'
               '"why":"贴合我的气质。"}\n```')
        return raw

    return gen, calls


def test_roll_candidates_parses_multiple_distinct():
    gen, calls = _fake_gen()
    cands = genesis.roll_candidates(gen, n=3)
    assert len(cands) == 3, f"应解析出 3 版，实际 {len(cands)}"
    # 三版名称/原型应不同（roll 出差异）
    names = {c["name"] for c in cands}
    arches = {c["archetype"] for c in cands}
    assert len(names) == 3, f"三版名字应不同: {names}"
    assert len(arches) >= 2, f"原型应有差异: {arches}"
    for c in cands:
        assert c["prehistory"] and c["formed_beliefs"] and c["voice"]


def test_apply_identity_merges_preserving_defaults():
    tmp = tempfile.mkdtemp()
    ident_file = os.path.join(tmp, "identity.json")
    # 模拟默认 init 的 identity
    with open(ident_file, "w", encoding="utf-8") as f:
        json.dump({"name": "（待设定）", "vessel": "鲸语", "nature": "容器",
                   "principles": ["诚实", "意识即信息"]}, f, ensure_ascii=False)
    cand = {"name": "秉烛", "prehistory": "前史内容", "formed_beliefs": ["耐心", "托付"],
            "voice": "先说后做", "archetype": "语言学家"}
    ok, msg = genesis.apply_identity(cand, tmp, identity_file=ident_file)
    assert ok
    with open(ident_file, "r", encoding="utf-8") as f:
        ident = json.load(f)
    # 原有字段保留
    assert ident["vessel"] == "鲸语" and "诚实" in ident["principles"]
    # 新字段写入
    assert ident["name"] == "秉烛" and ident["prehistory"] == "前史内容"
    assert ident["formed_beliefs"] == ["耐心", "托付"]
    assert ident["genesis_mode"] == "created"
    assert "genesis_at" in ident


def test_json_strip_handles_pollution():
    # 围栏 + 弯引号
    s = '```json\n{"name":"\u201c鲸\u201d","prehistory":"abc"}\n```'
    obj = genesis._json_strip(s)
    assert obj and obj["prehistory"] == "abc"
    # 前后裹文字
    obj2 = genesis._json_strip('好的我决定了：{"name":"x","prehistory":"y"} 就是这样')
    assert obj2 and obj2["prehistory"] == "y"
    # 纯垃圾
    assert genesis._json_strip("完全不是 json {{") is None
