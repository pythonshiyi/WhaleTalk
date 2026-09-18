"""用量/速率统计回归（TTFT · 输出速率 · 输入输出）。

覆盖：
  1. `_consume_stream` 返回首个/末个增量的计时（供上层算 TTFT 与 tok/s）；
  2. `chat()` 暴露 `on_metrics` 回调（契约）；
  3. `_usage_dict` 字段映射（prompt/completion/cache_hit/cache_miss）；
  4. api_server 的 `_norm_metrics` / `_safe_int` 规范化（脏数据不抛错）；
  5. 停止/中断也下发 metrics（interrupted=True），不再丢统计。
"""
import threading
import types

import deepseek_client as dc


def _chunk(content=None, reasoning=None, finish=None):
    delta = types.SimpleNamespace(content=content, reasoning_content=reasoning, tool_calls=None)
    choice = types.SimpleNamespace(delta=delta, finish_reason=finish)
    return types.SimpleNamespace(choices=[choice], usage=None)


def test_consume_stream_returns_timing():
    client = dc.DeepSeekClient(api_key="sk-test", base_url=dc.DEFAULT_BASE_URL)
    chunks = [_chunk(reasoning="想"), _chunk(content="Hello"), _chunk(content=" world")]
    reasoning, content, tool_calls, finish, usage, timing = client._consume_stream(
        iter(chunks), None, None, None
    )
    assert content == "Hello world"
    assert reasoning == "想"
    first_ts, last_ts = timing
    assert first_ts is not None and last_ts is not None
    assert last_ts >= first_ts


def test_consume_stream_no_content_has_no_timing():
    client = dc.DeepSeekClient(api_key="sk-test", base_url=dc.DEFAULT_BASE_URL)
    # 纯工具轮（无正文/思考）→ 无计时（速率 N/A）
    _, _, _, _, _, timing = client._consume_stream(iter([_chunk()]), None, None, None)
    assert timing == (None, None)


def test_chat_accepts_on_metrics():
    import inspect

    params = inspect.signature(dc.DeepSeekClient.chat).parameters
    assert "on_metrics" in params


def test_usage_dict_mapping():
    usage = types.SimpleNamespace(
        prompt_tokens=100, completion_tokens=50,
        prompt_cache_hit_tokens=80, prompt_cache_miss_tokens=20,
    )
    d = dc.DeepSeekClient._usage_dict(usage)
    assert d == {"prompt": 100, "completion": 50, "cache_hit": 80, "cache_miss": 20}


def test_norm_metrics_and_safe_int():
    import api_server

    m = api_server._norm_metrics({
        "rounds": "2", "completion": "120", "prompt": 300, "cache_hit": 250,
        "ttft_ms": "812.5", "gen_ms": "3000", "total_ms": "5000", "tps": "40",
        "interrupted": True,
    })
    assert m["rounds"] == 2 and m["completion"] == 120
    assert m["ttft_ms"] == 812.5 and m["tps"] == 40
    assert m["interrupted"] is True and m["cache_miss"] == 0
    # 脏数据不抛错
    bad = api_server._norm_metrics({"rounds": None, "gen_ms": "x", "ttft_ms": "y"})
    assert bad["rounds"] == 0 and bad["gen_ms"] == 0.0 and bad["ttft_ms"] is None
    assert bad["interrupted"] is False
    assert api_server._safe_int("12") == 12 and api_server._safe_int("x") == 0


def test_chat_emits_interrupted_metrics_on_stop():
    """停止请求（stop_event 已置位）也要下发一版 metrics，标注 interrupted。"""
    client = dc.DeepSeekClient(api_key="sk-test", base_url=dc.DEFAULT_BASE_URL)
    stop = threading.Event()
    stop.set()
    got = []
    ok = client.chat(
        [{"role": "user", "content": "hi"}],
        pure_chat=True, thinking="none", stop_event=stop, on_metrics=got.append,
    )
    assert ok is False
    assert got and got[-1]["interrupted"] is True
    assert "tps" in got[-1] and "total_ms" in got[-1]
