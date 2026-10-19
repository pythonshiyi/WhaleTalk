// ── 执行过程折叠策略回归 ──
// 锁定的承诺：
// 1. 历史消息（全部完成）默认折叠——一整轮 5–15 步不该把回答挤出屏幕；
// 2. 有失败一定展开——异常不能藏在折叠里；
// 3. 挂载时正在执行的组展开——否则用户会以为卡住了；
// 4. 用户手动展开过，就记住（折叠是默认值，不是教条）；
// 5. 折叠态仍有摘要（步数/工具名），不是信息黑洞。
//
// 为什么不用「实时判定」：一轮里工具是分段的，两次调用之间存在「全部完成」的
// 瞬间；实时重算会让面板在调用之间反复折叠/展开。所以判定只在挂载时做一次
// （见 StepGroup 的惰性初始化），这里测的就是那一份判据。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import {
  RAIL_PREF_KEY,
  initialRailOpen,
  railPreview,
  readRailPref,
  shouldOpenByDefault,
  writeRailPref,
} from "../src/toolRail.js";

const done = (tool, extra = {}) => ({ tool, status: "done", duration: "1.2s", ...extra });
const running = (tool) => ({ tool, status: "running" });
const failed = (tool) => ({ tool, status: "failed", duration: "0.4s" });

/** 极简 localStorage 替身（含隐私模式下会抛错的实现） */
function fakeStorage({ throwOnGet = false, throwOnSet = false } = {}) {
  const map = new Map();
  return {
    getItem(k) {
      if (throwOnGet) throw new Error("SecurityError");
      return map.has(k) ? map.get(k) : null;
    },
    setItem(k, v) {
      if (throwOnSet) throw new Error("QuotaExceededError");
      map.set(k, String(v));
    },
    removeItem(k) {
      if (throwOnSet) throw new Error("QuotaExceededError");
      map.delete(k);
    },
    _dump: () => Object.fromEntries(map),
  };
}

describe("执行过程默认折叠", () => {
  it("全部完成的历史会话 → 折叠", () => {
    assert.equal(shouldOpenByDefault([done("read_file"), done("web_search")]), false);
    assert.equal(initialRailOpen([done("a"), done("b")], false), false);
  });

  it("有失败 → 展开（即使步骤已结束、用户也没设偏好）", () => {
    assert.equal(shouldOpenByDefault([done("a"), failed("b")]), true);
    assert.equal(initialRailOpen([done("a"), failed("b")], false), true);
  });

  it("挂载时正在执行 → 展开（否则像卡住）", () => {
    assert.equal(shouldOpenByDefault([done("a"), running("b")]), true);
    assert.equal(initialRailOpen([done("a"), running("b")], false), true);
  });

  it("用户偏好 > 默认：设为展开后历史会话也展开", () => {
    assert.equal(initialRailOpen([done("a")], true), true);
  });

  it("失败优先级高于用户偏好：偏好关闭也不影响失败展开", () => {
    assert.equal(initialRailOpen([failed("a")], false), true);
  });

  it("空/脏数据不崩：null、缺 tool 的条目被忽略", () => {
    assert.equal(shouldOpenByDefault(null), false);
    assert.equal(shouldOpenByDefault([{}, { tool: "" }, null]), false);
    assert.equal(initialRailOpen(undefined), false);
    assert.equal(railPreview(null), "");
  });
});

describe("折叠态摘要", () => {
  it("去重并按出现顺序取前 N 个工具名", () => {
    const steps = [done("web_search"), done("read_file"), done("web_search"), done("write_file")];
    assert.equal(railPreview(steps, 3), "web_search · read_file · write_file");
  });

  it("超出上限用 +N 收尾", () => {
    const steps = ["a", "b", "c", "d", "e"].map((n) => done(n));
    assert.equal(railPreview(steps, 3), "a · b · c +2");
  });

  it("不超过上限时不加后缀", () => {
    assert.equal(railPreview([done("x"), done("y")], 3), "x · y");
  });
});

describe("展开偏好持久化", () => {
  it("写入 true 后读回 true，写入 false 后回到默认", () => {
    const st = fakeStorage();
    assert.equal(readRailPref(st), false);
    writeRailPref(true, st);
    assert.equal(readRailPref(st), true);
    assert.equal(st._dump()[RAIL_PREF_KEY], "1");
    writeRailPref(false, st);
    assert.equal(readRailPref(st), false);
    assert.equal(st._dump()[RAIL_PREF_KEY], undefined, "收起应删除键而非写 0");
  });

  it("存储被禁用（读取抛错）时退化为默认，不抛出", () => {
    assert.equal(readRailPref(fakeStorage({ throwOnGet: true })), false);
    assert.doesNotThrow(() => writeRailPref(true, fakeStorage({ throwOnSet: true })));
  });
});
