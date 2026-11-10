/* =====================================================================
   alert_rules.js - Settings > Custom alert rules (กฎแจ้งเตือนที่สร้างเอง)
   =====================================================================
   API: GET/PUT /api/alert-rules  (ทั้งรายการ)   POST /api/alert-rules/preview (ทดลองกับเครื่องตอนนี้)
   ใช้ตัวช่วยจาก settings.js: $, el, api, say
   ===================================================================== */
"use strict";

const RL = { data: null, rules: [], editing: null };

const RL_STATE = ["service", "process", "port_listen", "tcp_port", "http_url"];   // ไม่มีเกณฑ์ตัวเลข ผิดปกติ = ระดับที่เลือก
const RL_PROC = ["process", "process_cpu", "process_mem"];
function rlMetric(key) { return ((RL.data && RL.data.metrics) || []).find(m => m.key === key) || { label: key, unit: "" }; }

function rlWhere(r) {
    const parts = [];
    if ((r.targets || []).length) parts.push(r.targets.join(", "));
    if ((r.groups || []).length) parts.push("กลุ่ม " + r.groups.join(", "));
    if (r.os && r.os !== "any") parts.push(r.os === "windows" ? "Windows" : "Linux");
    return parts.join(" · ") || "ทุกเครื่อง";
}

function rlCond(r) {
    const m = rlMetric(r.metric), u = r.metric === "promql" ? (r.unit || "") : m.unit;
    const lvT = r.level === "warning" ? "Warning" : "Error";
    if (r.metric === "service") return `${r.service} หยุด → ${lvT}`;
    if (r.metric === "process") return `${r.process} ไม่ทำงาน → ${lvT}`;
    if (r.metric === "port_listen") return `พอร์ต ${r.port} ไม่เปิดรับ → ${lvT}`;
    if (r.metric === "tcp_port") return `ต่อพอร์ต ${r.port} ไม่ได้ → ${lvT}`;
    if (r.metric === "http_url") return `ไม่ตอบ / HTTP ≥ 400 → ${lvT}`;
    const sym = r.op === "<" ? "≤" : "≥", out = [];
    if (r.warning !== undefined && r.warning !== null) out.push(`W ${sym} ${r.warning}${u}`);
    if (r.error !== undefined && r.error !== null) out.push(`E ${sym} ${r.error}${u}`);
    return out.join(" · ");
}

function rlRender() {
    const tb = $("rlRows"); tb.textContent = "";
    if (!RL.rules.length) {
        tb.append(el("tr", {}, el("td", { colspan: "8", class: "st-mut" }, "ยังไม่มีกฎ กด + Add rule")));
        return;
    }
    RL.rules.forEach((r, i) => {
        const on = el("input", { type: "checkbox", title: "เปิด / ปิดกฎนี้" });
        on.checked = r.enabled !== false;
        on.onchange = () => { r.enabled = on.checked; rlSave(`${on.checked ? "เปิด" : "ปิด"}กฎ ${r.name} แล้ว`); };
        const m = rlMetric(r.metric);
        const what = m.label + (r.mount ? ` · ${r.mount}` : "") + (r.metric === "promql" ? ` · ${r.promql}` : "")
            + (RL_PROC.includes(r.metric) ? ` · ${r.process}` : "") + (r.metric === "http_url" ? ` · ${r.url}` : "");
        tb.append(el("tr", { class: r.enabled === false ? "off" : "" },
            el("td", {}, on), el("td", {}, el("b", {}, r.name)), el("td", { class: "rlwhat" }, what),
            el("td", {}, rlWhere(r)), el("td", {}, rlCond(r)), el("td", {}, `${r.for_minutes} นาที`),
            el("td", { class: r.notify !== false && !r.teams_url_set && !RL.data.main_url_set ? "rlwarn" : "" },
                r.notify === false ? "ไม่ส่ง" : r.teams_url_set ? "ช่องของกฎ" : RL.data.main_url_set ? "ช่องหลัก" : "ยังไม่ตั้งช่อง"),
            el("td", { class: "act" },
                el("button", { class: "st-btn sm", type: "button", onclick: () => rlOpen(i) }, "Edit"),
                el("button", { class: "st-btn sm", type: "button", onclick: () => rlDelete(i) }, "Delete"))));
    });
}

async function rlLoad() {
    try {
        RL.data = await api("GET", "/api/alert-rules");
        RL.rules = RL.data.rules || [];
        const sel = $("rl_metric"); sel.textContent = "";
        RL.data.metrics.forEach(m => sel.append(el("option", { value: m.key }, m.label)));
        rlRender();
    } catch (e) { if (e.message !== "auth") say("rl_msgbox", "โหลดกฎไม่สำเร็จ: " + e.message, "bad"); }
}

function rlChecks(boxId, items, chosen) {
    const box = $(boxId); box.textContent = "";
    if (!items.length) { box.append(el("span", { class: "st-mut" }, "-")); return; }
    items.forEach(v => {
        const i = el("input", { type: "checkbox", value: v.value });
        i.checked = chosen.includes(v.value);
        box.append(el("label", { class: "st-check" }, i, " " + v.label));
    });
}

function rlShowFields() {
    const m = rlMetric($("rl_metric").value);
    document.querySelectorAll("#rlForm .rl-mount").forEach(n => { n.hidden = !m.per_mount; });
    document.querySelectorAll("#rlForm .rl-service").forEach(n => { n.hidden = m.key !== "service"; });
    document.querySelectorAll("#rlForm .rl-process").forEach(n => { n.hidden = !RL_PROC.includes(m.key); });
    document.querySelectorAll("#rlForm .rl-port").forEach(n => { n.hidden = !["port_listen", "tcp_port"].includes(m.key); });
    document.querySelectorAll("#rlForm .rl-url").forEach(n => { n.hidden = m.key !== "http_url"; });
    document.querySelectorAll("#rlForm .rl-state").forEach(n => { n.hidden = !RL_STATE.includes(m.key); });
    document.querySelectorAll("#rlForm .rl-promql").forEach(n => { n.hidden = m.key !== "promql"; });
    document.querySelectorAll("#rlForm .rl-num").forEach(n => { n.hidden = RL_STATE.includes(m.key); });
    if (RL.editing === null || !RL.rules[RL.editing] || RL.rules[RL.editing].metric !== m.key) $("rl_op").value = m.op || ">";
    if (m.os === "windows") $("rl_os").value = "windows";
}

function rlClearErr() {
    document.querySelectorAll("#rlForm [data-r2]").forEach(f => { f.classList.remove("has-err"); const x = f.querySelector(".st-err"); if (x) x.textContent = ""; });
}

function rlOpen(i) {
    RL.editing = i;
    const r = i === null ? { metric: "disk", op: ">", for_minutes: 3, notify: true, enabled: true, os: "any" } : RL.rules[i];
    $("rl_title").textContent = i === null ? "กฎใหม่" : `แก้กฎ: ${r.name}`;
    $("rl_name").value = r.name || ""; $("rl_metric").value = r.metric; $("rl_enabled").checked = r.enabled !== false;
    $("rl_mount").value = r.mount || ""; $("rl_service").value = r.service || "";
    $("rl_process").value = r.process || ""; $("rl_port").value = r.port || ""; $("rl_url").value = r.url || ""; $("rl_level").value = r.level || "error";
    $("rl_promql").value = r.promql || ""; $("rl_unit").value = r.unit || "";
    $("rl_op").value = r.op || ">"; $("rl_warn").value = r.warning ?? ""; $("rl_err").value = r.error ?? "";
    $("rl_for").value = r.for_minutes ?? 3; $("rl_os").value = r.os || "any"; $("rl_notify").checked = r.notify !== false;
    $("rl_msg").value = r.message || "";
    $("rl_teams").value = "";
    $("rl_teams_hint").textContent = r.teams_url_set ? `ตั้งไว้แล้ว: ${r.teams_url_hint} · ใส่ - เพื่อลบ (กลับไปใช้ช่องหลัก)`
        : RL.data.main_url_set ? "ตอนนี้ใช้ช่องหลัก" : "ยังไม่มีช่องหลัก: ใส่ URL ที่นี่ หรือตั้งในการ์ด Notifications";
    rlTeamsShow();
    rlChecks("rl_targets", (RL.data.targets || []).map(t => ({ value: t.name, label: `${t.name}${t.os === "windows" ? " (Win)" : ""}` })), r.targets || []);
    rlChecks("rl_groups", (RL.data.groups || []).map(g => ({ value: g, label: g })), r.groups || []);
    rlShowFields(); rlClearErr(); $("rl_pv").textContent = "";
    $("rlForm").hidden = false;
    $("rlForm").scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("rl_name").focus();
}

function rlRead() {
    const m = $("rl_metric").value, picked = id => [...$(id).querySelectorAll("input:checked")].map(i => i.value);
    const r = {
        name: $("rl_name").value.trim(), metric: m, enabled: $("rl_enabled").checked,
        targets: picked("rl_targets"), groups: picked("rl_groups"), os: $("rl_os").value,
        for_minutes: $("rl_for").value, notify: $("rl_notify").checked, message: $("rl_msg").value.trim(),
    };
    if (RL.editing !== null) r.id = RL.rules[RL.editing].id;
    if (rlMetric(m).per_mount) r.mount = $("rl_mount").value.trim();
    if (m === "service") r.service = $("rl_service").value.trim();
    if (RL_PROC.includes(m)) r.process = $("rl_process").value.trim();
    if (m === "port_listen" || m === "tcp_port") r.port = $("rl_port").value.trim();
    if (m === "http_url") r.url = $("rl_url").value.trim();
    if (RL_STATE.includes(m)) r.level = $("rl_level").value;
    else { r.op = $("rl_op").value; r.warning = $("rl_warn").value.trim(); r.error = $("rl_err").value.trim(); }
    if (m === "promql") { r.promql = $("rl_promql").value.trim(); r.unit = $("rl_unit").value.trim(); }
    if ($("rl_teams").value.trim()) r.teams_url = $("rl_teams").value.trim();
    return r;
}

function rlShowErr(e, index) {
    const left = [];
    Object.entries((e && e.fields) || {}).forEach(([k, msg]) => {
        const key = index === undefined ? k : k.replace(new RegExp(`^rules\\.${index}\\.`), "");
        const f = document.querySelector(`#rlForm [data-r2="${key}"]`);
        if (f) { f.classList.add("has-err"); f.querySelector(".st-err").textContent = msg; } else left.push(msg);
    });
    return left.join(" · ");
}

async function rlSave(okText) {
    try {
        const d = await api("PUT", "/api/alert-rules", { rules: RL.rules });
        RL.data = d; RL.rules = d.rules || [];
        rlRender();
        say("rl_msgbox", okText || "บันทึกแล้ว · puller ใช้กฎใหม่ในรอบตรวจถัดไป (~30 วินาที)", "ok");
        return true;
    } catch (e) {
        say("rl_msgbox", "บันทึกไม่สำเร็จ: " + e.message, "bad");
        return e;
    }
}

async function rlSubmit() {
    rlClearErr();
    const r = rlRead(), backup = RL.rules.slice();
    const idx = RL.editing === null ? RL.rules.length : RL.editing;
    RL.rules = RL.rules.slice(); RL.rules[idx] = r;
    $("rl_save").disabled = true;
    const res = await rlSave(`บันทึกกฎ "${r.name}" แล้ว · เริ่มตรวจในรอบถัดไป (~30 วินาที)`);
    $("rl_save").disabled = false;
    if (res === true) { $("rlForm").hidden = true; RL.editing = null; return; }
    RL.rules = backup; rlRender();
    const left = rlShowErr(res, idx);
    if (left) say("rl_msgbox", "บันทึกไม่สำเร็จ: " + left, "bad");
}

async function rlDelete(i) {
    const r = RL.rules[i];
    if (!confirm(`ลบกฎ "${r.name}" ?`)) return;
    const backup = RL.rules.slice();
    RL.rules = RL.rules.filter((_, k) => k !== i);
    if ((await rlSave(`ลบกฎ "${r.name}" แล้ว`)) !== true) { RL.rules = backup; rlRender(); }
}

function rlTeamsShow() { document.querySelectorAll("#rlForm .rl-teams").forEach(n => { n.hidden = !$("rl_notify").checked; }); }

async function rlTest() {
    rlClearErr();
    $("rl_test").disabled = true; say("rl_msgbox", "กำลังส่งการ์ดตัวอย่าง...", "");
    try { const d = await api("POST", "/api/alert-rules/test", rlRead()); say("rl_msgbox", d.message + " · ดูที่ช่อง Teams", "ok"); }
    catch (e) { const left = rlShowErr(e); say("rl_msgbox", "ส่งไม่สำเร็จ: " + (left || e.message), "bad"); }
    finally { $("rl_test").disabled = false; }
}

const RL_ST = { ok: ["OK", "ok"], warning: ["Warning", "wr"], error: ["Error", "cr"], nodata: ["ไม่มีข้อมูล", ""] };
async function rlPreview() {
    rlClearErr();
    const box = $("rl_pv"); box.textContent = "กำลังคำนวณ...";
    try {
        const d = await api("POST", "/api/alert-rules/preview", rlRead());
        box.textContent = "";
        const rows = d.rows || [];
        if (!rows.length) { box.append(el("div", { class: "st-mut" }, "ไม่มีเครื่องที่ตรงกับกฎนี้")); return; }
        const tb = el("tbody");
        rows.forEach(x => {
            const [t, c] = RL_ST[x.status] || [x.status, ""];
            tb.append(el("tr", {}, el("td", {}, x.machine), el("td", {}, x.name || "-"),
                el("td", {}, x.message || "-"), el("td", {}, el("span", { class: "ovst " + c }, t))));
        });
        box.append(el("div", { class: "st-mut", style: "margin:10px 0 4px" }, "ค่าตอนนี้ (ยังไม่ได้บันทึก · ยังไม่นับเวลาต่อเนื่อง)"),
            el("table", { class: "st-tbl rltbl" }, el("thead", {}, el("tr", {}, el("th", {}, "เครื่อง"), el("th", {}, "sensor"), el("th", {}, "ค่า"), el("th", {}, "สถานะ"))), tb));
    } catch (e) {
        box.textContent = "";
        const left = rlShowErr(e);
        box.append(el("div", { class: "st-msg bad" }, "ทดลองไม่ได้: " + (left || e.message)));
    }
}

document.addEventListener("DOMContentLoaded", () => {
    if (!$("rlRows")) return;
    $("r_add").onclick = () => rlOpen(null);
    $("rl_cancel").onclick = () => { $("rlForm").hidden = true; RL.editing = null; };
    $("rl_save").onclick = rlSubmit;
    $("rl_preview").onclick = rlPreview;
    $("rl_test").onclick = rlTest;
    $("rl_notify").addEventListener("change", rlTeamsShow);
    $("rl_metric").addEventListener("change", rlShowFields);
    rlLoad();
});
