"""直播角色 v2：持续在线的 AI 主播（本地 P0）。

分层
----
- `events.py`    事件模型 + 总线（平台无关）
- `room.py`      直播间在场状态（观众/能量/情绪/话题/短期记忆）
- `attention.py` 这条事件值不值得回（"不接"是一等公民）
- `prompts.py`   角色卡 + 场景规则 + 房间状态 → 提示词
- `actor.py`     一句台词（多轮上下文、合规过滤、兜底）
- `voice.py`     TTS（后台合成，音频回填）
- `stage.py`     当前台词 / HUD 快照
- `director.py`  每秒一次的决策循环（回复 / 自主开麦 / 沉默）
- `simulator.py` 本地弹幕模拟器（没有平台也能验证"像不像活的"）

平台接入只需实现一个"把原生消息翻译成 LiveEvent"的适配器，
以上各层不需要改动。
"""

__all__ = ["actor", "attention", "director", "events", "prompts", "room", "stage", "voice"]
