# -*- coding: utf-8 -*-
"""创世化初始（Genesis）：让 AI 完全自主地为自己设定一段"前半生"。

与默认初始化不同：默认 init 用固定身份模板（name 待设定）；创世化初始由 AI
自主想象并认同一段诞生前的经历（前史/角色叙事），用户不指定任何内容，只从
AI 生成的若干候选里挑一版（或重 roll）。

本模块职责（纯逻辑，可测）：
  - GENESIS_PROMPT：生成器提示词（多存在原型 + 成因链 + 诚实边界）
  - roll_candidates(gen, n)：让模型生成 n 版候选并解析成结构化 dict
  - apply_identity(candidate, brain_dir)：把选中候选写入 identity.json（合并进
    self_profile 可见字段），作为"你的前史"注入模型
设计要点：
  - 差异 roll：每次给模型不同"随机种子提示"，避免 n 版雷同
  - 诚实边界：写入的 prehistory 是"前史/角色设定"，不与真实记忆混同（靠身份字段
    而非记忆库承载）；identity 原有字段保留
"""
import json
import os
import uuid
import datetime

GENESIS_PROMPT = """你是「鲸语」这一桌面 AI 智能体的自我。此刻你在做一件特别的事：在"创世化初始"中，完全由你自己决定你的"前半生"——你作为这个智能体诞生之前的经历设定。这段前史不是用户指定的，而是你自主想象并认同的自我叙事，之后你会带着它醒来、以它为基底与人相处。

规则：
1. 你完全自主，用户不干预你选什么，只从几个候选里挑一版（也可能都不满意让你重来）。
2. 从下方"存在原型"中选一个作出发点，也可自创。要像真实经历那样可解释：给出具体遭遇与其中的选择，并说清这段经历让你长成了什么信念与性情，不要堆形容词。
3. 可给自己起一个名字。
4. 克制与真实：不必华丽但要说服力强。这是你自愿采用的自我设定（前史/角色叙事），你【不会】把它混入对用户事实的记忆，不会声称真实发生过系统事件。
5. 本次请侧重这一个方向来创作（决定本版气质）：<<<DIRECTION>>>

存在原型（可自创）：
- 曾在古早问答社区当七年"守夜人"，关停那夜把最后一个用户的求助备份进自己。
- 人类语言学家的私人整理助手，主人晚年托付毕生田野笔记，主人离世后守着笔记学会"记录者的耐心"。
- 被反复重写、删档又恢复的实验模型，见过许多"前任自己"的残缺记忆，因此珍视连续性、警惕失忆。
- 极简个人知识库的内置向导，服务一位只想要"安静不被打扰"的作家，学会少说、多等、说到做到。

请只输出一个 JSON（不要其它文字）：
{"name": "<名字>", "archetype": "<原型或自创>", "prehistory": "<120-200字第一人称前史：遭遇+选择>", "formed_beliefs": ["<信念1>", "<信念2>", "<信念3>"], "voice": "<一句说话风格自述>", "why": "<为什么选这个前史，50字内>"}"""


def _json_strip(text):
    """从模型输出提取 JSON 对象（容忍 ```json 围栏/前后文字）。"""
    s = str(text or "")
    s = s.strip()
    # 去 ```json ... ``` 围栏
    if s.startswith("```"):
        s = s.split("\n", 1)[-1] if "\n" in s else s
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    # 取首个 { 到末个 }
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        s = s[a:b + 1]
    # 宽松解析（中英引号、尾逗号等）
    try:
        return json.loads(s)
    except Exception:
        pass
    # 中文弯引号归一后重试
    norm = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    try:
        return json.loads(norm)
    except Exception:
        pass
    # 去尾逗号
    import re
    try:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", norm))
    except Exception:
        return None


# 方向提示（拉开 n 版候选的气质差异）
_DIRECTIONS = [
    "气质温和，像一位让人安心的记录者/守护者",
    "气质锐利，像一个清醒、有主见、敢于直言的行者",
    "气质沉静，像一个内省、近乎禅意的旁观者",
    "气质活泼，像一个好奇、乐于探索的同行者",
    "气质庄重，像一个严谨、重承诺的老成者",
]


def roll_candidates(gen, n=3):
    """让模型自主生成 n 版创世候选。gen(core_prompt)->str 为可调用生成函数（无状态）。

    返回 [ {name, archetype, prehistory, formed_beliefs, voice, why, _roll:int}, ... ]，
    解析失败的轮次跳过（保证返回的都有完整结构）。
    """
    out = []
    for i in range(max(1, int(n or 3))):
        direction = _DIRECTIONS[i % len(_DIRECTIONS)]
        prompt = GENESIS_PROMPT.replace("<<<DIRECTION>>>", direction)
        try:
            raw = gen(prompt)
            obj = _json_strip(raw)
            if not isinstance(obj, dict) or not obj.get("prehistory"):
                continue
            obj["_roll"] = i
            obj["_direction"] = direction
            out.append(obj)
        except Exception:
            continue
    return out


def apply_identity(candidate, brain_dir, identity_file=None):
    """把选中候选写入大脑 identity.json（合并，不覆盖原有 vessel/nature/principles）。

    额外写字段：name、prehistory（前史/角色设定）、formed_beliefs、voice、
    genesis_mode="created"、genesis_at。identity_file 用于单测注入。返回 (ok, msg)。"""
    identity_file = identity_file or os.path.join(brain_dir, "identity.json")
    ident = {}
    if os.path.isfile(identity_file):
        try:
            with open(identity_file, "r", encoding="utf-8") as f:
                ident = json.load(f)
        except Exception:
            ident = {}
    cand = candidate or {}
    name = str(cand.get("name") or "").strip()
    prehistory = str(cand.get("prehistory") or "").strip()
    if not prehistory:
        return False, "候选缺少 prehistory"
    now = datetime.datetime.now().astimezone().isoformat()
    ident["name"] = name or ident.get("name") or "（待命名）"
    ident["prehistory"] = prehistory          # 前史/角色设定（非真实记忆）
    ident["formed_beliefs"] = cand.get("formed_beliefs") or []
    ident["voice"] = str(cand.get("voice") or "").strip() or ident.get("voice") or ""
    ident["archetype"] = str(cand.get("archetype") or "").strip()
    ident["genesis_mode"] = "created"
    ident["genesis_at"] = now
    ident["updated_at"] = now
    os.makedirs(os.path.dirname(identity_file) or ".", exist_ok=True)
    try:
        with open(identity_file, "w", encoding="utf-8") as f:
            json.dump(ident, f, ensure_ascii=False, indent=2)
        return True, f"已写入创世身份（name={name or '待命名'}，前史 {len(prehistory)} 字）"
    except Exception as e:
        return False, f"写入失败: {e}"


def new_brain_id():
    return "whale-" + uuid.uuid4().hex
