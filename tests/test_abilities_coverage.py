"""能力域清单必须覆盖全部内置工具（不能因域名拼写错误静默漏项）。"""
import deepseek_client as dc
import api_server


def test_abilities_cover_every_tool():
    data = api_server._abilities()
    names = [t["name"] for d in data["domains"] for t in d["tools"]]
    expected = {t["function"]["name"] for t in dc.TOOLS}
    assert data["total"] == len(expected)
    assert len(names) == len(expected)
    assert set(names) == expected, f"能力域漏项：{sorted(expected - set(names))}"
    assert all(d["count"] == len(d["tools"]) for d in data["domains"])
