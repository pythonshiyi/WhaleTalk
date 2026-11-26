"""消息布局与清洗（chat 拆分的 prompt 阶段起步）。

这里只放**无全局副作用**的纯函数：图片内容还原、reasoning 裁剪、非法历史清洗。
后续会把 json/记忆/图片提示的布局也迁进来。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("whaletalk")


def restore_text_content(m: Dict[str, Any]) -> Dict[str, Any]:
    """把发送时内联的图片内容块还原为纯文本 content（UI/存档只保留文本与图片路径）。"""
    if not (isinstance(m, dict) and isinstance(m.get("content"), list)):
        return m
    text_parts = []
    for b in m["content"]:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            text_parts.append(b["text"])
    m = dict(m)
    m["content"] = "\n".join(text_parts)
    return m


def prune_reasoning_for_send(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """发送时剥离「无工具调用轮次」的思考内容（官方多轮拼接规则）。

    官方规则：两个 user 消息之间，若模型未进行工具调用，中间 assistant 的
    reasoning_content 在后续轮次传入 API 会被忽略——不传即可省下这部分
    输入 token（思考内容可占数千 token，是纯浪费）。
    带 tool_calls 的轮次必须完整回传 reasoning_content（缺失会 400），
    因此只剥离「无 tool_calls 的 assistant 消息」。
    仅对浅拷贝生效，不影响调用方内存中的会话历史（UI/存档仍需 reasoning）。
    """
    cleaned = []
    for m in messages:
        if (
            isinstance(m, dict)
            and m.get("role") == "assistant"
            and not m.get("tool_calls")
            and m.get("reasoning_content")
        ):
            m = dict(m)
            m.pop("reasoning_content", None)
        cleaned.append(m)
    return cleaned


def sanitize_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """过滤非法消息：assistant 消息必须包含 content 或 tool_calls。

    中断生成或模型仅返回思考时会产生 content 为空的 assistant 消息，
    DeepSeek API 会以 400 (invalid_request_error) 拒绝此类历史记录。
    同时清理「悬空 tool_calls」：assistant 声明了工具调用但历史中
    没有配对 tool 消息（stop/异常中断的残留），不清理会导致后续 400。
    """
    has_tool = set()
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "tool" and m.get("tool_call_id"):
            has_tool.add(m["tool_call_id"])
    cleaned = []
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            cleaned.append(m)
            continue
        content = m.get("content")
        tcs = m.get("tool_calls")
        if tcs:
            valid = [
                tc for tc in tcs
                if isinstance(tc, dict) and tc.get("id") in has_tool
            ]
            if len(valid) != len(tcs):
                m = dict(m)
                if valid:
                    m["tool_calls"] = valid
                else:
                    m.pop("tool_calls", None)
        if not content and not m.get("tool_calls"):
            continue
        m = dict(m)
        if content is None:
            m["content"] = ""
        cleaned.append(m)
    # pass 2：保证「assistant(tool_calls) → 配对 tool 消息」严格相邻完整。
    # 历史保存错位（压缩裁剪/异常中断/并行轮次丢失 assistant 层）会产生孤立
    # tool 消息，或 assistant 声明了 tool_calls 却没有对应 tool 响应——两者
    # DeepSeek API 均以 400 拒绝（tool must be a response to preceding tool_calls）。
    # 规则：tool 消息必须紧跟在声明其 id 的 assistant 之后，否则丢弃；
    #       assistant 上未被满足的 tool_calls 一律剔除（必要时整条剔除）。
    final = []
    pending_msg = None      # final 中等待被响应、可原地修正的 assistant 副本
    pending_ids = set()

    def _drop_pending():
        nonlocal pending_msg, pending_ids
        if pending_msg is not None and pending_ids:
            kept = [
                tc for tc in (pending_msg.get("tool_calls") or [])
                if not (isinstance(tc, dict) and tc.get("id") in pending_ids)
            ]
            if kept:
                pending_msg["tool_calls"] = kept
            else:
                pending_msg.pop("tool_calls", None)
                if not pending_msg.get("content"):
                    try:
                        final.remove(pending_msg)
                    except ValueError:  # noqa: silent-except: pending 已被前序分支移除时忽略
                        pass
        pending_msg = None
        pending_ids = set()

    for m in cleaned:
        if not isinstance(m, dict):
            _drop_pending()
            final.append(m)
            continue
        role = m.get("role")
        if role == "assistant":
            _drop_pending()
            msg = dict(m)
            tcs = msg.get("tool_calls")
            if tcs:
                valid_tcs = [tc for tc in tcs if isinstance(tc, dict) and tc.get("id")]
                msg["tool_calls"] = valid_tcs
                if valid_tcs:
                    pending_msg = msg
                    pending_ids = {tc["id"] for tc in valid_tcs}
                else:
                    msg.pop("tool_calls", None)
            final.append(msg)
        elif role == "tool":
            tid = m.get("tool_call_id")
            if pending_msg is not None and tid and tid in pending_ids:
                pending_ids.discard(tid)
                final.append(m)
                if not pending_ids:
                    pending_msg = None
            else:
                continue  # 悬空 tool：丢弃，避免 400
        else:
            # 非 assistant 且非 tool（user/system）：此前的 tool_calls 已无法满足
            _drop_pending()
            final.append(m)
    _drop_pending()
    return final


@dataclass
class PreparedPrompt:
    """一次 chat 请求的消息布局结果（不包含 kwargs 采样参数）。"""

    work: List[Dict[str, Any]]
    base_work_len: int
    json_hint: Optional[Dict[str, Any]]
    memory_msg: Optional[Dict[str, Any]]
    image_hint_msg: Optional[Dict[str, Any]]
    trailing: str
    eff_model: str


class PromptBuilder:
    """消息清洗 + 图片内联 + 缓存友好布局。

    依赖通过构造注入，避免 chat/prompt 反向 import deepseek_client：
    - is_vision_model(model) -> bool
    - embed_images(messages, model) -> list
    - json_hint / image_hint 文案
    """

    def __init__(
        self,
        *,
        vision_model: str,
        is_vision_model: Callable[[str], bool],
        embed_images: Callable[[List[Dict[str, Any]], str], List[Dict[str, Any]]],
        json_hint: str,
        image_hint: str,
    ):
        self.vision_model = vision_model
        self.is_vision_model = is_vision_model
        self.embed_images = embed_images
        self.json_hint = json_hint
        self.image_hint = image_hint

    def prepare(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: str,
        is_official: bool,
        json_output: bool,
        memory_text: Optional[str],
        trailing_text: Optional[str],
    ) -> PreparedPrompt:
        messages[:] = sanitize_messages(messages)
        # 图片内联：仅替换受影响的 user 消息副本；原始消息对象（content 文本 +
        # images 路径）不受影响，chat() 结束时再同步回调用方（含新增 assistant/tool 消息）。
        # 统一模型原生多模态，默认路径不会走到兜底；此处仅对「自定义 OpenAI 兼容端点 +
        # 明确不支持图片的模型名」保留优雅降级，不抛错、不改全局配置。
        has_image = any(
            isinstance(m, dict) and m.get("images") and m.get("role") == "user"
            for m in messages
        )
        if has_image and not self.is_vision_model(model) and is_official:
            eff_model = self.vision_model
            logger.info("会话包含图片输入：本次请求自动改用多模态模型 %s（全局配置不变）",
                        self.vision_model)
        else:
            eff_model = model
        work = self.embed_images(messages, eff_model)
        # 记录「本轮新增消息」的起点：供上层做「只追加本轮」的断连兜底落盘。
        base_work_len = len(work)
        json_hint_msg = None
        memory_msg = None
        image_hint_msg = None
        # 缓存友好消息布局（官方硬盘缓存按「前缀完整匹配」命中）：
        # - 恒定的 json_hint 保持在最前（前缀稳定 → 可命中）
        # - 系统提示词 messages[0] 紧随其后（稳定前缀主体）
        # - 可能变化的记忆注入追加在末尾（system 消息位置任意）
        if json_output:
            json_hint_msg = {"role": "system", "content": self.json_hint}
            work = [json_hint_msg] + work
        if memory_text:
            memory_msg = {"role": "system", "content": memory_text}
            work = work + [memory_msg]
        # 图片须知（仅「本轮用户刚发图」时注入，临时 system 消息，不写回历史）
        last_user = None
        for m in messages:
            if isinstance(m, dict) and m.get("role") == "user":
                last_user = m
        if last_user and last_user.get("images"):
            paths = [
                str(p) for p in last_user.get("images")
                if isinstance(p, str) and str(p).strip()
            ][:5]
            hint = self.image_hint
            if paths:
                hint += "\n当前图片路径：\n" + "\n".join("- " + p for p in paths)
            image_hint_msg = {"role": "system", "content": hint}
            work = work + [image_hint_msg]
        return PreparedPrompt(
            work=work,
            base_work_len=base_work_len,
            json_hint=json_hint_msg,
            memory_msg=memory_msg,
            image_hint_msg=image_hint_msg,
            trailing=str(trailing_text or ""),
            eff_model=eff_model,
        )
