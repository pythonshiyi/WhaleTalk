"""本地跑通 P0：模拟弹幕 + 导演循环 + 台词打印（不依赖平台、不推流）。

用法（项目根目录）：
    python -m live.simulate                      # 默认 120 秒，语音关闭
    python -m live.simulate --speed 2 --idle 12  # 快进、缩短冷场阈值
    python -m live.simulate --voice              # 同时合成语音（需要 TTS 环境）
    python -m live.simulate --room data/live_room.local.json   # 落盘观众记忆

看什么：
    - 她是否只在值得回的时候回（刷屏/复读应该被无视）；
    - 冷场时是否会自己起话头；
    - 台词是否像"一个人"而不是客服。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)


def main() -> int:
    ap = argparse.ArgumentParser(description="本地模拟直播（P0）")
    ap.add_argument("--speed", type=float, default=1.0, help="模拟速度倍率（默认 1）")
    ap.add_argument("--seconds", type=float, default=120.0, help="运行时长，0=一直跑到 Ctrl+C")
    ap.add_argument("--idle", type=float, default=25.0, help="冷场多少秒后自主开麦（默认 25）")
    ap.add_argument("--persona", default="", help="角色卡 id（默认第一个角色）")
    ap.add_argument("--room", default="", help="观众/房间记忆落盘路径（默认仅内存）")
    ap.add_argument("--config", default="", help="config.json 路径（默认仓库根 config.json）")
    ap.add_argument("--voice", action="store_true", help="合成语音（默认关闭）")
    ap.add_argument("--no-loop", action="store_true", help="剧本只播一遍")
    ap.add_argument("--dry", action="store_true", help="不调模型，用假台词验证链路（离线自检）")
    args = ap.parse_args()

    import live.director as director_mod
    import live.stage as stage
    import live.voice as voice
    from live.room import Room
    from live.simulator import Simulator

    voice.ENABLED = bool(args.voice)
    # CLI 没有走 api_server 的启动装配，这里补上配置路径（否则拿不到 API Key，
    # 台词会全部走兜底，看起来像"模型没生效"）。
    import config_utils
    cfg_path = args.config or os.path.join(BASE_DIR, "config.json")
    if os.path.exists(cfg_path):
        config_utils.DEFAULT_CONFIG_PATH = cfg_path
    else:
        print(f"提示：未找到 {cfg_path}，台词将使用兜底（可用 --dry 明确走离线模式）")
    if args.dry:
        import live.actor as live_actor
        # 注意：不要用括号包住——clean_line 会把"（…）"当成旁白剥掉
        live_actor.generate = lambda messages, max_tokens=160: ("这是一句离线自检台词", "")

    room = Room(args.room or None).load()
    d = director_mod.configure(room=room, persona_id=args.persona)
    room.persona_name = d._persona_name()

    # 让本地 demo 的节奏更适合观察
    director_mod._cfg = lambda: {  # noqa: SLF001 - CLI 覆盖配置
        "live_idle_speak_seconds": args.idle,
        "live_max_lines_per_minute": 8,
    }

    sim = Simulator(d, speed=args.speed, loop=not args.no_loop)
    print(f"🐋 直播 P0 本地模拟开始：速度 {args.speed}x，冷场阈值 {args.idle}s，"
          f"角色「{room.persona_name}」" + ("，语音开" if args.voice else "，语音关"))
    print("─" * 60)
    d.start()
    sim.start()

    last_seq = 0
    started = time.time()
    try:
        while True:
            line = stage.latest()
            if int(line.get("seq") or 0) > last_seq:
                last_seq = int(line["seq"])
                kind = "（自主开麦）" if d.last_action == "self_talk" else ""
                print(f"[{time.strftime('%H:%M:%S')}] 她{kind}：{line.get('text')}")
            if args.seconds and (time.time() - started) >= args.seconds:
                break
            if not sim.running() and not d.running():
                break
            time.sleep(0.3)
    # noqa: silent-except: 用户 Ctrl+C 是正常退出方式，无需记录
    except KeyboardInterrupt:
        pass
    finally:
        sim.stop()
        d.stop()
        st = d.status()
        print("─" * 60)
        print(f"结束：台词 {int(st.get('stats', {}).get('replies') or 0)} 条回复 + "
              f"{int(st.get('stats', {}).get('self_talk') or 0)} 次自主开麦；"
              f"忽略事件 {int(st.get('stats', {}).get('ignored') or 0)}；"
              f"能量 {st.get('energy')}，情绪 {st.get('mood')}")
        if args.room:
            room.save(force=True)
            print(f"房间记忆已写入 {args.room}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
