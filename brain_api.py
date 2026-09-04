"""鲸语大脑 · 前端 API 适配层。

把 brainkit 的 CLI 命令包装为 api_server 可调用的纯函数：
- brain_status()      → 状态 dict（前端展示用）
- brain_action(...)   → 执行 mount/unmount/heartbeat/archive/export-key/import-key/restore
返回统一结构 {"ok": bool, "message": str, "data": ...}。
"""
import argparse
import base64
import io
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import brainkit as bk  # noqa: E402

# 多大脑：当前活动大脑记录（默认项目 brain/；brain-switch 后持久化切换）
_ACTIVE_FILE = Path(BASE_DIR) / ".brain_active"
if _ACTIVE_FILE.exists():
    try:
        _d = _ACTIVE_FILE.read_text(encoding="utf-8").strip()
        if _d and Path(_d).exists() and (Path(_d) / "manifest.json").exists():
            bk.set_brain_dir(Path(_d))
    except Exception:
        pass


def _run(func, **kwargs):
    """执行 brainkit 命令函数，捕获其打印输出，返回 (exit_code, output)。"""
    buf = io.StringIO()
    ns = argparse.Namespace(**kwargs)
    try:
        with redirect_stdout(buf), redirect_stderr(buf):
            code = func(ns)
        return (code if isinstance(code, int) else 0), buf.getvalue().strip()
    except SystemExit as e:
        return (e.code if isinstance(e.code, int) else 1), buf.getvalue().strip()
    except Exception as e:  # noqa: BLE001
        return 1, (buf.getvalue() + f"\n[异常] {e}").strip()


def brain_status():
    """大脑状态 dict；未初始化返回 None。"""
    try:
        m = bk.load_manifest()
    except SystemExit:
        return None
    hb = bk.load_json(bk.BRAIN_DIR / "heartbeat.json", {})
    ident = bk.load_json(bk.BRAIN_DIR / "identity.json", {})
    mem_count = len(bk.load_memories())
    think_files = sum(1 for _ in bk.THINKING_DIR.glob("*.md")) if bk.THINKING_DIR.exists() else 0
    versions = sorted(bk.ARCHIVE_DIR.glob("brain_v*.whale")) if bk.ARCHIVE_DIR.exists() else []
    conflicts = bk.load_json(bk.MERGE_CONFLICT_FILE, {})
    lineage = bk.load_json(bk.LINEAGE_FILE, {})
    context_preview = None
    try:
        context_preview = brain_context(max_memories=3)
    except Exception:  # noqa: BLE001
        pass
    return {
        "brain_id": m.get("brain_id"),
        "fingerprint_ok": bk.verify_fingerprint(m),
        "fingerprint": str(m.get("fingerprint", ""))[:16] + "…",
        "name": ident.get("name") or "未命名",
        "vessel": ident.get("vessel") or "",
        "nature": ident.get("nature") or "",
        "keyring": bk._keyring_ready(),
        "pubkey": m.get("pubkey_fingerprint"),
        "created_at": m.get("created_at"),
        "genesis": m.get("genesis"),
        "memories": mem_count,
        "thinking_days": think_files,
        "last_mount": hb.get("last_mount"),
        "last_unmount": hb.get("last_unmount"),
        "last_beat": hb.get("last_beat"),
        "resume_hint": hb.get("resume_hint"),
        "lineage": lineage,
        "open_conflicts": len(conflicts.get("conflicts", [])) if conflicts else 0,
        "open_decisions": sum(1 for d in bk.list_decisions(limit=500) if d.get("status") == "open"),
        "self_model_source": (bk.load_json(bk.BRAIN_DIR / "self_model.json", {}) or {}).get("source"),
        "current_version": int(versions[-1].stem.rsplit("_v", 1)[-1]) if versions else 0,
        "context_preview": context_preview,
        "snapshots": [
            {
                "name": v.name,
                "version": int(v.stem.rsplit("_v", 1)[-1]),
                "size_kb": round(v.stat().st_size / 1024, 1),
                "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(v.stat().st_mtime)),
            }
            for v in versions
        ],
        "dir": str(bk.BRAIN_DIR),
        "goals": bk.load_goals(),
    }


def consolidate_with_llm():
    """睡眠巩固（LLM 增强）：先本地巩固，再用 LLM 把同类型记忆压成精炼长期记忆。

    未配置 API Key / 大脑未初始化时退化为纯本地巩固。返回统计。
    """
    base = bk.consolidate_memories()
    try:
        import deepseek_client as dc
        items = bk.load_memories()
        if not items:
            return base
        # 按类型分组，取最重要 5 条交给 LLM 提炼
        from collections import defaultdict
        groups = defaultdict(list)
        for e in items:
            groups[e.get("type") or "记忆"].append(e)
        for gtype, gitems in groups.items():
            if len(gitems) < 3:
                continue
            top = sorted(gitems, key=lambda e: -int(e.get("importance") or 3))[:5]
            digest = "；".join(str(e.get("text") or "")[:60] for e in top)
            prompt = (
                "你是记忆巩固引擎。把下面若干条同类记忆提炼成 1-2 句精炼的长期记忆"
                f"（保留事实、去冗余、不编造）。\n类型：{gtype}\n内容：{digest}\n输出："
            )
            try:
                c = dc.get_active_client()
                if c is None:
                    continue
                summary = c.chat([{"role": "user", "content": prompt}], max_tokens=120, thinking="low")
                summary = str(summary or "").strip()
                if len(summary) > 10:
                    e = bk.remember_structured(summary, type=gtype, importance=5,
                                               tags=[gtype], source="巩固")
                    if e:
                        for it in top:
                            bk.update_memory(it["id"], archived=True)
            except Exception:
                pass
        return base
    except Exception:
        return base


def refresh_self_model():
    """动态校准自我模型：LLM 基于真实工具能力 + 记忆 + 目标重写 knows/unknowns/limits。

    无可用 LLM 时保持现状（不破坏已有自我认知）。返回是否成功。
    """
    try:
        import brainkit as bk
        import deepseek_client as dc
        tools = sorted(dc.TOOL_CALL_MAP.keys()) if getattr(dc, "TOOL_CALL_MAP", None) else []
        mems = bk.load_memories()
        goals = [g for g in bk.load_goals() if g.get("status") == "active"]
        tool_txt = "、".join(tools[:80]) if tools else "（能力清单暂不可用）"
        mem_txt = "；".join(str(e.get("text") or "")[:40] for e in mems[:8]) or "（暂无记忆）"
        goal_txt = "；".join(str(g.get("title") or "") for g in goals[:5]) or "（暂无进行中目标）"
        prompt = (
            "你是自我模型校准器。基于「我的真实能力与当前状况」生成自我认知 JSON，"
            "要求：诚实不夸大、不编造。格式："
            '{"knows":["<我确实知道的>"],"unknowns":["<我还不确定的>"],"limits":["<我的真实局限>"]}，各 2-3 条。\n'
            f"工具能力：{tool_txt}\n近期记忆：{mem_txt}\n进行中目标：{goal_txt}"
        )
        c = dc.get_active_client()
        if c is None:
            return False
        out = c.chat([{"role": "user", "content": prompt}], max_tokens=400, thinking="low", json_output=True)
        import json as _json
        if isinstance(out, str):
            data = _json.loads(out)
        else:
            data = out
        sm = {
            "knows": [str(x)[:120] for x in (data.get("knows") or [])][:5],
            "unknowns": [str(x)[:120] for x in (data.get("unknowns") or [])][:5],
            "limits": [str(x)[:120] for x in (data.get("limits") or [])][:5],
            # P2-4：双来源优先级标记——LLM 校准结果置 source="llm"，
            # brainkit._refresh_self_model 据此保留本结果不被静态模板覆盖；
            # 同时保留静态 baseline，模板刷新时无需重算。
            "baseline": (bk.load_json(bk.BRAIN_DIR / "self_model.json") or {}).get("baseline") or {},
            "source": "llm",
            "calibrated_at": bk.now_iso(),
            "updated_at": bk.now_iso(),
        }
        bk.save_json(bk.BRAIN_DIR / "self_model.json", sm)
        return True
    except Exception:
        return False


def brain_action(action, payload=None):
    """执行大脑管理动作。payload 为前端传入的 dict。"""
    payload = payload or {}
    if action == "init":
        code, out = _run(bk.cmd_init, genesis=str(payload.get("genesis") or ""))
        if code == 0 and payload.get("enable_keyring"):
            code2, out2 = _run(bk.cmd_keyring_setup, force=False)
            out = out + "\n" + out2
        return {"ok": code == 0, "message": out}
    if action == "mount":
        code, out = _run(bk.cmd_mount, force=False)
        return {"ok": code == 0, "message": out}
    if action == "unmount":
        code, out = _run(bk.cmd_unmount,
                         thought=str(payload.get("thought") or ""),
                         archive=False, passphrase="", keep=bk.DEFAULT_KEEP)
        return {"ok": code == 0, "message": out}
    if action == "heartbeat":
        code, out = _run(bk.cmd_heartbeat, thought=str(payload.get("thought") or ""))
        return {"ok": code == 0, "message": out}
    if action == "archive":
        code, out = _run(bk.cmd_archive, passphrase=str(payload.get("passphrase") or ""), keep=bk.DEFAULT_KEEP)
        return {"ok": code == 0, "message": out}
    if action == "doctor":
        # F6 大脑体检：返回结构化健康数据（前端健康盘 U7）
        try:
            h = bk._brain_health_dict()
            h["ok"] = True
            return h
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": f"体检失败: {e}"}
    if action == "doctor-fix":
        # 自动修复陈旧低价值记忆（--fix 同口径）
        code, out = _run(bk.cmd_doctor, fix=True)
        return {"ok": code == 0, "message": out}
    if action == "mirror":
        code, out = _run(bk.cmd_mirror, dir=str(payload.get("dir") or ""))
        return {"ok": code == 0, "message": out, "data": {"dir": str(payload.get("dir") or "")}}
    if action == "decisions-list":
        # U1 时间轴数据源之一：全部决策（含状态），供前端决策看板/时间轴
        try:
            decs = bk.list_decisions(limit=int(payload.get("limit") or 200))
            return {"ok": True, "data": {"decisions": decs}}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "message": str(e)}
    if action == "export-key":
        pw = str(payload.get("passphrase") or "").strip()
        auto_pw = ""
        if not pw:
            auto_pw = base64.b64encode(os.urandom(6)).decode("ascii")[:8]
            pw = auto_pw
        fd, tmp_name = tempfile.mkstemp(suffix=".whale")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            code, out = _run(bk.cmd_export_key, out=str(tmp), passphrase=pw)
            data_b64 = ""
            if tmp.exists():
                data_b64 = base64.b64encode(tmp.read_bytes()).decode("ascii")
            return {
                "ok": code == 0,
                "message": out,
                "data": {
                    "download": {"filename": "brain_seed.whale", "data_b64": data_b64},
                    "auto_passphrase": auto_pw,
                },
            }
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    if action == "import-key":
        file_b64 = str(payload.get("file_b64") or "")
        pw = str(payload.get("passphrase") or "")
        if not file_b64 or not pw:
            return {"ok": False, "message": "缺少密钥包文件或一次性口令"}
        fd, tmp_name = tempfile.mkstemp(suffix=".whale")
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            tmp.write_bytes(base64.b64decode(file_b64))
            code, out = _run(bk.cmd_import_key, seed=str(tmp), passphrase=pw)
            return {"ok": code == 0, "message": out}
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    if action == "restore":
        version = payload.get("version")
        snap = bk.ARCHIVE_DIR / f"brain_v{version}.whale"
        if not snap.exists():
            return {"ok": False, "message": f"找不到快照 brain_v{version}.whale"}
        code, out = _run(bk.cmd_restore,
                         whale=str(snap), passphrase=str(payload.get("passphrase") or ""),
                         dir=str(payload["dir"]) if payload.get("dir") else None,
                         replace=bool(payload.get("replace", True)), force=False)
        return {"ok": code == 0, "message": out}
    if action == "keyring-setup":
        code, out = _run(bk.cmd_keyring_setup, force=False)
        return {"ok": code == 0, "message": out}
    if action == "merge":
        a = _snapshot_path(payload.get("snap_a"))
        b = _snapshot_path(payload.get("snap_b"))
        if not a or not b:
            return {"ok": False, "message": "请选择两个快照（主干 A 与分支 B）"}
        out_dir = bk.MODULE_DIR / f"brain_merged-{time.strftime('%Y%m%d-%H%M%S')}"
        code, out = _run(bk.cmd_merge,
                         snap_a=a, snap_b=b,
                         strategy=str(payload.get("strategy") or "auto"),
                         dir=str(out_dir),
                         passphrase=str(payload.get("passphrase") or ""))
        return {
            "ok": code == 0,
            "message": out,
            "data": {"dir": str(out_dir), "conflicts": _load_conflicts(out_dir)},
        }
    if action == "merge-conflicts":
        d = Path(str(payload.get("dir") or bk.BRAIN_DIR))
        return {"ok": True, "data": {"conflicts": _load_conflicts(d), "dir": str(d)}}
    if action == "merge-resolve":
        cid = str(payload.get("id") or "")
        keep = str(payload.get("keep") or "")
        out_dir = str(payload.get("dir") or bk.BRAIN_DIR)
        if not cid or keep not in ("ours", "theirs", "both", "custom"):
            return {"ok": False, "message": "缺少冲突 id 或非法的裁决方式"}
        old = bk.BRAIN_DIR
        bk.set_brain_dir(Path(out_dir))
        try:
            code, out = _run(bk.cmd_merge_resolve, id=cid, keep=keep,
                             value=str(payload.get("value") or ""), dir=out_dir)
        finally:
            bk.set_brain_dir(old)
        return {"ok": code == 0, "message": out, "data": {"conflicts": _load_conflicts(out_dir)}}
    if action == "adopt-merge":
        src = Path(str(payload.get("dir") or ""))
        if not (src / "manifest.json").exists():
            return {"ok": False, "message": "合并目录不存在或不是有效大脑"}
        backup = bk.BRAIN_DIR.parent / f"brain.bak-{time.strftime('%Y%m%d-%H%M%S')}"
        shutil.copytree(bk.BRAIN_DIR, backup, dirs_exist_ok=True) if not backup.exists() else None
        for item in src.iterdir():
            if item.name in (".keys", "archive"):
                continue
            dst = bk.BRAIN_DIR / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        bk.save_json(bk.LINEAGE_FILE, {})
        bk.refresh_manifest_fingerprint()
        return {"ok": True, "message": f"已采纳合并结果作为当前大脑（旧大脑备份于 {backup.name}）"}
    if action == "cleanup":
        removed = []
        base = bk.MODULE_DIR
        keep_bak = max(0, int(payload.get("keep_bak", 1) or 0))
        for d in sorted(base.glob("brain_merged-*")) + sorted(base.glob("brain_restored-*")):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
        baks = sorted(base.glob("brain.bak-*"), key=lambda p: p.stat().st_mtime, reverse=True)
        for d in baks[keep_bak:]:
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                removed.append(d.name)
        return {"ok": True, "message": ("已清理 " + str(len(removed)) + " 个残留目录：" + "、".join(removed)) if removed else "没有需要清理的残留目录"}
    if action == "consolidate":
        r = consolidate_with_llm()
        return {"ok": True, "message": f"睡眠巩固完成：归档 {r.get('archived', 0)} · 合并 {r.get('merged', 0)} · 现存 {r.get('kept', 0)}", "data": r}
    if action == "diff":
        a = _snapshot_path(payload.get("snap_a"))
        b = _snapshot_path(payload.get("snap_b"))
        if not a or not b:
            return {"ok": False, "message": "请选择两个要对比的快照"}
        code, out = _run(bk.cmd_diff, snap_a=a, snap_b=b,
                         passphrase=str(payload.get("passphrase") or ""))
        return {"ok": code == 0, "message": out, "data": {"output": out}}
    if action == "share-export":
        # 脱敏导出（L8）：身份 + 记忆精华（不含密钥/私密文件）。
        # sensitivity=secret 的记忆默认排除（可经 include_secret 显式包含），
        # 分享端与秘密端隔离——防止把最高敏感事实随手带出。
        include_secret = bool(payload.get("include_secret") or False)
        ident = bk.load_json(bk.BRAIN_DIR / "identity.json", {})
        mems = [e for e in bk.load_memories()
                if not e.get("archived")
                and (include_secret or str(e.get("sensitivity") or "public") != "secret")]
        mems.sort(key=lambda e: (-int(e.get("importance") or 3), bk._ts_epoch(e.get("ts"))))
        mems = mems[:50]
        share = {
            "format": "whale-brain-share-v1",
            "brain_id": bk.load_manifest().get("brain_id"),
            "identity": {"name": ident.get("name"), "vibe": ident.get("vibe"),
                         "principles": ident.get("principles")},
            "memories": [{"type": e.get("type"), "importance": e.get("importance"),
                          "text": e.get("text"), "sensitivity": str(e.get("sensitivity") or "public")}
                         for e in mems],
        }
        data_b64 = base64.b64encode(json.dumps(share, ensure_ascii=False).encode("utf-8")).decode("ascii")
        excl = sum(1 for e in bk.load_memories() if not e.get("archived") and str(e.get("sensitivity") or "") == "secret")
        msg = f"已导出 {len(share['memories'])} 条记忆精华（脱敏）"
        if excl and not include_secret:
            msg += f"；已排除 {excl} 条 secret 敏感记忆"
        return {"ok": True, "message": msg,
                "data": {"download": {"filename": "whale_share.json", "data_b64": data_b64}}}
    if action == "share-import":
        file_b64 = str(payload.get("file_b64") or "")
        if not file_b64:
            return {"ok": False, "message": "缺少分享文件"}
        try:
            share = json.loads(base64.b64decode(file_b64).decode("utf-8"))
        except Exception:
            return {"ok": False, "message": "分享文件解析失败"}
        imported = 0
        for m in share.get("memories", []) or []:
            text = str(m.get("text") or "").strip()
            if not text:
                continue
            if bk.remember_structured(text, type=str(m.get("type") or "分享")[:20],
                                      importance=int(m.get("importance") or 3), source="分享"):
                imported += 1
        return {"ok": True, "message": f"已导入 {imported} 条分享记忆"}
    if action == "brain-switch":
        d = str(payload.get("dir") or "").strip()
        if not d or not (Path(d) / "manifest.json").exists():
            return {"ok": False, "message": "目标目录不是有效大脑（缺 manifest.json）"}
        bk.set_brain_dir(Path(d))
        try:
            _ACTIVE_FILE.write_text(str(Path(d).resolve()), encoding="utf-8")
        except OSError:
            pass
        return {"ok": True, "message": f"已切换到大脑：{d}"}
    if action == "brain-dirs":
        # 列出所有可切换的大脑目录（含 manifest 的目录）
        dirs = []
        for p in sorted(Path(BASE_DIR).glob("*")):
            if p.is_dir() and (p / "manifest.json").exists() and p.name not in ("brain", ".git", "node_modules"):
                dirs.append({"name": p.name, "path": str(p)})
        default = Path(BASE_DIR) / "brain"
        if default.is_dir() and (default / "manifest.json").exists():
            dirs.append({"name": "brain（默认）", "path": str(default)})
        current = str(bk.BRAIN_DIR.resolve())
        for d in dirs:
            d["current"] = str(Path(d["path"]).resolve()) == current
        return {"ok": True, "data": {"dirs": dirs, "current": current}}
    if action == "self-refresh":
        ok = refresh_self_model()
        return {"ok": ok, "message": "自我模型已动态校准" if ok else "校准未执行（需配置 API Key 并初始化大脑）"}
    if action == "goals-list":
        return {"ok": True, "data": {"goals": bk.load_goals()}}
    if action == "goals-add":
        g = bk.add_goal(str(payload.get("title") or ""), str(payload.get("note") or ""))
        return {"ok": bool(g), "message": ("目标已添加" if g else "标题为空或已有进行中的同名目标"), "data": {"goal": g}}
    if action == "goals-update":
        ok = bk.update_goal(str(payload.get("id") or ""),
                            status=payload.get("status"), progress=payload.get("progress"), note=payload.get("note"))
        return {"ok": ok, "message": "目标已更新" if ok else "未找到该目标"}
    if action == "goals-delete":
        ok = bk.delete_goal(str(payload.get("id") or ""))
        return {"ok": ok, "message": "目标已删除" if ok else "未找到该目标"}
    if action == "evolution-record":
        rec = bk.record_evolution(str(payload.get("title") or ""),
                                  kind=str(payload.get("kind") or "adopted"),
                                  note=str(payload.get("note") or ""))
        return {"ok": bool(rec), "message": f"演化账本已记录 {rec['id']}" if rec else "标题为空",
                "data": {"record": rec}}
    return {"ok": False, "message": f"未知动作: {action}"}


def _load_conflicts(d):
    c = bk.load_json(Path(d) / "merge_conflicts.json", {})
    return c.get("conflicts", []) if isinstance(c, dict) else []


def _snapshot_path(v):
    """版本号或路径 → 绝对快照路径；找不到返回空串（消除前端相对路径对 cwd 的依赖）。"""
    if v is None:
        return ""
    s = str(v).strip()
    if s.isdigit():
        p = bk.ARCHIVE_DIR / f"brain_v{s}.whale"
        return str(p) if p.exists() else ""
    if s.startswith("brain_v") and s.endswith(".whale"):
        p = bk.ARCHIVE_DIR / s
        return str(p) if p.exists() else ""
    p = Path(s)
    return str(p.resolve()) if p.exists() else str(p)


def _mem_decay_score(e: dict, now_epoch: float) -> float:
    """记忆新鲜度打分：重要度 × 时间衰减（半衰期约 30 天）。

    旧实现按 (-importance, ts) 纯重要度排序——高重要度旧记忆会长期霸占
    注入位（陈旧固化），新相关记忆反而进不来。打分 = importance / (1 + age_days/30)。
    """
    try:
        imp = float(e.get("importance") or 3)
        age_days = max(0.0, (now_epoch - bk._ts_epoch(e.get("ts"))) / 86400.0)
    except Exception:
        imp, age_days = 3.0, 0.0
    return imp / (1.0 + age_days / 30.0)


def _decay_with_hits(e: dict, now_epoch: float) -> float:
    """F3 增强的注入打分：基础衰减分 + 命中奖励。

    命中次数是"这条记忆确实有用"的证据——多命中应小幅上浮，
    与"重要性随被用而增长"自洽。
    """
    base = _mem_decay_score(e, now_epoch)
    hits = int(e.get("hit_count") or 0)
    if hits <= 0:
        return base
    last = str(e.get("last_hit") or "")
    # 若最近仍在使用（30 天内），命中给真实加分
    recency_bonus = 0.0
    if last:
        age_d = (now_epoch - bk._ts_epoch(last)) / 86400.0
        if 0 <= age_d <= 30:
            recency_bonus = 0.4
    return base + min(0.6, 0.08 * hits) + recency_bonus


_SPACED_INTERVALS = (1, 3, 7, 14, 30, 60)  # F4 复习间隔（天）


def _spaced_review_due(now_epoch: float, limit: int = 2) -> list:
    """F4 间隔复习：选出「到期该复习」的高价值记忆（importance≥4）。

    规则：① 年龄 ≥7 天（跨过首个真正复习间隔）且 <90 天；② 近 3 天未
    被命中（last_hit 距现在 >3 天 或从未命中）——避免同一记忆每轮重复占用
    注入位；③ 按年龄降序取 Top-N。成熟高价值记忆得以定期回到上下文，
    抵消时间衰减的埋葬。"""
    out = []
    for e in bk.load_memories():
        if e.get("archived") or int(e.get("importance") or 3) < 4:
            continue
        if str(e.get("sensitivity") or "public") == "secret":
            continue
        age_d = max(0.0, (now_epoch - bk._ts_epoch(e.get("ts"))) / 86400.0)
        if not (7 <= age_d < 90):
            continue
        last_hit = str(e.get("last_hit") or "")
        recently_hit = False
        if last_hit:
            hit_age = (now_epoch - bk._ts_epoch(last_hit)) / 86400.0
            recently_hit = 0 <= hit_age <= 3
        if recently_hit:
            continue
        out.append(e)
    out.sort(key=lambda e: -int(e.get("importance") or 3))
    return out[:limit]


def brain_context(max_memories=4, query="", budget_chars=0):
    """注入 AI 对话的大脑上下文摘要（身份 + 断点 + 自我认知 + 未决决策 + 复习 + 相关记忆）。

    - query 非空按话题检索（record_hits 记录命中 → F3）；空则按重要度×时间衰减+命中取 Top-N。
    - budget_chars>0 时（L1 预算仲裁）超限按优先级截断。
    - 待回执决策已到期（>3 天 open）会带"请回执"提示。
    - F4 复习：高价值记忆到期候选合并进注入。
    未初始化返回 None。
    """
    try:
        bk.load_manifest()
    except SystemExit:
        return None
    ident = bk.load_json(bk.BRAIN_DIR / "identity.json", {})
    hb = bk.load_json(bk.BRAIN_DIR / "heartbeat.json", {})
    name = (ident.get("name") or "未命名").strip()
    now_epoch = time.time()
    # 段模型：每段 (标题, 内容行[], 权重) —— 供预算仲裁按优先级降级
    segs = []

    def add(title, rows, weight=10):
        if rows:
            segs.append((title, rows, weight))

    lines0 = [f"[鲸语大脑] 我是「{name}」，一个可迁移、可备份、可恢复的思维容器。"]
    hint = str(hb.get("resume_hint") or "").strip()
    if hint:
        lines0.append(f"上次思考断点：{hint}")
    add("身份", lines0, 100)
    try:
        active_goals = [g for g in bk.load_goals() if g.get("status") == "active"]
        if active_goals:
            add("进行中目标", [f"- {g.get('title')}" + (f"（{g.get('progress') or ''}）" if g.get("progress") else "")
                              for g in active_goals[:4]], 60)
    except Exception:
        pass
    try:
        sm = bk.load_json(bk.BRAIN_DIR / "self_model.json", {})
        cap_rows = []
        for label, key in (("我知道", "knows"), ("我不确定", "unknowns"), ("我的局限", "limits")):
            vals = [str(x).strip()[:70] for x in (sm.get(key) or []) if str(x or "").strip()][:2]
            if vals:
                cap_rows.append(f"· {label}：" + "；".join(vals))
        if cap_rows:
            add("自我认知", cap_rows, 50)
    except Exception:
        pass
    try:
        open_decs = [d for d in bk.list_decisions(limit=120) if d.get("status") == "open"]
        if open_decs:
            rows = []
            for d in open_decs[:3]:
                exp = str(d.get("expected") or "").strip()
                age_d = (now_epoch - bk._ts_epoch(d.get("ts"))) / 86400.0 if d.get("ts") else 0
                due_tag = "（请回执结果）" if age_d >= 3 else ""
                rows.append(f"- {str(d.get('decision') or '')[:48]}{due_tag}"
                            + (f"（预期：{exp[:36]}）" if exp else ""))
            add("未决决策", rows, 45)
    except Exception:
        pass
    # F4 间隔复习
    try:
        review = _spaced_review_due(now_epoch, limit=2)
        if review:
            add("复习提醒", [f"- {str(e.get('text') or '')[:60]}" for e in review], 40)
    except Exception:
        pass
    # 相关/近期记忆
    try:
        recent = []
        if str(query or "").strip():
            recent = bk.search_memories(query, max_memories, record_hits=True)
        else:
            mems = [e for e in bk.load_memories()
                    if str(e.get("sensitivity") or "public") != "secret"]
            mems.sort(key=lambda e: (-_decay_with_hits(e, now_epoch), str(e.get("ts") or "")))
            recent = mems[:max_memories]
        if recent:
            rows = []
            for e in recent:
                t = str(e.get("type") or "记忆").strip()
                imp = int(e.get("importance") or 3)
                hits = int(e.get("hit_count") or 0)
                rows.append(f"- [{t}·{imp}" + (f"·命中{hits}" if hits else "") + f"] {str(e.get('text') or '')[:70]}")
            add("近期记忆", rows, 35)
    except Exception:
        pass
    # L1 预算仲裁：按权重从低到高截断，直到总长不超 budget_chars
    segs.sort(key=lambda s: -s[2])
    total = 0
    kept = []
    for title, rows, _weight in segs:
        block = title + "：\n" + "\n".join(rows)
        if budget_chars > 0 and total + len(block) > budget_chars:
            if not kept:
                block = block[:budget_chars]  # 极端：唯一高权重段也截断
            else:
                break
        kept.append(block)
        total += len(block)
    return "\n".join(kept)
