import React from "react";
import BrainBlock from "./BrainBlock.jsx";
import BrainGrant from "./BrainGrant.jsx";
import BrainSearch from "./BrainSearch.jsx";
import BrainInsights from "./BrainInsights.jsx";
import { MemoryPage } from "./Pages.jsx";
import { Icon } from "./icons.jsx";
import * as brainNav from "../brainNav.js";

// 大脑栏目：指挥舱（状态总览 / 成长轨迹 / 备份与延续）+ 记忆库（记忆与知识）
export default function BrainPage() {
  const [tab, setTab] = React.useState("cockpit");
  const tabRef = React.useRef(tab);
  React.useEffect(() => { tabRef.current = tab; }, [tab]);

  // 跨分区深链（全局检索 / 图谱筛选 / 待复习）会请求切到某个顶层分区
  React.useEffect(() => brainNav.subscribe((ev) => {
    if (ev.tab && ev.tab !== tabRef.current) {
      tabRef.current = ev.tab;
      setTab(ev.tab);
    }
  }), []);

  const go = (t) => {
    if (t !== tabRef.current) { tabRef.current = t; setTab(t); }
    brainNav.focusTab(t);
  };

  return (
    <div className="page">
      <div className="page-head">
        <h1>大脑</h1>
        <p>{tab === "cockpit"
          ? "鲸语的灵魂——身份、健康、成长轨迹与时光备份，按「状态 / 成长 / 延续」三个分区清晰铺开"
          : "大脑记忆库——类型 / 重要度 / 实体关系标注，可检索、可编辑；目标与知识库收纳在下方"}</p>
      </div>

      <BrainSearch />
      <BrainInsights />

      <div
        className="brain-tabs"
        role="tablist"
        onKeyDown={(e) => {
          if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
          e.preventDefault();
          const tabs = ["cockpit", "memory", "grant"];
          const i = tabs.indexOf(tabRef.current);
          const next = (i + (e.key === "ArrowRight" ? 1 : tabs.length - 1)) % tabs.length;
          go(tabs[next]);
        }}
      >
        <button role="tab" id="brain-tab-cockpit" aria-selected={tab === "cockpit"} aria-controls="brain-panel-cockpit"
          className={`brain-tab ${tab === "cockpit" ? "on" : ""}`} onClick={() => go("cockpit")}>
          <Icon name="brain" size={15} /> 指挥舱
        </button>
        <button role="tab" id="brain-tab-memory" aria-selected={tab === "memory"} aria-controls="brain-panel-memory"
          className={`brain-tab ${tab === "memory" ? "on" : ""}`} onClick={() => go("memory")}>
          <Icon name="book" size={15} /> 记忆库
        </button>
        <button role="tab" id="brain-tab-grant" aria-selected={tab === "grant"} aria-controls="brain-panel-grant"
          className={`brain-tab ${tab === "grant" ? "on" : ""}`} onClick={() => go("grant")}>
          <Icon name="lock" size={15} /> 授权
        </button>
      </div>

      <div className="brain-page-body">
        {tab === "cockpit"
          ? <div role="tabpanel" id="brain-panel-cockpit" aria-labelledby="brain-tab-cockpit"><BrainBlock /></div>
          : tab === "memory"
            ? <div role="tabpanel" id="brain-panel-memory" aria-labelledby="brain-tab-memory"><MemoryPage embedded /></div>
            : <div role="tabpanel" id="brain-panel-grant" aria-labelledby="brain-tab-grant"><BrainGrant /></div>}
      </div>
    </div>
  );
}
