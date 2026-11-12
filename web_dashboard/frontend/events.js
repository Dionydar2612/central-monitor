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
const OV_SAVED = (() => { try { return JSON.parse(sessionStorage.getItem("cm.ov") || "{}"); } catch (e) { return {}; } })();
function ovSave() {
    try { sessionStorage.setItem("cm.ov", JSON.stringify({ range: E.ov.range, mode: E.ov.mode, metric: E.ov.metric, off: [...E.ov.off] })); } catch (e) { /* ignore */ }
}
const E = { ov: { range: OV_SAVED.range || "2d", mode: OV_SAVED.mode || "max", metric: OV_SAVED.metric || "cpu", off: new Set(OV_SAVED.off || []), data: {}, at: {} }, tab: "overview", role: "viewer", data: null, find: "", rows: [], hist: null };

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
const TAB_TITLE = { overview: "Overview · ภาพรวมทุกเครื่อง", "down,error": "Sensors With Status Down / Error", warning: "Sensors With Status Warning",
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

/* ---------- Overview: กราฟรวมทุกเครื่องย้อนหลัง (แบบ PRTG) — ไม่มีค่าเฉลี่ย: ใช้ค่าสูงสุดพร้อมชื่อเครื่อง ---------- */
const OV_SERIES = [
    { key: "alarms", name: "Alarms", unit: "#", color: "#e5484d", axis: "r", step: true, hint: "จำนวนปัญหาที่เปิดอยู่" },
    { key: "cpu", name: "CPU สูงสุด", unit: "%", color: "#1f6feb", axis: "l", hint: "เครื่องที่ CPU สูงที่สุด ณ เวลานั้น" },
    { key: "memory", name: "Memory สูงสุด", unit: "%", color: "#8250df", axis: "l", hint: "เครื่องที่ Memory สูงที่สุด" },
    { key: "disk", name: "Disk สูงสุด", unit: "%", color: "#bf8700", axis: "l", hint: "ไดรฟ์ที่เต็มที่สุด" },
    { key: "traffic", name: "Traffic รวม", unit: "MB/s", color: "#e85aad", axis: "t", hint: "รับ + ส่ง ทุกเครื่อง · เส้นแสดงเป็น % ของค่าสูงสุดในช่วง" },
];
const OV_RANGES = [["2d", "2 days"], ["30d", "30 days"], ["365d", "365 days"]];
const OV_METRICS = [["cpu", "CPU", "%"], ["memory", "Memory", "%"], ["disk", "Disk", "%"], ["traffic", "Traffic", "MB/s"], ["alarms", "Alarms", "#"]];
const OV_PALETTE = ["#1f6feb", "#e5484d", "#2da44e", "#bf8700", "#8250df", "#e85aad", "#0aa5b8", "#fb8500", "#6e7781", "#a2c523", "#d63384", "#3d5afe"];
const ovColor = i => OV_PALETTE[i % OV_PALETTE.length];

async function ovLoad(range) {
    const c = E.ov.data[range];
    if (c && Date.now() - E.ov.at[range] < 60000) return c;
    const d = await getJson(`/api/overview/history?range=${range}`);
    E.ov.data[range] = d; E.ov.at[range] = Date.now();
    return d;
}

function ovFmtTime(t, range) {
    const d = new Date(t * 1000);
    return range === "2d" ? d.toLocaleString("en-GB", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })
        : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: range === "365d" ? "2-digit" : undefined });
}

/* เส้นที่จะวาดตามโหมด: max = ทุกเครื่องค่าสูงสุด · web:<ชื่อ> = เครื่องเดียว · compare = 1 metric หลายเครื่อง */
function ovLines(d) {
    const mode = E.ov.mode;
    if (mode === "compare") {
        const m = E.ov.metric, meta = OV_METRICS.find(x => x[0] === m);
        return Object.keys(d.webs || {}).sort().map((w, i) => ({ key: w, name: w, unit: meta[2], color: ovColor(i),
            axis: m === "alarms" || m === "traffic" ? "r" : "l", step: m === "alarms", pts: (d.webs[w] || {})[m] || [] }));
    }
    const src = mode.startsWith("web:") ? ((d.webs || {})[mode.slice(4)] || {}) : (d.series || {});
    const one = mode.startsWith("web:");
    return OV_SERIES.map(sr => ({ ...sr, name: one ? sr.name.replace(" สูงสุด", "").replace(" รวม", "") : sr.name,
        axis: sr.key === "traffic" ? "t" : sr.axis, pts: src[sr.key] || [] }));
}

function ovChart(d, lines) {
    const W = 1100, H = 320, L = 44, R = 52, T = 14, B = 40, iw = W - L - R, ih = H - T - B;
    const x = t => L + (t - d.start) / Math.max(1, d.end - d.start) * iw;
    const vis = lines.filter(l => !E.ov.off.has(l.key));
    const rvals = vis.filter(l => l.axis === "r").flatMap(l => l.pts.map(p => p[1]));
    const rUnit = (vis.find(l => l.axis === "r") || {}).unit || "#";
    let rmax = Math.max(rUnit === "#" ? 4 : 0.001, ...rvals);
    if (rUnit === "#") rmax = Math.ceil(rmax);
    const tmax = {};
    vis.filter(l => l.axis === "t").forEach(l => { tmax[l.key] = Math.max(0.001, ...l.pts.map(p => p[1])); });
    const y = (v, l) => T + ih - (l.axis === "r" ? v / rmax : l.axis === "t" ? v / tmax[l.key] : Math.min(v, 100) / 100) * ih;
    const hasL = vis.some(l => l.axis === "l" || l.axis === "t"), hasR = vis.some(l => l.axis === "r");
    let g = "";
    for (let i = 0; i <= 4; i++) {
        const yy = T + ih * i / 4, rv = rmax * (4 - i) / 4;
        g += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" class="ovgrid"/>`
           + (hasL ? `<text x="${L - 6}" y="${yy + 4}" class="ovax" text-anchor="end">${100 - i * 25}</text>` : "")
           + (hasR ? `<text x="${W - R + 6}" y="${yy + 4}" class="ovax">${rUnit === "#" ? Math.round(rv) : rv < 10 ? rv.toFixed(2) : Math.round(rv)}</text>` : "");
    }
    for (let i = 0; i <= 8; i++) {
        const t = d.start + (d.end - d.start) * i / 8;
        g += `<line x1="${x(t)}" x2="${x(t)}" y1="${T}" y2="${T + ih}" class="ovgrid v"/><text x="${x(t)}" y="${H - 18}" class="ovax" text-anchor="middle">${esc(ovFmtTime(t, d.range))}</text>`;
    }
    if (hasL) g += `<text x="${L - 6}" y="${T - 2}" class="ovax" text-anchor="end">%</text>`;
    if (hasR) g += `<text x="${W - R + 6}" y="${T - 2}" class="ovax">${esc(rUnit)}</text>`;
    const gap = (d.step || 300) * 2.5;                 /* ไม่มีข้อมูลนานเกินไป = ไม่ลากเส้นต่อ */
    let paths = "";
    vis.forEach(l => {
        let path = "", prev = null;
        l.pts.forEach(p => {
            const px = x(p[0]).toFixed(1), py = y(p[1], l).toFixed(1);
            if (!prev || p[0] - prev[0] > gap) path += `M${px},${py}`;
            else path += l.step ? `H${px}V${py}` : `L${px},${py}`;
            prev = p;
        });
        if (path) paths += `<path d="${path}" fill="none" stroke="${l.color}" stroke-width="${l.key === "alarms" ? 2 : 1.5}" stroke-linejoin="round"/>`;
    });
    return `<svg viewBox="0 0 ${W} ${H}" class="ovchart" id="ovSvg" role="img" aria-label="Overview chart">${g}${paths}<line id="ovCross" class="ovcross" x1="0" x2="0" y1="${T}" y2="${T + ih}" visibility="hidden"/><rect x="${L}" y="${T}" width="${iw}" height="${ih}" fill="transparent" id="ovHit"/></svg>`;
}

function ovNearest(pts, t, step) {
    let best = null, bd = Infinity;
    for (const p of pts || []) { const dd = Math.abs(p[0] - t); if (dd < bd) { bd = dd; best = p; } }
    return best && bd <= step * 1.5 ? best : null;
}

function ovPeaks(lines) {
    return lines.map(l => {
        if (!l.pts.length) return `<div class="ovpk"><i style="background:${l.color}"></i><span>${esc(l.name)}</span><b>-</b></div>`;
        const top = l.pts.reduce((a, b) => b[1] > a[1] ? b : a);
        return `<div class="ovpk"><i style="background:${l.color}"></i><span>${esc(l.name.replace(" สูงสุด", ""))} สูงสุด</span><b>${top[1]} ${l.unit}</b><small>${esc(ovFmtTime(top[0], "2d"))}${top[2] ? " · " + esc(top[2]) : ""}</small></div>`;
    }).join("");
}

async function showOverview() {
    $("evMeta").textContent = "";
    ovSave();
    const r = E.ov.range;
    if (!E.ov.data[r]) $("evBody").innerHTML = `<div class="empty">Loading...</div>`;
    const d = await ovLoad(r);
    const webs = Object.keys(d.webs || {}).sort();
    if (E.ov.mode.startsWith("web:") && !webs.includes(E.ov.mode.slice(4))) E.ov.mode = "max";
    const lines = ovLines(d);
    const sel = `<select id="ovMode" aria-label="แสดง"><option value="max">ทุกเครื่อง · ค่าสูงสุด</option><option value="compare">เปรียบเทียบทุกเครื่อง</option><optgroup label="เครื่องเดียว">${webs.map(w => `<option value="web:${esc(w)}">${esc(w)}</option>`).join("")}</optgroup></select>`;
    const metric = E.ov.mode === "compare" ? `<div class="tabs" id="ovMetric">${OV_METRICS.map(([k, l]) => `<button type="button" data-ovm="${k}" aria-pressed="${k === E.ov.metric}">${l}</button>`).join("")}</div>` : "";
    const head = `<div class="ovbar"><div class="tabs" id="ovRange">${OV_RANGES.map(([k, l]) => `<button type="button" data-ovr="${k}" aria-pressed="${k === r}">${l}</button>`).join("")}</div>${metric}<label class="ovsel">แสดง ${sel}</label></div>`;
    const legend = `<div class="ovlegend">${lines.map(l => `<label${l.hint ? ` title="${esc(l.hint)}"` : ""}><input type="checkbox" data-ovs="${esc(l.key)}"${E.ov.off.has(l.key) ? "" : " checked"}><i style="background:${l.color}"></i>${esc(l.name)} <small>(${l.axis === "t" ? "% of peak" : l.unit})</small></label>`).join("")}<span class="ovall"><a href="#" data-ovall="1">Show all</a> <a href="#" data-ovall="0">Hide all</a></span></div>`;
    const note = E.ov.mode === "max" ? "CPU / Memory / Disk = ค่าสูงสุดในบรรดาเครื่องทั้งหมด (ไม่ใช่ค่าเฉลี่ย) · ชี้กราฟเพื่อดูว่าเป็นเครื่องไหน · เลือก \"เปรียบเทียบทุกเครื่อง\" เพื่อดูเส้นแยกรายเครื่อง"
        : E.ov.mode === "compare" ? "1 เส้น = 1 เครื่อง · ติ๊กเพื่อซ่อน/แสดงเครื่อง · ชี้กราฟเพื่อดูค่าทุกเครื่อง ณ เวลานั้น (เรียงมากไปน้อย)"
        : `เฉพาะเครื่อง ${esc(E.ov.mode.slice(4))}`;
    $("evBody").innerHTML = head + `<div class="ovwrap">${ovChart(d, lines)}<div class="ovtip" id="ovTip" hidden></div></div>` + legend
        + `<div class="ovpeaks">${ovPeaks(lines.filter(l => !E.ov.off.has(l.key)))}</div><div class="evmeta">${d.machines} เครื่อง · ค่าทุก ${d.step >= 3600 ? d.step / 3600 + " ชม." : d.step / 60 + " นาที"} · ${note}</div>`;
    $("ovMode").value = E.ov.mode;
    const svg = $("ovSvg"), tip = $("ovTip"), cross = $("ovCross");
    $("ovHit").addEventListener("mousemove", e => {
        const b = svg.getBoundingClientRect(), vx = (e.clientX - b.left) / b.width * 1100;
        const t = d.start + (vx - 44) / (1100 - 96) * (d.end - d.start);
        cross.setAttribute("x1", vx); cross.setAttribute("x2", vx); cross.setAttribute("visibility", "visible");
        let rows = lines.filter(l => !E.ov.off.has(l.key)).map(l => ({ l, p: ovNearest(l.pts, t, d.step) })).filter(x => x.p);
        if (E.ov.mode === "compare") rows.sort((a, c) => c.p[1] - a.p[1]);
        tip.innerHTML = `<div class="ovtt">${esc(new Date(t * 1000).toLocaleString("en-GB"))}</div>` + (rows.length ? rows.map(({ l, p }) =>
            `<div><i style="background:${l.color}"></i>${esc(l.name)}: <b>${p[1]} ${l.unit}</b>${p[2] ? ` <small>${esc(p[2])}</small>` : ""}</div>`).join("") : `<div class="st-mut">ไม่มีข้อมูล</div>`);
        tip.hidden = false;
        tip.style.left = Math.min(e.clientX - b.left + 14, b.width - 250) + "px"; tip.style.top = "10px";
    });
    $("ovHit").addEventListener("mouseleave", () => { tip.hidden = true; cross.setAttribute("visibility", "hidden"); });
}

async function render() {
    [...$("evTabs").querySelectorAll("button[data-tab]")].forEach(b => b.setAttribute("aria-pressed", b.dataset.tab === E.tab));
    $("evTitle").textContent = TAB_TITLE[E.tab] || "Sensors";
    try {
        $("evHours").hidden = E.tab !== "incidents";
        $("evFind").hidden = E.tab === "overview";
        if (E.tab === "overview") await showOverview();
        else if (E.tab === "recovered") await showIncidents(true);
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
    E.tab = p.get("tab") || p.get("status") || "overview";
    if (E.tab === "error" || E.tab === "down") E.tab = "down,error";
    try {
        const me = await getJson("/api/me");
        E.role = me.role || "viewer";
        $("currentUsername").textContent = me.username || "?";
        $("tabActivity").hidden = E.role !== "admin";
        $("settingsLink").hidden = E.role !== "admin" && E.role !== "operator";
        if (E.tab === "activity" && E.role !== "admin") E.tab = "system";
    } catch (e) { return; }
    $("evTabs").addEventListener("click", e => {
        const b = e.target.closest("button[data-tab]"); if (!b) return;
        E.tab = b.dataset.tab;
        history.replaceState(null, "", `/events?${["overview", "system", "activity", "recovered", "incidents"].includes(E.tab) ? "tab" : "status"}=${E.tab}`);
        render();
    });
    $("evFind").addEventListener("input", e => { E.find = e.target.value.trim().toLowerCase(); render(); });
    $("evHours").addEventListener("change", render);
    $("evBody").addEventListener("change", e => {
        if (e.target.id === "ovMode") { E.ov.mode = e.target.value; E.ov.off = new Set(); render(); return; }
        const c = e.target.closest("[data-ovs]"); if (!c) return;
        c.checked ? E.ov.off.delete(c.dataset.ovs) : E.ov.off.add(c.dataset.ovs); render();
    });
    $("evBody").addEventListener("click", e => {
        const ovr = e.target.closest("[data-ovr]"), all = e.target.closest("[data-ovall]");
        if (ovr) { E.ov.range = ovr.dataset.ovr; render(); return; }
        const ovm = e.target.closest("[data-ovm]");
        if (ovm) { E.ov.metric = ovm.dataset.ovm; render(); return; }
        if (all) { e.preventDefault(); E.ov.off = all.dataset.ovall === "1" ? new Set() : new Set(ovLines(E.ov.data[E.ov.range] || {}).map(x => x.key)); render(); return; }
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
