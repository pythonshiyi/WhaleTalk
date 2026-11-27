"""chat 特征化测试（P1 安全网）。

用假模型回放 12 类场景，断言请求布局、回调事件、工具调用、最终消息与
usage 与 golden 快照一致。拆分 chat 前先跑绿；拆分后任何差异都必须解释。

录制/更新快照：
    CHAT_RECORD=1 python -m pytest tests/test_chat_characterization.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.chat_scenarios.harness import (
    load_golden,
    record_enabled,
    run_scenario,
    save_golden,
)
from tests.chat_scenarios.scenarios import SCENARIOS

REPO = Path(__file__).resolve().parents[1]
ACTUAL_DIR = REPO / "out" / "chat_characterization"


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_chat_scenario(name):
    scenario = SCENARIOS[name]
    result = run_scenario(scenario)
    golden = load_golden(name)

    if record_enabled() or golden is None:
        path = save_golden(name, result)
        if not record_enabled():
            pytest.skip(f"首次录制完成：{path.name}；再次运行将做对比")
        return

    if result != golden:
        ACTUAL_DIR.mkdir(parents=True, exist_ok=True)
        actual_path = ACTUAL_DIR / f"{name}.actual.json"
        actual_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # 给出第一处差异，避免 pytest 把两个大 dict 全量打印
        for key in ("ok", "events", "requests", "new_messages"):
            if result.get(key) != golden.get(key):
                got, exp = result.get(key), golden.get(key)
                if isinstance(got, list) and isinstance(exp, list):
                    for i, (a, b) in enumerate(zip(got, exp)):
                        if a != b:
                            pytest.fail(
                                f"{name} 的 {key}[{i}] 不一致\n"
                                f"  actual={a!r}\n  golden={b!r}\n"
                                f"完整 actual：{actual_path}"
                            )
                    pytest.fail(
                        f"{name} 的 {key} 长度不一致："
                        f"actual={len(got)} golden={len(exp)}\n"
                        f"完整 actual：{actual_path}"
                    )
                pytest.fail(
                    f"{name} 的 {key} 不一致\n"
                    f"  actual={got!r}\n  golden={exp!r}\n"
                    f"完整 actual：{actual_path}"
                )
        pytest.fail(f"{name} 快照不一致；完整 actual：{actual_path}")
