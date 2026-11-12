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

const TH_KINDS = ["cpu", "memory", "disk", "disk_latency", "packet_loss", "net_latency", "mem_available"];

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
        tb.append(el("tr", {}, el("td", { colspan: "9", class: "empty" }, "ยังไม่มีเครื่อง กด + Add target")));
        return;
    }
    S.targets.forEach(t => {
        if (t.type === "agent") {
            const n = (t.agent.logs || []).length;
            tb.append(el("tr", {},
                el("td", {}, el("b", {}, t.name)),
                el("td", {}, "Windows (agent)"),
                el("td", {}, t.group || "-"),
                el("td", {}, t.host || "-"),
                el("td", {}, "-"),
                el("td", {}, `${(t.agent.channels || []).length} event log · ${n} file`),
                el("td", {}, "Fluent Bit"),
                el("td", { class: t.paused ? "wr" : "ok" }, t.paused ? "⏸ Paused" : "Active"),
                el("td", {}, el("div", { class: "st-actions" },
                    el("button", { class: "st-btn sm", onclick: () => pauseTarget(t) }, t.paused ? "Resume" : "Pause"),
                    el("button", { class: "st-btn sm", onclick: () => openForm(t) }, "Edit"),
                    el("button", { class: "st-btn sm", onclick: () => { openForm(t); $("agCentral").focus(); } }, "Install command"),
                    el("button", { class: "st-btn sm danger", onclick: () => removeTarget(t) }, "Delete")))));
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

/* ชนิดเครื่อง: ssh = แสดงช่อง SSH/Prometheus/log path แบบ Linux · agent = ช่อง Windows + คำสั่งติดตั้ง */
function setType(type) {
    $("f_type").value = type;
    const agent = type === "agent";
    document.querySelectorAll("#formCard .ssh-only").forEach(n => { n.hidden = agent; });
    document.querySelectorAll("#formCard .agent-only").forEach(n => { n.hidden = !agent; });
    $("f_type_hint").textContent = agent ? "เครื่องส่งข้อมูลเข้ามาเอง (ไม่ต้องเปิด SSH) · Host ไม่บังคับ" : "central เข้าไปดึง log ทาง SSH";
    $("f_host").placeholder = agent ? "(ไม่บังคับ) 10.0.0.21" : "10.0.10.13";
}

function agentCentralGuess() {
    try { const v = localStorage.getItem("cm.agentCentral"); if (v) return v; } catch (e) { /* ignore */ }
    return location.hostname === "localhost" || location.hostname === "127.0.0.1" ? "" : location.hostname;
}

async function makeAgentCommand() {
    const name = S.editing;
    if (!name) { $("agStatus").textContent = "บันทึกเครื่องก่อน แล้วกด Install command ที่แถวของเครื่องนี้"; return; }
    const central = $("agCentral").value.trim();
    try { localStorage.setItem("cm.agentCentral", central); } catch (e) { /* ignore */ }
    try {
        const d = await api("GET", `/api/targets/${encodeURIComponent(name)}/agent-setup${central ? "?central=" + encodeURIComponent(central) : ""}`);
        $("agCmd").value = d.command;
        $("agPort").textContent = d.port;
        if (!$("agCentral").value) $("agCentral").value = d.central;
        const st = d.status || {};
        const warn = [!d.token_set && "ยังไม่มี CENTRAL_AGENT_TOKEN ใน docker/.env (รัน cm-update)",
                      !d.ca_available && "ยังไม่มีใบรับรอง keys/agent-tls (รัน cm-update)"].filter(Boolean);
        $("agStatus").textContent = (st.online ? `✓ agent ส่งข้อมูลอยู่ · last seen ${new Date(st.last_seen).toLocaleString("en-GB")}`
            : st.last_seen ? `⚠ ไม่ได้รับข้อมูลจาก agent ตั้งแต่ ${new Date(st.last_seen).toLocaleString("en-GB")}`
            : "ยังไม่ได้รับข้อมูลจาก agent (ยังไม่ได้ติดตั้ง หรือเครื่องต่อมาไม่ได้)") + (warn.length ? " · " + warn.join(" · ") : "");
        $("agStatus").className = st.online ? "ok" : warn.length ? "wr" : "";
    } catch (e) { if (e.message !== "auth") $("agStatus").textContent = "สร้างคำสั่งไม่สำเร็จ: " + e.message; }
}

function openForm(t) {
    S.editing = t ? t.name : null; S.scanned = []; S.hostKeyKnown = !!(t && t.host_key_known);
    setType(t ? (t.type || "ssh") : "ssh");
    $("f_type").disabled = !!t;
    $("agCmd").value = ""; $("agStatus").textContent = "บันทึกเครื่องก่อน แล้วคำสั่งติดตั้งจะขึ้นที่นี่"; $("agStatus").className = "";
    $("agCentral").value = agentCentralGuess();
    $("a_channels").value = t && t.agent ? (t.agent.channels || []).join(", ") : "System, Application";
    $("a_logs").value = t && t.agent ? (t.agent.logs || []).join("\n") : "";
    $("a_crit").value = t && Array.isArray(t.critical_services) ? (t.critical_services.length ? t.critical_services.join(", ") : "-") : "";
    if (t && t.type === "agent") {
        $("formTitle").textContent = `Edit target: ${t.name}`;
        $("f_name").value = t.name; $("f_name").disabled = true;
        $("f_group").value = t.group || ""; $("f_paused").checked = !!t.paused; $("f_host").value = t.host || "";
        TH_KINDS.forEach(k => ["warning", "error"].forEach(lv => { $(`th_${k}_${lv}`).value = t.thresholds && t.thresholds[k] ? (t.thresholds[k][lv] ?? "") : ""; }));
        clearErrors(); say("formMsg", "");
        $("formCard").hidden = false;
        $("formCard").scrollIntoView({ behavior: "smooth", block: "start" });
        makeAgentCommand();
        return;
    }
    $("formTitle").textContent = t ? `Edit target: ${t.name}` : "Add target";
    $("f_name").value = t ? t.name : ""; $("f_name").disabled = !!t;
    $("f_group").value = t ? (t.group || "") : "";
    $("f_paused").checked = !!(t && t.paused);
    TH_KINDS.forEach(k => ["warning", "error"].forEach(lv => {
        const v = t && t.thresholds && t.thresholds[k] ? t.thresholds[k][lv] : "";
        $(`th_${k}_${lv}`).value = v ?? "";
    }));
    $("f_host").value = t ? t.host : "";
    $("f_port").value = t ? ((t.ssh && t.ssh.port) || 22) : 22;
    $("f_user").value = t ? t.ssh.user : "central-pull";
    $("f_job").value = t ? (t.prometheus.job || "") : "";
    $("f_inst").value = t ? (t.prometheus.instance || "") : "";
    $("f_scheme").value = t && t.prometheus ? (t.prometheus.scheme || "http") : "http";
    $("f_mpath").value = t && t.prometheus ? (t.prometheus.metrics_path || "") : "";
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
    if ($("f_type").value === "agent") {
        return {
            type: "agent",
            name: $("f_name").value.trim(),
            group: $("f_group").value.trim(),
            paused: $("f_paused").checked,
            host: $("f_host").value.trim(),
            thresholds: Object.fromEntries(TH_KINDS.map(k => [k, { warning: $(`th_${k}_warning`).value.trim(), error: $(`th_${k}_error`).value.trim() }])),
            /* ว่าง = ค่าทั้งระบบ (null) · "-" = ไม่ตรวจ ([]) */
            critical_services: $("a_crit").value.trim() === "" ? null : $("a_crit").value.trim() === "-" ? [] : $("a_crit").value.split(",").map(s => s.trim()).filter(Boolean),
            agent: {
                logs: $("a_logs").value.split(/\r?\n/).map(s => s.trim()).filter(Boolean),
                channels: $("a_channels").value.split(",").map(s => s.trim()).filter(Boolean),
            },
        };
    }
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
        prometheus: { job: $("f_job").value.trim(), instance: $("f_inst").value.trim(),
                      scheme: $("f_scheme").value, metrics_path: $("f_mpath").value.trim() },
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
    if (body.type === "agent") return saveAgent(body);
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

/* บันทึกเครื่อง Windows (agent) แล้วเปิดฟอร์มค้างไว้เพื่อแสดงคำสั่งติดตั้ง */
async function saveAgent(body) {
    const b = $("saveBtn"); b.disabled = true;
    try {
        if (S.editing) await api("PUT", "/api/targets/" + encodeURIComponent(S.editing), body);
        else await api("POST", "/api/targets", body);
        S.editing = body.name || S.editing;
        $("f_name").disabled = true; $("f_type").disabled = true;
        $("formTitle").textContent = `Edit target: ${S.editing}`;
        say("formMsg", `บันทึก ${S.editing} แล้ว — ติดตั้ง agent ตามขั้นตอนด้านบน`, "ok");
        await loadTargets();
        await makeAgentCommand();
        $("agCmd").scrollIntoView({ behavior: "smooth", block: "center" });
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
};
function alInput(id, v) { return el("input", { id, type: "number", step: "any", min: "0", inputmode: "decimal", value: v ?? "" }); }
function renderAlerts(d) {
    $("al_min").value = d.alert_minutes; $("al_hyst").value = d.hysteresis; $("al_bn").checked = !!d.bottleneck;
    $("al_crit").value = (d.critical_services || []).join(", ");
    const tb = $("alRows"); tb.textContent = "";
    (d.kinds || Object.keys(AL_LABEL)).forEach(k => {
        const lx = (d.thresholds || {})[k] || {}, wn = (d.thresholds_windows || {})[k] || {};
        const cell = (os, lv, v) => el("td", { "data-al": `${os}.${k}.${lv}` }, alInput(`al_${os}_${k}_${lv}`, v), el("div", { class: "st-err" }));
        const tr = el("tr", {}, el("td", {}, AL_LABEL[k] || k),
            k === "mem_available" ? el("td", { colspan: "2", class: "st-mut" }, "Windows เท่านั้น (Linux ดูที่ Memory used %)") : [cell("thresholds", "warning", lx.warning), cell("thresholds", "error", lx.error)],
            cell("thresholds_windows", "warning", wn.warning), cell("thresholds_windows", "error", wn.error));
        tb.append(tr);
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
        critical_services: $("al_crit").value.split(",").map(s => s.trim()).filter(Boolean),
        thresholds: th("thresholds"), thresholds_windows: th("thresholds_windows"),
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
        critical_services: AL.defaults.critical_services, thresholds: AL.defaults.thresholds, thresholds_windows: AL.defaults.thresholds_windows });
    say("al_msg", "ใส่ค่าเริ่มต้นแล้ว กด Save alerts เพื่อบันทึก", "");
}

/* ---------- Notifications: Microsoft Teams (/api/notify-settings) ---------- */
const N_LABEL = { down: "เครื่อง Down", error: "Error (disk เต็ม, service หยุด, ...)", warning: "Warning",
    recovered: "กลับมาปกติ (Recovered)", checks: "Web / SSL check", software: "ติดตั้ง / ถอน / อัปเดตโปรแกรม (Windows)" };
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
    $("r_save").onclick = saveRetention;
    $("al_save").onclick = saveAlerts;
    $("al_reset").onclick = resetAlerts;
    $("n_save").onclick = () => saveNotify();
    $("n_test").onclick = testNotify;
    $("n_popos").addEventListener("change", async e => {
        if (!window.cmPopup) return;
        const want = e.target.checked, ok = await window.cmPopup.setOs(want);
        e.target.checked = ok;
        say("n_msg", ok ? "เปิดแจ้งเตือนของ Windows บน browser นี้แล้ว"
            : want ? "browser ไม่อนุญาต: กดไอคอนแม่กุญแจหน้า URL > Notifications > Allow แล้วติ๊กใหม่" : "ปิดแจ้งเตือนของ Windows บน browser นี้แล้ว", ok || !want ? "ok" : "bad");
    });
    $("n_poptest").onclick = () => { if (window.cmPopup) window.cmPopup.test(); };
    $("n_sumnow").onclick = async () => {
        try { const d = await api("POST", "/api/notify-settings/summary-now", {}); say("n_msg", d.message, "ok"); }
        catch (e) { say("n_msg", "ส่งไม่ได้: " + e.message, "bad"); }
    };
    $("n_clear").onclick = () => { if (confirm("ลบ webhook URL? (หยุดส่งแจ้งเตือนไป Teams)")) saveNotify({ webhook_url: "-", enabled: false }); };
    $("f_type").addEventListener("change", e => setType(e.target.value));
    $("agMake").onclick = makeAgentCommand;
    $("agCopy").onclick = async () => {
        const t = $("agCmd"); if (!t.value) return; t.select();
        try { await navigator.clipboard.writeText(t.value); } catch (e) { document.execCommand("copy"); }
        $("agCopyMsg").textContent = "คัดลอกแล้ว"; setTimeout(() => { $("agCopyMsg").textContent = ""; }, 2500);
    };
    ["r_log", "r_log_u"].forEach(id => $(id).addEventListener("input", retentionWarn));
    loadUser(); loadTargets(); loadUsers(); loadRetention(); loadAlerts(); loadNotify();
    if (SHOW_CHECKS) loadChecks();
});
