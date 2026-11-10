import React from "react";
import * as api from "../api.js";
import { SkeletonCard } from "./Skeleton.jsx";
import { silentWarn } from "../quiet.js";

/**
 * 直播角色控制台：数值监控 + 手动投喂 + OBS 地址。
 *
 * 这里是"主播视角"，观众看到的是 /live/ 那个 HUD 页面（OBS 浏览器源）。
 * 两者共用同一份后端状态，所以在控制台喂一个礼物，OBS 画面会立刻变化。
 */

const MOOD_TEXT = {
  starving: "饿坏了",
  hungry: "有点饿",
  normal: "还行",
  full: "吃饱了",
};

const GIFT_PRESETS = [
  { name: "小心心", doucoin: 1 },
  { name: "玫瑰", doucoin: 1 },
  { name: "棒棒糖", doucoin: 9 },
  { name: "墨镜", doucoin: 99 },
  { name: "礼花筒", doucoin: 199 },
  { name: "火箭", doucoin: 9999 },
];

function Stat({ label, value, accent }) {
  return (
    <div className="live-stat">
      <b style={accent ? { color: accent } : undefined}>{value}</b>
      <span>{label}</span>
    </div>
  );
}

export default function LivePage() {
  const [snap, setSnap] = React.useState(null);
  const [settings, setSettings] = React.useState(null);
  const [err, setErr] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [giver, setGiver] = React.useState("测试观众");
  const [chatText, setChatText] = React.useState("");
  const [lastLine, setLastLine] = React.useState(null);

  const flash = (t) => { setMsg(t); setTimeout(() => setMsg(""), 2500); };

  const load = React.useCallback(async () => {
    try {
      const s = await api.getLiveSnapshot();
      setSnap(s);
      setErr("");
    } catch (e) {
      setErr("直播状态读取失败，请确认后端已连接。");
      silentWarn(e, "LivePage");
    }
  }, []);

  React.useEffect(() => {
    load();
    api.getLiveSettings().then((r) => setSettings(r?.config || null)).catch(() => {});
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, [load]);

  const sendGift = async (preset) => {
    setBusy(true);
    try {
      const r = await api.sendLiveGift({
        giver: giver.trim() || "测试观众",
        doucoin: preset.doucoin,
        gift: preset.name,
        count: 1,
        say: true,
      });
      setSnap(r.snapshot);
      setLastLine(r.line);
      flash(r.line?.spoken ? "已送出，角色说话了" : "已送出");
    } catch (e) {
      setErr(`送礼失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const sendChat = async () => {
    if (!chatText.trim()) return;
    setBusy(true);
    try {
      const r = await api.sendLiveChat({ user: giver.trim() || "观众", text: chatText.trim(), say: true });
      setLastLine(r.line);
      setSnap(r.snapshot);
      flash(r.skipped ? "冷却中，已跳过" : "已回复");
      setChatText("");
    } catch (e) {
      setErr(`发送失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  const updateSetting = async (key, value) => {
    setBusy(true);
    try {
      const r = await api.saveLiveSettings({ [key]: value });
      setSnap(r.snapshot);
      setSettings((s) => ({ ...s, [key]: value }));
      flash("已更新");
    } catch (e) {
      setErr(`更新失败：${e.message || e}`);
    } finally {
      setBusy(false);
    }
  };

  if (!snap && !err) return <div className="page"><SkeletonCard /></div>;

  const hudUrl = `${window.location.origin}/live/`;
  const filledPct = Math.round((snap?.filled || 0) * 100);
  const mood = snap?.mood || "normal";

  return (
    <div className="page live-page">
      <div className="page-head">
        <h1>直播角色</h1>
        <p>
          观众用抖音官方礼物打赏，角色靠打赏「吃饭」活下来。这里是主播控制台；
          观众看到的是 HUD 页面，用 OBS「浏览器源」加载即可。
        </p>
      </div>

      {err && <div className="empty-tip is-err">{err}</div>}

      {/* ── OBS 地址 ── */}
      <section className="set-card live-card">
        <div className="svc-title">OBS 浏览器源</div>
        <div className="pm-form">
          <div className="pm-field">
            <label>把这个地址加进 OBS「浏览器源」（建议 1280×720）</label>
            <div className="live-url-row">
              <input className="set-select" readOnly value={hudUrl}
                onFocus={(e) => e.target.select()} />
              <button className="confirm-btn" onClick={() => {
                navigator.clipboard?.writeText(hudUrl);
                flash("已复制");
              }}>复制</button>
              <button className="confirm-btn confirm-primary" onClick={() => window.open(hudUrl, "_blank")}>
                预览
              </button>
            </div>
            <div className="pm-hint">
              HUD 每 1 秒拉一次状态，送礼后画面会立刻变化。OBS 里建议勾选「关闭源时关闭页面」为否。
            </div>
          </div>
        </div>
      </section>

      {/* ── 实时状态 ── */}
      <section className={`set-card live-card live-status mood-${mood}`}>
        <div className="svc-title">
          当前状态
          <span className={`live-mood mood-${mood}`}>{MOOD_TEXT[mood]}</span>
        </div>

        <div className="live-bar-wrap">
          <div className="live-bar">
            <div className="live-bar-fill" style={{ width: `${filledPct}%` }} />
          </div>
          <span className="live-bar-text">{filledPct}% 饱腹</span>
        </div>

        <div className="live-stats">
          <Stat label="今日打赏" value={`¥${(snap?.today_yuan || 0).toFixed(1)}`} accent="#6ee7b7" />
          <Stat label="距离吃饱" value={`¥${(snap?.remaining_yuan || 0).toFixed(1)}`} />
          <Stat label="礼物次数" value={snap?.gift_count || 0} />
          <Stat label="投喂人数" value={snap?.unique_givers || 0} />
          <Stat label="预计实收" value={`¥${(snap?.today_net_yuan || 0).toFixed(1)}`} />
          <Stat label="今日额度" value={`¥${(snap?.quota_yuan || 0).toFixed(0)}`} />
        </div>

        {snap?.top_gifter && (
          <div className="live-top">
            榜一：<b>{snap.top_gifter}</b>（¥{(snap.top_gifter_yuan || 0).toFixed(1)}）
          </div>
        )}
      </section>

      {/* ── 手动投喂（接入真实弹幕前用来测试）── */}
      <section className="set-card live-card">
        <div className="svc-title">手动投喂（测试用）</div>
        <div className="pm-form">
          <div className="pm-field">
            <label>观众昵称</label>
            <input className="set-select" value={giver} onChange={(e) => setGiver(e.target.value)} />
          </div>
          <div className="live-gift-grid">
            {GIFT_PRESETS.map((p) => (
              <button key={p.name} className="live-gift-btn" disabled={busy}
                onClick={() => sendGift(p)}>
                <b>{p.name}</b>
                <span>{p.doucoin} 抖币</span>
              </button>
            ))}
          </div>
          <div className="pm-hint">
            模拟一次打赏，角色会用 AI 生成一句符合当前饥饿状态的台词并念出来。
          </div>

          <div className="pm-field">
            <label>测试弹幕</label>
            <div className="live-url-row">
              <input className="set-select" value={chatText} placeholder="比如：你今天吃什么了"
                onChange={(e) => setChatText(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") sendChat(); }} />
              <button className="confirm-btn" disabled={busy || !chatText.trim()} onClick={sendChat}>
                发送
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* ── 最近台词 ── */}
      {lastLine?.text && (
        <section className="set-card live-card">
          <div className="svc-title">角色刚说的话</div>
          <div className="live-line">{lastLine.text}</div>
          <div className="pm-hint">
            {lastLine.spoken ? `已播报${lastLine.engine ? `（${lastLine.engine}）` : ""}` : `未播报：${lastLine.reason || ""}`}
          </div>
        </section>
      )}

      {/* ── 数值参数 ── */}
      <section className="set-card live-card">
        <div className="svc-title">数值参数</div>
        <div className="pm-form">
          <div className="pm-row">
            <div className="pm-field">
              <label>每日额度（元）—— 吃满算「吃饱」</label>
              <input className="set-select" type="number" min="1" step="1"
                value={settings?.daily_quota ?? ""} disabled={busy}
                onChange={(e) => setSettings((s) => ({ ...s, daily_quota: e.target.value }))}
                onBlur={(e) => updateSetting("daily_quota", Number(e.target.value) || 20)} />
            </div>
            <div className="pm-field">
              <label>多久饿到空（小时）</label>
              <input className="set-select" type="number" min="0.5" step="0.5"
                value={settings?.hunger_hours ?? ""} disabled={busy}
                onChange={(e) => setSettings((s) => ({ ...s, hunger_hours: e.target.value }))}
                onBlur={(e) => updateSetting("hunger_hours", Number(e.target.value) || 6)} />
            </div>
          </div>
          <div className="pm-row">
            <div className="pm-field">
              <label>每分钟最多说几句</label>
              <input className="set-select" type="number" min="1" max="20"
                value={settings?.max_lines_per_minute ?? ""} disabled={busy}
                onChange={(e) => setSettings((s) => ({ ...s, max_lines_per_minute: e.target.value }))}
                onBlur={(e) => updateSetting("max_lines_per_minute", Number(e.target.value) || 3)} />
            </div>
            <div className="pm-field">
              <label>同一观众冷却（秒）</label>
              <input className="set-select" type="number" min="0" max="600"
                value={settings?.max_chat_reply_seconds ?? ""} disabled={busy}
                onChange={(e) => setSettings((s) => ({ ...s, max_chat_reply_seconds: e.target.value }))}
                onBlur={(e) => updateSetting("max_chat_reply_seconds", Number(e.target.value) || 20)} />
            </div>
          </div>
          <div className="pm-hint">
            「定额度」决定一天要多少打赏才吃饱，调小更容易吃饱、观众更有成就感；调大则更难。
            「饥饿速度」越小，角色饿得越快，催促效果越强——但太快会让观众觉得被逼单。
          </div>
        </div>
      </section>

      <div className="set-card live-card live-warn">
        <div className="svc-title">合规提醒</div>
        <ul className="qq-steps">
          <li>画面左上角会常驻「AI 虚拟角色 · 非真人」标识，这是法规要求，<b>不要隐藏</b>。</li>
          <li>角色不会索要打赏、不会诱导消费——提示词和出稿过滤都已内置。</li>
          <li>建议你本人在场控场；平台要求虚拟人直播由真人驱动、真人实名。</li>
          <li>「没喂饱」只表现为没精神，<b>不要</b>设计成角色会死掉，避免情感绑架式诱导。</li>
        </ul>
      </div>

      {msg && <div className="set-saved-tip">{msg}</div>}
    </div>
  );
}
