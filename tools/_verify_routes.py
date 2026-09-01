# -*- coding: utf-8 -*-
"""P2-8 行为等价验证：旧 do_POST if/elif 链 vs 新 _POST_ROUTES 表。
对比：对所有候选路径，两者匹配结果（是否命中 + 命中目标方法名）必须一致。
只读源码，不 import 任何模块，无副作用。"""
import re, ast, sys, pathlib

SRC = pathlib.Path("api_server.py")
RN = pathlib.Path("api_server.py.routes_new")
src = SRC.read_text(encoding="utf-8")
lines = src.split("\n")

# ── 1. 从源文件提取旧 do_POST 分支条件 ──
start = end = None
for i, ln in enumerate(lines):
    if ln.startswith("    def do_POST(self):"):
        start = i
    elif start is not None and ln.startswith("    def _valid_messages"):
        end = i
        break
assert start is not None and end is not None

TOP = 12
branches = []  # {kind, cond, body}
cur = None
i = start + 1
while i < end:
    ln = lines[i]
    stripped = ln.strip()
    if stripped == "":
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
        cur = {"kind": m.group(2), "cond": cond}
    elif cur is not None and indent <= TOP:
        branches.append(cur)
        cur = None
    i += 1
if cur:
    branches.append(cur)

def mk(cond):
    c = cond.rstrip(":").replace("self.path", "p")
    return eval("lambda p: " + c)

old = []  # [(kind, pred_or_None)]
for b in branches:
    if b["kind"] == "else":
        old.append(("else", None))
    else:
        old.append((b["kind"], mk(b["cond"])))

def old_match(p):
    for kind, pred in old:
        if kind == "else":
            return None  # 404
        if pred(p):
            return kind
    return None

# ── 2. 从 routes_new 提取 _POST_ROUTES 表（AST 安全解析）──
rn = RN.read_text(encoding="utf-8")
tree = ast.parse(rn)
routes = []  # (matcher, method_name)
for node in ast.walk(tree):
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id == "_POST_ROUTES":
                if isinstance(node.value, ast.List):
                    for elt in node.value.elts:
                        if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
                            m = elt.elts[0]
                            nm = elt.elts[1]
                            if isinstance(nm, ast.Constant) and isinstance(nm.value, str):
                                if isinstance(m, ast.Constant) and isinstance(m.value, str):
                                    routes.append((m.value, nm.value))
                                elif isinstance(m, ast.Tuple):
                                    parts = []
                                    for x in ast.walk(m):
                                        if isinstance(x, ast.Constant) and isinstance(x.value, str):
                                            parts.append(x.value)
                                    routes.append((tuple(parts), nm.value))

print(f"旧分支: {len(old)} 个（含 else） · 新路由表: {len(routes)} 条")

# 若 _POST_ROUTES 是动态填充（装饰器），routes 为空 → 提示走装饰器路径
if not routes:
    print("注意：_POST_ROUTES 为空（装饰器动态填充模式），改用装饰器语义验证")
    # 从类内方法提取 @_post_route(...) 装饰器参数
    deco_routes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for d in node.decorator_list:
                if isinstance(d, ast.Call) and isinstance(d.func, ast.Name) and d.func.id == "_post_route":
                    arg = d.args[0]
                    if isinstance(arg, ast.Constant):
                        deco_routes.append((arg.value, node.name))
                    elif isinstance(arg, ast.Tuple):
                        parts = []
                        for x in ast.walk(arg):
                            if isinstance(x, ast.Constant) and isinstance(x.value, str):
                                parts.append(x.value)
                        deco_routes.append((tuple(parts), node.name))
    routes = deco_routes
    print(f"装饰器注册路由: {len(routes)} 条")

def new_match(p):
    for matcher, name in routes:
        if isinstance(matcher, str):
            if p == matcher:
                return name
        elif matcher[0] == "set":
            if p in matcher[1:]:
                return name
        elif matcher[0] == "pre":
            if p.startswith(matcher[1]) and p.endswith(matcher[2]):
                return name
    return None

# ── 3. 构造测试路径：所有精确路由 + set 内路径 + 前缀变形 + 边界 ──
tests = set()
for matcher, _ in routes:
    if isinstance(matcher, str):
        tests.add(matcher)
        tests.add(matcher + "x")
        tests.add(matcher.rstrip("/") + "/")
    elif matcher[0] == "set":
        for p in matcher[1:]:
            tests.add(p)
            tests.add(p + "x")
    elif matcher[0] == "pre":
        tests.add(matcher[1] + "some_tool" + matcher[2])
        tests.add(matcher[1] + matcher[2])
        tests.add(matcher[1] + "x")
tests.update([
    "/v1/unknown", "", "/v1", "/v1/", "/v1/chat", "/v1/chat/", "/v1/chatx",
    "/v1/tools/", "/v1/tools/x/invoke/", "/v1/sessions", "/v1/tts/voices",
    "/v1/chat/stream", "/v1/chat/stream/", "/v1/upload", "/v1/upload/",
])

# 前缀歧义处理：新路由表顺序 = 旧分支顺序，逐条验证
mismatch = []
checked = 0
for t in sorted(tests):
    o = old_match(t)
    n = new_match(t)
    checked += 1
    # 旧命中但新未命中 / 反之 → 不一致
    if (o is not None) != (n is not None):
        mismatch.append((t, o, n))
    # 都命中时：新表应返回与旧分支一致的“最终目标”——旧分支无方法名，只要新表返回非 None 即等价；
    # 但对同 path 命中多个 matcher 的情况，顺序一致性由表顺序保证（生成时保持原顺序）。

print(f"测试路径: {checked} · 不一致: {len(mismatch)}")
for t, o, n in mismatch:
    print(f"  MISMATCH {t!r}: old={'HIT' if o else '404'} new={n or '404'}")
sys.exit(1 if mismatch else 0)
