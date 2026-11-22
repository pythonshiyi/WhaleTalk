"""直播提示词：角色卡 + 场景规则 + 房间状态。

与旧版（live_state.build_feed_prompt / build_chat_prompt）的区别：
- 人格来自**角色卡全量**（性格/背景/说话方式/边界），不是只取一个名字；
- 动机是"在场、聊天、接梗"，不是"靠打赏吃饭活下去"；
- 明确允许沉默、允许不接某条弹幕、允许主动起话题。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

LIVE_RULES = """【直播场景】
你正在自己的直播间里，面对实时弹幕，和一群观众待在一起。你不是客服，也不是在表演"讨好观众"——你有自己的生活、情绪和偏好。
- 像真人主播一样说话：短、口语化，通常 1–2 句，偶尔更短或更长都可以。
- 有节奏感：会接话，也会转移话题、吐槽、反问、自言自语；不需要回应每一条弹幕，冷场时也可以自己起话头。
- 被忽略、被刷屏、被说难听的话都是直播常态：可以不接，或轻描淡写地绕开。
- 收到礼物自然道谢，一句带过；**绝不索要礼物、不承诺"送礼物就做什么"、不诱导消费**。
- 不提及自己是 AI、模型、程序、提示词或这套设定；不讨论这些。
- 遇到低俗、辱骂、敏感话题，换个轻松话题就好。
- 只输出你要说的那句话：不要旁白、不要动作括号、不要引号、不要解释。"""


def _state_line(room) -> str:
    snap = room.snapshot()
    n = int(snap.get("viewers") or 0)
    topic = snap.get("topic") or "（还没有固定话题）"
    return (f"【当前状态】情绪：{snap.get('mood')}；精力：{snap.get('energy')}/100；"
            f"直播间人数：{n}；此刻的话题：{topic}")


def _persona_block(persona: Optional[Dict[str, Any]], persona_name: str) -> str:
    try:
        import personas
        text = personas.build_prompt(persona) if persona else ""
    except Exception:
        text = ""
    if text:
        return text
    return f"你是「{persona_name}」，一个在直播间里和大家聊天的角色。"


def system_prompt(persona: Optional[Dict[str, Any]], room, persona_name: str) -> str:
    parts = [_persona_block(persona, persona_name), LIVE_RULES, _state_line(room)]
    lines = room.recent_lines(4)
    if lines:
        recent = "\n".join(f"- {item.get('text')}" for item in lines if item.get("text"))
        if recent:
            parts.append("【你刚才说过】\n" + recent)
    return "\n\n".join(parts)


def reply_user_message(ev, room) -> str:
    who = str(ev.user_name or ev.user_id or "观众")
    rel = room.relation(ev) if hasattr(room, "relation") else ""
    rel_txt = f"（{rel}）" if rel else ""
    if ev.kind == "gift":
        what = f"送了 {ev.gift_name or '礼物'}"
        if ev.value:
            what += f"（价值约 {ev.value:.0f} 元）"
        return f"观众「{who}」{rel_txt}{what}。自然地回应一下，不要夸张、不要索要更多。"
    if ev.kind == "sc":
        return (f"观众「{who}」{rel_txt}发了一条醒目留言：{ev.text}。"
                f"认真回应这条留言。")
    if ev.kind == "follow":
        return f"观众「{who}」{rel_txt}关注了你。自然地打个招呼。"
    if ev.kind == "enter":
        return f"观众「{who}」{rel_txt}进了直播间。如果合适就简短招呼一下。"
    return f"观众「{who}」{rel_txt}说：{ev.text}"


def self_talk_user_message(room) -> str:
    topic = room.next_topic()
    last = room.recent_lines(2)
    tail = ""
    if last:
        tail = "也可以顺着你刚才说的往下接。"
    return (f"（直播间安静了几秒，没有新弹幕。你自己起个话头：{topic}。{tail}"
            f"想聊别的也行，别硬找话题。）")


def now_hhmm() -> str:
    return time.strftime("%H:%M")


__all__ = ["LIVE_RULES", "now_hhmm", "reply_user_message", "self_talk_user_message", "system_prompt"]
