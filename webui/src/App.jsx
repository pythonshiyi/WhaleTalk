import React from "react";
import Sidebar from "./components/Sidebar.jsx";
import FirstRunPage from "./components/FirstRunPage.jsx";
import ChatPage, { BackendBanner } from "./components/ChatPage.jsx";
import DepsBanner from "./components/DepsBanner.jsx";
import InstallBanner from "./components/InstallBanner.jsx";
import { AbilitiesPage, PluginsPage, SettingsPage, WorkbenchPage } from "./components/Pages.jsx";
import BrainPage from "./components/BrainPage.jsx";
import AutonomyPage from "./components/AutonomyPage.jsx";
import PromptsPage from "./components/PromptsPage.jsx";
import { FlashProvider, ToastProvider } from "./components/FlashToast.jsx";
import * as api from "./api.js";

import { silentWarn } from "./quiet.js";
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
    const check = async () => {
      if (!alive) return;
      try {
        const d = await api.getFirstRun();
        if (alive) setFirstRun(!!(d && d.first_run));
      } catch {
        if (!alive) return;
        tries += 1;
        if (tries < 30) setTimeout(check, 2000);
        else setFirstRun(false); // 后端长时间不可用：进主界面由 BackendBanner 提示
      }
    };
    check();
    return () => {
      alive = false;
    };
  }, []);

  const switchMode = async (m) => {
    setMode(m);
    try {
      await api.setMode(m);
    } catch (e) { silentWarn(e, "App"); }
  };

  // 首次启动：全屏依赖安装向导（装完/跳过 → 刷新进入主界面）
  if (firstRun === null) {
    return (
      <div className="app" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh" }}>
        <div style={{ color: "var(--text-3)", fontSize: 13 }}>正在初始化…</div>
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
              <div className="app">
                <Sidebar page={page} onPage={setPage} />
                <main className="app-main">
                  <BackendBanner />
                  <InstallBanner />
                  <DepsBanner
                    onGoSettings={() => {
                      try { window.location.hash = "#deps"; } catch (e) { silentWarn(e, "App"); }
                      setPage("settings");
                    }}
                  />
                  <ErrorBoundary>
                    {page === "chat" && (
                      <ChatPage
                        onGoWorkbench={() => setPage("workbench")}
                        onGoSettings={() => setPage("settings")}
                        applyPrompt={applyPrompt}
                        onApplyDone={() => setApplyPrompt(null)}
                        openSessionId={openSessionId}
                        onOpenSessionDone={() => setOpenSessionId(null)}
                        quietMode={quietMode}
                        onToggleQuiet={toggleQuiet}
                      />
                    )}
                    {page === "workbench" && (
                      <WorkbenchPage
                        onApply={(text) => {
                          setApplyPrompt(text);
                          setPage("chat");
                        }}
                        onPickSession={(id) => {
                          setOpenSessionId(id);
                          setPage("chat");
                        }}
                      />
                    )}
                    {page === "abilities" && <AbilitiesPage />}
                    {page === "plugins" && (
                      <PluginsPage
                        onApply={(text) => {
                          setApplyPrompt(text);
                          setPage("chat");
                        }}
                      />
                    )}
                    {page === "prompts" && (
                      <PromptsPage
                        onApply={(text) => {
                          setApplyPrompt(text);
                          setPage("chat");
                        }}
                      />
                    )}
                    {page === "brain" && <BrainPage />}
                    {page === "autonomy" && <AutonomyPage />}
                    {page === "settings" && (
                      <SettingsPage
                        onGoPrompts={() => setPage("prompts")}
                        quietMode={quietMode}
                        onToggleQuiet={toggleQuiet}
                      />
                    )}
                  </ErrorBoundary>
                </main>
              </div>
            </ToastProvider>
          </FlashProvider>
        </DisplayContext.Provider>
      </ModeContext.Provider>
    </ThemeContext.Provider>
  );
}