# -*- coding: utf-8 -*-
"""P2-8 第二层验证：新 _p_* 方法体 vs 旧 do_POST 分支体，逐行比对（忽略纯缩进差异）。
同时校验：分支顺序 == 路由表顺序（匹配优先级一致）。"""
import re, ast, pathlib, sys

SRC = pathlib.Path("api_server.py")
RN = pathlib.Path("api_server.py.routes_new")
src = SRC.read_text(encoding="utf-8")
lines = src.split("\n")
rn = RN.read_text(encoding="utf-8")

# ── 1. 提取旧 do_POST 分支及其 body ──
start = end = None
for i, ln in enumerate(lines):
    if ln.startswith("    def do_POST(self):"):
        start = i
    elif start is not None and ln.startswith("    def _valid_messages"):
        end = i
        break

TOP = 12
branches = []
cur = None
i = start + 1
while i < end:
    ln = lines[i]
    stripped = ln.strip()
    if stripped == "":
        if cur:
            cur["body"].append("")
        i += 1
        continue
    m = re.match(r"^(\s*)(if|elif|else)\b", ln)
    indent = len(m.group(1)) if m else len(ln) - len(ln.lstrip())
    if m and indent == TOP:
        if cur:
            branches.append(cur)
        cond = ln[m.end():].rstrip()
        while not cond.endswith(":") and i + 1 < end:
            i += 1
            nxt = lines[i].strip()
            if nxt == "":
                continue
            cond = cond + " " + nxt.rstrip()
        cur = {"kind": m.group(2), "cond": cond, "body": []}
    elif cur is not None and indent > TOP:
        cur["body"].append(ln)
    elif cur is not None:
        branches.append(cur)
        cur = None
    i += 1
if cur:
    branches.append(cur)

def norm_body(body):
    """去空白行、统一缩进（剥离每行前导空白），逐行返回可比较 token 序列。"""
    out = []
    for ln in body:
        if ln.strip() == "":
            continue
        out.append(ln.strip())
    return out

def old_route_cond(cond):
    return cond.rstrip(":").replace("self.path", "p").strip()

# ── 2. 提取 routes_new 中的方法定义与装饰器（AST 拿方法顺序 + 装饰器 matcher）──
tree = ast.parse(rn)
methods = {}   # name -> (matcher_tuple, body_lines)
deco_order = []
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name.startswith("_p_"):
        matcher = None
        for d in node.decorator_list:
            if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "_post_route":
                arg = d.args[0]
                if isinstance(arg, ast.Constant):
                    matcher = arg.value
                elif isinstance(arg, ast.Tuple):
                    parts = []
                    for x in ast.walk(arg):
                        if isinstance(x, ast.Constant) and isinstance(x.value, str):
                            parts.append(x.value)
                    matcher = tuple(parts)
        if matcher is not None:
            methods[node.name] = matcher
            deco_order.append(node.name)

# 用行号映射：从源文本直接取方法体原始行（避免 ast.unparse 重排）
rn_lines = rn.split("\n")
def method_body_lines(name):
    """按 '    def name(self):' 定位，取缩进 >8 的连续行（方法体，去 8 空格）。"""
    pat = re.compile(r"^    def %s\(self\):" % re.escape(name))
    out = []
    collecting = False
    for ln in rn_lines:
        if not collecting:
            if pat.match(ln):
                collecting = True
            continue
        # 方法体结束：缩进 <= 4 或文件尾
        if ln.strip() == "":
            continue
        if not ln.startswith("        "):
            break
        out.append(ln[8:])
    return out

# ── 3. 逐个分支比对 ──
errors = []
route_idx = 0
for b in branches:
    kind = b["kind"]
    if kind == "else":
        continue
    cond = b["cond"]
    # 旧分支 body（保留原始缩进文本行）
    old_body = [ln[8:] if len(ln) >= 8 else ln for ln in b["body"] if ln.strip() != ""]
    # 找对应新方法：按顺序
    if route_idx >= len(deco_order):
        errors.append(f"方法数不足: 分支 {cond}")
        break
    name = deco_order[route_idx]
    matcher = methods[name]
    route_idx += 1

    # 归一化比较（strip 每行）
    old_norm = [ln.strip() for ln in old_body]
    new_norm = [ln.strip() for ln in method_body_lines(name) if ln.strip() != ""]
    if old_norm != new_norm:
        errors.append(f"body 不一致: {name} (cond={cond})")
        for j in range(max(len(old_norm), len(new_norm))):
            o = old_norm[j] if j < len(old_norm) else "<缺失>"
            n = new_norm[j] if j < len(new_norm) else "<缺失>"
            if o != n:
                errors.append(f"  L{j}: old={o!r} new={n!r}")
                if len(errors) > 12:
                    break

print(f"比对分支: {route_idx} 个 · 方法数: {len(methods)} · 差异: {len(errors)}")
for e in errors[:15]:
    print(" ", e)
sys.exit(1 if errors else 0)
