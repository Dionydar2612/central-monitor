/* =====================================================================
   sensor.js - หน้า sensor แบบ PRTG (ดู sensor.html)
   ไม่มีค่าเฉลี่ยในตาราง (กฎของระบบ) ใช้ ค่าล่าสุด / ต่ำสุด / สูงสุด
   ===================================================================== */
"use strict";
const $ = id => document.getElementById(id);
const N = { id: "", tab: "overview", d: null, hist: {}, timer: null };
const ST = { ok: ["✓", "OK", "ok"], warning: ["!", "Warning", "wr"], error: ["✕", "Error", "cr"], down: ["▼", "Down", "cr"],
             paused: ["II", "Paused", "ps"], unknown: ["?", "Unknown", "na"] };
const KIND = { availability: "Availability (Ping / Agent)", cpu: "CPU Load", memory: "Memory", disk: "Disk Usage", mem_available: "Available RAM",
               disk_latency: "Disk latency", packet_loss: "Packet loss", net_latency: "Ping latency", service: "Windows service", bottleneck: "Bottleneck",
               log_pull: "Log pull", rule: "Custom alert rule" };

function esc(v) { return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
async function getJson(url) {
    const r = await fetch(url, { cache: "no-store", credentials: "same-origin" });
    if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    return r.json();
}
function ts(v) { if (!v) return null; const n = typeof v === "number" ? v * (v < 1e12 ? 1000 : 1) : Date.parse(v); return Number.isFinite(n) ? n : null; }
function fmtTime(v) { const n = ts(v); return n ? new Date(n).toLocaleString("en-GB") : "-"; }
function fmtDur(sec) {
    sec = Math.max(0, Math.round(sec || 0));
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
    return d ? `${d} d ${h} h` : h ? `${h} h ${m} m` : m ? `${m} m ${s} s` : `${s} s`;
}
function ago(v) { const n = ts(v); return n ? fmtDur((Date.now() - n) / 1000) : "-"; }
const fmt = (v, u) => CMGauge.fmt(v, u);

function renderHead() {
    const s = N.d.sensor, [icon, label, cls] = ST[s.status] || ST.unknown;
    document.title = `Central Monitor - ${s.web} · ${s.name}`;
    $("snHead").className = "snhead s-" + cls;
    $("snIco").textContent = icon;
    $("snName").textContent = s.name;
    $("snSt").innerHTML = `${esc(label)}${s.ack ? " · Acknowledged" : ""} <span>· ${esc(s.message || "")}</span>`;
    const path = (s.group ? s.group.split("/") : []);
    $("snCrumb").innerHTML = ["Devices", ...path].map(esc).join(" » ") + ` » <a href="/?web=${encodeURIComponent(s.web)}">${esc(s.web)}</a> » ${esc(s.name)}`;
    const canOp = N.role === "operator" || N.role === "admin";
    const toggle = canOp ? `<button type="button" class="sntog" id="snToggle">${s.disabled ? "▶ เปิด sensor" : "⏸ ปิด sensor"}</button>` : "";
    $("snWho").innerHTML = `<a href="/?web=${encodeURIComponent(s.web)}"><b>${esc(s.web)}</b></a>${s.host ? ` · ${esc(s.host)}` : ""}${s.os ? ` · ${esc(s.os === "windows" ? "Windows" : "Linux")}` : ""}${toggle}`;
    if (canOp) $("snToggle").onclick = toggleSensor;
    const d = N.d;
    const cell = (k, v) => `<div><small>${esc(k)}:</small><b>${v}</b></div>`;
    $("snInfo").innerHTML = `<div class="sngrid">`
        + cell("Last Scan", esc(ago(d.updated_at)))
        + cell("Last Up", d.last_up ? esc(ago(d.last_up)) : "-")
        + cell("Last Down", d.last_down ? esc(ago(d.last_down)) : "never")
        + cell(`Uptime (${d.days} d)`, `${d.uptime_pct.toFixed(4)}%`)
        + cell(`Downtime (${d.days} d)`, `${d.downtime_pct.toFixed(4)}%`)
        + cell("Sensor Type", esc(KIND[s.kind] || s.kind || "-"))
        + cell("Status since", `${esc(fmtTime(s.since))} <span class="st-mut">(${esc(ago(s.since))})</span>`)
        + cell("Interval", `${esc(d.interval)} s`)
        + cell("Limits", s.warning != null || s.error != null ? `warning ${esc(fmt(s.warning, s.unit))} · error ${esc(fmt(s.error, s.unit))}${s.low_is_bad ? " (ต่ำกว่า = แจ้ง)" : ""}` : "-")
        + cell("ID", `<code>${esc(s.id)}</code>`)
        + `</div>`;
    [...$("snTabs").children].forEach(b => b.classList.toggle("on", b.dataset.t === N.tab));
    const noGraph = !d.has_graph;
    [...$("snTabs").children].forEach(b => { if (["live", "2d", "30d", "365d"].includes(b.dataset.t)) b.disabled = noGraph; });
}

/* ---------- กราฟ (แกนซ้าย = ค่าของ sensor, เส้นประ = warning / error) ---------- */
function chart(h) {
    const pts = h.series || [];
    if (!pts.length) return `<div class="empty">ไม่มีข้อมูลกราฟในช่วงนี้</div>`;
    const W = 1000, H = 300, L = 52, R = 16, T = 12, B = 34, iw = W - L - R, ih = H - T - B;
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    const vals = pts.map(p => p[1]);
    let max = h.unit === "%" ? 100 : Math.max(...vals, Number(h.error) || 0, Number(h.warning) || 0) * 1.1 || 1;
    const x = t => L + (t - t0) / Math.max(1, t1 - t0) * iw, y = v => T + ih - Math.min(v, max) / max * ih;
    let g = "";
    for (let i = 0; i <= 4; i++) {
        const yy = T + ih * i / 4;
        g += `<line x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}" class="ovgrid"/><text x="${L - 6}" y="${yy + 4}" class="ovax" text-anchor="end">${esc(fmt(max * (4 - i) / 4, ""))}</text>`;
    }
    for (let i = 0; i <= 8; i++) {
        const t = t0 + (t1 - t0) * i / 8, d = new Date(t * 1000);
        const lab = h.range === "live" || h.range === "2d" ? d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) + (h.range === "2d" ? " " + d.getDate() + "/" + (d.getMonth() + 1) : "")
            : d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" });
        g += `<line x1="${x(t)}" x2="${x(t)}" y1="${T}" y2="${T + ih}" class="ovgrid v"/><text x="${x(t)}" y="${H - 12}" class="ovax" text-anchor="middle">${esc(lab)}</text>`;
    }
    g += `<text x="${L - 6}" y="${T - 1}" class="ovax" text-anchor="end">${esc(h.unit || "")}</text>`;
    [["warning", "#f5b400"], ["error", "#e5484d"]].forEach(([k, c]) => {
        const v = Number(h[k]); if (Number.isFinite(v) && v <= max) g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="${c}" stroke-dasharray="5 4"/><text x="${W - R - 4}" y="${y(v) - 4}" class="ovax" text-anchor="end" fill="${c}">${k} ${esc(fmt(v, h.unit))}</text>`;
    });
    const gap = (t1 - t0) / Math.max(1, pts.length - 1) * 3;
    let path = "", prev = null;
    pts.forEach(p => { path += (!prev || p[0] - prev[0] > gap ? "M" : "L") + x(p[0]).toFixed(1) + "," + y(p[1]).toFixed(1); prev = p; });
    const imin = vals.indexOf(Math.min(...vals)), imax = vals.indexOf(Math.max(...vals));
    const mark = (i, lab) => `<circle cx="${x(pts[i][0])}" cy="${y(pts[i][1])}" r="3.5" fill="#d33"/><text x="${x(pts[i][0])}" y="${y(pts[i][1]) - 7}" class="ovax" text-anchor="middle">${lab}: ${esc(fmt(pts[i][1], h.unit))}</text>`;
    return `<div class="ovwrap"><svg viewBox="0 0 ${W} ${H}" class="ovchart" id="snSvg">${g}<path d="${path}" fill="none" stroke="#1f6feb" stroke-width="1.6"/>${mark(imax, "Max")}${mark(imin, "Min")}<line id="snCross" class="ovcross" y1="${T}" y2="${T + ih}" visibility="hidden"/><rect id="snHit" x="${L}" y="${T}" width="${iw}" height="${ih}" fill="transparent"/></svg><div class="ovtip" id="snTip" hidden></div></div>
<div class="ovlegend"><label><i style="background:#1f6feb"></i>${esc(h.name)} <small>(${esc(h.unit || "-")})</small></label><label><i style="background:#f5b400"></i>Warning</label><label><i style="background:#e5484d"></i>Error</label></div>`;
}
function hookChart(h) {
    const svg = $("snSvg"); if (!svg) return;
    const pts = h.series, t0 = pts[0][0], t1 = pts[pts.length - 1][0];
    $("snHit").addEventListener("mousemove", e => {
        const b = svg.getBoundingClientRect(), vx = (e.clientX - b.left) / b.width * 1000, t = t0 + (vx - 52) / (1000 - 68) * (t1 - t0);
        let best = pts[0]; for (const p of pts) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
        const c = $("snCross"); c.setAttribute("x1", vx); c.setAttribute("x2", vx); c.setAttribute("visibility", "visible");
        const tip = $("snTip"); tip.hidden = false;
        tip.innerHTML = `<div class="ovtt">${esc(new Date(best[0] * 1000).toLocaleString("en-GB"))}</div><div><b>${esc(fmt(best[1], h.unit))}</b></div>`;
        tip.style.left = Math.min(e.clientX - b.left + 14, b.width - 220) + "px"; tip.style.top = "10px";
    });
    $("snHit").addEventListener("mouseleave", () => { $("snTip").hidden = true; $("snCross").setAttribute("visibility", "hidden"); });
}
function dataTable(h) {
    const rows = (h.series || []).slice().reverse().slice(0, 60);
    if (!rows.length) return "";
    return `<section class="card"><div class="tblw"><table class="st-tbl sntbl"><thead><tr><th>Date Time</th><th>${esc(h.name)}</th></tr></thead>
<tbody><tr class="snsum"><td>ต่ำสุด / สูงสุด (${(h.series || []).length} ค่า)</td><td>${esc(fmt(h.min, h.unit))} / ${esc(fmt(h.max, h.unit))}</td></tr>
${rows.map(p => `<tr><td>${esc(new Date(p[0] * 1000).toLocaleString("en-GB"))}</td><td>${esc(fmt(p[1], h.unit))}</td></tr>`).join("")}</tbody></table></div>
<div class="st-mut" style="font-size:12px;margin-top:6px">แสดง ${rows.length} ค่าล่าสุด</div></section>`;
}
async function hist(range, force) {
    const c = N.hist[range];
    if (c && !force && Date.now() - c.at < (range === "live" ? 25000 : 120000)) return c.d;
    const d = await getJson(`/api/sensors/history?id=${encodeURIComponent(N.id)}&range=${range}`);
    N.hist[range] = { at: Date.now(), d };
    return d;
}

async function showOverview() {
    const s = N.d.sensor, d2 = N.d.has_graph ? await hist("2d").catch(() => null) : null;
    const details = s.details && typeof s.details === "object" ? Object.entries(s.details).filter(([, v]) => typeof v === "number" || (typeof v === "string" && v.length < 60)) : [];
    const lim = s.warning != null || s.error != null ? `${esc(fmt(s.warning, s.unit))} / ${esc(fmt(s.error, s.unit))}` : "-";
    $("snBody").innerHTML = `<section class="card snover">
<div class="snbig"><div class="snbt">${esc(s.name)}</div>${CMGauge.svg(s, { size: "lg" })}<div class="snbv">${esc(fmt(s.value, s.unit))}</div>${s.peak != null ? `<div class="st-mut" style="font-size:12px">peak ${esc(fmt(s.peak, s.unit))}</div>` : ""}</div>
<div class="snside"><div class="tblw"><table class="st-tbl sntbl"><thead><tr><th>Channel</th><th>Last Value</th><th>Minimum <small>(2 days)</small></th><th>Maximum <small>(2 days)</small></th><th>Limits (W / E)</th></tr></thead><tbody>
<tr><td><b>${esc(s.name)}</b></td><td>${esc(fmt(s.value, s.unit))}</td><td>${d2 ? esc(fmt(d2.min, s.unit)) : "-"}</td><td>${d2 ? esc(fmt(d2.max, s.unit)) : "-"}</td><td>${lim}</td></tr>
${details.map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(typeof v === "number" ? fmt(v, "") : v)}</td><td>-</td><td>-</td><td>-</td></tr>`).join("")}
</tbody></table></div>
<div class="snmsg"><small>Message</small><div>${esc(s.message || "-")}</div></div>
${s.ack ? `<div class="snmsg"><small>Acknowledged</small><div>${esc(s.ack.by || "-")} · ${esc(fmtTime(s.ack.at))}${s.ack.note ? " · “" + esc(s.ack.note) + "”" : ""}</div></div>` : ""}
</div></section>
${d2 ? `<section class="card"><div class="top"><h2>2 days</h2></div>${chart(d2)}</section>` : ""}`;
    if (d2) hookChart(d2);
}
async function showGraph(range) {
    if (!N.hist[range]) $("snBody").innerHTML = `<div class="empty">Loading...</div>`;
    const h = await hist(range);
    const title = { live: "Live Data (2 ชั่วโมงล่าสุด · รีเฟรชทุก 30 วินาที)", "2d": "2 days", "30d": "30 days", "365d": "365 days" }[range];
    $("snBody").innerHTML = `<section class="card"><div class="top"><h2>${esc(title)}</h2><span class="st-mut" style="font-size:12px">ต่ำสุด ${esc(fmt(h.min, h.unit))} · สูงสุด ${esc(fmt(h.max, h.unit))}</span></div>${chart(h)}</section>${dataTable(h)}`;
    hookChart(h);
}
function showLog() {
    const rows = N.d.incidents || [];
    $("snBody").innerHTML = `<section class="card"><div class="top"><h2>Log · ประวัติปัญหาของ sensor นี้</h2></div>${rows.length ? `<div class="tblw"><table class="st-tbl sntbl"><thead><tr><th>เริ่ม</th><th>จบ</th><th>นาน</th><th>ระดับแย่สุด</th><th>ค่าสูงสุด</th><th>สถานะ</th></tr></thead><tbody>
${rows.map(i => { const [, l, c] = ST[i.worst] || ST.unknown; return `<tr><td>${esc(fmtTime(i.start))}</td><td>${i.end ? esc(fmtTime(i.end)) : "<b>ยังไม่จบ</b>"}</td><td>${esc(fmtDur(i.duration))}</td><td><span class="evst ${c}">${esc(l)}</span></td><td>${esc(fmt(i.peak, i.unit))}</td><td>${esc(i.state === "ongoing" ? "ongoing" : i.ended_by === "paused" ? "ended (paused)" : "recovered")}</td></tr>`; }).join("")}
</tbody></table></div>` : `<div class="empty">ไม่มีปัญหาที่บันทึกไว้</div>`}</section>`;
}

/* ปิด / เปิด sensor นี้ (เช่น ping ไปไม่ถึงเครื่อง) -> targets[].alert.disabled_sensors  มีผลรอบตรวจถัดไป */
async function toggleSensor() {
    const s = N.d.sensor, sid = String(s.id).split(":").slice(1).join(":"), off = !s.disabled;
    if (off && !confirm(`ปิด sensor "${s.name}" ของ ${s.web}?\nจะแสดงเป็น Paused ไม่แจ้งเตือน และไม่นับเป็นปัญหา จนกว่าจะกดเปิด`)) return;
    const b = $("snToggle"); b.disabled = true;
    try {
        const r = await fetch(`/api/targets/${encodeURIComponent(s.web)}/sensor-toggle`, { method: "POST", credentials: "same-origin",
            headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sensor: sid, disabled: off }) });
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.error || Object.values(j.fields || {}).join(" · ") || `HTTP ${r.status}`);
        b.textContent = off ? "ปิดแล้ว · มีผลใน ~30 วินาที" : "เปิดแล้ว · มีผลใน ~30 วินาที";
        setTimeout(render, 35000);
    } catch (e) { alert("ทำไม่สำเร็จ: " + e.message); b.disabled = false; }
}

async function render() {
    try {
        N.d = await getJson(`/api/sensors/detail?id=${encodeURIComponent(N.id)}`);
        if (!N.d.has_graph && ["live", "2d", "30d", "365d"].includes(N.tab)) N.tab = "overview";
        renderHead();
        if (N.tab === "overview") await showOverview();
        else if (N.tab === "log") showLog();
        else await showGraph(N.tab);
    } catch (e) {
        if (e.message !== "auth") $("snBody").innerHTML = `<div class="empty">โหลดไม่สำเร็จ: ${esc(e.message)} · <a href="/events?status=all">ดู sensor ทั้งหมด</a></div>`;
    }
}

document.addEventListener("DOMContentLoaded", async () => {
    const p = new URLSearchParams(location.search);
    N.id = p.get("id") || ""; N.tab = p.get("tab") || "overview";
    try { const me = await getJson("/api/me"); $("currentUsername").textContent = me.username || "?"; N.role = me.role || "viewer"; } catch (e) { return; }
    $("snTabs").addEventListener("click", e => {
        const b = e.target.closest("button[data-t]"); if (!b || b.disabled) return;
        N.tab = b.dataset.t;
        const u = new URL(location.href); u.searchParams.set("tab", N.tab); history.replaceState(null, "", u.pathname + u.search);
        render();
    });
    $("logoutButton").addEventListener("click", async () => { try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } finally { location.href = "/login"; } });
    render();
    setInterval(() => { if (!document.hidden) { if (N.tab === "live") N.hist.live = null; render(); } }, 30000);
});
