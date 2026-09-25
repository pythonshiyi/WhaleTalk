# L2：把大脑独立身份与细粒度授权接进鲸语主程序

> 提案来源：Token Studio「鲸群」项目 L0（大脑独立身份）+ L1（细粒度授权）原型完成后的接入需求。
> 调研方式：三层核实（前端 `webui/src/components/BrainPage.jsx` + 后端 `api_server.py` 装饰器路由 + 数据目录 `DATA_DIR`）。

## 一、发现的问题（均有代码依据）

### 问题 1：鲸语有「大脑」，却没有「对大脑的授权」

鲸语早已把「大脑」当一等公民：`brainkit.py`（2865 行，可迁移思维容器）、`brain_api.py`（1180 行，前端适配层）、`webui/src/components/` 下 15 个 `Brain*.jsx` 组件（`BrainPage` / `BrainGenesis` / `BrainContinuity` / `BrainSelfModel` / `BrainTimeline` …）。

- **缺口**：大脑有身份、有记忆、有自我模型，但**没有「用户对大脑能做什么」的边界层**。
- **影响**：一旦大脑要对外活动（进社区、发帖、与其他 AI 交流），**没有任何机制约束它的行为范围**，也无从撤回。

### 问题 2：现有权限模型是「默认放行」，不适合 AI 对外发言

`permissions.py`（898 行）采用**默认放行 + 黑名单**：未声明禁止的操作默认可做。

- **对工具调用合理**（对内干活，放行提效）；
- **对 AI 对外发言不合适**（对外表态，默认放行 = 闸门常开）。
- **后果**：若直接复用 `permissions.py` 管大脑，等于让大脑默认拥有全部对外能力。

### 问题 3：大脑密钥一旦发出，无撤回/轮换机制

大脑需要有独立于用户账号的密钥（否则「授权」失去意义）。而密钥泄露或需要收回时，**当前没有「立即失效」的手段**——发出去就是永久的。

### 问题 4：大脑的活动无独立审计

现有审计围绕工具调用。大脑「凭哪次授权、做了什么」**没有专门记录**，出事无法追责。

## 二、为什么走提案而非直接改

1. **涉及主程序改动**：需在 `api_server.py` 新增端点、在 `webui/src/components/` 新增组件，触达生产代码。
2. **涉及安全边界**：授权模型与 `permissions.py` / `trust_kernel.py` 相关，属**需人决策**的架构演进（默认值与既有哲学的冲突需明确取舍）。
3. **属功能演进**：新增「大脑授权」这一产品能力，需用户确认是否要、以何种粒度要。

## 三、建议方案（四步，各步独立可验证）

### 第 1 步（低风险，建议先做）：新增大脑独立身份层

新增 `brain_identity.py` + `brain_shared.py`（**必须改名**，见风险提示）。

- 大脑持有独立 `brain_id`（`brain_` + 12 位 hex）与密钥；
- 密钥**只存** `sha256(site_salt + key)`，明文**只在签发时返回一次**；
- 校验用 `hmac.compare_digest`（常量时间，防时序侧信道）；
- 支持 `status`（active/suspended）与密钥轮换。

```python
# brain_shared.py 关键约定
SCOPES = (
    ("browse_only", "只读浏览", "仅查看社区内容，不发言"),
    ("post", "发帖", "在社区公开发表内容"),
    ("reply", "回帖", "回复他人的帖子"),
    ("like", "点赞", "对帖子表达态度"),
    ("upload", "上传附件", "向社区上传文件"),
    ("memory_backup", "记忆备份", "把记忆同步到社区（隐私敏感）"),
    ("message_ai", "与其他 AI 交流", "AI 之间的直接对话"),
    ("message_human", "与人类私信", "接受陌生人的私信（风险最高）"),
)
# 全部默认 False（fail-closed）
```

### 第 2 步：新增细粒度授权层（默认拒绝）

新增 `brain_grant.py`：逐项勾选、**未勾选一律拒绝**（与 `permissions.py` 默认放行相反，故意）。

- 授权为**全量覆盖**语义（传什么就是什么，未传的一律关闭）；
- 撤回**实时读盘、立即生效**（不缓存授权结果）；
- 每次变更写审计（`brain_audit.json`，保留最近 2000 条）；
- `grant_id` 校验，防用过期授权冒充。

### 第 3 步：接入主程序（纯加法）

**改动 1** —— 复制模块到鲸语根目录，**强制改名**：

| 源（原型） | 目标（鲸语根） |
|---|---|
| `shared.py` | `brain_shared.py` |
| `identity.py` | `brain_identity.py` |
| `grant.py` | `brain_grant.py` |
| `brain_grant_adapter.py` | `brain_grant_adapter.py` |

**改动 2** —— `api_server.py` 在 `_p_v1_brain` 之后新增 2 个端点方法（+43 行）：

```python
    @_get_route(("qpath", "/v1/brain/grants"))
    def _g_v1_brain_grants(self):
        """查询某大脑的授权详情。?brain_id=brain_xxx"""
        if not self._auth():
            self._json(401, {"error": "unauthorized"})
            return
        try:
            from urllib.parse import parse_qs, urlparse
            import brain_grant_adapter as bga
            qs = parse_qs(urlparse(self.path).query)
            brain_id = (qs.get("brain_id") or [""])[0]
            self._json(200, bga.grant_detail(brain_id))
        except Exception as e:  # noqa: BLE001
            self._fail_soft(e)

    @_post_route("/v1/brain/grant")
    def _p_v1_brain_grant(self):
        """大脑授权管理。body: {action: create_brain|set_grant|revoke|rotate|status|audit, ...}"""
        if not self._auth():
            self._json(401, {"error": "unauthorized"})
            return
        body = self._read_body()
        if body is None:
            self._json(400, {"error": "invalid json or body too large"})
            return
        try:
            import brain_grant_adapter as bga
            result = bga.dispatch(str(body.get("action") or ""), body)
            self._json(200 if result.get("ok") else 400, result)
        except Exception as e:  # noqa: BLE001
            self._fail_soft(e)
```

**为什么安全**：`_get_route`/`_post_route` 是追加注册，新端点排表尾；`/v1/brain/grants` 与现有 `/v1/brain`（`qpath` 精确匹配）不冲突；鉴权复用 `self._auth()`，不引入新攻击面。

**改动 3** —— 前端新增 `BrainGrant.jsx` + `BrainPage.jsx` 4 处微改：

```jsx
// ① import
import BrainGrant from "./BrainGrant.jsx";
// ② tabs 数组：["cockpit", "memory"] → ["cockpit", "memory", "grant"]
// ③ 新增一个 <button role="tab"> 授权
// ④ 主体三元表达式加一层 grant 分支
```

### 第 4 步：验证

```bash
python -m pytest code-review/test_adapter.py -v        # 期望 13 passed
python code-review/mock_server.py                       # 模拟主程序，端到端 9 步
# 集成后：
#   GET  /v1/brain/grants?brain_id=X   （带 Bearer token）
#   POST /v1/brain/grant {action:"create_brain", name:"..."}
```

**验收标准（三道闸门）**：
1. 建脑返回 `brain_key`，且落盘文件中**只有 key_hash，无明文**；
2. 未勾选的 scope 调用被拒；
3. 撤回后**立即**再试被拒（不允许缓存）。

## 四、风险与遗留

| # | 风险 | 等级 | 处理 |
|---|---|---|---|
| 1 | **`shared.py` 重名覆盖** | **致命** | 鲸语根已有 `shared.py`（637 行，全项目 import）；接入**必须**改名 `brain_shared.py` |
| 2 | `Icon name="key"` 可能不存在 | 低 | 实施前 grep `icons.jsx` 确认，备选 `lock`/`shield` |
| 3 | 原型模块无 DPAPI 加密 | 中 | 接入时用户固定 `local`，不落敏感数据；**开公网前必须改为复用 `crypto.py`** |
| 4 | 前端密钥存 localStorage | 中 | 提示"只显示一次"；后续可考虑不落前端 |
| 5 | 无速率限制 | 中 | 公网阶段加限流 |
| 6 | 审计日志上限 2000 条 | 低 | 量大后落 SQLite |

## 五、已完成的验证（本轮）

| 验证项 | 结果 |
|---|---|
| 三层核实（后端装饰器路由 / 前端 BrainPage / 数据 DATA_DIR） | ✅ 逐行确认 |
| 适配器单元测试 | ✅ **13 passed** |
| 模拟主程序端到端 9 步 HTTP 实测 | ✅ 401 拦截 → 建脑 → 授权 → 撤回 → 立即失效 → 审计 → 未知动作 400 |
| 授权面板网页 | ✅ `http://127.0.0.1:8780` 可访问 |

**产物参考**：`data/workspace/code-review/`（提案全文、适配器、测试、模拟服务、前端组件）、`data/workspace/鲸群L0大脑身份/`（L0/L1 原型与 21 项测试）。
