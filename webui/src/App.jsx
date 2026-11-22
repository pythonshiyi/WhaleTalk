import React, { lazy, Suspense } from "react";
import Sidebar from "./components/Sidebar.jsx";
import FirstRunPage from "./components/FirstRunPage.jsx";
import ChatPage, { BackendBanner } from "./components/ChatPage.jsx";
import DepsBanner from "./components/DepsBanner.jsx";
import InstallBanner from "./components/InstallBanner.jsx";
import CommandPalette from "./components/CommandPalette.jsx";
import { usePoll } from "./usePoll.js";
import { FlashProvider, ToastProvider } from "./components/FlashToast.jsx";
import DialogHost from "./components/DialogHost.jsx";
import * as api from "./api.js";

import { silentWarn } from "./quiet.js";

// ── M2 按页懒加载：除首屏 Chat 外，其余页面按需加载（砍首屏主 chunk）──
// 每页切成独立 chunk，进入时才 fetch。chat 保持同步渲染（首屏体验）。
const LazyPages = {
  workbench: lazy(() => import("./components/Pages.jsx").then((m) => ({ default: m.WorkbenchPage }))),
  abilities: lazy(() => import("./components/Pages.jsx").then((m) => ({ default: m.AbilitiesPage }))),
  create: lazy(() => import("./components/CreatePage.jsx")),
  agent: lazy(() => import("./components/BrainPage.jsx")),
  settings: lazy(() => import("./components/Pages.jsx").then((m) => ({ default: m.SettingsPage }))),
};
const PageFallback = () => (
  <div className="page-fallback">
    <span className="page-loading-dot" aria-hidden="true" /> 加载中…
  </div>
);

export const ThemeContext = React.createContext({ theme: "starfield", setTheme: () => {} });
export const ModeContext = React.createContext({ mode: "task", setMode: () => {}, switchMode: () => {} });
export const DisplayContext = React.createContext({ density: "comfort", setDensity: () => {}, fontSize: 14, setFontSize: () => {} });

const THEME_KEY = "whaletalk.theme";
const DENSITY_KEY = "whaletalk.density";
const FONT_KEY = "whaletalk.fontsize";

// 全局错误边界：页面崩溃显示错误而非白屏
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error) {
    return { error };
  }
  componentDidCatch(error, info) {
    console.error("页面渲染错误:", error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div className="page" style={{ padding: 32 }}>
          <div className="page-head">
            <h1>⚠️ 页面出错了</h1>
            <p>请把下面的错误信息发给开发者：</p>
          </div>
          <pre style={{ background: "var(--bg-2)", padding: 16, borderRadius: 12, color: "var(--danger)", userSelect: "text", overflow: "auto" }}>
            {String(this.state.error && (this.state.error.message || this.state.error))}
          </pre>
          <button className="confirm-btn confirm-primary" style={{ marginTop: 16 }} onClick={() => this.setState({ error: null })}>
            重试
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

export default function App() {
  const [page, setPage] = React.useState("chat");
  const [theme, setTheme] = React.useState(() => {
    try {
      return localStorage.getItem(THEME_KEY) || "starfield";
    } catch {
      return "starfield";
    }
  });
  const [density, setDensity] = React.useState(() => {
    try {
      return localStorage.getItem(DENSITY_KEY) || "comfort";
    } catch {
      return "comfort";
    }
  });
  const [fontSize, setFontSize] = React.useState(() => {
    try {
      return Number(localStorage.getItem(FONT_KEY)) || 14;
    } catch {
      return 14;
    }
  });
  // 默认任务模式（无限权限，黑名单主导），启动后从后端读真实模式覆盖
  const [mode, setMode] = React.useState("task");
  // 纯净对话总开关：对话页 header 与设置页双入口共享同一状态（localStorage 持久化）
  const [quietMode, setQuietMode] = React.useState(() => {
    try {
      return localStorage.getItem("whaletalk.quietMode") === "1";
    } catch {
      return false;
    }
  });
  const toggleQuiet = React.useCallback(() => {
    // 副作用（localStorage）移出 setState updater：StrictMode 下 updater 可能双调，保持纯函数
    setQuietMode((v) => !v);
  }, []);
  // 持久化跟随 state：仅在值真正变化时写一次
  React.useEffect(() => {
    try {
      localStorage.setItem("whaletalk.quietMode", quietMode ? "1" : "");
    } catch (e) { silentWarn(e, "App"); }
  }, [quietMode]);
  // 首次启动引导：true 渲染全屏依赖安装向导，装完才进入主界面
  const [firstRun, setFirstRun] = React.useState(null);
  // 指令库/工作台「应用」→ 把指令内容带进会话输入框（试跑用）
  const [applyPrompt, setApplyPrompt] = React.useState(null);
  // 工作台「最近会话」→ 直达对应会话
  const [openSessionId, setOpenSessionId] = React.useState(null);
  // 角色页「开始对话」→ 切到该角色并新开会话
  const [openPersonaId, setOpenPersonaId] = React.useState(null);
  // 二级页面标签提到 App 层：设置/工作台跳转时能直达目标标签
  const [createTab, setCreateTab] = React.useState("personas");
  const [settingsTab, setSettingsTab] = React.useState("model");
  const [workbenchTab, setWorkbenchTab] = React.useState("overview");
  const [agentTab, setAgentTab] = React.useState("cockpit");
  const [abilitiesQuery, setAbilitiesQuery] = React.useState("");
  const [assetPath, setAssetPath] = React.useState("");
  const [cmdOpen, setCmdOpen] = React.useState(false);
  const [runningJobs, setRunningJobs] = React.useState([]);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (e) { silentWarn(e, "App"); }
  }, [theme]);

  React.useEffect(() => {
    try {
      localStorage.setItem(DENSITY_KEY, density);
    } catch (e) { silentWarn(e, "App"); }
  }, [density]);

  React.useEffect(() => {
    try {
      localStorage.setItem(FONT_KEY, String(fontSize));
    } catch (e) { silentWarn(e, "App"); }
  }, [fontSize]);

  React.useEffect(() => {
    // 启动时读取真实模式（config.json full_auto / pure_chat）
    let alive = true;
    (async () => {
      try {
        const s = await api.getStatus();
        if (alive && s && s.mode) setMode(s.mode);
      } catch (e) { silentWarn(e, "App"); }
    })();
    return () => {
      alive = false;
    };
  }, []);

  React.useEffect(() => {
    // 首次启动检测：后端返回 first_run=true 时渲染全屏依赖安装向导。
    // 后端暂未就绪（硬依赖安装中）→ 每 2s 重试，就绪后自动进入正确页面。
    let alive = true;
    let tries = 0;
    let timer = null;
    const check = async () => {
      if (!alive) return;
      try {
        const d = await api.getFirstRun();
        if (alive) setFirstRun(!!(d && d.first_run));
      } catch {
        if (!alive) return;
        tries += 1;
        if (tries < 30) timer = setTimeout(check, 2000);
        else setFirstRun(false); // 后端长时间不可用：进主界面由 BackendBanner 提示
      }
    };
    check();
    return () => {
      alive = false;
      clearTimeout(timer); // 卸载时停掉重试链，避免悬挂定时器
    };
  }, []);

  const switchMode = async (m) => {
    setMode(m);
    try {
      await api.setMode(m);
    } catch (e) { silentWarn(e, "App"); }
  };

  // 全局导航：命令面板 / 待处理收件箱 / 后台任务条共用。
  const navigate = React.useCallback((dest) => {
    if (!dest || typeof dest !== "object") return;
    if (dest.workbenchTab) setWorkbenchTab(dest.workbenchTab);
    if (dest.createTab) setCreateTab(dest.createTab);
    if (dest.settingsTab) setSettingsTab(dest.settingsTab);
    if (dest.agentTab) setAgentTab(dest.agentTab);
    if (dest.toolQuery != null) setAbilitiesQuery(String(dest.toolQuery));
    if (dest.assetPath != null) setAssetPath(String(dest.assetPath));
    if (dest.sessionId != null) setOpenSessionId(dest.sessionId);
    if (dest.personaId != null) setOpenPersonaId(dest.personaId);
    if (dest.prompt != null) setApplyPrompt(dest.prompt);
    if (dest.page) setPage(dest.page);
  }, []);

  const loadRunningJobs = React.useCallback(async () => {
    try {
      const d = await api.runningJobs();
      setRunningJobs((d && d.jobs) || []);
    } catch (e) { silentWarn(e, "App"); }
  }, []);
  usePoll(loadRunningJobs, 5000);

  React.useEffect(() => {
    const onKey = (e) => {
      const key = String(e.key || "").toLowerCase();
      if ((e.ctrlKey || e.metaKey) && key === "k") {
        e.preventDefault();
        setCmdOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // 首次启动：全屏依赖安装向导（装完/跳过 → 刷新进入主界面）
  if (firstRun === null) {
    return (
      <div className="app-boot">
        <div className="app-boot-whale" aria-hidden="true">🐋</div>
        <div className="app-boot-text">正在初始化…</div>
      </div>
    );
  }
  if (firstRun) {
    return (
      <FirstRunPage
        onDone={() => {
          // 直接切主界面（不 reload——避免与流式响应/连接复用产生竞态）
          setFirstRun(false);
        }}
      />
    );
  }

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      <ModeContext.Provider value={{ mode, setMode, switchMode }}>
        <DisplayContext.Provider value={{ density, setDensity, fontSize, setFontSize }}>
          <FlashProvider>
            <ToastProvider>
              <DialogHost />
              <div className="app">
                <Sidebar page={page} onPage={setPage} onNavigate={navigate} runningCount={runningJobs.length} />
                <main className="app-main">
                  <BackendBanner />
                  <InstallBanner />
                  {runningJobs.length > 0 && (
                    <div className="bg-jobs-bar" role="status">
                      <span className="bg-jobs-dot" aria-hidden="true" />
                      <b>{runningJobs.length} 个后台任务正在生成</b>
                      <span className="bg-jobs-meta">
                        {runningJobs[0]?.last_tool ? `当前：${runningJobs[0].last_tool}` : "模型思考中"}
                        {runningJobs[0]?.seconds ? ` · ${Math.round(runningJobs[0].seconds)}s` : ""}
                      </span>
                      {runningJobs[0]?.session_id && (
                        <button className="msg-op" onClick={() => navigate({ page: "chat", sessionId: runningJobs[0].session_id })}>
                          打开会话
                        </button>
                      )}
                      <button className="msg-op" onClick={() => navigate({ page: "workbench", workbenchTab: "tasks" })}>任务记录</button>
                    </div>
                  )}
                  <DepsBanner
                    onGoSettings={() => {
                      try { window.location.hash = "#deps"; } catch (e) { silentWarn(e, "App"); }
                      setPage("settings");
                    }}
                  />
                  <ErrorBoundary>
                    <Suspense fallback={<PageFallback />}>
                    {/* ChatPage 常驻挂载（仅隐藏，不卸载）：切换设置/其它页面不会销毁
                        组件 → 流式输出继续、消息与滚动状态留存（否则卸载会 abort 流并
                        让 finish 被 alive=false 短路 → 会话保存失败）。后端亦把生成放在
                        独立作业线程，切页/关标签/多标签页都不会打断。 */}
                    <div className="chat-keepalive" hidden={page !== "chat"}>
                      <ChatPage
                        onGoWorkbench={() => setPage("workbench")}
                        onGoSettings={() => setPage("settings")}
                        applyPrompt={applyPrompt}
                        onApplyDone={() => setApplyPrompt(null)}
                        openSessionId={openSessionId}
                        onOpenSessionDone={() => setOpenSessionId(null)}
                        openPersonaId={openPersonaId}
                        onOpenPersonaDone={() => setOpenPersonaId(null)}
                        quietMode={quietMode}
                        onToggleQuiet={toggleQuiet}
                        active={page === "chat"}
                      />
                    </div>
                    {page === "workbench" && (() => {
                      const P = LazyPages.workbench;
                      return (
                        <P
                          tab={workbenchTab}
                          onTab={setWorkbenchTab}
                          onApply={(text) => {
                            setApplyPrompt(text);
                            setPage("chat");
                          }}
                          onPickSession={(id) => {
                            setOpenSessionId(id);
                            setPage("chat");
                          }}
                        />
                      );
                    })()}
                    {page === "abilities" && (() => {
                      const P = LazyPages.abilities;
                      return (
                        <P
                          initialQuery={abilitiesQuery}
                          onAskTool={(name) => {
                            setApplyPrompt(`请解释工具 ${name} 的用途、参数和一个最小调用示例；先不要执行它。`);
                            setPage("chat");
                          }}
                        />
                      );
                    })()}
                    {page === "create" && (() => {
                      const P = LazyPages.create;
                      return (
                        <P
                          tab={createTab}
                          onTab={setCreateTab}
                          onChatPersona={(persona) => {
                            setOpenPersonaId(persona.id);
                            setPage("chat");
                          }}
                          onApply={(text) => {
                            setApplyPrompt(text);
                            setPage("chat");
                          }}
                          onGoWechat={() => {
                            setSettingsTab("wechat");
                            setPage("settings");
                          }}
                          assetPath={assetPath}
                        />
                      );
                    })()}
                    {page === "agent" && (() => {
                      const P = LazyPages.agent;
                      return <P tab={agentTab} onTab={setAgentTab} />;
                    })()}
                    {page === "settings" && (() => {
                      const P = LazyPages.settings;
                      return (
                        <P
                          tab={settingsTab}
                          onTab={setSettingsTab}
                          onGoPrompts={() => {
                            setCreateTab("prompts");
                            setPage("create");
                          }}
                          onGoAbilities={() => setPage("abilities")}
                          onOpenSession={(sid) => {
                            if (!sid) return;
                            setOpenSessionId(sid);
                            setPage("chat");
                          }}
                          quietMode={quietMode}
                          onToggleQuiet={toggleQuiet}
                        />
                      );
                    })()}
                    </Suspense>
                  </ErrorBoundary>
                </main>
              </div>
              <CommandPalette open={cmdOpen} onClose={() => setCmdOpen(false)} onNavigate={navigate} />
            </ToastProvider>
          </FlashProvider>
        </DisplayContext.Provider>
      </ModeContext.Provider>
    </ThemeContext.Provider>
  );
}