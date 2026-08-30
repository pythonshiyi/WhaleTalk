import React from "react";
import BrainBlock from "./BrainBlock.jsx";
import { MemoryPage } from "./Pages.jsx";

// 大脑栏目：指挥舱（身份 / 心跳 / 时光备份 / 生命延续）+ 记忆库（记忆与知识）
export default function BrainPage() {
  const [tab, setTab] = React.useState("cockpit");
  return (
    <div className="page">
      <div className="page-head">
        <h1>⬡ 大脑</h1>
        <p>鲸语的灵魂——身份、记忆、思考与时光备份，全部汇聚于此；可备份、可迁移、可复活</p>
      </div>
      <div className="brain-tabs">
        <button className={`brain-tab ${tab === "cockpit" ? "on" : ""}`} onClick={() => setTab("cockpit")}>
          🧠 指挥舱
        </button>
        <button className={`brain-tab ${tab === "memory" ? "on" : ""}`} onClick={() => setTab("memory")}>
          📚 记忆库
        </button>
      </div>
      <div className="brain-page-body">
        {tab === "cockpit" ? <BrainBlock /> : <MemoryPage embedded />}
      </div>
    </div>
  );
}
