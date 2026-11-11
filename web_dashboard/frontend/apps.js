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
    if (BAD.includes(x.status) || WARN.includes(x.status)) return x.since ? `หยุด ${ago(x.since)}` : "หยุด";
    if (x.status === "paused") return "ปิดอยู่";
    return "รอข้อมูล";
}

function tile(x) {
    const del = A.role === "admin" ? `<button type="button" class="apEdit" data-edit="${esc(x.rule)}" title="แก้ไข">✎ แก้ไข</button>`
        + `<button type="button" class="apDel" data-del="${esc(x.rule)}" title="เลิกเฝ้าดู">× ลบ</button>` : "";
    const href = x.web && x.web !== "-" && !x.web.includes(",") ? `/?web=${encodeURIComponent(x.web)}` : "#";
    return `<a class="apT ${cls(x.status)}" href="${href}"><span class="apDot"></span><div class="apMain"><b title="${esc(x.what)}">${esc(x.name)}</b>`
        + `<small>${esc(x.web)} · ${esc(TYPE_TH[x.type] || x.type)}${["http_local", "port_listen"].includes(x.metric) ? " · บนเครื่อง" : ["http_url", "tcp_port"].includes(x.metric) ? " · จาก central" : ""}</small><p>${esc(x.message)}</p></div>`
        + `<span class="apAge">${esc(ageText(x))}</span>${del}</a>`;
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
        A.local = !["http_url", "tcp_port"].includes(edit.metric);
        document.querySelectorAll("#apLocal button").forEach(b => b.setAttribute("aria-pressed", String((b.dataset.l === "1") === A.local)));
        setType(edit.type);
    } else {
        $("apVal").value = ""; $("apName").value = "";
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
        $("settingsLink").hidden = A.role !== "admin";
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
    $("apGrid").addEventListener("click", e => {
        const ed = e.target.closest("[data-edit]");
        if (ed) { e.preventDefault(); openForm(A.items.find(i => i.rule === ed.dataset.edit)); return; }
        const d = e.target.closest("[data-del]"); if (!d) return;
        e.preventDefault(); remove(d.dataset.del);
    });
    load();
    setInterval(() => { if (!document.hidden) load(); }, 30000);
});
