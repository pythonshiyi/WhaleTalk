/* 直播 HUD 前端
   职责：轮询状态 → 更新画面 → 播放语音 → 触发动画。
   零依赖，OBS 浏览器源直接加载。 */

(function () {
  "use strict";

  // HUD 用单一免鉴权接口取全部数据（OBS 浏览器源带不了 Authorization 头）
  const API = { data: "/live/data" };

  const POLL_MS = 1000;
  const MOOD_TEXT = { starving: "饿坏了", hungry: "有点饿", normal: "还行", full: "吃饱了" };
  const GIFT_EMOJI = {
    "小心心": "💗", "玫瑰": "🌹", "棒棒糖": "🍭", "抖音": "🎵",
    "墨镜": "🕶️", "礼花筒": "🎉", "火箭": "🚀", "嘉年华": "🎡",
  };

  const $ = (id) => document.getElementById(id);
  const els = {
    stage: document.querySelector(".stage"),
    char: $("char"),
    charParticles: $("charParticles"),
    bgStars: $("bgStars"),
    feedFx: $("feedFx"),
    bubble: $("bubble"),
    bubbleText: $("bubbleText"),
    barFill: $("barFill"),
    barGlow: $("barGlow"),
    filledText: $("filledText"),
    remainingText: $("remainingText"),
    moodTag: $("moodTag"),
    todayYuan: $("todayYuan"),
    giftCount: $("giftCount"),
    uniqueGivers: $("uniqueGivers"),
    feedList: $("feedList"),
    celebrate: $("celebrate"),
    voice: $("voice"),
  };

  let prev = null;
  let lastEventTs = 0;
  let lastLineSeq = 0;
  let seenEventKeys = new Set();
  let bubbleTimer = null;

  // ── 背景：星场 ──

  function buildStars(n) {
    if (!els.bgStars) return;
    const frag = document.createDocumentFragment();
    for (let i = 0; i < n; i++) {
      const s = document.createElement("i");
      const size = Math.random() * 2.2 + 0.6;
      s.style.width = size + "px";
      s.style.height = size + "px";
      s.style.left = Math.random() * 100 + "%";
      s.style.top = Math.random() * 100 + "%";
      s.style.animationDuration = (2.2 + Math.random() * 4.5) + "s";
      s.style.animationDelay = (Math.random() * 5) + "s";
      s.style.opacity = (0.15 + Math.random() * 0.6).toFixed(2);
      frag.appendChild(s);
    }
    els.bgStars.appendChild(frag);
  }

  // ── 角色环绕粒子 ──

  function buildCharParticles(n) {
    if (!els.charParticles) return;
    const frag = document.createDocumentFragment();
    for (let i = 0; i < n; i++) {
      const p = document.createElement("i");
      p.style.left = (18 + Math.random() * 64) + "%";
      p.style.top = (55 + Math.random() * 40) + "%";
      p.style.animationDuration = (3.0 + Math.random() * 3.4) + "s";
      p.style.animationDelay = (Math.random() * 4.5) + "s";
      // 随机横向漂移，形成螺旋上升而不是直线
      p.style.setProperty("--dx", ((Math.random() - 0.5) * 56).toFixed(1) + "px");
      frag.appendChild(p);
    }
    els.charParticles.appendChild(frag);
  }

  // ── 工具 ──

  function yuan(v) {
    const n = Number(v) || 0;
    return "¥" + (n >= 1000 ? n.toFixed(0) : n.toFixed(1));
  }

  function bumpEl(el) {
    if (!el) return;
    el.classList.remove("bump");
    void el.offsetWidth;
    el.classList.add("bump");
    setTimeout(() => el.classList.remove("bump"), 340);
  }

  function setText(el, text) {
    if (el && el.textContent !== text) el.textContent = text;
  }

  // ── 渲染 ──

  function render(snap) {
    if (!snap) return;

    if (snap.mood) {
      const cls = "mood-" + snap.mood;
      if (document.body.className !== cls) document.body.className = cls;
    }

    setText(els.moodTag, MOOD_TEXT[snap.mood] || "还行");

    const pct = Math.max(0, Math.min(1, Number(snap.filled) || 0));
    const w = (pct * 100).toFixed(1) + "%";
    if (els.barFill) els.barFill.style.width = w;
    if (els.barGlow) els.barGlow.style.width = w;
    setText(els.filledText, Math.round(pct * 100) + "%");

    setText(els.todayYuan, yuan(snap.today_yuan));
    setText(els.giftCount, String(snap.gift_count || 0));
    setText(els.uniqueGivers, String(snap.unique_givers || 0));

    const remain = Number(snap.remaining_yuan) || 0;
    setText(els.remainingText, remain > 0 ? `还差 ${yuan(remain)} 吃饱` : "今天吃饱了");

    if (prev) {
      if (Number(snap.today_yuan) > Number(prev.today_yuan)) {
        bumpEl(els.todayYuan);
        bumpEl(els.filledText);
        pulseBar();
      }
      // 刚刚吃饱 → 放庆祝
      if (snap.mood === "full" && prev.mood !== "full") {
        celebrate();
      }
    }
    prev = snap;
  }

  function celebrate() {
    if (!els.celebrate) return;
    els.celebrate.hidden = false;
    const clone = els.celebrate.cloneNode(true);
    els.celebrate.parentNode.replaceChild(clone, els.celebrate);
    els.celebrate = clone;
    setTimeout(() => { if (els.celebrate) els.celebrate.hidden = true; }, 2700);
  }

  function showBubble(text) {
    if (!text || !els.bubble) return;
    els.bubbleText.textContent = text;
    els.bubble.hidden = false;
    els.bubble.style.animation = "none";
    void els.bubble.offsetWidth;
    els.bubble.style.animation = "";
    clearTimeout(bubbleTimer);
    const hold = Math.max(3600, Math.min(13000, text.length * 240));
    bubbleTimer = setTimeout(() => { els.bubble.hidden = true; }, hold);
  }

  function chomp(giftName) {
    if (els.char) {
      els.char.classList.remove("eating");
      void els.char.offsetWidth;
      els.char.classList.add("eating");
      setTimeout(() => els.char && els.char.classList.remove("eating"), 700);
    }
    const emoji = GIFT_EMOJI[giftName] || "✨";
    for (let i = 0; i < 4; i++) {
      const node = document.createElement("i");
      node.textContent = emoji;
      node.style.left = (32 + Math.random() * 36) + "%";
      node.style.top = (46 + Math.random() * 22) + "%";
      node.style.animationDelay = (i * 0.08) + "s";
      els.feedFx.appendChild(node);
      setTimeout(() => node.remove(), 1600 + i * 80);
    }
  }

  // ── 玩法：礼物驱动演出 ──

  // 礼物档位：金额越大，演出越夸张。这是"打赏改变画面"的核心。
  const GIFT_TIERS = [
    { min: 0,    tier: 1, act: "act-sway",  label: "小动" },
    { min: 5,    tier: 2, act: "act-jump",  label: "跳一下" },
    { min: 20,   tier: 2, act: "act-spin",  label: "转圈" },
    { min: 50,   tier: 3, act: "act-glowup", label: "发光" },
    { min: 100,  tier: 3, act: "act-shake", label: "大动作" },
  ];

  function tierOf(yuan) {
    let out = GIFT_TIERS[0];
    for (const t of GIFT_TIERS) if (yuan >= t.min) out = t;
    return out;
  }

  function spawnShockwave(tier) {
    const host = els.char && els.char.parentElement;
    if (!host) return;
    const el = document.createElement("div");
    el.className = "shockwave" + (tier >= 3 ? " tier-3" : tier === 2 ? " tier-2" : "");
    host.appendChild(el);
    setTimeout(() => el.remove(), 1200);
  }

  function spawnFloatAmount(yuan, tier) {
    const host = els.char && els.char.parentElement;
    if (!host) return;
    const el = document.createElement("div");
    el.className = "float-amt" + (tier >= 3 ? " tier-3" : tier === 2 ? " tier-2" : "");
    el.textContent = "+¥" + (yuan >= 10 ? yuan.toFixed(0) : yuan.toFixed(1));
    el.style.left = (38 + Math.random() * 24) + "%";
    el.style.top = "26%";
    host.appendChild(el);
    setTimeout(() => el.remove(), 1800);
  }

  function spawnBeam() {
    const el = document.createElement("div");
    el.className = "beam";
    els.stage.appendChild(el);
    setTimeout(() => el.remove(), 1900);
  }

  function spawnFlash() {
    const el = document.createElement("div");
    el.className = "flash";
    els.stage.appendChild(el);
    setTimeout(() => el.remove(), 700);
  }

  function spawnBanner(who, giftName, yuan) {
    const existing = els.stage.querySelector(".banner");
    if (existing) existing.remove();
    const el = document.createElement("div");
    el.className = "banner";
    const g = document.createElement("span");
    g.className = "banner-gift";
    g.textContent = GIFT_EMOJI[giftName] || "🎁";
    const w = document.createElement("span");
    w.className = "banner-who";
    w.textContent = who + " 送出 " + giftName;
    const a = document.createElement("span");
    a.className = "banner-amt";
    a.textContent = "+¥" + yuan.toFixed(0);
    el.append(g, w, a);
    els.stage.appendChild(el);
    setTimeout(() => el.remove(), 2500);
  }

  /** 送礼演出：动作 + 特效，按金额分层 */
  function performGift(evt) {
    const yuan = Number(evt.yuan) || 0;
    const t = tierOf(yuan);

    // 角色动作
    chomp(evt.gift);
    if (els.char) {
      els.char.classList.remove("act-sway", "act-jump", "act-spin", "act-shake", "act-glowup");
      void els.char.offsetWidth;
      els.char.classList.add(t.act);
      setTimeout(() => els.char && els.char.classList.remove(t.act), 1500);
    }

    spawnShockwave(t.tier);
    spawnFloatAmount(yuan, t.tier);

    // 大额礼物：光柱 + 闪白 + 横幅
    if (t.tier >= 3) {
      spawnBeam();
      spawnFlash();
      spawnBanner(evt.who || "观众", evt.gift || "礼物", yuan);
      screenShake(5);
    } else if (t.tier === 2) {
      screenShake(2);
    }
  }

  // ── 动态效果增强（纯 CSS 做不到的部分）──

  /** 眼睛跟随鼠标，让角色"在看你"。
   *  这是让角色显得有意识的最有效手段——观众会感觉自己被注视。 */
  let gazeStarted = false;
  function initGaze() {
    if (gazeStarted) return;
    const eyes = document.querySelectorAll(".eye-shine");
    if (!eyes.length) return;
    gazeStarted = true;

    let tx = 0, ty = 0, cx = 0, cy = 0;
    window.addEventListener("mousemove", (e) => {
      const cx0 = window.innerWidth / 2;
      const cy0 = window.innerHeight * 0.42;
      tx = Math.max(-1, Math.min(1, (e.clientX - cx0) / cx0)) * 3.6;
      ty = Math.max(-1, Math.min(1, (e.clientY - cy0) / (window.innerHeight * 0.42))) * 2.6;
    }, { passive: true });

    // OBS 中没有鼠标事件：用缓慢自动游移兜底，保证"活着"
    let idle = 0;
    (function tick() {
      idle += 0.008;
      if (!tx && !ty) {
        tx = Math.sin(idle) * 2.2;
        ty = Math.cos(idle * 0.7) * 1.4;
      }
      cx += (tx - cx) * 0.07;
      cy += (ty - cy) * 0.07;
      eyes.forEach((el) => {
        el.style.translate = cx.toFixed(2) + "px " + cy.toFixed(2) + "px";
      });
      requestAnimationFrame(tick);
    })();
  }

  /** 进度条增长时在尖端打一个光脉冲 */
  function pulseBar() {
    if (!els.barFill) return;
    els.barFill.classList.remove("gain");
    void els.barFill.offsetWidth;
    els.barFill.classList.add("gain");
    setTimeout(() => els.barFill && els.barFill.classList.remove("gain"), 1250);
  }

  /** 大额礼物：整个画面轻微震动 */
  function screenShake(strength) {
    const st = els.stage;
    if (!st) return;
    st.style.setProperty("--shake", String(strength));
    st.style.animation = "none";
    void st.offsetWidth;
    st.style.animation = "shakeScreen .5s cubic-bezier(.36,.07,.19,.97)";
    setTimeout(() => { if (st) st.style.animation = ""; }, 560);
  }

  /** 收到礼物事件：入列表 + 触发演出 */
  function pushEvent(evt) {
    if (!evt) return;
    const key = String(evt.ts || "") + "|" + String(evt.who || "") + "|" + String(evt.gift || "");
    if (seenEventKeys.has(key)) return;
    seenEventKeys.add(key);
    if (seenEventKeys.size > 240) seenEventKeys = new Set([key]);

    const item = document.createElement("div");
    item.className = "feed-item";

    const emoji = document.createElement("span");
    emoji.className = "gift-emoji";
    emoji.textContent = GIFT_EMOJI[evt.gift] || "🎁";

    const who = document.createElement("span");
    who.className = "who";
    who.textContent = (evt.who || "观众") + " 送了 " + (evt.gift || "礼物");

    const amt = document.createElement("span");
    amt.className = "amt";
    amt.textContent = yuan(evt.yuan);

    item.append(emoji, who, amt);
    els.feedList.appendChild(item);

    while (els.feedList.children.length > 5) {
      els.feedList.removeChild(els.feedList.firstChild);
    }
  }

  // ── 语音 ──

  function playVoice(url) {
    if (!url || !els.voice) return;
    try {
      els.voice.src = url;
      els.voice.currentTime = 0;
      const p = els.voice.play();
      if (p && typeof p.catch === "function") p.catch(() => {});
    } catch (e) { /* 语音失败不影响画面 */ }
  }

  // ── 轮询 ──

  async function poll() {
    try {
      const res = await fetch(API.data, { cache: "no-store" });
      if (res.ok) {
        const data = await res.json();

        if (data.snapshot) render(data.snapshot);

        const list = Array.isArray(data.events) ? data.events.slice() : [];
        list.reverse().forEach((evt) => {
          const ts = Number(evt.ts) || 0;
          if (ts > lastEventTs) {
            lastEventTs = ts;
            pushEvent(evt);
            performGift(evt);     // 礼物驱动演出（按金额分层）
          }
        });

        const line = data.line || {};
        const seq = Number(line.seq) || 0;
        if (seq > lastLineSeq && line.text) {
          lastLineSeq = seq;
          showBubble(line.text);
          if (line.audio) playVoice(line.audio);
        }
      }
    } catch (e) { /* 后端重启中，保持画面 */ }
    setTimeout(poll, POLL_MS);
  }

  window.__hud = {
    say(text, audioUrl) { showBubble(text); if (audioUrl) playVoice(audioUrl); },
    reset() { lastEventTs = 0; lastLineSeq = 0; seenEventKeys = new Set(); },
  };

  buildStars(110);
  buildCharParticles(20);
  initGaze();
  poll();
})();


