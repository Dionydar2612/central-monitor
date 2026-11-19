/* netstat.js - Apps & Web > Netstat Monitor (/netstat)
   Dashboard: Server-Sent Events /api/netstat/stream (ต่อใหม่เองเมื่อหลุด / ถูกตัดทุก ~55 วิ) เต็ม -> polling /api/netstat/dashboard
   ไม่ refresh หน้า · ค่าที่ไม่มี (null) แสดง Unavailable / Unsupported ไม่ใช่ 0 */
"use strict";
const $ = id => document.getElementById(id);
const N = {
    role: "viewer", tab: "dash", every: 2, es: null, poll: null, lastMsg: 0, data: null,
    buf: {}, graphKey: "", hosts: [], host: null, edit: null, alSt: "active", hRange: "1h",
};
const STATUS = { normal: ["Normal", "ok"], warning: ["Warning", "wr"], critical: ["Critical", "cr"], offline: ["Offline", "cr"],
    stale: ["Agent หยุดส่ง", "cr"], unknown: ["Unknown", "na"], disabled: ["ปิดอยู่", "na"] };
const AGENT = { online: ["Online", "ok"], offline: ["Offline", "cr"], stale: ["หยุดส่งข้อมูล", "cr"], "no-data": ["ไม่มีข้อมูล Netstat", "wr"],
    unknown: ["Unknown", "na"], paused: ["Paused", "ps"] };
const KIND = { app_queue: "Queue exceed", app_error: "Log ERROR", queue: "Queue", conn: "Connections", not_listening: "Port ไม่ LISTEN", host_offline: "Host Offline",
    agent_stale: "Agent หยุดส่งข้อมูล", queue_spike: "Queue เพิ่มสูงผิดปกติ", app_queue: "Application Queue exceed" };
const APP_LV = { WARN: ["WARN", "wr"], WARNING: ["WARN", "wr"], ERROR: ["ERROR", "cr"], RECOVER: ["กลับปกติ", "ok"] };
const STATE_NAME = { established: "Established", syn_sent: "Syn Sent", syn_received: "Syn Received", fin_wait1: "Fin Wait 1",
    fin_wait2: "Fin Wait 2", time_wait: "Time Wait", close_wait: "Close Wait", last_ack: "Last Ack", closing: "Closing", other: "อื่น ๆ" };

function esc(v) { return String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }
async function api(method, url, body) {
    const r = await fetch(url, { method, cache: "no-store", credentials: "same-origin",
        headers: body ? { "Content-Type": "application/json" } : {}, body: body ? JSON.stringify(body) : undefined });
    if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { const e = new Error(d.error || `HTTP ${r.status}`); e.fields = d.fields; throw e; }
    return d;
}
const canEdit = () => N.role === "operator" || N.role === "admin";
const fmtT = t => t ? new Date(t * 1000).toLocaleString("en-GB", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "-";
function dur(s) {
    s = Math.max(0, Math.round(Number(s) || 0));
    const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60), x = s % 60;
    return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m ${x}s` : `${x}s`;
}
const ago = t => t ? dur(Date.now() / 1000 - t) + " ที่แล้ว" : "-";
/* ค่าที่ไม่มีข้อมูล -> Unavailable (ไม่ใช่ 0) */
const na = (txt, why) => `<span class="nxNa" title="${esc(why || "")}">${esc(txt || "Unavailable")}</span>`;
const val = (v, why, txt) => v === null || v === undefined ? na(txt, why) : esc(v);
const pill = ([l, c]) => `<span class="evst ${c === "cr" ? "dn" : c === "na" ? "" : c}">${esc(l)}</span>`;
const portTxt = r => `${r.port}/${(r.proto || "tcp").toUpperCase()}${r.dir === "out" ? " (ขาออก)" : ""}`;

/* ---------------------------------------------------------------- real-time */
function setConn(state, note) {
    const box = $("nxLive"); box.dataset.s = state;
    $("nxConn").textContent = { live: "Live", connecting: "กำลังเชื่อมต่อ…", reconnecting: "หลุด · กำลังต่อใหม่", poll: "Live (polling)",
        offline: "ไม่ได้รับข้อมูล", stale: "ข้อมูลไม่อัปเดต" }[state] || state;
    $("nxConn").title = note || "";
}
function filterQs() {
    const p = new URLSearchParams();
    if ($("fHost").value) p.set("host", $("fHost").value);
    return p;
}
function stopLive() {
    if (N.es) { N.es.close(); N.es = null; }
    if (N.poll) { clearInterval(N.poll); N.poll = null; }
}
function connect() {
    stopLive();
    const p = filterQs(); p.set("every", N.every);
    if (!window.EventSource) return startPoll();
    setConn(N.lastMsg ? "reconnecting" : "connecting");
    const es = new EventSource(`/api/netstat/stream?${p}`);
    N.es = es;
    es.addEventListener("snapshot", e => { N.lastMsg = Date.now(); apply(JSON.parse(e.data)); });
    es.onerror = () => {
        if (es.readyState === EventSource.CLOSED) {          // 503 (เต็ม) / ถูกปฏิเสธ -> polling แล้วลอง SSE ใหม่ใน 30 วิ
            startPoll();
            setTimeout(() => { if (N.poll) connect(); }, 30000);
        } else if (Date.now() - N.lastMsg > N.every * 2000 + 3000) setConn("reconnecting");   // EventSource ต่อใหม่เอง (ตัดทุก ~55 วิ = ปกติ)
    };
}
function startPoll() {
    if (N.es) { N.es.close(); N.es = null; }
    if (N.poll) clearInterval(N.poll);
    const tick = async () => {
        try { N.lastMsg = Date.now(); apply(await api("GET", `/api/netstat/dashboard?${filterQs()}`), true); }
        catch (e) { if (e.message !== "auth") setConn("reconnecting", e.message); }
    };
    tick();
    N.poll = setInterval(tick, N.every * 1000);
}
/* ตรวจทุกวินาที: ไม่ได้รับข้อความนาน / puller ไม่อัปเดต live.json */
setInterval(() => {
    const d = N.data;
    $("nxUpd").textContent = d && d.updated ? `Last Update ${fmtT(d.updated)} (${dur(Date.now() / 1000 - d.updated)} ago)` : "";
    if (!N.lastMsg) return;
    if (Date.now() - N.lastMsg > Math.max(20000, N.every * 3000)) setConn("offline", "ไม่ได้รับข้อมูลจาก central");
    else if (d && !d.live) setConn("stale", "central ไม่ได้อัปเดตข้อมูล Netstat (puller ไม่ทำงาน?)");
    else setConn(N.poll ? "poll" : "live");
}, 1000);

function apply(d, polled) {
    N.data = d;
    const now = Date.now() / 1000;
    (d.rows || []).forEach(r => {
        const k = `${r.web}|${r.key}`, b = N.buf[k] || (N.buf[k] = []);
        const t = r.last_update || d.updated || now;
        if (!b.length || b[b.length - 1][0] !== t) b.push([t, r.conn, r.queue]);
        while (b.length && b[0][0] < now - 300) b.shift();
    });
    fillHosts(d.hosts || []);
    if (N.tab === "dash") renderDash();
    if (N.tab === "hosts" && N.hostsAt && Date.now() - N.hostsAt > 10000) loadHosts();
    if (!polled) setConn("live");
}

/* ---------------------------------------------------------------- dashboard */
function fillHosts(list) {
    ["fHost", "nxAlHost"].forEach(id => {
        const s = $(id), cur = s.value;
        if (s.options.length - 1 === list.length && list.every((h, i) => s.options[i + 1].value === h)) return;
        s.innerHTML = `<option value="">ทุกเครื่อง</option>` + list.map(h => `<option>${esc(h)}</option>`).join("");
        s.value = cur;
    });
}
function tile(label, n, cls, sub) { return `<div class="ovtile ${cls}"><small>${esc(label)}</small><b>${n ?? "-"}</b>${sub ? `<em>${esc(sub)}</em>` : ""}</div>`; }
/* ไม่มีค่าคิว -> แสดง 0 (สีจาง ชี้ดูเหตุผล) */
const zero = why => `<span class="nxZero" title="${esc(why || "")}">0</span>`;
function queueCell(r, k) {
    if (r.host_status && r.host_status !== "online") return na("Offline", "เครื่องไม่ตอบ");
    if (k === "recv") return zero("Windows ไม่มี Recv-Q ต่อ socket");
    if (k === "send") return zero("Windows ไม่มี Send-Q ต่อ socket");
    if (r.queue === null || r.queue === undefined) return zero(r.queue_note || "ไม่มีคิวของพอร์ตนี้");
    const lv = r.queue_level ? ` ${r.queue_level === "critical" ? "cr" : "wr"}` : "";
    return `<b class="nxV${lv || (r.app_state ? " cr" : "")}" title="${esc(r.queue_kind || "")}">${Number(r.queue).toLocaleString()}</b>${r.accept_limit ? `<small> / ${Number(r.accept_limit).toLocaleString()}</small>` : ""}`;
}
const LIGHT = { red: ["แดง", "dn"], yellow: ["เหลือง", "wr"], green: ["เขียว", "ok"] };
function renderDash() {
    const d = N.data; if (!d) return;
    const s = d.summary || {}, now = Date.now() / 1000, fh = $("fHost").value;
    const rows = (d.lights || []).filter(l => !fh || l.web === fh).map(l => ({ ...l, out: `${l.log} ${l.ch}` }));
    (d.rows || []).filter(r => !fh || r.web === fh).forEach(r => {       // พอร์ตที่เลือก Monitor ไว้ = แถวละ 1 พอร์ต
        const color = { normal: "green", warning: "yellow" }[r.status] || (r.status === "unknown" ? "yellow" : "red");
        const q = r.queue != null ? ` · Queue ${r.queue}` : "";
        const msg = r.status === "offline" || r.status === "stale" ? (STATUS[r.status] || [r.status])[0]
            : r.listening === false ? "พอร์ตไม่ LISTEN" : `Connections ${r.conn ?? 0}${r.established != null ? ` (EST ${r.established})` : ""}${q}`;
        const k = `${r.web}|${r.key}`, ps = N.portSince || (N.portSince = {});
        if (!ps[k] || ps[k].c !== color) ps[k] = { c: color, t: now };
        rows.push({ web: r.web, ip: r.ip, port: portTxt(r), out: r.proc || "-", color, msg, ts: r.last_update, changed: ps[k].t });
    });
    (d.alerts || []).filter(a => a.status === "active" && !a.port && !String(a.kind).startsWith("app_") && (!fh || a.web === fh))
        .forEach(a => rows.push({ web: a.web, ip: a.ip, port: a.port, out: a.process || "-", color: a.level === "critical" ? "red" : "yellow",
            msg: `${KIND[a.kind] || a.kind}${a.message ? " · " + a.message : ""}`, ts: a.last || a.first, changed: a.first }));
    const ord = { red: 0, yellow: 1, green: 2 };
    rows.sort((x, y) => ord[x.color] - ord[y.color] || String(x.web).localeCompare(y.web));
    const n = c => rows.filter(r => r.color === c).length;
    $("nxTiles").innerHTML = tile("เครื่องทั้งหมด", s.hosts, "tot", "Windows Server") + tile("ออนไลน์", s.online, "up", `${(s.hosts || 0) - (s.online || 0)} ไม่ตอบ`)
        + tile("แดง", n("red"), n("red") ? "dn" : "tot", "ERROR / CRITICAL") + tile("เหลือง", n("yellow"), n("yellow") ? "nok" : "tot", "WARN") + tile("เขียว", n("green"), "up", "ปกติ");
    $("nxNow").innerHTML = rows.length ? rows.map(r => {
        const [t, c] = LIGHT[r.color] || LIGHT.green;
        return `<tr class="nxLt ${c}"><td><span class="evst ${c}">${t}</span>${r.offline ? ` <small>เครื่องไม่ตอบ</small>` : ""}</td><td><b>${esc(r.web)}</b></td><td>${esc(r.ip || "")}</td>`
            + `<td>${r.port ?? "-"}</td><td>${esc(r.out)}</td><td class="m">${esc(r.msg || "")}</td><td>${esc(fmtT(r.ts))}</td><td>${r.changed ? esc(dur(now - r.changed)) : "-"}</td></tr>`;
    }).join("") : `<tr><td colspan="8" class="empty">ยังไม่มีข้อมูล</td></tr>`;
}
function what(a) {
    if (a.kind === "app_queue") return `${a.log} ${a.ch} คิวเกิน${a.peak ? ` (สูงสุด ${Number(a.peak).toLocaleString()}${a.threshold ? ` / ${Number(a.threshold).toLocaleString()}` : ""})` : ""}${a.port ? ` · port ${a.port}` : ""}`;
    if (a.kind === "app_error") return `${a.log} ${a.ch} ${a.message || ""}`;
    return `${a.port ? `:${a.port} ` : ""}${KIND[a.kind] || a.kind}${a.message ? ` · ${a.message}` : ""}${a.process ? ` · ${a.process}` : ""}`;
}
function alertLevel(a) { if (a.status === "logged") return pill(["ERROR", "cr"]); return a.status === "active" ? pill(a.level === "critical" ? ["Critical", "cr"] : ["Warning", "wr"]) : pill(["Recovered", "ok"]); }

/* กราฟเส้น SVG: series = [{name, pts:[[t, v|null]], cls}]  null = ช่องว่าง (ไม่มีข้อมูล) */
function lineChart(series, opt) {
    opt = opt || {};
    const W = 760, H = opt.h || 170, L = 44, R = 10, T = 10, B = 22;
    const all = series.flatMap(s => s.pts.filter(p => p[1] !== null && p[1] !== undefined));
    if (!all.length) return `<div class="empty">${opt.empty || na("Unavailable", "ไม่มีข้อมูลในช่วงนี้")}</div>`;
    const t0 = opt.t0 ?? Math.min(...all.map(p => p[0])), t1 = opt.t1 ?? Math.max(...all.map(p => p[0]));
    const vmax = Math.max(1, ...all.map(p => p[1]), ...(opt.lines || []).map(l => l.v || 0)) * 1.1;
    const X = t => L + (t1 > t0 ? (t - t0) / (t1 - t0) : 1) * (W - L - R), Y = v => H - B - v / vmax * (H - T - B);
    const path = pts => { let d = "", pen = false; pts.forEach(([t, v]) => { if (v === null || v === undefined) { pen = false; return; } d += `${pen ? "L" : "M"}${X(t).toFixed(1)},${Y(v).toFixed(1)}`; pen = true; }); return d; };
    const ticks = [0, .5, 1].map(f => `<text x="${L - 6}" y="${Y(vmax / 1.1 * f) + 4}" text-anchor="end">${Math.round(vmax / 1.1 * f)}</text><line x1="${L}" x2="${W - R}" y1="${Y(vmax / 1.1 * f)}" y2="${Y(vmax / 1.1 * f)}" class="g"/>`).join("");
    const tl = [t0, (t0 + t1) / 2, t1].map((t, i) => `<text x="${X(t)}" y="${H - 6}" text-anchor="${["start", "middle", "end"][i]}">${new Date(t * 1000).toLocaleTimeString("en-GB", t1 - t0 > 86400 ? { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" } : { hour: "2-digit", minute: "2-digit", ...(t1 - t0 < 1800 ? { second: "2-digit" } : {}) })}</text>`).join("");
    const lines = (opt.lines || []).filter(l => l.v != null).map(l => `<line x1="${L}" x2="${W - R}" y1="${Y(l.v)}" y2="${Y(l.v)}" class="th ${l.cls}"><title>${esc(l.name)} ${l.v}</title></line>`).join("");
    return `<svg class="nxSvg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img">${ticks}${tl}${lines}${series.map((s, i) => `<path d="${path(s.pts)}" class="s${i} ${s.cls || ""}"/>`).join("")}</svg>`
        + `<div class="nxLeg">${series.map((s, i) => `<span class="s${i}">${esc(s.name)}</span>`).join("")}${(opt.lines || []).filter(l => l.v != null).map(l => `<span class="th ${l.cls}">${esc(l.name)} ${l.v}</span>`).join("")}</div>`;
}

/* ---------------------------------------------------------------- hosts & ports */
async function loadHosts() {
    N.hostsAt = Date.now();
    try {
        const d = await api("GET", "/api/netstat/hosts");
        N.hosts = d.hosts || [];
        $("nxHosts").innerHTML = N.hosts.length ? N.hosts.map(h => {
            const ed = canEdit() && !h.paused;
            return `<tr data-web="${esc(h.web)}" class="${N.host === h.web ? "on" : ""}"><td><b>${esc(h.web)}</b>${h.group ? `<small> ${esc(h.group)}</small>` : ""}</td><td>${esc(h.ip)}</td><td>${h.os ? esc(h.os) : na()}</td>`
                + `<td>${pill(AGENT[h.status] || AGENT.unknown)}${h.error && h.status !== "online" ? `<div class="nxErr">${esc(h.error)}</div>` : ""}</td><td>${h.last_seen ? esc(ago(h.last_seen)) : na()}</td>`
                + `<td>${ed ? `<label class="nxSw"><input type="checkbox" data-mon${h.enabled ? " checked" : ""}><span>${h.enabled ? "เปิด" : "ปิด"}</span></label>` : (h.enabled ? "เปิด" : "ปิด")}</td>`
                + `<td>${ed ? `<select data-int>${[1, 2, 5].map(i => `<option value="${i}"${h.interval === i ? " selected" : ""}>${i} วิ</option>`).join("")}</select>` : `${h.interval} วิ`}${h.agent_interval && h.agent_interval !== h.interval ? `<small title="agent บนเครื่องตั้งไว้ (ติดตั้งด้วย -NetstatInterval)"> agent ${h.agent_interval} วิ</small>` : ""}</td>`
                + `<td class="n">${h.ports_monitored} / ${h.ports_found}</td><td><button type="button" class="svbtn sm" data-open>พอร์ต</button></td></tr>`;
        }).join("") : `<tr><td colspan="9" class="empty">ยังไม่มีเครื่อง Windows (agent) — เพิ่มได้ที่ Settings</td></tr>`;
        if (!d.puller_ok) $("nxHostMsg").textContent = "central ยังไม่ได้อัปเดตข้อมูล Netstat (puller ไม่ทำงาน / ยังไม่อัปเดตเป็นรุ่นใหม่)";
        else $("nxHostMsg").textContent = "";
    } catch (e) { if (e.message !== "auth") $("nxHostMsg").textContent = "โหลดไม่ได้: " + e.message; }
}
async function saveHostQuick(web, patch) {
    try {
        const d = await api("GET", `/api/netstat/hosts/${encodeURIComponent(web)}`);
        const body = { enabled: d.enabled, interval: d.interval, ports: d.ports.filter(p => p.configured).map(cfgOf), apps: d.apps_cfg, ...patch };
        await api("PUT", `/api/netstat/hosts/${encodeURIComponent(web)}`, body);
        $("nxHostMsg").textContent = `${web}: บันทึกแล้ว · มีผลใน ~5 วินาที`;
        loadHosts();
        if (N.host === web) openHost(web);
    } catch (e) { if (e.message !== "auth") $("nxHostMsg").textContent = `${web}: ไม่สำเร็จ ${e.message} ${Object.values(e.fields || {}).join(" · ")}`; }
}
function cfgOf(p) {
    const o = { port: p.port, proto: p.proto, dir: p.dir, enabled: p.selected !== false };
    const th = x => Object.fromEntries(Object.entries(x || {}).filter(([, v]) => v !== "" && v != null));
    if (Object.keys(th(p.queue_th)).length) o.queue = th(p.queue_th);
    if (Object.keys(th(p.conn_th)).length) o.conn = th(p.conn_th);
    return o;
}
async function openHost(web) {
    N.host = web;
    document.querySelectorAll("#nxHosts tr").forEach(tr => tr.classList.toggle("on", tr.dataset.web === web));
    const box = $("nxHost"); box.hidden = false;
    box.innerHTML = `<div class="empty">Loading...</div>`;
    let d;
    try { d = await api("GET", `/api/netstat/hosts/${encodeURIComponent(web)}`); } catch (e) { box.innerHTML = `<div class="empty">โหลดไม่ได้: ${esc(e.message)}</div>`; return; }
    N.edit = d;
    renderHost();
    box.scrollIntoView({ behavior: "smooth", block: "start" });
}
function renderHost() {
    const d = N.edit, box = $("nxHost"), ed = canEdit();
    const f = N.portFilter || "listen", q = (N.portFind || "").toLowerCase();
    const sup = d.support || {};
    const ports = d.ports.filter(p => (f === "all" || (f === "listen" ? (p.listening || p.configured) : f === "selected" ? p.selected : p.dir === "out"))
        && (!q || `${p.port} ${p.proc} ${p.addr} ${(p.peers || []).map(x => x.remote).join(" ")}`.toLowerCase().includes(q)))
        .sort((a, b) => (b.selected - a.selected) || (a.dir !== b.dir ? (a.dir === "in" ? -1 : 1) : 0) || a.proto.localeCompare(b.proto) || a.port - b.port);
    const sts = p => Object.entries(p.states || {}).filter(([, v]) => v).map(([k, v]) => `${STATE_NAME[k] || k} ${v}`).join(" · ");
    const inp = (p, grp, k) => `<input class="nxTh" inputmode="numeric" data-th="${grp}.${k}" value="${esc((p[grp + "_th"] || {})[k] ?? "")}" placeholder="${k === "warning" ? "W" : "C"}"${ed ? "" : " disabled"}>`;
    box.innerHTML = `<div class="nxH"><b>${esc(d.web)}</b><small>${esc(d.ip)} · ${d.os ? esc(d.os) : "Windows"} · ${pill(AGENT[d.status] || AGENT.unknown)}</small>`
        + `<span class="nxSup">Recv-Q ${sup.recv ? "อ่านได้" : na("Unsupported", "Windows ไม่มี Recv-Q ต่อ socket")} · Send-Q ${sup.send ? "อ่านได้" : na("Unsupported", "Windows ไม่มี Send-Q ต่อ socket")} · Listening Queue ${sup.accept ? "อ่านได้ (IIS / HTTP.sys)" : na("Unsupported", "อ่านได้เฉพาะพอร์ตของ IIS / HTTP.sys")}</span></div>`
        + `<div class="nxFilt"><div class="vseg" id="nxPf">${[["listen", "Listening + ที่เลือก"], ["selected", "ที่เลือก"], ["out", "ขาออก"], ["all", "ทั้งหมด"]].map(([k, l]) => `<button type="button" data-pf="${k}" aria-pressed="${f === k}">${l}</button>`).join("")}</div>`
        + `<input id="nxPfind" placeholder="ค้นหาพอร์ต / process / IP" value="${esc(N.portFind || "")}" autocomplete="off">`
        + (ed ? `<label class="nxSw"><input type="checkbox" id="nxEn"${d.enabled ? " checked" : ""}><span>Monitoring</span></label><button type="button" class="st-btn primary" id="nxSave">บันทึก</button>` : "") + `<span class="st-msg" id="nxSaveMsg"></span></div>`
        + `<div class="tblw"><table class="st-tbl nxTbl nxPorts"><thead><tr><th>Monitor</th><th>Port</th><th>Protocol</th><th>Local Address</th><th>Remote Address</th><th>Process</th><th>PID</th><th>Connection State</th><th>Listening</th><th colspan="2">Queue W / C</th><th colspan="2">Connections W / C</th></tr></thead><tbody>`
        + (ports.length ? ports.map(p => `<tr data-key="${esc(p.key)}" class="${p.selected ? "sel" : ""}"><td><input type="checkbox" data-sel${p.selected ? " checked" : ""}${ed ? "" : " disabled"}></td>`
            + `<td><b>${esc(p.port)}</b>${p.dir === "out" ? "<small> ขาออก</small>" : ""}${p.missing ? ` <span class="evst dn">ไม่พบตอนนี้</span>` : ""}</td><td>${esc(p.proto.toUpperCase())}</td>`
            + `<td>${p.addr ? esc(p.addr) : na()}</td><td>${(p.peers || []).length ? p.peers.map(x => `${esc(x.remote)} <small>×${x.count}</small>`).join("<br>") : "-"}</td>`
            + `<td>${p.proc ? esc(p.proc) : na()}</td><td>${p.pid ?? na()}</td><td><small>${esc(sts(p)) || (p.total === 0 ? "ไม่มีการเชื่อมต่อ" : p.proto === "udp" ? "UDP (ไม่มีสถานะ)" : "-")}</small></td>`
            + `<td>${p.dir === "out" ? "-" : p.listening ? `<span class="evst ok">LISTEN</span>` : `<span class="evst dn">ไม่ LISTEN</span>`}</td>`
            + `<td>${inp(p, "queue", "warning")}</td><td>${inp(p, "queue", "critical")}</td><td>${inp(p, "conn", "warning")}</td><td>${inp(p, "conn", "critical")}</td></tr>`).join("")
            : `<tr><td colspan="13" class="empty">${d.status === "online" ? "ไม่พบพอร์ตตามตัวกรอง" : "ยังไม่มีข้อมูลจากเครื่องนี้"}</td></tr>`)
        + `</tbody></table></div>`
        + appHostHtml(d, ed)
        + (d.active.length ? `<div class="nxH" style="margin-top:14px"><b>Alert ที่ยังเกิดอยู่</b></div>${alertTable(d.active)}` : "");
}
function appHostHtml(d, ed) {
    const c = d.apps_cfg || {}, apps = d.apps || [];
    const dis = ed ? "" : " disabled";
    return `<div class="nxH" style="margin-top:18px"><b>Application Queue (จาก log)</b><small>ไฟล์ log ที่ตั้งไว้ (Settings / set-logs.bat) ที่มีบรรทัดแบบ "Output[n] queue exceed / queue decrease"</small></div>`
        + `<div class="nxFilt"><label class="nxSw"><input type="checkbox" id="apEn"${c.enabled !== false ? " checked" : ""}${dis}><span>ดูคิวจาก log</span></label>`
        + `<label>แจ้งเมื่อเกินนาน ≥ <input class="nxTh" id="apAfter" inputmode="numeric" value="${esc(c.notify_after ?? 10)}"${dis}> วินาที</label>`
        + `<label>หรือ size ≥ <input class="nxTh nxW" id="apCrit" inputmode="numeric" placeholder="ไม่ตั้ง" value="${esc(c.critical_size ?? "")}"${dis}> = Critical</label>`
        + `<small class="st-mut">สั้นกว่านี้ = บันทึกใน Alerts อย่างเดียว</small></div>`
        + (apps.length ? `<div class="tblw"><table class="st-tbl nxTbl"><thead><tr><th>Log</th><th>Output / Process</th><th>Port</th><th>Status</th><th class="n">Size</th><th class="n">Critical</th><th class="n">Exceed</th><th class="n">Disconnect</th><th class="n">Unknown Socket</th><th class="n">Drop</th></tr></thead><tbody>`
            + apps.map(a => `<tr><td>${esc(a.log)}</td><td>${esc(a.ch)}</td><td>${a.port ?? na()}</td><td>${a.state ? pill(["Queue exceed", "cr"]) : pill(["Normal", "ok"])}</td><td class="n">${a.size != null ? Number(a.size).toLocaleString() : na()}</td><td class="n">${a.critical != null ? Number(a.critical).toLocaleString() : na()}</td>`
                + ["exceed", "disconnect", "unknown_socket", "drop"].map(k => `<td class="n">${(a.counts || {})[k] ?? "-"}</td>`).join("") + `</tr>`).join("") + `</tbody></table></div>`
            : `<div class="empty">ยังไม่พบ — ตั้ง path ของไฟล์ log บนเครื่องนี้ก่อน (set-logs.bat หรือ Settings > ไฟล์ log แล้วติดตั้งซ้ำ)</div>`);
}
/* อ่านค่าที่แก้ในตารางพอร์ตกลับเข้า N.edit */
function syncPorts() {
    document.querySelectorAll("#nxHost tr[data-key]").forEach(tr => {
        const p = N.edit.ports.find(x => x.key === tr.dataset.key); if (!p) return;
        const sel = tr.querySelector("[data-sel]").checked;
        const th = {};
        tr.querySelectorAll("[data-th]").forEach(i => { const [g, k] = i.dataset.th.split("."); (th[g] = th[g] || {})[k] = i.value.trim(); });
        const any = Object.values(th).some(o => Object.values(o).some(Boolean));
        p.selected = sel; p.queue_th = th.queue; p.conn_th = th.conn;
        p.configured = sel || (p.configured && any);
    });
}
async function saveHost() {
    syncPorts();
    const d = N.edit, msg = $("nxSaveMsg");
    const body = { enabled: $("nxEn").checked, interval: d.interval, ports: d.ports.filter(p => p.configured).map(cfgOf),
        apps: { enabled: $("apEn").checked, notify_after: $("apAfter").value.trim(), critical_size: $("apCrit").value.trim() } };
    document.querySelectorAll("#nxHost .has-err").forEach(x => x.classList.remove("has-err"));
    try {
        await api("PUT", `/api/netstat/hosts/${encodeURIComponent(d.web)}`, body);
        msg.className = "st-msg ok"; msg.textContent = "บันทึกแล้ว · มีผลใน ~5 วินาที";
        loadHosts();
    } catch (e) {
        if (e.message === "auth") return;
        msg.className = "st-msg bad";
        const f = e.fields || {};
        msg.textContent = "บันทึกไม่ได้: " + (Object.entries(f).map(([k, v]) => {
            const m = k.match(/^ports\.(\d+)\.(\w+)(?:\.(\w+))?/);
            if (m) { const p = body.ports[Number(m[1])]; const tr = document.querySelector(`#nxHost tr[data-key="${p.proto}/${p.dir || "in"}/${p.port}"]`); tr?.querySelector(`[data-th="${m[2]}.${m[3]}"]`)?.classList.add("has-err"); return `${p.port}: ${v}`; }
            return v;
        }).join(" · ") || e.message);
    }
}
function alertTable(list) {
    return `<div class="tblw"><table class="st-tbl nxTbl"><thead><tr><th>Level</th><th>Alert</th><th>Host</th><th>IP Address</th><th>Port</th><th>Process</th><th class="n">Current</th><th class="n">Threshold</th><th>First Detected</th><th>Last Detected</th><th>Duration</th><th>Status</th></tr></thead><tbody>${alertRows(list)}</tbody></table></div>`;
}
function alertRows(list) {
    return list.length ? list.map(a => `<tr><td>${pill(a.level === "critical" ? ["Critical", "cr"] : ["Warning", "wr"])}</td><td>${esc(KIND[a.kind] || a.kind)}</td><td><b>${esc(a.web)}</b></td><td>${esc(a.ip || "")}</td>`
        + `<td>${a.port ? esc(`${a.port}/${(a.proto || "tcp").toUpperCase()}`) : "-"}</td><td>${esc(a.kind === "app_queue" ? `${a.log} ${a.ch}` : a.process || "-")}</td><td class="n">${a.value ?? "-"}${a.peak != null && a.peak !== a.value ? `<small> (สูงสุด ${esc(a.peak)})</small>` : ""}</td><td class="n">${a.threshold ?? "-"}</td>`
        + `<td>${esc(fmtT(a.first))}</td><td>${esc(fmtT(a.status === "active" ? a.last : a.recovered_at))}</td><td>${esc(dur(a.duration ?? ((a.recovered_at || Date.now() / 1000) - a.first)))}</td>`
        + `<td>${a.status === "active" ? pill(["Active", a.level === "critical" ? "cr" : "wr"]) : a.status === "recovered" ? pill(["Recovered", "ok"]) : pill(["ปิด (เลิก Monitor)", "na"])}${a.kind === "app_queue" && a.notified === false ? `<small title="สั้นกว่าเวลาที่ตั้งให้แจ้ง"> · ไม่แจ้ง</small>` : ""}</td></tr>`).join("")
        : `<tr><td colspan="12" class="empty">ไม่มี alert</td></tr>`;
}

/* ---------------------------------------------------------------- alerts tab */
async function loadAlerts() {
    try {
        const p = new URLSearchParams({ status: N.alSt }); if ($("nxAlHost").value) p.set("web", $("nxAlHost").value);
        const d = await api("GET", `/api/netstat/alerts?${p}`);
        $("nxAlerts").innerHTML = alertRows(d.alerts || []);
    } catch (e) { if (e.message !== "auth") $("nxAlerts").innerHTML = `<tr><td colspan="12" class="empty">โหลดไม่ได้: ${esc(e.message)}</td></tr>`; }
    document.querySelectorAll("#nxAlSt button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.s === N.alSt)));
}

/* ---------------------------------------------------------------- history tab */
async function fillHistoryHosts() {
    if (!N.hosts.length) await loadHosts();
    const s = $("hHost"), cur = (N.hJump && N.hJump.w) || s.value || N.host || (N.hosts.find(h => h.enabled) || N.hosts[0] || {}).web || "";
    s.innerHTML = N.hosts.map(h => `<option${h.web === cur ? " selected" : ""}>${esc(h.web)}</option>`).join("");
    await fillHistoryPorts();
}
async function fillHistoryPorts() {
    const web = $("hHost").value; if (!web) return;
    try {
        const d = await api("GET", `/api/netstat/hosts/${encodeURIComponent(web)}`);
        const ports = d.ports.filter(p => p.configured).concat(d.ports.filter(p => !p.configured && p.listening && p.proto === "tcp"));
        const cur = $("hPort").value;
        $("hPort").innerHTML = (ports.length || (d.apps || []).length) ? ports.map(p => `<option value="${esc(p.key)}">${esc(portTxt(p))}${p.proc ? " · " + esc(p.proc) : ""}${p.configured ? " ★" : ""}</option>`).join("")
            + (d.apps || []).map(a => `<option value="app:${esc(a.key)}">Queue: ${esc(a.log)} ${esc(a.ch)}</option>`).join("") : `<option value="">ไม่มีพอร์ต</option>`;
        if (cur && ports.some(p => p.key === cur)) $("hPort").value = cur;
        if (N.hJump && N.hJump.w === web) { $("hPort").value = N.hJump.k; N.hJump = null; }
    } catch (e) { /* แสดงตอนโหลดกราฟ */ }
    loadHistory();
}
async function loadHistory() {
    const web = $("hHost").value, key = $("hPort").value, body = $("hBody");
    document.querySelectorAll("#hRange button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.r === N.hRange)));
    $("hCustom").hidden = N.hRange !== "custom";
    if (!web || !key) { body.innerHTML = `<div class="empty">เลือกเครื่องและพอร์ต</div>`; return; }
    const p = new URLSearchParams({ web, key });
    if (N.hRange === "custom") {
        const s = Date.parse($("hStart").value), e = Date.parse($("hEnd").value);
        if (!s) { body.innerHTML = `<div class="empty">เลือกวันเวลาเริ่ม</div>`; return; }
        p.set("start", s / 1000); if (e) p.set("end", e / 1000);
    } else p.set("range", N.hRange);
    body.innerHTML = `<div class="empty">Loading...</div>`;
    let d;
    try { d = await api("GET", `/api/netstat/history?${p}`); } catch (e) { body.innerHTML = `<div class="empty">โหลดไม่ได้: ${esc(e.message)} ${esc(Object.values(e.fields || {}).join(" "))}</div>`; return; }
    if (!d.available) { body.innerHTML = `<div class="empty">${na("Unavailable", "ติดต่อ Prometheus ไม่ได้")} ดึงข้อมูลย้อนหลังไม่ได้</div>`; return; }
    const opt = { t0: d.start, t1: d.end };
    if (d.app) {
        const crit = (d.critical || []).length ? d.critical[d.critical.length - 1][1] : null;
        body.innerHTML = `<div class="nxHist"><div><div class="nxH"><b>Queue Size</b><small>ค่า size ล่าสุดจาก log ในแต่ละช่วง ${dur(d.step)}</small></div>${lineChart([{ name: "Queue size", pts: d.size }], { ...opt, lines: [{ name: "CRITICAL SIZE", v: crit, cls: "cr" }] })}</div>`
            + `<div><div class="nxH"><b>ช่วงที่คิวเกิน</b><small>${(d.alerts || []).length} ครั้ง</small></div>${alertTable(d.alerts || [])}</div></div>`;
        return;
    }
    const stNames = Object.keys(d.states || {}).sort();
    body.innerHTML = `<div class="nxHist"><div><div class="nxH"><b>Connection Count</b><small>สูงสุดในแต่ละช่วง ${dur(d.step)}</small></div>${lineChart([{ name: "Connections (peak)", pts: d.conn }, { name: "Established", pts: d.established }], opt)}</div>`
        + `<div><div class="nxH"><b>Queue Count / Utilization</b></div>${d.queue_supported ? lineChart([{ name: "Listening Queue (peak)", pts: d.queue }, { name: "Utilization %", pts: d.util }], opt) : `<div class="empty">${na("Unsupported", "Windows อ่านคิวได้เฉพาะพอร์ตของ IIS / HTTP.sys")} พอร์ตนี้อ่านคิวไม่ได้ (Windows ไม่มี Recv-Q / Send-Q ต่อ socket)</div>`}</div>`
        + `<div><div class="nxH"><b>Connection State</b></div>${lineChart(stNames.map(k => ({ name: STATE_NAME[k] || k, pts: d.states[k] })), opt)}</div>`
        + `<div><div class="nxH"><b>Alert History</b></div>${alertTable(d.alerts || [])}</div></div>`;
}

/* ---------------------------------------------------------------- tabs / events */
function showTab(t) {
    N.tab = t;
    try { sessionStorage.setItem("cm.nxTab", t); } catch (e) { /* ignore */ }
    document.querySelectorAll("#nxTabs button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.t === t)));
    $("pDash").hidden = t !== "dash"; $("pHosts").hidden = t !== "hosts"; $("pAlerts").hidden = t !== "alerts"; $("pHistory").hidden = t !== "history";
    if (t === "dash") renderDash();
    if (t === "hosts") { loadHosts(); if (N.host) openHost(N.host); }
    if (t === "alerts") loadAlerts();
    if (t === "history") fillHistoryHosts();
}

document.addEventListener("DOMContentLoaded", async () => {
    try {
        const me = await api("GET", "/api/me");
        N.role = me.role || "viewer";
        $("currentUsername").textContent = me.username || "?";
        $("settingsLink").hidden = !canEdit();
    } catch (e) { return; }
    $("logoutButton").onclick = async () => { await fetch("/api/logout", { method: "POST" }); location.href = "/login"; };
    try { N.every = Number(localStorage.getItem("cm.nxEvery")) || 2; } catch (e) { /* ignore */ }
    $("nxEvery").value = String(N.every);
    $("nxEvery").onchange = e => { N.every = Number(e.target.value); try { localStorage.setItem("cm.nxEvery", N.every); } catch (err) { /* ignore */ } connect(); };
    $("nxTabs").onclick = e => { const b = e.target.closest("[data-t]"); if (b) showTab(b.dataset.t); };
    $("fHost").addEventListener("change", connect);
    $("nxHosts").addEventListener("change", e => {
        const tr = e.target.closest("tr[data-web]"); if (!tr) return;
        if (e.target.matches("[data-mon]")) saveHostQuick(tr.dataset.web, { enabled: e.target.checked });
        if (e.target.matches("[data-int]")) saveHostQuick(tr.dataset.web, { interval: Number(e.target.value) });
    });
    $("nxHosts").addEventListener("click", e => { const tr = e.target.closest("tr[data-web]"); if (tr && !e.target.closest("input,select,label")) openHost(tr.dataset.web); });
    $("nxHost").addEventListener("click", e => {
        const b = e.target.closest("[data-pf]"); if (b) { syncPorts(); N.portFilter = b.dataset.pf; renderHost(); return; }
        if (e.target.id === "nxSave") saveHost();
    });
    $("nxHost").addEventListener("input", e => { if (e.target.id === "nxPfind") { syncPorts(); N.portFind = e.target.value; const pos = e.target.selectionStart; renderHost(); const i = $("nxPfind"); i.focus(); i.setSelectionRange(pos, pos); } });
    $("nxHost").addEventListener("change", e => { if (e.target.matches("[data-sel]")) e.target.closest("tr").classList.toggle("sel", e.target.checked); });
    $("nxAlSt").onclick = e => { const b = e.target.closest("[data-s]"); if (b) { N.alSt = b.dataset.s; loadAlerts(); } };
    $("nxAlHost").onchange = loadAlerts;
    $("hHost").onchange = fillHistoryPorts; $("hPort").onchange = loadHistory;
    $("hRange").onclick = e => { const b = e.target.closest("[data-r]"); if (b) { N.hRange = b.dataset.r; loadHistory(); } };
    $("hGo").onclick = loadHistory;
    const q = new URLSearchParams(location.search);
    let t = q.get("tab");
    if (!t) { try { t = sessionStorage.getItem("cm.nxTab"); } catch (e) { /* ignore */ } }
    connect();
    showTab(["dash", "hosts", "alerts", "history"].includes(t) ? t : "dash");
    if (q.get("web")) { N.host = q.get("web"); if (N.tab === "hosts") openHost(N.host); }
});
