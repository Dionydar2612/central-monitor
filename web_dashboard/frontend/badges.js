/* =====================================================================
   badges.js - ป้ายสถานะบนหัวทุกหน้า (เหมือนแถบของ PRTG)
     [New Log Entries N]  [▼ Down/Error]  [W Warning]  [✓ OK]  [II Paused]
   คลิกป้าย -> หน้า /events (รายการ sensor ตามสถานะ / System log)
   ข้อมูล: GET /api/events/summary ทุก 15 วินาที (sensor จาก puller/sensor_monitor.py)
   "New Log Entries" = log ที่ใหม่กว่าครั้งล่าสุดที่ผู้ใช้คนนี้เปิดดู (จำใน browser: localStorage)
   ใช้ร่วมกันใน index.html / settings.html / events.html  ต้องมี <div id="sbadges"> ในหน้า
   ===================================================================== */
"use strict";
(function () {
    const box = document.getElementById("sbadges");
    if (!box) return;
    let user = "";

    const key = kind => `cm.seen.${kind}.${user}`;
    function seen(kind) {
        try {
            const v = Number(localStorage.getItem(key(kind)));
            if (v > 0) return v;
        } catch (e) { /* ไม่มี localStorage */ }
        return Date.now() / 1000 - 86400;          // ครั้งแรก: นับ log 24 ชม. ล่าสุดเป็นของใหม่
    }
    window.cmMarkSeen = function (kind, ts) {         // หน้า /events เรียกเมื่อผู้ใช้เปิดดู log แล้ว
        try { localStorage.setItem(key(kind), String(ts || Date.now() / 1000)); } catch (e) { /* ignore */ }
        refresh();
    };

    function badge(href, cls, icon, n, title) {
        const a = document.createElement("a");
        a.href = href; a.className = "sbadge " + cls; a.title = title;
        if (icon) { const i = document.createElement("i"); i.textContent = icon; a.append(i); }
        a.append(String(n));
        return a;
    }

    async function refresh() {
        try {
            const p = new URLSearchParams({ since_system: seen("system"), since_activity: seen("activity") });
            const r = await fetch(`/api/events/summary?${p}`, { cache: "no-store", credentials: "same-origin" });
            if (!r.ok) return;
            const d = await r.json(), c = (d.sensors && d.sensors.counts) || {};
            const newLogs = (d.new_system || 0) + (d.new_activity || 0);
            const bad = (c.down || 0) + (c.error || 0);
            box.textContent = "";
            const logs = badge("/events?tab=system", "logs" + (newLogs ? " has" : ""), "", "", "New log entries since you last looked");
            logs.textContent = `New Log Entries ${newLogs}`;
            box.append(logs);
            if (bad) box.append(badge("/events?status=down,error", "dn", "▼", bad, `${bad} sensor(s) Down / Error`));
            if (c.warning) box.append(badge("/events?status=warning", "wr", "W", c.warning, `${c.warning} sensor(s) Warning`));
            box.append(badge("/events?status=ok", "ok", "✓", c.ok || 0, `${c.ok || 0} sensor(s) OK`));
            if (c.paused) box.append(badge("/events?status=paused", "ps", "II", c.paused, `${c.paused} sensor(s) Paused`));
            if (c.unknown) box.append(badge("/events?status=unknown", "na", "?", c.unknown, `${c.unknown} sensor(s) Unknown`));
        } catch (e) { /* เครือข่ายหลุดชั่วคราว: รอบหน้าลองใหม่ */ }
    }

    (async () => {
        try {
            const r = await fetch("/api/me", { cache: "no-store", credentials: "same-origin" });
            if (r.ok) user = (await r.json()).username || "";
        } catch (e) { /* ignore */ }
        refresh();
        setInterval(refresh, 15000);
    })();
})();
