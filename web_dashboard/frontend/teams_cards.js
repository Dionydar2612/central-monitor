/*
  teams_cards.js - Settings > รูปแบบการ์ด Teams (/api/notify-settings/cards)
    แต่ละหัวข้อ (Alert, Custom rules, Netstat Queue / Log, Netstat พอร์ต, Web check, Software) ตั้งได้:
    หัวเรื่อง · ช่องที่แสดง (เปิด / ปิด, เรียงลำดับ, เปลี่ยนชื่อ) · ข้อความท้ายการ์ด  + ดูตัวอย่าง + ส่งทดสอบ
    ใช้ $, api, say จาก settings.js
*/
(function () {
    const C = { data: null, cat: "alert", edit: {} };
    const esc = v => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    const COLOR = { alert: "#c4314b", rule: "#c4314b", queue: "#c4314b", netstat: "#c4314b", web: "#c4314b", software: "#5b5fc7" };

    function cur() { return C.edit[C.cat] || (C.edit[C.cat] = JSON.parse(JSON.stringify(C.data.cards[C.cat]))); }
    function render(t, ctx) {
        return String(t || "").replace(/\{(\w+)\}/g, (m, k) => ctx[k] == null ? "" : String(ctx[k])).replace(/[ \t]{2,}/g, " ").replace(/\[\]/g, "").trim();
    }
    function ctxOf(cat) {
        const s = C.data.samples[cat] || {};
        return { prefix: "Central Monitor Notification", ...s, device: s.machine + (s.host ? ` [${s.host}]` : ""),
            time: new Date().toLocaleString("en-US"), duration: "5 นาที" };
    }
    function draw() {
        const d = C.data, t = cur(), all = Object.keys(d.fields);
        $("tcCat").innerHTML = Object.entries(d.categories).map(([k, v]) => `<option value="${k}"${k === C.cat ? " selected" : ""}>${esc(v)}${d.custom.includes(k) ? " · ปรับแล้ว" : ""}</option>`).join("");
        $("tcTitle").value = t.title;
        $("tcNote").value = t.note || "";
        const order = t.fields.concat(all.filter(f => !t.fields.includes(f)));
        $("tcFields").innerHTML = order.map(f => {
            const on = t.fields.includes(f);
            return `<div class="tcF${on ? "" : " off"}" data-f="${f}"><label class="st-check"><input type="checkbox" ${on ? "checked" : ""}> <code>${f}</code></label>`
                + `<input class="tcL" value="${esc(t.labels[f] || "")}" placeholder="${esc(d.fields[f])}" ${on ? "" : "disabled"}>`
                + `<button type="button" class="st-btn sm" data-mv="-1" ${on ? "" : "disabled"}>↑</button><button type="button" class="st-btn sm" data-mv="1" ${on ? "" : "disabled"}>↓</button></div>`;
        }).join("");
        const mo = (d.mute_options || {})[C.cat] || {};
        $("tcMuteF").hidden = !Object.keys(mo).length;
        $("tcMute").innerHTML = Object.entries(mo).map(([k, v]) => `<label class="st-check"><input type="checkbox" value="${k}" ${(t.mute || []).includes(k) ? "checked" : ""}> ${esc(v)}</label>`).join("");
                $("tcVars").textContent = d.vars.map(v => `{${v}}`).join("  ");
        preview();
    }
    function preview() {
        const t = cur(), ctx = ctxOf(C.cat), d = C.data;
        let h = `<div class="tcTitle" style="color:${COLOR[C.cat]}">${esc(render(t.title, ctx) || render(d.defaults[C.cat].title, ctx))}</div>`;
        t.fields.forEach(f => {
            let v = ctx[f];
            if (Array.isArray(v)) v = v.join("\n");
            if (v == null || v === "") return;
            h += `<b>${esc(t.labels[f] || d.fields[f])}:</b><div>${esc(v).replace(/\n/g, "<br>")}</div>`;
        });
        const note = render(t.note, ctx);
        if (note) h += `<hr><div>${esc(note)}</div>`;
        $("tcPrev").innerHTML = h;
    }
    function readForm() {
        const t = cur();
        t.title = $("tcTitle").value; t.note = $("tcNote").value;
        t.fields = [...$("tcFields").querySelectorAll(".tcF")].filter(r => r.querySelector("input[type=checkbox]").checked).map(r => r.dataset.f);
        t.mute = [...$("tcMute").querySelectorAll("input:checked")].map(x => x.value);
        t.labels = {};
        $("tcFields").querySelectorAll(".tcF").forEach(r => { const v = r.querySelector(".tcL").value.trim(); if (v) t.labels[r.dataset.f] = v; });
    }
    async function load() {
        try { C.data = await api("GET", "/api/notify-settings/cards"); C.edit = {}; $("tcCard").hidden = false; draw(); }
        catch (e) { /* ไม่ใช่ admin */ }
    }
    function bind() {
        $("tcCat").onchange = () => { readForm(); C.cat = $("tcCat").value; draw(); };
        $("tcMute").onchange = () => readForm();
        $("tcTitle").oninput = $("tcNote").oninput = () => { readForm(); preview(); };
        $("tcFields").oninput = $("tcFields").onchange = e => { readForm(); if (e.target.type === "checkbox") draw(); else preview(); };
        $("tcFields").onclick = e => {
            const b = e.target.closest("[data-mv]"); if (!b) return;
            readForm();
            const t = cur(), f = b.closest(".tcF").dataset.f, i = t.fields.indexOf(f), j = i + Number(b.dataset.mv);
            if (i < 0 || j < 0 || j >= t.fields.length) return;
            [t.fields[i], t.fields[j]] = [t.fields[j], t.fields[i]]; draw();
        };
        $("tcSave").onclick = async () => {
            readForm();
            try { C.data = await api("PUT", "/api/notify-settings/cards", { cards: C.edit }); C.edit = {}; draw(); say("tcMsg", "บันทึกแล้ว (มีผลกับการแจ้งเตือนครั้งถัดไป)", "ok"); }
            catch (e) { say("tcMsg", e.fields ? Object.values(e.fields).join(" · ") : e.message, "err"); }
        };
        $("tcReset").onclick = async () => {
            try { C.data = await api("PUT", "/api/notify-settings/cards", { reset: [C.cat] }); delete C.edit[C.cat]; draw(); say("tcMsg", "คืนค่าเดิมแล้ว", "ok"); }
            catch (e) { say("tcMsg", e.message, "err"); }
        };
        $("tcTest").onclick = async () => {
            readForm(); say("tcMsg", "กำลังส่ง…");
            try { const r = await api("POST", "/api/notify-settings/cards/test", { cat: C.cat, card: cur() }); say("tcMsg", "ส่งการ์ดตัวอย่างแล้ว · " + r.message, "ok"); }
            catch (e) { say("tcMsg", e.message, "err"); }
        };
    }
    bind(); load();
})();
