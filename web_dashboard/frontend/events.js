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
const E = { tab: "warning", role: "viewer", data: null, find: "" };

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
    ok: ["✓", "OK", "ok"], paused: ["II", "Paused", "ps"], unknown: ["?", "Unknown", "na"]
};
const TAB_TITLE = { "down,error": "Sensors With Status Down / Error", warning: "Sensors With Status Warning",
    ok: "Sensors With Status OK", paused: "Paused Sensors", all: "All Sensors", system: "System log", activity: "Activity log" };

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

function sensorCard(s) {
    const [icon, label, cls] = ST[s.status] || ST.unknown;
    const path = (s.group ? s.group.split("/") : ["Ungrouped"]).map(esc).join(" » ");
    return `<article class="evcard s-${cls}">
      <h3><span class="evico ${cls}">${icon}</span>${esc(s.name)}</h3>
      <div class="evgrid">
        <div><small>Group » Device</small><span class="evpath">${path} »</span><a href="/?web=${encodeURIComponent(s.web)}"><b>${esc(s.web)}</b>${s.host ? ` (${esc(s.host)})` : ""}</a></div>
        <div><small>Status</small><b class="evst ${cls}">${label}</b> <span class="evsince" title="since ${esc(fmtTime(s.since))}">for ${fmtAgo(s.since)}</span></div>
        <div><small>Last value</small>${value(s)}</div>
        <div><small>Message</small>${esc(s.message || "-")}</div>
        <div><small>Graph (last 1 hour)</small><div class="evgraph">${s.history ? spark(s.history) : `<span class="evnone">-</span>`}</div></div>
      </div>
    </article>`;
}

function matches(text) { return !E.find || String(text).toLowerCase().includes(E.find); }

async function showSensors() {
    const statuses = E.tab === "all" ? "" : E.tab;
    const withGraph = E.tab !== "ok" && E.tab !== "all" ? "1" : "0";
    const d = await getJson(`/api/sensors?status=${encodeURIComponent(statuses)}&history=${withGraph}`);
    const rows = (d.sensors || []).filter(s => matches(`${s.web} ${s.host} ${s.group} ${s.name} ${s.message}`));
    $("evMeta").textContent = `${rows.length} sensor${rows.length === 1 ? "" : "s"} · updated ${fmtTime(d.updated_at)} · checked every 30 seconds by the puller`
        + (d.thresholds ? ` · limits: CPU ${d.thresholds.cpu.warning}/${d.thresholds.cpu.error}% · Memory ${d.thresholds.memory.warning}/${d.thresholds.memory.error}% · Disk ${d.thresholds.disk.warning}/${d.thresholds.disk.error}% (warning/error)` : "");
    $("evBody").innerHTML = rows.length ? `<div class="evcards">${rows.map(sensorCard).join("")}</div>`
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
    "user.add": "Add user", "user.update": "Edit user", "user.delete": "Delete user"
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

async function render() {
    [...$("evTabs").querySelectorAll("button[data-tab]")].forEach(b => b.setAttribute("aria-pressed", b.dataset.tab === E.tab));
    $("evTitle").textContent = TAB_TITLE[E.tab] || "Sensors";
    try {
        if (E.tab === "system") await showSystem();
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
        history.replaceState(null, "", `/events?${["system", "activity"].includes(E.tab) ? "tab" : "status"}=${E.tab}`);
        render();
    });
    $("evFind").addEventListener("input", e => { E.find = e.target.value.trim().toLowerCase(); render(); });
    $("logoutButton").addEventListener("click", async () => {
        try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } finally { location.href = "/login"; }
    });
    render();
    setInterval(render, 30000);
});
