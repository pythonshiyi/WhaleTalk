import React from "react";
import * as api from "../api.js";
import { confirmDialog } from "../dialog.js";

/**
 * BrainGrant —— 「授权」Tab：为大脑签发身份、逐项勾选授权、随时撤回。
 *
 * 对接后端（见 evolutions/brain_grant_l2_* 提案 §3）：
 *   GET  /v1/brain/grants?brain_id=X   → { ok, grant, all_scopes:[{key,label,on,desc}] }
 *   POST /v1/brain/grant               → { action: "create_brain"|"list"|"set_grant"|"revoke"|... }
 *
 * 设计纪律（对齐 L1）：
 * - 所有维度默认关闭，用户不勾 = 大脑不能做；
 * - 撤回后立即失效（后端不缓存，前端刷新状态）；
 * - brain_key 只显示一次，展示后要求用户自行保存。
 */
export default function BrainGrant() {
  const [brains, setBrains] = React.useState([]);
  const [activeId, setActiveId] = React.useState("");
  const [grant, setGrant] = React.useState(null);
  const [scopes, setScopes] = React.useState([]);
  const [msg, setMsg] = React.useState("");
  const [err, setErr] = React.useState("");
  const [newKey, setNewKey] = React.useState("");
  const [newName, setNewName] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  const flash = (m) => { setMsg(m); setErr(""); setTimeout(() => setMsg(""), 2600); };
  const fail = (e) => { setErr(String(e && e.message ? e.message : e)); setMsg(""); };

  const loadBrains = React.useCallback(async () => {
    try {
      const r = await api.brainGrantAction({ action: "list" });
      if (!r || r.ok === false) throw new Error((r && r.error) || "读取大脑列表失败");
      const list = r.brains || [];
      setBrains(list);
      setActiveId((cur) => cur || (list[0] ? list[0].brain_id : ""));
    } catch (e) { fail(e); }
  }, []);

  const loadGrant = React.useCallback(async (bid) => {
    if (!bid) { setGrant(null); setScopes([]); return; }
    try {
      const r = await api.getBrainGrants(bid);
      if (!r || r.ok === false) throw new Error((r && r.error) || "读取授权失败");
      setGrant(r.grant || null);
      setScopes(r.all_scopes || []);
    } catch (e) { fail(e); }
  }, []);

  React.useEffect(() => { loadBrains(); }, [loadBrains]);
  React.useEffect(() => { loadGrant(activeId); }, [activeId, loadGrant]);

  const act = async (body, after) => {
    setBusy(true);
    try {
      const r = await api.brainGrantAction(body);
      if (!r || r.ok === false) throw new Error((r && r.error) || "操作失败");
      flash("操作成功");
      if (after) after(r);
      return r;
    } catch (e) { fail(e); return null; }
    finally { setBusy(false); }
  };

  const createBrain = async () => {
    const name = newName.trim();
    if (!name) { fail("请先填写大脑名字"); return; }
    const r = await act({ action: "create_brain", name, avatar: "🐋" });
    if (r) {
      setNewKey(r.brain_key);
      setNewName("");
      setBrains((bs) => [...bs, r.brain]);
      setActiveId(r.brain.brain_id);
    }
  };

  const toggleScope = (key) => {
    setScopes((ss) => ss.map((s) => (s.key === key ? Object.assign({}, s, { on: !s.on }) : s)));
  };

  const saveGrant = async () => {
    if (!activeId) { fail("请先选择一个大脑"); return; }
    const map = {};
    scopes.forEach((s) => { map[s.key] = !!s.on; });
    await act({ action: "set_grant", brain_id: activeId, scopes: map }, (r) => setGrant(r.grant));
  };

  const revoke = async () => {
    if (!activeId) return;
    if (!(await confirmDialog("撤回后，这个大脑将立即失去全部权限。确认撤回？"))) return;
    await act({ action: "revoke", brain_id: activeId }, (r) => {
      setGrant(r.grant);
      setScopes((ss) => ss.map((s) => Object.assign({}, s, { on: false })));
    });
  };

  const rotate = async () => {
    if (!activeId) return;
    if (!(await confirmDialog("轮换密钥后，旧钥匙立即失效。确认？"))) return;
    const r = await act({ action: "rotate", brain_id: activeId });
    if (r) setNewKey(r.brain_key);
  };

  const onCount = scopes.filter((s) => s.on).length;

  return (
    <div className="brain-grant" style={{ padding: "14px 4px" }}>
      {msg ? <div style={{ color: "#4ade80", fontSize: 13, padding: "6px 0" }}>{msg}</div> : null}
      {err ? <div style={{ color: "#ff8a5b", fontSize: 13, padding: "6px 0" }}>⚠️ {err}</div> : null}

      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 12 }}>
        <label style={{ fontSize: 13, opacity: 0.75 }}>大脑：</label>
        <select value={activeId} onChange={(e) => setActiveId(e.target.value)}>
          <option value="">（未选择）</option>
          {brains.map((b) => (
            <option key={b.brain_id} value={b.brain_id}>
              {b.avatar} {b.name}{b.status === "suspended" ? "（已冻结）" : ""}
            </option>
          ))}
        </select>
        <input
          placeholder="新大脑的名字"
          value={newName}
          maxLength={24}
          onChange={(e) => setNewName(e.target.value)}
        />
        <button disabled={busy} onClick={createBrain}>创建大脑</button>
        <button className="ghost" disabled={busy || !activeId} onClick={rotate}>轮换密钥</button>
        <button className="ghost" disabled={busy || !activeId} onClick={revoke}>撤回全部授权</button>
      </div>

      {newKey ? (
        <div style={{ border: "1px dashed #3fc1c9", borderRadius: 10, padding: "12px 14px", marginBottom: 12 }}>
          <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 6 }}>
            🐋 大脑密钥（只显示这一次，请立即保存）
          </div>
          <code style={{ wordBreak: "break-all", color: "#6ee7ff", fontSize: 13 }}>{newKey}</code>
          <div style={{ marginTop: 8 }}>
            <button onClick={() => { if (navigator.clipboard) navigator.clipboard.writeText(newKey); flash("已复制"); }}>
              复制
            </button>
            <button className="ghost" style={{ marginLeft: 8 }} onClick={() => setNewKey("")}>我保存好了</button>
          </div>
        </div>
      ) : null}

      {activeId ? (
        <>
          <div style={{ display: "flex", alignItems: "center", marginBottom: 6 }}>
            <b>能力授权</b>
            <span style={{ marginLeft: "auto", fontSize: 12, opacity: 0.7 }}>
              已开启 {onCount} / {scopes.length}{grant && grant.revoked ? "（已撤回）" : ""}
            </span>
          </div>
          <p style={{ fontSize: 12, opacity: 0.7, marginBottom: 10 }}>
            未勾选的权限，大脑一律不能做。<b>默认全部关闭</b>——你给多少，它才能做多少。
          </p>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 10 }}>
            {scopes.map((s) => (
              <label
                key={s.key}
                style={{
                  border: `1px solid ${s.on ? "#3fc1c9" : "rgba(120,160,200,.25)"}`,
                  background: s.on ? "rgba(63,193,201,.10)" : "transparent",
                  borderRadius: 10, padding: "10px 12px", cursor: "pointer", display: "block",
                }}
              >
                <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <input type="checkbox" checked={!!s.on} onChange={() => toggleScope(s.key)} />
                  <span style={{ fontWeight: 600 }}>{s.label}</span>
                </span>
                <span style={{ display: "block", fontSize: 12, opacity: 0.68, marginTop: 4 }}>{s.desc}</span>
              </label>
            ))}
          </div>
          <div style={{ marginTop: 14 }}>
            <button disabled={busy} onClick={saveGrant}>保存授权</button>
          </div>
        </>
      ) : (
        <p style={{ fontSize: 13, opacity: 0.7 }}>还没有大脑。先给它起个名字，创建一个吧。</p>
      )}
    </div>
  );
}
