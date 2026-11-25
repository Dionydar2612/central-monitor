/* =====================================================================
   settings.js - ตรรกะของหน้า Settings (settings.html)
   =====================================================================
   ทำอะไร   : แสดงรายการเครื่องจาก puller.yaml, ฟอร์ม เพิ่ม/แก้/ลบ, สแกน SSH host key
   เรียก API : GET/POST /api/targets, PUT/DELETE /api/targets/<ชื่อ>, POST /api/targets/host-key,
              POST /api/targets/<ชื่อ>/deploy-key
              (logic ฝั่งเซิร์ฟเวอร์ + การตรวจค่าอยู่ที่ backend/targets_service.py)
   ความปลอดภัย: ข้อความจากเซิร์ฟเวอร์ใส่ผ่าน textContent เท่านั้น (ไม่ใช้ innerHTML กับข้อมูลผู้ใช้)
   ===================================================================== */
"use strict";

const $ = id => document.getElementById(id);
const S = { targets: [], publicKey: null, editing: null, scanned: [], hostKeyKnown: false, checks: [], editingCheck: null, me: null };

const TH_KINDS = ["cpu", "memory", "disk", "disk_latency", "packet_loss", "net_latency", "time_offset"];

/* ---------- ตัวช่วย ---------- */

function el(tag, props, ...kids) {
    const n = document.createElement(tag);
    Object.entries(props || {}).forEach(([k, v]) => {
        if (k === "class") n.className = v;
        else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
        else n.setAttribute(k, v);
    });
    kids.flat().forEach(c => n.append(c));
    return n;
}

async function api(method, url, body) {
    const opt = { method, credentials: "same-origin", cache: "no-store", headers: {} };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    const r = await fetch(url, opt);
    if (r.status === 401) { window.location.href = "/login"; throw new Error("auth"); }
    let data = {};
    try { data = await r.json(); } catch (e) { /* ไม่มี body */ }
    if (!r.ok) { const err = new Error(data.error || r.statusText); err.fields = data.fields || null; err.status = r.status; throw err; }
    return data;
}

function say(id, text, kind) {
    const n = $(id); n.textContent = text || ""; n.className = "st-msg" + (kind ? " " + kind : "");
}

/* ---------- รายการเครื่อง ---------- */

async function loadTargets() {
    try {
        const d = await api("GET", "/api/targets");
        S.targets = d.targets; S.publicKey = d.public_key;
        renderTable(); fillGroups(); formRestore();
    } catch (e) { if (e.message !== "auth") say("listMsg", "โหลดรายการไม่สำเร็จ: " + e.message, "bad"); }
}

function renderTable() {
    const tb = $("tbody"); tb.textContent = "";
    if (!S.targets.length) {
        tb.append(el("tr", {}, el("td", { colspan: "9", class: "empty" }, "ยังไม่มีเครื่อง กด + Add target")));
        return;
    }
    S.targets.forEach(t => {
        if (t.type === "agent") {               /* เครื่อง Windows แบบเดิม (เลิกรองรับแล้ว) -> ลบได้อย่างเดียว */
            tb.append(el("tr", {},
                el("td", {}, el("b", {}, t.name)),
                el("td", { class: "st-mut" }, "Windows (เลิกรองรับ)"),
                el("td", {}, t.group || "-"),
                el("td", {}, t.host || "-"),
                el("td", {}, "-"), el("td", {}, "-"), el("td", {}, "-"),
                el("td", { class: "wr" }, "ไม่ได้ตรวจแล้ว"),
                el("td", {}, isOp() ? "" : el("button", { class: "st-btn sm danger", onclick: () => removeTarget(t) }, "Delete"))));
            return;
        }
        if (t.type === "winrm") {               /* Windows แบบไม่ลง agent (WinRM -> JEA) */
            const wp = t.winrm.port && Number(t.winrm.port) !== 5986 ? ":" + t.winrm.port : "";
            const cert = t.winrm.thumbprint || t.pinned_thumbprint;
            const ready = t.has_password && cert;
            tb.append(el("tr", {},
                el("td", {}, el("b", {}, t.name)),
                el("td", {}, "Windows (WinRM)"),
                el("td", {}, t.group || "-"),
                el("td", {}, t.host + wp),
                el("td", {}, t.winrm.user || "-"),
                el("td", {}, String(t.logs.length) + " + Event log"),
                el("td", { class: ready ? "ok" : "wr", title: cert || "" }, ready ? "✓ cert + password"
                    : t.has_password ? "⏳ รอติดตั้ง / เชื่อมต่อครั้งแรก" : "⚠ ยังไม่ได้ติดตั้ง"),
                el("td", { class: t.paused ? "wr" : "ok", title: t.paused ? `${t.pause_reason || ""}${t.paused_by ? " · " + t.paused_by : ""}` : "" }, t.paused ? `⏸ Paused${t.pause_reason ? ": " + t.pause_reason : ""}` : "Active"),
                el("td", {}, el("div", { class: "st-actions" }, rowActions(t)))));
            return;
        }
        const port = t.ssh.port && Number(t.ssh.port) !== 22 ? ":" + t.ssh.port : "";
        tb.append(el("tr", {},
            el("td", {}, el("b", {}, t.name)),
            el("td", {}, "Linux (SSH)"),
            el("td", {}, t.group || "-"),
            el("td", {}, t.host + port),
            el("td", {}, t.ssh.user || "-"),
            el("td", {}, String(t.logs.length)),
            el("td", { class: t.host_key_known ? "ok" : "wr" }, t.host_key_known ? "✓ known" : "⚠ missing"),
            el("td", { class: t.paused ? "wr" : "ok", title: t.paused ? `${t.pause_reason || ""}${t.paused_by ? " · " + t.paused_by : ""}` : "" }, t.paused ? `⏸ Paused${t.pause_reason ? ": " + t.pause_reason : ""}` : "Active"),
            el("td", {}, el("div", { class: "st-actions" }, rowActions(t)))));
    });
}

const isOp = () => S.role === "operator";
/* ปุ่มท้ายแถว: operator = Pause + แก้การแจ้งเตือน/Log · admin = ทุกปุ่ม */
function rowActions(t) {
    const out = [el("button", { class: "st-btn sm", onclick: () => pauseTarget(t) }, t.paused ? "Resume" : "Pause"),
                 el("button", { class: "st-btn sm", onclick: () => openForm(t) }, isOp() ? "การแจ้งเตือน / Log" : "Edit")];
    /* Deploy key / Install command อยู่ในฟอร์ม Edit (แท็บ การเชื่อมต่อ) ไม่แสดงในรายการ */
    if (!isOp()) out.push(el("button", { class: "st-btn sm danger", onclick: () => removeTarget(t) }, "Delete"));
    return out;
}

/* Pause ต้องมีเหตุผล · ช่วงที่ Pause ไม่นับเป็น downtime */
function setPauseFields(t) {
    $("f_paused").checked = !!(t && t.paused);
    $("f_pause_reason").value = (t && t.pause_reason) || "";
    $("f_paused").dataset.at = (t && t.paused_at) || "";
    $("f_paused").dataset.by = (t && t.paused_by) || "";
    $("f_pause_reason").closest(".st-f").hidden = !$("f_paused").checked;
}
function pauseFields() {
    if (!$("f_paused").checked) return {};
    return { pause_reason: $("f_pause_reason").value.trim(), paused_at: Number($("f_paused").dataset.at) || null, paused_by: $("f_paused").dataset.by || "" };
}
async function pauseTarget(t) {
    let reason = "";
    if (!t.paused) {
        reason = (prompt(`Pause ${t.name}\nเหตุผล (จำเป็น) เช่น ปิดซ่อมบำรุง / ย้ายเครื่อง\nช่วงที่ Pause ไม่นับเป็น downtime`) || "").trim();
        if (!reason) return;
    }
    try {
        await api("PUT", `/api/targets/${encodeURIComponent(t.name)}/pause`, { paused: !t.paused, reason });
        say("listMsg", `${t.name}: ${t.paused ? "Resumed (ดึงทันที)" : "Paused (หยุดดึง log / สถานะ)"}`, "ok");
        await loadTargets();
    } catch (e) { if (e.message !== "auth") say("listMsg", "เปลี่ยนสถานะไม่สำเร็จ: " + e.message, "bad"); }
}

/* รายชื่อกลุ่มที่มีอยู่ (จากเครื่อง + checks) ให้เลือกในช่อง group */
function fillGroups() {
    const set = new Set();
    [...S.targets, ...S.checks].forEach(x => {
        const parts = String(x.group || "").split("/").filter(Boolean);
        parts.forEach((_, i) => set.add(parts.slice(0, i + 1).join("/")));
    });
    const dl = $("groupList"); dl.textContent = "";
    [...set].sort().forEach(g => dl.append(el("option", { value: g })));
    S.groupSet = set;
    ["f_group", "c_group"].forEach(groupPicker);
}

/* ตัวเลือกกลุ่มแบบต้นไม้: คลิกชื่อ = เลือก · "+ ย่อย" = เติม "กลุ่ม/" ให้พิมพ์ชื่อกลุ่มย่อยต่อ */
function groupPicker(id) {
    const inp = $(id), box = $(id + "_pick"), crumb = $(id + "_crumb");
    if (!inp || !box) return;
    const paint = () => {
        const cur = inp.value.trim().replace(/\/+$/, "");
        const parts = inp.value.split("/").map(p => p.trim()).filter(Boolean);
        crumb.textContent = "";
        if (parts.length) {
            crumb.append(...parts.flatMap((p, i) => [i ? " › " : "", el("b", {}, p)]),
                ` (ชั้น ${parts.length}${parts.length > 10 ? " · เกิน 10 ชั้น" : ""})`);
        }
        box.textContent = "";
        [...(S.groupSet || [])].sort().forEach(g => {
            const segs = g.split("/"), depth = segs.length - 1;
            const name = el("span", { class: "gpn", title: g }, el("i", {}, depth ? "└" : "▸"), segs[depth]);
            const add = el("button", { type: "button", class: "gpa", title: `สร้างกลุ่มย่อยใต้ ${g}` }, "+ ย่อย");
            const row = el("div", { class: "gpr" + (g === cur ? " on" : "") }, name, add);
            row.style.paddingLeft = (4 + depth * 16) + "px";
            name.onclick = () => { inp.value = g; paint(); inp.dispatchEvent(new Event("input")); };
            add.onclick = () => { inp.value = g + "/"; paint(); inp.focus(); };
            box.append(row);
        });
    };
    if (!inp.dataset.gpick) { inp.dataset.gpick = "1"; inp.addEventListener("input", () => { if (!inp._gpPainting) { inp._gpPainting = 1; paint(); inp._gpPainting = 0; } }); }
    inp._gpPaint = paint;
    paint();
}

async function removeTarget(t) {
    const win = t.type === "winrm" ? `\n\nเครื่อง Windows: ปุ่มนี้ลบออกจาก central อย่างเดียว ไม่ได้ถอนออกจากเครื่อง` +
        `\nถ้าต้องการถอน user cm-monitor และทุกอย่างบนเครื่อง ให้กด Cancel แล้วไปที่ Edit > การเชื่อมต่อ > "ถอนการติดตั้งออกจากเครื่อง"` +
        `\n(หรือรัน remove-cm-monitor.bat บนเครื่อง)` : "";
    if (!confirm(`ลบเครื่อง "${t.name}" ออกจาก puller.yaml ?\n(ข้อมูล log ที่เก็บไว้แล้วไม่ถูกลบ)${win}`)) return;
    try { await api("DELETE", "/api/targets/" + encodeURIComponent(t.name)); say("listMsg", `ลบ ${t.name} แล้ว`, "ok"); await loadTargets(); }
    catch (e) { say("listMsg", "ลบไม่สำเร็จ: " + e.message, "bad"); }
}

/* ---------- ฟอร์ม ---------- */

function logRow(item) {
    item = item || { name: "application", path: "", type: "application" };
    const mk = (cls, label, key, val, ph, list) => {
        const i = el("input", { "data-k": key, value: val, placeholder: ph || "", autocomplete: "off" });
        if (list) i.setAttribute("list", list);
        return el("div", { class: "st-f " + cls }, el("label", {}, label), i, el("div", { class: "st-err" }));
    };
    const row = el("div", { class: "st-logrow" },
        mk("n", "ชนิด log (parser)", "name", item.name, "application", "logNames"),
        mk("p", "path", "path", item.path, formType() === "winrm" ? "D:\\App\\logs\\*.log" : "/var/log/myapp/app.log  หรือ  journal:nginx.service"),
        mk("t", "ชื่อเรียก", "type", item.type || "", "ชื่อที่แสดงในหน้า Logs", "logLabels"),
        el("button", { class: "st-btn sm danger", type: "button", onclick: e => e.target.closest(".st-logrow").remove() }, "✕"),
        el("div", { class: "st-logst" }));
    row.dataset.saved = item.path ? `${item.name}\u0000${item.path}` : "";
    return row;
}

/* สถานะของแต่ละ log: ดึงล่าสุดเมื่อไหร่ (checkpoints) + ตรวจสด (puller SSH ไปดูว่ามีไฟล์ / อ่านได้ไหม) */
function logStatusText(r) {
    const c = r.check, out = [];
    if (c) {
        if (c.ok && c.journal_entries != null) out.push(["ok", "✓ อ่าน journal ได้"]);
        else if (c.ok) out.push(["ok", `✓ พบ ${c.files.length} ไฟล์ อ่านได้ ${c.files.filter(f => f.readable).length}`
            + ` · ${c.files.slice(0, 3).map(f => f.path.split("/").pop() + " " + fmtBytes(f.size)).join(", ")}${c.files.length > 3 ? " …" : ""}`]);
        if (c.problem) out.push([c.ok ? "wr" : "bad", (c.ok ? "! " : "✕ ") + c.problem]);
    }
    out.push(r.last_pull ? ["", `ดึงล่าสุด ${new Date(r.last_pull).toLocaleString()} · ${r.files} ไฟล์`]
        : c && !c.ok ? ["", "ยังไม่เคยดึง"] : ["wr", "ยังไม่เคยดึง (รอรอบถัดไป ~2 นาที)"]);
    return out;
}
function fmtBytes(n) { n = Number(n) || 0; return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : n >= 1024 ? (n / 1024).toFixed(0) + " KB" : n + " B"; }
async function loadLogStatus(check) {
    const name = S.editing;
    if (!name) return;
    const btn = $("logCheck");
    if (check) { btn.disabled = true; btn.textContent = "กำลังตรวจ…"; }
    try {
        const d = await api("GET", `/api/targets/${encodeURIComponent(name)}/log-status${check ? "?check=1" : ""}`);
        if (S.editing !== name) return;
        const map = new Map((d.logs || []).map(r => [`${r.name}\u0000${r.path}`, r]));
        [...$("logs").children].forEach(row => {
            const box = row.querySelector(".st-logst"), r = map.get(row.dataset.saved);
            box.textContent = "";
            if (!row.dataset.saved) { box.append(el("span", { class: "wr" }, "ยังไม่ได้บันทึก — กด Save แล้วกดตรวจ")); return; }
            if (r) logStatusText(r).forEach(([cls, txt]) => box.append(el("span", { class: cls }, txt)));
        });
        say("logsErr", d.check_error || "");
    } catch (e) { if (e.message !== "auth") say("logsErr", "ตรวจไม่ได้: " + e.message); }
    finally { btn.disabled = false; btn.textContent = "ตรวจ log"; }
}

/* ฟอร์มเครื่อง: แท็บ ทั่วไป / การเชื่อมต่อ / Log / การแจ้งเตือน */
function formSave(tab) {
    try {
        if (S.editing) sessionStorage.setItem("cm.form", JSON.stringify({ name: S.editing, tab }));
        else sessionStorage.removeItem("cm.form");
    } catch (e) { /* ignore */ }
}
/* reload แล้วเปิดฟอร์มเครื่องเดิม + แท็บเดิม (ครั้งแรกที่โหลดรายการเครื่อง) */
function formRestore() {
    if (S.restored) return;
    S.restored = true;
    let st = null;
    try { st = JSON.parse(sessionStorage.getItem("cm.form") || "null"); } catch (e) { /* ignore */ }
    const t = st && S.targets.find(x => x.name === st.name);
    if (!t) return;
    openForm(t);
    if (st.tab && !document.querySelector(`#formCard .ftabs button[data-t="${st.tab}"]`).hidden) formTab(st.tab);
}

function formTab(name) {
    formSave(name);
    document.querySelectorAll("#formCard .ftabs button").forEach(b => b.classList.toggle("on", b.dataset.t === name));
    document.querySelectorAll("#formCard .fpane").forEach(p => { p.hidden = p.dataset.p !== name; });
}

/* ช่องว่าง = ค่าเริ่มต้น: แสดงค่าที่ระบบใช้อยู่เป็นตัวจางในช่อง */
function formDefaults() {
    const d = AL || {};
    const th = d.thresholds || {};
    TH_KINDS.forEach(k => ["warning", "error"].forEach(lv => {
        const v = th[k] ? th[k][lv] : undefined;
        $(`th_${k}_${lv}`).placeholder = v != null ? String(v) : "ค่าเริ่มต้น";
    }));
    $("fa_min").placeholder = d.alert_minutes != null ? String(d.alert_minutes) : "3";
    $("fa_hyst").placeholder = d.hysteresis != null ? String(d.hysteresis) : "2";
    $("fa_bn").options[0].textContent = `ค่าเริ่มต้น (${d.bottleneck === false ? "ไม่ตรวจ" : "ตรวจ"})`;
}

/* ตาราง Priority / เวลาแจ้งเฉพาะ sensor: targets[].alert.sensors {"<sensor>": {priority, minutes}} */
const PRIO_OPTS = [["", "ของเครื่อง"], ["5", "★★★★★ 5"], ["4", "★★★★ 4"], ["3", "★★★ 3"], ["2", "★★ 2"], ["1", "★ 1"]];
function sensRow(key, v) {
    v = v || {};
    const tr = el("tr", {});
    tr.innerHTML = `<td><input data-k="key" list="sensKeys" autocomplete="off" placeholder="เช่น cpu, disk:C:, service:Spooler"></td>`
        + `<td><select data-k="priority">${PRIO_OPTS.map(([x, l]) => `<option value="${x}">${l}</option>`).join("")}</select></td>`
        + `<td><input data-k="minutes" type="number" min="0.5" max="1440" step="0.5" inputmode="decimal" placeholder="ของเครื่อง"></td>`
        + `<td><button type="button" class="st-btn sm" data-del title="ลบ">×</button></td>`;
    tr.querySelector('[data-k="key"]').value = key || "";
    tr.querySelector('[data-k="priority"]').value = v.priority ? String(v.priority) : "";
    tr.querySelector('[data-k="minutes"]').value = v.minutes ?? "";
    tr.querySelector("[data-del]").onclick = () => tr.remove();
    /* warning / error / วิธีแจ้ง ตั้งที่หน้า sensor -> เก็บไว้ไม่ให้หายตอนบันทึกฟอร์ม */
    tr._extra = Object.fromEntries(["warning", "error", "notify"].filter(k => v[k] != null && v[k] !== "").map(k => [k, v[k]]));
    return tr;
}
function sensRows(map) {
    const tb = $("fa_sens"); if (!tb) return;
    tb.innerHTML = "";
    Object.entries(map || {}).forEach(([k, v]) => tb.append(sensRow(k, v)));
}
function readSensRows() {
    const out = {};
    document.querySelectorAll("#fa_sens tr").forEach(tr => {
        const k = tr.querySelector('[data-k="key"]').value.trim();
        if (!k) return;
        const p = tr.querySelector('[data-k="priority"]').value, m = tr.querySelector('[data-k="minutes"]').value.trim();
        const x = tr._extra || {};
        if (p || m || Object.keys(x).length) out[k] = { ...x, ...(p ? { priority: Number(p) } : {}), ...(m ? { minutes: m } : {}) };
    });
    return out;
}

function fillAlertFields(t) {
    const a = (t && t.alert) || {};
    sensRows(a.sensors || {});                 /* Priority / เวลาเฉพาะ sensor (ตั้งที่หน้า sensor ได้ด้วย) */
    $("fa_min").value = a.minutes ?? ""; $("fa_hyst").value = a.hysteresis ?? "";
    $("fa_bn").value = a.bottleneck === true ? "1" : a.bottleneck === false ? "0" : "";
    /* sensor ที่ปิดไว้: ติ๊กกล่องที่ครบทุกชื่อ ที่เหลือไปช่อง "อื่น ๆ" */
    const ds = new Set(a.disabled_sensors || []), used = new Set();
    document.querySelectorAll("#fa_ds [data-ds]").forEach(cb => {
        const keys = cb.dataset.ds.split(",");
        cb.checked = keys.every(k => ds.has(k));
        if (cb.checked) keys.forEach(k => used.add(k));
    });
    $("fa_ds_more").value = [...ds].filter(k => !used.has(k)).join(", ");
}

function readAlertFields() {
    const b = $("fa_bn").value;
    const ds = [];
    document.querySelectorAll("#fa_ds [data-ds]:checked").forEach(cb => ds.push(...cb.dataset.ds.split(",")));
    $("fa_ds_more").value.split(",").map(x => x.trim()).filter(Boolean).forEach(x => ds.push(x));
    return { minutes: $("fa_min").value.trim(), hysteresis: $("fa_hyst").value.trim(), bottleneck: b === "" ? null : b === "1",
             disabled_sensors: [...new Set(ds)], sensors: readSensRows() };
}

function openForm(t) {
    S.editing = t ? t.name : null; S.scanned = []; S.hostKeyKnown = !!(t && t.host_key_known);
    fillAlertFields(t); formDefaults();
    document.querySelectorAll('#formCard .ftabs button[data-t="general"], #formCard .ftabs button[data-t="conn"]').forEach(b => { b.hidden = isOp(); });
    formTab(isOp() ? "alert" : "general");
    $("formTitle").textContent = t ? `Edit target: ${t.name}` : "Add target";
    $("f_name").value = t ? t.name : ""; $("f_name").disabled = !!t;
    $("f_group").value = t ? (t.group || "") : ""; $("f_group")._gpPaint?.();
    setPauseFields(t);
    $("f_prio").value = String((t && t.priority) || 3);
    TH_KINDS.forEach(k => ["warning", "error"].forEach(lv => {
        const v = t && t.thresholds && t.thresholds[k] ? t.thresholds[k][lv] : "";
        $(`th_${k}_${lv}`).value = v ?? "";
    }));
    $("f_host").value = t ? t.host : "";
    $("f_type").value = t && t.type === "winrm" ? "winrm" : "ssh";
    $("f_type").disabled = !!t;                    /* เปลี่ยนชนิดของเครื่องที่บันทึกแล้วไม่ได้ (ลบแล้วเพิ่มใหม่) */
    applyType();
    const w = (t && t.winrm) || {};
    $("w_port").value = w.port || 5986;
    $("w_user").value = w.user || "cm-monitor";
    $("w_thumb").value = w.thumbprint || "";
    $("w_evlogs").value = (w.event_logs || ["System", "Application"]).join(", ");
    $("w_pass").value = "";
    $("w_pass").placeholder = t && t.has_password ? "•••••••• บันทึกไว้แล้ว (เว้นว่าง = ใช้รหัสเดิม)" : "เว้นว่าง = ระบบตั้งให้ตอนติดตั้ง";
    $("w_thumb_hint").textContent = w.thumbprint ? "central ไม่ส่งรหัสผ่านถ้าใบรับรองไม่ตรง (กันเครื่องปลอม)"
        : t && t.pinned_thumbprint ? `จำใบรับรองไว้แล้ว: ${t.pinned_thumbprint}` : "เว้นว่าง = จำใบรับรองตอนเชื่อมต่อสำเร็จครั้งแรก";
    $("wi_pass").value = ""; say("wi_msg", ""); $("wi_log").hidden = true; $("wi_log").textContent = "";
    $("wu_box").hidden = !t; $("wu_del").checked = true;
    $("w_adv").open = !!(t && w.thumbprint && !t.pinned_thumbprint && !t.has_password);
    if (!$("w_central").value) $("w_central").value = /^\d+\.\d+\.\d+\.\d+$/.test(location.hostname) ? location.hostname : "";
    say("w_msg", "");
    $("f_port").value = t && t.ssh ? (t.ssh.port || 22) : 22;
    $("f_user").value = t && t.ssh ? t.ssh.user : "central-pull";
    if (!t || t.type !== "winrm") {
    $("f_job").value = t ? (t.prometheus.job || "") : "";
    $("f_inst").value = t ? (t.prometheus.instance || "") : "";
    $("f_scheme").value = t && t.prometheus ? (t.prometheus.scheme || "http") : "http";
    $("f_mpath").value = t && t.prometheus ? (t.prometheus.metrics_path || "") : "";
    }
    /* ชื่อเรียกที่เคยตั้งไว้ (ทุกเครื่อง) ให้เลือกซ้ำได้ ชื่อใหม่พิมพ์เองได้ */
    const dlb = $("logLabels");
    if (dlb) { dlb.textContent = ""; [...new Set(S.targets.flatMap(x => (x.logs || []).map(l => l.type)).filter(Boolean))].sort().forEach(v => dlb.append(el("option", { value: v }))); }
    const logs = $("logs"); logs.textContent = "";
    (t && t.logs.length ? t.logs : [null]).forEach(l => logs.append(logRow(l)));
    if (t) loadLogStatus(false);
    clearErrors(); say("formMsg", ""); say("deployMsg", "");
    $("dp_admin").value = ""; $("dp_pass").value = "";
    $("hkKeys").textContent = "";
    renderHostKeyStatus(); renderSetup();
    if (isOp() && t) $("formTitle").textContent = `การแจ้งเตือน / Log: ${t.name}`;
    $("formCard").hidden = false;
    $("formCard").scrollIntoView({ behavior: "smooth", block: "start" });
    (t ? $("f_host") : $("f_name")).focus();
}

function closeForm() { $("formCard").hidden = true; S.editing = null; formSave(null); }

function clearErrors() {
    document.querySelectorAll("#formCard .st-err").forEach(n => n.textContent = "");
    document.querySelectorAll("#formCard .has-err").forEach(n => n.classList.remove("has-err"));
}

function showErrors(fields) {
    const general = [];
    Object.entries(fields || {}).forEach(([k, msg]) => {
        let box = document.querySelector(`#formCard [data-f="${CSS.escape(k)}"]`);
        const m = k.match(/^logs\.(\d+)\.(name|path|type)$/);
        if (m) {
            const row = $("logs").children[Number(m[1])];
            box = row && row.querySelector(`[data-k="${m[2]}"]`).closest(".st-f");
        }
        if (box) { box.classList.add("has-err"); box.querySelector(".st-err").textContent = msg; }
        else general.push(msg);
    });
    const first = document.querySelector("#formCard .has-err");
    const pane = first && first.closest(".fpane");
    if (pane) formTab(pane.dataset.p);
    return general.join(" · ");
}

function readForm() {
    const logs = [...$("logs").children].map(r => ({
        name: r.querySelector('[data-k="name"]').value.trim(),
        path: r.querySelector('[data-k="path"]').value.trim(),
        type: r.querySelector('[data-k="type"]').value.trim(),
    })).filter(l => formType() !== "winrm" || l.path);
    return {
        name: $("f_name").value.trim(),
        group: $("f_group").value.trim(),
        priority: Number($("f_prio").value) || 3,
        paused: $("f_paused").checked, ...pauseFields(),
        thresholds: Object.fromEntries(TH_KINDS.map(k => [k, { warning: $(`th_${k}_warning`).value.trim(), error: $(`th_${k}_error`).value.trim() }])),
        alert: readAlertFields(),
        host: $("f_host").value.trim(),
        type: formType(),
        ...(formType() === "winrm" ? {
            winrm: { port: $("w_port").value.trim() || 5986, user: $("w_user").value.trim() || "cm-monitor",
                     thumbprint: $("w_thumb").value.trim(), event_logs: $("w_evlogs").value.split(",").map(x => x.trim()).filter(Boolean) },
            password: $("w_pass").value,
        } : {}),
        ssh: { port: $("f_port").value.trim() || 22, user: $("f_user").value.trim() },
        prometheus: { job: $("f_job").value.trim(), instance: $("f_inst").value.trim(),
                      scheme: $("f_scheme").value, metrics_path: $("f_mpath").value.trim() },
        logs,
    };
}

/* ---------- Windows (WinRM ไม่ลง agent) ---------- */

function formType() { return $("f_type") ? $("f_type").value : "ssh"; }
function applyType() { $("formCard").dataset.type = formType(); }

function winrmBody() {
    return { host: $("f_host").value.trim(), port: $("w_port").value.trim() || 5986, user: $("w_user").value.trim() || "cm-monitor",
             password: $("w_pass").value, thumbprint: $("w_thumb").value.trim(), name: S.editing || "" };
}

async function scanCert() {
    const b = $("w_scan"); b.disabled = true; say("w_msg", "กำลังอ่านใบรับรอง...", "");
    try {
        const d = await api("POST", "/api/targets/winrm-cert", winrmBody());
        const old = $("w_thumb").value.replace(/[^0-9a-f]/gi, "").toUpperCase();
        $("w_thumb").value = d.thumbprint;
        say("w_msg", old && old !== d.thumbprint
            ? `⚠ ใบรับรองเปลี่ยน (เดิม ${old.slice(0, 8)}…) ตรวจกับค่าที่ setup-cm-monitor.bat แสดงก่อนบันทึก`
            : `Thumbprint ${d.thumbprint} — ตรวจให้ตรงกับที่ setup-cm-monitor.bat แสดง`, old && old !== d.thumbprint ? "bad" : "ok");
    } catch (e) { if (e.message !== "auth") say("w_msg", "อ่านไม่ได้: " + (e.fields ? Object.values(e.fields).join(" · ") : e.message), "bad"); }
    finally { b.disabled = false; }
}

async function testWinrm() {
    const b = $("w_test"); b.disabled = true; say("w_msg", "กำลังทดสอบ (สูงสุด ~30 วินาที)...", "");
    try {
        const d = await api("POST", "/api/targets/winrm-test", winrmBody());
        if (d.ok) say("w_msg", `✓ เชื่อมต่อได้ ${d.computer || ""} · ${d.os || ""} (build ${d.build || "?"}) · PowerShell ${d.ps || "?"} · module ${d.module || "?"} · ${d.seconds}s`, "ok");
        else say("w_msg", "✗ " + (d.error || "เชื่อมต่อไม่ได้"), "bad");
    } catch (e) { if (e.message !== "auth") say("w_msg", "✗ " + (e.data && e.data.error || e.message), "bad"); }
    finally { b.disabled = false; }
}

/* ติดตั้ง / ดาวน์โหลดต้องมีเครื่องในระบบก่อน: บันทึกฟอร์มให้เงียบ ๆ (ฟอร์มยังเปิดอยู่) คืน true ถ้าสำเร็จ */
async function ensureSaved() {
    clearErrors(); say("formMsg", "");
    const body = readForm();
    try {
        if (S.editing) await api("PUT", "/api/targets/" + encodeURIComponent(S.editing), body);
        else await api("POST", "/api/targets", body);
    } catch (e) {
        if (e.message === "auth") return false;
        const general = showErrors(e.fields);
        say("formMsg", general || (e.fields ? "กรุณาแก้ช่องที่มีข้อความสีแดงก่อน" : "บันทึกไม่สำเร็จ: " + e.message), "bad");
        return false;
    }
    if (!S.editing) {
        S.editing = body.name;
        $("f_name").disabled = true; $("f_type").disabled = true;
        $("formTitle").textContent = `Edit target: ${body.name}`;
        formSave("conn");
    }
    $("w_pass").value = "";
    try { const d = await api("GET", "/api/targets"); S.targets = d.targets; renderTable(); } catch (e) { /* ignore */ }
    return true;
}

async function downloadSetup() {
    const central = $("w_central").value.trim();
    if (!/^\d+\.\d+\.\d+\.\d+$/.test(central) && !central.includes(":")) { say("w_msg", "ใส่ IP ของ central ก่อน", "bad"); $("w_central").focus(); return; }
    if (!await ensureSaved()) return;
    const paths = [...$("logs").children].map(r => r.querySelector('[data-k="path"]').value.trim()).filter(Boolean).join(";");
    const q = new URLSearchParams({ central, logpaths: paths, name: S.editing });
    location.href = "/api/targets/windows-setup.zip?" + q.toString();
    say("w_msg", "ดาวน์โหลดแล้ว: คัดลอกไปเครื่อง Windows → แตก zip → ดับเบิลคลิก setup-cm-monitor.bat (ไม่ต้องกรอกอะไรกลับมา) · ลบ zip หลังติดตั้ง", "ok");
}

/* ติดตั้ง / ถอน ทำเบื้องหลังที่ puller: ถามสถานะทุก 3 วินาที คืนผลสุดท้าย */
async function pollJob(name) {
    const started = Date.now();
    for (;;) {
        await new Promise(r => setTimeout(r, 3000));
        let d;
        try { d = await api("GET", `/api/targets/${encodeURIComponent(name)}/winrm-install`); }
        catch (e) { if (e.message === "auth") return null; d = { state: "running", step: "รอคำตอบ..." }; }
        if (d.state === "running") { say("wi_msg", `⏳ ${d.step || "กำลังทำงาน"} (${Math.round((Date.now() - started) / 1000)} วิ)`, ""); continue; }
        if (d.log) { $("wi_log").textContent = d.log; $("wi_log").hidden = false; }
        return d;
    }
}

function adminBody() {
    return { admin_user: $("wi_user").value.trim(), admin_password: $("wi_pass").value,
             admin_port: $("wi_port").value, central: $("wi_central").value.trim() };
}

async function installWin() {
    const b = $("wi_go");
    const body = adminBody();
    if (!body.admin_password) { say("wi_msg", "ใส่รหัสผ่านของ admin ก่อน", "bad"); $("wi_pass").focus(); return; }
    b.disabled = true;
    try {
        if (!await ensureSaved()) return;
        const name = S.editing;
        say("wi_msg", "กำลังเริ่ม...", "");
        try { await api("POST", `/api/targets/${encodeURIComponent(name)}/winrm-install`, body); }
        catch (e) {
            if (e.message === "auth") return;
            say("wi_msg", "เริ่มไม่ได้: " + (showErrors(e.fields) || e.message), "bad");
            return;
        } finally { $("wi_pass").value = ""; }       /* รหัส admin ไม่ค้างในหน้า */
        const d = await pollJob(name);
        if (!d) return;
        if (d.state === "done" && d.ok) {
            say("wi_msg", `✓ ติดตั้งเสร็จบน ${d.computer || name}${d.warnings ? ` (คำเตือน ${d.warnings} ข้อ ดู log ด้านล่าง)` : ""} · central เริ่มเก็บข้อมูลภายใน ~1 นาที`, "ok");
            try {
                const r = await api("GET", "/api/targets"); S.targets = r.targets; renderTable();
                const nt = S.targets.find(x => x.name === name);
                if (nt) {
                    $("w_thumb").value = nt.winrm.thumbprint || ""; $("w_port").value = nt.winrm.port || 5986;
                    $("w_pass").placeholder = "•••••••• บันทึกไว้แล้ว (เว้นว่าง = ใช้รหัสเดิม)";
                    $("wu_box").hidden = false;
                }
            } catch (e) { /* ignore */ }
        } else {
            say("wi_msg", "✗ " + (d.error || "ติดตั้งไม่สำเร็จ") + (d.log ? " · ดู log ด้านล่าง" : ""), "bad");
        }
    } finally { b.disabled = false; }
}

async function uninstallWin() {
    const name = S.editing;
    if (!name) return;
    const body = { ...adminBody(), delete_target: $("wu_del").checked };
    if (!body.admin_password) { say("wi_msg", "ใส่ Admin user / password ด้านบนก่อน (ใช้ถอนครั้งเดียว ไม่บันทึก)", "bad"); $("wi_pass").focus(); return; }
    if (!confirm(`ถอน Central Monitor ออกจากเครื่อง ${name}?\n\nลบ: user cm-monitor + profile, JEA endpoint, module + ใบรับรอง, WinRM HTTPS listener ที่ setup สร้าง,` +
                 ` firewall rule, สิทธิ์ต่าง ๆ ที่ให้ cm-monitor และโฟลเดอร์ใน ProgramData` +
                 (body.delete_target ? `\nแล้วลบ ${name} ออกจาก central (ข้อมูลย้อนหลังใน OpenSearch / Prometheus ยังอยู่จนหมดอายุ)` : `\nเครื่องใน central จะถูก Pause ไว้`))) return;
    const b = $("wu_go"); b.disabled = true; $("wi_go").disabled = true;
    try {
        say("wi_msg", "กำลังเริ่มถอน...", "");
        try { await api("POST", `/api/targets/${encodeURIComponent(name)}/winrm-uninstall`, body); }
        catch (e) {
            if (e.message === "auth") return;
            say("wi_msg", "เริ่มไม่ได้: " + (showErrors(e.fields) || e.message), "bad");
            return;
        } finally { $("wi_pass").value = ""; }
        const d = await pollJob(name);
        if (!d) return;
        if (d.state === "done" && d.ok) {
            const left = (d.leftovers || []).length ? ` · ยังเหลือ: ${d.leftovers.join(", ")}` : " · ไม่มีอะไรค้าง";
            if (d.deleted) {
                closeForm(); await loadTargets();
                say("listMsg", `ถอน Central Monitor ออกจาก ${d.computer || name} แล้ว${left} · ลบ ${name} ออกจาก central แล้ว`, (d.leftovers || []).length ? "bad" : "ok");
                window.scrollTo({ top: 0, behavior: "smooth" });
            } else {
                say("wi_msg", `✓ ถอนออกจาก ${d.computer || name} แล้ว${left} · เครื่องใน central ถูก Pause ไว้`, (d.leftovers || []).length ? "bad" : "ok");
                loadTargets();
            }
        } else {
            say("wi_msg", "✗ " + (d.error || "ถอนไม่สำเร็จ") + (d.log ? " · ดู log ด้านล่าง" : ""), "bad");
        }
    } finally { b.disabled = false; $("wi_go").disabled = false; }
}

/* ---------- host key ---------- */

function renderHostKeyStatus() {
    const n = $("hkStatus");
    if (S.scanned.length) { n.textContent = "ตรวจ fingerprint ด้านล่างให้ตรงกับเครื่องจริง แล้วติ๊กยืนยัน"; n.className = ""; }
    else if (S.hostKeyKnown) { n.textContent = "✓ มี host key ของ host:port นี้ใน known_hosts แล้ว"; n.className = "ok"; }
    else { n.textContent = "⚠ ยังไม่มี host key: puller ดึงเครื่องนี้ไม่ได้จนกว่าจะเก็บ (กด Scan host key)"; n.className = "wr"; }
}

async function scanHostKey() {
    const b = $("scanBtn"); b.disabled = true; b.textContent = "Scanning...";
    $("hkKeys").textContent = "";
    try {
        const d = await api("POST", "/api/targets/host-key", { host: $("f_host").value.trim(), port: $("f_port").value.trim() || 22 });
        S.scanned = d.keys;
        const box = el("div", { class: "st-box" });
        d.keys.forEach(k => box.append(el("div", { class: "st-fp" }, el("b", {}, k.type), "  ", k.fingerprint)));
        box.append(el("div", { class: "st-fp", style: "margin-top:8px" }, "ตรวจเทียบบนเครื่องปลายทาง:  ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub"));
        box.append(el("label", { style: "display:flex;gap:8px;align-items:center;margin-top:10px;font-size:14px" },
            el("input", { type: "checkbox", id: "hkTrust" }), "fingerprint ตรงกับเครื่องจริง เชื่อถือเครื่องนี้"));
        $("hkKeys").append(box);
    } catch (e) {
        S.scanned = [];
        const msg = e.fields ? Object.values(e.fields).join(" · ") : e.message;
        $("hkKeys").append(el("div", { class: "st-msg bad" }, msg));
    } finally { b.disabled = false; b.textContent = "Scan host key"; renderHostKeyStatus(); }
}

/* ---------- คำสั่งตั้งค่าเครื่องปลายทาง ---------- */

function renderSetup() {
    const user = $("f_user").value.trim() || "central-pull";
    const pub = S.publicKey || "<ไม่พบ keys/central_ed25519.pub บน server กลาง>";
    $("setupCmd").value =
`# รันบนเครื่องปลายทาง (ต้องมี sudo)
sudo apt install -y prometheus-node-exporter && sudo systemctl enable --now prometheus-node-exporter
sudo adduser --disabled-password --gecos "" ${user}
sudo usermod -aG adm ${user}          # ให้อ่านไฟล์ log ใน /var/log ได้ (ปรับตามจริง)
sudo usermod -aG systemd-journal ${user}   # ให้อ่าน journal (journalctl) ได้ ถ้าใช้ journal log
sudo -u ${user} mkdir -p /home/${user}/.ssh
echo '${pub}' | sudo -u ${user} tee -a /home/${user}/.ssh/authorized_keys
sudo chmod 700 /home/${user}/.ssh && sudo chmod 600 /home/${user}/.ssh/authorized_keys
# แนะนำ: เปิดพอร์ต 9100 เฉพาะ IP ของ server กลาง (ufw allow from <IP_central> to any port 9100 proto tcp)`;
}

async function copySetup() {
    const t = $("setupCmd"); t.select();
    try { await navigator.clipboard.writeText(t.value); $("copyMsg").textContent = "คัดลอกแล้ว"; }
    catch (e) { document.execCommand("copy"); $("copyMsg").textContent = "คัดลอกแล้ว"; }
    setTimeout(() => $("copyMsg").textContent = "", 2500);
}

/* ---------- วาง public key ลงเครื่อง target ---------- */

// อ่านรหัสผ่านแล้วล้างช่องทันที (ไม่เก็บค้างในหน้า) -> เรียก API  คืน {ok, message} หรือ null ถ้าไม่ได้กรอกรหัส
async function deployKey(name) {
    const pass = $("dp_pass").value;
    if (!pass) return null;
    const body = { admin_user: $("dp_admin").value.trim(), password: pass };
    $("dp_pass").value = "";
    try { return await api("POST", `/api/targets/${encodeURIComponent(name)}/deploy-key`, body); }
    catch (e) {
        if (e.message === "auth") throw e;
        const msg = e.fields ? Object.values(e.fields).join(" · ") : e.message;
        return { ok: false, message: msg };
    }
}

async function deployNow() {
    if (!S.editing) { say("deployMsg", "เครื่องนี้ยังไม่ได้บันทึก — กรอกรหัสผ่านแล้วกด Save ระบบจะวาง key ให้หลังบันทึก", "bad"); return; }
    if (!$("dp_pass").value) { say("deployMsg", "กรอกรหัสผ่านก่อน", "bad"); return; }
    const b = $("deployBtn"); b.disabled = true; say("deployMsg", "กำลังวาง key...", "");
    try { const r = await deployKey(S.editing); say("deployMsg", r.message, r.ok ? "ok" : "bad"); }
    catch (e) { /* auth -> api() พาไป /login เอง */ }
    finally { b.disabled = false; }
}

/* ---------- บันทึก ---------- */

async function save() {
    clearErrors(); say("formMsg", "");
    const body = readForm();
    if (isOp()) {                                   /* operator: บันทึกเฉพาะการแจ้งเตือน + log */
        const b = $("saveBtn"); b.disabled = true;
        try {
            await api("PUT", "/api/targets/" + encodeURIComponent(S.editing), body);
            const name = S.editing; closeForm(); await loadTargets();
            say("listMsg", `บันทึกการแจ้งเตือน / Log ของ ${name} แล้ว · มีผลในรอบตรวจถัดไป (~30 วินาที)`, "ok");
        } catch (e) {
            if (e.message === "auth") return;
            const general = showErrors(e.fields);
            say("formMsg", general || (e.fields ? "กรุณาแก้ช่องที่มีข้อความสีแดง" : "บันทึกไม่สำเร็จ: " + e.message), "bad");
        } finally { b.disabled = false; }
        return;
    }
    const trust = S.scanned.length && $("hkTrust") && $("hkTrust").checked;
    if (body.type === "winrm") { /* Windows: ไม่มี SSH host key */ }
    else if (trust) body.trust_fingerprints = S.scanned.map(k => k.fingerprint);
    else if (!S.hostKeyKnown && !confirm("ยังไม่ได้เก็บ SSH host key ของเครื่องนี้\npuller จะดึง log ไม่ได้จนกว่าจะเก็บ host key\n\nบันทึกต่อไหม?")) return;

    const b = $("saveBtn"); b.disabled = true;
    try {
        if (S.editing) await api("PUT", "/api/targets/" + encodeURIComponent(S.editing), body);
        else await api("POST", "/api/targets", body);
        const name = S.editing || body.name;
        const dep = body.type === "winrm" ? null : await deployKey(name);   // null = ไม่ได้กรอกรหัสผ่าน (ข้ามการวาง key)
        $("w_pass").value = "";
        closeForm(); await loadTargets();
        if (dep && dep.ok) say("listMsg", `บันทึก ${name} แล้ว · ${dep.message}`, "ok");
        else if (dep) say("listMsg", `บันทึก ${name} แล้ว แต่วาง public key ไม่สำเร็จ: ${dep.message} — กด Deploy key ที่แถวของเครื่องนี้เพื่อลองใหม่ หรือวางเองตามคำสั่งในหน้า Edit`, "bad");
        else say("listMsg", body.type === "winrm"
            ? `บันทึก ${name} แล้ว — central เริ่มอ่านเครื่อง Windows นี้ภายใน ~15 วินาที (CPU / RAM / Disk ขึ้นในหน้า Dashboard ภายใน ~1 นาที)`
            : `บันทึก ${name} แล้ว — puller จะดึงเครื่องนี้ภายในไม่กี่วินาที (Prometheus เห็นเครื่องใหม่ทันที)`, "ok");
        window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (e) {
        if (e.message === "auth") return;
        const general = showErrors(e.fields);
        say("formMsg", general || (e.fields ? "กรุณาแก้ช่องที่มีข้อความสีแดง" : "บันทึกไม่สำเร็จ: " + e.message), "bad");
    } finally { b.disabled = false; }
}

/* ---------- Web checks (uptime / SSL) ---------- */

const CHECK_FIELDS = { name: "c_name", url: "c_url", group: "c_group", interval: "c_interval", timeout: "c_timeout",
    expect_status: "c_expect", keyword: "c_keyword", ssl_warn_days: "c_sslwarn" };

async function loadChecks() {
    try { S.checks = (await api("GET", "/api/checks")).checks || []; renderChecks(); fillGroups(); }
    catch (e) { if (e.message !== "auth") say("checkMsg", "โหลด checks ไม่สำเร็จ: " + e.message, "bad"); }
}

function checkStatus(c) {
    const map = { up: ["✓ UP", "ok"], warning: ["⚠ Warning", "wr"], down: ["▼ DOWN", "bad"], paused: ["⏸ Paused", "wr"], pending: ["… pending", ""] };
    const [text, cls] = map[c.status] || [c.status || "-", ""];
    return el("td", { class: cls, title: c.error || "" }, text);
}

function renderChecks() {
    const tb = $("checkBody"); tb.textContent = "";
    if (!S.checks.length) { tb.append(el("tr", {}, el("td", { colspan: "7", class: "empty" }, "ยังไม่มี check กด + Add check"))); return; }
    S.checks.forEach(c => tb.append(el("tr", {},
        el("td", {}, el("b", {}, c.name)),
        el("td", {}, c.group || "-"),
        el("td", { class: "st-url" }, c.url),
        el("td", {}, `${c.interval}s`),
        el("td", {}, c.expect_status + (c.keyword ? ` + "${c.keyword}"` : "")),
        checkStatus(c),
        el("td", {}, el("div", { class: "st-actions" },
            el("button", { class: "st-btn sm", onclick: () => pauseCheck(c) }, c.paused ? "Resume" : "Pause"),
            el("button", { class: "st-btn sm", onclick: () => openCheckForm(c) }, "Edit"),
            el("button", { class: "st-btn sm danger", onclick: () => deleteCheck(c) }, "Delete"))))));
}

function openCheckForm(c) {
    S.editingCheck = c ? c.name : null;
    $("checkTitle").textContent = c ? `Edit check: ${c.name}` : "Add check";
    Object.entries(CHECK_FIELDS).forEach(([k, id]) => { $(id).value = c && c[k] != null ? c[k] : ""; }); $("c_group")._gpPaint?.();
    $("c_name").disabled = !!c;
    $("c_verify").checked = !c || c.verify_tls !== false;
    $("c_paused").checked = !!(c && c.paused);
    document.querySelectorAll("#checkGrid .st-err").forEach(n => n.textContent = "");
    document.querySelectorAll("#checkGrid .has-err").forEach(n => n.classList.remove("has-err"));
    say("checkFormMsg", "");
    $("checkForm").hidden = false;
    (c ? $("c_url") : $("c_name")).focus();
}

async function saveCheck() {
    const body = {};
    Object.entries(CHECK_FIELDS).forEach(([k, id]) => { body[k] = $(id).value.trim(); });
    body.verify_tls = $("c_verify").checked; body.paused = $("c_paused").checked;
    document.querySelectorAll("#checkGrid .st-err").forEach(n => n.textContent = "");
    document.querySelectorAll("#checkGrid .has-err").forEach(n => n.classList.remove("has-err"));
    try {
        if (S.editingCheck) await api("PUT", "/api/checks/" + encodeURIComponent(S.editingCheck), body);
        else await api("POST", "/api/checks", body);
        $("checkForm").hidden = true;
        say("checkMsg", `บันทึก ${S.editingCheck || body.name} แล้ว — เช็คครั้งแรกภายใน ~10 วินาที`, "ok");
        await loadChecks();
    } catch (e) {
        if (e.message === "auth") return;
        const general = [];
        Object.entries(e.fields || {}).forEach(([k, msg]) => {
            const box = document.querySelector(`#checkGrid [data-c="${CSS.escape(k)}"]`);
            if (box) { box.classList.add("has-err"); box.querySelector(".st-err").textContent = msg; } else general.push(msg);
        });
        say("checkFormMsg", general.join(" · ") || (e.fields ? "กรุณาแก้ช่องที่มีข้อความสีแดง" : "บันทึกไม่สำเร็จ: " + e.message), "bad");
    }
}

async function pauseCheck(c) {
    try { await api("PUT", `/api/checks/${encodeURIComponent(c.name)}/pause`, { paused: !c.paused }); await loadChecks(); }
    catch (e) { if (e.message !== "auth") say("checkMsg", "เปลี่ยนสถานะไม่สำเร็จ: " + e.message, "bad"); }
}

async function deleteCheck(c) {
    if (!confirm(`ลบ check "${c.name}" ?`)) return;
    try { await api("DELETE", "/api/checks/" + encodeURIComponent(c.name)); say("checkMsg", `ลบ ${c.name} แล้ว`, "ok"); await loadChecks(); }
    catch (e) { if (e.message !== "auth") say("checkMsg", "ลบไม่สำเร็จ: " + e.message, "bad"); }
}

/* ---------- Users ---------- */

function fmtTime(sec) { return sec ? new Date(sec * 1000).toLocaleString("en-GB") : "-"; }

async function loadUsers() {
    try {
        const d = await api("GET", "/api/users");
        const tb = $("userBody"); tb.textContent = "";
        tb.append(el("tr", {}, el("td", {}, el("b", {}, d.builtin_admin), " ", el("small", { class: "st-mut" }, "(docker/.env)")),
            el("td", {}, "admin"), el("td", {}, "-"), el("td", {}, el("small", { class: "st-mut" }, "เปลี่ยนที่ docker/.env"))));
        d.users.forEach(u => {
            const sel = el("select", { class: "st-sel", "aria-label": `Role of ${u.username}` });
            (d.roles || ["viewer", "operator", "admin"]).forEach(r => { const o = el("option", { value: r }, r); if (r === u.role) o.selected = true; sel.append(o); });
            sel.addEventListener("change", () => updateUser(u.username, { role: sel.value }));
            tb.append(el("tr", {},
                el("td", {}, el("b", {}, u.username)),
                el("td", {}, sel),
                el("td", {}, fmtTime(u.last_login)),
                el("td", {}, el("div", { class: "st-actions" },
                    el("button", { class: "st-btn sm", onclick: () => resetPassword(u.username) }, "Reset password"),
                    el("button", { class: "st-btn sm danger", onclick: () => deleteUser(u.username) }, "Delete")))));
        });
    } catch (e) { if (e.message !== "auth") say("userMsg", "โหลดผู้ใช้ไม่สำเร็จ: " + e.message, "bad"); }
}

async function updateUser(name, body) {
    try { await api("PUT", "/api/users/" + encodeURIComponent(name), body); say("userMsg", `แก้ ${name} แล้ว`, "ok"); }
    catch (e) { if (e.message !== "auth") say("userMsg", (e.fields ? Object.values(e.fields).join(" · ") : e.message), "bad"); }
    await loadUsers();
}

async function resetPassword(name) {
    const pw = prompt(`รหัสผ่านใหม่ของ ${name} (อย่างน้อย 8 ตัว)`);
    if (pw) await updateUser(name, { password: pw });
}

async function deleteUser(name) {
    if (!confirm(`ลบผู้ใช้ "${name}" ?`)) return;
    try { await api("DELETE", "/api/users/" + encodeURIComponent(name)); say("userMsg", `ลบ ${name} แล้ว`, "ok"); }
    catch (e) { if (e.message !== "auth") say("userMsg", "ลบไม่สำเร็จ: " + e.message, "bad"); }
    await loadUsers();
}

async function addUser() {
    document.querySelectorAll("[data-u] .st-err").forEach(n => n.textContent = "");
    const body = { username: $("u_name").value.trim(), password: $("u_pass").value, role: $("u_role").value };
    try {
        await api("POST", "/api/users", body);
        $("u_name").value = ""; $("u_pass").value = "";
        say("userMsg", `เพิ่ม ${body.username} (${body.role}) แล้ว`, "ok");
        await loadUsers();
    } catch (e) {
        if (e.message === "auth") return;
        Object.entries(e.fields || {}).forEach(([k, msg]) => { const b = document.querySelector(`[data-u="${k}"] .st-err`); if (b) b.textContent = msg; });
        say("userMsg", e.fields ? "กรุณาแก้ช่องที่มีข้อความสีแดง" : "เพิ่มไม่สำเร็จ: " + e.message, "bad");
    }
}

/* ---------- ผู้ใช้ / Logout / เริ่มต้น ---------- */

/* ---------- อายุการเก็บข้อมูล ---------- */
const RT = { log_retention: "r_log", incident_retention: "r_inc", system_history_retention: "r_sys" };
let RT_DATA = null;
function rtSec(v) { const m = /^(\d+)\s*([smhd])$/.exec(String(v || "").trim().toLowerCase()); return m ? m[1] * { s: 1, m: 60, h: 3600, d: 86400 }[m[2]] : null; }
function rtText(v) {
    const s = rtSec(v); if (s == null) return v || "-";
    return s % 86400 === 0 ? `${s / 86400} day${s === 86400 ? "" : "s"}` : s % 3600 === 0 ? `${s / 3600} hours` : v;
}
function rtFill(id, v) {
    const s = rtSec(v);
    if (s != null && s % 86400 === 0) { $(id).value = s / 86400; $(id + "_u").value = "d"; }
    else if (s != null) { $(id).value = Math.max(1, Math.round(s / 3600)); $(id + "_u").value = "h"; }
}
function rtRead(id) { const n = parseInt($(id).value, 10); return Number.isFinite(n) && n > 0 ? `${n}${$(id + "_u").value}` : ""; }
function fmtBytes(b) { if (!b) return "0 B"; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; } return `${b.toFixed(i ? 1 : 0)} ${u[i]}`; }

async function loadRetention() {
    try {
        const d = RT_DATA = await api("GET", "/api/retention");
        Object.entries(RT).forEach(([k, id]) => rtFill(id, d[k]));
        const ix = d.indices;
        $("r_log_note").textContent = ix
            ? `ตอนนี้เก็บอยู่ ${ix.docs.toLocaleString()} log · ${fmtBytes(ix.bytes)} · ${ix.count} index${ix.oldest ? ` · เก่าสุดตั้งแต่ ${new Date(ix.oldest * 1000).toLocaleString("en-GB")}` : ""}`
            : "อ่านข้อมูลจาก OpenSearch ไม่ได้";
        $("r_prom").textContent = d.prometheus_retention ? rtText(d.prometheus_retention) : "ไม่ทราบ (ติดต่อ Prometheus ไม่ได้)";
        retentionWarn();
    } catch (e) { if (e.message !== "auth") say("r_msg", "โหลดค่าไม่สำเร็จ: " + e.message, "bad"); }
}

/* log เก็บนานกว่า metrics -> log ส่วนที่เกินจะไม่แสดงในหน้า Logs (แสดงเฉพาะ log ที่มี metrics) */
function retentionWarn() {
    const lg = rtSec(rtRead("r_log")), pr = rtSec(RT_DATA && RT_DATA.prometheus_retention);
    const w = $("r_warn");
    w.hidden = !(lg && pr && lg > pr);
    if (!w.hidden) w.textContent = `หมายเหตุ: Prometheus เก็บ metrics ${rtText(RT_DATA.prometheus_retention)} — หน้า Logs แสดงเฉพาะ log ที่มี metrics ดังนั้น log ที่เก่ากว่า ${rtText(RT_DATA.prometheus_retention)} จะไม่แสดง (แต่ยังเก็บอยู่ใน OpenSearch) ถ้าต้องการเห็นด้วย ให้เพิ่ม PROMETHEUS_RETENTION ให้เท่ากัน`;
}

async function saveRetention() {
    document.querySelectorAll("[data-r]").forEach(f => { f.classList.remove("has-err"); f.querySelector(".st-err").textContent = ""; });
    const body = {};
    Object.entries(RT).forEach(([k, id]) => { const v = rtRead(id); if (v && (!RT_DATA || rtSec(v) !== rtSec(RT_DATA[k]))) body[k] = v; });
    if (!Object.keys(body).length) { say("r_msg", "ไม่มีค่าที่เปลี่ยน", ""); return; }
    const shrink = Object.keys(body).filter(k => RT_DATA && rtSec(body[k]) < rtSec(RT_DATA[k]));
    if (shrink.length && !confirm(`ลดอายุการเก็บ:\n${shrink.map(k => `- ${k}: ${rtText(RT_DATA[k])} → ${rtText(body[k])}`).join("\n")}\n\nข้อมูลที่เก่ากว่าค่าใหม่จะถูกลบ และกู้คืนไม่ได้ ดำเนินการต่อ?`)) return;
    $("r_save").disabled = true;
    try {
        const d = await api("PUT", "/api/retention", body);
        say("r_msg", "บันทึกแล้ว · " + (d.notes || []).join(" · "), "ok");
        await loadRetention();
    } catch (e) {
        if (e.fields) Object.entries(e.fields).forEach(([k, m]) => { const f = document.querySelector(`[data-r="${k}"]`); if (f) { f.classList.add("has-err"); f.querySelector(".st-err").textContent = m; } });
        say("r_msg", "บันทึกไม่สำเร็จ: " + e.message, "bad");
    } finally { $("r_save").disabled = false; }
}

/* ---------- Alerts (/api/alert-settings) ---------- */
let AL = null;
const AL_LABEL = {
    cpu: "CPU %", memory: "Memory used %", disk: "Disk used %", disk_latency: "Disk latency (ms)",
    packet_loss: "Packet loss (ping) %", net_latency: "Ping latency (ms)", mem_available: "Available RAM % (ต่ำกว่า = แจ้ง)",
    time_offset: "เวลาคลาด / NTP (ms)",
};
function alInput(id, v) { return el("input", { id, type: "number", step: "any", min: "0", inputmode: "decimal", value: v ?? "" }); }
function renderAlerts(d) {
    $("al_min").value = d.alert_minutes; $("al_hyst").value = d.hysteresis; $("al_bn").checked = !!d.bottleneck;
    const tb = $("alRows"); tb.textContent = "";
    (d.kinds || Object.keys(AL_LABEL)).forEach(k => {
        if (k === "mem_available") return;                     /* ใช้ Memory used % แทน */
        const lx = (d.thresholds || {})[k] || {};
        const cell = (os, lv, v) => el("td", { "data-al": `${os}.${k}.${lv}` }, alInput(`al_${os}_${k}_${lv}`, v), el("div", { class: "st-err" }));
        tb.append(el("tr", {}, el("td", {}, AL_LABEL[k] || k), cell("thresholds", "warning", lx.warning), cell("thresholds", "error", lx.error)));
    });
}
async function loadAlerts() {
    try { AL = await api("GET", "/api/alert-settings"); renderAlerts(AL); }
    catch (e) { if (e.message !== "auth") say("al_msg", "โหลดค่าไม่สำเร็จ: " + e.message, "bad"); }
}
async function saveAlerts() {
    document.querySelectorAll("[data-al]").forEach(f => { f.classList.remove("has-err"); const er = f.querySelector(".st-err"); if (er) er.textContent = ""; });
    const th = os => Object.fromEntries((AL.kinds || []).filter(k => $(`al_${os}_${k}_warning`)).map(k =>
        [k, { warning: $(`al_${os}_${k}_warning`).value.trim(), error: $(`al_${os}_${k}_error`).value.trim() }]));
    const body = {
        alert_minutes: $("al_min").value.trim(), hysteresis: $("al_hyst").value.trim(), bottleneck: $("al_bn").checked,
        thresholds: th("thresholds"),
    };
    $("al_save").disabled = true;
    try {
        AL = await api("PUT", "/api/alert-settings", body);
        renderAlerts(AL);
        say("al_msg", `บันทึกแล้ว · แจ้งเตือนเมื่อเกินต่อเนื่อง ${AL.alert_minutes} นาที (${AL.confirm_checks} รอบตรวจ)`, "ok");
    } catch (e) {
        const left = [];
        if (e.fields) Object.entries(e.fields).forEach(([k, m]) => {
            const f = document.querySelector(`[data-al="${CSS.escape(k)}"]`);
            if (f) { f.classList.add("has-err"); const er = f.querySelector(".st-err"); if (er) er.textContent = m; } else left.push(m);
        });
        say("al_msg", "บันทึกไม่สำเร็จ: " + e.message + (left.length ? " · " + left.join(" · ") : ""), "bad");
    } finally { $("al_save").disabled = false; }
}
function resetAlerts() {
    if (!AL || !AL.defaults) return;
    renderAlerts({ ...AL, alert_minutes: AL.defaults.alert_minutes, hysteresis: 2, bottleneck: true,
        thresholds: AL.defaults.thresholds });
    say("al_msg", "ใส่ค่าเริ่มต้นแล้ว กด Save alerts เพื่อบันทึก", "");
}

/* ---------- Notifications: Microsoft Teams (/api/notify-settings) ---------- */
const N_LABEL = { down: "เครื่อง Down", error: "Error (disk เต็ม, service หยุด, ...)", warning: "Warning",
    recovered: "กลับมาปกติ (Recovered)", checks: "Web / SSL check" };
function renderNotify(d) {
    $("n_on").checked = !!d.enabled || !d.url_set;
    $("n_url").value = "";
    $("n_times").value = (d.summary_times || []).join(", ");
    $("n_popup").checked = d.popup !== false;
    const os = $("n_popos"), secure = window.isSecureContext && "Notification" in window;
    os.disabled = !secure;
    os.checked = !!(window.cmPopup && window.cmPopup.osOn());
    $("n_hint").textContent = d.url_set ? `บันทึกไว้แล้ว: ${d.url_hint}` : "ยังไม่ได้ตั้ง";
    const box = $("n_levels"); box.textContent = "";
    (d.all_levels || Object.keys(N_LABEL)).forEach(k => box.append(
        el("label", { class: "st-check" }, el("input", Object.assign({ type: "checkbox", value: k }, (d.levels || []).includes(k) ? { checked: "" } : {})), " " + (N_LABEL[k] || k))));
}
async function loadNotify() {
    try { renderNotify(await api("GET", "/api/notify-settings")); }
    catch (e) { if (e.message !== "auth") say("n_msg", "โหลดค่าไม่สำเร็จ: " + e.message, "bad"); }
}
function notifyErrors(e) {
    document.querySelectorAll("[data-n]").forEach(f => { f.classList.remove("has-err"); const x = f.querySelector(".st-err"); if (x) x.textContent = ""; });
    Object.entries((e && e.fields) || {}).forEach(([k, m]) => { const f = document.querySelector(`[data-n="${k}"]`); if (f) { f.classList.add("has-err"); f.querySelector(".st-err").textContent = m; } });
}
async function saveNotify(extra) {
    notifyErrors(null);
    const body = Object.assign({
        enabled: $("n_on").checked, summary_times: $("n_times").value, popup: $("n_popup").checked,
        levels: [...$("n_levels").querySelectorAll("input:checked")].map(i => i.value),
    }, $("n_url").value.trim() ? { webhook_url: $("n_url").value.trim() } : {}, extra || {});
    $("n_save").disabled = true;
    try { renderNotify(await api("PUT", "/api/notify-settings", body)); say("n_msg", "บันทึกแล้ว · puller ใช้ค่าใหม่ในรอบตรวจถัดไป (~30 วินาที)", "ok"); }
    catch (e) { notifyErrors(e); say("n_msg", "บันทึกไม่สำเร็จ: " + e.message, "bad"); }
    finally { $("n_save").disabled = false; }
}
async function testNotify() {
    $("n_test").disabled = true; say("n_msg", "กำลังส่งการ์ดทดสอบ...", "");
    try {
        const d = await api("POST", "/api/notify-settings/test", { webhook_url: $("n_url").value.trim() || undefined });
        say("n_msg", `ส่งแล้ว (${d.message}) · ดูที่ช่อง Teams${$("n_url").value.trim() ? " · อย่าลืมกด Save" : ""}`, "ok");
    } catch (e) { say("n_msg", "ส่งไม่สำเร็จ: " + e.message, "bad"); }
    finally { $("n_test").disabled = false; }
}

async function loadUser() {
    try { const d = await api("GET", "/api/me"); $("currentUsername").textContent = d.username || "?"; } catch (e) { /* api() พาไป /login เอง */ }
}

async function logout() {
    $("logoutButton").disabled = true;
    try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } finally { window.location.href = "/login"; }
}

document.addEventListener("DOMContentLoaded", () => {
    $("f_paused")?.addEventListener("change", () => {
        const box = $("f_pause_reason").closest(".st-f");
        box.hidden = !$("f_paused").checked;
        if ($("f_paused").checked) { $("f_paused").dataset.at = ""; $("f_pause_reason").focus(); }
    });
    $("fa_sens_add")?.addEventListener("click", () => { const tr = sensRow("", {}); $("fa_sens").append(tr); tr.querySelector("input").focus(); });
    const dl = el("datalist", { id: "logNames" });
    ["application", "logapp", "loginuser", "journal", "system", "kernel"].forEach(v => dl.append(el("option", { value: v })));
    document.body.append(dl, el("datalist", { id: "logLabels" }));
    $("addBtn").onclick = () => openForm(null);
    $("cancelBtn").onclick = closeForm;
    $("saveBtn").onclick = save;
    $("logCheck").onclick = () => loadLogStatus(true);
    $("addLog").onclick = () => $("logs").append(logRow(null));
    $("addJournal").onclick = () => $("logs").append(logRow({ name: "journal", path: "journal", type: "system" }));
    $("scanBtn").onclick = scanHostKey;
    $("f_type").addEventListener("change", () => {
        applyType(); clearErrors();
        document.querySelectorAll('#logs [data-k="path"]').forEach(i => {
            i.placeholder = formType() === "winrm" ? "D:\\App\\logs\\*.log" : "/var/log/myapp/app.log  หรือ  journal:nginx.service";
        });
    });
    $("w_scan").onclick = scanCert;
    $("w_test").onclick = testWinrm;
    $("w_download").onclick = downloadSetup;
    $("wi_go").onclick = installWin;
    $("wu_go").onclick = uninstallWin;
    $("copyCmd").onclick = copySetup;
    $("deployBtn").onclick = deployNow;
    $("f_user").addEventListener("input", renderSetup);
    ["f_host", "f_port"].forEach(id => $(id).addEventListener("input", () => { S.scanned = []; $("hkKeys").textContent = ""; S.hostKeyKnown = false; renderHostKeyStatus(); }));
    $("logoutButton").onclick = logout;
    document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("formCard").hidden) closeForm(); });
    $("addCheckBtn").onclick = () => openCheckForm(null);
    $("checkCancel").onclick = () => { $("checkForm").hidden = true; };
    $("checkSave").onclick = saveCheck;
    $("userAdd").onclick = addUser;
    /* Web checks ซ่อนไว้ก่อน: เปลี่ยน SHOW_CHECKS เป็น true แล้วการ์ดจะกลับมา (คู่กับ dashboard.js) */
    const SHOW_CHECKS = false;
    $("checksCard").hidden = !SHOW_CHECKS;
    $("r_save").onclick = saveRetention;
    $("al_save").onclick = saveAlerts;
    $("al_reset").onclick = resetAlerts;
    $("n_save").onclick = () => saveNotify();
    $("n_test").onclick = testNotify;
    $("n_popos").addEventListener("change", async e => {
        if (!window.cmPopup) return;
        const want = e.target.checked, ok = await window.cmPopup.setOs(want);
        e.target.checked = ok;
        say("n_msg", ok ? "เปิดแจ้งเตือนของระบบบน browser นี้แล้ว"
            : want ? "browser ไม่อนุญาต: กดไอคอนแม่กุญแจหน้า URL > Notifications > Allow แล้วติ๊กใหม่" : "ปิดแจ้งเตือนของระบบบน browser นี้แล้ว", ok || !want ? "ok" : "bad");
    });
    $("n_poptest").onclick = () => { if (window.cmPopup) window.cmPopup.test(); };
    $("n_sumnow").onclick = async () => {
        try { const d = await api("POST", "/api/notify-settings/summary-now", {}); say("n_msg", d.message, "ok"); }
        catch (e) { say("n_msg", "ส่งไม่ได้: " + e.message, "bad"); }
    };
    $("n_clear").onclick = () => { if (confirm("ลบ webhook URL? (หยุดส่งแจ้งเตือนไป Teams)")) saveNotify({ webhook_url: "-", enabled: false }); };
    document.querySelectorAll("#formCard .ftabs button").forEach(b => { b.onclick = () => formTab(b.dataset.t); });
    ["r_log", "r_log_u"].forEach(id => $(id).addEventListener("input", retentionWarn));
    (async () => {
        let role = "admin";
        try { const d = await api("GET", "/api/me"); $("currentUsername").textContent = d.username || "?"; role = d.role || "viewer"; }
        catch (e) { return; }
        S.role = role;
        if (role !== "admin") {                    /* operator: เครื่อง (การแจ้งเตือน + Log) + Custom alert rules */
            document.querySelectorAll("section.card").forEach(c => { if (!["rulesCard", "targetsCard", "formCard"].includes(c.id)) c.hidden = true; });
            $("addBtn").hidden = true;
            loadTargets(); loadAlerts();
            return;
        }
        loadTargets(); loadUsers(); loadRetention(); loadAlerts(); loadNotify();
        if (SHOW_CHECKS) loadChecks();
    })();
});
