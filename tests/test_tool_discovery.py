"""能力发现：activate_tools 必须能按**自然语言**找到能力，而不只接受已知名字。

锁定的承诺（对应一条真实的能力缺陷）：
分组懒加载省了上下文，代价是模型不知道自己有什么 → 只能猜名字，猜不中就宣称
"我没有这项能力"，转头用 Python 硬做一遍。所以 activate_tools 必须接受描述性查询。

1. 已知工具名 / 组名原样激活（不破坏既有行为）；
2. 中文描述（"读文件"）能召回包含正确工具的候选；
3. 英文关键词（"pdf"）优先命中名称含该词的工具——强信号；
4. **无意义的查询不得返回噪声**（"zzzz不存在"、单字"的"）——返回噪声比不返回更糟，
   会让模型把猜测当事实；
5. notes 必须回传给模型，且措辞含"候选/未必精确"（不能假装精确）。
"""
import deepseek_client as dc


def _names():
    return {t["function"]["name"] for t in dc.TOOLS}


# ── 1. 既有行为不变 ────────────────────────────────────────────────

def test_exact_tool_name_still_works():
    act = set()
    dc._expand_activation(["read_file"], _names(), act)
    assert act == {"read_file"}


def test_group_name_expands_whole_group():
    act = set()
    dc._expand_activation(["文件与目录"], _names(), act)
    assert "read_file" in act and len(act) > 1


def test_unknown_name_does_not_raise():
    act = set()
    _a, notes = dc._expand_activation(["__nope__"], _names(), act)
    assert notes and "没有匹配" in notes[0]


# ── 2. 中文描述能召回 ──────────────────────────────────────────────

def test_chinese_description_recalls_relevant_tools():
    found = dc._discover_tools("读文件", _names(), limit=8)
    assert found, "中文描述必须能召回候选"
    assert "read_file" in found, f"「读文件」的候选里应有 read_file，实际 {found}"


def test_chinese_description_activates_candidates():
    act = set()
    _a, notes = dc._expand_activation(["读文件"], _names(), act)
    assert "read_file" in act
    assert any("读文件" in n for n in notes)


def test_write_description_finds_write_file():
    assert "write_file" in dc._discover_tools("写文件", _names(), limit=8)


def test_weather_description_finds_weather():
    assert "get_weather" in dc._discover_tools("查天气", _names(), limit=8)


# ── 3. 英文关键词是强信号 ──────────────────────────────────────────

def test_english_keyword_prefers_name_match():
    found = dc._discover_tools("pdf", _names(), limit=5)
    assert found, "pdf 必须召回"
    assert all("pdf" in n.lower() for n in found[:3]), f"名称含 pdf 的应排在前面：{found}"


def test_mixed_query_works():
    found = dc._discover_tools("pdf 抽取", _names(), limit=5)
    assert "pdf_extract" in found


# ── 4. 噪声必须被挡住（比召回更重要）────────────────────────────────

def test_nonsense_query_returns_nothing():
    for q in ("zzzz不存在", "asdfghjkl", "qqqq"):
        assert dc._discover_tools(q, _names(), limit=8) == [], f"{q!r} 不该返回候选"


def test_single_character_query_returns_nothing():
    # "的" 是高频字，撞出来的结果全是噪声——宁可让模型改用组名点菜
    assert dc._discover_tools("的", _names(), limit=8) == []


def test_empty_query_returns_nothing():
    assert dc._discover_tools("", _names()) == []
    assert dc._discover_tools(None, _names()) == []


# ── 5. 回传措辞不得假装精确 ────────────────────────────────────────

def test_note_wording_marks_uncertainty():
    act = set()
    _a, notes = dc._expand_activation(["读文件"], _names(), act)
    joined = " ".join(notes)
    assert "候选" in joined and "未必精确" in joined, "模糊匹配必须标明不确定"


def test_note_for_no_match_suggests_group_names():
    act = set()
    _a, notes = dc._expand_activation(["zzzz不存在"], _names(), act)
    assert any("组名" in n for n in notes)


# ── 6. 能力包装载后索引必须跟着更新 ────────────────────────────────

def test_haystack_follows_runtime_capability_changes():
    """能力包会就地重建 TOOLS，索引若按 id(TOOLS) 缓存就必须失效。"""
    before = dc._tool_haystack()
    assert before is dc._tool_haystack()          # 同一对象 → 命中缓存
    assert "read_file" in before
