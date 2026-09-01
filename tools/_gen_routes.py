# -*- coding: utf-8 -*-
"""P2-8 辅助脚本：把 api_server.py 的 do_POST if/elif 链抽取为
装饰器注册的端点方法 + 轻量路由表。生成结果写入 .routes_new 文件供审查。

v2 修复：
- `else:`（404 兜底）正确识别并跳过（正则用词边界而非要求空白，兼容冒号紧贴）
- `except`/`try` 等 do_POST 顶层结构不再混入分支 body（缩进 <= TOP 即结束收集）
- 路由表改为装饰器动态注册（_POST_ROUTES 由 @_post_route 填充，单一来源）
- 模块级设施（装饰器/查表函数）生成在 class _Handler 定义之前；
  端点方法与新 do_POST 生成在类内原 do_POST 位置
"""
import re, pathlib

SRC = pathlib.Path("api_server.py")
txt = SRC.read_text(encoding="utf-8")
lines = txt.split("\n")

# 定位 do_POST 与下一个类方法
start = None
end = None
for i, ln in enumerate(lines):
    if ln.startswith("    def do_POST(self):"):
        start = i
    elif start is not None and ln.startswith("    def _valid_messages"):
        end = i
        break
assert start is not None and end is not None, f"start={start} end={end}"

# 定位 class _Handler 定义（模块级设施插到它之前）
cls_start = None
for i, ln in enumerate(lines):
    if ln.startswith("class _Handler(BaseHTTPRequestHandler):"):
        cls_start = i
        break
assert cls_start is not None, "class _Handler 未找到"

TOP = 12  # do_POST 顶层 if/elif 缩进
# 提取分支：从 start+1 起找 TOP 缩进的 if/elif/else，到 end-1 止。
# 缩进 > TOP 的行属于当前分支 body；缩进 <= TOP 的非分支行（try/except 等）结束收集。
branches = []  # (kind, matcher_code, body_lines)
cur = None
i = start + 1
while i < end:
    ln = lines[i]
    stripped = ln.strip()
    if stripped == "":
        if cur is not None:
            cur["body"].append(ln)
        i += 1
        continue
    m = re.match(r"^(\s*)(if|elif|else)\b", ln)
    indent = len(m.group(1)) if m else len(ln) - len(ln.lstrip())
    if m and indent == TOP:
        if cur:
            branches.append(cur)
        cond = ln[m.end():].rstrip()
        # 多行条件：继续拼接直到冒号结尾
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
        # 缩进 <= TOP 的非分支行（except/try/else 等 do_POST 顶层结构）→ 结束当前分支
        branches.append(cur)
        cur = None
        # 该行本身（如 except）不归属任何分支，跳过
    i += 1
if cur:
    branches.append(cur)

print(f"提取分支 {len(branches)} 个")

def parse_matcher(cond):
    """从条件表达式解析路由 matcher。"""
    cond = cond.strip().rstrip(":")
    m = re.match(r"self\.path\s*==\s*\"([^\"]+)\"$", cond)
    if m:
        return ("exact", m.group(1))
    m = re.match(r"self\.path\s+in\s+\(([^)]+)\)$", cond)
    if m:
        paths = re.findall(r"\"([^\"]+)\"", m.group(1))
        return ("set", tuple(paths))
    m = re.match(r"self\.path\.startswith\(\"([^\"]+)\"\)\s+and\s+self\.path\.endswith\(\"([^\"]+)\"\)$", cond)
    if m:
        return ("pre", m.group(1), m.group(2))
    raise ValueError(f"无法解析条件: {cond}")

def method_name(matcher, idx, used):
    if matcher[0] == "exact":
        base = matcher[1].strip("/").replace("/", "_")
    elif matcher[0] == "set":
        base = matcher[1][0].strip("/").replace("/", "_") + "_actions"
    else:
        base = matcher[1].strip("/").replace("/", "_") + "_invoke"
    name = f"_p_{base}" if base else f"_p_route{idx}"
    n = 1
    orig = name
    while name in used:
        n += 1
        name = f"{orig}_{n}"
    used.add(name)
    return name

def matcher_expr(matcher):
    if matcher[0] == "exact":
        return f'"{matcher[1]}"'
    if matcher[0] == "set":
        return f'("set", {list(matcher[1])!r})'
    return f'("pre", "{matcher[1]}", "{matcher[2]}")'

used = set()
methods = []
routes = []
for idx, br in enumerate(branches):
    kind = br["kind"]
    if kind == "else":
        continue  # else = 404，由新 do_POST 兜底
    matcher = parse_matcher(br["cond"])
    name = method_name(matcher, idx, used)
    routes.append((matcher, name))
    body = br["body"]
    # 去掉 body 首尾纯空行
    while body and body[0].strip() == "":
        body.pop(0)
    while body and body[-1].strip() == "":
        body.pop()
    # body 每行去 8 空格缩进（分支体 16 → 方法体 8）
    dedent = []
    for ln in body:
        if ln.strip() == "":
            dedent.append("")
        else:
            dedent.append(ln[8:] if len(ln) >= 8 else ln)
    methods.append((name, matcher, dedent))

# ── 模块级设施（生成在 class _Handler 之前）──
facility = []
facility.append("# P2-8：POST 端点路由表（装饰器注册，端点方法就近声明；路由表由 _post_route 动态生成）")
facility.append("_POST_ROUTES = []  # (matcher, method_name)，顺序即匹配优先级（类定义时由 @_post_route 填充）")
facility.append("")
facility.append("")
facility.append("def _post_route(matcher):")
facility.append("    \"\"\"端点装饰器：把 (matcher, 方法名) 注册进 _POST_ROUTES（顺序即优先级）。\"\"\"")
facility.append("    def deco(fn):")
facility.append("        _POST_ROUTES.append((matcher, fn.__name__))")
facility.append("        return fn")
facility.append("    return deco")
facility.append("")
facility.append("")
facility.append("def _match_post_route(path):")
facility.append("    \"\"\"查表分发：返回匹配的端点方法名；无匹配返回 None。\"\"\"")
facility.append("    for matcher, name in _POST_ROUTES:")
facility.append("        if isinstance(matcher, str):")
facility.append("            if path == matcher:")
facility.append("                return name")
facility.append("        elif matcher[0] == \"set\":")
facility.append("            if path in matcher[1]:")
facility.append("                return name")
facility.append("        elif matcher[0] == \"pre\":")
facility.append("            if path.startswith(matcher[1]) and path.endswith(matcher[2]):")
facility.append("                return name")
facility.append("    return None")
facility.append("")
facility.append("")

# ── 类内端点方法（生成在原 do_POST 位置）──
methods_code = []
for name, matcher, body in methods:
    methods_code.append(f"    @_post_route({matcher_expr(matcher)})")
    methods_code.append(f"    def {name}(self):")
    if body:
        for ln in body:
            methods_code.append(ln)
    else:
        methods_code.append("        pass")
    methods_code.append("")
    methods_code.append("")

new_do_post = [
    "    def do_POST(self):",
    "        if not self._auth():",
    "            self._json(401, {\"error\": \"unauthorized\"})",
    "            return",
    "        try:",
    "            self.path = self._strip_api_prefix(self.path)",
    "            handler = _match_post_route(self.path)",
    "            if handler is None:",
    "                self._json(404, {\"error\": \"not found\"})",
    "                return",
    "            getattr(self, handler)()",
    "        except Exception as e:",
    "            logger.exception(\"POST %s 失败\", self.path)",
    "            self._json(500, {\"error\": _friendly_error(e), \"code\": 500, \"detail\": str(e)})",
]

# 组装：文件开头..class 定义前 + 模块级设施 + class 头..do_POST 前 + 端点方法 + 新 do_POST + tail
head0 = lines[:cls_start]            # class 定义之前
cls_head = lines[cls_start:start]    # class _Handler .. do_POST 之前（含 do_GET 等类内代码）
tail = lines[end:]                   # _valid_messages 起
new_file = head0 + facility + cls_head + methods_code + new_do_post + [""] + tail
SRC_NEW = pathlib.Path("api_server.py.routes_new")
SRC_NEW.write_text("\n".join(new_file), encoding="utf-8")
print(f"生成完成: {SRC_NEW}（{len(new_file)} 行）")
print(f"路由条数: {len(routes)} · 方法数: {len(methods)}")
