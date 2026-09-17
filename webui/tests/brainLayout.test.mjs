// 回归门禁：大脑指挥舱的「分区重构」结构不回退。
// 1. 指挥舱保留三个语义分区：状态总览 / 成长轨迹 / 备份与延续；
// 2. 低频/危险操作集中在 BrainContinuity，不与总览混排；
// 3. 大脑界面 chrome 不使用 emoji（设计系统 §8：chrome 一律 SVG 图标）。
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const comp = path.resolve(__dirname, "../src/components");
const read = (f) => fs.readFileSync(path.join(comp, f), "utf8");

describe("大脑指挥舱分区结构", () => {
  it("BrainBlock 声明三个分区并接入对应组件", () => {
    const t = read("BrainBlock.jsx");
    for (const label of ["状态总览", "成长轨迹", "备份与延续"]) {
      assert.ok(t.includes(label), `BrainBlock 应含分区「${label}」`);
    }
    assert.ok(/brain-subtabs/.test(t), "分区导航应使用 .brain-subtabs");
    assert.ok(t.includes("BrainContinuity"), "备份与延续应来自 BrainContinuity");
    assert.ok(t.includes("BrainGenesis"), "创世化初始应来自 BrainGenesis");
  });

  it("危险/低频操作集中在 BrainContinuity，不从总览壳直接触发", () => {
    const block = read("BrainBlock.jsx");
    const cont = read("BrainContinuity.jsx");
    for (const kw of ["adopt-merge", "merge-preview", "export-key", "import-key", "cleanup", "mirror", "brain-switch"]) {
      assert.ok(cont.includes(kw), `BrainContinuity 应包含 ${kw}`);
      assert.ok(!block.includes(kw), `BrainBlock（总览壳）不应直接出现危险动作 ${kw}`);
    }
  });

  it("大脑界面 chrome 不使用 emoji", () => {
    const files = [
      "BrainBlock.jsx", "BrainContinuity.jsx", "BrainGenesis.jsx",
      "BrainTimeline.jsx", "BrainKanban.jsx", "BrainHealth.jsx", "BrainGraph.jsx",
      "BrainSearch.jsx", "BrainLineage.jsx", "BrainSelfModel.jsx", "BrainReview.jsx",
    ];
    const emoji = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE0F}]/u;
    const offenders = files.filter((f) => emoji.test(read(f)));
    assert.deepEqual(offenders, [], `以下大脑组件仍含 emoji（chrome 应改用 SVG 图标）：${offenders.join(", ")}`);
  });

  it("新增能力已接入（检索/血缘/自我认知/待复习/恢复对比/实体编辑）", () => {
    assert.ok(read("BrainSearch.jsx").includes("brain-search"), "全局检索应调用 brain-search");
    assert.ok(read("BrainLineage.jsx").includes("lineage"), "血缘图应调用 lineage");
    assert.ok(read("BrainBlock.jsx").includes("BrainReview"), "总览应接入待复习");
    assert.ok(read("BrainBlock.jsx").includes("BrainSelfModel"), "总览应接入自我认知");
    assert.ok(read("BrainContinuity.jsx").includes("diff-current"), "备份应支持恢复前对比");
    const pages = fs.readFileSync(path.resolve(comp, "Pages.jsx"), "utf8");
    assert.ok(/parseEntities/.test(pages) && /parseRelations/.test(pages), "记忆编辑应支持实体/关系");
    assert.ok(pages.includes("brainNav"), "记忆库应接入实体筛选/定位导航");
  });
});
