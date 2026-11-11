/* =====================================================================
   popup.js - แจ้งเตือนแบบป๊อปอัป (มุมขวาล่าง แบบ Windows toast) ทุกหน้า
     ข้อมูล: GET /api/events?kind=system&since=<ล่าสุด> ทุก 15 วินาที
             (sensor / Monitor เปลี่ยนสถานะ, กลับปกติ, ติดตั้ง/ถอนโปรแกรม  <- puller เขียน data/state/events.log)
     ป๊อปอัปในหน้าเว็บ: แสดงเสมอ (ปิดได้ที่ปุ่ม 🔔)  ·  แจ้งเตือนของ Windows (desktop): เปิดที่ปุ่ม 🔔
       (browser อนุญาตเฉพาะเว็บที่เปิดผ่าน https:// หรือ http://localhost)
     ไม่แสดงของเก่า: เริ่มนับจากตอนเปิดหน้า · หลายแท็บใช้เวลาล่าสุดร่วมกัน (ไม่เด้งซ้ำ)
   ใช้ร่วมกันใน index.html / apps.html / events.html / settings.html (ต่อจาก badges.js)
   ===================================================================== */
"use strict";
(function () {
    const POLL_MS = 15000, MAX_TOASTS = 4;
    const K_LAST = "cm.popup.last", K_ON = "cm.popup.on", K_OS = "cm.popup.os";
    const LV = {
        down: ["🔴", "Down", "bad"], error: ["🔴", "Error", "bad"], warning: ["🟡", "Warning", "wr"],
        recovered: ["🟢", "กลับมาปกติ", "ok"], info: ["💿", "Software", "info"],
    };
    const get = (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } };
    const set = (k, v) => { try { localStorage.setItem(k, String(v)); } catch (e) { /* ignore */ } };
    const on = () => get(K_ON, "1") === "1";
    const osOn = () => get(K_OS, "0") === "1" && "Notification" in window && Notification.permission === "granted";
    const esc = v => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    let last = Math.max(Number(get(K_LAST, "0")) || 0, Date.now() / 1000 - 5);
    set(K_LAST, last);

    /* ---------- กล่องป๊อปอัป ---------- */
    const stack = document.createElement("div");
    stack.className = "cmToasts"; stack.setAttribute("role", "status"); stack.setAttribute("aria-live", "polite");
    document.addEventListener("DOMContentLoaded", () => document.body.append(stack));
    if (document.body) document.body.append(stack);

    function link(e) {
        if (e.source === "software" && e.web) return `/?web=${encodeURIComponent(e.web)}`;
        if (/:rule:/.test(String(e.sid || ""))) return "/apps";
        return e.web ? `/?web=${encodeURIComponent(e.web)}` : "/events?tab=system";
    }
    function toast(e, force) {
        const [icon, label, cls] = LV[e.level] || LV.info;
        const title = [e.web, e.sensor].filter(Boolean).join(" · ") || "Central Monitor";
        const msg = String(e.message || "").replace(new RegExp(`^${(e.web || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} · `), "");
        if (osOn()) {
            try {
                const n = new Notification(`${icon} ${title}`, { body: msg, tag: `cm-${e.sid || title}-${e.level}`, renotify: true });
                n.onclick = () => { window.focus(); location.href = link(e); n.close(); };
            } catch (err) { /* browser ไม่รองรับ */ }
        }
        if (!on() && !force) return;
        const el = document.createElement("a");
        el.className = `cmToast ${cls}`; el.href = link(e);
        el.innerHTML = `<span class="cmTi">${icon}</span><span class="cmTb"><small>Central Monitor · ${esc(label)} · ${esc(new Date((e.time || Date.now() / 1000) * 1000).toLocaleTimeString("en-GB"))}</small>`
            + `<b>${esc(title)}</b><span>${esc(msg)}</span></span><button type="button" class="cmTx" aria-label="ปิด">×</button>`;
        el.querySelector(".cmTx").addEventListener("click", ev => { ev.preventDefault(); ev.stopPropagation(); close(el); });
        stack.prepend(el);
        requestAnimationFrame(() => el.classList.add("in"));
        [...stack.querySelectorAll(".cmToast")].slice(MAX_TOASTS).forEach(close);
        let t = setTimeout(() => close(el), cls === "bad" ? 30000 : 10000);
        el.addEventListener("mouseenter", () => clearTimeout(t));
        el.addEventListener("mouseleave", () => { t = setTimeout(() => close(el), 6000); });
    }
    function close(el) {
        el.classList.remove("in");
        setTimeout(() => el.remove(), 250);
    }
    function summaryToast(n) {
        toast({ level: "warning", sensor: `อีก ${n} รายการ`, message: "เปิด Status & Logs > System log เพื่อดูทั้งหมด", time: Date.now() / 1000 });
    }

    /* ---------- ดึงเหตุการณ์ใหม่ ---------- */
    async function poll() {
        if (!on() && !osOn()) return;
        const since = Math.max(last, Number(get(K_LAST, "0")) || 0);
        let d;
        try {
            const r = await fetch(`/api/events?kind=system&limit=30&since=${since}`, { credentials: "same-origin", cache: "no-store" });
            if (!r.ok) return;
            d = await r.json();
        } catch (e) { return; }
        const rows = (d.entries || []).filter(e => e.time > since && LV[e.level]).reverse();   // เก่าก่อน
        if (!rows.length) return;
        last = Math.max(...rows.map(e => e.time));
        set(K_LAST, last);
        rows.slice(-MAX_TOASTS).forEach(e => toast(e));
        if (rows.length > MAX_TOASTS && on()) summaryToast(rows.length - MAX_TOASTS);
    }
    setInterval(() => { poll(); }, POLL_MS);
    setTimeout(poll, 3000);

    /* ---------- ปุ่ม 🔔 ตั้งค่า (ในแถบป้ายบนหัว) ---------- */
    function bell() {
        const host = document.getElementById("sbadges");
        if (!host || document.getElementById("cmBell")) return;
        const wrap = document.createElement("span"); wrap.className = "cmBellWrap";
        const secure = window.isSecureContext && "Notification" in window;
        wrap.innerHTML = `<button type="button" class="cmBell" id="cmBell" title="ตั้งค่าแจ้งเตือนป๊อปอัป" aria-haspopup="true">🔔</button>`
            + `<div class="cmBellMenu" hidden><b>แจ้งเตือนป๊อปอัป</b>`
            + `<label><input type="checkbox" id="cmPopOn"> ป๊อปอัปมุมขวาล่างในหน้าเว็บ</label>`
            + `<label${secure ? "" : ' class="dis"'}><input type="checkbox" id="cmPopOs"${secure ? "" : " disabled"}> แจ้งเตือนของ Windows (แม้ย่อหน้าต่างไว้)</label>`
            + (secure ? "" : `<small>ใช้ได้เมื่อเปิดเว็บผ่าน https:// หรือ http://localhost</small>`)
            + `<button type="button" id="cmPopTest">ทดลองแจ้งเตือน</button></div>`;
        host.after(wrap);                        // ไม่ใส่ใน #sbadges (badges.js ล้างทุก 15 วินาที)
        const menu = wrap.querySelector(".cmBellMenu");
        const sync = () => { wrap.querySelector("#cmPopOn").checked = on(); wrap.querySelector("#cmPopOs").checked = osOn(); };
        wrap.querySelector("#cmBell").addEventListener("click", e => { e.stopPropagation(); menu.hidden = !menu.hidden; sync(); });
        document.addEventListener("click", e => { if (!wrap.contains(e.target)) menu.hidden = true; });
        wrap.querySelector("#cmPopOn").addEventListener("change", e => set(K_ON, e.target.checked ? "1" : "0"));
        wrap.querySelector("#cmPopOs").addEventListener("change", async e => {
            if (!e.target.checked) { set(K_OS, "0"); return; }
            const p = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
            set(K_OS, p === "granted" ? "1" : "0");
            if (p !== "granted") alert("browser ไม่อนุญาตการแจ้งเตือน: เปิดที่ไอคอนแม่กุญแจหน้า URL > Notifications > Allow");
            sync();
        });
        wrap.querySelector("#cmPopTest").addEventListener("click", () => toast({
            level: "error", web: "ตัวอย่าง", sensor: "เว็บระบบบัญชี", time: Date.now() / 1000,
            message: "http://localhost:8080 ไม่ตอบ (HTTP 0) · นี่คือการทดลอง",
        }, true));
    }
    document.addEventListener("DOMContentLoaded", bell);
    setTimeout(bell, 1500);                     // badges.js สร้างป้ายทีหลัง
})();
