/* =====================================================================
   apps.js - หน้า Apps & Web (apps.html)
     ภาพรวมบนสุด: ทุกอย่างทำงานปกติ / มีปัญหา N รายการ
     การ์ดละ 1 รายการ: จุดสี · ชื่อ · เครื่อง · ข้อความล่าสุด · ทำงาน / หยุด มานานเท่าไหร่  (มีปัญหาขึ้นก่อน)
     admin: + เพิ่ม (โปรแกรม / เว็บ / พอร์ต / Service) และ × ลบ   -> สร้าง / ลบ Custom alert rule ให้
   ===================================================================== */
"use strict";
const $ = id => document.getElementById(id);
const A = { role: "viewer", items: [], filter: "all", find: "", type: "program", local: true, targets: [], at: 0, editing: null };
const TYPE_TH = { program: "โปรแกรม", service: "Service", web: "เว็บ", port: "พอร์ต", docker: "Docker" };
const LOCAL_METRICS = ["http_local", "port_listen"];
const BAD = ["error", "down"], WARN = ["warning"];

function esc(v) {
    return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
function ago(sec) {
    if (!sec) return "";
    let s = Math.max(0, Date.now() / 1000 - sec);
    const d = Math.floor(s / 86400); s -= d * 86400;
    const h = Math.floor(s / 3600); s -= h * 3600;
    const m = Math.floor(s / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : `${Math.max(m, 1)}m`;
}
async function api(method, url, body) {
    const r = await fetch(url, { method, cache: "no-store", credentials: "same-origin",
        headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(d.error || `HTTP ${r.status}`); e.fields = d.fields; throw e; }
    return d;
}

function cls(st) { return BAD.includes(st) ? "bad" : WARN.includes(st) ? "wr" : st === "ok" ? "ok" : ""; }
function ageText(x) {
    if (x.status === "ok") return x.since ? `ทำงาน ${ago(x.since)}` : "ทำงาน";
    if (WARN.includes(x.status)) return x.since ? `เตือน ${ago(x.since)}` : "เตือน";
    if (BAD.includes(x.status)) return x.since ? `หยุด ${ago(x.since)}` : "หยุด";
    if (x.status === "paused") return "ปิดอยู่";
    return "รอข้อมูล";
}

function tile(x) {
    const del = A.role === "admin" ? `<button type="button" class="apEdit" data-edit="${esc(x.rule)}" title="แก้ไข">✎ แก้ไข</button>`
        + `<button type="button" class="apDel" data-del="${esc(x.rule)}" title="เลิกเฝ้าดู">× ลบ</button>` : "";
    const href = x.web && x.web !== "-" && !x.web.includes(",") ? `/?web=${encodeURIComponent(x.web)}` : "#";
    const isWeb = !!x.id;                       // ทุกประเภทที่มีสถานะแล้ว: กราฟเล็ก + uptime + คลิกดูรายละเอียด
    const up = x.uptime_24h, upc = up == null ? "" : up >= 99.9 ? "ok" : up >= 99 ? "wr" : "cr";
    const stat = isWeb ? `<div class="apStat">${spark(x.spark) || strip(x)}<span>uptime <b class="${upc}">${up == null ? "-" : up.toFixed(up >= 99.9 ? 2 : 1) + "%"}</b> 24h</span>`
        + `${x.code != null ? `<span>HTTP <b class="${x.code && x.code < 400 ? "ok" : "cr"}">${esc(x.code)}</b></span>` : ""}</div>` : "";
    const attrs = isWeb ? `href="/apps?open=${encodeURIComponent(x.rule)}&web=${encodeURIComponent(x.web)}" data-open="${esc(x.rule)}" data-oweb="${esc(x.web)}"` : `href="${href}"`;
    return `<a class="apT ${cls(x.status)}" ${attrs}><span class="apDot"></span><div class="apMain"><b title="${esc(x.what)}">${esc(x.name)}</b>`
        + `<small>${esc(x.web)} · ${esc(TYPE_TH[x.type] || x.type)}${["http_local", "port_listen"].includes(x.metric) ? " · บนเครื่อง" : ["http_url", "tcp_port"].includes(x.metric) ? " · จาก central" : ""}</small><p>${esc(x.message)}</p>${stat}</div>`
        + `<span class="apAge">${esc(ageText(x))}</span>${del}</a>`;
}

function strip(x) {
    /* ไม่มีกราฟเวลาตอบ (โปรแกรม / Service / Docker): แถบสถานะปัจจุบัน */
    const c = cls(x.status) || "na";
    return `<span class="apStrip1 ${c}" aria-hidden="true"></span>`;
}
function spark(pts) {
    if (!pts || pts.length < 2) return "";
    const W = 90, H = 22, mx = Math.max(...pts, 1);
    const d = pts.map((v, i) => `${i ? "L" : "M"}${(i * W / (pts.length - 1)).toFixed(1)},${(H - 1 - v / mx * (H - 4)).toFixed(1)}`).join("");
    return `<svg class="apSpark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><path d="${d}"/></svg>`;
}

/* ---------- หน้ารายละเอียดของ Monitor เว็บ ---------- */
const D = { rule: null, web: null, range: "24h", data: null };
function fmtT(t, range) {
    const d = new Date(t * 1000);
    return ["7d", "30d"].includes(range) ? d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" })
        : d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
function dur(sec) {
    sec = Math.max(0, Math.round(sec));
    const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m` : `${sec}s`;
}
function chartSvg(series, range) {
    const ms = series.ms || [], up = series.up || [];
    if (!ms.length && !up.length) return `<div class="apEmpty">ยังไม่มีข้อมูลกราฟ (เริ่มเก็บหลังอัปเดตระบบ · ทุก 30 วินาที)</div>`;
    if (!ms.length) {                           // โปรแกรม / Service / Docker / พอร์ตบนเครื่อง: แถบทำงาน / ไม่ทำงาน ตามเวลา
        const t0 = up[0][0], t1 = Math.max(up[up.length - 1][0], t0 + 1), W = 900, H = 70, L = 8, R = 8;
        const X = t => L + (t - t0) / (t1 - t0) * (W - L - R), step = up.length > 1 ? up[1][0] - up[0][0] : 60;
        const bars = up.map(p => `<rect class="${p[1] >= 1 ? "upb" : "dn2"}" x="${X(p[0] - step / 2).toFixed(1)}" y="8" width="${Math.max(1.5, X(p[0] + step / 2) - X(p[0] - step / 2)).toFixed(1)}" height="34"><title>${p[1] >= 1 ? "ทำงาน" : "ไม่ทำงาน"} ${fmtT(p[0], "1h")}</title></rect>`).join("");
        const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => { const t = t0 + (t1 - t0) * f;
            return `<text x="${X(t).toFixed(1)}" y="${H - 6}" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${fmtT(t, range)}</text>`; }).join("");
        return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="ช่วงที่ทำงาน (เขียว) และไม่ทำงาน (แดง)">${bars}${ticks}</svg>`;
    }
    const all = ms.concat(up);
    const t0 = Math.min(...all.map(p => p[0])), t1 = Math.max(...all.map(p => p[0]), t0 + 1);
    const W = 900, H = 220, L = 46, R = 8, T = 10, B = 24, mx = Math.max(10, ...ms.map(p => p[1])) * 1.1;
    const X = t => L + (t - t0) / (t1 - t0) * (W - L - R), Y = v => T + (1 - v / mx) * (H - T - B);
    const step = up.length > 1 ? up[1][0] - up[0][0] : 60;
    const down = up.filter(p => p[1] < 1).map(p => `<rect class="dn" x="${X(p[0] - step / 2).toFixed(1)}" y="${T}" width="${Math.max(2, X(p[0] + step / 2) - X(p[0] - step / 2)).toFixed(1)}" height="${H - T - B}"><title>ไม่ทำงาน ${fmtT(p[0], "1h")}</title></rect>`).join("");
    const line = ms.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    const area = ms.length ? `${line}L${X(ms[ms.length - 1][0]).toFixed(1)},${H - B}L${X(ms[0][0]).toFixed(1)},${H - B}Z` : "";
    const grid = [0, 0.5, 1].map(f => { const v = mx * f, y = Y(v);
        return `<line class="gl" x1="${L}" x2="${W - R}" y1="${y.toFixed(1)}" y2="${y.toFixed(1)}"/><text x="${L - 6}" y="${(y + 4).toFixed(1)}" text-anchor="end">${Math.round(v)}</text>`; }).join("");
    const ticks = [0, 0.25, 0.5, 0.75, 1].map(f => { const t = t0 + (t1 - t0) * f;
        return `<text x="${X(t).toFixed(1)}" y="${H - 6}" text-anchor="${f === 0 ? "start" : f === 1 ? "end" : "middle"}">${fmtT(t, range)}</text>`; }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="เวลาตอบ (ms) และช่วงที่ไม่ทำงาน">${grid}${down}`
        + (area ? `<path class="ar" d="${area}"/><path class="ln" d="${line}"/>` : "") + `${ticks}<text x="${L + 4}" y="${T + 10}">ms</text></svg>`;
}
async function loadDetail() {
    try {
        D.data = await api("GET", `/api/apps/${encodeURIComponent(D.rule)}/detail?web=${encodeURIComponent(D.web || "")}&range=${D.range}`);
    } catch (e) { if (e.message !== "auth") $("adMsg").textContent = "โหลดไม่ได้: " + e.message; return; }
    const d = D.data, x = d.item, c = cls(x.status);
    $("adDot").className = "apBig " + c;
    $("adName").textContent = x.name;
    const where = ["http_local", "port_listen"].includes(x.metric) ? "ตรวจบนเครื่อง" : ["http_url", "tcp_port"].includes(x.metric) ? "ตรวจจาก central" : TYPE_TH[x.type] || "";
    $("adSub").textContent = `${x.what} · ${x.web} · ${where}`;
    const pct = v => v == null ? "-" : `${v.toFixed(v >= 99.9 ? 2 : 1)}%`;
    const pc = v => v == null ? "" : v >= 99.9 ? "ok" : v >= 99 ? "wr" : "cr";
    const st = { ok: ["ทำงาน", "ok"], warning: ["ทำงาน (เตือน)", "wr"], error: ["ไม่ทำงาน", "cr"], down: ["ไม่ทำงาน", "cr"] }[x.status] || ["รอข้อมูล", ""];
    const u = d.uptime || {};
    const timed = x.type === "web" || x.metric === "tcp_port";
    $("adStats").innerHTML = [
        ["สถานะ", `<b class="${st[1]}">${st[0]}</b>`],
        x.type === "web" ? ["HTTP ล่าสุด", `<b class="${x.code && x.code < 400 ? "ok" : x.code != null ? "cr" : ""}">${x.code ?? "-"}</b>`]
            : ["ประเภท", `<b>${esc(TYPE_TH[x.type] || x.type)}</b>`],
        timed ? ["เวลาตอบล่าสุด", `<b>${x.ms != null && (x.code == null || x.code) ? Math.round(x.ms) + " ms" : "-"}</b>`]
            : ["ตรวจ", `<b>${esc(x.what || "-")}</b>`],
        timed ? [`สูงสุด (${D.range})`, `<b>${d.max_ms != null ? d.max_ms + " ms" : "-"}</b>`] : ["เครื่อง", `<b>${esc(x.web)}</b>`],
        ["Uptime 24h", `<b class="${pc(u.h24)}">${pct(u.h24)}</b><small>7d ${pct(u.d7)} · 30d ${pct(u.d30)}</small>`],
        [x.status === "ok" || x.status === "warning" ? "ทำงานมา" : "เป็นแบบนี้มา", `<b>${x.since ? ago(x.since) : "-"}</b>`],
    ].map(([k, v]) => `<div><small>${k}</small>${v}</div>`).join("");
    $("adChart").innerHTML = chartSvg(d.series || {}, D.range);
    $("adMsg").textContent = x.message || "";
    const inc = d.incidents || [];
    $("adInc").innerHTML = inc.length ? `<div class="tblw"><table class="ptbl"><thead><tr><th>เริ่ม</th><th>จบ</th><th>นาน</th><th>ระดับ</th></tr></thead><tbody>${
        inc.map(i => `<tr><td>${esc(new Date(i.start * 1000).toLocaleString("en-GB"))}</td><td>${i.end ? esc(new Date(i.end * 1000).toLocaleString("en-GB")) : "<b>ยังไม่หาย</b>"}</td>`
            + `<td>${i.duration ? esc(dur(i.duration)) : "-"}</td><td>${esc(i.worst || "-")}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="apMsgLine">ไม่มีปัญหาใน 30 วันที่ผ่านมา</div>`;
    const set = [`ระดับเมื่อไม่ทำงาน: ${x.level === "warning" ? "Warning" : "Error"}`, `ต้องผิดปกติต่อเนื่อง ${x.for_minutes ?? 3} นาที`,
        x.keyword ? `ต้องมีคำ "${x.keyword}"` : "", x.slow_ms ? `เตือนเมื่อช้ากว่า ${x.slow_ms} ms` : "", x.ssl_days ? `เตือน SSL ก่อนหมดอายุ ${x.ssl_days} วัน` : "",
        x.ssl_days_left != null ? `SSL เหลือ ${x.ssl_days_left} วัน` : ""].filter(Boolean);
    $("adSet").textContent = "ตั้งค่า: " + set.join(" · ") + (x.type === "web" ? " · 2xx / 3xx = ทำงาน, 4xx / 5xx / ไม่ตอบ = ไม่ทำงาน" : "");
    $("adEdit").hidden = A.role !== "admin";
}
function openDetail(rule, web) {
    D.rule = rule; D.web = web; D.data = null;
    $("adStats").innerHTML = ""; $("adChart").innerHTML = `<div class="apEmpty">กำลังโหลด...</div>`; $("adInc").innerHTML = ""; $("adMsg").textContent = "";
    $("apDetail").hidden = false;
    try { history.replaceState(null, "", `/apps?open=${encodeURIComponent(rule)}&web=${encodeURIComponent(web || "")}`); } catch (e) { /* ignore */ }
    loadDetail();
}
function closeDetail() {
    $("apDetail").hidden = true; D.rule = null;
    try { history.replaceState(null, "", "/apps"); } catch (e) { /* ignore */ }
}

function render() {
    const items = A.items;
    const bad = items.filter(x => BAD.includes(x.status) || WARN.includes(x.status));
    const hero = $("apHero");
    const okN = items.filter(x => x.status === "ok").length, waitN = items.length - okN - bad.length;
    hero.className = "card apHero " + (!items.length ? "" : bad.some(x => BAD.includes(x.status)) ? "bad" : bad.length || waitN ? "wr" : "ok");
    $("apHeadline").textContent = !items.length ? "ยังไม่ได้เฝ้าดูโปรแกรมหรือเว็บ"
        : bad.length ? `มีปัญหา ${bad.length} จาก ${items.length} รายการ`
        : waitN ? `ทำงานปกติ ${okN} · รอข้อมูล ${waitN} รายการ` : `ทำงานปกติทั้งหมด ${items.length} รายการ`;
    $("apSub").textContent = items.length ? `${bad.length ? bad.map(x => x.name).slice(0, 3).join(", ") + (bad.length > 3 ? " ..." : "") + " · " : ""}อัปเดต ${new Date(A.at).toLocaleTimeString("en-GB")} · ตรวจทุก 30 วินาที`
        : (A.role === "admin" ? "กด + เพิ่ม เพื่อเลือกโปรแกรม เว็บ หรือพอร์ตที่ต้องการดู" : "ให้ admin เพิ่มรายการที่ต้องการดู");
    $("cAll").textContent = items.length; $("cBad").textContent = bad.length;

    const q = A.find.toLowerCase();
    const rows = items.filter(x => (A.filter === "all" || (A.filter === "problem" ? bad.includes(x) : x.type === A.filter))
        && (!q || `${x.name} ${x.web} ${x.what} ${x.message}`.toLowerCase().includes(q)));
    $("apGrid").innerHTML = rows.length ? rows.map(tile).join("")
        : `<div class="apEmpty card" style="grid-column:1/-1"><b>${items.length ? "ไม่มีรายการที่ตรงกับตัวกรอง" : "ยังไม่มีรายการ"}</b>${items.length ? "" : "เช่น SQL Server บนเครื่อง DB, เว็บระบบบัญชี, พอร์ต 1433"}</div>`;
}

async function load() {
    try {
        const d = await api("GET", "/api/apps");
        A.items = d.items || []; A.at = Date.now();
        render();
    } catch (e) { if (e.message !== "auth") $("apHeadline").textContent = "โหลดข้อมูลไม่ได้: " + e.message; }
}

/* ---------- เพิ่ม ---------- */
const HINT = {
    program: ["ชื่อโปรแกรม", "เช่น sqlservr, java, w3wp", "ผิดปกติเมื่อไม่มี process ชื่อนี้ทำงาน (เครื่อง Windows) · รายการที่แนะนำมาจากโปรแกรมที่ทำงานอยู่บนเครื่องตอนนี้"],
    web: ["URL", "เช่น http://{host}:8080/health", "central เรียก URL นี้ทุก 30 วินาที ผิดปกติเมื่อเรียกไม่ได้ / เกิน 10 วินาที / HTTP 4xx-5xx · {host} = Host ของเครื่อง"],
    port: ["พอร์ต", "เช่น 1433", "ผิดปกติเมื่อพอร์ตนี้ไม่ได้เปิดรับบนเครื่อง (ดูจากรายการพอร์ตที่เครื่อง Windows ส่งมา)"],
    service: ["ชื่อ service", "เช่น MSSQLSERVER, W3SVC, nginx.service", "ผิดปกติเมื่อ Windows service นี้ไม่ได้ Running"],
    docker: ["ชื่อ container", "เช่น web, postgres", "ผิดปกติเมื่อ container ไม่ได้ running หรือ unhealthy · Windows: เครื่องตรวจเองทุก 1 นาที · Linux: central ตรวจผ่าน SSH ทุก ~1 นาที (user SSH ต้องใช้คำสั่ง docker ได้)"],
};
const HINT_LOCAL = { port: HINT.port };
HINT.web = ["URL", "เช่น http://localhost:8090 หรือ http://{host}:8080/health",
    "localhost / 127.0.0.1 = ตรวจบนเครื่อง · ที่อื่น = central เรียก · 2xx / 3xx = ทำงาน · 4xx / 5xx / ไม่ตอบ = ไม่ทำงาน"];

/* ---------- Monitor เว็บ: โหมดเลือกจาก URL (ต้องตรงกับ alert_rules_service.normalize_url / web_metric) ---------- */
const LOCAL_HOSTS = ["localhost", "127.0.0.1", "::1", "[::1]"];
function normUrl(v) {
    v = String(v || "").trim();
    if (/^:?\d{1,5}$/.test(v)) v = "localhost:" + v.replace(/^:/, "");
    else if (/^:\d{1,5}(\/.*)?$/.test(v)) v = "localhost" + v;
    if (v && !/^[a-z][a-z0-9+.-]*:\/\//i.test(v)) v = "http://" + v;
    return v;
}
function urlParts(v) {
    const m = /^[a-z][a-z0-9+.-]*:\/\/(\[[^\]]*\]|[^/:?#]*)(?::(\d*))?/i.exec(v || "");
    return m ? { host: m[1].toLowerCase(), port: m[2] || "" } : { host: "", port: "" };
}
function isLocalUrl(v) { return LOCAL_HOSTS.includes(urlParts(normUrl(v)).host); }
function showMode() {
    const box = $("apMode");
    if (A.type !== "web") { box.hidden = true; return; }
    const raw = $("apVal").value.trim();
    box.hidden = !raw;
    if (!raw) return;
    const u = normUrl(raw), p = urlParts(u), local = isLocalUrl(raw);
    const win = (A.targets.find(x => x.name === $("apWeb").value) || {}).os === "windows";
    box.innerHTML = local
        ? `จะตรวจ: <b>บนเครื่อง</b> (${win ? "cm-probe ทุก 1 นาที" : "curl ผ่าน SSH ทุก ~1 นาที"}) · ${esc(u)}${p.port ? "" : " · <span style=\"color:#e5484d\">ใส่พอร์ตด้วย เช่น :8090</span>"}`
        : `จะตรวจ: <b>จาก central</b> (ทุก 30 วินาที) · ${esc(u.replace("{host}", machineHost() || "{host}"))}`;
    document.querySelectorAll("#apPorts button[data-port]").forEach(b => b.setAttribute("aria-pressed", String(local && b.dataset.port === p.port)));
}
async function loadPorts() {
    const box = $("apPorts"); box.hidden = true; box.textContent = "";
    const web = $("apWeb").value, t = A.targets.find(x => x.name === web);
    if (A.type !== "web" || !t || t.os !== "windows") return;
    let d;
    try { d = await api("GET", `/api/host/${encodeURIComponent(web)}/probe-ports`); } catch (e) { return; }
    if (web !== $("apWeb").value || A.type !== "web") return;
    const ports = d.ports || [];
    if (!ports.length) {
        box.hidden = false;
        box.textContent = d.probe_time ? "เครื่องนี้ยังไม่พบเว็บบนพอร์ตใด" : "เครื่องนี้ยังไม่ส่งผลตรวจเว็บ (ติดตั้ง agent ซ้ำ: Settings > เครื่อง > Download zip + Make command)";
        return;
    }
    const btn = p => { const c = !p.code ? "na" : p.code < 400 ? "ok" : "bad";
        return `<button type="button" class="${c}" data-port="${p.port}" data-scheme="${esc(p.scheme)}"${p.code ? "" : " hidden"}>`
            + `${p.port} · <b>HTTP ${p.code}</b>${p.ms != null ? ` · ${p.ms} ms` : ""}</button>`; };
    const hidden = ports.filter(p => !p.code).length;
    box.innerHTML = `<span>เว็บที่พบบนเครื่อง:</span>${ports.map(btn).join("")}`
        + (hidden ? `<button type="button" class="lnk" id="apPortsAll">แสดงทั้งหมด (+${hidden} ที่ไม่ใช่เว็บ)</button>` : "");
    box.hidden = false;
    showMode();
}
const HINT_REMOTE = {
    web: HINT.web,
    port: ["พอร์ต", "เช่น 8080", "central ต่อ TCP ไปที่ Host (IP) ของเครื่องทุก 30 วินาที (เครื่องต้องมี Host ในหน้า Settings) ผิดปกติเมื่อต่อไม่ได้"],
};
async function suggest() {
    const dl = $("apSuggest"); dl.textContent = "";
    const web = $("apWeb").value;
    if (!web || (A.type === "web" && !A.local)) return;
    try {
        const d = await api("GET", `/api/apps/suggest?web=${encodeURIComponent(web)}&type=${A.type}`);
        (d.items || []).forEach(v => { const o = document.createElement("option"); o.value = v; dl.append(o); });
    } catch (e) { /* ไม่มีข้อมูล = ไม่แนะนำ */ }
}
function machineHost() {
    const t = A.targets.find(x => x.name === $("apWeb").value);
    return t && t.host ? t.host : "";
}
function refreshHint() {
    const t = A.type, both = t === "port";          // เว็บ: เลือกโหมดจาก URL เอง ไม่มีตัวเลือก
    $("apWhere").hidden = !both;
    $("apMore").hidden = t !== "web";
    const [label, ph, hint] = both ? (A.local ? HINT_LOCAL : HINT_REMOTE)[t] : HINT[t];
    $("apValLabel").textContent = label; $("apVal").placeholder = ph;
    const h = machineHost();
    $("apHint").textContent = hint + (both && !A.local ? (h ? ` · Host ของเครื่องนี้: ${h} (ใช้ {host} ใน URL ได้)` : " · ⚠ เครื่องนี้ยังไม่มี Host ในหน้า Settings") : "");
    $("apVal").inputMode = t === "port" ? "numeric" : "text";
    showMode();
}
function setType(t) {
    A.type = t;
    document.querySelectorAll("#apType button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.t === t)));
    refreshHint(); suggest(); loadPorts();
}
function setLocal(on) {
    A.local = on;
    document.querySelectorAll("#apLocal button").forEach(b => b.setAttribute("aria-pressed", String((b.dataset.l === "1") === on)));
    refreshHint(); suggest();
}
async function openForm(edit) {
    $("apForm").hidden = false; $("apMsg").textContent = "";
    if (!A.targets.length) {
        try { A.targets = ((await api("GET", "/api/alert-rules")).targets || []); } catch (e) { A.targets = []; }
    }
    const sel = $("apWeb"); sel.textContent = "";
    A.targets.forEach(t => { const o = document.createElement("option"); o.value = t.name; o.textContent = t.name + (t.os === "windows" ? " (Windows)" : ""); sel.append(o); });
    A.editing = edit ? edit.rule : null;
    $("apSave").textContent = edit ? "บันทึก" : "เพิ่ม";
    if (edit) {
        sel.value = (edit.web || "").split(",")[0].trim();
        $("apVal").value = edit.metric === "port_listen" || edit.metric === "tcp_port" ? String(edit.what).replace(/^.*:/, "") : edit.what;
        $("apName").value = edit.name;
        $("apKw").value = edit.keyword || ""; $("apSlow").value = edit.slow_ms || ""; $("apSsl").value = edit.ssl_days || "";
        $("apFor").value = edit.for_minutes || "";
        $("apMore").open = !!(edit.keyword || edit.slow_ms || edit.ssl_days);
        A.local = !["http_url", "tcp_port"].includes(edit.metric);
        document.querySelectorAll("#apLocal button").forEach(b => b.setAttribute("aria-pressed", String((b.dataset.l === "1") === A.local)));
        setType(edit.type);
    } else {
        $("apVal").value = ""; $("apName").value = "";
        ["apKw", "apSlow", "apSsl", "apFor"].forEach(id => { $(id).value = ""; }); $("apMore").open = false;
        setType(A.type);
    }
    $("apForm").scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("apVal").focus();
}
async function save(ev) {
    ev.preventDefault();
    $("apMsg").textContent = ""; $("apSave").disabled = true;
    try {
        const v = $("apVal").value.trim();
        const body = { type: A.type, web: $("apWeb").value, value: A.type === "web" ? normUrl(v) : v, name: $("apName").value.trim(),
                       local: A.type === "web" ? isLocalUrl(v) : A.local };
        if (A.type === "web") Object.assign(body, { keyword: $("apKw").value.trim(), slow_ms: $("apSlow").value.trim(),
                                                    ssl_days: $("apSsl").value.trim(), for_minutes: $("apFor").value.trim() });
        if (A.editing) await api("PUT", `/api/apps/${encodeURIComponent(A.editing)}`, body);
        else await api("POST", "/api/apps", body);
        A.editing = null;
        $("apVal").value = ""; $("apName").value = ""; $("apForm").hidden = true;
        await load();
    } catch (e) {
        if (e.message !== "auth") $("apMsg").textContent = Object.values(e.fields || {}).join(" · ") || e.message;
    } finally { $("apSave").disabled = false; }
}
async function remove(rule) {
    const x = A.items.find(i => i.rule === rule);
    if (!confirm(`เลิกเฝ้าดู "${x ? x.name : rule}"?`)) return;
    try { await api("DELETE", `/api/apps/${encodeURIComponent(rule)}`); await load(); }
    catch (e) { if (e.message !== "auth") alert("ลบไม่สำเร็จ: " + e.message); }
}

document.addEventListener("DOMContentLoaded", async () => {
    try {
        const me = await api("GET", "/api/me");
        A.role = me.role || "viewer";
        $("currentUsername").textContent = me.username || "?";
        $("settingsLink").hidden = A.role !== "admin" && A.role !== "operator";
        $("apAddBtn").hidden = A.role !== "admin";
    } catch (e) { return; }
    $("logoutButton").onclick = async () => { await fetch("/api/logout", { method: "POST" }); location.href = "/login"; };
    $("apBar").addEventListener("click", e => {
        const b = e.target.closest("[data-f]"); if (!b) return;
        A.filter = b.dataset.f;
        document.querySelectorAll("#apBar [data-f]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
        render();
    });
    $("apFind").addEventListener("input", e => { A.find = e.target.value; render(); });
    $("apAddBtn").onclick = () => openForm(null);
    $("apCancel").onclick = () => { $("apForm").hidden = true; A.editing = null; };
    $("apType").addEventListener("click", e => { const b = e.target.closest("[data-t]"); if (b) setType(b.dataset.t); });
    $("apWeb").addEventListener("change", () => { refreshHint(); suggest(); loadPorts(); });
    $("apVal").addEventListener("input", showMode);
    $("apPorts").addEventListener("click", e => {
        if (e.target.closest("#apPortsAll")) {
            $("apPorts").querySelectorAll("button[hidden]").forEach(b => { b.hidden = false; });
            e.target.closest("#apPortsAll").remove(); return;
        }
        const b = e.target.closest("button[data-port]"); if (!b) return;
        $("apVal").value = `${b.dataset.scheme === "https" ? "https" : "http"}://localhost:${b.dataset.port}`;
        if (!$("apName").value.trim()) $("apName").placeholder = `เช่น เว็บพอร์ต ${b.dataset.port}`;
        showMode(); $("apVal").focus();
    });
    $("apLocal").addEventListener("click", e => { const b = e.target.closest("[data-l]"); if (b) setLocal(b.dataset.l === "1"); });
    $("apForm").addEventListener("submit", save);
    $("adClose").onclick = closeDetail;
    $("apDetail").addEventListener("click", e => { if (e.target === $("apDetail")) closeDetail(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("apDetail").hidden) closeDetail(); });
    $("adRange").addEventListener("click", e => {
        const b = e.target.closest("[data-r]"); if (!b) return;
        D.range = b.dataset.r;
        document.querySelectorAll("#adRange [data-r]").forEach(x => x.setAttribute("aria-pressed", String(x === b)));
        loadDetail();
    });
    $("adEdit").onclick = () => { const x = D.data && D.data.item; closeDetail(); if (x) openForm(x); };
    $("apGrid").addEventListener("click", e => {
        if (!e.target.closest("[data-edit],[data-del]")) {
            const o = e.target.closest("[data-open]");
            if (o) { e.preventDefault(); openDetail(o.dataset.open, o.dataset.oweb); return; }
        }
        const ed = e.target.closest("[data-edit]");
        if (ed) { e.preventDefault(); openForm(A.items.find(i => i.rule === ed.dataset.edit)); return; }
        const d = e.target.closest("[data-del]"); if (!d) return;
        e.preventDefault(); remove(d.dataset.del);
    });
    load();
    const qs = new URLSearchParams(location.search);
    if (qs.get("open")) openDetail(qs.get("open"), qs.get("web"));
    setInterval(() => { if (!document.hidden) { load(); if (D.rule) loadDetail(); } }, 30000);
});
