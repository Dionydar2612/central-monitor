/* =====================================================================
   dashboard.js - ตรรกะทั้งหมดของหน้า dashboard (index.html)
   =====================================================================

   ภาพรวมการทำงาน
     1) เปิดหน้า -> DOMContentLoaded (ท้ายไฟล์) ตั้งปุ่มต่าง ๆ แล้วเรียก loadAll()
     2) loadAll() เรียก API ของ web_dashboard/backend/api.py:
          /api/overview        ทุกเครื่อง: Total/Up/Down + CPU/Mem/Disk -> renderMachines (ตาราง Server Status)
          /api/summary         สรุปเครื่อง (State, Last pull, ...)  -> renderMeta
          /api/metrics         ตัวเลข + service/process/event       -> renderLists
          /api/problems        รายการ log (ERROR / WARN เท่านั้น)   -> renderProblems
          /api/info-logs       INFO สด เฉพาะตอนกดปุ่ม Load INFO log  -> loadInfo (ไม่เก็บ)
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
    pick: null,                            // Set ของ service ที่ติ๊กอยู่ในตัวเลือก (null = ปิดตัวเลือก)
    svf: "all", svnf: false,               // ตัวกรองการ์ด Service: แท็บ (all/failed/active/inactive) + แสดง not-found ไหม
    chart: "main", cmp: false, ext: null,  // กราฟ System Load: แท็บ, เทียบเมื่อวาน, ข้อมูลของแท็บ (main = S.hist)
    marks: [], markGroups: [], view: null, // จุด ERROR/WARN log + ข้อมูลที่วาดอยู่ (ใช้ตอนชี้เมาส์)
    hoverX: null, drag: null, tipPos: null, zoomPrev: null, logKey: null,
    info: null, infoBusy: false,           // INFO ที่กดโหลดสด {rows, errors, lines} (null = แสดง ERROR/WARN ปกติ) ไม่เก็บที่ไหน
    live: null, liveAt: 0, liveBusy: false, liveErr: null,   // process ต่อ core + top memory วัดสด (/api/live-processes)
    pullInterval: 3600,                    // scheduler.interval (วินาที) ใช้ตัดสินว่า Last pull เก่าผิดปกติ
    ov: null, ovq: "", closed: loadClosedGroups(),   // ภาพรวม (/api/overview), ตัวกรองต้นไม้, กลุ่มที่ยุบไว้
    role: null,                            // role ของผู้ใช้ (viewer / operator / admin) จาก /api/me
    search: null, searchBusy: false,       // ผลค้นหา log {q, scope, rows, total, matched, truncated}
    withMetrics: loadWithMetrics(),        // ตาราง Logs: เฉพาะ log ที่มี Metrics at time (ค่าเริ่มต้น = แสดงทั้งหมด, จำใน browser)
    showAcked: loadShowAcked(),            // แสดง log ที่ acknowledge แล้วด้วยไหม (ค่าเริ่มต้น = ซ่อน)
    sel: new Map(), logRows: []            // log ที่ติ๊กเลือกไว้ (id -> แถว) + แถวที่แสดงอยู่
};
/* วัด process สดใหม่ทุกกี่ ms (ขณะเปิดหน้า) */
const LIVE_EVERY = 60000;

/* Last pull เก่ากว่า 2 รอบดึง = puller ติดต่อเครื่องไม่ได้ (เช่น SSH ล้มเหลว) */
function isStale(iso) {
    const t = Date.parse(iso || "");
    return Number.isFinite(t) && (Date.now() - t) / 1000 > 2 * (S.pullInterval || 3600) + 300;
}
function pullHtml(iso) {
    if (!iso) return "-";
    const txt = escapeHtml(formatTimestamp(iso));
    return isStale(iso) ? `<span class="stale" title="puller has not reached this machine for more than 2 pull cycles - check: docker logs central-puller">${txt} ⚠</span>` : txt;
}

/* เกณฑ์สีของการ์ด KPI (%) : ถึง warn = เหลือง, ถึง crit = แดง  (แก้ตัวเลขที่นี่ที่เดียว) */
const TH = {
    cpu: { warn: 75, crit: 90 },
    memory: { warn: 80, crit: 90 },
    disk: { warn: 80, crit: 90 },
    io_util: { warn: 70, crit: 90 }
};
/* ค่าทั้งระบบ (ใช้เมื่อเครื่องไม่มีเกณฑ์เฉพาะ) ; applyTargetThresholds เปลี่ยน TH ตามเครื่องที่เลือก (puller.yaml -> targets[].thresholds) */
const TH_DEFAULT = JSON.parse(JSON.stringify(TH));
function applyTargetThresholds(th) {
    ["cpu", "memory", "disk"].forEach(k => {
        const t = th && th[k];
        TH[k] = t && t.warning != null && t.error != null ? { warn: Number(t.warning), crit: Number(t.error) } : { ...TH_DEFAULT[k] };
    });
}
/* ระดับของค่า v ตามเกณฑ์ของ key -> "cr" | "wr" | "" */
function level(key, v) {
    const t = TH[key], n = Number(v);
    if (!t || !hasValue(v) || !Number.isFinite(n)) return "";
    return n >= t.crit ? "cr" : n >= t.warn ? "wr" : "";
}

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
   การ์ด WEB status (จาก /api/overview) แบบกลุ่ม (เหมือน device tree)
     ช่องบน     : Total / OK / Warning / Down / Paused  (+ Web checks)
     ต้นไม้กลุ่ม : group ของเครื่อง/check ใน puller.yaml คั่นชั้นด้วย "/" (เช่น TQ/IT) ไม่มีกลุ่ม = Ungrouped
                  หัวกลุ่มมีป้ายนับสถานะของทุกอย่างข้างใน (รวมกลุ่มย่อย) คลิกหัวกลุ่ม = ยุบ/ขยาย (จำไว้ใน browser)
     ในกลุ่ม    : ตารางเครื่อง (คลิกแถว = เลือกเครื่องที่จะดูรายละเอียดด้านล่าง) + ตาราง web checks
     ปุ่ม Pause/Resume แสดงเฉพาะ operator / admin */
const STATE_LABEL = { HEALTHY: ["OK", "ok"], WARNING: ["Warning", "wr"], PROBLEM: ["Problem", "cr"], DOWN: ["Down", "cr"], PAUSED: ["Paused", "ps"] };
const CHECK_LABEL = { up: ["Up", "ok"], warning: ["Warning", "wr"], down: ["Down", "cr"], paused: ["Paused", "ps"], pending: ["Pending", ""] };
const UNGROUPED = "Ungrouped";
/* Web checks (uptime / SSL) ซ่อนไว้ก่อน: เปลี่ยนเป็น true เพื่อแสดงในต้นไม้กลุ่ม + ช่องสรุป (ต้องเปิดที่ settings.js ด้วย) */
const SHOW_CHECKS = false;

/* สถานะรวม 4 แบบ (ใช้นับป้ายของกลุ่ม) */
function itemBucket(x, isCheck) {
    if (isCheck) return { up: "ok", warning: "wr", down: "dn", paused: "ps" }[x.status] || "na";
    return { HEALTHY: "ok", WARNING: "wr", PROBLEM: "dn", DOWN: "dn", PAUSED: "ps" }[x.state] || "na";
}
const BUCKET_PILL = { dn: ["▼", "Down / Problem"], wr: ["W", "Warning"], ok: ["✓", "OK"], ps: ["II", "Paused"], na: ["?", "Unknown"] };

function loadWithMetrics() {
    try { return localStorage.getItem("cm.logsWithMetrics") === "1"; } catch (e) { return false; }
}
function loadShowAcked() {
    try { return localStorage.getItem("cm.logsShowAcked") === "1"; } catch (e) { return false; }
}

function loadClosedGroups() {
    try { return new Set(JSON.parse(localStorage.getItem("cm.closedGroups") || "[]")); } catch (e) { return new Set(); }
}
function saveClosedGroups() {
    try { localStorage.setItem("cm.closedGroups", JSON.stringify([...S.closed])); } catch (e) { /* browser ไม่ให้เก็บ -> ไม่จำ */ }
}

function canOperate() { return S.role === "operator" || S.role === "admin"; }

function buildTree(servers, checks) {
    const root = { name: "", path: "", kids: new Map(), servers: [], checks: [], counts: {} };
    const node = path => {
        let n = root;                       /* ไม่มีกลุ่ม -> อยู่ชั้นบนสุด ไม่มีหัวกลุ่ม */
        String(path || "").split("/").filter(Boolean).forEach((part, i, all) => {
            if (!n.kids.has(part)) n.kids.set(part, { name: part, path: all.slice(0, i + 1).join("/"), kids: new Map(), servers: [], checks: [], counts: {} });
            n = n.kids.get(part);
        });
        return n;
    };
    servers.forEach(x => node(x.group).servers.push(x));
    checks.forEach(x => node(x.group).checks.push(x));
    const count = n => {
        const c = {};
        n.servers.forEach(x => { const k = itemBucket(x); c[k] = (c[k] || 0) + 1; });
        n.checks.forEach(x => { const k = itemBucket(x, true); c[k] = (c[k] || 0) + 1; });
        n.kids.forEach(k => Object.entries(count(k)).forEach(([b, v]) => { c[b] = (c[b] || 0) + v; }));
        n.counts = c;
        return c;
    };
    count(root);
    return root;
}

function pillsHtml(counts) {
    return ["dn", "wr", "ok", "ps", "na"].filter(k => counts[k]).map(k =>
        `<span class="gpill ${k}" title="${BUCKET_PILL[k][1]}"><i>${BUCKET_PILL[k][0]}</i>${counts[k]}</span>`).join("");
}

function serverRows(list, cur) {
    const cell = (key, v) => {
        const ok = hasValue(v) && Number.isFinite(Number(v));
        return `<td class="ovv ${ok ? level(key, v) || "ok" : ""}">${ok ? Number(v).toFixed(1) : "-"}</td>`;
    };
    const op = canOperate();
    return `<table class="ovtbl"><thead><tr><th>Name</th><th>IP</th><th>Status</th><th>CPU avg 5m</th><th>Memory %</th><th>Disk %</th><th>Last pull</th>${op ? "<th></th>" : ""}</tr></thead><tbody>` +
        list.map(w => {
            const [label, cls] = STATE_LABEL[w.state] || [w.state || "-", ""];
            const btn = op ? `<td class="act"><button type="button" class="svbtn sm" data-pause="${escapeHtml(w.web)}" data-kind="targets" data-paused="${w.paused ? 1 : 0}">${w.paused ? "Resume" : "Pause"}</button></td>` : "";
            const ack = w.acked ? ` <span class="ovack" title="${w.acked} acknowledged problem(s)">ACK ${w.acked}</span>` : "";
            const why = w.problems && w.problems.length ? ` title="${escapeHtml(w.problems.join(", "))}"` : "";
            return `<tr data-web="${escapeHtml(w.web)}" aria-selected="${w.web === cur}" tabindex="0" class="${w.paused ? "paused" : ""}"><td><b>${escapeHtml(w.web)}</b></td><td>${escapeHtml(w.host || w.instance || "-")}</td><td><span class="ovst ${cls}"${why}>${escapeHtml(label)}</span>${ack}</td>${cell("cpu", w.cpu)}${cell("memory", w.memory)}${cell("disk", w.disk)}<td>${w.paused ? "-" : pullHtml(w.last_pull)}</td>${btn}</tr>`;
        }).join("") + `</tbody></table>`;
}

function checkRows(list) {
    const op = canOperate();
    const sslCell = c => {
        const d = c.ssl && c.ssl.days_left;
        if (!c.ssl) return `<td>-</td>`;
        if (!hasValue(d)) return `<td class="ovv cr" title="${escapeHtml(c.ssl.error || "")}">error</td>`;
        const cls = d < 0 ? "cr" : d <= (c.ssl_warn_days || 14) ? "wr" : "ok";
        return `<td class="ovv ${cls}" title="${escapeHtml(`${c.ssl.issuer || ""} · expires ${c.ssl.expires_at ? formatTimestamp(c.ssl.expires_at) : "-"}${c.ssl.valid === false ? " · " + (c.ssl.error || "invalid") : ""}`)}">${d < 0 ? "expired" : d + " days"}</td>`;
    };
    return `<table class="ovtbl chk"><thead><tr><th>Web check</th><th>URL</th><th>Status</th><th>HTTP</th><th>Response</th><th>Uptime 24h</th><th>SSL expires</th><th>Last check</th>${op ? "<th></th>" : ""}</tr></thead><tbody>` +
        list.map(c => {
            const [label, cls] = CHECK_LABEL[c.status] || [c.status || "-", ""];
            const up = hasValue(c.uptime_24h) ? Number(c.uptime_24h) : null;
            const btn = op ? `<td class="act"><button type="button" class="svbtn sm" data-pause="${escapeHtml(c.name)}" data-kind="checks" data-paused="${c.paused ? 1 : 0}">${c.paused ? "Resume" : "Pause"}</button></td>` : "";
            return `<tr class="${c.paused ? "paused" : ""}"><td><b>${escapeHtml(c.name)}</b></td><td class="url"><a href="${escapeHtml(c.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(c.url)}</a></td><td><span class="ovst ${cls}" title="${escapeHtml(c.error || "")}">${escapeHtml(label)}</span>${c.error && c.status !== "up" ? `<div class="cerr">${escapeHtml(c.error)}</div>` : ""}</td><td>${hasValue(c.code) ? escapeHtml(c.code) : "-"}</td><td>${hasValue(c.ms) ? escapeHtml(c.ms) + " ms" : "-"}</td><td class="ovv ${up == null ? "" : up >= 99.9 ? "ok" : up >= 99 ? "wr" : "cr"}">${up == null ? "-" : up.toFixed(up >= 99.9 ? 2 : 1) + "%"}</td>${sslCell(c)}<td>${c.checked_at ? escapeHtml(formatTimestamp(c.checked_at)) : "-"}</td>${btn}</tr>`;
        }).join("") + `</tbody></table>`;
}

function groupHtml(n, cur, depth, forceOpen) {
    const open = forceOpen || !S.closed.has(n.path);
    const body = !open ? "" :
        `<div class="gbody">${n.servers.length ? `<div class="tblw">${serverRows(n.servers, cur)}</div>` : ""}${n.checks.length ? `<div class="tblw">${checkRows(n.checks)}</div>` : ""}${[...n.kids.values()].sort((a, b) => a.name.localeCompare(b.name)).map(k => groupHtml(k, cur, depth + 1, forceOpen)).join("")}</div>`;
    const items = n.servers.length + n.checks.length;
    return `<div class="gnode" style="--d:${depth}"><div class="ghead" data-group="${escapeHtml(n.path)}" role="button" tabindex="0" aria-expanded="${open}"><i class="caret">${open ? "▾" : "▸"}</i><span class="gname">${escapeHtml(n.name)}</span>${items && !open ? `<span class="gsub">${items} item${items === 1 ? "" : "s"}</span>` : ""}<span class="gpills">${pillsHtml(n.counts)}</span></div>${body}</div>`;
}

function matchesFilter(x, f, isCheck) {
    if (!f) return true;
    const hay = isCheck ? [x.name, x.url, x.group] : [x.web, x.host, x.instance, x.group, x.state];
    return hay.some(v => String(v || "").toLowerCase().includes(f));
}

function renderMachines(ov) {
    S.ov = ov || S.ov;
    ov = S.ov || {};
    const webs = Array.isArray(ov.servers) ? ov.servers : [];
    const checks = SHOW_CHECKS && Array.isArray(ov.checks) ? ov.checks : [];
    S.webs = webs;
    if (ov.pull_interval) S.pullInterval = Number(ov.pull_interval) || 3600;
    const cur = webs.some(w => w.web === S.web) ? S.web : ((webs.find(w => !w.paused) || webs[0] || {}).web || null);
    S.web = cur;
    applyTargetThresholds((webs.find(w => w.web === cur) || {}).thresholds);
    setText("cur", cur || "-");

    const cb = c => checks.filter(x => x.status === c).length;
    const tile = (label, n, cls, sub) => `<div class="ovtile ${cls}"><small>${label}</small><b>${hasValue(n) ? n : "-"}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
    const okCount = webs.filter(w => w.state === "HEALTHY").length;
    q("#ovTiles").innerHTML =
        tile("Total", ov.total, "tot", "machines") +
        tile("OK", okCount, "up", hasValue(ov.up) ? `${ov.up} up` : "") +
        tile("Warning", (ov.warning || 0), "nok", "≥ warning threshold") +
        tile("Down / Problem", (ov.down || 0) + (ov.problem || 0), "dn", `${ov.down || 0} down · ${ov.problem || 0} problem`) +
        tile("Paused", ov.paused || 0, "ps", "") +
        (SHOW_CHECKS ? tile("Web checks", checks.length, "chk", checks.length ? `${cb("up")} up · ${cb("warning")} warn · ${cb("down")} down` : "none") : "");
    q("#ovTiles").classList.toggle("five", !SHOW_CHECKS);

    const f = (S.ovq || "").trim().toLowerCase();
    const tree = buildTree(webs.filter(w => matchesFilter(w, f)), checks.filter(c => matchesFilter(c, f, true)));
    const tops = [...tree.kids.values()].sort((a, b) => a.name.localeCompare(b.name));
    /* เครื่อง/check ที่ไม่ได้ตั้งกลุ่ม: แสดงตารางตรง ๆ ไม่มีหัวกลุ่ม (กลุ่มแสดงเฉพาะที่ตั้งชื่อไว้) */
    const loose = (tree.servers.length ? `<div class="tblw gloose">${serverRows(tree.servers, cur)}</div>` : "") +
        (tree.checks.length ? `<div class="tblw gloose">${checkRows(tree.checks)}</div>` : "");
    q("#mach").innerHTML = !webs.length && !checks.length
        ? `<div class="empty">No WEB configured - open Settings and add a target</div>`
        : (tops.length || loose) ? loose + tops.map(n => groupHtml(n, cur, 0, !!f)).join("") : `<div class="empty">Nothing matches "${escapeHtml(S.ovq)}"</div>`;
}

async function togglePause(btn) {
    const kind = btn.dataset.kind, name = btn.dataset.pause, paused = btn.dataset.paused !== "1";
    if (paused && !confirm(`Pause ${name}?\n${kind === "targets" ? "หยุดดึง log และเก็บสถานะของเครื่องนี้จนกว่าจะกด Resume" : "หยุดเช็ค URL นี้จนกว่าจะกด Resume"}`)) return;
    btn.disabled = true;
    try {
        const r = await fetch(`/api/${kind}/${encodeURIComponent(name)}/pause`, {
            method: "PUT", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ paused }) });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
        renderMachines(await apiGet("/api/overview"));
    } catch (err) { alert("Pause/Resume failed: " + err.message); btn.disabled = false; }
}

/* แถบสรุปใต้ปุ่มเครื่อง: State, Last pull, Problem logs, Errors 15m, Load, Uptime, Net, ... (ช่องไม่มีค่าจะไม่แสดง) */
function renderMeta() {
    const s = S.summary || {}, m = S.metrics || {}, out = [];
    const add = (k, v, cls) => { if (hasValue(v)) out.push(`<span>${k}<b class="${cls || ""}">${escapeHtml(String(v))}</b></span>`); };
    const st = String(s.current_state || "");
    add("State", st, st === "PROBLEM" ? "bad" : st === "WARNING" ? "wr" : "ok");
    add("Last pull", s.last_pull ? formatTimestamp(s.last_pull) + (isStale(s.last_pull) ? " ⚠ stale" : "") : null, s.last_pull && isStale(s.last_pull) ? "bad" : "");
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

/* รายการ mount point ในการ์ด Disk (เต็มมากสุดขึ้นก่อน แสดง 3 อันแรก ที่เหลือดูได้จาก tooltip) */
function mountsHtml(mounts) {
    if (!Array.isArray(mounts) || !mounts.length) return "";
    const show = mounts.slice(0, 3), more = mounts.length - show.length;
    const rows = show.map(x => {
        const lv = level("disk", x.used), w = Math.max(0, Math.min(100, Number(x.used) || 0));
        return `<div class="${lv}" title="${escapeHtml(`${x.mountpoint} · ${x.device || ""} ${x.fstype || ""}`)}"><code>${escapeHtml(x.mountpoint)}</code><u><i style="width:${w}%"></i></u><b>${Math.round(Number(x.used))}%</b></div>`;
    }).join("");
    const rest = more > 0 ? `<div title="${escapeHtml(mounts.slice(3).map(x => `${x.mountpoint} ${Math.round(x.used)}%`).join("\n"))}"><code>+${more} more</code></div>` : "";
    return `<div class="mnts">${rows}${rest}</div>`;
}

/* การ์ด KPI 4 ใบ: CPU average 5m / Memory Usage % / Disk Usage % (Per Mount Point) / Disk I/O utilization (%)
   พร้อมเส้นกราฟเล็กและ ▲▼ เทียบก่อนหน้า
   Disk: ตัวเลขใหญ่ = mount point ที่เต็มที่สุด, ด้านล่างแสดงทีละ mount point */
function renderKPIs() {
    const m = S.metrics || {}, ser = (S.hist && S.hist.series) || {};
    const K = [["CPU average 5m", "cpu"], ["Memory Usage %", "memory"], ["Disk Usage % (Per Mount Point)", "disk"], ["Disk I/O utilization (%)", "io_util"]];
    q("#kpis").innerHTML = K.map(([label, key]) => {
        const v = m[key], pts = ser[key] || [], dl = delta(pts);
        const ok = hasValue(v) && Number.isFinite(Number(v)), val = ok ? Number(v).toFixed(1) + "%" : "–";
        const lv = level(key, v), t = TH[key];
        /* ลูกศร: ค่าพวกนี้ "ยิ่งสูงยิ่งแย่" -> เพิ่มขึ้น = สีเตือน, ลดลง = เขียว, เปลี่ยนไม่ถึง 1% = เทา */
        let d = "";
        if (dl) {
            const r = Math.round(dl.d);
            d = r === 0 ? `<i class="fl">● 0%</i>${dl.label}` : r > 0 ? `<i class="ri">▲ +${r}%</i>${dl.label}` : `<i>▼ ${r}%</i>${dl.label}`;
        }
        const tip = t ? ` title="warning ≥ ${t.warn}% · critical ≥ ${t.crit}%"` : "";
        const badge = lv === "cr" ? `<em class="kb cr">Critical</em>` : lv === "wr" ? `<em class="kb wr">Warning</em>` : "";
        const extra = key === "disk" ? mountsHtml(m.disk_mounts) : "";
        return `<div class="card k ${lv}${extra ? " dk" : ""}"${tip}><div><small>${label}</small><div class="v">${val}${badge}</div><div class="d">${d}</div></div>${extra}${sparkSvg(pts)}</div>`;
    }).join("");
}

/* ---------- กราฟใหญ่ System Load ----------
   แท็บ (CH): CPU & Memory / CPU breakdown / Disk I/O / Network / Load
   - ช่วงไม่มีข้อมูล: ตัดเส้น + ระบายเทา "No data"
   - เส้นเกณฑ์ (TH) สำหรับกราฟ %, เส้นจำนวน core สำหรับกราฟ Load
   - "vs yesterday": เส้นประของเมื่อวานเวลาเดียวกัน
   - จุด ERROR/WARN log ใต้กราฟ (/api/log-markers) คลิกเปิดรายละเอียด
   - เลื่อนเมาส์ดูค่า, ลากเพื่อซูม (ตั้งช่วงเวลาของทั้งหน้า)
   ชั้นบน #loadOv (overlay) แยกจาก #load เพื่อไม่ให้รีเฟรชทุก 10 วินาทีลบสิ่งที่กำลังชี้/ลากอยู่ */
const CH = {
    main: { label: "CPU & Memory", unit: "%", max: 100, th: "cpu",
        lines: [["cpu", "CPU avg 5m", "#1f7bff", true], ["memory", "Memory", "#8b5cf6"]] },
    cpu: { label: "CPU breakdown", unit: "%", max: 100, th: "cpu", stacked: true,
        lines: [["cpu_user", "User", "#1f7bff"], ["cpu_system", "System", "#8b5cf6"], ["cpu_iowait", "I/O wait", "#f5a524"]] },
    io: { label: "Disk I/O", unit: "MB/s",
        lines: [["disk_read", "Read", "#1f7bff", true], ["disk_write", "Write", "#10c27a"]] },
    net: { label: "Network", unit: "MB/s",
        lines: [["net_rx", "Receive ↓", "#1f7bff", true], ["net_tx", "Send ↑", "#10c27a"]] }
    /* แท็บ Load ถูกนำออก (ค่า Load ยังดูได้ที่แถบสรุปด้านบน) */
};
/* เรขาคณิตของกราฟ (หน่วย viewBox) */
const GW = 900, GH = 312, PL = 52, PR = 12, PT = 10, PH = 230, PW = GW - PL - PR, MY = 262;
const PB = PT + PH;

/* ค่าบนแกน Y ที่ "สวย" ขั้นต่ำ m */
function niceMax(m) {
    if (!(m > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(m)));
    for (const f of [1, 2, 2.5, 5, 10]) if (f * p >= m) return f * p;
    return 10 * p;
}
function fmtVal(v, unit) {
    if (!hasValue(v) || !Number.isFinite(Number(v))) return "-";
    const n = Number(v);
    if (unit === "%") return `${n.toFixed(n < 10 ? 1 : 0)}%`;
    const t = Math.abs(n) >= 100 ? n.toFixed(0) : Math.abs(n) >= 10 ? n.toFixed(1) : n.toFixed(2);
    return unit ? `${t} ${unit}` : t;
}
/* ตัดอนุกรมเป็นท่อน เมื่อจุดห่างกันเกิน gap วินาที */
function splitGaps(pts, gap) {
    const out = []; let cur = [];
    (pts || []).forEach(p => {
        if (cur.length && p[0] - cur[cur.length - 1][0] > gap) { out.push(cur); cur = []; }
        cur.push(p);
    });
    if (cur.length) out.push(cur);
    return out;
}
/* ค่าที่ใกล้เวลา t ที่สุด (ห่างไม่เกิน gap) */
function valueAt(pts, t, gap) {
    let best = null, bd = Infinity;
    for (const p of pts || []) { const d = Math.abs(p[0] - t); if (d < bd) { bd = d; best = p; } }
    return best && bd <= gap ? best : null;
}

/* เตรียมข้อมูลของแท็บที่เลือก -> S.view (ใช้ทั้งตอนวาดและตอนชี้เมาส์) */
function chartView() {
    const cfg = CH[S.chart], data = S.ext || {}, ser = data.series || {}, cmp = S.cmp ? (data.compare || {}) : null;
    const [s0, e0] = rng(), t0 = s0 / 1000, t1 = e0 / 1000, span = Math.max(t1 - t0, 1);
    const step = Number(data.step || 15), gap = Math.max(step * 3, 60);
    let lines = cfg.lines.map(([k, name, color, area]) => ({ k, name, color, area, pts: ser[k] || [], cpts: cmp ? (cmp[k] || []) : [] }));
    if (cfg.stacked) {
        /* ซ้อนชั้น: แต่ละจุดเก็บ [t, ค่าสะสมบน, ค่าสะสมล่าง, ค่าจริง] */
        const base = new Map();
        lines.forEach(l => {
            l.stack = l.pts.map(p => { const b = base.get(p[0]) || 0, top = b + p[1]; base.set(p[0], top); return [p[0], top, b, p[1]]; });
        });
        const total = new Map();
        lines.forEach(l => l.cpts.forEach(p => total.set(p[0], (total.get(p[0]) || 0) + p[1])));
        lines.cmpTotal = [...total.entries()].sort((a, b) => a[0] - b[0]);
    }
    let ymax = cfg.max;
    if (!ymax) {
        let m = 0;
        lines.forEach(l => { l.pts.forEach(p => { m = Math.max(m, p[1]); }); l.cpts.forEach(p => { m = Math.max(m, p[1]); }); });
        if (cfg.ref && data[cfg.ref]) m = Math.max(m, data[cfg.ref] * 1.15);
        ymax = niceMax(m * 1.1);
    }
    const X = t => PL + Math.min(Math.max((t - t0) / span, 0), 1) * PW;
    const Y = v => PB - Math.min(Math.max(v, 0), ymax) / ymax * PH;
    return S.view = { cfg, data, lines, t0, t1, span, step, gap, ymax, X, Y, ref: cfg.ref ? data[cfg.ref] : null };
}

function renderLoad() {
    const v = chartView(), { cfg, lines, t0, t1, span, gap, ymax, X, Y } = v;
    q("#chTabs").innerHTML = Object.keys(CH).map(k => `<button type="button" data-ch="${k}" aria-pressed="${S.chart === k}">${CH[k].label}</button>`).join("");
    q("#chNote").innerHTML = cfg.note || "";      /* ข้อความคงที่ของเราเอง (ไม่ใช่ข้อมูลจากเซิร์ฟเวอร์) */
    let s = "";
    /* แกน Y + เส้นตาราง */
    for (let i = 0; i <= 4; i++) {
        const val = ymax * i / 4, y = Y(val);
        s += `<line x1="${PL}" x2="${PL + PW}" y1="${y}" y2="${y}" stroke="var(--line)"/><text x="${PL - 10}" y="${y + 4}" text-anchor="end" font-size="12" fill="var(--mut)">${cfg.unit === "%" ? Math.round(val) + "%" : +val.toFixed(2)}</text>`;
    }
    /* แกน X */
    const n = 7;
    for (let i = 0; i < n; i++) {
        const d = new Date((t0 + span * i / (n - 1)) * 1000), x = PL + i / (n - 1) * PW;
        const t = span > 2 * 86400 ? d.toLocaleDateString("en-GB", { day: "2-digit", month: "short" }) : fmtDT(d);
        s += `<text x="${x}" y="${GH - 4}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}" font-size="12" fill="var(--mut)">${t}</text>`;
    }
    /* ช่วงไม่มีข้อมูล */
    const times = [...new Set(lines.flatMap(l => l.pts.map(p => p[0])))].sort((a, b) => a - b), holes = [];
    if (times.length) {
        if (times[0] - t0 > gap) holes.push([t0, times[0]]);
        for (let i = 1; i < times.length; i++) if (times[i] - times[i - 1] > gap) holes.push([times[i - 1], times[i]]);
        if (t1 - times[times.length - 1] > gap) holes.push([times[times.length - 1], t1]);
    }
    holes.forEach(([a, b]) => {
        const x1 = X(a), w = X(b) - x1;
        if (w < 2) return;
        s += `<rect x="${x1}" y="${PT}" width="${w}" height="${PH}" fill="var(--chip)" opacity=".55"/>`;
        if (w > 70) s += `<text x="${x1 + w / 2}" y="${PT + PH / 2 + 4}" text-anchor="middle" fill="var(--mut)" font-size="12">No data</text>`;
    });
    /* เส้นแนวตั้งจาง ๆ ตรงเวลาที่มี ERROR log */
    (S.markGroups || []).filter(g => g.level === "ERROR").forEach(g => {
        s += `<line x1="${g.x}" x2="${g.x}" y1="${PT}" y2="${PB}" stroke="#e5484d" stroke-opacity=".22" stroke-dasharray="3 3"/>`;
    });
    /* เส้นเกณฑ์ / เส้นจำนวน core */
    const hline = (val, color, label) => {
        if (!(val > 0) || val > ymax) return "";
        const y = Y(val);
        return `<line x1="${PL}" x2="${PL + PW}" y1="${y}" y2="${y}" stroke="${color}" stroke-width="1.2" stroke-dasharray="6 5" opacity=".85"/><text x="${PL + PW - 4}" y="${y - 5}" text-anchor="end" font-size="11" fill="${color}">${label}</text>`;
    };
    if (cfg.th && TH[cfg.th]) s += hline(TH[cfg.th].warn, "#d98a00", `warning ${TH[cfg.th].warn}%`) + hline(TH[cfg.th].crit, "#e5484d", `critical ${TH[cfg.th].crit}%`);
    if (v.ref) s += hline(v.ref, "#d98a00", `${v.ref} CPU cores`);
    /* ข้อมูล */
    const path = (pts, color, opt = {}) => splitGaps(pts, gap).map(g => {
        const p = g.map(x => [X(x[0]), Y(x[1])]);
        if (p.length === 1) return opt.dash ? "" : `<circle cx="${p[0][0]}" cy="${p[0][1]}" r="2.5" fill="${color}"/>`;
        const d = smooth(p);
        return (opt.area ? `<path d="${d} L${p[p.length - 1][0]},${PB} L${p[0][0]},${PB}Z" fill="${color}" fill-opacity=".12"/>` : "") +
            `<path d="${d}" fill="none" stroke="${color}" stroke-width="${opt.dash ? 1.5 : 2}"${opt.dash ? ` stroke-dasharray="5 4" stroke-opacity=".7"` : ""}/>`;
    }).join("");
    const has = times.length > 0;
    if (cfg.stacked) {
        lines.forEach(l => {
            splitGaps(l.stack, gap).forEach(g => {
                if (g.length < 2) return;
                const top = g.map(x => [X(x[0]), Y(x[1])]), bot = g.map(x => [X(x[0]), Y(x[2])]).reverse();
                s += `<path d="M${top.map(p => p.join(",")).join(" L")} L${bot.map(p => p.join(",")).join(" L")}Z" fill="${l.color}" fill-opacity=".35" stroke="${l.color}" stroke-width="1.2"/>`;
            });
        });
        if (S.cmp) s += path(lines.cmpTotal, "var(--mut)", { dash: true });
    } else {
        if (S.cmp) lines.forEach(l => { s += path(l.cpts, l.color, { dash: true }); });
        [...lines].reverse().forEach(l => { s += path(l.pts, l.color, { area: l.area }); });
    }
    if (!has) s += `<text x="${PL + PW / 2}" y="${PT + PH / 2}" text-anchor="middle" fill="var(--mut)" font-size="14">No history data for this range</text>`;
    /* แถบจุด log */
    s += `<line x1="${PL}" x2="${PL + PW}" y1="${MY}" y2="${MY}" stroke="var(--line)"/>`;
    q("#load").innerHTML = s;

    /* ชั้นบน: จุด log (คลิกได้) */
    q("#ovMarks").innerHTML = (S.markGroups || []).map((g, i) => {
        const c = g.level === "ERROR" ? "#e5484d" : "#f5a524", r = g.items.length > 1 ? 7 : 5;
        return `<g class="mk" data-g="${i}"><circle cx="${g.x}" cy="${MY}" r="${r}" fill="${c}" stroke="var(--card)" stroke-width="1.5"/>${g.items.length > 1 ? `<text x="${g.x}" y="${MY + 3.5}" text-anchor="middle" font-size="9" font-weight="700" fill="#fff">${g.items.length > 99 ? "99+" : g.items.length}</text>` : ""}</g>`;
    }).join("");

    /* คำอธิบาย */
    const lg = lines.map(l => `<span><i style="background:${l.color}"></i>${escapeHtml(l.name)}</span>`);
    if (S.cmp) lg.push(`<span><i class="dash"></i>Yesterday</span>`);
    if ((S.markGroups || []).length) lg.push(`<span><i style="background:#e5484d"></i>Error log</span><span><i style="background:#f5a524"></i>Warn log</span>`);
    q("#lgd").innerHTML = lg.join("");
    q("#zoomReset").hidden = !(S.zoomPrev && S.start);
    if (S.hoverX != null) drawHover(S.hoverX);
}

/* จัดกลุ่มจุด log ที่อยู่ใกล้กัน (ห่างกันไม่ถึง 12 หน่วย ไม่งั้นจุดจะทับกัน) -> S.markGroups */
function groupMarks() {
    const [s0, e0] = rng(), t0 = s0 / 1000, span = Math.max(e0 / 1000 - t0, 1);
    const pts = (S.marks || []).filter(m => m.ts >= t0 && m.ts <= t0 + span)
        .map(m => ({ m, x: PL + (m.ts - t0) / span * PW })).sort((a, b) => a.x - b.x);
    const groups = [];
    pts.forEach(({ m, x }) => {
        const g = groups[groups.length - 1];
        if (g && x - g.x1 < 12) { g.items.push(m); g.x1 = x; if (m.level === "ERROR") g.level = "ERROR"; }
        else groups.push({ x0: x, x1: x, items: [m], level: m.level === "ERROR" ? "ERROR" : "WARN" });
    });
    groups.forEach(g => { g.x = (g.x0 + g.x1) / 2; g.items.sort((a, b) => b.ts - a.ts); });
    S.markGroups = groups;
}

/* พิกัดเมาส์ -> หน่วย viewBox */
function svgX(e) {
    const r = q("#loadOv").getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width * GW, y: (e.clientY - r.top) / r.height * GH, r };
}

/* เส้นตั้งตามเมาส์ + กล่องค่า */
function drawHover(x, clientPos) {
    const v = S.view; if (!v) return;
    const tip = q("#chTip"), hov = q("#ovHover");
    if (x < PL || x > PL + PW) { hov.innerHTML = S.drag ? hov.innerHTML : ""; tip.hidden = true; return; }
    const t = v.t0 + (x - PL) / PW * v.span;
    let html = `<b>${escapeHtml(fmtDT(new Date(t * 1000), v.span > 86400 ? 1 : 0))}</b>`, any = false;
    v.lines.forEach(l => {
        const p = valueAt(l.pts, t, v.gap), c = S.cmp ? valueAt(l.cpts, t, v.gap) : null;
        if (p) any = true;
        html += `<div><i style="background:${l.color}"></i>${escapeHtml(l.name)}<span>${escapeHtml(p ? fmtVal(p[1], v.cfg.unit) : "no data")}${c ? ` <em>(${escapeHtml(fmtVal(c[1], v.cfg.unit))} yday)</em>` : ""}</span></div>`;
    });
    const near = (S.markGroups || []).filter(g => Math.abs(g.x - x) <= 8).flatMap(g => g.items);
    if (near.length) {
        const e = near.filter(m => m.level === "ERROR").length, w = near.length - e;
        html += `<div class="lg">${e ? `<i style="background:#e5484d"></i>${e} error` : ""}${e && w ? " · " : ""}${w ? `<i style="background:#f5a524"></i>${w} warn` : ""}</div><div class="msg">${escapeHtml(near[0].message.slice(0, 140))}</div>`;
    }
    const sel = S.drag ? `<rect x="${Math.min(S.drag.x0, x)}" y="${PT}" width="${Math.abs(x - S.drag.x0)}" height="${PH}" fill="var(--blue)" fill-opacity=".12" stroke="var(--blue)" stroke-opacity=".5"/>` : "";
    hov.innerHTML = sel + `<line x1="${x}" x2="${x}" y1="${PT}" y2="${PB}" stroke="var(--mut)" stroke-opacity=".6"/>` +
        v.lines.map(l => { const p = valueAt(v.cfg.stacked ? l.stack : l.pts, t, v.gap); return p ? `<circle cx="${v.X(p[0])}" cy="${v.Y(p[1])}" r="3.5" fill="${l.color}" stroke="var(--card)" stroke-width="1.5"/>` : ""; }).join("");
    if (!any && !near.length && !S.drag) { tip.hidden = true; return; }
    tip.innerHTML = html;
    tip.hidden = false;
    if (clientPos) S.tipPos = clientPos;
    const pos = S.tipPos; if (!pos) return;
    const w = tip.offsetWidth, box = q("#chw").clientWidth;
    let left = pos.left + 14; if (left + w > box) left = pos.left - w - 14;
    tip.style.left = Math.max(0, left) + "px";
    const h = tip.offsetHeight, bh = q("#chw").clientHeight;
    let top = pos.top - 10; if (top + h > bh) top = pos.top - h - 14;     // ใกล้ขอบล่าง (แถบจุด log) -> แสดงด้านบนแทน
    tip.style.top = Math.max(0, top) + "px";
}

/* ซูมไปช่วง [a, b] (unix วินาที) ทั้งหน้า */
function zoomTo(a, b) {
    if (b - a < 120) { const c = (a + b) / 2; a = c - 60; b = c + 60; }
    if (!S.zoomPrev || !S.start) S.zoomPrev = { dur: S.dur, start: S.start, end: S.end };   // ซูมครั้งแรกจากช่วงนี้ -> จำไว้ให้ Reset
    S.start = new Date(a * 1000); S.end = new Date(Math.min(b * 1000, Date.now()));
    setLbl(); loadHist();
}

function setupChart() {
    const ov = q("#loadOv");
    q("#chTabs").addEventListener("click", e => {
        const b = e.target.closest("button[data-ch]"); if (!b || b.dataset.ch === S.chart) return;
        S.chart = b.dataset.ch; S.ext = null; renderLoad(); loadHist();
    });
    /* ช่อง "vs yesterday" ถูกนำออกจากหน้าแล้ว (S.cmp = false เสมอ) */
    const cmpBox = q("#cmp");
    if (cmpBox) cmpBox.addEventListener("change", e => { S.cmp = e.target.checked; loadHist(); });
    q("#zoomReset").addEventListener("click", () => {
        const p = S.zoomPrev; if (!p) return;
        S.dur = p.dur; S.start = p.start; S.end = p.end; S.zoomPrev = null;
        setLbl(); loadHist();
    });
    const local = e => { const { x, r } = svgX(e), box = q("#chw").getBoundingClientRect(); return { x, pos: { left: e.clientX - box.left, top: e.clientY - box.top }, r }; };
    ov.addEventListener("pointermove", e => {
        const { x, pos } = local(e);
        S.hoverX = x; drawHover(x, pos);
    });
    ov.addEventListener("pointerleave", () => {
        if (S.drag) return;
        S.hoverX = null; q("#ovHover").innerHTML = ""; q("#chTip").hidden = true;
    });
    ov.addEventListener("pointerdown", e => {
        if (e.target.closest(".mk")) return;          // คลิกจุด log -> ไม่เริ่มลาก
        const { x } = local(e);
        if (x < PL || x > PL + PW) return;
        S.drag = { x0: x }; ov.setPointerCapture(e.pointerId);
    });
    ov.addEventListener("pointerup", e => {
        const mk = e.target.closest(".mk");
        if (!S.drag) {
            if (!mk) return;
            const g = (S.markGroups || [])[+mk.dataset.g]; if (!g) return;
            /* เปิด log ที่ร้ายแรงที่สุดและใหม่ที่สุดในกลุ่ม */
            const item = g.items.find(m => m.level === "ERROR") || g.items[0];
            openLogDetail(item);
            return;
        }
        const { x } = local(e), x0 = S.drag.x0; S.drag = null;
        if (Math.abs(x - x0) < 8) { drawHover(x); return; }
        const v = S.view, tt = px => v.t0 + (Math.min(Math.max(px, PL), PL + PW) - PL) / PW * v.span;
        zoomTo(tt(Math.min(x, x0)), tt(Math.max(x, x0)));
    });
}

/* สถานะ service -> คลาสสี
     "e" แดง  : failed / error / crit / down
     ""  เขียว : active / running / online / ok / up / healthy
     "i" เทา  : inactive / dead / exited (หยุดอยู่ตามปกติ ไม่ใช่ปัญหา)
     "w" เหลือง: อื่น ๆ เช่น not-found / masked / activating / not collected
   เทียบทั้งคำ (เดิมใช้ regex หาคำย่อย ทำให้ "inactive" ซึ่งมีคำว่า "active" อยู่ข้างใน ขึ้นสีเขียว) */
function svcClass(status) {
    const v = String(status || "").trim().toLowerCase();
    if (/^(failed|fail|error|crit|critical|down)$/.test(v)) return "e";
    if (/^(active|running|online|ok|up|healthy)$/.test(v)) return "";
    if (/^(inactive|dead|exited|stopped)$/.test(v)) return "i";
    return "w";
}
/* กลุ่มของ service สำหรับแท็บกรอง + ลำดับการเรียง (ปัญหาขึ้นก่อน) */
const SVC_ORDER = { e: 0, w: 1, "": 2, i: 3 };
const isNotFound = st => /^(not-found|masked|bad-setting)$/.test(String(st || "").toLowerCase());

/* แท่งเปอร์เซ็นต์ในตาราง process (v เทียบกับ max) พร้อมข้อความ */
function bar(v, max, text) {
    const w = Math.max(0, Math.min(100, Number(v) / max * 100)) || 0;
    return `<div class="bar"><u><i style="width:${w}%"></i></u><b style="width:auto;min-width:64px">${escapeHtml(text)}</b></div>`;
}

/* ข้อมูลวัดสดของเครื่องที่เลือก (ใช้กับการ์ด Memory) ไม่มี -> null */
function liveData() { return S.live && S.live.web === S.web ? S.live.data : null; }

/* การ์ด CPU process (#svc): process ที่กิน CPU สูงสุด 8 อันดับ จาก snapshot ของ puller (ps ตอนรอบดึง) */
function renderCores(procs) {
    /* เวลาของข้อมูล (snapshot รอบดึงล่าสุด) เก่าเกิน 2 รอบ -> สีแดง ⚠ (puller เข้าเครื่องไม่ได้) */
    const ts = (S.metrics || {}).system_timestamp;
    q("#cpuAsOf").innerHTML = ts ? (isStale(ts)
        ? `<span class="stale" title="puller could not reach this machine - see the Log pull sensor">data from ${escapeHtml(formatTimestamp(ts))} ⚠ outdated</span>`
        : `as of ${escapeHtml(formatTimestamp(ts))}`) : "";
    const l = [...(procs || [])].sort((a, b) => Number(b.cpu || 0) - Number(a.cpu || 0)).slice(0, 8);
    const mx = Math.max(100, ...l.map(p => Number(p.cpu || 0)));
    q("#svc").innerHTML = l.length
        ? l.map(p => `<tr><td>${escapeHtml(p.name || "-")}</td><td>${escapeHtml(p.pid ?? "-")}</td><td>${bar(p.cpu, mx, formatPercent(p.cpu))}</td></tr>`).join("")
        : `<tr><td colspan="3" class="empty">No process data collected</td></tr>`;
}

/* รายการจาก snapshot ของ puller: Service (#sv), process ที่กิน CPU (#svc), process ที่กิน Memory (#mem)
   เรียงมากไปน้อย 8 อันดับ, และ System events (#ev ซ่อนการ์ดเมื่อไม่มี) */
function renderLists() {
    const m = S.metrics || {}, svcs = Array.isArray(m.services) ? m.services : [], procs = Array.isArray(m.processes) ? m.processes : [];
    renderServices(svcs);
    renderCores(procs);
    renderMem(procs);
    const ev = Array.isArray(m.system_events) ? m.system_events.filter(e => hasValue(e.message) || hasValue(e.type)) : [];
    q("#evCard").hidden = !ev.length;
    setText("evCnt", ev.length ? `${ev.length} events` : "");
    q("#ev").innerHTML = ev.map(e => `<div class="row"><span>${escapeHtml(formatTimestamp(e.timestamp))}</span><span>${escapeHtml(e.type || "")}</span><span>${escapeHtml(e.message || "")}</span></div>`).join("");
}

/* การ์ด Memory Usage by Process: 10 process ที่ใช้ RAM สูงสุด (วัดสด; ไม่มี -> snapshot)
   แท่ง = % ของ RAM ทั้งเครื่อง, ข้อความ = % · MB */
function renderMem(snapProcs) {
    const ld = liveData();
    const list = ld && Array.isArray(ld.memory_top) && ld.memory_top.length
        ? ld.memory_top
        : [...(snapProcs || [])].sort((a, b) => Number(b.memory || 0) - Number(a.memory || 0)).slice(0, 10);
    setText("memCnt", list.length ? `top ${list.length}` : "");
    setText("memMeta", ld && ld.memory_top && ld.memory_top.length ? `Live sample at ${formatTimestamp(ld.time)} · % of total RAM`
        : list.length ? `From last pull ${formatTimestamp((S.metrics || {}).system_timestamp)}` : "");
    const mx = Math.max(10, ...list.map(p => Number(p.memory || 0)));
    q("#mem").innerHTML = list.length ? list.map(p => {
        const text = formatPercent(p.memory) + (hasValue(p.rss_mb) ? ` · ${Number(p.rss_mb) >= 1024 ? (p.rss_mb / 1024).toFixed(1) + " GB" : Math.round(p.rss_mb) + " MB"}` : "");
        return `<tr><td>${escapeHtml(p.name || "-")}</td><td>${escapeHtml(p.pid ?? "-")}</td><td>${bar(p.memory, mx, text)}</td></tr>`;
    }).join("") : `<tr><td colspan="3" class="empty">No process data collected</td></tr>`;
}

/* วัด top memory สดผ่าน puller (การ์ด Memory) ทุก LIVE_EVERY ms */
async function loadLive(force) {
    if (!S.web || S.liveBusy) return;
    if (!force && S.live && S.live.web === S.web && Date.now() - S.liveAt < LIVE_EVERY) return;
    const web = S.web;
    S.liveBusy = true;
    try {
        const r = await fetch(`/api/live-processes?web=${encodeURIComponent(web)}`, { cache: "no-store", credentials: "same-origin" });
        if (r.status === 401) { window.location.href = "/login"; return; }
        const d = await r.json().catch(() => ({}));
        if (web !== S.web) return;
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        S.live = { web, data: d }; S.liveErr = null;
    } catch (err) {
        console.error("Live processes error:", err);
        S.liveErr = err.message; S.live = null;
    } finally {
        S.liveAt = Date.now(); S.liveBusy = false;
        if (web === S.web) renderMem((S.metrics || {}).processes);
    }
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
    /* ซ่อน not-found/masked เป็นค่าเริ่มต้น (unit ที่ไม่มีอยู่จริงบนเครื่อง ส่วนใหญ่เป็นสิ่งรบกวน)
       ยกเว้นตัวที่ผู้ใช้เลือกไว้เองใน Select */
    const nf = watch.length ? [] : list.filter(x => isNotFound(x.status));
    if (!S.svnf && nf.length) list = list.filter(x => !isNotFound(x.status));
    /* เรียง: แดง -> เหลือง -> เขียว -> เทา แล้วตามชื่อ */
    list = [...list].sort((a, b) => (SVC_ORDER[svcClass(a.status)] - SVC_ORDER[svcClass(b.status)]) || String(a.name).localeCompare(String(b.name)));
    const cnt = { all: list.length, failed: 0, active: 0, inactive: 0 };
    list.forEach(x => { const c = svcClass(x.status); if (c === "e") cnt.failed++; else if (c === "") cnt.active++; else if (c === "i") cnt.inactive++; });
    const pass = x => { const c = svcClass(x.status); return S.svf === "all" || (S.svf === "failed" && c === "e") || (S.svf === "active" && c === "") || (S.svf === "inactive" && c === "i"); };
    const shown = list.filter(pass);

    const L = { all: "All", failed: "Failed", active: "Active", inactive: "Inactive" };
    q("#svTabs").innerHTML = Object.keys(L).map(k => `<button type="button" data-svf="${k}" aria-pressed="${S.svf === k}"${k === "failed" && cnt.failed ? ' class="bad"' : ""}>${L[k]}<i>${cnt[k]}</i></button>`).join("");
    const ts = (S.metrics || {}).system_timestamp;
    q("#svMeta").innerHTML = (ts ? `As of ${escapeHtml(formatTimestamp(ts))}` : "") +
        (nf.length ? `<button type="button" id="svNf">${S.svnf ? "Hide" : "Show"} not-found (${nf.length})</button>` : "");
    setText("svCnt", watch.length ? `${watch.length} of ${svcs.length}` : (svcs.length ? `All ${svcs.length}` : ""));
    q("#sv").innerHTML = shown.length
        ? shown.map(x => `<div class="row"><span class="st ${svcClass(x.status)}">${escapeHtml(x.name || "-")}</span><span>${escapeHtml(x.status || "-")}</span></div>`).join("")
        : `<div class="empty">${svcs.length ? "No service in this filter" : "No service data collected"}</div>`;
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
    q("#svTabs").addEventListener("click", e => {
        const b = e.target.closest("button[data-svf]"); if (!b) return;
        S.svf = b.dataset.svf; renderServices(Array.isArray((S.metrics || {}).services) ? S.metrics.services : []);
    });
    q("#svMeta").addEventListener("click", e => {
        if (!e.target.closest("#svNf")) return;
        S.svnf = !S.svnf; renderServices(Array.isArray((S.metrics || {}).services) ? S.metrics.services : []);
    });
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

/* ตาราง Logs: 1 แถวต่อ log, คลิกแถว -> openLogDetail
   โหมด INFO (S.info): แสดงแถวที่โหลดสดจากปุ่ม Load INFO log (ไม่ได้มาจาก OpenSearch) */
/* ตาราง Logs + Acknowledge
   ติ๊กเลือกแถว (operator ขึ้นไป) -> "Acknowledge selected" = รับทราบแล้ว: log ถูกซ่อนจากตาราง/ตัวนับ/จุดบนกราฟ
   (ยังอยู่ใน OpenSearch ตามอายุเดิม) ติ๊ก "Show acknowledged" เพื่อดูอีกครั้ง/ยกเลิก */
function canAckLogs() { return canOperate() && !S.info; }
function renderLogHead(selectable) {
    q("#plogHead").innerHTML = `<tr>${selectable ? `<th class="sel"><input type="checkbox" id="selAll" aria-label="Select all shown logs"></th>` : ""}<th>Time</th><th>WEB</th><th>Level</th><th>Type</th><th>Message</th><th>Metrics at time</th></tr>`;
}
function renderAckBar() {
    const can = canAckLogs();
    q("#ackBar").hidden = !can;
    q("#showAckedWrap").hidden = !!S.info;
    const sel = [...S.sel.values()], acked = sel.filter(p => p.ack).length;
    q("#ackSel").disabled = sel.length - acked === 0;
    q("#ackSel").textContent = sel.length - acked ? `Acknowledge selected (${sel.length - acked})` : "Acknowledge selected";
    q("#unackSel").hidden = !acked;
    q("#unackSel").textContent = `Unacknowledge (${acked})`;
    const all = q("#selAll");
    if (all) { all.checked = S.logRows.length > 0 && S.logRows.every(p => S.sel.has(p.id)); all.indeterminate = !all.checked && sel.length > 0; }
}

function renderProblems(list) {
    const tb = q("#plog");
    const filtered = S.lv !== "ALL" || S.lt !== "ALL";
    const selectable = canAckLogs();
    if (S.search && !S.showAcked && Array.isArray(list)) list = list.filter(p => !p.ack);
    S.logRows = Array.isArray(list) ? list.filter(p => p.id) : [];
    [...S.sel.keys()].forEach(id => { if (!S.logRows.some(p => p.id === id)) S.sel.delete(id); });
    renderLogHead(selectable);
    renderAckBar();
    if (!Array.isArray(list) || !list.length) {
        tb.innerHTML = `<tr><td colspan="${selectable ? 7 : 6}" class="empty">${S.search ? "No logs match this search" : S.info ? "No INFO lines in the loaded range" : filtered ? "No logs match this filter" : "No ERROR / WARN logs"}</td></tr>`;
        setText("logCnt", "");
        renderLogStat(0);
        return;
    }
    tb.innerHTML = "";
    setText("logCnt", S.search ? `${list.length} search results ·` : S.info ? `${list.length} INFO logs (live, not stored) ·` : `${list.length} logs ·`);
    renderLogStat(list.length);
    list.forEach(p => {
        const tr = document.createElement("tr");
        if (p.ack) tr.className = "acked";
        const ackTag = p.ack ? ` <span class="logack" title="${escapeHtml(`Acknowledged by ${p.ack.by || "-"} · ${p.ack.at ? new Date(p.ack.at * 1000).toLocaleString("en-GB") : ""}${p.ack.note ? " · " + p.ack.note : ""}`)}">ACK</span>` : "";
        const sel = selectable ? `<td class="sel">${p.id ? `<input type="checkbox" data-sel="${escapeHtml(p.id)}"${S.sel.has(p.id) ? " checked" : ""} aria-label="Select log">` : ""}</td>` : "";
        tr.innerHTML = `${sel}<td>${escapeHtml(formatTimestamp(p.timestamp))}</td><td>${escapeHtml(p.web || "-")}</td><td class="lv ${String(p.level || "").toLowerCase()}">${escapeHtml(p.level || "INFO")}${ackTag}</td><td>${escapeHtml(p.type || "application")}</td><td class="msg">${p.highlight ? hlHtml(p.highlight) : escapeHtml(p.message || "")}</td><td>${escapeHtml(buildProblemMetrics(p.correlated_metrics || {}) || "-")}</td>`;
        tr.addEventListener("click", e => {
            if (e.target.closest("td.sel")) {
                const cb = tr.querySelector("input[data-sel]"); if (!cb) return;
                if (e.target !== cb) cb.checked = !cb.checked;
                cb.checked ? S.sel.set(p.id, p) : S.sel.delete(p.id);
                renderAckBar();
                return;
            }
            openLogDetail(p);
        });
        tb.appendChild(tr);
    });
}

/* ---------- ตัวกรอง log (Level / Type) ----------
   รายการจริงถูกกรองที่ server (/api/problems?level=&type=) ด้วยตัวจัดระดับเดียวกับตัวนับ
   จึงรับประกันว่า "Error" ที่เห็นคือ log ที่ระบบตัดสินว่า ERROR ตรงกับตัวเลข Problem logs / Errors 15m */
/* ช่วงเวลาของตาราง Logs: เลือกวันเอง/ซูม = เฉพาะช่วงนั้น, ช่วงด่วน (Last 15 minutes ...) = log ล่าสุด 300 รายการเหมือนเดิม */
function logRange(p) {
    if (!S.start) return p;
    p.set("start", String(Math.floor(S.start / 1000)));
    if (S.end) p.set("end", String(Math.ceil(S.end / 1000)));
    return p;
}
function logKey() { return S.start ? `${+S.start}-${S.end ? +S.end : "now"}` : "latest"; }
function statsUrl() { return `/api/log-stats?${logRange(new URLSearchParams({ web: S.web }))}`; }

function logUrl() {
    const p = logRange(new URLSearchParams({ web: S.web, size: "300" }));
    if (S.withMetrics) p.set("with_metrics", "1");
    if (S.showAcked) p.set("acked", "1");
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
        const label = { ALL: "All", ERROR: "Error", WARN: "Warn" }[k];
        b.innerHTML = hasValue(n) ? `${label}<i>${n}</i>` : label;
        setText("ackedCnt", stats && stats.acknowledged ? `(${stats.acknowledged})` : "");
        b.setAttribute("aria-pressed", !S.info && k === S.lv);
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
/* ข้อความที่ OpenSearch ครอบส่วนที่ตรงคำค้นด้วย \x02...\x03 -> escape ก่อน แล้วค่อยเปลี่ยนเป็น <mark> (ปลอดภัยจาก HTML ใน log) */
function hlHtml(text) {
    return escapeHtml(text).replace(/\u0002/g, "<mark>").replace(/\u0003/g, "</mark>");
}

function renderLogStat(shown) {
    if (S.search) {
        const sr = S.search, scope = sr.scope === "all" ? "all WEB" : sr.web;
        const more = sr.truncated ? " · showing the newest matches only, narrow the search or time range to see more" : "";
        setText("lvStat", `Search "${sr.q}" in ${scope}${S.start ? " · selected time range" : ""} · ${sr.matched} match${sr.matched === 1 ? "" : "es"}${more}`);
        return;
    }
    if (S.info) {
        const err = (S.info.errors || []).length ? ` · ${S.info.errors.length} source error(s): ${S.info.errors.join("; ")}` : "";
        setText("lvStat", `INFO · last ${S.info.lines} lines per log${S.start ? " · in selected range" : ""}${err}`);
        return;
    }
    const parts = [];
    if (S.lv !== "ALL") parts.push(S.lv);
    if (S.lt !== "ALL") parts.push(S.lt);
    if (S.start) parts.push(`${fmtDT(S.start, 1)} → ${S.end ? fmtDT(S.end, 1) : "now"}`);
    setText("lvStat", parts.length ? `Showing ${shown} ${parts.join(" · ")}` : "");
}

/* เปลี่ยนตัวกรองแล้วโหลดเฉพาะรายการ log + จำนวน (ไม่โหลดทั้งหน้า) */
async function reloadLogs() {
    if (!S.web) return;
    try {
        const w = encodeURIComponent(S.web);
        S.logKey = logKey();
        const [problems, stats] = await Promise.all([apiGet(logUrl()), apiGet(statsUrl())]);
        renderLogFilter(stats);
        if (S.search) await runSearch(true);
        else if (!S.info) renderProblems(problems);
    } catch (err) { console.error("Log filter error:", err); }
}

/* ---------- ปุ่ม Load INFO log ----------
   ระบบไม่เก็บ INFO -> กดปุ่มแล้วดึงสดจากเครื่อง (N บรรทัดท้ายของแต่ละ log, ถ้าเลือกช่วงเวลาไว้กรองตามช่วงนั้น)
   ผลอยู่ในหน่วยความจำของหน้าเว็บเท่านั้น (S.info) กด Close INFO / เปลี่ยนเครื่อง / รีโหลดหน้า = หายไป */
async function loadInfo() {
    if (!S.web || S.infoBusy) return;
    const btn = q("#infoBtn"), web = S.web;
    S.infoBusy = true; btn.disabled = true; btn.textContent = "Loading INFO...";
    try {
        const p = logRange(new URLSearchParams({ web, lines: q("#infoLines").value }));
        const r = await fetch(`/api/info-logs?${p}`, { cache: "no-store", credentials: "same-origin" });
        if (r.status === 401) { window.location.href = "/login"; return; }
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        if (web !== S.web) return;                 /* เปลี่ยนเครื่องระหว่างรอ */
        S.info = { rows: d.entries || [], errors: d.errors || [], lines: d.lines || q("#infoLines").value };
        if (S.search) { S.search = null; q("#logClear").hidden = true; }
        showInfo();
    } catch (err) {
        console.error("INFO log error:", err);
        setText("lvStat", `Load INFO failed: ${err.message}`);
    } finally {
        S.infoBusy = false; btn.disabled = false; btn.textContent = S.info ? "Reload INFO" : "Load INFO log";
    }
}
function showInfo() {
    q("#infoClear").hidden = !S.info;
    q("#infoBtn").textContent = S.info ? "Reload INFO" : "Load INFO log";
    renderLogFilter(S.stats);
    if (S.info) renderProblems(S.info.rows);
}
function closeInfo() { S.info = null; showInfo(); reloadLogs(); }

/* Acknowledge / ยกเลิก log ที่ติ๊กไว้ */
async function ackLogs(on) {
    const rows = [...S.sel.values()].filter(p => on ? !p.ack : p.ack);
    if (!rows.length) return;
    let note = "";
    if (on) {
        note = prompt(`Acknowledge ${rows.length} log(s)?\nThey will be hidden from the table and counters (still kept in storage).\nNote (optional):`, "");
        if (note === null) return;
    }
    try {
        const r = await fetch(`/api/logs/${on ? "ack" : "unack"}`, {
            method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ids: rows.map(p => p.id), note, web: S.web }) });
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
        S.sel.clear();
        await reloadLogs();            /* ค้นอยู่ -> reloadLogs ค้นซ้ำให้ */
        loadHist();
    } catch (err) { alert("Acknowledge failed: " + err.message); }
}

/* ---------- ค้นหาข้อความใน log ----------
   ค้นทั้งหมดที่เก็บใน OpenSearch (ไม่ใช่แค่ 300 รายการล่าสุด) ด้วยตัวกรอง Level / Type / ช่วงเวลาเดียวกับตาราง
   ผลค้นค้างไว้จนกด Clear search (รีเฟรชอัตโนมัติไม่ทับ)  keep = ค้นซ้ำด้วยคำเดิม (เปลี่ยนตัวกรอง) */
async function runSearch(keep) {
    const qv = keep && S.search ? S.search.q : q("#logQ").value.trim();
    if (!qv) { clearSearch(); return; }
    if (S.searchBusy) return;
    const scope = keep && S.search ? S.search.scope : q("#logScope").value;
    const p = logRange(new URLSearchParams({ q: qv, size: "300" }));
    if (scope !== "all" && S.web) p.set("web", S.web);
    if (S.lv !== "ALL") p.set("level", S.lv);
    if (S.lt !== "ALL") p.set("type", S.lt);
    const btn = q("#logGo");
    S.searchBusy = true; btn.disabled = true; btn.textContent = "Searching...";
    try {
        const r = await fetch(`/api/logs/search?${p}`, { cache: "no-store", credentials: "same-origin" });
        if (r.status === 401) { window.location.href = "/login"; return; }
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
        S.search = { q: qv, scope, web: S.web, rows: d.results || [], total: d.total, matched: d.matched ?? (d.results || []).length, truncated: !!d.truncated };
        if (S.info) { S.info = null; showInfo(); }
        q("#logClear").hidden = false;
        renderProblems(S.search.rows);
    } catch (err) {
        console.error("Log search error:", err);
        setText("lvStat", `Search failed: ${err.message}`);
    } finally { S.searchBusy = false; btn.disabled = false; btn.textContent = "Search"; }
}
function clearSearch() {
    S.search = null; q("#logClear").hidden = true; q("#logQ").value = "";
    reloadLogs();
}

/* คลิกแถว log: เปิดหน้าต่างทันทีด้วยข้อมูลที่มี แล้วโหลดรายละเอียดเต็มมาเติม
   แถว INFO ที่โหลดสด (on_demand) ไม่มีใน OpenSearch -> แสดงเท่าที่มี */
async function openLogDetail(p) {
    showProblemModal(p);
    if (p.on_demand) return;
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
    const [s, e] = rng(), w = encodeURIComponent(S.web), a = Math.floor(s / 1000), b = Math.floor(e / 1000);
    const base = `/api/metrics/history?web=${w}&start=${a}&end=${b}`, main = S.chart === "main", cmp = S.cmp ? "&compare=1" : "";
    const r = await Promise.allSettled([
        apiGet(base + (main ? cmp : "")),
        main ? Promise.resolve(null) : apiGet(`${base}&set=${S.chart}${cmp}`),
        apiGet(`/api/log-markers?web=${w}&start=${a}&end=${b}`)
    ]);
    r.forEach(x => { if (x.status === "rejected") console.error("History error:", x.reason); });
    S.hist = r[0].status === "fulfilled" ? r[0].value : null;
    S.ext = main ? S.hist : (r[1].status === "fulfilled" ? r[1].value : null);
    S.marks = r[2].status === "fulfilled" && Array.isArray(r[2].value) ? r[2].value : [];
    groupMarks();
    renderKPIs();
    renderLoad();
    if (logKey() !== S.logKey) reloadLogs();      // เปลี่ยนช่วงเวลา (ซูม/เลือกวัน) -> ตาราง Logs ตามช่วงนั้น
}

/* โหลดทั้งหน้า: รายชื่อเครื่อง -> (summary, metrics, problems พร้อมกัน) -> กราฟ
   กันเรียกซ้อน (S.busy) ; ตั้งสถานะมุมขวาบนตามผล (DOWN / Problem / Warning / Healthy) */
async function loadAll() {
    if (S.busy) return;
    S.busy = true;
    try {
        setSystemStatus("Loading...", "yellow");
        const ov = await apiGet("/api/overview");
        renderMachines(ov);
        if (!S.web) { setSystemStatus("No WEB configured", "red"); return; }
        const w = encodeURIComponent(S.web);
        const [summary, metrics, problems, stats] = await Promise.all([
            apiGet(`/api/summary?web=${w}`),
            apiGet(`/api/metrics?web=${w}`),
            apiGet(logUrl()),
            apiGet(statsUrl())
        ]);
        S.logKey = logKey();
        await loadWatch();
        S.summary = summary;
        S.metrics = { ...(summary.metrics || {}), ...(metrics || {}) };
        renderMeta();
        renderLists();
        renderLogFilter(stats);
        if (S.search) renderProblems(S.search.rows); else if (S.info) renderProblems(S.info.rows); else renderProblems(problems);
        loadLive(false);
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
        S.role = d.role || "viewer";
        document.querySelectorAll(".st-nav[href='/settings']").forEach(a => { a.hidden = S.role !== "admin"; });
        const info = q(".infold"); if (info) info.hidden = !canOperate();
        if (S.ov) renderMachines();
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
    /* /?web=<ชื่อ> (ลิงก์จากหน้า Status & Logs) -> เลือกเครื่องนั้นตั้งแต่แรก */
    const qsWeb = new URLSearchParams(location.search).get("web");
    if (qsWeb) S.web = qsWeb;
    setupPicker();
    setupServicePicker();
    setupChart();
    setLbl();
    q("#mach").addEventListener("keydown", e => {
        if ((e.key === "Enter" || e.key === " ") && e.target.matches("[data-web],[data-group]")) { e.preventDefault(); e.target.click(); }
    });
    q("#mach").addEventListener("click", e => {
        if (e.target.closest("a")) return;
        const pb = e.target.closest("button[data-pause]");
        if (pb) { e.stopPropagation(); togglePause(pb); return; }
        const gh = e.target.closest("[data-group]");
        if (gh) {
            const path = gh.dataset.group;
            S.closed.has(path) ? S.closed.delete(path) : S.closed.add(path);
            saveClosedGroups(); renderMachines(); return;
        }
        const b = e.target.closest("[data-web]"); if (!b) return;
        if (b.dataset.web !== S.web) { togglePick(false); S.svf = "all"; S.svnf = false; S.ext = null; S.marks = []; S.markGroups = []; S.info = null; q("#infoClear").hidden = true; q("#infoBtn").textContent = "Load INFO log"; S.live = null; S.liveErr = null; if (S.search && S.search.scope !== "all") { S.search = null; q("#logClear").hidden = true; } }
        S.web = b.dataset.web; S.hist = null; S.lv = "ALL"; S.lt = "ALL"; loadAll();
    });
    q("#lvTabs").addEventListener("click", e => {
        const b = e.target.closest("button[data-lv]"); if (!b) return;
        S.lv = b.dataset.lv; S.info = null; showInfo(); reloadLogs();     /* ค้นอยู่ -> reloadLogs ค้นซ้ำด้วยตัวกรองใหม่ */
    });
    q("#ltSel").addEventListener("change", e => { S.lt = e.target.value; S.info = null; showInfo(); reloadLogs(); });
    q("#infoBtn").addEventListener("click", loadInfo);
    q("#logSearch").addEventListener("submit", e => { e.preventDefault(); runSearch(false); });
    q("#logClear").addEventListener("click", clearSearch);
    q("#withMetrics").checked = S.withMetrics;
    q("#showAcked").checked = S.showAcked;
    q("#showAcked").addEventListener("change", e => {
        S.showAcked = e.target.checked;
        try { localStorage.setItem("cm.logsShowAcked", S.showAcked ? "1" : "0"); } catch (err) { /* ignore */ }
        if (S.search) renderProblems(S.search.rows); else reloadLogs();
    });
    q("#plogHead").addEventListener("change", e => {
        if (e.target.id !== "selAll") return;
        S.logRows.forEach(p => e.target.checked ? S.sel.set(p.id, p) : S.sel.delete(p.id));
        q("#plog").querySelectorAll("input[data-sel]").forEach(cb => { cb.checked = e.target.checked; });
        renderAckBar();
    });
    q("#ackSel").addEventListener("click", () => ackLogs(true));
    q("#unackSel").addEventListener("click", () => ackLogs(false));
    q("#withMetrics").addEventListener("change", e => {
        S.withMetrics = e.target.checked;
        try { localStorage.setItem("cm.logsWithMetrics", S.withMetrics ? "1" : "0"); } catch (err) { /* ignore */ }
        reloadLogs();
    });
    q("#ovFind").addEventListener("input", e => { S.ovq = e.target.value; renderMachines(); });
    q("#ovExpand").addEventListener("click", () => { S.closed.clear(); saveClosedGroups(); renderMachines(); });
    q("#ovCollapse").addEventListener("click", () => {
        const all = [...(S.ov && S.ov.servers || []), ...(SHOW_CHECKS && S.ov && S.ov.checks || [])];
        all.forEach(x => { const parts = String(x.group || "").split("/").filter(Boolean); parts.forEach((_, i) => S.closed.add(parts.slice(0, i + 1).join("/"))); });
        saveClosedGroups(); renderMachines();
    });
    q("#infoClear").addEventListener("click", closeInfo);
    $("logoutButton").addEventListener("click", logout);
    loadCurrentUser();
    loadAll();
    setInterval(loadAll, 10000);
});
