# -*- coding: utf-8 -*-
"""P2-8 回归：/v1/token 发 token 的放行判定（Origin 白名单 + 无 Origin 时 Host 回环校验）。

覆盖 _token_request_allowed / _host_is_loopback 纯函数判定矩阵：
- 带白名单 Origin → 放行（dev/同源；无论 Host 是什么）
- 带非白名单 Origin（恶意网页跨源 / sandboxed iframe 的 null）→ 拒绝
- 无 Origin + 回环 Host（127.0.0.1 / localhost / [::1]，任意端口）→ 放行
- 无 Origin + 非回环 Host（DNS rebinding 域名 / 内网 IP / 无 Host）→ 拒绝
"""
import api_server as srv


def _allow(origin, host):
    return srv._token_request_allowed(origin, host)


class TestTokenOrigin:
    def test_whitelisted_origin_allowed(self):
        assert _allow("http://127.0.0.1:8745", "")
        assert _allow("http://localhost:8745", "")
        # vite dev 白名单（代理转发场景 Host 可能五花八门，Origin 命中即放行）
        assert _allow("http://localhost:5173", "attacker.com:8745")

    def test_evil_origin_rejected(self):
        assert not _allow("http://evil.example", "localhost:8745")
        assert not _allow("https://attacker.com", "127.0.0.1:8745")
        assert not _allow("null", "localhost:8745")  # sandboxed iframe

    def test_no_origin_no_host_rejected(self):
        assert not _allow("", "")


class TestHostLoopback:
    def test_loopback_hosts_accepted(self):
        for h in (
            "127.0.0.1:8745", "localhost:8745", "[::1]:8745",
            "localhost", "127.0.0.1", "localhost:5173", "[::1]",
        ):
            assert srv._host_is_loopback(h), h

    def test_non_loopback_rejected(self):
        for h in (
            "attacker.com:8745", "example.com", "127.0.0.2:8745",
            "192.168.1.5:8745", "0.0.0.0:8745", "",
        ):
            assert not srv._host_is_loopback(h), h


class TestTokenDecision:
    def test_no_origin_loopback_host_allowed(self):
        assert _allow("", "127.0.0.1:8745")
        assert _allow("", "localhost:8745")
        assert _allow("", "[::1]:8745")

    def test_no_origin_rebinding_host_rejected(self):
        # DNS rebinding：恶意域名解析到 127.0.0.1，但 Host 头仍是恶意域名 → 拒绝
        assert not _allow("", "attacker.com:8745")
        assert not _allow("", "evil.com")
        assert not _allow("", "intranet.local:8745")
