#!/usr/bin/env python3
"""前端端到端冒烟（P0）。

两种模式：
    --boot-only   只验证「服务起得来 + 页面能打开 + 输入框可见 + 无 JS 异常」，
                  不需要 API Key，适合 CI。
    默认          真发一条消息，等助手回复包含 E2E-OK，验证 SSE → 渲染 → 落盘链路，
                  需要本机 config.json 已配置模型。

用法：
    python tools/e2e_smoke.py --boot-only
    python tools/e2e_smoke.py --headed
    python tools/e2e_smoke.py --timeout 120
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # silent-except: 控制台不支持 reconfigure 时保持默认编码
        pass

try:
    import agent_eval as ev
except Exception as e:  # pragma: no cover
    print(f"[e2e] 无法导入评测台：{e}")
    raise SystemExit(2) from None

PROMPT = "请只回复：E2E-OK。不要调用任何工具。"
NEEDLE = "E2E-OK"


def _launch_browser(p, headed: bool, channel: str):
    """优先用系统 Edge（CI runner 自带），失败再回退 Playwright 自带 Chromium。"""
    tried = []
    if channel:
        try:
            return p.chromium.launch(headless=not headed, channel=channel)
        except Exception as e:
            tried.append(f"{channel}: {e}")
    try:
        return p.chromium.launch(headless=not headed)
    except Exception as e:
        tried.append(f"chromium: {e}")
    raise RuntimeError("浏览器启动失败：\n  " + "\n  ".join(tried))


def run(args: argparse.Namespace) -> int:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"[e2e] 缺少 playwright：{e}（pip install playwright）")
        return 2

    if not (REPO / "webui" / "dist" / "index.html").exists():
        print("[e2e] webui/dist 不存在；先执行 cd webui && npm ci && npm run build")
        return 2

    run_dir = Path(args.report_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    # 首次运行向导会挡住界面：先放 marker（服务启动时 data 目录还没建，这里先建）
    (run_dir / "data").mkdir(parents=True, exist_ok=True)
    (run_dir / "data" / "first_run_done").write_text("", encoding="utf-8")

    prompt = str(getattr(args, "prompt", "") or PROMPT)
    needle = str(getattr(args, "needle", "") or NEEDLE)
    task = ev.Task(
        id="e2e_smoke", category="ui", turns=[prompt], accept=[],
        notes="前端端到端冒烟",
    )
    srv = None
    browser = None
    page = None
    page_errors: List[str] = []
    console_errors: List[str] = []
    started = time.monotonic()
    try:
        srv = ev._start_server(
            task, run_dir, model_override=args.model,
            startup_timeout=args.startup_timeout,
            require_key=not args.boot_only,
        )
        print(f"[e2e] 服务就绪：{srv.base}（启动 {srv.startup_s:.1f}s）")

        with sync_playwright() as p:
            browser = _launch_browser(p, args.headed, args.channel)
            page = browser.new_page(viewport={"width": 1280, "height": 860})
            page.set_default_timeout(args.timeout * 1000)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)

            page.goto(srv.base + "/", wait_until="domcontentloaded")
            page.wait_for_selector("textarea", state="visible", timeout=30000)
            print("[e2e] 页面已加载，输入框可见")

            if not args.boot_only:
                composer = page.locator("textarea").first
                composer.fill(prompt)
                composer.press("Enter")
                print("[e2e] 已发送，等待助手回复 ...")
                page.wait_for_function(
                    """(needle) => [...document.querySelectorAll('.msg.msg-assistant')]
                         .some(e => (e.innerText || '').includes(needle))""",
                    arg=needle,
                    timeout=args.timeout * 1000,
                )
                # 等生成徽标消失（若存在），确认不是半截流
                try:
                    page.wait_for_function(
                        """() => !document.querySelector('.msg.msg-assistant .turn-badge-run')""",
                        timeout=15000,
                    )
                except Exception:  # silent-except: 徽标消失是附加断言，正文已确认
                    pass
                assistant = page.locator(".msg.msg-assistant").all_inner_texts()
                user = page.locator(".msg.msg-user").all_inner_texts()
                if not any(needle in t for t in assistant):
                    raise RuntimeError(f"助手回复未包含 {needle}：{assistant[-1:] if assistant else '无'}")
                if not any(prompt[:12] in t for t in user):
                    raise RuntimeError("用户消息未出现在界面")
                print("[e2e] 收到助手回复，SSE → 渲染链路通过")

            shot = run_dir / "ui.png"
            page.screenshot(path=str(shot), full_page=True)
            print(f"[e2e] 截图：{shot}")
    except Exception as e:
        if page is not None:
            try:
                page.screenshot(path=str(run_dir / "failure.png"), full_page=True)
            except Exception:  # silent-except: 失败现场截图是排障辅助，不能掩盖原异常
                pass
        print(f"[e2e] 失败：{type(e).__name__}: {e}")
        return 1
    finally:
        try:
            if browser is not None:
                browser.close()
        except Exception:  # silent-except: 浏览器关闭失败不影响已得出的结论
            pass
        if srv is not None:
            ev._stop_server(srv.proc)
            ev._redact_config(srv.config_path)
        elapsed = round(time.monotonic() - started, 1)
        print(f"[e2e] 用时 {elapsed}s，输出目录：{run_dir}")

    if page_errors:
        print(f"[e2e] 页面 JS 异常 {len(page_errors)} 条：")
        for e in page_errors[:5]:
            print(f"  - {e}")
        return 1
    if console_errors:
        # console.error 可能是资源 404 等噪声，只提示不判失败
        print(f"[e2e] 控制台 error {len(console_errors)} 条（不判失败）：")
        for e in console_errors[:3]:
            print(f"  - {e}")
    print("[e2e] PASS")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="前端端到端冒烟")
    parser.add_argument("--boot-only", action="store_true", help="只验 UI 启动，不发消息（无需 API Key）")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（调试用）")
    parser.add_argument("--channel", default="msedge", help="浏览器通道（默认 msedge，失败回退 chromium）")
    parser.add_argument("--timeout", type=float, default=90.0, help="单步超时秒数")
    parser.add_argument("--startup-timeout", type=float, default=90.0, help="服务启动超时秒数")
    parser.add_argument("--model", default="", help="覆盖模型名")
    parser.add_argument("--prompt", default="", help="自定义发送内容（默认只回复 E2E-OK）")
    parser.add_argument("--needle", default="", help="等待助手回复包含的文本（默认 E2E-OK）")
    parser.add_argument("--report-dir", default=str(REPO / "out" / "e2e"), help="输出目录")
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
