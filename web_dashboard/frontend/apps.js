/* =====================================================================
   apps.js - หน้า Apps & Web (apps.html)
     ภาพรวมบนสุด: ทุกอย่างทำงานปกติ / มีปัญหา N รายการ
     การ์ดละ 1 รายการ: จุดสี · ชื่อ · เครื่อง · ข้อความล่าสุด · ทำงาน / หยุด มานานเท่าไหร่  (มีปัญหาขึ้นก่อน)
     admin: + เพิ่ม (โปรแกรม / เว็บ / พอร์ต / Service) และ × ลบ   -> สร้าง / ลบ Custom alert rule ให้
   ===================================================================== */
"use strict";
const $ = id => document.getElementById(id);
const A = { role: "viewer", items: [], filter: "all", find: "", type: "program", targets: [], at: 0 };
const TYPE_TH = { program: "โปรแกรม", service: "Service", web: "เว็บ", port: "พอร์ต" };
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
    const del = A.role === "admin" ? `<button type="button" class="apDel" data-del="${esc(x.rule)}" title="เลิกเฝ้าดู">× ลบ</button>` : "";
    const href = x.web && x.web !== "-" && !x.web.includes(",") ? `/?web=${encodeURIComponent(x.web)}` : "#";
    return `<a class="apT ${cls(x.status)}" href="${href}"><span class="apDot"></span><div class="apMain"><b title="${esc(x.what)}">${esc(x.name)}</b>`
        + `<small>${esc(x.web)} · ${esc(TYPE_TH[x.type] || x.type)}</small><p>${esc(x.message)}</p></div>`
        + `<span class="apAge">${esc(ageText(x))}</span>${del}</a>`;
}

function render() {
    const items = A.items;
    const bad = items.filter(x => BAD.includes(x.status) || WARN.includes(x.status));
    const hero = $("apHero");
    hero.className = "card apHero " + (!items.length ? "" : bad.some(x => BAD.includes(x.status)) ? "bad" : bad.length ? "wr" : "ok");
    $("apHeadline").textContent = !items.length ? "ยังไม่ได้เฝ้าดูโปรแกรมหรือเว็บ"
        : bad.length ? `มีปัญหา ${bad.length} จาก ${items.length} รายการ` : `ทำงานปกติทั้งหมด ${items.length} รายการ`;
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
    service: ["ชื่อ service", "เช่น MSSQLSERVER, W3SVC", "ผิดปกติเมื่อ Windows service นี้ไม่ได้ Running"],
};
async function suggest() {
    const dl = $("apSuggest"); dl.textContent = "";
    const web = $("apWeb").value;
    if (!web || !["program", "port"].includes(A.type)) return;
    try {
        const d = await api("GET", `/api/host/${encodeURIComponent(web)}/software`);
        const vals = A.type === "program" ? (d.running || []).map(p => p.name) : (d.ports || []).map(p => `${p.port}`);
        [...new Set(vals)].slice(0, 300).forEach(v => { const o = document.createElement("option"); o.value = v; dl.append(o); });
    } catch (e) { /* ไม่มีข้อมูล = ไม่แนะนำ */ }
}
function setType(t) {
    A.type = t;
    document.querySelectorAll("#apType button").forEach(b => b.setAttribute("aria-pressed", String(b.dataset.t === t)));
    const [label, ph, hint] = HINT[t];
    $("apValLabel").textContent = label; $("apVal").placeholder = ph; $("apHint").textContent = hint;
    $("apVal").inputMode = t === "port" ? "numeric" : "text";
    suggest();
}
async function openForm() {
    $("apForm").hidden = false; $("apMsg").textContent = "";
    if (!A.targets.length) {
        try { A.targets = ((await api("GET", "/api/alert-rules")).targets || []); } catch (e) { A.targets = []; }
    }
    const sel = $("apWeb"); sel.textContent = "";
    A.targets.forEach(t => { const o = document.createElement("option"); o.value = t.name; o.textContent = t.name + (t.os === "windows" ? " (Windows)" : ""); sel.append(o); });
    setType(A.type);
    $("apVal").focus();
}
async function save(ev) {
    ev.preventDefault();
    $("apMsg").textContent = ""; $("apSave").disabled = true;
    try {
        await api("POST", "/api/apps", { type: A.type, web: $("apWeb").value, value: $("apVal").value.trim(), name: $("apName").value.trim() });
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
    $("apAddBtn").onclick = openForm;
    $("apCancel").onclick = () => { $("apForm").hidden = true; };
    $("apType").addEventListener("click", e => { const b = e.target.closest("[data-t]"); if (b) setType(b.dataset.t); });
    $("apWeb").addEventListener("change", suggest);
    $("apForm").addEventListener("submit", save);
    $("apGrid").addEventListener("click", e => {
        const d = e.target.closest("[data-del]"); if (!d) return;
        e.preventDefault(); remove(d.dataset.del);
    });
    load();
    setInterval(() => { if (!document.hidden) load(); }, 30000);
});
