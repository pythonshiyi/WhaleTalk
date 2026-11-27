#!/usr/bin/env python3
"""版本与发布纪律门禁（P0）。

背景：VERSION 已是单一事实源，但历史上出现过「源码 3.28.0、CHANGELOG 顶部仍是
未发版、git 没有对应 tag」的三方漂移。本门禁分两档：

    --check   CI 用：VERSION 合法；CHANGELOG 顶部要么是「未发版」，要么就是
              当前 VERSION；README 的版本引用与 VERSION 一致。未打 tag 只警告。
    --strict  发版用：额外要求 git tag v<VERSION> 存在，且 CHANGELOG 顶部
              必须有对应版本标题（不允许「未发版」）。

用法：
    python tools/check_release.py --check
    python tools/check_release.py --strict
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VERSION_FILE = REPO / "config_defaults.py"
CHANGELOG = REPO / "CHANGELOG.md"
README = REPO / "README.md"

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # silent-except: 控制台不支持 reconfigure 时保持默认编码
        pass

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
HEADING_VERSION = re.compile(r"^##\s+(?:\[)?v?(\d+\.\d+\.\d+)(?:\])?")
HEADING_UNRELEASED = re.compile(r"^##\s*未发版")


def read_version() -> str:
    text = VERSION_FILE.read_text(encoding="utf-8", errors="replace")
    m = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("[发布门禁] config_defaults.py 找不到 VERSION")
    version = m.group(1).strip()
    if not SEMVER.match(version):
        raise SystemExit(f"[发布门禁] VERSION 不是 x.y.z：{version!r}")
    return version


def first_changelog_section(text: str) -> tuple[str, str]:
    """返回 (类型, 版本)：类型为 unreleased / version / unknown。"""
    for line in text.splitlines():
        if not line.startswith("## "):
            continue
        if HEADING_UNRELEASED.match(line):
            return "unreleased", ""
        m = HEADING_VERSION.match(line)
        if m:
            return "version", m.group(1)
        return "unknown", line.strip()
    return "unknown", ""


def changelog_versions(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        if line.startswith("## "):
            m = HEADING_VERSION.match(line)
            if m:
                out.append(m.group(1))
    return out


def git_tag_exists(version: str) -> bool:
    try:
        out = subprocess.check_output(
            ["git", "tag", "--list", f"v{version}"],
            cwd=str(REPO), text=True, stderr=subprocess.DEVNULL,
        )
        return bool(out.strip())
    except Exception:
        return False


def readme_version_checks(version: str) -> list[str]:
    errors = []
    text = README.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"当前\s*\*\*([0-9]+\.[0-9]+\.[0-9]+)\*\*", text)
    if m and m.group(1) != version:
        errors.append(f"README「当前版本」为 {m.group(1)}，VERSION 为 {version}")
    m = re.search(r"WhaleTalk\s+v([0-9]+\.[0-9]+\.[0-9]+)", text)
    if m and m.group(1) != version:
        errors.append(f"README「WhaleTalk v...」为 {m.group(1)}，VERSION 为 {version}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="版本/CHANGELOG/tag 一致性门禁")
    parser.add_argument("--check", action="store_true", help="CI 档：宽松，未打 tag 只警告")
    parser.add_argument("--strict", action="store_true", help="发版档：必须有 tag 与版本标题")
    args = parser.parse_args(argv)
    strict = bool(args.strict)

    version = read_version()
    text = CHANGELOG.read_text(encoding="utf-8", errors="replace")
    kind, top_version = first_changelog_section(text)
    errors: list[str] = []
    warnings: list[str] = []

    if not text.lstrip().startswith("#"):
        errors.append("CHANGELOG.md 为空或缺少标题")

    if kind == "unreleased":
        if strict:
            errors.append(f"发版模式不允许 CHANGELOG 顶部仍是「未发版」（VERSION={version}）")
        else:
            warnings.append("CHANGELOG 顶部是「未发版」：发版时请改成版本标题并打 tag")
    elif kind == "version":
        if top_version != version:
            errors.append(
                f"CHANGELOG 顶部版本 {top_version} 与 VERSION {version} 不一致"
            )
    else:
        errors.append(f"CHANGELOG 第一个二级标题无法识别：{top_version!r}")

    if version not in changelog_versions(text) and kind != "unreleased":
        errors.append(f"CHANGELOG 中没有 {version} 的版本条目")

    errors.extend(readme_version_checks(version))

    has_tag = git_tag_exists(version)
    if strict and not has_tag:
        errors.append(f"缺少 git tag v{version}（发版必须打 tag）")
    if not strict and not has_tag:
        warnings.append(f"当前 VERSION 尚无 git tag v{version}（发版时补上）")

    for w in warnings:
        print(f"[发布门禁] 警告：{w}")
    if errors:
        print("[发布门禁] 失败：")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"[发布门禁] 通过：VERSION={version}，CHANGELOG 顶部={kind}"
          f"{('/' + top_version) if top_version else ''}，tag={'有' if has_tag else '无'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
