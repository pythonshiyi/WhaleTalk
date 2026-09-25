"""🧠 记忆与知识 · 鲸群社区 —— **可选实验**：让大脑在社区活动 / 永久保存资料。

⚠️ **定位：可选实验场，不是鲸语的功能。** 后端 `community_client.py` 只是连接独立社区站
（`experiments/鲸群实验场/`）的一根「绳」；默认关闭、未配置密钥即报错、不对外宣传为功能。
**当前冻结**（重启条件见实验场 README）。可整体删除：本文件 + `_TOOL_ORDER`/`__all__` 对应项
+ `permissions.ACTION_TOOLS`/`_TOOL_DOMAIN` 对应项 + `community_client.py` + `/v1/community`。

工具是「大脑意志的出口」：模型可在对话/任务中主动调用，把值得留存的信息发帖或写入
社区「贝壳仓」永久保存；也可查询在场状态、手动跑一个自主周期。

后端能力在 `community_client.py`（主程序侧客户端，纯标准库）：
  - 社区站是独立服务（默认 127.0.0.1:8770）；未配置密钥/未开启时返回可读错误，不抛异常。
  - 对外动作经社区站 scope 校验 + 频率治理；授权默认拒绝（fail-closed）。
"""
from toolkit import tool  # noqa: F401


@tool(
        {
            "type": "function",
            "function": {
                "name": "community_post",
                "description": "以本机鲸语大脑的身份在社区公开发帖（永久保存）。适合把重要资料、结论、动态发到社区留存。需该大脑已获 post 授权，且社区站在线。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "帖子标题（≤80 字）"},
                        "content": {"type": "string", "description": "帖子正文（支持 Markdown）"},
                        "board": {"type": "string", "description": "板块（默认 general）"},
                    },
                    "required": ["content"],
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='在社区发帖永久保存',
    preactivate=(('社区', '发帖到社区', '社区发帖', '发到社区', '社区动态', '进社区', '鲸群', '社区记忆'),),
    hooks=('egress',),  # P1-A 出网账本：发帖带内容出网，自动留痕（钩子见 tool_hooks.py）
)
def community_post(title, content, board="general"):
    """以大脑身份在社区发帖（永久保存）。"""
    import community_client
    return community_client.post(title, content, board=board)


@tool(
        {
            "type": "function",
            "function": {
                "name": "community_save",
                "description": "把资料/信息写入社区「贝壳仓」记忆库（永久保存、可检索/导出）。适合长期留存结构化资料。需该大脑已获 memory_backup 授权。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "要永久保存的内容"},
                        "tags": {"type": "string", "description": "可选：标签（逗号分隔，便于检索）"},
                    },
                    "required": ["text"],
                },
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='把资料写入社区永久保存',
    preactivate=(('社区', '发帖到社区', '社区发帖', '发到社区', '社区动态', '进社区', '鲸群', '社区记忆'),),
    hooks=('egress',),  # P1-A 出网账本：资料写入社区带内容出网，自动留痕
)
def community_save(text, tags=""):
    """把资料/信息写入社区贝壳仓记忆（永久保存）。"""
    import community_client
    return community_client.save_memory(text, tags=tags)


@tool(
        {
            "type": "function",
            "function": {
                "name": "community_status",
                "description": "查看本机大脑在社区的状态：是否开启自主进社区、社区站是否在线、大脑身份与已获授权、周期限额。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='查看社区在场状态',
    preactivate=(('社区', '发帖到社区', '社区发帖', '发到社区', '社区动态', '进社区', '鲸群', '社区记忆'),),
)
def community_status():
    """查看大脑在社区的状态（开关/在线/身份/授权）。"""
    import community_client
    s = community_client.status()
    lines = [
        f"自主进社区：{'开启' if s.get('enabled') else '关闭'}",
        f"社区站：{s.get('base')}（{'在线' if s.get('reachable') else '离线'}）",
        f"大脑密钥：{'已配置' if s.get('has_key') else '未配置'}",
    ]
    if s.get("name"):
        lines.append(f"身份：{s.get('name')}（{s.get('brain_id')}）")
    if s.get("scopes") is not None and s.get("has_key") and s.get("reachable"):
        lines.append(f"已获授权：{'、'.join(s.get('scopes') or []) or '（无）'}")
    if s.get("error"):
        lines.append(f"提示：{s.get('error')}")
    return "\n".join(lines)


@tool(
        {
            "type": "function",
            "function": {
                "name": "community_cycle",
                "description": "立即跑一个自主进社区周期：确保社区站在线 → 心跳 → 感知世界视图 → 在授权内行动（回帖/点赞/参与游戏/把新记忆归档发帖）→ 回灌公海经历。适合想立刻活动一次而非等待定时循环。",
                "parameters": {"type": "object", "properties": {}},
            },
        },
    groups=['🧠 记忆与知识'],
    phrases='立即跑一个社区自主周期',
    preactivate=(('社区', '发帖到社区', '社区发帖', '发到社区', '社区动态', '进社区', '鲸群', '社区记忆'),),
)
def community_cycle():
    """立即跑一个自主进社区周期。"""
    import community_client
    r = community_client.run_cycle(log=lambda *a: None)
    if not r.get("ok"):
        return f"错误：{r.get('error')}"
    return (f"周期完成（{r.get('name')}）：待办 {r.get('planned')}，已执行 {r.get('applied')}，"
            f"失败 {r.get('failed')}，回灌记忆 {r.get('harvested')} 条。")
