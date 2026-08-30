import React from "react";
import Sidebar from "./components/Sidebar.jsx";
import ChatPage, { BackendBanner } from "./components/ChatPage.jsx";
import DepsBanner from "./components/DepsBanner.jsx";
import InstallBanner from "./components/InstallBanner.jsx";
import { AbilitiesPage, PluginsPage, MemoryPage, SettingsPage, WorkbenchPage } from "./components/Pages.jsx";
import AutonomyPage from "./components/AutonomyPage.jsx";
import PromptsPage from "./components/PromptsPage.jsx";
import { FlashProvider, ToastProvider } from "./components/FlashToast.jsx";
import * as api from "./api.js";

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
  const [mode, setMode] = React.useState("task");
  // 指令库/工作台「应用」→ 把指令内容带进会话输入框（试跑用）
  const [applyPrompt, setApplyPrompt] = React.useState(null);
  // 工作台「最近会话」→ 直达对应会话
  const [openSessionId, setOpenSessionId] = React.useState(null);

  React.useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {}
  }, [theme]);

  React.useEffect(() => {
    try {
      localStorage.setItem(DENSITY_KEY, density);
    } catch {}
  }, [density]);

  React.useEffect(() => {
    try {
      localStorage.setItem(FONT_KEY, String(fontSize));
    } catch {}
  }, [fontSize]);

  React.useEffect(() => {
    // 启动时读取真实模式（config.json full_auto / pure_chat）
    let alive = true;
    (async () => {
      try {
        const s = await api.getStatus();
        if (alive && s && s.mode) setMode(s.mode);
      } catch {}
    })();
    return () => {
      alive = false;
    };
  }, []);

  const switchMode = async (m) => {
    setMode(m);
    try {
      await api.setMode(m);
    } catch {}
  };

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
                      try { window.location.hash = "#deps"; } catch {}
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
                    {page === "memory" && <MemoryPage />}
                    {page === "autonomy" && <AutonomyPage />}
                    {page === "settings" && <SettingsPage onGoPrompts={() => setPage("prompts")} />}
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