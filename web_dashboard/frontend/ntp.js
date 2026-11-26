/* =====================================================================
   ntp.js - หน้า NTP Monitor (ntp.html)
     NTP servers : ตารางสถานะ + คลิกแถว = กราฟ offset (มีเครื่องหมาย) / RTT ย้อนหลัง · admin เพิ่ม / แก้ / หยุด / ลบ / เกณฑ์
     เครื่อง     : กราฟเวลาคลาดของทุกเครื่อง Linux เทียบกัน + ตาราง (จาก sensor Time sync)
   ===================================================================== */
"use strict";
const $ = id => document.getElementById(id);
const N = { role: "viewer", data: null, sel: null, srange: "24h", mrange: "24h", hi: null, mhist: null, editing: null };
const RANGES = ["1h", "6h", "24h", "3d", "7d", "30d"];
const COLORS = ["#1f7bff", "#10c27a", "#f5a524", "#a855f7", "#e5484d", "#0ea5e9", "#64748b", "#d946ef", "#84cc16", "#f97316"];
const ST = { ok: ["OK", "ok"], warning: ["Warning", "wr"], error: ["Error", "cr"], down: ["Down", "cr"], paused: ["Paused", "ps"],
             pending: ["รอผล", ""], unknown: ["Unknown", "wr"] };

function esc(v) { return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }
async function api(method, url, body) {
    const r = await fetch(url, { method, cache: "no-store", credentials: "same-origin",
        headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(d.error || `HTTP ${r.status}`); e.fields = d.fields; throw e; }
    return d;
}
function ago(ts) {
    if (!ts) return "-";
    const s = Math.max(0, Date.now() / 1000 - ts);
    return s < 60 ? `${Math.round(s)} วิที่แล้ว` : s < 3600 ? `${Math.round(s / 60)} นาทีที่แล้ว` : `${(s / 3600).toFixed(1)} ชม.ที่แล้ว`;
}
const ms = (v, sign) => v == null ? "-" : `${sign && v > 0 ? "+" : ""}${Math.abs(v) >= 100 ? Math.round(v) : Number(v).toFixed(1)} ms`;
const badge = st => { const [t, c] = ST[st] || [st || "-", ""]; return `<span class="ovst ${c}">${esc(t)}</span>`; };
function offCls(v) {
    const s = (N.data || {}).settings || {};
    if (v == null) return "";
    v = Math.abs(v);
    return v >= s.offset_error_ms ? "cr" : v >= s.offset_warning_ms ? "wr" : "ok";
}

/* ---------- กราฟเส้น (SVG) : lines = [{name, color, pts:[[t,v]]}] ---------- */
function fmtT(t, range) {
    const d = new Date(t * 1000);
    return ["3d", "7d", "30d"].includes(range) ? d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) + (range === "3d" ? " " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "")
        : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
function chart(lines, o) {
    const pts = lines.flatMap(l => l.pts);
    if (!pts.length) return `<div class="empty">ยังไม่มีข้อมูลกราฟ (เริ่มเก็บหลังอัปเดตระบบ)</div>`;
    const W = 900, H = o.h || 230, L = 58, R = 10, T = 12, B = 26;
    const t0 = o.start, t1 = Math.max(o.end, t0 + 1);
    let lo = Math.min(0, ...pts.map(p => p[1])), hi = Math.max(0, ...pts.map(p => p[1]));
    (o.bands || []).forEach(b => { if (Math.abs(b) <= Math.max(Math.abs(lo), Math.abs(hi)) * 3) { hi = Math.max(hi, b); lo = Math.min(lo, o.signed ? -b : 0); } });
    if (o.signed) { const m = Math.max(Math.abs(lo), Math.abs(hi), 1) * 1.15; lo = -m; hi = m; } else { hi = Math.max(hi * 1.15, 1); lo = 0; }
    const X = t => L + (t - t0) / (t1 - t0) * (W - L - R), Y = v => T + (hi - v) / (hi - lo) * (H - T - B);
    const gap = Math.max((o.step || 60) * 3, 90);
    const path = l => { let d = "", prev = null;
        l.pts.forEach(p => { d += `${prev == null || p[0] - prev > gap ? "M" : "L"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`; prev = p[0]; }); return d; };
    const grid = [hi, (hi + lo) / 2, lo].concat(o.signed ? [0] : []).map(v => `<line class="gl" x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"${v === 0 ? ' style="stroke:var(--mut);opacity:.5"' : ""}/><text x="${L - 6}" y="${(Y(v) + 4).toFixed(1)}" text-anchor="end">${Math.abs(v) >= 100 ? Math.round(v) : v.toFixed(1)}</text>`).join("");
    const bands = (o.bands || []).flatMap((b, i) => (o.signed ? [b, -b] : [b]).filter(v => v <= hi && v >= lo)
        .map(v => `<line x1="${L}" x2="${W - R}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}" stroke="${i ? "#e5484d" : "#d98a00"}" stroke-dasharray="6 5" stroke-width="1.2"/>`)).join("");
    const down = (o.down || []).map(([a, b]) => `<rect x="${X(a).toFixed(1)}" y="${T}" width="${Math.max(2, X(b) - X(a)).toFixed(1)}" height="${H - T - B}" fill="rgba(229,72,77,.14)"><title>ไม่ตอบ ${fmtT(a, "1h")}–${fmtT(b, "1h")}</title></rect>`).join("");
    const ls = lines.map(l => `<path d="${path(l)}" fill="none" stroke="${l.color}" stroke-width="${N.hi && N.hi === l.name ? 3 : 1.8}" opacity="${N.hi && N.hi !== l.name ? .2 : 1}" vector-effect="non-scaling-stroke"><title>${esc(l.name)}</title></path>`).join("");
    const ticks = [0, .25, .5, .75, 1].map(f => { const t = t0 + (t1 - t0) * f;
        return `<text x="${X(t).toFixed(1)}" y="${H - 7}" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${fmtT(t, o.range)}</text>`; }).join("");
    return `<svg class="ntSvg" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(o.label || "")}">${grid}${down}${bands}${ls}${ticks}</svg>`;
}
function rangeTabs(box, cur, fn) {
    box.innerHTML = RANGES.map(r => `<button type="button" data-r="${r}" aria-pressed="${r === cur}">${r}</button>`).join("");
    box.onclick = e => { const b = e.target.closest("[data-r]"); if (b) fn(b.dataset.r); };
}

/* ---------- ภาพรวม + ตาราง ---------- */
function render() {
    const d = N.data, admin = N.role === "admin";
    const srv = d.servers, mach = d.machines;
    const badS = srv.filter(s => ["down", "error", "warning"].includes(s.status)), badM = mach.filter(m => ["down", "error", "warning"].includes(m.status));
    const hero = $("ntHero");
    hero.className = "card apHero " + (badS.some(s => s.status !== "warning") || badM.some(m => m.status !== "warning") ? "bad" : badS.length || badM.length ? "wr" : srv.length || mach.length ? "ok" : "");
    $("ntHeadline").textContent = !srv.length && !mach.length ? "ยังไม่มี NTP server หรือเครื่องที่ตรวจเวลา"
        : badS.length + badM.length ? `มีปัญหา ${badS.length + badM.length} รายการ` : "เวลาตรงกันทุกที่";
    const maxOff = mach.filter(m => m.offset_ms != null).reduce((a, m) => Math.abs(m.offset_ms) > Math.abs(a.offset_ms ?? 0) ? m : a, {});
    $("ntSub").textContent = [`NTP server ${srv.length} (ปัญหา ${badS.length})`, `เครื่อง ${mach.length} (ปัญหา ${badM.length})`,
        maxOff.web ? `คลาดมากสุด ${ms(maxOff.offset_ms)} (${maxOff.web})` : "",
        `เกณฑ์ ${d.settings.offset_warning_ms} / ${d.settings.offset_error_ms} ms`].filter(Boolean).join(" · ");
    $("ntSrvCnt").textContent = srv.length ? `${srv.length}` : "";
    $("ntAddBtn").hidden = $("ntSetBtn").hidden = !admin;
    $("ntActH").textContent = admin ? "" : "";
    $("ntSrv").innerHTML = srv.length ? srv.map(s => {
        const pend = s.pending && s.pending[0] !== s.status ? ` <small title="กำลังยืนยัน">→ ${esc(s.pending[0])} ${s.pending[1]}</small>` : "";
        const act = admin ? `<td class="ntAct"><button type="button" class="svbtn sm" data-edit="${esc(s.name)}">แก้ไข</button><button type="button" class="svbtn sm" data-pause="${esc(s.name)}">${s.paused ? "Resume" : "Pause"}</button><button type="button" class="svbtn sm" data-del="${esc(s.name)}">ลบ</button></td>` : "<td></td>";
        return `<tr data-srv="${esc(s.name)}" class="${N.sel === s.name ? "on" : ""}" title="${esc(s.message)}"><td>${badge(s.status)}${pend}</td><td><b>${esc(s.name)}</b>${s.group ? ` <small>${esc(s.group)}</small>` : ""}</td>`
            + `<td><code>${esc(s.host)}${s.port && s.port !== 123 ? ":" + esc(s.port) : ""}</code></td><td class="n ${offCls(s.offset_ms)}">${ms(s.offset_ms, true)}</td>`
            + `<td class="n ${s.rtt_ms >= d.settings.rtt_warning_ms ? "wr" : ""}">${ms(s.rtt_ms)}</td><td class="n ${s.stratum >= 16 ? "cr" : ""}">${s.stratum ?? "-"}</td>`
            + `<td><small>${esc(s.refid || "-")}</small></td><td><small>${esc(ago(s.checked_at))}</small></td>${act}</tr>`;
    }).join("") : `<tr><td colspan="9" class="empty">${admin ? "ยังไม่มี NTP server · กด + เพิ่ม NTP server" : "ยังไม่มี NTP server (admin เพิ่มได้)"}</td></tr>`;
    $("ntMCnt").textContent = mach.length ? `${mach.length} เครื่อง Linux` : "";
    $("ntMach").innerHTML = mach.length ? mach.map(m => `<tr data-m="${esc(m.web)}" class="${N.hi === m.web ? "on" : ""}"><td>${badge(m.status)}</td><td><a href="/?web=${encodeURIComponent(m.web)}"><b>${esc(m.web)}</b></a></td>`
        + `<td>${m.synced === true ? '<b class="ok">sync</b>' : m.synced === false ? '<b class="cr">ไม่ sync</b>' : "-"}</td><td class="n ${offCls(m.offset_ms)}">${ms(m.offset_ms)}</td><td><small>${esc(m.message || "")}</small></td></tr>`).join("")
        : `<tr><td colspan="5" class="empty">ยังไม่มีข้อมูล (เครื่อง Linux ที่ node_exporter ส่งค่า timex จะขึ้นที่นี่)</td></tr>`;
}

async function load() {
    try { N.data = await api("GET", "/api/ntp"); render(); }
    catch (e) { if (e.message !== "auth") $("ntHeadline").textContent = "โหลดไม่ได้: " + e.message; }
}

/* ---------- กราฟของ server ---------- */
async function loadServer() {
    if (!N.sel) { $("ntDetail").hidden = true; return; }
    $("ntDetail").hidden = false;
    $("ndTitle").textContent = N.sel;
    rangeTabs($("ndRange"), N.srange, r => { N.srange = r; loadServer(); });
    let h;
    try { h = await api("GET", `/api/ntp/history?server=${encodeURIComponent(N.sel)}&range=${N.srange}`); }
    catch (e) { $("ndOffset").innerHTML = `<div class="empty">โหลดไม่ได้: ${esc(e.message)}</div>`; return; }
    const s = (N.data.servers || []).find(x => x.name === N.sel) || {}, set = N.data.settings, se = h.series;
    const offs = se.offset.map(p => p[1]), rtts = se.rtt.map(p => p[1]);
    const stat = (k, v, c) => `<div><small>${k}</small><b class="${c || ""}">${v}</b></div>`;
    $("ndStats").innerHTML = stat("สถานะ", badge(s.status)) + stat(`Uptime (${h.range})`, h.uptime_pct == null ? "-" : h.uptime_pct + "%", h.uptime_pct >= 99.9 ? "ok" : h.uptime_pct >= 99 ? "wr" : h.uptime_pct == null ? "" : "cr")
        + stat("Offset ตอนนี้", ms(s.offset_ms, true), offCls(s.offset_ms)) + stat(`Offset ต่ำ / สูงสุด`, offs.length ? `${ms(Math.min(...offs), true)} / ${ms(Math.max(...offs), true)}` : "-")
        + stat(`RTT เฉลี่ย / สูงสุด`, rtts.length ? `${ms(rtts.reduce((a, b) => a + b, 0) / rtts.length)} / ${ms(Math.max(...rtts))}` : "-")
        + stat("Stratum / Ref", `${s.stratum ?? "-"} · ${esc(s.refid || "-")}`) + stat("Root delay / dispersion", s.root_delay_ms == null ? "-" : `${ms(s.root_delay_ms)} / ${ms(s.root_dispersion_ms)}`);
    const down = []; let a = null;
    se.up.forEach(([t, v], i) => { if (v < 1 && a == null) a = t; if ((v >= 1 || i === se.up.length - 1) && a != null) { down.push([a, t + (v < 1 ? h.step : 0)]); a = null; } });
    const o = { start: h.start, end: h.end, step: h.step, range: h.range, down };
    $("ndOffset").innerHTML = chart([{ name: "offset", color: "#1f7bff", pts: se.offset }], { ...o, signed: true, bands: [set.offset_warning_ms, set.offset_error_ms], label: "offset ms" });
    $("ndRtt").innerHTML = chart([{ name: "rtt", color: "#10c27a", pts: se.rtt }], { ...o, h: 170, bands: [set.rtt_warning_ms], label: "RTT ms" });
}

/* ---------- กราฟเครื่อง ---------- */
async function loadMachines() {
    rangeTabs($("nmRange"), N.mrange, r => { N.mrange = r; loadMachines(); });
    try { N.mhist = await api("GET", `/api/ntp/history?range=${N.mrange}`); } catch (e) { $("nmChart").innerHTML = `<div class="empty">โหลดไม่ได้: ${esc(e.message)}</div>`; return; }
    drawMachines();
}
function drawMachines() {
    const h = N.mhist; if (!h) return;
    const names = Object.keys(h.series).sort();
    const lines = names.map((n, i) => ({ name: n, color: COLORS[i % COLORS.length], pts: h.series[n] }));
    const set = (N.data || {}).settings || {};
    $("nmChart").innerHTML = chart(lines, { start: h.start, end: h.end, step: h.step, range: h.range, signed: true, bands: [set.offset_warning_ms, set.offset_error_ms], label: "machine clock offset ms" });
    $("nmLeg").innerHTML = lines.map(l => `<button type="button" data-hi="${esc(l.name)}" aria-pressed="${N.hi === l.name}"><i style="background:${l.color}"></i>${esc(l.name)}</button>`).join("");
}

/* ---------- ฟอร์ม (admin) ---------- */
function openForm(s) {
    N.editing = s ? s.name : null;
    $("ntSet").hidden = true; $("ntForm").hidden = false; $("nfMsg").textContent = "";
    $("nfName").value = s ? s.name : ""; $("nfHost").value = s ? s.host : ""; $("nfPort").value = s && s.port !== 123 ? s.port : "";
    $("nfGroup").value = s ? s.group : ""; $("nfNotify").checked = s ? s.notify : true;
    $("nfName").focus();
}
function fieldErr(box, e) { $(box).textContent = e.fields ? Object.entries(e.fields).map(([k, v]) => `${k}: ${v}`).join(" · ") : e.message; $(box).className = "ntMsg bad"; }
function bind() {
    $("ntAddBtn").onclick = () => openForm(null);
    $("nfCancel").onclick = () => { $("ntForm").hidden = true; };
    $("ntForm").onsubmit = async ev => {
        ev.preventDefault();
        const body = { name: $("nfName").value.trim(), host: $("nfHost").value.trim(), port: $("nfPort").value.trim() || 123,
            group: $("nfGroup").value.trim(), notify: $("nfNotify").checked };
        try {
            N.data = N.editing ? await api("PUT", `/api/ntp/servers/${encodeURIComponent(N.editing)}`, body) : await api("POST", "/api/ntp/servers", body);
            $("ntForm").hidden = true; render();
        } catch (e) { fieldErr("nfMsg", e); }
    };
    $("ntSetBtn").onclick = () => {
        const s = N.data.settings; $("ntForm").hidden = true; $("ntSet").hidden = false; $("nsMsg").textContent = "";
        $("nsWarn").value = s.offset_warning_ms; $("nsErr").value = s.offset_error_ms; $("nsRtt").value = s.rtt_warning_ms; $("nsInt").value = s.interval;
    };
    $("nsCancel").onclick = () => { $("ntSet").hidden = true; };
    $("ntSet").onsubmit = async ev => {
        ev.preventDefault();
        try {
            N.data = await api("PUT", "/api/ntp/settings", { offset_warning_ms: $("nsWarn").value, offset_error_ms: $("nsErr").value,
                rtt_warning_ms: $("nsRtt").value, interval: $("nsInt").value });
            $("ntSet").hidden = true; render(); loadServer(); drawMachines();
        } catch (e) { fieldErr("nsMsg", e); }
    };
    $("ntSrv").onclick = async e => {
        const b = e.target.closest("button");
        if (b) {
            const name = b.dataset.edit || b.dataset.pause || b.dataset.del, s = N.data.servers.find(x => x.name === name);
            try {
                if (b.dataset.edit) return openForm(s);
                if (b.dataset.pause) N.data = await api("PUT", `/api/ntp/servers/${encodeURIComponent(name)}`, { paused: !s.paused });
                if (b.dataset.del) { if (!confirm(`ลบ NTP server ${name}?`)) return; N.data = await api("DELETE", `/api/ntp/servers/${encodeURIComponent(name)}`); if (N.sel === name) N.sel = null; }
                render(); loadServer();
            } catch (err) { alert(err.message); }
            return;
        }
        const tr = e.target.closest("tr[data-srv]"); if (!tr) return;
        N.sel = N.sel === tr.dataset.srv ? null : tr.dataset.srv; render(); loadServer();
    };
    const hi = n => { N.hi = N.hi === n ? null : n; drawMachines(); render(); };
    $("nmLeg").onclick = e => { const b = e.target.closest("[data-hi]"); if (b) hi(b.dataset.hi); };
    $("ntMach").onclick = e => { if (e.target.closest("a")) return; const tr = e.target.closest("tr[data-m]"); if (tr) hi(tr.dataset.m); };
}

(async () => {
    bind();
    try {
        const me = await api("GET", "/api/me");
        N.role = me.role || "viewer";
        $("currentUsername").textContent = me.username || "?";
        $("settingsLink").hidden = N.role !== "admin" && N.role !== "operator";
    } catch (e) { /* ignore */ }
    $("logoutButton").onclick = async () => { await fetch("/api/logout", { method: "POST" }); location.href = "/login"; };
    const qs = new URLSearchParams(location.search);
    if (qs.get("server")) N.sel = qs.get("server");
    await load();
    loadServer(); loadMachines();
    setInterval(load, 30000);
    setInterval(() => { loadMachines(); if (N.sel) loadServer(); }, 120000);
})();
