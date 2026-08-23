# -*- coding: utf-8 -*-
"""WebUI 启动引导测试：右侧「参数」控制台加载问题。

背景：网页版程序启动后，右侧「参数」面板长时间显示「加载中…」，需切换标签才加载。
根因（前端 api.js）：api() 使用 localStorage 里缓存的 token 直接发请求，
但 token 只有 checkBackend() 执行后才会写入 localStorage。
启动早期（预取 / ParamsTab 首次挂载）在 token 就绪前就发出请求，
携带空 Bearer → 401 → .catch(() => {}) 静默丢弃 → 面板永远停在「加载中…」。

本测试在后端复现同一竞态（无 token → 401 → 自取 token → 重试成功），
并验证 token 自取 / 401 恢复链路（/v1/config、/v1/context、/v1/status 三接口）。
"""
import json
import os
import sys
import urllib.request
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api_server


class TestConfigBootstrap:
    """后端引导可用性（网页版 ParamsTab 依赖的三接口）。"""

    @classmethod
    def setup_class(cls):
        cls.port, cls.token, err = api_server.start_server(8746, "bootstrap-secret")
        assert err is None
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def teardown_class(cls):
        api_server.stop_server()

    def _get(self, path, token):
        req = urllib.request.Request(
            f"{self.base}{path}",
            headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_unauthorized_without_token(self):
        """无 token 直接请求参数接口 → 401（复现前端启动早期空 token 场景）。"""
        for path in ("/v1/config", "/v1/context", "/v1/status"):
            req = urllib.request.Request(f"{self.base}{path}")
            try:
                urllib.request.urlopen(req, timeout=5)
                raise AssertionError(f"{path} 未鉴权竟然返回 200")
            except urllib.error.HTTPError as e:
                assert e.code == 401, f"{path} 期望 401，实际 {e.code}"

    def test_token_self_fetch(self):
        """/v1/token 免鉴权自取，与启动写入的 token 一致（前端 checkBackend 依赖）。"""
        with urllib.request.urlopen(f"{self.base}/v1/token", timeout=5) as r:
            assert r.status == 200
            assert json.loads(r.read().decode("utf-8"))["token"] == self.token

    def test_params_endpoints_with_token(self):
        """拿到 token 后，参数控制台三接口全部可读（非空数据）。"""
        for path in ("/v1/config", "/v1/context", "/v1/status"):
            status, body = self._get(path, self.token)
            assert status == 200, path
            assert body, f"{path} 返回空数据"

    def test_bootstrap_without_prior_token(self):
        """完整引导链路：无 token → 自取 → 重试成功。

        模拟前端 401 后 _selfFetchToken() + 重试的路径。
        """
        # 1) 无 token → 401
        req = urllib.request.Request(f"{self.base}/v1/config")
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("未带 token 竟然成功")
        except urllib.error.HTTPError as e:
            assert e.code == 401
        # 2) 自取 token
        with urllib.request.urlopen(f"{self.base}/v1/token", timeout=5) as r:
            fetched = json.loads(r.read().decode("utf-8"))["token"]
        assert fetched == self.token
        # 3) 用自取 token 重试 → 200
        status, body = self._get("/v1/config", fetched)
        assert status == 200
        assert body.get("models") or body.get("model")


class TestConfigShape:
    """返回结构是否满足 ParamsTab 渲染所需字段（防「加载中」持续）。"""

    @classmethod
    def setup_class(cls):
        cls.port, cls.token, err = api_server.start_server(8746, "shape-secret")
        assert err is None
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def teardown_class(cls):
        api_server.stop_server()

    def _get(self, path):
        req = urllib.request.Request(
            f"{self.base}{path}",
            headers={"Authorization": f"Bearer {self.token}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))

    def test_config_has_panel_fields(self):
        cfg = self._get("/v1/config")
        for key in ("model", "thinking", "scenario", "max_tokens", "base_url",
                    "temperature", "top_p", "seed", "has_key",
                    "models", "thinking_modes", "scenarios"):
            assert key in cfg, f"/v1/config 缺少 ParamsTab 所需字段 {key}"

    def test_status_has_cost_field(self):
        st = self._get("/v1/status")
        assert "monthly_cost" in st, "/v1/status 缺少 monthly_cost（成本统计卡依赖）"
