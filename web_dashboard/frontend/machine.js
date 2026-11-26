/* =====================================================================
   machine.js - แท็บรายละเอียดเครื่อง (Machine details) ใน index.html  ใช้กับทุกเครื่อง (Linux + Windows)
   =====================================================================
   แท็บ (#dTabs):
     Overview         KPI + กราฟ System Load + Service                    (dashboard.js)
     Performance      CPU / Memory / Disk ต่อไดรฟ์ / Network ต่อการ์ด / TCP / Ping   GET /api/host/<web>/performance
     Bottleneck       ผลตรวจคอขวดหลายค่าพร้อมกัน (sensor_monitor)                 GET /api/host/<web>/bottleneck
     Processes        process ที่ใช้ CPU / RAM / Disk I/O                          (dashboard.js + /api/live-processes)
     Services         (Windows) service ทั้งหมด + exe / บัญชีที่รัน / PID             GET /api/host/<web>/win-services
     Task Scheduler   (Windows) task ทั้งหมด + คำสั่งที่รัน / ผลรอบล่าสุด / รอบถัดไป   GET /api/host/<web>/win-tasks
     Programs         (Windows) exe ที่รันอยู่ทั้งหมด + path / ผู้ผลิต / service ในนั้น  GET /api/host/<web>/win-programs
                      (3 แท็บนี้แสดงเฉพาะเครื่อง Windows ดึงสดตอนเปิดแท็บ ไม่เก็บลงดิสก์)
     Hardware         CPU รุ่น / RAM / ดิสก์ / การ์ดเครือข่าย / อุณหภูมิ             GET /api/host/<web>/hardware
     System           ชื่อเครื่อง / OS / build / uptime / เวลา / IP / ตัวเก็บข้อมูล + System events   GET /api/host/<web>/system
     Alerts & Events  sensor ของเครื่อง + ประวัติปัญหา 7 วัน                      GET /api/sensors?web= , /api/incidents?web=
   Linux = node_exporter · Windows = WinRM (puller แปลงเป็นชื่อ metric แบบ node_exporter) จึงใช้ตัววาดชุดเดียวกัน
   ค่าที่อ่านไม่ได้ไม่แสดง (ซ่อนแถว / คอลัมน์ / การ์ดที่ไม่มีค่า) ไม่มีค่าสมมติ
   ใช้ตัวแปร/ฟังก์ชันจาก dashboard.js: S, q, escapeHtml, hasValue, formatNumber, formatTimestamp, apiGet
   ===================================================================== */
(function () {
    "use strict";
    const PANES = ["overview", "performance", "bottleneck", "processes", "winsvc", "wintask", "winproc", "hardware", "system", "alerts"];
    const WIN_TABS = ["winsvc", "wintask", "winproc"];         // เฉพาะเครื่อง Windows
    const API_KIND = { performance: "performance", bottleneck: "bottleneck", hardware: "hardware", system: "system",
                       winsvc: "win-services", wintask: "win-tasks", winproc: "win-programs" };
    const REFRESH_MS = { performance: 15000, bottleneck: 30000, hardware: 300000, system: 60000, alerts: 30000, processes: 20000,
                         winsvc: 60000, wintask: 120000, winproc: 30000 };
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
        const ping = !p.available ? "" : card("Ping from central", kv([
            ["Latency (avg 5m)", val(num(p.latency_ms, 1, " ms"), lv("net_latency", p.latency_ms))],
            ["Packet loss (5m)", val(num(p.packet_loss, 1, "%"), lv("packet_loss", p.packet_loss))],
            ["Reachable now", p.reachable ? `<b class="ok">Yes</b>` : `<b class="cr">No</b>`],
            ["Samples (5m)", esc(p.samples_5m)],
        ]));
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
        const sy = d.system || {}, c = d.cpu || {}, m = d.memory || {};
        const t = v => v ? esc(v) : NA();
        const system = card("System", kv([
            ["Manufacturer", t(sy.manufacturer)], ["Model", t(sy.model)], ["Family", t(sy.family)],
            ["Serial number", sy.serial ? `<code>${esc(sy.serial)}</code>` : NA()],
            ["UUID", sy.uuid ? `<code>${esc(sy.uuid)}</code>` : NA()],
            ["Motherboard", t(sy.board)], ["BIOS", t(sy.bios)], ["BIOS date", t(sy.bios_date)],
        ]));
        const models = [...new Set((c.models || []).map(x => x.name).filter(Boolean))];
        const cache = (c.models || [])[0] || {};
        const cpu = card("Processor & memory", kv([
            ["Processor", models.length ? models.map(esc).join("<br>") : NA()],
            ["Sockets", num(c.sockets, 0)], ["Cores", num(c.cores, 0)], ["Logical processors", num(c.logical_processors, 0)],
            ["Clock", hasValue(c.frequency_mhz) ? `${(c.frequency_mhz / 1000).toFixed(2)} GHz` : NA()],
            ["L2 / L3 cache", cache.l2_bytes || cache.l3_bytes ? `${bytes(cache.l2_bytes)} / ${bytes(cache.l3_bytes)}` : NA()],
            ["Installed memory", bytes(m.total_bytes)],
            ["Page file", hasValue(m.pagefile_bytes) ? `${bytes(m.pagefile_bytes)}${hasValue(m.pagefile_used_bytes) ? ` (used ${bytes(m.pagefile_used_bytes)})` : ""}` : NA()],
        ]));
        const drives = card("Physical disks", table(["Disk", "Model", "Size", "Partitions", "Status"],
            (d.disk_drives || []).map(x => `<tr><td><b>${esc(x.device)}</b></td><td>${x.model ? esc(x.model) : NA()}</td><td>${bytes(x.size_bytes)}</td>
                <td>${num(x.partitions, 0)}</td><td class="${x.status && x.status !== "OK" ? "cr" : x.status ? "ok" : ""}">${x.status ? esc(x.status) : NA()}</td></tr>`)));
        const vols = card("Volumes", table(["Drive", "Label", "File system", "Size", "Used", "Free"],
            (d.volumes || []).map(x => {
                const used = x.size_bytes && hasValue(x.free_bytes) ? (1 - x.free_bytes / x.size_bytes) * 100 : null;
                return `<tr><td><b>${esc(x.volume)}</b>${x.device ? ` <small>${esc(x.device)}</small>` : ""}</td><td>${x.label ? esc(x.label) : NA()}</td>
                    <td>${x.filesystem ? esc(x.filesystem) : NA()}</td><td>${bytes(x.size_bytes)}</td>
                    <td>${barCell(used, lv("disk", used))}</td><td>${bytes(x.free_bytes)}</td></tr>`;
            })));
        const nics = card("Network adapters", table(["Name", "Adapter", "MAC", "Speed", "Status"],
            (d.network_adapters || []).map(x => `<tr><td><b>${x.name ? esc(x.name) : esc(x.nic)}</b></td><td>${x.name ? esc(x.nic) : NA()}</td>
                <td>${x.mac ? `<code>${esc(x.mac)}</code>` : NA()}</td><td>${hasValue(x.speed_mbps) ? (x.speed_mbps >= 1000 ? `${x.speed_mbps / 1000} Gbps` : `${x.speed_mbps} Mbps`) : NA()}</td>
                <td class="${x.status === "up" ? "ok" : x.status ? "wr" : ""}">${x.status ? esc(x.status) : NA()}</td></tr>`)));
        const sensRows = [...(d.sensors || []).map(x => [x.sensor, x.type, x.value, x.unit]),
            ...(d.temperatures || []).map(x => [x.zone, "Temperature", x.temperature_c, "°C"]),
            ...(d.fans || []).map(x => [x.fan, "Fan", x.rpm, "rpm"])]
            .map(([a, b, v, u]) => `<tr><td>${esc(a)}</td><td>${esc(b)}</td><td>${num(v, u === "rpm" ? 0 : 1, " " + u)}</td></tr>`);
        const sensors = sensRows.length ? card("Sensors", table(["Sensor", "Type", "Value"], sensRows)) : "";
        return `<div class="grid pgrid2">${system}${cpu}</div>${drives}${vols}${nics}${sensors}`;
    }

    /* ---------------- System ---------------- */
    function renderSystem(d) {
        const a = d.agent || {}, t = v => v ? esc(v) : NA();
        const off = d.clock_offset_seconds, offCls = hasValue(off) ? (Math.abs(off) >= 60 ? "cr" : Math.abs(off) >= 5 ? "wr" : "ok") : "";
        const head = `<section class="card syshead"><div><small>Hostname</small><b>${esc(d.hostname || S.web)}</b>${d.fqdn && d.fqdn !== d.hostname ? `<em>${esc(d.fqdn)}</em>` : ""}</div>
            <div><small>Operating system</small><b>${t(d.os_name)}</b>${d.build ? `<em>build ${esc(d.build)}</em>` : ""}</div>
            <div><small>Uptime</small><b>${hasValue(d.uptime_seconds) ? dur(d.uptime_seconds) : NA()}</b>${d.boot_time ? `<em>since ${ts(d.boot_time)}</em>` : ""}</div></section>`;
        const os = card("Operating system", kv([
            ["Edition", t(d.os_name)], ["Version", t(d.os_version)], ["Build", t(d.build)],
            ["Installation type", t(d.installation_type)], ["Architecture", t(d.architecture)],
            ["Installed on", d.install_time ? ts(d.install_time) : NA()],
            ["Activation", d.license ? val(esc(d.license), /licensed/i.test(d.license) ? "ok" : "wr") : NA()],
            ["Pending updates", hasValue(d.updates_pending) ? val(String(d.updates_pending), d.updates_pending ? "wr" : "ok") : NA()],
            ["Last update installed", d.updates_last_install ? ts(d.updates_last_install) : NA()],
        ]));
        const time = card("Time", kv([
            ["Last boot", d.boot_time ? ts(d.boot_time) : NA()],
            ["Time zone", t(d.timezone)],
            ["Clock offset", hasValue(off) ? val(`${off} s`, offCls) : NA()],
            ["Time source", t(d.clock_source)],
            ["Last time sync", d.last_time_sync ? ts(d.last_time_sync) : NA()],
        ]));
        const ident = card("Identity", kv([
            ["Hostname", t(d.hostname)], ["Domain", t(d.domain)], ["FQDN", t(d.fqdn)],
            ["Group", t(d.group)], ["Configured host", d.host ? `<code>${esc(d.host)}</code>` : NA()],
        ]));
        const act = card("Activity", kv([
            ["Logged-on users", num(d.users, 0)], ["Processes", num(d.processes, 0)], ["Threads", num(d.threads, 0)],
        ]));
        const ips = card("IP addresses", table(["Adapter", "Address", "Family", "MAC"],
            (d.ip_addresses || []).map(x => `<tr><td>${x.nic ? esc(x.nic) : NA()}</td><td><code>${esc(x.address)}</code></td><td>${x.family ? esc(x.family) : NA()}</td><td>${x.mac ? `<code>${esc(x.mac)}</code>` : NA()}</td></tr>`)));
        const ag = card("Monitoring", kv([
            ["Type", d.type === "winrm" ? "Windows · WinRM (ไม่ลง agent, JEA อ่านอย่างเดียว)" : "Linux · SSH + node_exporter"],
            ["Metrics collector", t(a.collector)], ["PowerShell", t(a.powershell)],
        ]));
        const hf = card("Windows updates (ล่าสุด)", table(["Update", "Installed"],
            (d.hotfixes || []).map(x => `<tr><td><b>${esc(x.id)}</b></td><td>${esc(x.date || "-")}</td></tr>`)));
        return `${head}<div class="grid pgrid2">${os}${time}</div><div class="grid pgrid2">${ident}${act}</div>${ips}${(d.hotfixes || []).length ? hf : ""}${ag}`;
    }

    /* ---------------- Alerts & Events ---------------- */
    const ST_CLS = { down: "cr", error: "cr", warning: "wr", unknown: "wr", ok: "ok", paused: "ps" };
    function renderAlerts(d) {
        const sensors = (d.sensors || []).filter(s => s.kind !== "bottleneck" || s.status !== "ok");
        const now = Date.now() / 1000;
        const sRows = sensors.map(s => `<tr><td><span class="ovst ${ST_CLS[s.status] || ""}">${esc((s.status || "").toUpperCase())}</span>${s.ack ? ` <span class="ovack" title="${esc(s.ack.note || "")}">ACK</span>` : ""}</td>
            <td><a class="dsnl" href="/sensor?id=${encodeURIComponent(s.id)}"><b>${esc(s.name)}</b></a></td><td>${hasValue(s.value) ? `${esc(s.value)}${esc(s.unit || "")}` : "-"}</td>
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

    /* ---------------- ซ่อนค่าที่อ่านไม่ได้ ----------------
       แถว (dl.kv) ที่ไม่มีค่า -> ลบ · คอลัมน์ตารางที่ไม่มีค่าทุกแถว -> ลบ · ช่องที่เหลือ -> "-"
       การ์ดที่ไม่เหลือข้อมูล -> ลบ */
    function blank(node) {
        if (node.querySelector && node.querySelector("input,button,svg")) return false;
        const c = node.cloneNode(true);
        c.querySelectorAll(".na").forEach(n => n.remove());
        return !/[0-9A-Za-z\u0E00-\u0E7F]/.test(c.textContent);
    }
    function prune(box) {
        box.querySelectorAll("dl.kv dd").forEach(dd => {
            if (dd.querySelector(".na") && blank(dd)) { const dt = dd.previousElementSibling; if (dt) dt.remove(); dd.remove(); }
        });
        box.querySelectorAll("table.ptbl").forEach(t => {
            const rows = [...t.tBodies[0].rows], n = t.tHead.rows[0].cells.length;
            for (let i = n - 1; i >= 1; i--) {
                if (rows.length && rows.every(r => !r.cells[i] || blank(r.cells[i]))) {
                    t.tHead.rows[0].deleteCell(i); rows.forEach(r => r.cells[i] && r.deleteCell(i));
                }
            }
        });
        box.querySelectorAll(".na").forEach(n => n.replaceWith("-"));
        box.querySelectorAll(".pcard").forEach(c => {
            const hasKv = c.querySelector("dl.kv dt"), hasRows = c.querySelector("table.ptbl tbody tr"), extra = c.querySelector(".cores,.advice,.bnhead");
            if (!hasKv && !hasRows && !extra) c.remove();
        });
        box.querySelectorAll(".grid.pgrid2").forEach(g => {
            const n = g.querySelectorAll(":scope > .card").length;
            if (!n) g.remove(); else if (n === 1) g.classList.remove("pgrid2");
        });
        box.querySelectorAll(".syshead > div").forEach(x => { const b = x.querySelector("b"); if (b && b.textContent.trim() === "-") x.remove(); });
    }

    /* ---------------- Windows: Services / Task Scheduler / Programs (ดึงสดเมื่อเปิดแท็บ ไม่เก็บ) ---------------- */
    const WF = { winsvc: { q: "", k: "all" }, wintask: { q: "", k: "all", ms: false }, winproc: { q: "", k: "all" } };
    const curWeb = () => (S.webs || []).find(x => x.web === S.web);
    const mono = v => v ? `<code class="wpath">${esc(v)}</code>` : "-";
    const n0 = (v, d, u) => hasValue(v) && Number.isFinite(Number(v)) ? `${Number(v).toFixed(d)}${u || ""}` : "-";
    async function winGet(url) {
        const r = await fetch(url, { cache: "no-store", credentials: "same-origin" });
        if (r.status === 401) { window.location.href = "/login"; throw new Error("HTTP 401"); }
        let d = null;
        try { d = await r.json(); } catch (e) { /* not json */ }
        if (r.status === 403) throw new Error("ต้องเป็น operator ขึ้นไป (คำสั่งของ task อาจมีข้อมูลลับ)");
        if (!r.ok) throw new Error((d && d.error) || `HTTP ${r.status}`);
        return d;
    }
    function wtool(tab, kinds, extra) {
        const f = WF[tab];
        return `<div class="wtool" data-wt="${tab}"><input type="search" data-wq placeholder="ค้นหา ชื่อ / exe / บัญชี / คำอธิบาย" value="${esc(f.q)}" aria-label="ค้นหา">` +
            `<select data-wk aria-label="ตัวกรอง">${kinds.map(([v, t]) => `<option value="${v}"${f.k === v ? " selected" : ""}>${esc(t)}</option>`).join("")}</select>` +
            `${extra || ""}<span class="wcnt" data-wc></span></div>`;
    }
    function wfilter(tab) {
        const box = q(BOX[tab]);
        if (!box) return;
        const f = WF[tab], words = f.q.toLowerCase().split(/\s+/).filter(Boolean);
        let n = 0, all = 0;
        box.querySelectorAll("tr[data-s]").forEach(tr => {
            all++;
            const ok = (f.k === "all" || ` ${tr.dataset.k} `.includes(` ${f.k} `)) && words.every(w => tr.dataset.s.includes(w));
            tr.hidden = !ok;
            if (ok) n++;
        });
        const c = box.querySelector("[data-wc]");
        if (c) c.textContent = `แสดง ${n} จาก ${all}`;
    }
    const asof = d => `<small class="wasof">ดึงสดจากเครื่อง ${esc(d.time ? formatTimestamp(d.time) : "")}</small>`;
    const helperNote = d => d.native ? "" : `<div class="wnote">เครื่องนี้ยังไม่มีตัวช่วยของ module 1.1.4 (บางค่าไม่แสดง) · อัปเดตได้ที่ Settings → แก้ไขเครื่อง → อัปเดตบนเครื่อง</div>`;

    function renderWinServices(d) {
        const list = d.services || [];
        const rows = list.map(s => {
            const run = s.status === "Running", auto = s.start === "Automatic";
            const k = [run ? "run" : "stop", auto && !run ? "bad" : "", String(s.start || "").toLowerCase(), s.watched ? "watch" : ""].join(" ");
            const search = [s.name, s.display, s.path, s.account, s.desc, s.pid].join(" ").toLowerCase();
            return `<tr data-s="${esc(search)}" data-k="${k}"><td><b>${esc(s.display || s.name)}</b>${s.watched ? ' <span class="wtag">เฝ้าดู</span>' : ""}` +
                `<br><small>${esc(s.name)}</small>${s.desc ? `<div class="wdesc">${esc(s.desc)}</div>` : ""}</td>` +
                `<td class="${run ? "ok" : auto ? "cr" : ""}">${esc(s.status || "-")}</td><td>${esc(s.start || "-")}${s.delayed ? " <small>(Delayed)</small>" : ""}</td>` +
                `<td>${esc(s.pid || "-")}</td><td>${esc(s.account || "-")}</td><td>${mono(s.path)}</td></tr>`;
        });
        const bad = list.filter(s => s.start === "Automatic" && s.status !== "Running").length;
        return card(`Services (${list.length})`,
            wtool("winsvc", [["all", "ทั้งหมด"], ["run", "Running"], ["stop", "Stopped"], ["bad", `Automatic แต่หยุดอยู่ (${bad})`],
                             ["automatic", "Start: Automatic"], ["manual", "Start: Manual"], ["disabled", "Start: Disabled"], ["watch", "ที่เฝ้าดู"]]) +
            helperNote(d) + table(["Service", "Status", "Start type", "PID", "Run as", "Executable (path + arguments)"], rows, "ไม่มี service"), asof(d));
    }

    function renderWinTasks(d) {
        const list = d.tasks || [];
        const rows = list.map(t => {
            const acts = (t.actions || []).map(a => a.exe ? `${a.exe}${a.args ? " " + a.args : ""}` : a.com ? `COM handler ${a.com}` : `action type ${a.other}`);
            const st = t.enabled ? (t.state || "-") : "Disabled";
            const k = [t.enabled ? "on" : "off", t.state === "Running" ? "running" : "", t.result_level === "bad" ? "bad" : "", t.ms ? "ms" : "own"].join(" ");
            const search = [t.path, t.user, t.author, t.desc, acts.join(" ")].join(" ").toLowerCase();
            const rc = { ok: "ok", bad: "cr" }[t.result_level] || "";
            return `<tr data-s="${esc(search)}" data-k="${k}"><td><b>${esc(t.name)}</b><br><small>${esc(t.folder || "\\")}${t.author ? " · " + esc(t.author) : ""}</small>` +
                `${t.desc ? `<div class="wdesc">${esc(t.desc)}</div>` : ""}</td>` +
                `<td class="${t.state === "Running" ? "ok" : !t.enabled ? "wr" : ""}">${esc(st)}</td>` +
                `<td>${t.last_run ? esc(ts(t.last_run)) : "-"}</td><td class="${rc}" title="${esc(t.last_result || "")}">${esc(t.result_text || "-")}</td>` +
                `<td>${t.next_run ? esc(ts(t.next_run)) : "-"}</td><td>${esc((t.triggers || []).join(", ") || "-")}</td>` +
                `<td>${esc(t.user || "-")}${t.highest ? ' <span class="wtag">Highest</span>' : ""}</td><td>${acts.map(mono).join("<br>") || "-"}</td></tr>`;
        });
        const bad = list.filter(t => t.result_level === "bad").length;
        const notes = [];
        if (d.denied) notes.push(`มี ${d.denied} โฟลเดอร์ที่ cm-monitor ไม่มีสิทธิ์อ่าน (task ที่ตั้งสิทธิ์ให้เฉพาะ admin จะไม่แสดง)`);
        if (d.truncated) notes.push("แสดงสูงสุด 1,500 task");
        const ms = `<label class="wms"><input type="checkbox" data-wms${WF.wintask.ms ? " checked" : ""}> รวม task ของ Microsoft</label>`;
        return card(`Task Scheduler (${list.length})`,
            wtool("wintask", [["all", "ทั้งหมด"], ["bad", `ผลรอบล่าสุดผิดพลาด (${bad})`], ["running", "กำลังทำงาน"], ["on", "เปิดอยู่"], ["off", "ปิดอยู่"],
                              ["own", "ไม่ใช่ของ Microsoft"]], ms) +
            (notes.length ? `<div class="wnote">${esc(notes.join(" · "))}</div>` : "") +
            table(["Task", "State", "Last run", "Last result", "Next run", "Triggers", "Run as", "Action (exe + arguments)"], rows,
                  WF.wintask.ms ? "ไม่มี task" : "ไม่มี task ที่ไม่ใช่ของ Microsoft (ติ๊ก \"รวม task ของ Microsoft\" เพื่อดูทั้งหมด)"), asof(d));
    }

    function renderWinPrograms(d) {
        const list = d.processes || [];
        const rows = list.map(p => {
            const svc = p.services || [];
            const k = [p.session === 0 ? "svc" : "user", svc.length ? "hosts" : "", p.path ? "" : "nopath"].join(" ");
            const search = [p.name, p.pid, p.path, p.desc, p.company, svc.join(" ")].join(" ").toLowerCase();
            const sub = [p.desc, p.company].filter(Boolean).join(" · ");
            return `<tr data-s="${esc(search)}" data-k="${k}"><td><b>${esc(p.name)}</b>${p.self ? ' <span class="wtag">central</span>' : ""}` +
                `${sub ? `<br><small>${esc(sub)}</small>` : ""}</td><td>${esc(p.pid)}</td>` +
                `<td class="${lv("cpu", p.cpu)}">${n0(p.cpu, 1, "%")}</td><td>${n0(p.ws_mb, 0, " MB")}${hasValue(p.memory) ? ` <small>${n0(p.memory, 1, "%")}</small>` : ""}</td>` +
                `<td>${mono(p.path)}</td><td>${svc.length ? `<small>${esc(svc.join(", "))}</small>` : "-"}</td>` +
                `<td>${p.started ? esc(ts(p.started)) : "-"}</td></tr>`;
        });
        return card(`Programs (${list.length} process)`,
            wtool("winproc", [["all", "ทั้งหมด"], ["svc", "รันเบื้องหลัง (session 0 / service)"], ["user", "รันในหน้าจอผู้ใช้"], ["hosts", "มี service อยู่ข้างใน"]]) +
            helperNote(d) + table(["Program", "PID", `CPU${d.interval ? ` (${d.interval}s)` : ""}`, "RAM", "Exe path", "Services", "Started"], rows, "ไม่มีข้อมูล"), asof(d));
    }

    /* ---------------- ทุกเครื่องใช้แท็บ · Service อยู่ข้างกราฟในแท็บ Overview ---------------- */
    function layout() {
        const main = q("#ovMain"), sv = q("#detail .svcard");
        if (sv && main && sv.parentNode !== main) { main.append(sv); main.className = "grid main"; }
        const w = curWeb();
        document.querySelectorAll("#dTabs [data-win]").forEach(b => { b.hidden = !(w && w.os === "windows"); });
        show(P.tab);
    }

    /* ---------------- โหลด / วาด ---------------- */
    const RENDER = { performance: renderPerformance, bottleneck: renderBottleneck, hardware: renderHardware,
                     system: renderSystem, alerts: renderAlerts, processes: renderProcesses,
                     winsvc: renderWinServices, wintask: renderWinTasks, winproc: renderWinPrograms };
    const BOX = { performance: "#pnPerf", bottleneck: "#pnBn", hardware: "#pnHw", system: "#pnSys", alerts: "#pnAl", processes: "#pnProc",
                  winsvc: "#pnWSvc", wintask: "#pnWTask", winproc: "#pnWProc" };

    async function fetchPane(tab) {
        const w = encodeURIComponent(S.web);
        if (WIN_TABS.includes(tab)) return winGet(`/api/host/${w}/${API_KIND[tab]}${tab === "wintask" && WF.wintask.ms ? "?ms=1" : ""}`);
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
            const win = WIN_TABS.includes(tab), act = document.activeElement;
            const typing = win && act && box.contains(act) && act.matches("[data-wq]");
            box.innerHTML = RENDER[tab](d || {});
            if (win) {
                wfilter(tab);
                const i = typing && box.querySelector("[data-wq]");
                if (i) { i.focus(); i.setSelectionRange(i.value.length, i.value.length); }
            } else prune(box);
            if (!box.textContent.trim() && tab !== "processes") box.innerHTML = `<div class="empty">ยังไม่มีข้อมูล</div>`;
        } catch (err) {
            console.error(`Pane ${tab}:`, err);
            if (tab !== "processes") box.innerHTML = `<div class="empty">โหลดข้อมูลไม่ได้: ${esc(err.message || err)}</div>`;
        } finally {
            P.busy[tab] = false;
        }
    }

    function show(tab) {
        const w = curWeb();
        if (WIN_TABS.includes(tab) && w && w.os !== "windows") tab = "overview";       // แท็บเฉพาะ Windows
        P.tab = PANES.includes(tab) ? tab : "overview";
        saveTab(P.tab);
        document.querySelectorAll("#dTabs [data-tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === P.tab)));
        document.querySelectorAll("#detail .dpane").forEach(p => { p.hidden = p.dataset.pane !== P.tab; });
        load(P.tab, false);
    }

    window.CMPanes = {
        refresh(force) { layout(); if (S.web) load(P.tab, force); },
        reset() { layout(); P.data = {}; P.at = {}; ["#pnPerf", "#pnBn", "#pnHw", "#pnSys", "#pnAl", "#pnProc", "#pnWSvc", "#pnWTask", "#pnWProc"].forEach(id => { const b = q(id); if (b) b.innerHTML = ""; }); },
        show,
    };

    document.addEventListener("DOMContentLoaded", () => {
        const tabs = q("#dTabs");
        if (!tabs) return;
        tabs.addEventListener("click", e => { const b = e.target.closest("[data-tab]"); if (b) show(b.dataset.tab); });
        tabs.addEventListener("keydown", e => {
            if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
            const vis = PANES.filter(p => { const b = tabs.querySelector(`[data-tab="${p}"]`); return b && !b.hidden; });
            const i = Math.max(0, vis.indexOf(P.tab)), n = vis[(i + (e.key === "ArrowRight" ? 1 : vis.length - 1)) % vis.length];
            show(n); const b = tabs.querySelector(`[data-tab="${n}"]`); if (b) b.focus();
        });
        /* ค้นหา / ตัวกรอง ของแท็บ Windows (กรองในหน้า ไม่ดึงใหม่) */
        document.addEventListener("input", e => {
            const t = e.target.closest && e.target.closest(".wtool");
            if (t && e.target.matches("[data-wq]")) { WF[t.dataset.wt].q = e.target.value; wfilter(t.dataset.wt); }
        });
        document.addEventListener("change", e => {
            const t = e.target.closest && e.target.closest(".wtool");
            if (!t) return;
            if (e.target.matches("[data-wk]")) { WF[t.dataset.wt].k = e.target.value; wfilter(t.dataset.wt); }
            if (e.target.matches("[data-wms]")) { WF.wintask.ms = e.target.checked; P.at[`${S.web}|wintask`] = 0; load("wintask", true); }
        });
        layout();
    });
})();
