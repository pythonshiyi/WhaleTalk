// ── timeFmt.formatClock 时间格式统一回归测试 ──
// 全站消息/会话时间统一：今天 HH:MM / 今年 MM-DD HH:MM / 跨年 YYYY-MM-DD；
// 非法/空输入返回空串（避免 UI 出现 "Invalid Date" 或裸时间）。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { formatClock, nowClock } from "../src/timeFmt.js";

const pad = (n) => String(n).padStart(2, "0");

describe("formatClock 时间统一格式", () => {
  it("空 / 非法输入返回空串", () => {
    assert.equal(formatClock(null), "");
    assert.equal(formatClock(undefined), "");
    assert.equal(formatClock(""), "");
    assert.equal(formatClock("not-a-date"), "");
  });

  it("跨年 → YYYY-MM-DD（无时分）", () => {
    assert.equal(formatClock("2020-05-06 10:30:00"), "2020-05-06");
    assert.equal(formatClock(new Date(2021, 0, 1, 9, 5)), "2021-01-01");
  });

  it("今年但非今天 → MM-DD HH:MM", () => {
    const now = new Date();
    // 造一个今年的 1 月 2 日（若今天恰是 1/2 会退到"今天"分支，此处跳过该极端）
    const d = new Date(now.getFullYear(), 0, 2, 14, 3);
    if (!(now.getMonth() === 0 && now.getDate() === 2)) {
      assert.equal(formatClock(d), "01-02 14:03");
    }
  });

  it("今天 → HH:MM（与当前日期对齐）", () => {
    const now = new Date();
    const d = new Date(now.getFullYear(), now.getMonth(), now.getDate(), 9, 7);
    assert.equal(formatClock(d), "09:07");
  });

  it("数字 ms 入参可用", () => {
    const t = new Date(2024, 5, 15, 8, 30).getTime();
    assert.equal(formatClock(t), "2024-06-15");
  });

  it("nowClock 返回当前 HH:MM（今天语义）", () => {
    const out = nowClock();
    const now = new Date();
    assert.equal(out, `${pad(now.getHours())}:${pad(now.getMinutes())}`);
  });
});
