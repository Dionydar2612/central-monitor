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

const TH_KINDS = ["cpu", "memory", "disk"];

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
        renderTable(); fillGroups();
    } catch (e) { if (e.message !== "auth") say("listMsg", "โหลดรายการไม่สำเร็จ: " + e.message, "bad"); }
}

function renderTable() {
    const tb = $("tbody"); tb.textContent = "";
    if (!S.targets.length) {
        tb.append(el("tr", {}, el("td", { colspan: "8", class: "empty" }, "ยังไม่มีเครื่อง กด + Add target")));
        return;
    }
    S.targets.forEach(t => {
        const port = t.ssh.port && Number(t.ssh.port) !== 22 ? ":" + t.ssh.port : "";
        tb.append(el("tr", {},
            el("td", {}, el("b", {}, t.name)),
            el("td", {}, t.group || "-"),
            el("td", {}, t.host + port),
            el("td", {}, t.ssh.user || "-"),
            el("td", {}, String(t.logs.length)),
            el("td", { class: t.host_key_known ? "ok" : "wr" }, t.host_key_known ? "✓ known" : "⚠ missing"),
            el("td", { class: t.paused ? "wr" : "ok" }, t.paused ? "⏸ Paused" : "Active"),
            el("td", {}, el("div", { class: "st-actions" },
                el("button", { class: "st-btn sm", onclick: () => pauseTarget(t) }, t.paused ? "Resume" : "Pause"),
                el("button", { class: "st-btn sm", onclick: () => openForm(t) }, "Edit"),
                el("button", { class: "st-btn sm", onclick: () => { openForm(t); $("dp_pass").focus(); } }, "Deploy key"),
                el("button", { class: "st-btn sm danger", onclick: () => removeTarget(t) }, "Delete")))));
    });
}

async function pauseTarget(t) {
    try {
        await api("PUT", `/api/targets/${encodeURIComponent(t.name)}/pause`, { paused: !t.paused });
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
}

async function removeTarget(t) {
    if (!confirm(`ลบเครื่อง "${t.name}" ออกจาก puller.yaml ?\n(ข้อมูล log ที่เก็บไว้แล้วไม่ถูกลบ)`)) return;
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
    return el("div", { class: "st-logrow" },
        mk("n", "name", "name", item.name, "application", "logNames"),
        mk("p", "path", "path", item.path, "/var/log/myapp/app.log  หรือ  journal:nginx.service"),
        mk("t", "type", "type", item.type || "application", "application"),
        el("button", { class: "st-btn sm danger", type: "button", onclick: e => e.target.closest(".st-logrow").remove() }, "✕"));
}

function openForm(t) {
    S.editing = t ? t.name : null; S.scanned = []; S.hostKeyKnown = !!(t && t.host_key_known);
    $("formTitle").textContent = t ? `Edit target: ${t.name}` : "Add target";
    $("f_name").value = t ? t.name : ""; $("f_name").disabled = !!t;
    $("f_group").value = t ? (t.group || "") : "";
    $("f_paused").checked = !!(t && t.paused);
    TH_KINDS.forEach(k => ["warning", "error"].forEach(lv => {
        const v = t && t.thresholds && t.thresholds[k] ? t.thresholds[k][lv] : "";
        $(`th_${k}_${lv}`).value = v ?? "";
    }));
    $("f_host").value = t ? t.host : "";
    $("f_port").value = t ? (t.ssh.port || 22) : 22;
    $("f_user").value = t ? t.ssh.user : "central-pull";
    $("f_job").value = t ? (t.prometheus.job || "") : "";
    $("f_inst").value = t ? (t.prometheus.instance || "") : "";
    const logs = $("logs"); logs.textContent = "";
    (t && t.logs.length ? t.logs : [null]).forEach(l => logs.append(logRow(l)));
    clearErrors(); say("formMsg", ""); say("deployMsg", "");
    $("dp_admin").value = ""; $("dp_pass").value = "";
    $("hkKeys").textContent = "";
    renderHostKeyStatus(); renderSetup();
    $("formCard").hidden = false;
    $("formCard").scrollIntoView({ behavior: "smooth", block: "start" });
    (t ? $("f_host") : $("f_name")).focus();
}

function closeForm() { $("formCard").hidden = true; S.editing = null; }

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
    return general.join(" · ");
}

function readForm() {
    const logs = [...$("logs").children].map(r => ({
        name: r.querySelector('[data-k="name"]').value.trim(),
        path: r.querySelector('[data-k="path"]').value.trim(),
        type: r.querySelector('[data-k="type"]').value.trim(),
    }));
    return {
        name: $("f_name").value.trim(),
        group: $("f_group").value.trim(),
        paused: $("f_paused").checked,
        thresholds: Object.fromEntries(TH_KINDS.map(k => [k, { warning: $(`th_${k}_warning`).value.trim(), error: $(`th_${k}_error`).value.trim() }])),
        host: $("f_host").value.trim(),
        ssh: { port: $("f_port").value.trim() || 22, user: $("f_user").value.trim() },
        prometheus: { job: $("f_job").value.trim(), instance: $("f_inst").value.trim() },
        logs,
    };
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
    const trust = S.scanned.length && $("hkTrust") && $("hkTrust").checked;
    if (trust) body.trust_fingerprints = S.scanned.map(k => k.fingerprint);
    else if (!S.hostKeyKnown && !confirm("ยังไม่ได้เก็บ SSH host key ของเครื่องนี้\npuller จะดึง log ไม่ได้จนกว่าจะเก็บ host key\n\nบันทึกต่อไหม?")) return;

    const b = $("saveBtn"); b.disabled = true;
    try {
        if (S.editing) await api("PUT", "/api/targets/" + encodeURIComponent(S.editing), body);
        else await api("POST", "/api/targets", body);
        const name = S.editing || body.name;
        const dep = await deployKey(name);            // null = ไม่ได้กรอกรหัสผ่าน (ข้ามการวาง key)
        closeForm(); await loadTargets();
        if (dep && dep.ok) say("listMsg", `บันทึก ${name} แล้ว · ${dep.message}`, "ok");
        else if (dep) say("listMsg", `บันทึก ${name} แล้ว แต่วาง public key ไม่สำเร็จ: ${dep.message} — กด Deploy key ที่แถวของเครื่องนี้เพื่อลองใหม่ หรือวางเองตามคำสั่งในหน้า Edit`, "bad");
        else say("listMsg", `บันทึก ${name} แล้ว — puller จะดึงเครื่องนี้ภายในไม่กี่วินาที (Prometheus เห็นเครื่องใหม่ทันที)`, "ok");
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
    Object.entries(CHECK_FIELDS).forEach(([k, id]) => { $(id).value = c && c[k] != null ? c[k] : ""; });
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

async function loadUser() {
    try { const d = await api("GET", "/api/me"); $("currentUsername").textContent = d.username || "?"; } catch (e) { /* api() พาไป /login เอง */ }
}

async function logout() {
    $("logoutButton").disabled = true;
    try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } finally { window.location.href = "/login"; }
}

document.addEventListener("DOMContentLoaded", () => {
    const dl = el("datalist", { id: "logNames" });
    ["application", "logapp", "loginuser", "journal"].forEach(v => dl.append(el("option", { value: v })));
    document.body.append(dl);
    $("addBtn").onclick = () => openForm(null);
    $("cancelBtn").onclick = closeForm;
    $("saveBtn").onclick = save;
    $("addLog").onclick = () => $("logs").append(logRow(null));
    $("addJournal").onclick = () => $("logs").append(logRow({ name: "journal", path: "journal", type: "system" }));
    $("scanBtn").onclick = scanHostKey;
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
    loadUser(); loadTargets(); loadUsers();
    if (SHOW_CHECKS) loadChecks();
});
