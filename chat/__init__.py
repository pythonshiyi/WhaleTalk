"""chat 分层包（P1）：把 DeepSeekClient.chat 拆成可单测的阶段。

当前已抽出：
- metrics：本轮 TTFT / 生成耗时 / token / 轮次聚合

后续阶段（按 ROADMAP）：
prompt / stream / tools / loop / persist
"""
