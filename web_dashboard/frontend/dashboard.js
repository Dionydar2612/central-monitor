/* =====================================================================
   dashboard.js - ตรรกะทั้งหมดของหน้า dashboard (index.html)
   =====================================================================

   ภาพรวมการทำงาน
     1) เปิดหน้า -> DOMContentLoaded (ท้ายไฟล์) ตั้งปุ่มต่าง ๆ แล้วเรียก loadAll()
     2) loadAll() เรียก API ของ web_dashboard/backend/api.py:
          /api/web-status      รายชื่อเครื่อง + online/offline     -> renderMachines
          /api/summary         สรุปเครื่อง (State, Last pull, ...)  -> renderMeta
          /api/metrics         ตัวเลข + service/process/event       -> renderLists
          /api/problems        รายการ log                           -> renderProblems
          /api/metrics/history อนุกรมเวลาสำหรับกราฟ                 -> renderKPIs, renderLoad
     3) รีเฟรชอัตโนมัติทุก 10 วินาที (setInterval ท้ายไฟล์) หรือกดปุ่ม Refresh
     4) คลิกแถว log -> openLogDetail -> /api/problems/detail -> หน้าต่างรายละเอียด
     5) ถ้า API ตอบ 401 (session หมดอายุ) apiGet จะพาไปหน้า /login เอง

   โครงของไฟล์ (เรียงจากบนลงล่าง)
     A. ตัวช่วยจัดรูปแบบข้อความ/ตัวเลข
     B. หน้าต่างรายละเอียด log (showProblemModal)
     C. สถานะกลาง (S), ตัวเรียก API (apiGet)
     D. ตัวช่วยวาดกราฟ SVG (ไม่ใช้ไลบรารีภายนอก)
     E. ช่วงเวลา (time range)
     F. ตัววาดส่วนต่าง ๆ ของหน้า (render*)
     G. การโหลดข้อมูล (loadHist, loadAll)
     H. ตัวเลือกช่วงเวลา/ปฏิทิน (setupPicker)
     I. ผู้ใช้/Logout และจุดเริ่มต้น

   ความปลอดภัย: ข้อความจากเซิร์ฟเวอร์ทุกอย่างผ่าน escapeHtml ก่อนใส่ใน HTML
   ===================================================================== */

/* ---------- A. ตัวช่วยจัดรูปแบบ ---------- */
/* ย่อ document.getElementById */
function $(id) {
    return document.getElementById(id);
}

/* ใส่ข้อความในช่องที่มี id นั้น (ไม่มีช่อง = ข้าม ไม่ error) */
function setText(id, value) {

    const el = $(id);

    if (el) {
        el.textContent = value;
    }
}

/* ตัวเลข -> ข้อความทศนิยม digits ตำแหน่ง; ค่าว่าง/ไม่ใช่ตัวเลข -> "-" */
function formatNumber(value, digits = 2) {

    if (
        value === null ||
        value === undefined ||
        value === ""
    ) {
        return "-";
    }

    const n = Number(value);

    if (!Number.isFinite(n)) {
        return "-";
    }

    return n.toFixed(digits);
}

/* ตัวเลข -> "xx.xx%" (ไม่มีค่า -> "-") */
function formatPercent(value) {

    const v = formatNumber(value, 2);

    return v === "-" ? "-" : `${v}%`;
}

/* ISO เวลา -> วันเวลาแบบไทย (th-TH) ; แปลงไม่ได้ -> คืนข้อความเดิม */
function formatTimestamp(value) {

    if (!value) {
        return "-";
    }

    const d = new Date(value);

    if (Number.isNaN(d.getTime())) {
        return value;
    }

    return d.toLocaleString("th-TH", {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        day: "2-digit",
        month: "2-digit",
        year: "numeric"
    });
}

/* ป้องกัน XSS: แปลง & < > " ' ก่อนนำข้อความจากเซิร์ฟเวอร์ไปใส่ใน innerHTML */
function escapeHtml(value) {

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}

/* มีค่าจริงไหม (ไม่ใช่ null / undefined / ข้อความว่าง; เลข 0 ถือว่ามีค่า) */
function hasValue(value) {
    return value !== null &&
           value !== undefined &&
           value !== "";
}

/* ค่า + หน่วยต่อท้าย ถ้าไม่มีค่าคืนข้อความว่าง */
function formatMetricValue(value, suffix = "") {

    if (!hasValue(value)) {
        return "";
    }

    return `${value}${suffix}`;
}

/* สร้างข้อความสรุป "Metrics at time" ต่อท้ายแต่ละแถว log
   เช่น "CPU 42.00% · Memory 61.00% · Disk 55.00%"  (ข้อมูล = correlated_metrics จาก /api/problems) */
function buildProblemMetrics(metrics) {

    if (!metrics) {
        return "";
    }

    const parts = [];

    if (metrics.cpu != null) {
        parts.push(`CPU ${formatPercent(metrics.cpu)}`);
    }

    if (metrics.memory != null) {
        parts.push(`Memory ${formatPercent(metrics.memory)}`);
    }

    if (metrics.disk != null) {
        parts.push(`Disk ${formatPercent(metrics.disk)}`);
    }

    if (metrics.io != null) {
        parts.push(`I/O ${formatNumber(metrics.io)} MB/s`);
    }

    if (metrics.disk_read != null) {
        parts.push(`Read ${formatNumber(metrics.disk_read)} MB/s`);
    }

    if (metrics.disk_write != null) {
        parts.push(`Write ${formatNumber(metrics.disk_write)} MB/s`);
    }

    if (metrics.net_rx != null) {
        parts.push(`RX ${formatNumber(metrics.net_rx)}`);
    }

    if (metrics.net_tx != null) {
        parts.push(`TX ${formatNumber(metrics.net_tx)}`);
    }

    return parts.join(" · ");
}

/* ---------- B. หน้าต่างรายละเอียด log ----------
   แสดง: ข้อความ log, metrics ณ เวลานั้น (CPU/Load/Memory/Disk/...), service,
         process, system event, และข้อมูลดิบ (JSON)
   เรียก 2 รอบจาก openLogDetail: รอบแรกด้วยข้อมูลจากรายการ (เปิดเร็ว),
   รอบสอง ด้วยข้อมูลเต็มจาก /api/problems/detail (เติม service/process/event)
   สร้างหน้าต่าง (div#problemModal) ครั้งแรกที่เปิด แล้วใช้ซ้ำ */
function showProblemModal(problem) {

    const raw = problem.raw || {};
    const metrics = problem.correlated_metrics || {};

    let modal = $("problemModal");

    if (!modal) {
        modal = document.createElement("div");
        modal.id = "problemModal";
        modal.className = "problem-modal";
        document.body.appendChild(modal);
    }

    function modalMetric(label, value) {

        if (!hasValue(value)) {
            return "";
        }

        return `
            <div class="modal-metric">
                <div class="modal-metric-label">
                    ${escapeHtml(label)}
                </div>

                <div class="modal-metric-value">
                    ${escapeHtml(String(value))}
                </div>
            </div>
        `;
    }


    const metricHtml = [

        modalMetric(
            "CPU",
            hasValue(metrics.cpu)
                ? formatPercent(metrics.cpu)
                : null
        ),

        modalMetric(
            "LOAD",
            hasValue(metrics.load)
                ? formatNumber(metrics.load)
                : null
        ),

        modalMetric(
            "LOAD 1M",
            hasValue(metrics.load_1m)
                ? formatNumber(metrics.load_1m)
                : null
        ),

        modalMetric(
            "LOAD 5M",
            hasValue(metrics.load_5m)
                ? formatNumber(metrics.load_5m)
                : null
        ),

        modalMetric(
            "MEMORY",
            hasValue(metrics.memory)
                ? formatPercent(metrics.memory)
                : null
        ),

        modalMetric(
            "MEMORY USED",
            metrics.memory_used
        ),

        modalMetric(
            "MEMORY AVAILABLE",
            metrics.memory_available
        ),

        modalMetric(
            "SWAP",
            hasValue(metrics.swap)
                ? formatPercent(metrics.swap)
                : null
        ),

        modalMetric(
            "DISK",
            hasValue(metrics.disk)
                ? formatPercent(metrics.disk)
                : null
        ),

        modalMetric(
            "DISK AVAILABLE",
            metrics.disk_available
        ),

        modalMetric(
            "INODE",
            hasValue(metrics.inode)
                ? formatPercent(metrics.inode)
                : null
        ),

        modalMetric(
            "READ",
            hasValue(metrics.disk_read)
                ? `${formatNumber(metrics.disk_read)} MB/s`
                : null
        ),

        modalMetric(
            "WRITE",
            hasValue(metrics.disk_write)
                ? `${formatNumber(metrics.disk_write)} MB/s`
                : null
        ),

        modalMetric(
            "I/O",
            hasValue(metrics.io)
                ? `${formatNumber(metrics.io)} MB/s`
                : null
        ),

        modalMetric(
            "I/O UTILIZATION",
            hasValue(metrics.io_utilization)
                ? formatPercent(metrics.io_utilization)
                : null
        ),

        modalMetric(
            "RX",
            hasValue(metrics.net_rx)
                ? formatNumber(metrics.net_rx)
                : null
        ),

        modalMetric(
            "TX",
            hasValue(metrics.net_tx)
                ? formatNumber(metrics.net_tx)
                : null
        ),

        modalMetric(
            "RX ERRORS",
            metrics.net_rx_errors
        ),

        modalMetric(
            "TX ERRORS",
            metrics.net_tx_errors
        ),

        modalMetric(
            "UPTIME",
            metrics.uptime
        )

    ].join("");


    const services =
        Array.isArray(problem.services)
            ? problem.services
            : Array.isArray(raw.services)
                ? raw.services
                : [];


    const processes =
        Array.isArray(problem.processes)
            ? problem.processes
            : Array.isArray(raw.processes)
                ? raw.processes
                : [];


    const events =
        Array.isArray(problem.system_events)
            ? problem.system_events
            : Array.isArray(raw.system_events)
                ? raw.system_events
                : [];


    const serviceHtml =
        services
            .filter(service =>
                service &&
                hasValue(service.name) &&
                hasValue(service.status)
            )
            .map(service => `

                <div class="dynamic-service-row">

                    <div>
                        <strong>
                            ${escapeHtml(service.name)}
                        </strong>
                    </div>

                    <div>
                        ${escapeHtml(service.status)}
                    </div>

                </div>

            `)
            .join("");


    const processHtml =
        processes
            .filter(process =>
                process &&
                hasValue(process.name)
            )
            .map(process => `

                <div class="dynamic-process-row">

                    <strong>
                        ${escapeHtml(process.name)}
                    </strong>

                    ${
                        hasValue(process.cpu)
                            ? `<span>
                                CPU ${escapeHtml(
                                    formatPercent(process.cpu)
                                )}
                               </span>`
                            : ""
                    }

                    ${
                        hasValue(process.memory)
                            ? `<span>
                                MEM ${escapeHtml(
                                    formatPercent(process.memory)
                                )}
                               </span>`
                            : ""
                    }

                </div>

            `)
            .join("");


    const eventHtml =
        events
            .filter(event =>
                event &&
                (
                    hasValue(event.message) ||
                    hasValue(event.type)
                )
            )
            .map(event => `

                <div class="dynamic-event-row">

                    ${
                        hasValue(event.timestamp)
                            ? `<div class="dynamic-event-time">
                                ${escapeHtml(
                                    formatTimestamp(
                                        event.timestamp
                                    )
                                )}
                               </div>`
                            : ""
                    }

                    ${
                        hasValue(event.type)
                            ? `<div class="problem-type">
                                ${escapeHtml(event.type)}
                               </div>`
                            : ""
                    }

                    ${
                        hasValue(event.message)
                            ? `<div class="problem-message">
                                ${escapeHtml(event.message)}
                               </div>`
                            : ""
                    }

                </div>

            `)
            .join("");


    modal.innerHTML = `

        <div
            class="problem-modal-backdrop"
            onclick="closeProblemModal()">
        </div>


        <div class="problem-modal-dialog">

            <div class="problem-modal-header">

                <div>

                    <div class="problem-modal-title">
                        Problem Log
                    </div>

                    <div class="problem-modal-subtitle">
                        ${escapeHtml(problem.web || "-")}
                        ·
                        ${escapeHtml(
                            problem.type || "application"
                        )}
                    </div>

                </div>

                <button
                    class="modal-close"
                    onclick="closeProblemModal()"
                    aria-label="Close">
                    ×
                </button>

            </div>


            <div class="problem-modal-body">

                <div class="modal-info-grid">

                    <div>
                        <span>TIME</span>
                        <strong>
                            ${escapeHtml(
                                formatTimestamp(
                                    problem.timestamp
                                )
                            )}
                        </strong>
                    </div>

                    <div>
                        <span>WEB</span>
                        <strong>
                            ${escapeHtml(
                                problem.web || "-"
                            )}
                        </strong>
                    </div>

                    <div>
                        <span>LEVEL</span>
                        <strong class="modal-error">
                            ${escapeHtml(
                                problem.level || "ERROR"
                            )}
                        </strong>
                    </div>

                    <div>
                        <span>TYPE</span>
                        <strong>
                            ${escapeHtml(
                                problem.type || "-"
                            )}
                        </strong>
                    </div>

                </div>


                <div class="modal-block">

                    <div class="modal-block-title">
                        MESSAGE
                    </div>

                    <pre class="modal-message">${escapeHtml(
                        problem.message || "-"
                    )}</pre>

                </div>


                ${
                    metricHtml
                        ? `
                            <div class="modal-block">

                                <div class="modal-block-title">
                                    INFRASTRUCTURE AT TIME
                                </div>

                                <div class="modal-metrics">
                                    ${metricHtml}
                                </div>

                            </div>
                          `
                        : ""
                }


                ${
                    serviceHtml
                        ? `
                            <div class="modal-block">

                                <div class="modal-block-title">
                                    SERVICES
                                </div>

                                <div class="dynamic-service-list">
                                    ${serviceHtml}
                                </div>

                            </div>
                          `
                        : ""
                }


                ${
                    processHtml
                        ? `
                            <div class="modal-block">

                                <div class="modal-block-title">
                                    TOP PROCESSES
                                </div>

                                <div class="dynamic-process-list">
                                    ${processHtml}
                                </div>

                            </div>
                          `
                        : ""
                }


                ${
                    eventHtml
                        ? `
                            <div class="modal-block">

                                <div class="modal-block-title">
                                    SYSTEM EVENTS
                                </div>

                                <div class="dynamic-event-list">
                                    ${eventHtml}
                                </div>

                            </div>
                          `
                        : ""
                }


                <div class="modal-block">

                    <details class="modal-raw-details">

                        <summary>
                            Show raw log data
                        </summary>

                        <pre class="modal-raw">${escapeHtml(
                            JSON.stringify(
                                raw,
                                null,
                                2
                            )
                        )}</pre>

                    </details>

                </div>

            </div>

        </div>
    `;

    modal.style.display = "flex";
}

/* ปิดหน้าต่างรายละเอียด (ปุ่มปิด หรือกด Esc) */
function closeProblemModal() {

    const modal =
        $("problemModal");

    if (modal) {
        modal.style.display = "none";
    }
}


/* ============================================================
   Central Monitor dashboard
   ============================================================ */

/* ============================================================
   Central Monitor dashboard (ส่วนหลัก)
   ============================================================ */

/* ---------- C. สถานะกลาง ---------- */

/* MIN = 1 นาทีเป็นมิลลิวินาที ; RANGES = ตัวเลือกช่วงเวลาด่วนในเมนู [ป้าย, ความยาว ms] */
const MIN = 60000;
const RANGES = [
    ["Last 15 minutes", 15 * MIN], ["Last 30 minutes", 30 * MIN],
    ["Last 1 hour", 60 * MIN], ["Last 6 hours", 360 * MIN],
    ["Last 24 hours", 1440 * MIN], ["Last 7 days", 10080 * MIN],
    ["Last 30 days", 43200 * MIN]
];

/* S = สถานะของหน้า (ที่เดียว)
     web      เครื่องที่เลือกอยู่        webs     รายชื่อเครื่องล่าสุด
     metrics  ข้อมูลจาก /api/metrics + summary.metrics (รวมกัน)
     summary  ข้อมูลจาก /api/summary     hist     ข้อมูลกราฟจาก /api/metrics/history
     dur      ช่วงเวลาด่วน (ms)          start/end ช่วงที่เลือกเอง (end=null แปลว่า "ตอนนี้")
     tmp/ed/vm สถานะชั่วคราวของปฏิทิน    busy     กำลังโหลดอยู่ (กันเรียกซ้อน) */
const S = {
    web: null, busy: false, webs: [],
    metrics: null, summary: null, hist: null,
    dur: 15 * MIN, start: null, end: null,     // end === null means "now"
    tmp: null, ed: "s", vm: null,
    lv: "ALL", lt: "ALL", stats: null,     // ตัวกรอง log: ระดับ / ชนิด + จำนวนจาก /api/log-stats
    watch: [], watchWeb: null,             // service ที่เลือกให้แสดง ([] = ทั้งหมด) และเครื่องที่โหลดค่านั้นมา
    pick: null                             // Set ของ service ที่ติ๊กอยู่ในตัวเลือก (null = ปิดตัวเลือก)
};

/* ย่อ document.querySelector */
const q = s => document.querySelector(s);

/* เรียก GET ไป API ของเรา คืน JSON
   - ส่ง cookie session ไปด้วย (credentials: same-origin) และไม่ใช้ cache
   - 401 = session หมดอายุ -> พาไปหน้า login
   - status อื่นที่ไม่ใช่ 2xx -> throw (ตัวเรียกจับแล้วแสดง "API Error") */
async function apiGet(url) {
    const r = await fetch(url, { cache: "no-store", credentials: "same-origin" });
    if (r.status === 401) { window.location.href = "/login"; throw new Error("HTTP 401"); }
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
}

/* ตั้งข้อความ + สีจุดสถานะมุมขวาบน (type: "green" | "yellow" | "red") */
function setSystemStatus(text, type) {
    setText("systemStatus", text);
    const dot = $("systemDot");
    if (dot) dot.className = "dot " + (type === "red" ? "red" : type === "yellow" ? "yellow" : "green");
}

/* ---------- D. ตัวช่วยวาดกราฟ SVG ---------- */
/* ---------- chart helpers ---------- */

/* จุด [[x,y],...] -> path ของ SVG เส้นโค้งเนียน (Bezier) */
function smooth(p) {
    let d = `M${p[0][0].toFixed(1)},${p[0][1].toFixed(1)}`;
    for (let i = 1; i < p.length; i++) {
        const c = ((p[i - 1][0] + p[i][0]) / 2).toFixed(1);
        d += ` C${c},${p[i - 1][1].toFixed(1)} ${c},${p[i][1].toFixed(1)} ${p[i][0].toFixed(1)},${p[i][1].toFixed(1)}`;
    }
    return d;
}

/* กราฟเส้นเล็กในการ์ด KPI (200x60) จากอนุกรมเวลา [[เวลา,ค่า],...] ; ข้อมูลน้อยกว่า 2 จุด -> ว่าง */
function sparkSvg(pts) {
    if (!pts || pts.length < 2) return "";
    const v = pts.map(p => p[1]), lo = Math.min(...v), hi = Math.max(...v), pad = Math.max((hi - lo) * 0.1, 1);
    const t0 = pts[0][0], t1 = pts[pts.length - 1][0] || t0 + 1;
    const p = pts.map(x => [(x[0] - t0) / (t1 - t0 || 1) * 200, 4 + 52 - (x[1] - (lo - pad)) / (hi - lo + 2 * pad) * 52]);
    const d = smooth(p);
    return `<svg viewBox="0 0 200 60" preserveAspectRatio="none" aria-hidden="true"><path d="${d} L200,60 L0,60Z" fill="var(--fill)" opacity=".7"/><path d="${d}" fill="none" stroke="var(--blue)" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg>`;
}

/* เทียบค่าล่าสุดกับ "1 ชั่วโมงก่อน" (ถ้าช่วงสั้นกว่า 1 ชม. เทียบกับจุดเริ่มช่วง)
   คืน {d: ผลต่าง, label} สำหรับโชว์ ▲/▼ ในการ์ด KPI */
function delta(pts) {
    if (!pts || pts.length < 2) return null;
    const last = pts[pts.length - 1], target = last[0] - 3600;
    let ref = pts[0];
    for (const p of pts) if (p[0] <= target) ref = p;
    return { d: last[1] - ref[1], label: pts[0][0] <= target ? "vs. last hour" : "vs. range start" };
}

/* ---------- E. ช่วงเวลา ---------- */
/* ---------- time range ---------- */

/* ช่วงเวลาที่เลือกอยู่ -> [เริ่ม, จบ] เป็น Date (ไม่ได้เลือกวันที่เอง = ย้อนหลัง S.dur จนถึงตอนนี้) */
function rng() {
    const e = S.end || new Date();
    return [S.start || new Date(e - S.dur), e];
}

/* Date -> "HH:MM" หรือถ้า long=1 -> "02 Oct 2026, 13:45" */
function fmtDT(d, long) {
    const p = n => String(n).padStart(2, "0");
    return long
        ? d.toLocaleString("en-GB", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })
        : p(d.getHours()) + ":" + p(d.getMinutes());
}

/* อัปเดตป้ายช่วงเวลาที่ปุ่มด้านบน (เช่น "Last 15 minutes" หรือ "02 Oct ... → now") */
function setLbl() {
    q("#lbl").textContent = S.start
        ? fmtDT(S.start, 1) + " → " + (S.end ? fmtDT(S.end, 1) : "now")
        : RANGES.find(r => r[1] === S.dur)[0];
}

/* ---------- renderers ---------- */

/* ---------- F. ตัววาดส่วนต่าง ๆ ----------
   วาดปุ่มเลือกเครื่อง (UP/DOWN) จาก /api/web-status ; ถ้าเครื่องที่เลือกอยู่หายไป
   จะเลือกเครื่องแรกให้ */
function renderMachines(webs) {
    S.webs = webs;
    const cur = webs.some(w => w.web === S.web) ? S.web : (webs[0] && webs[0].web) || null;
    S.web = cur;
    setText("cur", cur || "-");
    q("#mach").innerHTML = webs.length ? webs.map(w => {
        const st = String(w.status || "unknown").toLowerCase();
        const label = st === "online" ? "UP" : st === "offline" ? "DOWN" : st.toUpperCase();
        return `<button class="chip${st === "offline" ? " dn" : st === "online" ? "" : " wn"}" aria-pressed="${w.web === cur}" data-web="${escapeHtml(w.web)}">${escapeHtml(w.web)}<b>${escapeHtml(label)}</b></button>`;
    }).join("") : `<div class="empty">No WEB configured - open Settings and add a target</div>`;
}

/* แถบสรุปใต้ปุ่มเครื่อง: State, Last pull, Problem logs, Errors 15m, Load, Uptime, Net, ... (ช่องไม่มีค่าจะไม่แสดง) */
function renderMeta() {
    const s = S.summary || {}, m = S.metrics || {}, out = [];
    const add = (k, v, cls) => { if (hasValue(v)) out.push(`<span>${k}<b class="${cls || ""}">${escapeHtml(String(v))}</b></span>`); };
    const st = String(s.current_state || "");
    add("State", st, st === "PROBLEM" ? "bad" : st === "WARNING" ? "wr" : "ok");
    add("Last pull", s.last_pull ? formatTimestamp(s.last_pull) : null);
    add("Problem logs", s.problem_count);
    add("Errors 15m", s.errors_15m);
    add("Load", hasValue(m.load_1m) ? [m.load_1m, m.load_5m, m.load_15m].map(v => formatNumber(v)).join(" / ") : (hasValue(m.load) ? formatNumber(m.load) : null));
    add("Uptime", m.uptime);
    add("Net ↓/↑ MB/s", hasValue(m.net_rx) || hasValue(m.net_tx) ? `${formatNumber(m.net_rx)} / ${formatNumber(m.net_tx)}` : null);
    add("Requests", m.web_requests);
    add("Worker queue", m.worker_queue);
    add("DB connections", m.db_connections);
    add("Edge connections", m.edge_connections);
    q("#meta").innerHTML = out.join("");
}

/* การ์ด KPI 4 ใบ: CPU / MEMORY / DISK / DISK I/O พร้อมเส้นกราฟเล็กและ ▲▼ เทียบก่อนหน้า */
function renderKPIs() {
    const m = S.metrics || {}, ser = (S.hist && S.hist.series) || {};
    const K = [["CPU USAGE", "cpu"], ["MEMORY USAGE", "memory"], ["DISK USAGE", "disk"], ["DISK I/O UTILIZATION (%)", "io_util"]];
    q("#kpis").innerHTML = K.map(([label, key]) => {
        const v = m[key], pts = ser[key] || [], dl = delta(pts);
        const val = hasValue(v) && Number.isFinite(Number(v)) ? Math.round(Number(v)) + "%" : "–";
        const d = dl ? `<i>${dl.d >= 0 ? "▲ +" : "▼ "}${Math.round(dl.d)}%</i>${dl.label}` : "";
        return `<div class="card k"><div><small>${label}</small><div class="v">${val}</div><div class="d">${d}</div></div>${sparkSvg(pts)}</div>`;
    }).join("");
}

/* กราฟใหญ่ System Load (SVG วาดเอง): เส้น CPU (มีพื้นที่ใต้เส้น) และ Memory ตามช่วงเวลาที่เลือก */
function renderLoad() {
    const svg = q("#load"), ser = (S.hist && S.hist.series) || {};
    const [s0, e0] = rng(), t0 = s0 / 1000, t1 = e0 / 1000, W = 900, pl = 44, pw = W - pl - 10, ph = 230;
    let s = "";
    [0, 25, 50, 75, 100].forEach(t => {
        const y = 10 + ph - t / 100 * ph;
        s += `<line x1="${pl}" x2="${pl + pw}" y1="${y}" y2="${y}" stroke="var(--line)"/><text x="${pl - 10}" y="${y + 4}" text-anchor="end" font-size="12" fill="var(--mut)">${t}</text>`;
    });
    const span = t1 - t0, n = 7;
    for (let i = 0; i < n; i++) {
        const d = new Date((t0 + span * i / (n - 1)) * 1000), x = pl + i / (n - 1) * pw;
        const t = span > 2 * 86400 ? d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : fmtDT(d);
        s += `<text x="${x}" y="292" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" font-size="12" fill="var(--mut)">${t}</text>`;
    }
    const line = (pts, color, area) => {
        if (!pts || pts.length < 2) return "";
        const p = pts.map(x => [pl + Math.min(Math.max((x[0] - t0) / span, 0), 1) * pw, 10 + ph - Math.min(Math.max(x[1], 0), 100) / 100 * ph]);
        const d = smooth(p);
        return (area ? `<path d="${d} L${p[p.length - 1][0]},${10 + ph} L${p[0][0]},${10 + ph}Z" fill="var(--fill)"/>` : "") + `<path d="${d}" fill="none" stroke="${color}" stroke-width="2"/>`;
    };
    const has = (ser.cpu && ser.cpu.length > 1) || (ser.memory && ser.memory.length > 1);
    s += line(ser.memory, "var(--soft)", false) + line(ser.cpu, "var(--blue)", true);
    if (!has) s += `<text x="${pl + pw / 2}" y="130" text-anchor="middle" fill="var(--mut)" font-size="14">No history data for this range</text>`;
    svg.innerHTML = s;
}

/* สถานะ service -> คลาสสี: "e" (แดง: fail/dead/error), "" (ปกติ: active/running), "w" (เหลือง: อื่น ๆ) */
function svcClass(status) {
    const v = String(status || "").toLowerCase();
    if (/(fail|dead|error|down|crit)/.test(v)) return "e";
    if (/(run|active|online|ok|up|healthy)/.test(v)) return "";
    return "w";
}

/* แท่งเปอร์เซ็นต์ในตาราง process (v เทียบกับ max) พร้อมข้อความ */
function bar(v, max, text) {
    const w = Math.max(0, Math.min(100, Number(v) / max * 100)) || 0;
    return `<div class="bar"><u><i style="width:${w}%"></i></u><b style="width:auto;min-width:64px">${escapeHtml(text)}</b></div>`;
}

/* รายการจาก snapshot ของ puller: Service (#sv), process ที่กิน CPU (#svc) / Memory (#mem)
   เรียงมากไปน้อย 8 อันดับ, และ System events (#ev ซ่อนการ์ดเมื่อไม่มี) */
function renderLists() {
    const m = S.metrics || {}, svcs = Array.isArray(m.services) ? m.services : [], procs = Array.isArray(m.processes) ? m.processes : [];
    renderServices(svcs);
    const top = key => [...procs].sort((a, b) => Number(b[key] || 0) - Number(a[key] || 0)).slice(0, 8);
    const rows = key => {
        const l = top(key), mx = Math.max(100, ...l.map(p => Number(p[key] || 0)));
        return l.length ? l.map(p => `<tr><td>${escapeHtml(p.name || "-")}</td><td>${escapeHtml(p.pid ?? "-")}</td><td>${bar(p[key], mx, formatPercent(p[key]))}</td></tr>`).join("") : `<tr><td colspan="3" class="empty">No process data collected</td></tr>`;
    };
    q("#svc").innerHTML = rows("cpu");
    q("#mem").innerHTML = rows("memory");
    const ev = Array.isArray(m.system_events) ? m.system_events.filter(e => hasValue(e.message) || hasValue(e.type)) : [];
    q("#evCard").hidden = !ev.length;
    setText("evCnt", ev.length ? `${ev.length} events` : "");
    q("#ev").innerHTML = ev.map(e => `<div class="row"><span>${escapeHtml(formatTimestamp(e.timestamp))}</span><span>${escapeHtml(e.type || "")}</span><span>${escapeHtml(e.message || "")}</span></div>`).join("");
}

/* ---------- การ์ด Service: แสดงเฉพาะ service ที่เลือก ----------
   S.watch = รายชื่อที่เลือกไว้ (เก็บใน puller.yaml ผ่าน /api/targets/<web>/services) ว่าง = แสดงทั้งหมด
   service ที่เลือกไว้แต่ไม่อยู่ในข้อมูลล่าสุด แสดงเป็น "not collected" (จุดเหลือง) */
function renderServices(svcs) {
    const watch = S.watch || [];
    let list = svcs;
    if (watch.length) {
        const byName = new Map(svcs.map(x => [x.name, x]));
        list = watch.map(n => byName.get(n) || { name: n, status: "not collected" });
    }
    setText("svCnt", watch.length ? `${watch.length} of ${svcs.length}` : (svcs.length ? `All ${svcs.length}` : ""));
    q("#sv").innerHTML = list.length
        ? list.map(x => `<div class="row"><span class="st ${svcClass(x.status)}">${escapeHtml(x.name || "-")}</span><span>${escapeHtml(x.status || "-")}</span></div>`).join("")
        : `<div class="empty">No service data collected</div>`;
}

/* โหลดรายชื่อ service ที่เลือกไว้ของเครื่องปัจจุบัน (ครั้งเดียวต่อการเปลี่ยนเครื่อง) */
async function loadWatch() {
    if (S.watchWeb === S.web) return;
    try {
        const d = await apiGet(`/api/targets/${encodeURIComponent(S.web)}/services`);
        S.watch = Array.isArray(d.services) ? d.services : [];
    } catch (err) {
        console.error("Failed to load selected services:", err);
        S.watch = [];
    }
    S.watchWeb = S.web;
}

/* รายชื่อทั้งหมดในตัวเลือก = service ที่เก็บได้ล่าสุด + ที่เลือกไว้ (กันชื่อที่เลือกไว้หายจากรายการ) */
function pickNames() {
    const m = S.metrics || {}, svcs = Array.isArray(m.services) ? m.services : [];
    const status = new Map(svcs.map(x => [x.name, x.status]));
    (S.watch || []).forEach(n => { if (!status.has(n)) status.set(n, "not collected"); });
    return [...status.entries()].filter(([n]) => n).sort((a, b) => a[0].localeCompare(b[0]));
}

/* วาดรายการติ๊กเลือกตามคำค้น */
function renderPick() {
    const f = q("#svFind").value.trim().toLowerCase();
    const rows = pickNames().filter(([n]) => !f || n.toLowerCase().includes(f));
    q("#svList").innerHTML = rows.length
        ? rows.map(([n, st]) => `<label><input type="checkbox" data-svc="${escapeHtml(n)}"${S.pick.has(n) ? " checked" : ""}><span class="st ${svcClass(st)}">${escapeHtml(n)}</span><span>${escapeHtml(st || "-")}</span></label>`).join("")
        : `<div class="empty">No matching service</div>`;
    setText("svSel", S.pick.size ? `${S.pick.size} selected` : "None selected = show all");
}

/* เปิด/ปิดตัวเลือก */
function togglePick(open) {
    S.pick = open ? new Set(S.watch || []) : null;
    q("#svPick").hidden = !open;
    q("#svEdit").setAttribute("aria-expanded", String(open));
    setText("svErr", "");
    if (open) { q("#svFind").value = ""; renderPick(); q("#svFind").focus(); }
}

/* บันทึกการเลือก -> PUT /api/targets/<web>/services */
async function savePick() {
    const b = q("#svSave");
    b.disabled = true; setText("svErr", "");
    try {
        const r = await fetch(`/api/targets/${encodeURIComponent(S.web)}/services`, {
            method: "PUT", credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ services: [...S.pick] })
        });
        if (r.status === 401) { window.location.href = "/login"; return; }
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error((d.fields && d.fields.services) || d.error || `HTTP ${r.status}`);
        S.watch = d.services || []; S.watchWeb = S.web;
        togglePick(false);
        renderServices(Array.isArray((S.metrics || {}).services) ? S.metrics.services : []);
    } catch (err) {
        setText("svErr", `Save failed: ${err.message}`);
    } finally {
        b.disabled = false;
    }
}

function setupServicePicker() {
    q("#svEdit").addEventListener("click", () => togglePick(q("#svPick").hidden));
    q("#svCancel").addEventListener("click", () => togglePick(false));
    q("#svSave").addEventListener("click", savePick);
    q("#svFind").addEventListener("input", renderPick);
    q("#svList").addEventListener("change", e => {
        const c = e.target.closest("input[data-svc]"); if (!c) return;
        c.checked ? S.pick.add(c.dataset.svc) : S.pick.delete(c.dataset.svc);
        setText("svSel", S.pick.size ? `${S.pick.size} selected` : "None selected = show all");
    });
    q("#svAll").addEventListener("click", () => {
        q("#svList").querySelectorAll("input[data-svc]").forEach(c => { c.checked = true; S.pick.add(c.dataset.svc); });
        renderPick();
    });
    q("#svNone").addEventListener("click", () => { S.pick.clear(); renderPick(); });
}

/* ตาราง Logs: 1 แถวต่อ log, คลิกแถว -> openLogDetail */
function renderProblems(list) {
    const tb = q("#plog");
    const filtered = S.lv !== "ALL" || S.lt !== "ALL";
    if (!Array.isArray(list) || !list.length) {
        tb.innerHTML = `<tr><td colspan="6" class="empty">${filtered ? "No logs match this filter" : "No logs"}</td></tr>`;
        setText("logCnt", "");
        renderLogStat(0);
        return;
    }
    tb.innerHTML = "";
    setText("logCnt", `${list.length} logs ·`);
    renderLogStat(list.length);
    list.forEach(p => {
        const tr = document.createElement("tr");
        tr.innerHTML = `<td>${escapeHtml(formatTimestamp(p.timestamp))}</td><td>${escapeHtml(p.web || "-")}</td><td class="lv ${String(p.level || "").toLowerCase()}">${escapeHtml(p.level || "INFO")}</td><td>${escapeHtml(p.type || "application")}</td><td class="msg">${escapeHtml(p.message || "")}</td><td>${escapeHtml(buildProblemMetrics(p.correlated_metrics || {}) || "-")}</td>`;
        tr.addEventListener("click", () => openLogDetail(p));
        tb.appendChild(tr);
    });
}

/* ---------- ตัวกรอง log (Level / Type) ----------
   รายการจริงถูกกรองที่ server (/api/problems?level=&type=) ด้วยตัวจัดระดับเดียวกับตัวนับ
   จึงรับประกันว่า "Error" ที่เห็นคือ log ที่ระบบตัดสินว่า ERROR ตรงกับตัวเลข Problem logs / Errors 15m */
function logUrl() {
    const p = new URLSearchParams({ web: S.web, size: "300" });
    if (S.lv !== "ALL") p.set("level", S.lv);
    if (S.lt !== "ALL") p.set("type", S.lt);
    return `/api/problems?${p}`;
}

/* ตัวเลขบนปุ่ม level + รายการใน dropdown ชนิด (จาก /api/log-stats) */
function renderLogFilter(stats) {
    S.stats = stats || null;
    const lv = (stats && stats.levels) || {};
    const total = stats ? stats.total : null;
    [...q("#lvTabs").children].forEach(b => {
        const k = b.dataset.lv, n = k === "ALL" ? total : lv[k];
        const label = { ALL: "All", ERROR: "Error", WARN: "Warn", INFO: "Info" }[k];
        b.innerHTML = hasValue(n) ? `${label}<i>${n}</i>` : label;
        b.setAttribute("aria-pressed", k === S.lv);
    });
    const types = Object.keys((stats && stats.types) || {}).sort();
    if (S.lt !== "ALL" && !types.includes(S.lt)) types.push(S.lt);     // ชนิดที่เลือกอยู่ต้องไม่หายไปจากรายการ
    const sel = q("#ltSel");
    if (document.activeElement === sel) return;          // กำลังเลือกอยู่ ห้ามวาดใหม่ทับ (รีเฟรชทุก 10 วินาที)
    sel.innerHTML = `<option value="ALL">All types</option>` +
        types.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}${stats && stats.types[t] != null ? ` (${stats.types[t]})` : ""}</option>`).join("");
    sel.value = S.lt;
}

/* ข้อความสรุปข้างตัวกรอง: กำลังแสดงอะไรอยู่ */
function renderLogStat(shown) {
    const parts = [];
    if (S.lv !== "ALL") parts.push(S.lv);
    if (S.lt !== "ALL") parts.push(S.lt);
    setText("lvStat", parts.length ? `Showing ${shown} ${parts.join(" · ")}` : "");
}

/* เปลี่ยนตัวกรองแล้วโหลดเฉพาะรายการ log + จำนวน (ไม่โหลดทั้งหน้า) */
async function reloadLogs() {
    if (!S.web) return;
    try {
        const w = encodeURIComponent(S.web);
        const [problems, stats] = await Promise.all([apiGet(logUrl()), apiGet(`/api/log-stats?web=${w}`)]);
        renderLogFilter(stats);
        renderProblems(problems);
    } catch (err) { console.error("Log filter error:", err); }
}

/* คลิกแถว log: เปิดหน้าต่างทันทีด้วยข้อมูลที่มี แล้วโหลดรายละเอียดเต็มมาเติม */
async function openLogDetail(p) {
    showProblemModal(p);
    try {
        const d = await apiGet(`/api/problems/detail?web=${encodeURIComponent(p.web)}&id=${encodeURIComponent(p.id)}`);
        showProblemModal(d);
    } catch (err) { console.error("Log detail error:", err); }
}

/* ---------- loading ---------- */

/* ---------- G. การโหลดข้อมูล ----------
   โหลดเฉพาะกราฟย้อนหลังตามช่วงเวลาที่เลือก (เรียกเมื่อเปลี่ยนช่วงเวลา ไม่ต้องโหลดทั้งหน้า) */
async function loadHist() {
    if (!S.web) return;
    const [s, e] = rng();
    try {
        S.hist = await apiGet(`/api/metrics/history?web=${encodeURIComponent(S.web)}&start=${Math.floor(s / 1000)}&end=${Math.floor(e / 1000)}`);
    } catch (err) {
        console.error("History error:", err);
        S.hist = null;
    }
    renderKPIs();
    renderLoad();
}

/* โหลดทั้งหน้า: รายชื่อเครื่อง -> (summary, metrics, problems พร้อมกัน) -> กราฟ
   กันเรียกซ้อน (S.busy) ; ตั้งสถานะมุมขวาบนตามผล (DOWN / Problem / Warning / Healthy) */
async function loadAll() {
    if (S.busy) return;
    S.busy = true;
    try {
        setSystemStatus("Loading...", "yellow");
        const webs = await apiGet("/api/web-status");
        renderMachines(webs);
        if (!S.web) { setSystemStatus("No WEB configured", "red"); return; }
        const w = encodeURIComponent(S.web);
        const [summary, metrics, problems, stats] = await Promise.all([
            apiGet(`/api/summary?web=${w}`),
            apiGet(`/api/metrics?web=${w}`),
            apiGet(logUrl()),
            apiGet(`/api/log-stats?web=${w}`)
        ]);
        await loadWatch();
        S.summary = summary;
        S.metrics = { ...(summary.metrics || {}), ...(metrics || {}) };
        renderMeta();
        renderLists();
        renderLogFilter(stats);
        renderProblems(problems);
        await loadHist();
        const st = String(summary.status || "").toLowerCase(), cs = summary.current_state;
        if (st === "offline") setSystemStatus(`${S.web} is DOWN`, "red");
        else if (cs === "PROBLEM") setSystemStatus("Problem detected", "red");
        else if (cs === "WARNING") setSystemStatus("Warning", "yellow");
        else setSystemStatus("System Healthy", "green");
    } catch (err) {
        console.error("Dashboard API error:", err);
        setSystemStatus("API Error", "red");
    } finally {
        S.busy = false;
    }
}

/* ---------- time picker ---------- */

/* DN = ตัวย่อชื่อวันในปฏิทิน (อาทิตย์เป็นวันแรก) */
const DN = ["S", "M", "T", "W", "T", "F", "S"];
/* ---------- H. ตัวเลือกช่วงเวลา/ปฏิทิน ----------
   ปิดเมนู/ปฏิทินทั้งหมด */
function hide() { q("#menu").hidden = q("#dates").hidden = q("#cal").hidden = true; }
/* อัปเดตป้ายวันเวลา เริ่ม/จบ ในกล่อง Show dates */
function upd() {
    q("#ds").textContent = fmtDT(S.tmp.s, 1);
    q("#de").textContent = S.tmp.e ? fmtDT(S.tmp.e, 1) : "now";
    q("#err").textContent = "";
}
/* วันเวลาที่กำลังแก้ไขอยู่ (เริ่ม หรือ จบ) */
function cur() { return S.ed === "s" ? S.tmp.s : (S.tmp.e || new Date()); }
/* เป็นวันเดียวกันไหม */
function sameDay(a, b) { return a.toDateString() === b.toDateString(); }

/* วาดปฏิทินของเดือนที่ดูอยู่ (S.vm) พร้อมช่องเลือกเวลา */
function renderCal() {
    const c = cur(), y = S.vm.getFullYear(), m = S.vm.getMonth(), f = new Date(y, m, 1).getDay(), n = new Date(y, m + 1, 0).getDate(), p = v => String(v).padStart(2, "0");
    let g = DN.map(d => `<span>${d}</span>`).join("") + "<span></span>".repeat(f);
    for (let d = 1; d <= n; d++) {
        const x = new Date(y, m, d);
        g += `<button type="button" data-d="${d}" class="${sameDay(x, c) && (S.ed === "s" || S.tmp.e) ? "sel" : sameDay(x, new Date()) ? "td" : ""}">${d}</button>`;
    }
    q("#cal").innerHTML = `<div class="ch"><button type="button" data-n="-1" aria-label="Previous month">‹</button>${S.vm.toLocaleString("en-US", { month: "long", year: "numeric" })}<button type="button" data-n="1" aria-label="Next month">›</button></div><div class="cg">${g}</div><div class="ct"><input type="time" id="tm" value="${p(c.getHours())}:${p(c.getMinutes())}">${S.ed === "e" ? '<button type="button" id="nw">Now</button>' : ""}</div>`;
}

/* เปิด/ปิดปฏิทินสำหรับแก้ "เริ่ม" (k="s") หรือ "จบ" (k="e") */
function openCal(k) {
    const same = !q("#cal").hidden && S.ed === k;
    S.ed = k;
    q("#ds").setAttribute("aria-expanded", !same && k === "s");
    q("#de").setAttribute("aria-expanded", !same && k === "e");
    q("#cal").hidden = same;
    if (!same) { S.vm = new Date(cur()); renderCal(); }
}

/* ผูกเหตุการณ์ทั้งหมดของตัวเลือกช่วงเวลา: เมนูด่วน, Show dates, ปฏิทิน, Apply, Refresh, Esc */
function setupPicker() {
    q("#menu").innerHTML = RANGES.map(r => `<button data-d="${r[1]}">${r[0]}</button>`).join("");
    const mark = () => [...q("#menu").children].forEach(b => b.setAttribute("aria-current", !S.start && +b.dataset.d === S.dur));
    q("#qb").onclick = q("#lbl").onclick = () => { const o = q("#menu").hidden; hide(); q("#menu").hidden = !o; mark(); };
    q("#menu").onclick = e => {
        const b = e.target.closest("button"); if (!b) return;
        S.dur = +b.dataset.d; S.start = S.end = null; setLbl(); hide(); loadHist();
    };
    q("#sd").onclick = () => {
        const o = q("#dates").hidden, r = rng(); hide();
        if (o) { S.tmp = { s: r[0], e: S.start ? S.end : null }; S.ed = "s"; upd(); q("#dates").hidden = false; q("#ds").setAttribute("aria-expanded", "false"); q("#de").setAttribute("aria-expanded", "false"); }
    };
    q("#ds").onclick = () => openCal("s");
    q("#de").onclick = () => openCal("e");
    q("#cal").onclick = e => {
        const b = e.target.closest("button"); if (!b) return;
        if (b.dataset.n) { S.vm = new Date(S.vm.getFullYear(), S.vm.getMonth() + +b.dataset.n, 1); renderCal(); }
        else if (b.id === "nw") { S.tmp.e = null; upd(); renderCal(); }
        else if (b.dataset.d) { const c = cur(); S.tmp[S.ed] = new Date(S.vm.getFullYear(), S.vm.getMonth(), +b.dataset.d, c.getHours(), c.getMinutes()); upd(); renderCal(); }
    };
    q("#cal").onchange = e => {
        if (e.target.id !== "tm" || !e.target.value) return;
        const t = e.target.value.split(":"), c = new Date(cur()); c.setHours(+t[0], +t[1], 0, 0); S.tmp[S.ed] = c; upd();
    };
    q("#ap").onclick = () => {
        const e = S.tmp.e || new Date();
        if (e <= S.tmp.s) { q("#err").textContent = "End must be after start"; return; }
        S.start = S.tmp.s; S.end = S.tmp.e; setLbl(); hide(); loadHist();
    };
    q("#rf").onclick = () => loadAll();
    document.addEventListener("pointerdown", e => { if (!e.target.closest(".tp")) hide(); });
    document.addEventListener("keydown", e => { if (e.key === "Escape") { hide(); closeProblemModal(); } });
}

/* ---------- user / logout ---------- */

/* ---------- I. ผู้ใช้ / Logout ----------
   ถามว่าใครล็อกอินอยู่ (/api/me) เพื่อแสดงชื่อ ; ไม่ได้ล็อกอิน -> หน้า login */
async function loadCurrentUser() {
    try {
        const d = await apiGet("/api/me");
        if (!d.authenticated) { window.location.href = "/login"; return; }
        setText("currentUsername", d.username || "Unknown");
    } catch (err) { console.error("Failed to load current user:", err); }
}

/* กด Logout: POST /api/logout แล้วกลับหน้า login */
async function logout() {
    const b = $("logoutButton");
    b.disabled = true; b.textContent = "Logging out...";
    try {
        const r = await fetch("/api/logout", { method: "POST", credentials: "same-origin" });
        if (r.ok) { window.location.href = "/login"; return; }
        throw new Error("Logout failed");
    } catch (err) { console.error(err); b.disabled = false; b.textContent = "Logout"; }
}

/* ---------- จุดเริ่มต้น: ตั้งค่าหน้า -> โหลดข้อมูลครั้งแรก -> รีเฟรชทุก 10 วินาที ---------- */
/* ---------- start ---------- */

document.addEventListener("DOMContentLoaded", () => {
    setupPicker();
    setupServicePicker();
    setLbl();
    q("#mach").addEventListener("click", e => {
        const b = e.target.closest("button[data-web]"); if (!b) return;
        if (b.dataset.web !== S.web) togglePick(false);
        S.web = b.dataset.web; S.hist = null; S.lv = "ALL"; S.lt = "ALL"; loadAll();
    });
    q("#lvTabs").addEventListener("click", e => {
        const b = e.target.closest("button[data-lv]"); if (!b) return;
        S.lv = b.dataset.lv; reloadLogs();
    });
    q("#ltSel").addEventListener("change", e => { S.lt = e.target.value; reloadLogs(); });
    $("logoutButton").addEventListener("click", logout);
    loadCurrentUser();
    loadAll();
    setInterval(loadAll, 10000);
});
