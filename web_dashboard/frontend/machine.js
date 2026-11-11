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
     Software      โปรแกรมที่ทำงาน / พอร์ตที่เปิดรับ / โปรแกรมที่ติดตั้ง / ประวัติติดตั้ง-ถอน   GET /api/host/<web>/software
     Alerts & Events  sensor ของเครื่อง + ประวัติปัญหา 7 วัน                      GET /api/sensors?web= , /api/incidents?web=
   ใช้เฉพาะเครื่อง Windows · เครื่อง Linux แสดงหน้าแบบเดิม (ไม่มีแท็บ: KPI + กราฟคู่ Service, Process, System events)
   ค่าที่อ่านไม่ได้ไม่แสดง (ซ่อนแถว / คอลัมน์ / การ์ดที่ไม่มีค่า) ไม่มีค่าสมมติ
   ใช้ตัวแปร/ฟังก์ชันจาก dashboard.js: S, q, escapeHtml, hasValue, formatNumber, formatTimestamp, apiGet
   ===================================================================== */
(function () {
    "use strict";
    const PANES = ["overview", "performance", "bottleneck", "processes", "services", "hardware", "system", "software", "alerts"];
    const API_KIND = { performance: "performance", bottleneck: "bottleneck", hardware: "hardware", system: "system", software: "software" };
    const REFRESH_MS = { performance: 15000, bottleneck: 30000, hardware: 300000, system: 60000, software: 60000, alerts: 30000, processes: 20000 };
    const P = { tab: loadTab(), data: {}, at: {}, busy: {}, win: false, swq: "" };

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

    /* เครื่องที่ยังใช้ตัวเก็บในตัว Fluent Bit (ไม่มี windows_exporter) ข้อมูลจะน้อย -> บอกวิธีแก้ */
    const OLD_AGENT = `<div class="advice">เครื่องนี้ยังใช้ตัวเก็บ metrics ในตัว Fluent Bit จึงมีข้อมูลน้อย ·
        รันคำสั่งติดตั้ง agent ใหม่ (Settings &gt; เครื่องนี้ &gt; Install command) เพื่อใช้ windows_exporter
        แล้วจะเห็นผู้ผลิต / รุ่น / BIOS / ดิสก์ / การ์ดเครือข่าย / IP / Windows Update ครบ</div>`;

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
        return `${d.os === "windows" && d.exporter === false ? OLD_AGENT : ""}<div class="grid pgrid2">${system}${cpu}</div>${drives}${vols}${nics}${sensors}`;
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
            ["Type", d.type === "agent" ? "Windows agent (Fluent Bit)" : "SSH + node_exporter"],
            ["Metrics collector", t(a.collector)], ["Fluent Bit", t(a.fluent_bit)],
            (a.failed_collectors || []).length ? ["Failed collectors", `<b class="wr">${esc(a.failed_collectors.join(", "))}</b>`] : null,
        ]));
        const old = d.os === "windows" && /built-in/.test(a.collector || "") ? OLD_AGENT : "";
        return `${old}${head}<div class="grid pgrid2">${os}${time}</div><div class="grid pgrid2">${ident}${act}</div>${ips}${ag}`;
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

    /* ---------------- Software ---------------- */
    const SW_ACT = { installed: ["ติดตั้ง", "ok"], removed: ["ถอน", "cr"], updated: ["อัปเดต", "wr"] };
    function renderSoftware(d) {
        if (!d.supported) return `<div class="empty">ใช้ได้กับเครื่อง Windows</div>`;
        const notes = (d.unsupported || []).map(u => `<div class="empty" style="text-align:left">${esc(u.item)}: ${esc(u.reason)}</div>`).join("");
        const run = card("Running programs", table(["Program", "Instances", "CPU", "RAM", "Listening ports"],
            (d.running || []).map(p => `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.count)}</td><td>${num(p.cpu, 1, "%")}</td>`
                + `<td>${num(p.mem_mb, 0, " MB")}</td><td>${esc((p.ports || []).join(", ") || "-")}</td></tr>`)),
            `<span class="cnt">${(d.running || []).length}</span>`);
        const ports = card("Listening TCP ports", table(["Port", "Address", "Program"],
            (d.ports || []).map(p => `<tr><td><b>${esc(p.port)}</b></td><td>${esc(p.address)}</td><td>${esc(p.process || "-")}</td></tr>`)),
            `<span class="cnt">${(d.ports || []).length}</span>`);
        const dk = d.containers || [];
        const docker = card("Docker containers", table(["Container", "Image", "State", "Status"],
            dk.map(c => `<tr><td><b>${esc(c.name)}</b></td><td>${esc(c.image || "-")}</td>`
                + `<td class="${c.state === "running" && !/unhealthy/i.test(c.status) ? "ok" : "cr"}">${esc(c.state || "-")}</td><td>${esc(c.status || "-")}</td></tr>`)),
            `<span class="cnt">${dk.filter(c => c.state === "running").length} / ${dk.length} running</span>`);
        const lw = (d.local_http || []).filter(w => w.code > 0);
        /* เฝ้าดูหลายเว็บทีเดียว: ติ๊กพอร์ตแล้วกด "เฝ้าดูที่เลือก" (admin) */
        const wmSet = new Set((d.apps || []).filter(a => a.metric === "http_local").map(a => String((/:(\d+)/.exec(a.what) || [])[1] || "")));
        const admin = (S.role || "") === "admin";
        const pick = w => !admin ? "" : wmSet.has(String(w.port)) ? `<td><small>เฝ้าดูอยู่</small></td>`
            : `<td><input type="checkbox" class="wmpick" value="${esc(w.port)}" aria-label="เฝ้าดูพอร์ต ${esc(w.port)}"></td>`;
        const webs = card("Web on this machine (localhost)", table((admin ? [""] : []).concat(["Port", "URL", "HTTP", "Time", "Program"]),
            lw.map(w => `<tr>${pick(w)}<td><b>${esc(w.port)}</b></td><td>${esc(`${w.scheme}://localhost:${w.port}`)}</td>`
                + `<td class="${w.code >= 400 ? "cr" : "ok"}">${esc(w.code)}</td><td>${num(w.ms, 0, " ms")}</td><td>${esc(w.process || "-")}</td></tr>`)),
            `<span class="cnt">ตรวจทุก 1 นาที</span>` + (admin && lw.some(w => !wmSet.has(String(w.port)))
                ? `<button type="button" class="svbtn sm wmbulk" style="margin-left:auto">เฝ้าดูที่เลือก</button>` : ""));
        const hist = card("Software changes", table(["Time", "Change", "Program", "Version"],
            (d.history || []).map(h => { const [t, c] = SW_ACT[h.action] || [h.action, ""];
                return `<tr><td>${ts(h.time)}</td><td class="${c}">${esc(t)}</td><td>${esc(h.name)}${h.arch === "x86" ? " <small>(x86)</small>" : ""}</td>`
                    + `<td>${esc(h.action === "updated" ? `${h.old} → ${h.new}` : (h.new || h.old || "-"))}</td></tr>`; }),
            "ยังไม่มีการเปลี่ยนแปลง (เริ่มจำรายการเมื่อเครื่องส่งรายการครั้งแรก)"));
        const inst = card("Installed software", table(["Name", "Version", "Publisher", "Installed", "Arch"],
            (d.installed || []).map(x => `<tr><td><b>${esc(x.name)}</b></td><td>${esc(x.version || "-")}</td><td>${esc(x.publisher || "-")}</td>`
                + `<td>${esc(x.installed || "-")}</td><td>${esc(x.arch || "-")}</td></tr>`)),
            `<span class="cnt">${(d.installed || []).length}${d.inventory_time ? ` · updated ${esc(ts(d.inventory_time))}` : ""}</span>`
            + `<input class="swq" type="search" placeholder="ค้นหาโปรแกรม" value="${esc(P.swq)}" style="margin-left:auto;max-width:220px">`);
        const ap = d.apps || [];
        const bad = ap.filter(x => ["error", "down", "warning"].includes(x.status)).length;
        const tile = x => { const c = ["error", "down"].includes(x.status) ? "bad" : x.status === "warning" ? "wr" : x.status === "ok" ? "ok" : "";
            const age = x.status === "ok" ? "ทำงาน" : c ? "หยุด" : "รอข้อมูล";
            return `<div class="apT ${c}"><span class="apDot"></span><div class="apMain"><b>${esc(x.name)}</b><p>${esc(x.message)}</p></div><span class="apAge">${age}</span></div>`; };
        const watched = `<section class="card pcard"><div class="top"><h2>Watched apps &amp; web</h2><span class="cnt">${ap.length ? (bad ? `มีปัญหา ${bad}` : "ทำงานปกติทั้งหมด") : ""}</span>`
            + `<a class="st-nav" href="/apps" style="margin-left:auto">จัดการ</a></div>`
            + (ap.length ? `<div class="apGrid advice">${ap.map(tile).join("")}</div>` : `<div class="empty advice">ยังไม่ได้เฝ้าดูโปรแกรม / เว็บของเครื่องนี้ · เพิ่มได้ที่หน้า Apps &amp; Web</div>`) + `</section>`;
        return notes + watched + `<div class="grid pgrid2">${run}${ports}</div>` + `<div class="grid pgrid2">${webs}${docker}</div>` + inst + hist;
    }
    function swFilter(box) {
        const qv = P.swq.trim().toLowerCase();
        box.querySelectorAll(".swq").forEach(inp => {
            const tb = inp.closest(".pcard").querySelector("tbody");
            if (tb) [...tb.rows].forEach(r => { r.hidden = !!qv && !r.textContent.toLowerCase().includes(qv); });
        });
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

    /* ---------------- Windows = แท็บ / Linux = หน้าแบบเดิม ---------------- */
    function isWindows() {
        const w = (S.webs || []).find(x => x.web === S.web);
        return !!w && w.os === "windows";
    }
    const LINUX_PANES = ["overview", "processes", "system"];
    function layout() {
        const win = isWindows(), main = q("#ovMain"), home = q("#svHome"), sv = q("#detail .svcard");
        P.win = win;
        q("#dTabs").hidden = !win;
        if (sv && main && home) {
            if (win && sv.parentNode !== home) { home.append(sv); main.className = "grid one"; }
            if (!win && sv.parentNode !== main) { main.append(sv); main.className = "grid main"; }
        }
        if (win) { show(P.tab); return; }
        document.querySelectorAll("#detail .dpane").forEach(p => { p.hidden = !LINUX_PANES.includes(p.dataset.pane); });
    }

    /* ---------------- โหลด / วาด ---------------- */
    const RENDER = { performance: renderPerformance, bottleneck: renderBottleneck, hardware: renderHardware,
                     system: renderSystem, software: renderSoftware, alerts: renderAlerts, processes: renderProcesses };
    const BOX = { performance: "#pnPerf", bottleneck: "#pnBn", hardware: "#pnHw", system: "#pnSys", software: "#pnSw", alerts: "#pnAl", processes: "#pnProc" };

    async function fetchPane(tab) {
        const w = encodeURIComponent(S.web);
        if (API_KIND[tab] && tab !== "software") return apiGet(`/api/host/${w}/${API_KIND[tab]}`);
        if (tab === "alerts") {
            const [s, i] = await Promise.all([apiGet(`/api/sensors?web=${w}`), apiGet(`/api/incidents?web=${w}&hours=168`)]);
            return { sensors: s.sensors || [], incidents: i.incidents || [] };
        }
        if (tab === "processes") return apiGet(`/api/live-processes?web=${w}`);
        if (tab === "software") {
            const [sw, ap] = await Promise.all([apiGet(`/api/host/${w}/software`), apiGet(`/api/apps?web=${w}`).catch(() => ({ items: [] }))]);
            return { ...sw, apps: ap.items || [] };
        }
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
            prune(box);
            if (tab === "software") swFilter(box);
        } catch (err) {
            console.error(`Pane ${tab}:`, err);
            if (tab !== "processes") box.innerHTML = `<div class="empty">โหลดข้อมูลไม่ได้: ${esc(err.message || err)}</div>`;
        } finally {
            P.busy[tab] = false;
        }
    }

    function show(tab) {
        if (!P.win) return;
        P.tab = PANES.includes(tab) ? tab : "overview";
        saveTab(P.tab);
        document.querySelectorAll("#dTabs [data-tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === P.tab)));
        document.querySelectorAll("#detail .dpane").forEach(p => { p.hidden = p.dataset.pane !== P.tab; });
        load(P.tab, false);
    }

    window.CMPanes = {
        refresh(force) { layout(); if (S.web && P.win) load(P.tab, force); },
        reset() { layout(); P.data = {}; P.at = {}; ["#pnPerf", "#pnBn", "#pnHw", "#pnSys", "#pnSw", "#pnAl", "#pnProc"].forEach(id => { const b = q(id); if (b) b.innerHTML = ""; }); },
        show,
    };

    document.addEventListener("DOMContentLoaded", () => {
        const tabs = q("#dTabs");
        if (!tabs) return;
        tabs.addEventListener("click", e => { const b = e.target.closest("[data-tab]"); if (b) show(b.dataset.tab); });
        const sw = q("#pnSw");
        if (sw) sw.addEventListener("input", e => { if (e.target.matches(".swq")) { P.swq = e.target.value; swFilter(sw); } });
        if (sw) sw.addEventListener("click", async e => {
            const b = e.target.closest(".wmbulk"); if (!b) return;
            const ports = [...sw.querySelectorAll(".wmpick:checked")].map(i => Number(i.value));
            if (!ports.length) { alert("ติ๊กพอร์ตที่ต้องการเฝ้าดูก่อน"); return; }
            b.disabled = true;
            try {
                const r = await fetch("/api/apps/bulk", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ web: S.web, ports }) });
                const j = await r.json().catch(() => ({}));
                if (!r.ok) throw new Error(j.error || Object.values(j.fields || {}).join(" · ") || `HTTP ${r.status}`);
                b.textContent = `เพิ่มแล้ว ${j.added.length} เว็บ`;
                setTimeout(() => load("software", true), 800);
            } catch (err) { alert("เพิ่มไม่สำเร็จ: " + err.message); b.disabled = false; }
        });
        tabs.addEventListener("keydown", e => {
            if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
            const i = PANES.indexOf(P.tab), n = PANES[(i + (e.key === "ArrowRight" ? 1 : PANES.length - 1)) % PANES.length];
            show(n); const b = tabs.querySelector(`[data-tab="${n}"]`); if (b) b.focus();
        });
        layout();
    });
})();
