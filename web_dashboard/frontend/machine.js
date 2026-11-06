/* =====================================================================
   machine.js - แท็บรายละเอียดเครื่อง (Machine details) ใน index.html
   =====================================================================
   แท็บ (#dTabs):
     Overview      KPI + กราฟ System Load            (dashboard.js เดิม)
     Performance   CPU / Memory / Disk ต่อไดรฟ์ / Network ต่อการ์ด / TCP / Ping   GET /api/host/<web>/performance
     Bottleneck    ผลตรวจคอขวดหลายค่าพร้อมกัน (sensor_monitor)                 GET /api/host/<web>/bottleneck
     Processes     process ที่ใช้ CPU / RAM / Disk I/O                          (dashboard.js + /api/live-processes)
     Services      รายการ service                                               (dashboard.js เดิม)
     Hardware      CPU รุ่น / RAM / ดิสก์ / การ์ดเครือข่าย / GPU / อุณหภูมิ       GET /api/host/<web>/hardware
     System        ชื่อเครื่อง / OS / build / uptime / เวลา / IP / agent          GET /api/host/<web>/system
     Alerts & Events  sensor ของเครื่อง + ประวัติปัญหา 7 วัน                      GET /api/sensors?web= , /api/incidents?web=
   ค่าที่ exporter ไม่ส่ง -> "Unavailable" ; สิ่งที่ระบบอ่านไม่ได้ -> "Unsupported" พร้อมเหตุผล (ไม่มีค่าสมมติ)
   ใช้ตัวแปร/ฟังก์ชันจาก dashboard.js: S, q, escapeHtml, hasValue, formatNumber, formatTimestamp, apiGet
   ===================================================================== */
(function () {
    "use strict";
    const PANES = ["overview", "performance", "bottleneck", "processes", "services", "hardware", "system", "alerts"];
    const API_KIND = { performance: "performance", bottleneck: "bottleneck", hardware: "hardware", system: "system" };
    const REFRESH_MS = { performance: 15000, bottleneck: 30000, hardware: 300000, system: 60000, alerts: 30000, processes: 20000 };
    const P = { tab: loadTab(), data: {}, at: {}, busy: {} };

    function loadTab() {
        try { const t = localStorage.getItem("cm.detailTab"); return PANES.includes(t) ? t : "overview"; } catch (e) { return "overview"; }
    }
    function saveTab(t) { try { localStorage.setItem("cm.detailTab", t); } catch (e) { /* ignore */ } }

    const esc = v => escapeHtml(v == null ? "" : String(v));
    const NA = (why) => `<span class="na" title="${esc(why || "exporter ไม่ได้ส่งค่านี้")}">Unavailable</span>`;
    const num = (v, d = 1, unit = "") => hasValue(v) && Number.isFinite(Number(v)) ? `${Number(v).toFixed(d)}${unit}` : NA();
    function bytes(v) {
        if (!hasValue(v)) return NA();
        let n = Number(v), i = 0;
        const u = ["B", "KB", "MB", "GB", "TB", "PB"];
        while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
        return `${n.toFixed(n >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
    }
    function dur(sec) {
        if (!hasValue(sec)) return "-";
        sec = Math.max(0, Math.round(Number(sec)));
        const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
        return d ? `${d}d ${h}h` : h ? `${h}h ${m}m` : m ? `${m}m` : `${sec}s`;
    }
    function ts(unix) { return hasValue(unix) ? formatTimestamp(new Date(Number(unix) * 1000).toISOString()) : "-"; }

    /* เกณฑ์ของเครื่องนี้ (จาก sensor_monitor ผ่าน /api/overview) */
    function th(kind) {
        const w = (S.webs || []).find(x => x.web === S.web) || {};
        return (w.thresholds || {})[kind] || null;
    }
    function lv(kind, v, low) {
        const t = th(kind);
        if (!t || !hasValue(v)) return "";
        v = Number(v);
        if (low) return v <= t.error ? "cr" : v <= t.warning ? "wr" : "ok";
        return v >= t.error ? "cr" : v >= t.warning ? "wr" : "ok";
    }
    const val = (html, cls) => `<b class="${cls || ""}">${html}</b>`;
    const kv = rows => `<dl class="kv">${rows.filter(Boolean).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v}</dd>`).join("")}</dl>`;
    const card = (title, body, extra) => `<section class="card pcard"><div class="top"><h2>${esc(title)}</h2>${extra || ""}</div>${body}</section>`;
    const table = (head, rows, empty) => rows.length
        ? `<div class="tblw"><table class="ptbl"><thead><tr>${head.map(h => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`
        : `<div class="empty">${esc(empty || "No data")}</div>`;
    const barCell = (v, cls) => hasValue(v)
        ? `<span class="pbar ${cls || ""}"><u><i style="width:${Math.max(0, Math.min(100, Number(v)))}%"></i></u><em>${Number(v).toFixed(1)}%</em></span>` : NA();

    /* ---------------- Performance ---------------- */
    function renderPerformance(d) {
        const c = d.cpu || {}, m = d.memory || {}, win = d.os === "windows";
        const cores = (c.per_core || []).map(x => `<div class="core ${lv("cpu", x.usage)}" title="core ${esc(x.core)}: ${num(x.usage)}"><i style="height:${Math.max(2, Math.min(100, Number(x.usage) || 0))}%"></i></div>`).join("");
        const cpu = card("CPU", kv([
            ["Usage", val(num(c.usage, 1, "%"), lv("cpu", c.usage))],
            ["User", num(c.user, 1, "%")],
            [win ? "Privileged (kernel)" : "System (kernel)", num(c.privileged, 1, "%")],
            win ? ["Interrupt + DPC", num(c.interrupt_dpc, 2, "%")] : ["I/O wait", num(c.iowait, 2, "%")],
            win ? ["Processor queue", hasValue(c.queue_length) ? `${c.queue_length}${c.cores ? ` (${(c.queue_length / c.cores).toFixed(2)} / core)` : ""}` : NA()]
                : ["Load 1m", hasValue(c.load1) ? `${c.load1}${c.cores ? ` (${(c.load1 / c.cores).toFixed(2)} / core)` : ""}` : NA()],
            ["Cores", num(c.cores, 0)],
            ["Context switches/s", num(c.context_switches_per_sec, 0)],
            ["Processes", num(c.processes, 0)],
            win ? ["Threads", num(c.threads, 0)] : null,
        ]) + (cores ? `<div class="cores" aria-label="CPU per core">${cores}</div>` : ""));
        const mem = card("Memory", kv([
            ["Used", val(`${num(m.used_pct, 1, "%")} · ${bytes(m.used_bytes)} / ${bytes(m.total_bytes)}`, lv("memory", m.used_pct))],
            ["Available", val(`${bytes(m.available_bytes)} (${num(m.available_pct, 1, "%")})`, win ? lv("mem_available", m.available_pct, true) : "")],
            ["Committed", hasValue(m.committed_bytes) ? `${bytes(m.committed_bytes)} / ${bytes(m.commit_limit_bytes)} (${num(m.committed_pct, 1, "%")})` : NA()],
            ["Cache", bytes(m.cache_bytes)],
            [win ? "Pages/sec" : "Swap pages/sec", num(m.pages_per_sec, 1)],
            win ? ["Pages input/sec", num(m.pages_input_per_sec, 1)] : ["Swap used", num(m.swap_used_pct, 1, "%")],
            [win ? "Page faults/sec" : "Major faults/sec", num(m.page_faults_per_sec, 1)],
            win ? ["Pool paged / nonpaged", hasValue(m.pool_paged_bytes) ? `${bytes(m.pool_paged_bytes)} / ${bytes(m.pool_nonpaged_bytes)}` : NA()] : null,
        ]));
        const disks = card(win ? "Disks (per drive)" : "Disks (per mount point)", table(
            ["Drive", "Used", "Free", "Read MB/s", "Write MB/s", "Read IOPS", "Write IOPS", "Latency R / W ms", "Queue", "Busy", "Status"],
            (d.disks || []).map(x => `<tr><td><b>${esc(x.volume)}</b>${x.label ? ` <small>${esc(x.label)}</small>` : ""}${x.device ? ` <small>${esc(x.device)}</small>` : ""}</td>
                <td>${barCell(x.used_pct, lv("disk", x.used_pct))}</td><td>${bytes(x.free_bytes)}</td>
                <td>${num(x.read_mbps, 2)}</td><td>${num(x.write_mbps, 2)}</td><td>${num(x.read_iops, 1)}</td><td>${num(x.write_iops, 1)}</td>
                <td class="${lv("disk_latency", Math.max(Number(x.read_latency_ms) || 0, Number(x.write_latency_ms) || 0))}">${num(x.read_latency_ms, 1)} / ${num(x.write_latency_ms, 1)}</td>
                <td>${num(x.queue, 1)}</td><td>${num(x.busy_pct, 1, "%")}</td><td>${x.status ? esc(x.status) : NA()}</td></tr>`),
            "No disk data"));
        const devs = (d.disk_devices || []).length ? card("Disk devices", table(
            ["Device", "Read IOPS", "Write IOPS", "Latency R / W ms", "Queue", "Busy"],
            d.disk_devices.map(x => `<tr><td><b>${esc(x.device)}</b></td><td>${num(x.read_iops, 1)}</td><td>${num(x.write_iops, 1)}</td>
                <td class="${lv("disk_latency", Math.max(Number(x.read_latency_ms) || 0, Number(x.write_latency_ms) || 0))}">${num(x.read_latency_ms, 1)} / ${num(x.write_latency_ms, 1)}</td>
                <td>${num(x.queue, 2)}</td><td>${num(x.busy_pct, 1, "%")}</td></tr>`))) : "";
        const net = card("Network (per adapter)", table(
            ["Adapter", "Status", "Receive Mbps", "Send Mbps", "Speed", "Bandwidth", "Errors/s", "Discards/s", "Out queue"],
            (d.network || []).map(x => `<tr><td><b>${esc(x.nic)}</b></td><td>${x.status ? esc(x.status) : NA()}</td>
                <td>${num(x.rx_mbps, 2)}</td><td>${num(x.tx_mbps, 2)}</td><td>${hasValue(x.speed_mbps) ? `${x.speed_mbps} Mbps` : NA("การ์ดไม่รายงานความเร็ว")}</td>
                <td>${barCell(x.bandwidth_pct, Number(x.bandwidth_pct) >= 80 ? "wr" : "")}</td>
                <td class="${Number(x.errors_per_sec) > 0 ? "wr" : ""}">${num(x.errors_per_sec, 2)}</td><td class="${Number(x.discards_per_sec) > 0 ? "wr" : ""}">${num(x.discards_per_sec, 2)}</td>
                <td>${num(x.output_queue, 0)}</td></tr>`), "No network data"));
        const t = d.tcp || {}, p = d.ping || {};
        const tcp = card("TCP", kv([
            ["Established", num(t.established, 0)], ["Connection failures/s", num(t.failures_per_sec, 3)],
            ["Resets/s", num(t.resets_per_sec, 3)], ["Retransmit", num(t.retransmit_pct, 2, "%")],
        ]));
        const ping = card("Ping from central", p.available ? kv([
            ["Latency (avg 5m)", val(num(p.latency_ms, 1, " ms"), lv("net_latency", p.latency_ms))],
            ["Packet loss (5m)", val(num(p.packet_loss, 1, "%"), lv("packet_loss", p.packet_loss))],
            ["Reachable now", p.reachable ? `<b class="ok">Yes</b>` : `<b class="cr">No</b>`],
            ["Samples (5m)", esc(p.samples_5m)],
        ]) : `<div class="empty">${esc(p.reason || "Unavailable")}</div>`);
        return `<div class="grid pgrid2">${cpu}${mem}</div>${disks}${devs}${net}<div class="grid pgrid2">${tcp}${ping}</div>
            <div class="pnote">ค่าจาก Prometheus (เฉลี่ย 5 นาที) · อัปเดต ${ts(d.generated_at)}</div>`;
    }

    /* ---------------- Bottleneck ---------------- */
    const BN_ORDER = { cpu: 0, memory: 1, disk: 2, network: 3 };
    const STATE_TXT = { bottleneck: ["Bottleneck", "cr"], trend: ["Trend", "wr"], normal: ["Normal", "ok"] };
    function renderBottleneck(d) {
        const rows = (d.bottlenecks || []).slice().sort((a, b) => (BN_ORDER[(a.details || {}).category] ?? 9) - (BN_ORDER[(b.details || {}).category] ?? 9));
        const o = d.options || {};
        const head = `<div class="pnote">ตรวจทุก 30 วินาที · แจ้งเตือนเมื่อเป็นต่อเนื่อง ${o.alert_after_seconds ? dur(o.alert_after_seconds) : "-"}
            · <b>Bottleneck</b> = เกินเกณฑ์พร้อมกันตั้งแต่ 2 ค่าขึ้นไป · <b>Trend</b> = เกินเพียงค่าเดียว (ยังไม่แจ้งเตือน)</div>`;
        if (!rows.length) return head + `<div class="empty">ยังไม่มีผล (รอ sensor รอบถัดไป ~30 วินาที) หรือเครื่อง Down / ปิดการตรวจ bottleneck ไว้ใน Settings</div>`;
        return head + `<div class="grid pgrid2">` + rows.map(s => {
            const det = s.details || {}, [stxt, scls] = STATE_TXT[det.state] || [det.state || "-", ""];
            const since = s.since ? `${dur(Date.now() / 1000 - s.since)}` : "-";
            const sig = (det.signals || []).map(x => `<tr class="${x.hit ? "hit" : ""}"><td>${esc(x.metric)}${x.note ? ` <small>${esc(x.note)}</small>` : ""}</td>
                <td class="${x.hit ? (x.level === "error" ? "cr" : "wr") : "ok"}"><b>${esc(x.value)}${esc(x.unit)}</b></td>
                <td>${x.low ? "≤" : "≥"} ${esc(x.threshold)}${esc(x.unit)}${hasValue(x.error_threshold) ? ` <small>(error ${esc(x.error_threshold)}${esc(x.unit)})</small>` : ""}</td>
                <td>${x.hit ? "เกิน" : "ปกติ"}</td></tr>`).join("");
            const pend = s.pending ? `<div class="pnote">กำลังยืนยัน: ${esc(s.pending.count)}/${esc(s.pending.need)} รอบ</div>` : "";
            return card(s.name, `<div class="bnhead"><span class="ovst ${scls}">${stxt}</span><span class="ovst ${s.status === "error" ? "cr" : s.status === "warning" ? "wr" : "ok"}">${esc((s.status || "").toUpperCase())}</span>
                <span class="bnmeta">duration ${since}</span></div>${pend}
                ${table(["Metric", "Value", "Threshold", ""], [sig], "")}
                ${det.advice && det.state !== "normal" ? `<div class="advice"><b>คำแนะนำ:</b> ${esc(det.advice)}</div>` : ""}`);
        }).join("") + `</div>`;
    }

    /* ---------------- Hardware ---------------- */
    function renderHardware(d) {
        const c = d.cpu || {};
        const cpu = card("CPU", kv([
            ["Model", (c.models || []).length ? c.models.map(x => esc(x.name || "-")).join("<br>") : NA()],
            ["Cores", num(c.cores, 0)], ["Logical processors", num(c.logical_processors, 0)],
            ["Frequency", hasValue(c.frequency_mhz) ? `${c.frequency_mhz} MHz` : NA()],
        ]));
        const mem = card("Memory", kv([["Installed", bytes((d.memory || {}).total_bytes)]]));
        const drives = card("Disk drives", table(["Device", "Model", "Size", "Status"],
            (d.disk_drives || []).map(x => `<tr><td>${esc(x.device)}</td><td>${x.model ? esc(x.model) : NA()}</td><td>${bytes(x.size_bytes)}</td>
                <td class="${x.status && x.status !== "OK" ? "cr" : ""}">${x.status ? esc(x.status) : NA()}</td></tr>`), "No disk drive data"));
        const vols = card("Volumes", table(["Volume", "Label", "File system", "Size"],
            (d.volumes || []).map(x => `<tr><td><b>${esc(x.volume)}</b>${x.device ? ` <small>${esc(x.device)}</small>` : ""}</td><td>${esc(x.label || "")}</td><td>${esc(x.filesystem || "-")}</td><td>${bytes(x.size_bytes)}</td></tr>`)));
        const nics = card("Network adapters", table(["Adapter", "Name", "MAC", "Speed", "Status"],
            (d.network_adapters || []).map(x => `<tr><td>${esc(x.nic)}</td><td>${esc(x.name || "")}</td><td><code>${esc(x.mac || "-")}</code></td>
                <td>${hasValue(x.speed_mbps) ? `${x.speed_mbps} Mbps` : NA()}</td><td>${x.status ? esc(x.status) : NA()}</td></tr>`)));
        const sens = [...(d.temperatures || []).map(x => `<tr><td>${esc(x.zone)}</td><td>Temperature</td><td>${num(x.temperature_c, 1, " °C")}</td></tr>`),
            ...(d.gpus || []).filter(x => hasValue(x.temperature_c)).map(x => `<tr><td>GPU ${esc(x.device)}</td><td>Temperature</td><td>${num(x.temperature_c, 1, " °C")}</td></tr>`),
            ...(d.gpus || []).filter(x => hasValue(x.fan_rpm)).map(x => `<tr><td>GPU ${esc(x.device)}</td><td>Fan</td><td>${num(x.fan_rpm, 0, " rpm")}</td></tr>`),
            ...(d.fans || []).map(x => `<tr><td>${esc(x.fan)}</td><td>Fan</td><td>${num(x.rpm, 0, " rpm")}</td></tr>`)];
        const sensors = card("Sensors", table(["Sensor", "Type", "Value"], sens, "ไม่มีเซนเซอร์ที่อ่านได้"));
        const uns = (d.unsupported || []).length ? card("Unsupported", `<ul class="unsup">${d.unsupported.map(x =>
            `<li><span class="na">Unsupported</span> <b>${esc(x.item)}</b> - ${esc(x.reason)}</li>`).join("")}</ul>`) : "";
        return `<div class="grid pgrid2">${cpu}${mem}</div>${drives}${vols}${nics}<div class="grid pgrid2">${sensors}${uns}</div>`;
    }

    /* ---------------- System ---------------- */
    function renderSystem(d) {
        const a = d.agent || {};
        const off = d.clock_offset_seconds, offCls = hasValue(off) ? (Math.abs(off) >= 60 ? "cr" : Math.abs(off) >= 5 ? "wr" : "ok") : "";
        const info = card("System", kv([
            ["Hostname", d.hostname ? esc(d.hostname) : NA()],
            ["Domain", d.domain ? esc(d.domain) : "-"],
            d.fqdn ? ["FQDN", esc(d.fqdn)] : null,
            ["OS", d.os_name ? esc(d.os_name) : NA()],
            ["Version", d.os_version ? esc(d.os_version) : "-"],
            ["Build / kernel", d.build ? esc(d.build) : NA()],
            d.architecture ? ["Architecture", esc(d.architecture)] : null,
            d.installation_type ? ["Installation type", esc(d.installation_type)] : null,
            ["Installed", d.install_time ? ts(d.install_time) : "-"],
            ["Last boot", d.boot_time ? ts(d.boot_time) : NA()],
            ["Uptime", hasValue(d.uptime_seconds) ? dur(d.uptime_seconds) : NA()],
            ["Time zone", d.timezone ? esc(d.timezone) : NA()],
            ["Clock offset", hasValue(off) ? val(`${off} s`, offCls) : NA("ไม่มีข้อมูลการซิงก์เวลา")],
            ["Time source", d.clock_source ? esc(d.clock_source) : "-"],
        ]));
        const ips = card("IP addresses", table(["Adapter", "Address", "Family"],
            (d.ip_addresses || []).map(x => `<tr><td>${esc(x.nic || "-")}</td><td><code>${esc(x.address)}</code></td><td>${esc(x.family || "")}</td></tr>`), "No address data"));
        const ag = card("Monitoring", kv([
            ["Type", d.type === "agent" ? "Windows agent (Fluent Bit)" : "SSH + node_exporter"],
            ["Group", esc(d.group || "-")], ["Configured host", esc(d.host || "-")],
            ["Metrics collector", esc(a.collector || "-")],
            a.fluent_bit ? ["Fluent Bit", esc(a.fluent_bit)] : null,
            (a.failed_collectors || []).length ? ["Failed collectors", `<b class="wr">${esc(a.failed_collectors.join(", "))}</b>`] : null,
        ]));
        return `<div class="grid pgrid2">${info}<div>${ag}${ips}</div></div>`;
    }

    /* ---------------- Alerts & Events ---------------- */
    const ST_CLS = { down: "cr", error: "cr", warning: "wr", unknown: "wr", ok: "ok", paused: "ps" };
    function renderAlerts(d) {
        const sensors = (d.sensors || []).filter(s => s.kind !== "bottleneck" || s.status !== "ok");
        const now = Date.now() / 1000;
        const sRows = sensors.map(s => `<tr><td><span class="ovst ${ST_CLS[s.status] || ""}">${esc((s.status || "").toUpperCase())}</span>${s.ack ? ` <span class="ovack" title="${esc(s.ack.note || "")}">ACK</span>` : ""}</td>
            <td><b>${esc(s.name)}</b></td><td>${hasValue(s.value) ? `${esc(s.value)}${esc(s.unit || "")}` : "-"}</td>
            <td>${hasValue(s.warning) ? `${esc(s.warning)} / ${esc(s.error)}${esc(s.unit || "")}` : "-"}</td>
            <td>${s.since ? dur(now - s.since) : "-"}</td><td class="msg">${esc(s.message || "")}</td></tr>`);
        const iRows = (d.incidents || []).map(i => `<tr><td><span class="ovst ${i.state === "ongoing" ? "cr" : "ok"}">${esc(i.state)}</span></td>
            <td><b>${esc(i.name)}</b></td><td><span class="ovst ${ST_CLS[i.worst] || ""}">${esc(i.worst)}</span></td>
            <td>${ts(i.start)}</td><td>${i.end ? ts(i.end) : "-"}</td><td>${dur(i.duration)}</td>
            <td>${hasValue(i.peak) ? `${Number(i.peak).toFixed(1)}${i.unit === "%" ? "%" : esc(i.unit || "")}` : "-"}</td></tr>`);
        return card("Sensors", table(["Status", "Sensor", "Value", "Warn / Error", "For", "Message"], sRows, "No sensors"),
            `<a class="linkbtn" href="/events?web=${encodeURIComponent(S.web)}">Open Events page</a>`) +
            card("Problem history (7 days)", table(["State", "Sensor", "Worst", "Start", "End", "Duration", "Peak"], iRows, "No problems in the last 7 days"));
    }

    /* ---------------- Processes (เพิ่มจากของเดิม) ---------------- */
    function renderProcesses(d) {
        const io = (d.io_top || []).filter(p => hasValue(p.io_mb));
        if (!io.length) return "";
        return card("Disk I/O by process", table(["Process", "PID", "Read + Write MB/s"],
            io.map(p => `<tr><td>${esc(p.name)}</td><td>${esc(p.pid || "-")}</td><td>${num(p.io_mb, 2)}</td></tr>`)));
    }

    /* ---------------- โหลด / วาด ---------------- */
    const RENDER = { performance: renderPerformance, bottleneck: renderBottleneck, hardware: renderHardware,
                     system: renderSystem, alerts: renderAlerts, processes: renderProcesses };
    const BOX = { performance: "#pnPerf", bottleneck: "#pnBn", hardware: "#pnHw", system: "#pnSys", alerts: "#pnAl", processes: "#pnProc" };

    async function fetchPane(tab) {
        const w = encodeURIComponent(S.web);
        if (API_KIND[tab]) return apiGet(`/api/host/${w}/${API_KIND[tab]}`);
        if (tab === "alerts") {
            const [s, i] = await Promise.all([apiGet(`/api/sensors?web=${w}`), apiGet(`/api/incidents?web=${w}&hours=168`)]);
            return { sensors: s.sensors || [], incidents: i.incidents || [] };
        }
        if (tab === "processes") return apiGet(`/api/live-processes?web=${w}`);
        return null;
    }

    async function load(tab, force) {
        if (!S.web || !RENDER[tab] || P.busy[tab]) return;
        const box = q(BOX[tab]);
        const key = `${S.web}|${tab}`;
        if (!force && P.at[key] && Date.now() - P.at[key] < REFRESH_MS[tab]) return;
        if (!P.data[key] && tab !== "processes") box.innerHTML = `<div class="empty">Loading...</div>`;
        P.busy[tab] = true;
        try {
            const web = S.web, d = await fetchPane(tab);
            if (web !== S.web) return;
            P.data[key] = d; P.at[key] = Date.now();
            box.innerHTML = RENDER[tab](d || {});
        } catch (err) {
            console.error(`Pane ${tab}:`, err);
            if (tab !== "processes") box.innerHTML = `<div class="empty">โหลดข้อมูลไม่ได้: ${esc(err.message || err)}</div>`;
        } finally {
            P.busy[tab] = false;
        }
    }

    function show(tab) {
        P.tab = PANES.includes(tab) ? tab : "overview";
        saveTab(P.tab);
        document.querySelectorAll("#dTabs [data-tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === P.tab)));
        document.querySelectorAll("#detail .dpane").forEach(p => { p.hidden = p.dataset.pane !== P.tab; });
        load(P.tab, false);
    }

    window.CMPanes = {
        refresh(force) { if (S.web) load(P.tab, force); },
        reset() { P.data = {}; P.at = {}; ["#pnPerf", "#pnBn", "#pnHw", "#pnSys", "#pnAl", "#pnProc"].forEach(id => { const b = q(id); if (b) b.innerHTML = ""; }); },
        show,
    };

    document.addEventListener("DOMContentLoaded", () => {
        const tabs = q("#dTabs");
        if (!tabs) return;
        tabs.addEventListener("click", e => { const b = e.target.closest("[data-tab]"); if (b) show(b.dataset.tab); });
        tabs.addEventListener("keydown", e => {
            if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
            const i = PANES.indexOf(P.tab), n = PANES[(i + (e.key === "ArrowRight" ? 1 : PANES.length - 1)) % PANES.length];
            show(n); const b = tabs.querySelector(`[data-tab="${n}"]`); if (b) b.focus();
        });
        show(P.tab);
    });
})();
