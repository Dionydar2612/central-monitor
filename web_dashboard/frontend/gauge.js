/* =====================================================================
   gauge.js - เกจแบบ PRTG (วงโค้ง 270° + เข็ม + แถบโซน OK / Warning / Error)
   ใช้ใน: index.html (ภาพรวมเครื่อง) · sensor.html (หน้า sensor)
     CMGauge.svg(sensor, { size: "lg" | "sm" })   sensor = {value, unit, warning, error, low_is_bad, status}
     CMGauge.fmt(value, unit)                     ข้อความค่า เช่น "41.2 %" "12 ms"
   ===================================================================== */
"use strict";
(function () {
    const COLOR = { ok: "#a8c522", warning: "#f5b400", error: "#e5484d", down: "#d12f35", paused: "#4c8ff0", unknown: "#b9c0cb" };
    const A0 = 135, SWEEP = 270;                    // เริ่มล่างซ้าย กวาดตามเข็มนาฬิกาไปล่างขวา

    function fmt(v, unit) {
        if (v === null || v === undefined || v === "" || !Number.isFinite(Number(v))) return "-";
        const n = Number(v), a = Math.abs(n);
        const s = a >= 1000 ? Math.round(n).toLocaleString("en-US") : a >= 100 ? n.toFixed(0) : a >= 10 ? n.toFixed(1) : n.toFixed(2).replace(/\.?0+$/, "");
        return unit ? `${s} ${unit}` : s;
    }

    /* สเกลเต็ม: % = 0-100 · หน่วยอื่น = ใหญ่กว่า error 25% (หรือค่าปัจจุบัน) */
    function scaleMax(s) {
        if (s.unit === "%") return 100;
        const cands = [Number(s.error) * 1.25, Number(s.warning) * 1.5, Number(s.value) * 1.1, Number(s.max) || 0].filter(Number.isFinite);
        return Math.max(1, ...cands);
    }

    function pt(cx, cy, r, deg) {
        const a = (deg - 90) * Math.PI / 180;
        return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
    }
    function arc(cx, cy, r, d0, d1) {
        const [x0, y0] = pt(cx, cy, r, d0), [x1, y1] = pt(cx, cy, r, d1);
        return `M${x0.toFixed(2)},${y0.toFixed(2)}A${r},${r} 0 ${d1 - d0 > 180 ? 1 : 0} 1 ${x1.toFixed(2)},${y1.toFixed(2)}`;
    }

    function svg(s, opt) {
        opt = opt || {};
        const big = opt.size === "lg", W = 120, cx = 60, cy = 60, R = 44;
        const max = scaleMax(s), frac = v => Math.max(0, Math.min(1, Number(v) / max));
        const deg = f => A0 + 90 + f * SWEEP;       // แปลงสัดส่วน -> องศา (0° = บน)
        const st = COLOR[s.status] ? s.status : "unknown";
        const has = Number.isFinite(Number(s.value)) && s.value !== null && s.value !== "";
        let zones = "";
        const w = Number(s.warning), e = Number(s.error);
        if (Number.isFinite(w) || Number.isFinite(e)) {
            const okEnd = s.low_is_bad ? 1 : frac(Number.isFinite(w) ? w : e), r2 = R - (big ? 15 : 13);
            const band = (f0, f1, c) => f1 > f0 ? `<path d="${arc(cx, cy, r2, deg(f0), deg(f1))}" stroke="${c}" stroke-width="3" fill="none"/>` : "";
            if (s.low_is_bad) {                      // ค่าต่ำ = แย่ (เช่น RAM ว่าง %)
                zones = band(0, frac(e), COLOR.error) + band(frac(e), frac(w), COLOR.warning) + band(frac(w), 1, COLOR.ok);
            } else {
                zones = band(0, okEnd, COLOR.ok) + (Number.isFinite(w) && Number.isFinite(e) ? band(frac(w), frac(e), COLOR.warning) : "") + band(frac(Number.isFinite(e) ? e : w), 1, COLOR.error);
            }
        }
        const f = has ? frac(s.value) : 0;
        const [nx, ny] = pt(cx, cy, R - (big ? 8 : 10), deg(f));
        const thick = big ? 20 : 12;
        return `<svg class="cmg ${big ? "lg" : "sm"}" viewBox="0 0 ${W} 108" role="img" aria-label="${fmt(s.value, s.unit)}">
<path d="${arc(cx, cy, R, deg(0), deg(1))}" stroke="var(--chip,#e9edf2)" stroke-width="${thick}" fill="none"/>
${has ? `<path d="${arc(cx, cy, R, deg(0), deg(Math.max(f, 0.004)))}" stroke="${COLOR[st]}" stroke-width="${thick}" fill="none"/>` : ""}
${zones}
${has ? `<line x1="${cx}" y1="${cy}" x2="${nx.toFixed(2)}" y2="${ny.toFixed(2)}" stroke="#555" stroke-width="${big ? 4 : 3}" stroke-linecap="round"/><circle cx="${cx}" cy="${cy}" r="${big ? 4 : 3}" fill="#555"/>` : ""}
${big ? `<text x="${pt(cx, cy, R, deg(0))[0] + 2}" y="104" class="cmgl" text-anchor="middle">0</text><text x="${pt(cx, cy, R, deg(1))[0] - 2}" y="104" class="cmgl" text-anchor="middle">${fmt(max, s.unit === "%" ? "" : "")}</text>` : ""}
</svg>`;
    }

    window.CMGauge = { svg, fmt, COLOR };
})();
