/* =====================================================================
   machine.js - จัดหน้ารายละเอียดเครื่อง (index.html #detail)
   =====================================================================
   แสดง: Overview (KPI + กราฟ System Load คู่กับ Service) · Processes · System events
   (หน้าแบบแท็บของเครื่อง Windows เอาออกแล้ว)
   ใช้ตัวแปร/ฟังก์ชันจาก dashboard.js: S, q
   ===================================================================== */
(function () {
    "use strict";
    const PANES = ["overview", "processes", "system"];

    function layout() {
        const main = q("#ovMain"), sv = q("#detail .svcard");
        if (sv && main && sv.parentNode !== main) { main.append(sv); main.className = "grid main"; }
        document.querySelectorAll("#detail .dpane").forEach(p => { p.hidden = !PANES.includes(p.dataset.pane); });
    }

    window.CMPanes = { refresh() { layout(); }, reset() { layout(); }, show() {} };
    document.addEventListener("DOMContentLoaded", layout);
})();
