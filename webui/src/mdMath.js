// ── 轻量 LaTeX 数学排版（手写 · 零依赖 · 流式安全）────────────────
// P2-1：公式不再纯文本占位，支持常用 LaTeX 子集的排版渲染。
// 能力清单：
//   上下标   x^2 / x_1 / x_i^2 / a^{b+c} / a_{ij}
//   分数     \frac{a}{b}
//   根号     \sqrt{x} / \sqrt[3]{y}
//   希腊字母 \alpha \beta \gamma ... \omega（含大写 \Gamma \Delta ... \Omega）
//   运算符   \times \div \pm \cdot \leq \geq \neq \approx \in \subset \cup
//            \cap \sum \prod \int \infty \partial \nabla \rightarrow ...
//   括号缩放 \left( ... \right) / \left[ ... \right] / \left\{ ... \right\}
//   文本     \text{中文说明}
//   空格     \, \; \: \! \quad \qquad ~
//   转义     \{ \} \[ \] \$ \% \& \# \_
// 安全：解析为纯数据节点树后由 React 渲染（无 HTML 注入、无 dangerouslySetInnerHTML）。
// 流式安全：未知命令/未闭合括号原样输出，绝不吞内容。
//
// 输出节点类型（纯数据，便于 node 直跑测试）：
//   {t:'text', v} | {t:'greek', v} | {t:'op', v}
//   {t:'script', base, sub, sup} | {t:'frac', num, den}
//   {t:'sqrt', body, index} | {t:'paren', open, close, c}
//   {t:'space', v}

// ── 希腊字母 / 运算符转写表（Unicode 数学符号）──────────────
const GREEK = {
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", epsilon: "ε", varepsilon: "ε",
  zeta: "ζ", eta: "η", theta: "θ", vartheta: "ϑ", iota: "ι", kappa: "κ",
  lambda: "λ", mu: "μ", nu: "ν", xi: "ξ", omicron: "ο", pi: "π",
  rho: "ρ", sigma: "σ", tau: "τ", upsilon: "υ", phi: "φ", varphi: "φ",
  chi: "χ", psi: "ψ", omega: "ω",
  Gamma: "Γ", Delta: "Δ", Theta: "Θ", Lambda: "Λ", Xi: "Ξ", Pi: "Π",
  Sigma: "Σ", Upsilon: "Υ", Phi: "Φ", Psi: "Ψ", Omega: "Ω",
};

const OPS = {
  times: "×", div: "÷", pm: "±", mp: "∓", cdot: "⋅", ast: "∗", circ: "∘",
  leq: "≤", geq: "≥", neq: "≠", approx: "≈", equiv: "≡", sim: "∼", propto: "∝",
  in: "∈", notin: "∉", subset: "⊂", subseteq: "⊆", supset: "⊃", supseteq: "⊇",
  cup: "∪", cap: "∩", setminus: "∖", forall: "∀", exists: "∃",
  infty: "∞", partial: "∂", nabla: "∇",
  sum: "∑", prod: "∏", int: "∫", iint: "∬", oint: "∮",
  rightarrow: "→", leftarrow: "←", leftrightarrow: "↔",
  Rightarrow: "⇒", Leftarrow: "⇐", Leftrightarrow: "⇔",
  to: "→", gets: "←", uparrow: "↑", downarrow: "↓", mapsto: "↦",
  ldots: "…", cdots: "⋯", vdots: "⋮", ddots: "⋱", prime: "′", degree: "°",
  angle: "∠", perp: "⊥", parallel: "∥", therefore: "∴", because: "∵",
  neg: "¬", land: "∧", lor: "∨", implies: "⟹", iff: "⟺",
  triangle: "△", square: "□", diamond: "◇",
};

const SPACE_MAP = { ",": "\u2009", ":": "\u2005", ";": "\u2004", "!": "\u200b", quad: "\u2003\u2003", qquad: "\u2003\u2003\u2003\u2003" };

// ── 词法：命令 / 花括号组 / 上下标 / 普通字符 ────────────
function tokenize(src) {
  const toks = [];
  let i = 0;
  const n = src.length;
  while (i < n) {
    const ch = src[i];
    if (ch === "\\") {
      let j = i + 1;
      while (j < n && /[a-zA-Z]/.test(src[j])) j++;
      if (j > i + 1) { toks.push({ t: "cmd", name: src.slice(i + 1, j) }); i = j; }
      else if (j < n) { toks.push({ t: "char", v: src[j] }); i = j + 1; } // 转义单个符号
      else { toks.push({ t: "char", v: "\\" }); i = j + 1; }
    } else if (ch === "{") { toks.push({ t: "lbrace" }); i++; }
    else if (ch === "}") { toks.push({ t: "rbrace" }); i++; }
    else if (ch === "^") { toks.push({ t: "sup" }); i++; }
    else if (ch === "_") { toks.push({ t: "sub" }); i++; }
    else if (ch === "~") { toks.push({ t: "cmd", name: " " }); i++; } // ~ → 不可断空格
    else { toks.push({ t: "char", v: ch }); i++; }
  }
  return toks;
}

// ── 递归下降解析：返回 [nodes, 下一位置] ─────────────
// 读取一个"单元"：单个字符 / 花括号组 / 命令
function parseUnit(toks, pos) {
  if (pos >= toks.length) return [null, pos];
  const tok = toks[pos];
  if (tok.t === "lbrace") {
    const [c, next] = parseExpr(toks, pos + 1, true);
    return [{ t: "group", c }, next];
  }
  if (tok.t === "char") return [{ t: "text", v: tok.v }, pos + 1];
  if (tok.t === "cmd") {
    const name = tok.name;
    if (name === " ") return [{ t: "space", v: "\u00a0" }, pos + 1];
    if (name === "frac") {
      const [num, p1] = parseUnit(toks, pos + 1);
      const [den, p2] = num ? parseUnit(toks, p1) : [null, p1];
      return [{ t: "frac", num, den }, p2];
    }
    if (name === "sqrt") {
      let p = pos + 1;
      let index = null;
      if (p < toks.length && toks[p].t === "char" && toks[p].v === "[") {
        const [idx, pn] = parseBracket(toks, p + 1);
        if (idx) { index = idx; p = pn; }
      }
      const [body, p2] = parseUnit(toks, p);
      return [{ t: "sqrt", body, index }, p2];
    }
    if (name === "text") {
      const [g, p2] = parseUnit(toks, pos + 1);
      if (g && g.t === "group") {
        return [{ t: "text", v: collectText(g.c) }, p2];
      }
      return [{ t: "text", v: `\\text{…}` }, p2];
    }
    if (name === "left" || name === "right") {
      // 交给 parseExpr 的括号配对逻辑处理；此处单独出现则原样输出
      let p = pos + 1;
      let delim = "(";
      if (p < toks.length && toks[p].t === "char") { delim = toks[p].v; p++; }
      else if (p < toks.length && toks[p].t === "cmd") { delim = toks[p].name; p++; }
      return [{ t: name === "left" ? "lopen" : "lclose", delim }, p];
    }
    if (Object.prototype.hasOwnProperty.call(GREEK, name)) return [{ t: "greek", v: GREEK[name] }, pos + 1];
    if (Object.prototype.hasOwnProperty.call(OPS, name)) return [{ t: "op", v: OPS[name] }, pos + 1];
    if (Object.prototype.hasOwnProperty.call(SPACE_MAP, name)) return [{ t: "space", v: SPACE_MAP[name] }, pos + 1];
    return [{ t: "text", v: "\\" + name }, pos + 1]; // 未知命令原样保留
  }
  return [null, pos];
}

// \sqrt[3]{x} 的 [..] 参数
function parseBracket(toks, pos) {
  const [c, next] = parseExpr(toks, pos, false, "rbrk");
  return [c && c.length ? { t: "group", c } : null, next];
}

// 解析一串单元直到 }（stopAtRbrace）或 [（stopAtRbrk）或结尾；
// 处理上下标（^ _ 后接一个单元，附着到前一个节点）
function parseExpr(toks, pos, stopAtRbrace, stopAtRbrk) {
  const nodes = [];
  let i = pos;
  const n = toks.length;
  while (i < n) {
    const tok = toks[i];
    if (tok.t === "rbrace" && stopAtRbrace) { i++; break; }
    if (tok.t === "char" && tok.v === "]" && stopAtRbrk) { i++; break; }
    if (tok.t === "sup" || tok.t === "sub") {
      const kind = tok.t;
      const [unit, p] = parseUnit(toks, i + 1);
      if (unit) {
        i = p;
        // 附着到前一个节点（x^2 / x_1 / x_i^2 连续附着）
        const base = nodes.pop() || { t: "text", v: "" };
        const script = base.t === "script"
          ? (kind === "sup" ? { ...base, sup: unit } : { ...base, sub: unit })
          : (kind === "sup" ? { t: "script", base, sub: null, sup: unit } : { t: "script", base, sub: unit, sup: null });
        nodes.push(script);
      } else {
        i++; // 孤立的 ^ 或 _：跳过，不吞内容
      }
      continue;
    }
    const [unit, p] = parseUnit(toks, i);
    if (unit) {
      if (unit.t === "lopen") {
        // \left( ... \right) 配对 → paren 节点
        const [pair, p2] = parseParenGroup(toks, p, unit.delim);
        if (pair) { nodes.push(pair.node); i = pair.next; continue; }
        nodes.push({ t: "text", v: "\\left" + unit.delim }); // 未闭合：原样
        i = p;
        continue;
      }
      if (unit.t === "lclose") {
        nodes.push({ t: "text", v: "\\right" + unit.delim }); // 孤立 \right：原样
        i = p;
        continue;
      }
      nodes.push(unit);
      i = p;
    }
    else i++;
  }
  return [nodes, i];
}

// \left<delim> ... \right<delim> 配对：返回 {node, next}
function parseParenGroup(toks, pos, openDelim) {
  const { nodes, closeDelim, next } = parseExprUntilRight(toks, pos);
  return [{ node: { t: "paren", open: openDelim, close: closeDelim, c: nodes }, next }, next];
}

function parseExprUntilRight(toks, pos) {
  const nodes = [];
  let i = pos;
  const n = toks.length;
  while (i < n) {
    const tok = toks[i];
    if (tok.t === "cmd" && tok.name === "right") {
      // \right 后接分隔符（可省略）
      let p = i + 1;
      let delim = ")";
      if (p < n && toks[p].t === "char") { delim = toks[p].v; p++; }
      else if (p < n && toks[p].t === "cmd") { delim = toks[p].name; p++; }
      return { nodes, closeDelim: delim, next: p };
    }
    if (tok.t === "rbrace") break;
    if (tok.t === "sup" || tok.t === "sub") {
      const kind = tok.t;
      const [unit, p] = parseUnit(toks, i + 1);
      if (unit) {
        i = p;
        const base = nodes.pop() || { t: "text", v: "" };
        nodes.push(base.t === "script"
          ? (kind === "sup" ? { ...base, sup: unit } : { ...base, sub: unit })
          : (kind === "sup" ? { t: "script", base, sub: null, sup: unit } : { t: "script", base, sub: unit, sup: null }));
      } else i++;
      continue;
    }
    const [unit, p] = parseUnit(toks, i);
    if (unit) {
      if (unit.t === "lopen") {
        const [pair, p2] = parseParenGroup(toks, p, unit.delim);
        if (pair) { nodes.push(pair.node); i = pair.next; continue; }
        nodes.push({ t: "text", v: "\\left" + unit.delim });
        i = p;
        continue;
      }
      if (unit.t === "lclose") {
        nodes.push({ t: "text", v: "\\right" + unit.delim });
        i = p;
        continue;
      }
      nodes.push(unit);
      i = p;
    }
    else i++;
  }
  return { nodes, closeDelim: null, next: i };
}

// 花括号组 → 纯文本（\text{...} 用）
function collectText(nodes) {
  let s = "";
  for (const nd of nodes) {
    if (nd.t === "text") s += nd.v;
    else if (nd.t === "space") s += nd.v;
    else if (nd.t === "group") s += collectText(nd.c);
    else if (nd.t === "greek" || nd.t === "op") s += nd.v;
    else if (nd.t === "script") s += collectText([nd.base]) + (nd.sup ? collectText([nd.sup]) : "") + (nd.sub ? collectText([nd.sub]) : "");
    else if (nd.t === "paren") s += nd.open + collectText(nd.c) + (nd.close || "");
    else s += "";
  }
  return s;
}

// ── 公共入口：字符串 → 节点树 ─────────────
export function parseMath(src) {
  if (src == null) return [];
  const toks = tokenize(String(src));
  const [nodes] = parseExpr(toks, 0, false);
  return nodes;
}

// ── React 渲染（分离：本模块不 import React，渲染层注入）─────────────
// 返回 (React, reactEl) 的工厂，方便 node 直测与浏览器共用同一解析逻辑
export function mathToReact(React, nodes) {
  const R = React;
  const renderNode = (node, key) => {
    if (!node) return null;
    switch (node.t) {
      case "text": return R.createElement("span", { key, className: "mx-text" }, node.v);
      case "greek": return R.createElement("span", { key, className: "mx-greek" }, node.v);
      case "op": return R.createElement("span", { key, className: "mx-op" }, node.v);
      case "space": return R.createElement("span", { key, className: "mx-space" }, node.v);
      case "group": return R.createElement("span", { key, className: "mx-group" }, node.c.map((nd, i) => renderNode(nd, i)));
      case "script": {
        const kids = [];
        kids.push(R.createElement("span", { key: "b", className: "mx-base" }, renderNode(node.base, "i")));
        if (node.sub) kids.push(R.createElement("span", { key: "u", className: "mx-sub" }, renderNode(node.sub, "j")));
        if (node.sup) kids.push(R.createElement("span", { key: "s", className: "mx-sup" }, renderNode(node.sup, "k")));
        return R.createElement("span", { key, className: "mx-script" }, kids);
      }
      case "frac": {
        return R.createElement("span", { key, className: "mx-frac" },
          R.createElement("span", { className: "mx-num" }, renderNode(node.num, "n")),
          R.createElement("span", { className: "mx-line" }),
          R.createElement("span", { className: "mx-den" }, renderNode(node.den, "d")),
        );
      }
      case "sqrt": {
        const kids = [];
        if (node.index) kids.push(R.createElement("span", { key: "i", className: "mx-sqrt-idx" }, renderNode(node.index, "x")));
        kids.push(R.createElement("span", { key: "r", className: "mx-radical" }, "√"));
        kids.push(R.createElement("span", { key: "b", className: "mx-sqrt-body" }, renderNode(node.body, "y")));
        return R.createElement("span", { key, className: "mx-sqrt" }, kids);
      }
      case "paren": {
        const kids = [];
        kids.push(R.createElement("span", { key: "o", className: "mx-paren-open" }, node.open));
        kids.push(R.createElement("span", { key: "c", className: "mx-paren-body" }, node.c.map((nd, i) => renderNode(nd, i))));
        kids.push(R.createElement("span", { key: "z", className: "mx-paren-close" }, node.close || ""));
        return R.createElement("span", { key, className: "mx-paren" }, kids);
      }
      default:
        return R.createElement("span", { key, className: "mx-text" }, node.v);
    }
  };
  return nodes.map((nd, i) => renderNode(nd, i));
}
