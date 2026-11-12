/* =====================================================================
   popup.js - ป๊อปอัปแจ้งเตือนมุมขวาล่าง (แบบ Windows toast) ทุกหน้า · ขึ้นเองอัตโนมัติ
     ข้อมูล: GET /api/events?kind=system&since=<ล่าสุด> ทุก 15 วินาที (puller เขียน data/state/events.log)
     เรื่องที่แจ้ง: sensor เปลี่ยนสถานะ (Availability / CPU / Memory / Disk / Log pull)
                   ระดับ Down / Error / Warning / กลับมาปกติ ตามที่เลือกใน Settings > Popup notifications
     เปิด / ปิด: GET /api/popup-settings (ทุก role อ่านได้ · แก้ได้เฉพาะ admin)
     แจ้งเตือนของ Windows (desktop): ติ๊กใน Settings (จำต่อ browser · ต้อง https:// หรือ localhost)
     ไม่แสดงของเก่าก่อนเปิดหน้า · หลายแท็บใช้เวลาล่าสุดร่วมกัน (ไม่เด้งซ้ำ)
   ใช้ใน index.html / events.html / settings.html (ต่อจาก badges.js)
   ===================================================================== */
"use strict";
(function () {
    const POLL_MS = 15000, CFG_MS = 300000, MAX_TOASTS = 4;
    const K_LAST = "cm.popup.last", K_OS = "cm.popup.os";
    const LV = {
        down: ["🔴", "Down", "bad"], error: ["🔴", "Error", "bad"], warning: ["🟡", "Warning", "wr"],
        recovered: ["🟢", "กลับมาปกติ", "ok"],
    };
    const get = (k, d) => { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } };
    const set = (k, v) => { try { localStorage.setItem(k, String(v)); } catch (e) { /* ignore */ } };
    const osOn = () => get(K_OS, "0") === "1" && "Notification" in window && Notification.permission === "granted";
    const esc = v => String(v ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    let cfg = { enabled: true, levels: ["down", "error", "warning", "recovered"] }, cfgAt = 0;
    let last = Math.max(Number(get(K_LAST, "0")) || 0, Date.now() / 1000 - 5);
    set(K_LAST, last);

    const stack = document.createElement("div");
    stack.className = "cmToasts"; stack.setAttribute("role", "status"); stack.setAttribute("aria-live", "polite");
    const mount = () => { if (document.body && !stack.isConnected) document.body.append(stack); };
    mount(); document.addEventListener("DOMContentLoaded", mount);

    const link = e => e.web ? `/?web=${encodeURIComponent(e.web)}` : "/events";
    const wanted = e => e.source === "sensor" && !!LV[e.level] && (cfg.levels || []).includes(e.level);

    function toast(e) {
        const [icon, label, cls] = LV[e.level] || LV.warning;
        const title = [e.web, e.sensor].filter(Boolean).join(" · ") || "Central Monitor";
        const msg = String(e.message || "").replace(new RegExp(`^${(e.web || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")} · `), "");
        if (osOn()) {
            try {
                const n = new Notification(`${icon} ${title}`, { body: msg, tag: `cm-${title}-${e.level}`, renotify: true });
                n.onclick = () => { window.focus(); location.href = link(e); n.close(); };
            } catch (err) { /* browser ไม่รองรับ */ }
        }
        mount();
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

    async function loadCfg(force) {
        if (!force && Date.now() - cfgAt < CFG_MS) return;
        try {
            const r = await fetch("/api/popup-settings", { credentials: "same-origin", cache: "no-store" });
            if (r.ok) { cfg = await r.json(); cfgAt = Date.now(); }
        } catch (e) { /* ใช้ค่าเดิม */ }
    }
    async function poll() {
        await loadCfg();
        const since = Math.max(last, Number(get(K_LAST, "0")) || 0);
        let d;
        try {
            const r = await fetch(`/api/events?kind=system&limit=30&since=${since}`, { credentials: "same-origin", cache: "no-store" });
            if (!r.ok) return;
            d = await r.json();
        } catch (e) { return; }
        const all = (d.entries || []).filter(e => e.time > since);
        if (!all.length) return;
        last = Math.max(...all.map(e => e.time));
        set(K_LAST, last);
        if (cfg.enabled === false) return;
        const rows = all.filter(wanted).reverse();                    // เก่าก่อน
        rows.slice(-MAX_TOASTS).forEach(toast);
        if (rows.length > MAX_TOASTS) toast({ level: "warning", sensor: `อีก ${rows.length - MAX_TOASTS} รายการ`, time: Date.now() / 1000,
                                               message: "เปิดหน้า Events > System log เพื่อดูทั้งหมด" });
    }
    setInterval(poll, POLL_MS);
    setTimeout(poll, 3000);

    /* ให้หน้า Settings ใช้: เปิด/ปิดแจ้งเตือนของ Windows + ทดลอง + โหลดค่าใหม่หลังบันทึก */
    window.cmPopup = {
        osOn,
        reload: () => loadCfg(true),
        async setOs(want) {
            if (!want) { set(K_OS, "0"); return false; }
            if (!("Notification" in window) || !window.isSecureContext) return false;
            const p = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
            set(K_OS, p === "granted" ? "1" : "0");
            return p === "granted";
        },
        test() {
            toast({ level: "error", web: "ตัวอย่าง", sensor: "CPU average 5m", time: Date.now() / 1000,
                    message: "Warning → Error · CPU 93% (limit 90%) · นี่คือการทดลอง" });
        },
    };
})();
