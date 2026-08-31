// ── 轻量语法高亮（手写 · 零依赖）────────────────────────
// 规则优先级：行注释/块注释/字符串 > 数字 > 关键字 > 内置函数 > 类型 > 属性。
// 输出 HTML 字符串（已转义），由 React dangerouslySetInnerHTML 注入。
// CSS 类：hl-com / hl-str / hl-num / hl-kw / hl-fn / hl-ty / hl-at / hl-tag

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function escRe(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// ── 语言表 ────────────────────────────────
const LANG = {
  js: {
    lineComment: "//", blockComment: ["/*", "*/"], strings: ['"', "'", "`"],
    keywords: "const let var function return if else for while do switch case break continue new class extends super this async await try catch finally throw typeof instanceof delete in of import export default from require module exports undefined null true false void yield static get set",
    builtins: "console Math JSON Promise Object Array Set Map WeakMap String Number Boolean Date RegExp Error TypeError RangeError fetch setTimeout clearTimeout setInterval clearInterval parseInt parseFloat isNaN isFinite encodeURIComponent decodeURIComponent",
  },
  ts: { parent: "js", keywords: "const let var function return if else for while do switch case break continue new class extends super this async await try catch finally throw typeof instanceof delete in of import export default from require module exports undefined null true false void yield static get set interface type enum namespace declare readonly private protected public implements abstract",
    builtins: "console Math JSON Promise Object Array Set Map WeakMap String Number Boolean Date RegExp Error TypeError RangeError fetch setTimeout clearTimeout setInterval clearInterval parseInt parseFloat isNaN isFinite Record Partial Pick Omit Readonly Exclude" },
  jsx: { parent: "js" },
  tsx: { parent: "ts" },
  python: {
    lineComment: "#", strings: ['"', "'"], triple: ['"""', "'''"],
    keywords: "def return if elif else for while in not and or is None True False import from as with try except finally raise class lambda pass break continue global nonlocal yield assert del async await match case",
    builtins: "print len range str int float list dict set tuple bool type isinstance enumerate zip map filter sorted sum min max open input repr abs round any all next iter dir hasattr getattr setattr super object property staticmethod classmethod __init__ __name__ __file__ __str__ __repr__",
  },
  py: { parent: "python" },
  bash: {
    lineComment: "#", strings: ['"', "'"],
    keywords: "if then else elif fi for do done while case esac function in select until return local export set unset readonly declare typeset shift source break continue",
    builtins: "echo cd ls cat grep sed awk pwd mkdir rm cp mv touch chmod sudo curl wget find xargs head tail wc sort uniq cut tr ps kill exit sleep date printf read test [ ] true false",
  },
  shell: { parent: "bash" }, sh: { parent: "bash" },
  json: { strings: ['"'], keywords: "true false null", numbers: true },
  sql: {
    lineComment: "--", blockComment: ["/*", "*/"], strings: ["'"],
    keywords: "SELECT FROM WHERE INSERT INTO VALUES UPDATE SET DELETE CREATE TABLE JOIN INNER LEFT RIGHT FULL OUTER ON GROUP BY ORDER HAVING LIMIT OFFSET AND OR NOT NULL AS DISTINCT UNION ALL CASE WHEN THEN ELSE END PRIMARY KEY FOREIGN REFERENCES INDEX DROP ALTER ADD CONSTRAINT DEFAULT UNIQUE CHECK EXISTS BETWEEN LIKE IN IS ASC DESC",
    builtins: "COUNT SUM AVG MIN MAX NOW DATE DATETIME TIMESTAMP CURRENT_TIMESTAMP ROW_NUMBER RANK DENSE_RANK",
    types: "INT INTEGER BIGINT SMALLINT DECIMAL NUMERIC FLOAT DOUBLE REAL CHAR VARCHAR TEXT BLOB BOOLEAN BOOL DATE TIME",
  },
  html: {
    blockComment: ["<!--", "-->"], strings: ['"', "'"],
    tags: "div span p a img ul ol li table thead tbody tr th td h1 h2 h3 h4 h5 h6 head body html meta link script style form input button label section article nav header footer main aside strong em code pre br hr blockquote small i b u",
    attrs: "class id style href src alt title target rel type name value placeholder disabled checked role data- aria- width height",
  },
  xml: { parent: "html" },
  css: {
    blockComment: ["/*", "*/"], strings: ['"', "'"],
    keywords: "inherit initial unset auto none block inline inline-block flex grid absolute relative fixed sticky static center space-between space-around row column wrap nowrap",
    builtins: "px em rem vh vw % rgba rgb hsl hsla var calc url",
    props: "color background background-color border margin padding font font-size font-weight font-family line-height text-align text-decoration display position top right bottom left width height max-width max-height min-width overflow flex flex-direction justify-content align-items gap grid grid-template-columns gap row-gap column-gap opacity transform transition animation box-shadow text-shadow border-radius cursor z-index white-space word-break",
  },
  c: {
    lineComment: "//", blockComment: ["/*", "*/"], strings: ['"', "'"],
    keywords: "int char float double void return if else for while do switch case break continue struct union enum typedef const static extern register volatile sizeof goto signed unsigned long short",
    builtins: "printf scanf malloc calloc realloc free memcpy memset strlen strcmp strcpy fopen fclose fprintf fread fwrite exit",
    types: "size_t FILE NULL",
  },
  cpp: {
    lineComment: "//", blockComment: ["/*", "*/"], strings: ['"', "'"],
    keywords: "int char float double void return if else for while do switch case break continue struct class union enum typedef const static extern register volatile sizeof goto signed unsigned long short new delete this virtual override final namespace template typename public private protected friend operator inline constexpr auto",
    builtins: "printf scanf cout cin endl malloc calloc realloc free memcpy memset strlen strcmp strcpy vector string map set unordered_map std",
    types: "size_t FILE NULL bool",
  },
  java: {
    lineComment: "//", blockComment: ["/*", "*/"], strings: ['"', "'"],
    keywords: "public private protected class interface extends implements return if else for while do switch case break continue new try catch finally throw throws static final void int long short byte char float double boolean abstract synchronized volatile transient native strictfp package import this super instanceof enum default",
    builtins: "System out println print System.out println print String Integer Long Double Boolean Character Math Object Exception RuntimeException",
    types: "void int long short byte char float double boolean String Object",
  },
  go: {
    lineComment: "//", blockComment: ["/*", "*/"], strings: ['"', "`"],
    keywords: "package import func return if else for range switch case break continue go defer chan struct interface map type var const select fallthrough default goto",
    builtins: "make new len cap append copy delete print println panic recover close complex real imag string int int8 int16 int32 int64 uint uint8 uint16 uint32 uint64 float32 float64 bool byte rune error nil",
  },
  rust: {
    lineComment: "//", blockComment: ["/*", "*/"], strings: ['"', "'"],
    keywords: "fn let mut return if else for while loop match move borrow ref impl trait struct enum mod use pub crate super self as where async await dyn const static type unsafe extern",
    builtins: "println print format vec String str Option Some None Result Ok Err Box Rc Arc Vec HashMap HashSet VecDeque iter map filter collect clone unwrap expect",
    types: "i8 i16 i32 i64 i128 u8 u16 u32 u64 u128 f32 f64 bool char usize isize",
  },
  yaml: { lineComment: "#", strings: ['"', "'"], keywords: "true false null yes no on off", props: null },
  toml: { lineComment: "#", strings: ['"', "'"], keywords: "true false" },
  dockerfile: { lineComment: "#", strings: ['"', "'"], keywords: "FROM RUN CMD ENTRYPOINT COPY ADD ENV ARG WORKDIR EXPOSE VOLUME USER LABEL ONBUILD STOPSIGNAL HEALTHCHECK SHELL", builtins: "ubuntu alpine debian node python golang nginx" },
  makefile: { lineComment: "#", strings: ['"', "'"] },
  ini: { lineComment: ";", strings: ['"', "'"] },
  plaintext: {}, text: {}, txt: {},
};

function getLang(name) {
  const key = String(name || "").toLowerCase().replace(/^\./, "").split("_")[0];
  const base = LANG[key] ? key : (key === "javascript" ? "js" : key === "typescript" ? "ts" : key === "python3" ? "python" : key === "jsx" ? "jsx" : key === "shell-session" || key === "console" ? "bash" : key === "c++" || key === "cc" || key === "cxx" ? "cpp" : key === "golang" ? "go" : key === "yml" ? "yaml" : key === "docker" ? "dockerfile" : key === "make" ? "makefile" : key === "html" ? "html" : LANG[key] ? key : null);
  return base || "plaintext";
}

function buildRules(name) {
  const l = LANG[name] || {};
  const rules = [];
  if (l.lineComment)
    rules.push({ re: new RegExp(escRe(l.lineComment) + "[^\\n]*", "g"), cls: "com" });
  if (l.triple)
    for (const t of l.triple)
      rules.push({ re: new RegExp(escRe(t) + "[\\s\\S]*?" + escRe(t), "g"), cls: "str" });
  if (l.blockComment)
    rules.push({ re: new RegExp(escRe(l.blockComment[0]) + "[\\s\\S]*?" + escRe(l.blockComment[1]), "g"), cls: "com" });
  for (const q of l.strings || ['"', "'"]) {
    const qq = escRe(q);
    rules.push({ re: new RegExp(qq + "(?:\\\\.|[^\\\\" + qq + "\\n])*" + qq, "g"), cls: "str" });
  }
  rules.push({ re: /\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b/g, cls: "num" });
  if (l.keywords)
    rules.push({ re: new RegExp("\\b(?:" + l.keywords.replace(/\s+/g, "|") + ")\\b", "g"), cls: "kw" });
  if (l.builtins)
    rules.push({ re: new RegExp("\\b(?:" + l.builtins.replace(/\s+/g, "|") + ")\\b", "g"), cls: "fn" });
  if (l.types)
    rules.push({ re: new RegExp("\\b(?:" + l.types.replace(/\s+/g, "|") + ")\\b", "g"), cls: "ty" });
  if (name === "html" || name === "xml") {
    rules.push({ re: /<\/?[a-zA-Z][\w-]*/g, cls: "tag" });
    if (l.attrs) rules.push({ re: new RegExp("(?<=<\\/?[a-zA-Z][^>]*?\\s)(" + l.attrs.replace(/\s+/g, "|") + ")(?==)", "g"), cls: "at" });
  }
  if (name === "css") {
    if (l.props) rules.push({ re: new RegExp("(?:^|[;{])\\s*(" + l.props.replace(/\s+/g, "|") + ")(?=\\s*:)", "g"), cls: "at" });
    rules.push({ re: /[.#][a-zA-Z][\w-]*/g, cls: "ty" });
  }
  if ((name === "yaml" || name === "toml") ) {
    rules.push({ re: /^[\w.-]+(?=\s*:)/gm, cls: "at" });
    rules.push({ re: /\b(true|false|null|~)\b/g, cls: "kw" });
  }
  if (name === "json") {
    rules.push({ re: /"(?:\\u[\da-fA-F]{4}|\\[\\"/bfnrt]|[^\\"])*"\s*(?=:)/g, cls: "at" });
    rules.push({ re: /"(?:\\u[\da-fA-F]{4}|\\[\\"/bfnrt]|[^\\"])*"/g, cls: "str" });
  }
  return rules;
}

// 高亮入口：返回 HTML（已转义，安全注入）
export function highlight(code, lang) {
  const name = getLang(lang);
  if (name === "plaintext") return escapeHtml(code);
  const rules = buildRules(name);
  const out = [];
  let pos = 0;
  const n = code.length;
  while (pos < n) {
    let best = null;
    for (const r of rules) {
      r.re.lastIndex = pos;
      const m = r.re.exec(code);
      if (m && (best === null || m.index < best.m.index)) best = { r, m };
    }
    if (!best) {
      out.push(escapeHtml(code.slice(pos)));
      break;
    }
    if (best.m.index > pos) out.push(escapeHtml(code.slice(pos, best.m.index)));
    const len = best.m[0].length;
    if (len > 0) {
      out.push(`<span class="hl-${best.r.cls}">${escapeHtml(best.m[0])}</span>`);
      pos = best.m.index + len;
    } else {
      out.push(escapeHtml(code[pos] || ""));
      pos++;
    }
  }
  return out.join("");
}

export { getLang };
