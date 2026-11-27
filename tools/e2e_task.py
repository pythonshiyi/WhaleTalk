#!/usr/bin/env python3
"""真实模型 · 长任务多步全流程回放（P5-3 护栏）。

与 e2e_smoke.py（只发一句 E2E-OK）不同，本脚本验证长任务引擎的完整链路：
  切任务模式 → 发显式多步任务 → SSE task_plan → 模型逐步调用 task_step_done
  → 证据即完成 → 后端 completed → 收尾(finalized/配方/记忆) → 产物落盘 → 无 JS 异常。

默认需要 config.json 已配置真实 API Key；**若无 key 或无 playwright 则自动 SKIP（exit 0）**，
因此可安全放进门禁脚本串，不阻塞无凭据的 CI。

用法：
    python tools/e2e_task.py                       # 默认三步文件任务
    python tools/e2e_task.py --timeout 240         # 放宽单步等待
    python tools/e2e_task.py --no-browser          # 直接调 HTTP，不启浏览器（更快，不验 UI）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    print(f"[e2e-task] 无法导入评测台：{e}")
    raise SystemExit(2) from None

PROMPT = (
    "请用 write_file 工具依次完成以下三个步骤（直接用 write_file 写文件，"
    "不要用 self_evolve / 分支 / 四层验证等复杂流程）："
    "第一步，在工作区创建文件 p4_step1.txt，内容写入 hello-p4；"
    "第二步，创建文件 p4_step2.txt，内容写入 world-p4；"
    "第三步，用 verify_files 确认这两个文件都存在且内容正确。"
    "每完成一步请立即用 task_step_done 工具验收该步，"
    "evidence 写明结果，verify_path 指向对应文件。"
)


def _has_real_key() -> bool:
    cfg = REPO / "config.json"
    if not cfg.exists():
        return False
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return False
    key = str(data.get("api_key") or "").strip()
    return bool(key) and not key.startswith("eval-")


def _http_json(url: str, token: str, timeout: float = 8.0) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _find_plan(srv, needle="p4_step"):
    data = _http_json(srv.base + "/v1/task_plans", srv.token)
    plans = [x for x in (data.get("plans") or [])
             if needle in str(x.get("goal") or "")]
    return plans[-1] if plans else None


def _poll_plan(srv, timeout: float, echo=True) -> Optional[Dict[str, Any]]:
    plan = None
    deadline = time.monotonic() + timeout
    last = 0.0
    while time.monotonic() < deadline:
        try:
            cur = _find_plan(srv)
            if cur:
                plan = cur
                now = time.monotonic()
                if echo and now - last > 6:
                    steps = [(s.get("status"), s.get("title")) for s in (cur.get("steps") or [])]
                    print(f"[e2e-task] status={cur.get('status')} "
                          f"done={cur.get('done')}/{cur.get('total')} steps={steps}", flush=True)
                    last = now
                if str(cur.get("status") or "") in ("completed", "blocked", "failed", "cancelled"):
                    break
        except Exception as e:
            print(f"[e2e-task] 轮询异常：{e}", flush=True)
        time.sleep(2.0)
    return plan


def _check_finalization(plan: Dict[str, Any], srv) -> Dict[str, Any]:
    """核对收尾链路：finalized / recipe_id / recipes.json / 产物文件。

    注意时序：后端在最后一步 mark 时先把 status 置 completed，收尾 finalize
    （finalized/recipe_id）在 SSE 线程里稍后执行。这里轮询等待收尾落盘，
    避免读到「completed 但尚未 finalized」的中间态而误判。
    """
    out: Dict[str, Any] = {}
    plan_id = str(plan.get("plan_id") or "")
    plan_file = srv.workdir.parent / "task_plans" / f"{plan_id}.json"
    data: Dict[str, Any] = {}
    wait_deadline = time.monotonic() + 15
    while time.monotonic() < wait_deadline:
        if plan_file.exists():
            try:
                data = json.loads(plan_file.read_text(encoding="utf-8"))
            except Exception:  # silent-except: 收尾写入中间态可能读到半截 JSON，下轮重试
                pass
        if data.get("finalized") and data.get("recipe_id"):
            break
        time.sleep(0.5)
    out["finalized"] = bool(data.get("finalized", False))
    out["recipe_id"] = str(data.get("recipe_id") or "")
    recipes = srv.workdir.parent / "task_plans" / "recipes" / "recipes.json"
    out["recipes_file"] = recipes.exists()
    f1 = srv.workdir / "p4_step1.txt"
    f2 = srv.workdir / "p4_step2.txt"
    out["file1"] = f1.exists() and "hello-p4" in f1.read_text(encoding="utf-8", errors="replace")
    out["file2"] = f2.exists() and "world-p4" in f2.read_text(encoding="utf-8", errors="replace")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="真实模型长任务多步全流程回放")
    parser.add_argument("--timeout", type=float, default=240.0, help="轮询计划完成的上限秒数")
    parser.add_argument("--startup-timeout", type=float, default=90.0)
    parser.add_argument("--prompt", default=PROMPT, help="自定义多步任务提示")
    parser.add_argument("--report-dir", default=str(REPO / "out" / "e2e_task"))
    parser.add_argument("--no-browser", action="store_true", help="只用 HTTP 驱动（不验 UI）")
    args = parser.parse_args(argv)

    if not _has_real_key():
        print("[e2e-task] SKIP：config.json 无真实 API Key（无需失败）")
        return 0

    try:
        import playwright.sync_api  # noqa: F401
        have_playwright = True
    except Exception:
        have_playwright = False
    if not args.no_browser and not have_playwright:
        print("[e2e-task] SKIP：未安装 playwright（可加 --no-browser 只验 HTTP）")
        return 0

    run_dir = Path(args.report_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "data").mkdir(parents=True, exist_ok=True)
    (run_dir / "data" / "first_run_done").write_text("", encoding="utf-8")

    task = ev.Task(id="e2e_task", category="ui", turns=[args.prompt], accept=[],
                   notes="真实模型长任务多步全流程回放")
    srv = ev._start_server(task, run_dir, startup_timeout=args.startup_timeout)
    print(f"[e2e-task] 服务就绪：{srv.base}（启动 {srv.startup_s:.1f}s）", flush=True)

    browser = None
    page_errors: List[str] = []
    result: Dict[str, Any] = {"ok": False}
    started = time.monotonic()
    try:
        if args.no_browser:
            print("[e2e-task] SKIP：--no-browser 暂未实现 SSE 客户端，请用完整模式（需 playwright）。")
            return 0
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel="msedge")
            except Exception:
                browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(30000)
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.goto(srv.base + "/", wait_until="domcontentloaded")
            page.wait_for_selector("textarea", state="visible", timeout=30000)

            mode_btns = page.locator(".mode-switch .mode-btn")
            for i in range(mode_btns.count()):
                if "任务" in (mode_btns.nth(i).inner_text() or ""):
                    mode_btns.nth(i).click()
                    break
            page.wait_for_timeout(600)
            composer = page.locator("textarea").first
            composer.fill(args.prompt)
            composer.press("Enter")
            print("[e2e-task] 已切任务模式并发送多步任务", flush=True)

            # 等任务计划卡
            seen = False
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if page.locator(".task-plan-block").count() > 0:
                    seen = True
                    break
                page.wait_for_timeout(1000)
            result["plan_card_seen"] = seen

            plan = _poll_plan(srv, args.timeout)
            if not plan:
                result["reason"] = "未找到后端任务计划"
            else:
                result["status"] = plan.get("status")
                result["steps"] = [
                    {"title": s.get("title"), "status": s.get("status"),
                     "evidence": (s.get("evidence") or "")[:120]}
                    for s in (plan.get("steps") or [])
                ]
                result["done"] = plan.get("done")
                result["total"] = plan.get("total")
                result["finalization"] = _check_finalization(plan, srv)

            page.screenshot(path=str(run_dir / "ui.png"), full_page=True)
            result["page_errors"] = list(page_errors)
            ok = (
                seen
                and result.get("status") == "completed"
                and result.get("done") == result.get("total")
                and all(result.get("finalization", {}).get(k) for k in
                        ("finalized", "recipe_id", "recipes_file", "file1", "file2"))
                and not page_errors
            )
            result["ok"] = bool(ok)
            if not ok and not result.get("reason"):
                result["reason"] = f"status={result.get('status')} finalization={result.get('finalization')} page_errors={len(page_errors)}"
    except Exception as e:
        result["reason"] = f"{type(e).__name__}: {e}"
        import traceback
        traceback.print_exc()
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # silent-except: 浏览器 best-effort 关闭，不影响已得出的验证结论
                pass
        ev._stop_server(srv.proc)
        ev._redact_config(srv.config_path)
        result["elapsed_s"] = round(time.monotonic() - started, 1)
        result["run_dir"] = str(run_dir)

    print("\n===== E2E TASK RESULT =====", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
