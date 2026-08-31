# ── quiet_mode「纯净对话」门控单元测试 ─────────────────
# 验证：开启纯净对话后，三路个性上下文（长期记忆/核心自我/大脑）全部停止注入；
#       关闭时正常注入；_chat_kwargs 正确透传 quiet_mode（body 优先、cfg 兜底）。
# 运行：python tests/test_quiet_mode.py（仓库根目录）
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import api_server
import deepseek_client as dc

failed = 0
def check(cond, name):
    global failed
    if cond:
        print("PASS:", name)
    else:
        print("FAIL:", name)
        failed += 1

# 无副作用构造 handler 实例（绕过 __init__，只测方法本身）
h = api_server._Handler.__new__(api_server._Handler)

# ── 桩：三路注入源全部返回“有内容” ──
api_server._memory_full = lambda: {"facts": [{"text": "用户偏好中文回复"}, {"text": "项目采用纯静态架构"}]}
dc.self_profile = lambda *a, **k: "[核心自我状态] 我是鲸语，专注而冷静。"
import brain_api
brain_api.brain_context = lambda: "[大脑上下文] 身份：鲸语；近期记忆：正在开发纯净对话开关。"

def inject(quiet_mode, memory_enabled=True, pure_chat=True):
    cfg = {"memory_enabled": memory_enabled, "quiet_mode": quiet_mode}
    return h._inject_system_messages([{"role": "user", "content": "你好"}], cfg, pure_chat, quiet_mode)

# ── 用例 1：quiet_mode=True → 三路全停（memory_text=None）──
msgs, mem = inject(quiet_mode=True, memory_enabled=True)
check(mem is None, "纯净对话开启：memory_text 为空（不注入任何个性上下文）")
sys_txt = "".join(m.get("content", "") for m in msgs if m.get("role") == "system")
check("[长期记忆]" not in sys_txt, "纯净对话开启：不注入长期记忆")
check("核心自我状态" not in sys_txt, "纯净对话开启：不注入核心自我")
check("[大脑上下文]" not in sys_txt, "纯净对话开启：不注入大脑上下文")

# ── 用例 2：quiet_mode=False → 三路正常注入 ──
msgs, mem = inject(quiet_mode=False, memory_enabled=True)
check(mem is not None, "纯净对话关闭：memory_text 有内容")
check("[长期记忆]" in mem and "用户偏好中文回复" in mem, "注入长期记忆")
check("核心自我状态" in mem, "注入核心自我状态")
check("[大脑上下文]" in mem, "注入大脑上下文")

# ── 用例 3：quiet_mode=False 但 memory_enabled=False → 记忆停、自我+大脑照常 ──
msgs, mem = inject(quiet_mode=False, memory_enabled=False)
check("[长期记忆]" not in (mem or ""), "memory_enabled=False：不注入记忆")
check("核心自我状态" in (mem or ""), "memory_enabled=False：核心自我仍注入（受纯净对话总开关控制）")
check("[大脑上下文]" in (mem or ""), "memory_enabled=False：大脑仍注入（受纯净对话总开关控制）")

# ── 用例 4：_chat_kwargs 透传 quiet_mode（body 优先）──
k = h._chat_kwargs({"quiet_mode": True}, {"quiet_mode": False})
check(k["quiet_mode"] is True, "_chat_kwargs：body quiet_mode=True 优先于 cfg=False")
k = h._chat_kwargs({}, {"quiet_mode": True})
check(k["quiet_mode"] is True, "_chat_kwargs：body 缺省时回退 cfg.quiet_mode=True")
k = h._chat_kwargs({}, {})
check(k["quiet_mode"] is False, "_chat_kwargs：body/cfg 都缺省时默认 False")

# ── 用例 5：任务模式（pure_chat=False）下 quiet_mode 同样生效 ──
msgs, mem = inject(quiet_mode=True, pure_chat=False)
sys_txt = "".join(m.get("content", "") for m in msgs if m.get("role") == "system")
check("[长期记忆]" not in sys_txt and "核心自我状态" not in sys_txt and "[大脑上下文]" not in sys_txt,
      "任务模式 + 纯净对话：三路个性上下文同样全停")

if failed:
    print(f"\n❌ {failed} 组失败")
    sys.exit(1)
print("\n✅ quiet_mode 纯净对话门控全部通过")
