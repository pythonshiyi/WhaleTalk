// ── 主题对比度门禁：三套主题的文字层级必须满足 WCAG AA（4.5:1）──
// 为什么需要它：颜色令牌是「视觉上感觉还行」就能改的，但 --text-3 承载的是
// 时间戳、元信息、提示这类 9–12px 小字——正是最需要对比度的场合。v3.21 之前
// 它在抬升面（--bg-3）上只有 3.7–3.9:1，肉眼「看着有点淡」，实际已不合规。
// 这个测试把「可读」变成可回归的断言：以后调色导致不达标，npm test 直接红。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const CSS = readFileSync(join(HERE, "..", "src", "theme.css"), "utf8");

/** 极简主题块解析：`[data-theme="x"] { --k: v; }` / `:root, [data-theme="starfield"]` */
function parseThemes(css) {
  const out = {};
  const re = /^(?:\[data-theme="(\w+)"\][^{]*|:root,)\s*\{([\s\S]*?)\n\}/gm;
  let m;
  while ((m = re.exec(css))) {
    const key = m[1] || "starfield";
    const vars = {};
    for (const [, k, v] of m[2].matchAll(/(--[\w-]+):\s*([^;]+);/g)) vars[k] = v.trim();
    out[key] = { ...(out[key] || {}), ...vars };
  }
  return out;
}

function luminance(hex) {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16) / 255);
  const lin = (c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(a, b) {
  const [la, lb] = [luminance(a), luminance(b)];
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const THEMES = parseThemes(CSS);
const AA = 4.5;

describe("主题文字对比度（WCAG AA）", () => {
  it("三套主题都被解析到（令牌改名/块结构变动时立即报警）", () => {
    for (const name of ["starfield", "deepsea", "arctic"]) {
      assert.ok(THEMES[name], `缺少主题 ${name}`);
      for (const key of ["--bg-0", "--bg-1", "--bg-2", "--bg-3", "--text-1", "--text-2", "--text-3"]) {
        assert.ok(THEMES[name][key], `${name} 缺少令牌 ${key}`);
      }
    }
  });

  for (const name of ["starfield", "deepsea", "arctic"]) {
    for (const fg of ["--text-1", "--text-2", "--text-3"]) {
      it(`${name} / ${fg} 在所有表面层上 ≥ ${AA}:1`, () => {
        for (const bg of ["--bg-0", "--bg-1", "--bg-2", "--bg-3"]) {
          const v = THEMES[name];
          const r = contrast(v[fg], v[bg]);
          assert.ok(r >= AA, `${name} 的 ${fg} 在 ${bg} 上只有 ${r.toFixed(2)}:1（需 ≥ ${AA}）`);
        }
      });
    }
  }

  it("文字层级不因提亮而塌陷：text-1 > text-2 > text-3（按对比度）", () => {
    for (const name of ["starfield", "deepsea", "arctic"]) {
      const v = THEMES[name];
      const c = (fg) => contrast(v[fg], v["--bg-1"]);
      assert.ok(c("--text-1") > c("--text-2"), `${name}: text-1 应比 text-2 更亮/更暗`);
      assert.ok(c("--text-2") > c("--text-3"), `${name}: text-2 应比 text-3 更醒目`);
    }
  });
});
