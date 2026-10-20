"""生命循环（Life Loop）。

把 WhaleBrain 从"被调用才醒"推进到"有连续时间、内驱、代谢、睡眠"的个体：

- ``timeline``   唯一事件流（brain/timeline.jsonl），所有状态变化可追溯。
- ``state``      当前生命状态 + 单实例租约（brain/life.json、brain/life.lock）。
- ``drives``     五维内驱评分（不调用模型，纯本地信号）。
- ``intentions`` 自发意图的提出、执行、完成/失败。
- ``metabolism`` 每日 token/费用预算、疲劳与能量。
- ``sleep``      睡眠巩固：合并记忆、日结、自我模型刷新、进化提案。
- ``loop``       tick 状态机，由 api_server 的 30s 调度循环驱动。
- ``report``     连续性/自主性/完成率/成本等可量化指标。

设计约定：
- 这里不引入审批、白名单或沙箱；约束只来自内稳态（预算、疲劳、目标上限）。
- 所有路径都动态读取 ``brainkit.BRAIN_DIR``，以兼容 ``--brain`` / ``set_brain_dir``
  与测试中的临时大脑目录。
- 模型调用只发生在 reflect/sleep 且预算允许时，绝不随 tick 无条件调用。
"""


