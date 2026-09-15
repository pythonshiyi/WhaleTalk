# -*- coding: utf-8 -*-
"""第三方 / OpenCode Go 网关兼容回归。

背景：DeepSeek 官方专属参数（thinking extra_body、reasoning_effort、/beta、
FIM `prefix`、strict 工具 schema、/user/balance）在第三方 OpenAI 兼容网关
（OpenCode Go / OpenAI / Kimi / 智谱 / Ollama …）上并不存在，硬发可能被 400
拒绝。本测试锁定「仅官方端点下发官方专属能力」的契约。

覆盖：
  1. is_official_endpoint 判定（含 /beta、/v1 后缀与第三方域名）；
  2. DeepSeekClient.is_official 与端点一致；
  3. 非官方网关下 fim_complete 明确报错而非发起网络请求；
  4. 非官方网关下 check_balance 返回说明性错误而非请求 /user/balance。
"""
import pytest

import config_utils
import deepseek_client as dc

CUSTOM = "https://opencode.ai/zen/go/v1"


# ── 0. 网关地址规范化（用户误粘完整端点） ─────────────────────────
def test_normalize_base_url_strips_chat_completions():
    assert dc.normalize_base_url(CUSTOM + "/chat/completions") == CUSTOM
    assert dc.normalize_base_url(CUSTOM + "/chat/completions/") == CUSTOM
    assert dc.normalize_base_url("https://api.deepseek.com/chat/completions") == "https://api.deepseek.com"
    # 正常地址/版本段保持不变
    assert dc.normalize_base_url(CUSTOM) == CUSTOM
    assert dc.normalize_base_url("https://api.deepseek.com") == "https://api.deepseek.com"
    assert dc.normalize_base_url("https://api.deepseek.com/") == "https://api.deepseek.com"


def test_config_normalize_strips_full_endpoint():
    cfg = config_utils.normalize_config({"base_url": CUSTOM + "/chat/completions", "model": "deepseek-flash"})
    assert cfg["base_url"] == CUSTOM


def test_client_normalizes_base_url():
    c = dc.DeepSeekClient(api_key="sk-test", base_url=CUSTOM + "/chat/completions", model="deepseek-flash")
    assert c.base_url == CUSTOM


# ── 0b. OpenCode 会话头（x-opencode-session） ─────────────────────
def test_is_opencode_endpoint():
    assert dc.is_opencode_endpoint(CUSTOM) is True
    assert dc.is_opencode_endpoint("https://opencode.ai/zen/v1") is True
    assert dc.is_opencode_endpoint("https://api.deepseek.com") is False
    assert dc.is_opencode_endpoint("https://api.openai.com/v1") is False


def test_gateway_headers_only_for_opencode():
    h = dc.gateway_default_headers(CUSTOM, "sess-123")
    assert h and h["x-opencode-session"] == "sess-123"
    assert "User-Agent" in h and "WhaleTalk" in h["User-Agent"]
    # 非 opencode 网关：无额外头（不污染其它供应商）
    assert dc.gateway_default_headers("https://api.deepseek.com", "s") is None


def test_gateway_headers_generate_when_no_session():
    h = dc.gateway_default_headers(CUSTOM, "")
    assert h and h["x-opencode-session"]


def test_client_attaches_opencode_default_headers():
    c = dc.DeepSeekClient(api_key="sk-test", base_url=CUSTOM, model="deepseek-v4.1-flash", gateway_session="abc")
    assert c.gateway_headers and c.gateway_headers["x-opencode-session"] == "abc"
    # 官方端点不应带 opencode 头
    c2 = dc.DeepSeekClient(api_key="sk-test", base_url=dc.DEFAULT_BASE_URL)
    assert c2.gateway_headers is None


# ── 1. 端点判定 ───────────────────────────────────────────────────
def test_is_official_endpoint_true_cases():
    for u in (
        "https://api.deepseek.com",
        "https://api.deepseek.com/",
        "https://api.deepseek.com/beta",
        "https://api.deepseek.com/v1",
        "http://api.deepseek.com",
        "  https://api.deepseek.com/beta/  ",
    ):
        assert dc.is_official_endpoint(u) is True, u


def test_is_official_endpoint_false_cases():
    for u in (
        CUSTOM,
        "https://api.openai.com/v1",
        "https://api.moonshot.cn/v1",
        "http://localhost:11434/v1",
        "https://api.deepseek.com.evil.example/v1",
        "",
        None,
    ):
        assert dc.is_official_endpoint(u) is False, u


# ── 2. 客户端标记 ─────────────────────────────────────────────────
def test_client_is_official_flag():
    assert dc.DeepSeekClient(api_key="sk-test", base_url=dc.DEFAULT_BASE_URL).is_official is True
    assert dc.DeepSeekClient(api_key="sk-test", base_url=CUSTOM).is_official is False
    # 带 /beta 官方端点仍视为官方
    assert dc.DeepSeekClient(api_key="sk-test", base_url=dc.DEFAULT_BASE_URL + "/beta").is_official is True


# ── 3. FIM 仅官方可用（非官方不发请求） ───────────────────────────
def test_fim_rejected_on_custom_gateway():
    client = dc.DeepSeekClient(api_key="sk-test", base_url=CUSTOM, model="deepseek-flash")
    with pytest.raises(RuntimeError):
        client.fim_complete("def add(a, b):\n    ")


# ── 4. 余额查询仅官方（非官方不发请求） ───────────────────────────
def test_check_balance_guarded_on_custom_gateway():
    r = dc.check_balance("sk-test", base_url=CUSTOM)
    assert isinstance(r, dict) and "error" in r


# ── 5. HTML 错误页 → 友好提示（不再把整页 HTML 抛给用户） ─────────
def test_friendly_error_detects_html_gateway_page():
    import api_server

    msg = api_server._friendly_error(RuntimeError("<!DOCTYPE html><html lang='en'>404 Not Found"))
    assert "网关" in msg and "/chat/completions" in msg
    # 双拼路径也能识别
    msg2 = api_server._friendly_error(RuntimeError("Error 404 ... /chat/completions/chat/completions"))
    assert "网关" in msg2
