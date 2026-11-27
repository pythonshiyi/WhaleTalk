#!/usr/bin/env python3
"""真实模型 · 参数化技能回放（P6-1 护栏）。

与 e2e_task.py（单次多步任务全链路）互补，本脚本验证「越用越省」的真实闭环：
同一工作区连续跑 3 次相同任务让配方成功次数达标，第 3 次应精确命中零模型技能；
第 4 次**只换文件名与内容**（结构骨架不变），应命中参数化模板，仍然零模型调用，
且产物是新的文件名/内容，而不是把旧文件重放一遍。

默认需要 config.json 已配置真实 API Key；**无 key 或无 playwright 自动 SKIP（exit 0）**，
可安全进 CI。用法：
    python tools/e2e_skill.py
    python tools/e2e_skill.py --timeout 120
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
    print(f"[e2e-skill] 无法导入评测台：{e}")
    raise SystemExit(2) from None

# 结构骨架保持不变，只替换槽位值；换参数后必须仍能零模型命中。
PROMPT = (
    "请用 write_file 工具依次完成以下三个步骤（直接用 write_file 写文件，"
    "不要用 self_evolve / 分支 / 四层验证等复杂流程）："
    "第一步，在工作区创建文件 skill1.txt，内容写入 skill-ok；"
    "第二步，创建文件 skill2.txt，内容写入 skill-yes；"
    "第三步，用 verify_files 确认这两个文件都存在且内容正确。"
    "每完成一步请立即用 task_step_done 工具验收该步，"
    "evidence 写明结果，verify_path 指向对应文件。"
)
VARIANT = (PROMPT.replace("skill1.txt", "p6file1.txt")
           .replace("skill2.txt", "p6file2.txt")
           .replace("skill-ok", "hello-p6")
           .replace("skill-yes", "world-p6"))


def _has_real_key() -> bool:
    cfg = REPO / "config.json"
    if not cfg.exists():
        return False
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:  # silent-except: 配置缺失/损坏等同无 key，走 SKIP
        return False
    key = str(data.get("api_key") or "").strip()
    return bool(key) and not key.startswith("eval-")


def _http_json(url: str, token: str, timeout: float = 8.0) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_post(url: str, token: str, payload: Dict[str, Any],
               timeout: float = 15.0) -> Dict[str, Any]:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _wait_finalized(plan_file: Path, timeout: float = 15.0) -> Dict[str, Any]:
    """轮询等收尾落盘：mark 先置 completed，finalize 稍后补 finalized/配方。"""
    data: Dict[str, Any] = {}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if plan_file.exists():
            try:
                data = json.loads(plan_file.read_text(encoding="utf-8"))
            except Exception:  # silent-except: 收尾写入中间态可能是半截 JSON，下轮重试
                pass
        if data.get("finalized") and data.get("recipe_id"):
            break
        time.sleep(0.5)
    return data


def _run_round(page, srv, run_dir: Path, prompt: str, before_ids: set,
               timeout: float) -> Dict[str, Any]:
    composer = page.locator("textarea").first
    composer.fill(prompt)
    composer.press("Enter")
    needle = "p6file1.txt" if "p6file1.txt" in prompt else "skill1.txt"
    deadline = time.monotonic() + timeout
    new_plan: Optional[Dict[str, Any]] = None
    while time.monotonic() < deadline:
        try:
            plans = _http_json(srv.base + "/v1/task_plans", srv.token).get("plans") or []
            mine = [p for p in plans
                    if needle in str(p.get("goal") or "")
                    and p.get("plan_id") not in before_ids]
            if mine and str(mine[-1].get("status") or "") == "completed":
                new_plan = mine[-1]
                break
        except Exception as e:  # silent-except: 轮询期服务瞬时未就绪，继续等待
            print(f"[e2e-skill] 轮询异常：{e}", flush=True)
        time.sleep(2.0)
    if not new_plan:
        return {"ok": False, "reason": "未在超时内找到已完成任务计划"}
    plan_id = str(new_plan.get("plan_id") or "")
    plan_file = run_dir / "data" / "task_plans" / f"{plan_id}.json"
    disk = _wait_finalized(plan_file)
    preview = disk.get("skill_preview") or {}
    return {
        "ok": True,
        "plan_id": plan_id,
        "status": str(new_plan.get("status") or ""),
        "done": new_plan.get("done"),
        "total": new_plan.get("total"),
        "mode": str(disk.get("execution_mode") or ""),
        "match_mode": str(preview.get("match_mode") or ""),
        "finalized": bool(disk.get("finalized")),
        "recipe_id": str(disk.get("recipe_id") or ""),
        "preview_paths": list(preview.get("paths") or []),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="真实模型参数化技能回放")
    parser.add_argument("--timeout", type=float, default=120.0, help="单轮任务等待上限（秒）")
    parser.add_argument("--startup-timeout", type=float, default=90.0)
    parser.add_argument("--report-dir", default=str(REPO / "out" / "e2e_skill"))
    args = parser.parse_args(argv)

    if not _has_real_key():
        print("[e2e-skill] SKIP：config.json 无真实 API Key（无需失败）")
        return 0
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        print("[e2e-skill] SKIP：未安装 playwright")
        return 0

    run_dir = Path(args.report_dir) / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "data").mkdir(parents=True, exist_ok=True)
    (run_dir / "data" / "first_run_done").write_text("", encoding="utf-8")

    task = ev.Task(id="e2e_skill", category="ui", turns=[PROMPT], accept=[],
                   notes="真实模型参数化技能回放")
    srv = ev._start_server(task, run_dir, startup_timeout=args.startup_timeout)
    print(f"[e2e-skill] 服务就绪：{srv.base}（启动 {srv.startup_s:.1f}s）", flush=True)

    result: Dict[str, Any] = {"ok": False, "rounds": []}
    page_errors: List[str] = []
    browser = None
    started = time.monotonic()
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True, channel="msedge")
            except Exception:  # silent-except: 无 Edge 时回退 Chromium
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

            variants = [PROMPT, PROMPT, PROMPT, VARIANT]
            for round_no, prompt in enumerate(variants, 1):
                try:
                    plans = _http_json(srv.base + "/v1/task_plans", srv.token).get("plans") or []
                except Exception:  # silent-except: 首轮前服务列表可能为空
                    plans = []
                before_ids = {p.get("plan_id") for p in plans}
                row = _run_round(page, srv, run_dir, prompt, before_ids, args.timeout)
                row["round"] = round_no
                row["variant"] = round_no == 4
                result["rounds"].append(row)
                print(f"[e2e-skill] round {round_no}: mode={row.get('mode')} "
                      f"match={row.get('match_mode')} finalized={row.get('finalized')} "
                      f"done={row.get('done')}/{row.get('total')}", flush=True)
                page.wait_for_timeout(1500)

            # 第 4 轮：参数化命中 + 新产物真实落盘且内容正确
            ws = srv.workdir
            f1, f2 = ws / "p6file1.txt", ws / "p6file2.txt"
            result["variant_artifacts"] = {
                "file1": f1.exists() and "hello-p6" in f1.read_text(encoding="utf-8", errors="replace"),
                "file2": f2.exists() and "world-p6" in f2.read_text(encoding="utf-8", errors="replace"),
            }
            # P6-2 产物直达：真实服务端零模型链路（非流式 /v1/chat 不该调用模型）
            try:
                direct = _http_post(srv.base + "/v1/chat", srv.token, {
                    "messages": [{"role": "user",
                                  "content": "上次的 skill1.txt 文件在哪？结论是什么？"}],
                    "session_id": "e2e-direct",
                    "mode": "chat",
                })
                card = direct.get("task_recall") or {}
                result["direct_recall"] = {
                    "zero_model": bool(direct.get("zero_model")),
                    "card": bool(card),
                    "artifacts": len(card.get("artifacts") or []),
                    "plan_id": str(card.get("plan_id") or ""),
                }
            except Exception as e:
                result["direct_recall"] = {"zero_model": False, "error": f"{type(e).__name__}: {e}"}

            recipes = run_dir / "data" / "task_plans" / "recipes" / "recipes.json"
            has_template = False
            if recipes.exists():
                try:
                    rows = json.loads(recipes.read_text(encoding="utf-8"))
                    has_template = any(bool((r.get("template") or {}).get("goal_pattern"))
                                       for r in rows if isinstance(r, dict))
                except Exception:  # silent-except: 配方文件损坏不影响已得出的结论
                    pass
            result["recipe_has_template"] = has_template
            result["page_errors"] = list(page_errors)
            rounds = result["rounds"]
            r3, r4 = rounds[2], rounds[3]
            result["ok"] = bool(
                all(r.get("finalized") and r.get("done") == r.get("total") for r in rounds)
                and r3.get("mode") == "deterministic"
                and r4.get("mode") == "deterministic"
                and r4.get("match_mode") == "template"
                and all(result["variant_artifacts"].values())
                and has_template
                and bool(result.get("direct_recall", {}).get("zero_model"))
                and bool(result.get("direct_recall", {}).get("card"))
                and not page_errors
            )
            if not result["ok"]:
                result["reason"] = "参数化技能闭环未完全满足，详见 rounds / variant_artifacts"
    except Exception as e:
        result["reason"] = f"{type(e).__name__}: {e}"
        import traceback
        traceback.print_exc()
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:  # silent-except: 浏览器 best-effort 关闭，不影响结论
                pass
        ev._stop_server(srv.proc)
        ev._redact_config(srv.config_path)
        result["elapsed_s"] = round(time.monotonic() - started, 1)
        result["run_dir"] = str(run_dir)

    print("\n===== E2E SKILL RESULT =====", flush=True)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
