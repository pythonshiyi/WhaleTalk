#!/usr/bin/env python3
"""依赖门禁与锁文件生成（P0）。

为什么不用 pip-compile：`pyautogui` 只有 sdist，pip-compile 在隔离构建元数据时
会失败（本机实测）。这里改为「从实际通过测试/评测的环境计算依赖闭包」——锁文件
记录的是**确实跑通过的版本**，比解析器理想解更贴近发布现实。

用法：
    python tools/check_deps.py --check     # CI 门禁：上限 + 锁文件完整性
    python tools/check_deps.py --freeze    # 重新生成 requirements.lock
    python tools/check_deps.py --verify    # 锁文件 vs 当前已安装版本
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import platform
import re
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
REQUIREMENTS = REPO / "requirements.txt"
LOCK = REPO / "requirements.lock"

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # silent-except: 控制台不支持 reconfigure 时保持默认编码
        pass

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except Exception as e:  # pragma: no cover - 环境缺 packaging 时给出可操作错误
    print(f"[依赖门禁] 缺少 packaging（pip 自带）：{e}")
    raise SystemExit(2) from None


def parse_requirements(path: Path = REQUIREMENTS) -> List[Tuple[str, Requirement, str]]:
    """返回 [(原始行, Requirement, 规范化名)]；跳过注释/空行/选项行。"""
    out: List[Tuple[str, Requirement, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        try:
            req = Requirement(line)
        except Exception as e:
            raise SystemExit(f"[依赖门禁] 无法解析 {line!r}: {e}") from None
        out.append((line, req, canonicalize_name(req.name)))
    return out


def missing_upper_bounds(reqs: List[Tuple[str, Requirement, str]]) -> List[str]:
    bad = []
    for raw, req, _name in reqs:
        if not any(spec.operator in ("<", "<=") for spec in req.specifier):
            bad.append(raw)
    return bad


def _marker_ok(req: Requirement, extra: str = "") -> bool:
    if req.marker is None:
        return True
    try:
        return bool(req.marker.evaluate({"extra": extra}))
    except Exception:
        return True


def dependency_closure(direct: List[str]) -> Tuple[Dict[str, str], List[str]]:
    """从直接依赖出发，沿已安装包的 requires 求闭包。

    只收当前环境**已安装**的包；缺失的依赖记入 warnings（不静默）。
    """
    seen: Set[str] = set()
    versions: Dict[str, str] = {}
    warnings: List[str] = []
    queue: List[str] = list(direct)
    while queue:
        name = canonicalize_name(queue.pop())
        if name in seen:
            continue
        seen.add(name)
        try:
            dist = md.distribution(name)
            versions[name] = dist.version
        except md.PackageNotFoundError:
            warnings.append(f"未安装：{name}")
            continue
        for raw in dist.requires or []:
            try:
                req = Requirement(raw)
            except Exception:
                continue
            # 只跟随当前环境成立的依赖；extras 依赖由直接声明（如 qrcode[pil]）覆盖
            if not _marker_ok(req):
                continue
            dep = canonicalize_name(req.name)
            if dep not in seen:
                queue.append(dep)
    return versions, warnings


def load_lock(path: Path = LOCK) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s;]+)", line)
        if m:
            out[canonicalize_name(m.group(1))] = m.group(2)
    return out


def freeze(direct_reqs: List[Tuple[str, Requirement, str]]) -> int:
    versions, warnings = dependency_closure([n for _r, _q, n in direct_reqs])
    lines = [
        "# 鲸语 WhaleTalk 依赖锁（测试快照）",
        "# 生成：python tools/check_deps.py --freeze",
        f"# 环境：Python {platform.python_version()} / {platform.system()} / {date.today().isoformat()}",
        "# 说明：记录实际通过测试与评测的版本；升级依赖后重新生成并重跑评测。",
        "# 这不是 pip-compile 的理想解，而是「确实跑通过」的现场快照。",
        "",
    ]
    for name in sorted(versions):
        lines.append(f"{name}=={versions[name]}")
    LOCK.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[依赖锁] 已写入 {LOCK.relative_to(REPO)}：{len(versions)} 个包")
    for w in warnings:
        print(f"  [警告] {w}")
    return 0


def check(direct_reqs: List[Tuple[str, Requirement, str]]) -> int:
    errors: List[str] = []
    bad = missing_upper_bounds(direct_reqs)
    if bad:
        errors.append("以下直接依赖缺少上限（< 或 <=）：\n  - " + "\n  - ".join(bad))
    lock = load_lock()
    if not lock:
        errors.append("requirements.lock 不存在或为空；运行 python tools/check_deps.py --freeze")
    else:
        direct_names = {n for _r, _q, n in direct_reqs}
        missing = sorted(direct_names - set(lock))
        if missing:
            errors.append("锁文件缺少直接依赖：\n  - " + "\n  - ".join(missing))
    if errors:
        print("[依赖门禁] 失败：")
        for e in errors:
            print(f"  {e}")
        return 1
    print(f"[依赖门禁] 通过：{len(direct_reqs)} 个直接依赖均有上限；"
          f"锁文件覆盖 {len(lock)} 个包。")
    return 0


def verify(direct_reqs: List[Tuple[str, Requirement, str]]) -> int:
    lock = load_lock()
    if not lock:
        print("[依赖校验] requirements.lock 不存在")
        return 2
    mism: List[str] = []
    for name, want in sorted(lock.items()):
        try:
            got = md.version(name)
        except md.PackageNotFoundError:
            mism.append(f"{name}: 锁 {want}，当前未安装")
            continue
        if got != want:
            mism.append(f"{name}: 锁 {want}，当前 {got}")
    if mism:
        print(f"[依赖校验] {len(mism)} 项与锁文件不一致：")
        for m in mism:
            print(f"  - {m}")
        return 1
    print(f"[依赖校验] 通过：{len(lock)} 个包与锁文件一致。")
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="依赖上限与锁文件门禁")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="CI 门禁：上限 + 锁文件完整性")
    group.add_argument("--freeze", action="store_true", help="重新生成 requirements.lock")
    group.add_argument("--verify", action="store_true", help="锁文件 vs 当前环境")
    args = parser.parse_args(argv)

    reqs = parse_requirements()
    if args.freeze:
        return freeze(reqs)
    if args.verify:
        return verify(reqs)
    return check(reqs)


if __name__ == "__main__":
    raise SystemExit(main())
