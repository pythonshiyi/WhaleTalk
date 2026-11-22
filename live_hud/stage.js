/* 直播舞台：轮询 /live/data 渲染立绘占位、台词、弹幕与情绪；控制本地模拟。 */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var el = {
    persona: $("personaName"), viewers: $("viewers"), energy: $("energyBar"), mood: $("mood"),
    topic: $("topic"), bubble: $("bubble"), bubbleText: $("bubbleText"),
    subtitle: $("subtitle"), events: $("eventList"), hint: $("hint"),
    charWrap: $("charWrap"), voice: $("voice")
  };

  var S = { seq: 0, audioSeq: 0, token: null, eventKey: "", speakTimer: null, needsUnlock: false };

  function hint(text) { if (el.hint) el.hint.textContent = text || ""; }

  function esc(node, text) { node.textContent = text == null ? "" : String(text); }

  function icon(kind) {
    return { gift: "🎁", enter: "👋", follow: "⭐", like: "👍", sc: "💬", danmaku: "💬" }[kind] || "·";
  }

  function describe(ev) {
    if (ev.kind === "gift") return " 送出 " + (ev.gift || "礼物");
    if (ev.kind === "enter") return " 进入直播间";
    if (ev.kind === "follow") return " 关注了";
    if (ev.kind === "like") return " 点了赞";
    return " " + (ev.text || "");
  }

  function renderEvents(list) {
    var key = (list || []).map(function (e) { return (e.ts || 0) + (e.user || "") + (e.text || ""); }).join("|");
    if (key === S.eventKey) return;
    S.eventKey = key;
    el.events.innerHTML = "";
    (list || []).slice().reverse().forEach(function (ev) {
      var li = document.createElement("li");
      li.className = ev.kind || "";
      var b = document.createElement("b");
      b.textContent = icon(ev.kind) + " " + (ev.user_name || ev.user || "观众");
      li.appendChild(b);
      li.appendChild(document.createTextNode(describe(ev)));
      el.events.appendChild(li);
    });
  }

  function setSpeaking(ms) {
    el.charWrap.classList.add("speaking");
    clearTimeout(S.speakTimer);
    S.speakTimer = setTimeout(function () { el.charWrap.classList.remove("speaking"); }, ms);
  }

  function playVoice(url) {
    if (!url) return;
    el.voice.src = url;
    var p = el.voice.play();
    if (p && p.catch) p.catch(function () {
      S.needsUnlock = true;
      hint("浏览器拦截了自动播放：点一下页面任意位置即可出声");
    });
  }

  function render(data) {
    var room = data.room || {};
    var line = data.line || {};

    el.persona.textContent = room.persona || "大肥鱼";
    el.viewers.textContent = room.viewers || 0;
    el.mood.textContent = room.mood || "";
    el.energy.style.width = Math.max(0, Math.min(100, Number(room.energy) || 0)) + "%";
    el.topic.textContent = room.topic ? "此刻：" + room.topic : "";

    renderEvents(data.events || []);

    if ((line.seq || 0) > S.seq && line.text) {
      S.seq = line.seq;
      el.bubbleText.textContent = line.text;
      el.bubble.hidden = false;
      el.subtitle.textContent = line.text;
      setSpeaking(3200);
    }
    if (line.audio && (line.audio_seq || 0) > S.audioSeq) {
      S.audioSeq = line.audio_seq;
      playVoice(line.audio);
    }
  }

  async function poll() {
    try {
      var r = await fetch("/live/data", { cache: "no-store" });
      if (!r.ok) throw new Error("HTTP " + r.status);
      render(await r.json());
    } catch (e) {
      hint("后端未连接：" + e.message);
    } finally {
      setTimeout(poll, 900);
    }
  }

  async function token() {
    if (S.token) return S.token;
    var r = await fetch("/v1/token", { cache: "no-store" });
    if (!r.ok) throw new Error("拿不到 token");
    S.token = (await r.json()).token;
    return S.token;
  }

  async function sim(action) {
    try {
      var t = await token();
      var r = await fetch("/v1/live/sim", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + t },
        body: JSON.stringify({ action: action, speed: 1 })
      });
      if (!r.ok) throw new Error("HTTP " + r.status);
      hint({ start: "模拟已开始", stop: "模拟已停止", step: "已单步" }[action] || "ok");
    } catch (e) {
      hint("模拟控制失败：" + e.message);
    }
  }

  $("btnStart").addEventListener("click", function () { sim("start"); });
  $("btnStop").addEventListener("click", function () { sim("stop"); });
  $("btnStep").addEventListener("click", function () { sim("step"); });

  document.addEventListener("click", function () {
    if (!S.needsUnlock) return;
    S.needsUnlock = false;
    el.voice.play().catch(function () {});
  });

  hint("本地 P0 · 点击「开始模拟」跑剧本（立绘为占位）");
  poll();
})();
