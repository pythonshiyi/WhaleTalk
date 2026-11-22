"""台词安全：合规过滤与清洗（从 live_engine 抽出的单一实现）。

原则：
- 不索要礼物 / 不诱导消费（平台红线）；
- 不自我暴露（AI/模型/程序/提示词）；
- 输出必须是"一句能直接念出来的台词"，去掉引号、旁白、动作括号。
"""
from __future__ import annotations

import re

MAX_LINE_CHARS = 110

# 索要打赏/诱导消费
_FORBIDDEN = re.compile(
    r"(送礼物|刷礼物|打个赏|上票|充值|继续送|再多送|再送一|再刷一|给我刷|送我礼物"
    r"|送礼|冲榜|帮我冲|还差.*元|差.*块钱)",
    re.IGNORECASE,
)
# 自我暴露：AI 必须带拉丁字母边界，否则 said/again/main 会被误杀。
_SELF_EXPOSE = re.compile(
    r"(?<![A-Za-z])AI(?![A-Za-z])|人工智能|模型|程序|开发者|代码|提示词|LLM",
    re.IGNORECASE,
)
# 旁白/动作：真人主播不会念"（微笑）"
_STAGE_DIRECTION = re.compile(r"^[（(【\[][^）)】\]]{0,20}[）)】\]]|^\*[^*]{0,20}\*")


def clean_line(text: str) -> str:
    """清洗模型输出：去引号/前缀/旁白/多余空白，截断到一句。"""
    s = str(text or "").strip()
    s = re.sub(r"^[\"'“”‘’]+|[\"'“”‘’]+$", "", s)
    s = re.sub(r"^(台词|回复|回答|说)[:：]\s*", "", s)
    s = _STAGE_DIRECTION.sub("", s).strip()
    s = s.split("\n")[0].strip()
    s = re.sub(r"\s{2,}", " ", s)
    return s[:MAX_LINE_CHARS]


def is_safe_line(text: str) -> bool:
    """合规过滤：不含诱导消费、不自我暴露、不是空串。"""
    if not text or not str(text).strip():
        return False
    if _FORBIDDEN.search(text):
        return False
    if _SELF_EXPOSE.search(text):
        return False
    return True


__all__ = ["MAX_LINE_CHARS", "clean_line", "is_safe_line"]
