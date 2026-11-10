/* =====================================================================
   events.js - หน้า Status & Logs (events.html)
     Sensors : การ์ดต่อ sensor (แบบ "Sensors With Status Warning" ของ PRTG)
               ชื่อ sensor · กลุ่ม » เครื่อง (IP) · ค่าล่าสุด · สถานะ · ข้อความ · กราฟ 1 ชม. · เป็นแบบนี้มานานเท่าไหร่
     System log   : sensor เปลี่ยนสถานะ ฯลฯ   (data/state/events.log ที่ puller เขียน)
     Activity log : ใครทำอะไรในหน้าเว็บ     (data/state/audit.log, admin เท่านั้น)
   ข้อความจากเซิร์ฟเวอร์ผ่าน esc() ก่อนใส่ HTML ทุกครั้ง
   ===================================================================== */
"use strict";
const $ = id => document.getElementById(id);
const E = { tab: "warning", role: "viewer", data: null, find: "", rows: [], hist: null };

function esc(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function fmtTime(sec) {
    if (!sec) return "-";
    return new Date(sec * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
}
function fmtAgo(sec) {
    if (!sec) return "-";
    let s = Math.max(0, Date.now() / 1000 - sec);
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${m}m`;
}
async function getJson(url) {
    const r = await fetch(url, { cache: "no-store", credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
}

const ST = {
    down: ["D", "Down", "dn"], error: ["E", "Error", "dn"], warning: ["W", "Warning", "wr"],
    ok: ["✓", "OK", "ok"], paused: ["II", "Paused", "ps"], unknown: ["?", "Unknown", "na"],
    recovered: ["↺", "Recovered", "ok"], info: ["i", "Info", "na"]
};
const TAB_TITLE = { "down,error": "Sensors With Status Down / Error", warning: "Sensors With Status Warning",
    ok: "Sensors With Status OK", paused: "Paused Sensors", acknowledged: "Acknowledged Sensors", all: "All Sensors", system: "System log", activity: "Activity log",
    recovered: "Recovered in the last 24 hours", incidents: "Incident history" };

function fmtDur(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m` : `${s}s`;
}

/* ปัญหาที่เกิดแล้ว: เริ่ม / จบ / นานเท่าไหร่ / สถานะแย่สุด / ค่าสูงสุด  (recovered = เฉพาะที่หายแล้ว 24 ชม.) */
async function showIncidents(recoveredOnly) {
    const hours = recoveredOnly ? 24 : $("evHours").value;
    const d = await getJson(`/api/incidents?hours=${hours}&ongoing=${recoveredOnly ? 0 : 1}`);
    const rows = (d.incidents || []).filter(i => (!recoveredOnly || i.state === "recovered") && matches(`${i.web} ${i.group} ${i.name}`));
    const ongoing = rows.filter(i => i.state === "ongoing").length;
    $("evMeta").textContent = recoveredOnly
        ? `${rows.length} problem${rows.length === 1 ? "" : "s"} started and recovered by themselves in the last 24 hours`
        : `${rows.length} incident${rows.length === 1 ? "" : "s"}${ongoing ? ` (${ongoing} still ongoing)` : ""} · an incident starts when a sensor leaves OK and ends when it is OK again`;
    const peak = i => i.peak == null ? "-" : `${Number(i.peak).toFixed(1)}${i.unit === "%" ? " %" : i.unit === "h" ? " h" : ""}`;
    const stateCell = i => i.state === "ongoing" ? `<b class="evst dn">ongoing</b>` : i.state === "paused" ? `<span class="evst ps">ended (paused)</span>` : `<span class="evst ok">↺ recovered</span>`;
    $("evBody").innerHTML = rows.length ? `<div class="tblw"><table class="evtbl"><thead><tr><th>Worst status</th><th>Machine</th><th>Sensor</th><th>Started</th><th>Ended</th><th>Duration</th><th>Peak</th><th>State</th></tr></thead><tbody>${
        rows.map(i => `<tr><td>${levelCell({ level: i.worst })}</td><td><a href="/?web=${encodeURIComponent(i.web)}">${esc(i.web)}</a>${i.group ? `<div class="evpath">${esc(i.group.split("/").join(" » "))}</div>` : ""}</td><td>${esc(i.name)}</td><td>${esc(fmtTime(i.start))}</td><td>${i.end ? esc(fmtTime(i.end)) : "-"}</td><td><b>${fmtDur(i.duration)}</b></td><td>${peak(i)}</td><td>${stateCell(i)}</td></tr>`).join("")
    }</tbody></table></div>` : `<div class="empty">${recoveredOnly ? "No problems recovered in the last 24 hours" : "No incidents in this period"}</div>`;
}
const canOperate = () => E.role === "operator" || E.role === "admin";

function spark(pts) {
    if (!pts || pts.length < 2) return `<span class="evnone">no graph</span>`;
    const v = pts.map(p => p[1]), lo = Math.min(...v, 0), hi = Math.max(...v, 100);
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
    const xy = pts.map(p => `${((p[0] - t0) / (t1 - t0 || 1) * 160).toFixed(1)},${(30 - (p[1] - lo) / (hi - lo || 1) * 28).toFixed(1)}`);
    return `<svg class="evspark" viewBox="0 0 160 32" preserveAspectRatio="none" aria-hidden="true"><path d="M0,32 L${xy.join(" L")} L160,32Z" fill="var(--chip)"/><path d="M${xy.join(" L")}" fill="none" stroke="var(--mut)" stroke-width="1.3" vector-effect="non-scaling-stroke"/></svg><span class="evlast">${esc(v[v.length - 1].toFixed(1))}%</span>`;
}

function value(s) {
    if (s.value == null) return "-";
    return s.unit === "%" ? `${Number(s.value).toFixed(1)} %` : s.unit === "h" ? `${Number(s.value).toFixed(1)} h ago`
        : s.unit === "min" ? `${Math.round(s.value)} min ago` : esc(s.value);
}

function sensorCard(s, i) {
    const [icon, label, cls] = ST[s.status] || ST.unknown;
    const ack = s.ack ? `<div class="evack"><b>Acknowledged</b> by ${esc(s.ack.by || "-")} · ${esc(fmtTime(s.ack.at))}${s.ack.note ? `<div class="evnote">“${esc(s.ack.note)}”</div>` : ""}</div>` : "";
    const ackable = ["down", "error", "warning", "unknown"].includes(s.status);
    const btn = !canOperate() || !ackable ? "" : s.ack
        ? `<button type="button" class="svbtn sm" data-unack="${i}">Remove acknowledgement</button>`
        : `<button type="button" class="svbtn sm" data-ack="${i}">Acknowledge</button>`;
    const limits = s.warning != null ? `<span class="evlim">limits ${esc(s.warning)} / ${esc(s.error)}${s.unit === "%" ? "%" : ""}</span>` : "";
    const path = s.group ? s.group.split("/").map(esc).join(" » ") : "";
    return `<article class="evcard s-${cls}">
      <h3><span class="evico ${cls}">${icon}</span>${esc(s.name)}${s.ack ? ` <span class="evackt">(Acknowledged)</span>` : ""}<span class="evbtns">${btn}</span></h3>
      <div class="evgrid">
        <div><small>${path ? "Group » Device" : "Device"}</small>${path ? `<span class="evpath">${path} »</span>` : ""}<a href="/?web=${encodeURIComponent(s.web)}"><b>${esc(s.web)}</b>${s.host ? ` (${esc(s.host)})` : ""}</a></div>
        <div><small>Status</small><b class="evst ${cls}">${label}</b> <span class="evsince" title="since ${esc(fmtTime(s.since))}">for ${fmtAgo(s.since)}</span></div>
        <div><small>Last value</small>${value(s)} ${limits}</div>
        <div><small>Message</small>${esc(s.message || "-")}</div>
        ${["cpu", "memory", "disk", "availability"].includes(s.kind)
            ? `<div><small>Graph (last 1 hour · click for more)</small><div class="evgraph" data-hist="${i}" role="button" tabindex="0" title="Open history">${s.history ? spark(s.history) : `<span class="evnone">open graph</span>`}</div></div>`
            : `<div><small>Graph</small><span class="evnone">-</span></div>`}
      </div>${ack}
    </article>`;
}

function matches(text) { return !E.find || String(text).toLowerCase().includes(E.find); }

async function showSensors() {
    const statuses = E.tab === "all" ? "" : E.tab;
    E.rows = [];
    const withGraph = E.tab !== "ok" && E.tab !== "all" ? "1" : "0";
    const d = await getJson(`/api/sensors?status=${encodeURIComponent(statuses)}&history=${withGraph}`);
    const rows = (d.sensors || []).filter(s => matches(`${s.web} ${s.host} ${s.group} ${s.name} ${s.message} ${s.ack ? s.ack.note : ""}`));
    E.rows = rows;
    const o = d.options || {};
    $("evMeta").textContent = `${rows.length} sensor${rows.length === 1 ? "" : "s"} · updated ${fmtTime(d.updated_at)} · checked every ${o.interval || 30} s, status changes after ${o.confirm_checks || 2} checks in a row, recovers ${o.hysteresis ?? 2} points below the limit`
        + (d.thresholds ? ` · limits: CPU ${d.thresholds.cpu.warning}/${d.thresholds.cpu.error}% · Memory ${d.thresholds.memory.warning}/${d.thresholds.memory.error}% · Disk ${d.thresholds.disk.warning}/${d.thresholds.disk.error}% (warning/error)` : "");
    $("evBody").innerHTML = rows.length ? `<div class="evcards">${rows.map((s, i) => sensorCard(s, i)).join("")}</div>`
        : `<div class="empty">${d.updated_at ? "No sensors with this status" : "No sensor data yet - the puller checks every 30 seconds (needs the updated central-puller)"}</div>`;
}

function levelCell(e) {
    const [icon, label, cls] = ST[e.level] || ST.unknown;
    return `<span class="evst ${cls}"><i class="evico sm ${cls}">${icon}</i>${label}</span>`;
}

async function showSystem() {
    const d = await getJson(`/api/events?kind=system&limit=500`);
    const rows = (d.entries || []).filter(e => matches(JSON.stringify(e)));
    if (d.entries && d.entries.length && window.cmMarkSeen) window.cmMarkSeen("system", d.entries[0].time);
    $("evMeta").textContent = `${rows.length} entr${rows.length === 1 ? "y" : "ies"} (newest first) · sensor status changes recorded by the puller`;
    $("evBody").innerHTML = rows.length ? `<div class="tblw"><table class="evtbl"><thead><tr><th>Time</th><th>Status</th><th>Machine</th><th>Sensor</th><th>Message</th></tr></thead><tbody>${
        rows.map(e => `<tr><td>${esc(fmtTime(e.time))}</td><td>${levelCell(e)}</td><td>${e.web ? `<a href="/?web=${encodeURIComponent(e.web)}">${esc(e.web)}</a>` : "-"}</td><td>${esc(e.sensor || e.source || "-")}</td><td class="evmsg">${esc(e.message || "")}</td></tr>`).join("")
    }</tbody></table></div>` : `<div class="empty">No system log entries</div>`;
}

const ACTION_TEXT = {
    "login": "Log in", "logout": "Log out",
    "target.add": "Add machine", "target.update": "Edit machine", "target.delete": "Delete machine", "target.pause": "Pause / Resume machine",
    "target.services": "Select services", "target.deploy_key": "Deploy SSH key", "target.scan_host_key": "Scan host key",
    "check.add": "Add web check", "check.update": "Edit web check", "check.delete": "Delete web check", "check.pause": "Pause / Resume web check",
    "user.add": "Add user", "user.update": "Edit user", "user.delete": "Delete user",
    "sensor.ack": "Acknowledge sensor", "sensor.unack": "Remove acknowledgement"
};

async function showActivity() {
    const d = await getJson(`/api/events?kind=activity&limit=500`);
    const rows = (d.entries || []).filter(e => matches(JSON.stringify(e)));
    if (d.entries && d.entries.length && window.cmMarkSeen) window.cmMarkSeen("activity", d.entries[0].time);
    $("evMeta").textContent = `${rows.length} entr${rows.length === 1 ? "y" : "ies"} (newest first) · every change made through this website (passwords are never stored)`;
    const details = e => {
        const d = e.details || {};
        const parts = Object.entries(d).map(([k, v]) => k === "paused" ? (v ? "paused" : "resumed") : `${k}: ${typeof v === "object" ? JSON.stringify(v) : v}`);
        return esc(parts.join(" · ") || "-");
    };
    $("evBody").innerHTML = rows.length ? `<div class="tblw"><table class="evtbl"><thead><tr><th>Time</th><th>User</th><th>Role</th><th>IP</th><th>Action</th><th>Target</th><th>Result</th><th>Details</th></tr></thead><tbody>${
        rows.map(e => `<tr><td>${esc(fmtTime(e.time))}</td><td><b>${esc(e.user || "-")}</b></td><td>${esc(e.role || "-")}</td><td>${esc(e.ip || "-")}</td><td>${esc(ACTION_TEXT[e.action] || e.action)}</td><td>${esc(e.target ?? "-")}</td><td class="${e.ok ? "ok" : "bad"}">${e.ok ? "✓ OK" : `✕ failed${e.status ? " (" + esc(e.status) + ")" : ""}`}</td><td class="evmsg">${details(e)}</td></tr>`).join("")
    }</tbody></table></div>` : `<div class="empty">No activity yet</div>`;
}

/* ---------- Acknowledge ---------- */
async function postJson(url, body) {
    const r = await fetch(url, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
    return d;
}
async function ack(s) {
    const note = prompt(`Acknowledge "${s.web} · ${s.name}"\nNote (what is being done / why it is OK for now):`, "");
    if (note === null) return;
    try { await postJson("/api/sensors/ack", { id: s.id, note }); render(); }
    catch (e) { alert("Acknowledge failed: " + e.message); }
}
async function unack(s) {
    try { await postJson("/api/sensors/unack", { id: s.id }); render(); }
    catch (e) { alert("Failed: " + e.message); }
}

/* ---------- กราฟย้อนหลัง (modal) ---------- */
function bigChart(d) {
    const pts = d.series || [];
    if (pts.length < 2) return `<div class="empty">No data for this range</div>`;
    const W = 860, H = 280, L = 44, R = 10, T = 10, B = 26, PW = W - L - R, PH = H - T - B;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    const hi = d.unit === "%" ? 100 : Math.max(...pts.map(p => p[1])) * 1.1 || 1;
    const X = t => L + (t - t0) / (t1 - t0 || 1) * PW, Y = v => T + PH - Math.min(Math.max(v, 0), hi) / hi * PH;
    let s = "";
    for (let i = 0; i <= 4; i++) { const v = hi * i / 4, y = Y(v); s += `<line x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke="var(--line)"/><text x="${L - 6}" y="${y + 4}" text-anchor="end" font-size="11" fill="var(--mut)">${Math.round(v)}</text>`; }
    for (let i = 0; i < 6; i++) { const t = t0 + (t1 - t0) * i / 5, dt = new Date(t * 1000);
        const lab = (t1 - t0) > 2 * 86400 ? dt.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : dt.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
        s += `<text x="${X(t)}" y="${H - 6}" text-anchor="${i === 0 ? "start" : i === 5 ? "end" : "middle"}" font-size="11" fill="var(--mut)">${lab}</text>`; }
    const lim = (v, c, n) => v != null && v <= hi ? `<line x1="${L}" x2="${W - R}" y1="${Y(v)}" y2="${Y(v)}" stroke="${c}" stroke-dasharray="6 5"/><text x="${W - R - 4}" y="${Y(v) - 4}" text-anchor="end" font-size="11" fill="${c}">${n} ${v}</text>` : "";
    s += lim(d.warning, "#d98a00", "warning") + lim(d.error, "#e5484d", "error");
    const path = pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join(" ");
    s += `<path d="${path} L${X(t1)},${T + PH} L${X(t0)},${T + PH}Z" fill="var(--fill)"/><path d="${path}" fill="none" stroke="var(--blue)" stroke-width="1.6"/>`;
    const vals = pts.map(p => p[1]);
    return `<svg viewBox="0 0 ${W} ${H}" class="evbig" role="img" aria-label="history">${s}</svg>
      <div class="evmstat">min ${Math.min(...vals).toFixed(1)} · avg ${(vals.reduce((a, b) => a + b, 0) / vals.length).toFixed(1)} · max ${Math.max(...vals).toFixed(1)}${d.unit === "%" ? " %" : ""}</div>`;
}
async function openHistory(s, range) {
    E.hist = s;
    $("evModal").hidden = false;
    $("evmTitle").textContent = `${s.web} · ${s.name}`;
    [...$("evmRange").children].forEach(b => b.setAttribute("aria-pressed", b.dataset.r === range));
    $("evmChart").innerHTML = `<div class="empty">Loading...</div>`;
    if (!["cpu", "memory", "disk", "availability"].includes(s.kind)) { $("evmChart").innerHTML = `<div class="empty">This sensor has no graph</div>`; return; }
    try { $("evmChart").innerHTML = bigChart(await getJson(`/api/sensors/history?id=${encodeURIComponent(s.id)}&range=${range}`)); }
    catch (e) { $("evmChart").innerHTML = `<div class="empty">Load failed: ${esc(e.message)}</div>`; }
}

async function render() {
    [...$("evTabs").querySelectorAll("button[data-tab]")].forEach(b => b.setAttribute("aria-pressed", b.dataset.tab === E.tab));
    $("evTitle").textContent = TAB_TITLE[E.tab] || "Sensors";
    try {
        $("evHours").hidden = E.tab !== "incidents";
        if (E.tab === "recovered") await showIncidents(true);
        else if (E.tab === "incidents") await showIncidents(false);
        else if (E.tab === "system") await showSystem();
        else if (E.tab === "activity") await showActivity();
        else await showSensors();
    } catch (err) {
        if (err.message !== "auth") $("evBody").innerHTML = `<div class="empty">Load failed: ${esc(err.message)}</div>`;
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    const p = new URLSearchParams(location.search);
    E.tab = p.get("tab") || p.get("status") || "warning";
    if (E.tab === "error" || E.tab === "down") E.tab = "down,error";
    try {
        const me = await getJson("/api/me");
        E.role = me.role || "viewer";
        $("currentUsername").textContent = me.username || "?";
        $("tabActivity").hidden = E.role !== "admin";
        $("settingsLink").hidden = E.role !== "admin";
        if (E.tab === "activity" && E.role !== "admin") E.tab = "system";
    } catch (e) { return; }
    $("evTabs").addEventListener("click", e => {
        const b = e.target.closest("button[data-tab]"); if (!b) return;
        E.tab = b.dataset.tab;
        history.replaceState(null, "", `/events?${["system", "activity", "recovered", "incidents"].includes(E.tab) ? "tab" : "status"}=${E.tab}`);
        render();
    });
    $("evFind").addEventListener("input", e => { E.find = e.target.value.trim().toLowerCase(); render(); });
    $("evHours").addEventListener("change", render);
    $("evBody").addEventListener("click", e => {
        const a = e.target.closest("[data-ack]"), u = e.target.closest("[data-unack]"), h = e.target.closest("[data-hist]");
        if (a) ack(E.rows[+a.dataset.ack]);
        else if (u) unack(E.rows[+u.dataset.unack]);
        else if (h) openHistory(E.rows[+h.dataset.hist], "24h");
    });
    $("evBody").addEventListener("keydown", e => { const h = e.target.closest("[data-hist]"); if (h && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); h.click(); } });
    $("evmRange").addEventListener("click", e => { const b = e.target.closest("[data-r]"); if (b && E.hist) openHistory(E.hist, b.dataset.r); });
    $("evmClose").addEventListener("click", () => { $("evModal").hidden = true; E.hist = null; });
    $("evModal").addEventListener("click", e => { if (e.target.id === "evModal") { $("evModal").hidden = true; E.hist = null; } });
    document.addEventListener("keydown", e => { if (e.key === "Escape") { $("evModal").hidden = true; E.hist = null; } });
    $("logoutButton").addEventListener("click", async () => {
        try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } finally { location.href = "/login"; }
    });
    render();
    setInterval(() => { if ($("evModal").hidden) render(); }, 30000);
});
