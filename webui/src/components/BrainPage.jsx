import React from "react";
import BrainBlock from "./BrainBlock.jsx";
import BrainGrant from "./braingrant.jsx";
import BrainSearch from "./BrainSearch.jsx";
import BrainInsights from "./BrainInsights.jsx";
import LifePage from "./LifePage.jsx";
import AutonomyPage from "./AutonomyPage.jsx";
import { MemoryPage } from "./Pages.jsx";
import { Icon } from "./icons.jsx";
import * as brainNav from "../brainNav.js";

const TABS = [
  { id: "cockpit", label: "总览", icon: "layout" },
  { id: "memory", label: "记忆", icon: "book" },
  { id: "life", label: "生命", icon: "activity" },
  { id: "autonomy", label: "自主", icon: "sparkles" },
  { id: "grant", label: "授权", icon: "lock" },
];

// 智能体栏目：把大脑、记忆、生命、自主、授权收进同一个一级入口。
export default function BrainPage({ tab: tabProp, onTab }) {
  const [tabState, setTabState] = React.useState("cockpit");
  const tab = tabProp ?? tabState;
  const setTab = React.useCallback((next) => {
    setTabState(next);
    onTab?.(next);
  }, [onTab]);
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
        <h1>智能体</h1>
        <p>鲸语的内部世界——状态、记忆、生命循环、自主进化与授权，都在这里。</p>
      </div>

      {(tab === "cockpit" || tab === "memory") && (
        <>
          <BrainSearch />
          <BrainInsights />
        </>
      )}

      <div
        className="ab-tabs"
        role="tablist"
        aria-label="智能体分区"
        onKeyDown={(e) => {
          if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
          e.preventDefault();
          const ids = TABS.map((t) => t.id);
          const i = ids.indexOf(tabRef.current);
          const next = (i + (e.key === "ArrowRight" ? 1 : ids.length - 1)) % ids.length;
          go(ids[next]);
        }}
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`agent-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`agent-panel-${t.id}`}
            className={`ab-tab ${tab === t.id ? "ab-tab-on" : ""}`}
            onClick={() => go(t.id)}
          >
            <Icon name={t.icon} size={15} /> {t.label}
          </button>
        ))}
      </div>

      <div className="brain-page-body">
        {tab === "cockpit" && (
          <div role="tabpanel" id="agent-panel-cockpit" aria-labelledby="agent-tab-cockpit">
            <BrainBlock />
          </div>
        )}
        {tab === "memory" && (
          <div role="tabpanel" id="agent-panel-memory" aria-labelledby="agent-tab-memory">
            <MemoryPage embedded />
          </div>
        )}
        {tab === "life" && (
          <div role="tabpanel" id="agent-panel-life" aria-labelledby="agent-tab-life">
            <LifePage embedded />
          </div>
        )}
        {tab === "autonomy" && (
          <div role="tabpanel" id="agent-panel-autonomy" aria-labelledby="agent-tab-autonomy">
            <AutonomyPage embedded />
          </div>
        )}
        {tab === "grant" && (
          <div role="tabpanel" id="agent-panel-grant" aria-labelledby="agent-tab-grant">
            <BrainGrant />
          </div>
        )}
      </div>
    </div>
  );
}
