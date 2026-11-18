/* =====================================================================
   network.js - หน้า Network & Security (ดู network.html)
     แผนผัง: วงกลม = เครื่องที่ monitor (สีตามผลตรวจแย่สุด) · ลูกศร A → B = A ต่อเข้าพอร์ตของ B (ป้าย = พอร์ต)
             Internet = IP สาธารณะ · LAN อื่น = IP ภายในที่ไม่ได้ monitor
     ข้อมูลมาจากตัวเครื่องเอง (ไม่ใช่ flow จาก switch / router): Linux ทุกรอบสถานะของ puller · Windows ทุก 5 นาที
   ===================================================================== */
"use strict";
const $ = id => document.getElementById(id);
const N = { d: null, ov: {}, view: "map", web: null, tab: "checks", m: null, q: "", layout: "subnet" };
const ST = { ok: ["OK", "ok"], warning: ["Warning", "wr"], error: ["Problem", "cr"], unknown: ["?", "na"] };
const COL = { ok: "#0a9a60", warning: "#f5b400", error: "#e5484d", unknown: "#9aa6bd" };

function esc(v) { return String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
async function getJson(url, opt) {
    const r = await fetch(url, { cache: "no-store", credentials: "same-origin", ...(opt || {}) });
    if (r.status === 401) { location.href = "/login"; throw new Error("auth"); }
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    return r.json();
}
function ago(v) {
    const t = typeof v === "number" ? v * 1000 : Date.parse(v);
    if (!Number.isFinite(t)) return "-";
    const s = Math.max(0, (Date.now() - t) / 1000);
    return s < 90 ? `${Math.round(s)} วินาที` : s < 5400 ? `${Math.round(s / 60)} นาที` : s < 172800 ? `${Math.round(s / 3600)} ชม.` : `${Math.round(s / 86400)} วัน`;
}
function fmtTime(v) { const t = typeof v === "number" ? v * 1000 : Date.parse(v); return Number.isFinite(t) ? new Date(t).toLocaleString("en-GB") : esc(v || "-"); }
const fwText = s => ({ active: "เปิด", inactive: "ปิด", unknown: "ดึงข้อมูลไม่ได้" }[s] || s || "-");
const fwCls = s => s === "active" ? "ok" : s === "inactive" ? "cr" : "na";
const pill = (txt, cls) => `<span class="ovst ${cls}">${esc(txt)}</span>`;
const match = (...v) => !N.q || v.some(x => String(x ?? "").toLowerCase().includes(N.q));

/* ---------------- ไทล์ ---------------- */
function renderTiles() {
    const t = N.d.totals || {};
    const tile = (label, n, cls, sub) => `<div class="ovtile ${cls}"><small>${label}</small><b>${n ?? "-"}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
    $("nsTiles").innerHTML = tile("เครื่อง", t.machines, "tot", `${N.d.edges.length} เส้นเชื่อมระหว่างเครื่อง`)
        + tile("Firewall ปิด", t.fw_off, t.fw_off ? "dn" : "up", t.fw_unknown ? `ดึงข้อมูลไม่ได้ ${t.fw_unknown} เครื่อง` : "ดึงข้อมูลได้ทุกเครื่อง")
        + tile("Login ไม่สำเร็จ 24h", t.failed_logins, t.failed_logins >= 100 ? "dn" : t.failed_logins >= 20 ? "nok" : "up", "SSH / Windows logon")
        + tile("ขาเข้า", t.conn_in, "tot", "การเชื่อมต่อที่ค้างอยู่")
        + tile("ขาออก", t.conn_out, "tot", "การเชื่อมต่อที่ค้างอยู่")
        + tile("IP สาธารณะ", t.public_peers, t.public_peers ? "nok" : "up", "ที่คุยกับเครื่องเรา");
}

/* ---------------- แผนผังการเชื่อมต่อ (ใครต่อหาใคร) อ่านจากซ้ายไปขวา ----------------
   ซ้ายสุด = ผู้เริ่มเชื่อมต่อ · ขวา = ปลายทางที่ถูกต่อเข้า (ลูกศร A → B = A ต่อเข้าพอร์ตของ B ป้ายบนเส้น = พอร์ต)
   Internet / LAN อื่น (IP ที่ไม่ได้ monitor) แยกเป็น "ต่อเข้ามา" (ซ้ายสุด) กับ "ต่อออกไป" (ขวาสุด)
   จัดชั้นอัตโนมัติ (longest path) + เรียงในชั้นให้เส้นตัดกันน้อย · เส้นโค้งนุ่มแนวนอน หนาตามจำนวนการเชื่อมต่อ
   เครื่องที่ไม่มีการเชื่อมต่อกับใคร อยู่แถวล่าง · คลิกเครื่อง = ไฮไลต์เส้นของเครื่องนั้น + รายละเอียด */
const STC = { ok: "#0a9a60", warning: "#f5b400", error: "#e5484d", unknown: "#9aa6bd" };
const ipNum = ip => /^\d+\.\d+\.\d+\.\d+$/.test(ip || "") ? ip.split(".").reduce((a, x) => a * 256 + Number(x), 0) : Infinity;
function flowModel() {
    const ms = N.d.machines.filter(m => match(m.web, m.host, m.group, m.ip));
    const byName = Object.fromEntries(ms.map(m => [m.web, m]));
    const nodes = {}, edges = {};
    const node = (id, o) => nodes[id] || (nodes[id] = { id, ...o, ins: [], outs: [] });
    const edge = (from, to, port, count, kind, peer, procs) => {
        const k = from + "→" + to;
        const e = edges[k] || (edges[k] = { from, to, ports: new Set(), count: 0, kind, peers: new Set(), procs: new Set() });
        if (port != null) e.ports.add(port); e.count += count || 1; if (peer) e.peers.add(peer); (procs || []).forEach(p => e.procs.add(p));
    };
    N.d.edges.filter(e => byName[e.from] && byName[e.to]).forEach(e => {
        node(e.from, { kind: "m", m: byName[e.from] }); node(e.to, { kind: "m", m: byName[e.to] });
        edge(e.from, e.to, e.port, e.count, "m", null, e.procs);
    });
    if (N.showExt !== false) N.d.external.filter(x => byName[x.web]).forEach(x => {
        const ext = (x.public ? "inet" : "lan") + (x.dir === "in" ? "-in" : "-out");
        node(x.web, { kind: "m", m: byName[x.web] });
        node(ext, { kind: "x", pub: x.public, dir: x.dir });
        if (x.dir === "in") edge(ext, x.web, x.port, x.count, x.public ? "pub" : "lan", x.peer, x.procs);
        else edge(x.web, ext, x.port, x.count, x.public ? "pub" : "lan", x.peer, x.procs);
    });
    Object.values(edges).forEach(e => { nodes[e.from].outs.push(e); nodes[e.to].ins.push(e); });
    /* ชั้น: ต่อเข้ามา = 0 · เครื่อง = 1 + ทางยาวสุดจากผู้เริ่ม (ไม่นับเส้นย้อนวน) · ต่อออกไป = ชั้นสุดท้าย */
    const layer = {}, state = {};
    const back = new Set();
    const dfs = id => {
        state[id] = 1;
        nodes[id].outs.forEach(e => { if (nodes[e.to].kind !== "m") return; if (state[e.to] === 1) back.add(e); else if (!state[e.to]) dfs(e.to); });
        state[id] = 2;
    };
    Object.keys(nodes).filter(id => nodes[id].kind === "m").sort().forEach(id => { if (!state[id]) dfs(id); });
    const lay = id => {
        if (layer[id] != null) return layer[id];
        layer[id] = 1;
        const ins = nodes[id].ins.filter(e => !back.has(e) && nodes[e.from].kind === "m");
        layer[id] = ins.length ? 1 + Math.max(...ins.map(e => lay(e.from))) : 1;
        return layer[id];
    };
    Object.keys(nodes).filter(id => nodes[id].kind === "m").forEach(lay);
    const maxL = Math.max(1, ...Object.values(layer));
    Object.values(nodes).forEach(n => { if (n.kind === "x") layer[n.id] = n.dir === "in" ? 0 : maxL + 1; });
    const cols = [];
    Object.values(nodes).forEach(n => { (cols[layer[n.id]] = cols[layer[n.id]] || []).push(n); });
    /* เรียงในชั้น: เริ่มตาม IP แล้วจัดตามค่าเฉลี่ยตำแหน่งของคู่เชื่อม (ลดเส้นตัดกัน) 4 รอบ */
    const key = n => n.kind === "x" ? (n.pub ? 0 : 1) : ipNum(n.m.ip) || 0;
    cols.forEach(c => c && c.sort((a, b) => key(a) - key(b) || a.id.localeCompare(b.id)));
    const idx = () => { const o = {}; cols.forEach(c => c && c.forEach((n, i) => { o[n.id] = i; })); return o; };
    for (let it = 0; it < 4; it++) {
        const ix = idx();
        cols.forEach((c, li) => {
            if (!c || li === 0) return;
            const bc = n => { const nb = (it % 2 ? n.outs.map(e => e.to) : n.ins.map(e => e.from)); return nb.length ? nb.reduce((a, x) => a + ix[x], 0) / nb.length : ix[n.id]; };
            c.sort((a, b) => bc(a) - bc(b));
        });
    }
    const lone = ms.filter(m => !nodes[m.web]).sort((a, b) => ipNum(a.ip) - ipNum(b.ip));
    return { nodes, edges: Object.values(edges), cols: cols.map(c => c || []), lone, back };
}
function mapHtml() {
    const md = flowModel(), sel = N.web;
    const NW = 196, NH = 54, GX = 120, GY = 24, PAD = 20;
    const ncol = md.cols.length;
    const colH = c => c.length * NH + Math.max(0, c.length - 1) * GY;
    const maxH = Math.max(NH, ...md.cols.map(colH));
    const W = Math.max(1120, PAD * 2 + ncol * NW + (ncol - 1) * GX);
    const gx = ncol > 1 ? (W - PAD * 2 - ncol * NW) / (ncol - 1) : 0;
    const pos = {};
    md.cols.forEach((c, li) => {
        const x = PAD + li * (NW + gx), y0 = 40 + (maxH - colH(c)) / 2;
        c.forEach((n, i) => { pos[n.id] = { x, y: y0 + i * (NH + GY) }; });
    });
    /* จุดต่อของเส้นกระจายตามขอบซ้าย / ขวาของกล่อง (เส้นไม่ทับกัน) */
    const port = {};
    Object.values(md.nodes).forEach(n => {
        const outs = n.outs.slice().sort((a, b) => pos[a.to].y - pos[b.to].y), ins = n.ins.slice().sort((a, b) => pos[a.from].y - pos[b.from].y);
        outs.forEach((e, i) => { port[e.from + "→" + e.to + ":s"] = pos[n.id].y + NH * (i + 1) / (outs.length + 1); });
        ins.forEach((e, i) => { port[e.from + "→" + e.to + ":t"] = pos[n.id].y + NH * (i + 1) / (ins.length + 1); });
    });
    const touches = e => sel && (e.from === sel || e.to === sel);
    let edgesSvg = "", labels = "";
    md.edges.forEach(e => {
        const a = pos[e.from], b = pos[e.to], k = e.from + "→" + e.to;
        const x1 = a.x + NW, y1 = port[k + ":s"], x2 = b.x, y2 = port[k + ":t"];
        const backward = x2 <= x1, dx = Math.max(60, Math.abs(x2 - x1) * 0.45);
        const d = backward ? `M${x1},${y1} C${x1 + 70},${y1} ${x1 + 70},${maxH + 90} ${(x1 + x2) / 2},${maxH + 90} S${x2 - 70},${y2} ${x2 - 8},${y2}`
            : `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2 - 8},${y2}`;
        const on = touches(e), dim = sel && !on;
        const w = 1.4 + Math.min(4, Math.log2(e.count + 1));
        const ports = [...e.ports].sort((p, q) => p - q);
        const tip = `${nodeName(md.nodes[e.from])} → ${nodeName(md.nodes[e.to])} · พอร์ต ${ports.join(", ")} · ${e.count} การเชื่อมต่อ`
            + (e.procs.size ? ` · ${[...e.procs].slice(0, 4).join(", ")}` : "") + (e.peers.size ? ` · ${[...e.peers].slice(0, 8).join(", ")}${e.peers.size > 8 ? " ..." : ""}` : "");
        edgesSvg += `<g class="fe ${e.kind}${on ? " on" : ""}${dim ? " dim" : ""}${backward ? " back" : ""}"><path d="${d}" style="stroke-width:${w.toFixed(1)}px" marker-end="url(#fA${e.kind}${on ? "o" : ""})"><title>${esc(tip)}</title></path></g>`;
        /* ป้ายพอร์ตใกล้ปลายทาง (อ่านได้ว่าเข้าพอร์ตไหนของใคร) */
        const lab = ports.slice(0, 3).map(p => ":" + p).join(" ") + (ports.length > 3 ? ` +${ports.length - 3}` : "");
        if (!backward && (!sel || on)) labels += `<text class="fl${on ? " on" : ""}" x="${x2 - 14}" y="${y2 - 4}" text-anchor="end">${esc(lab)}</text>`;
    });
    const nodeSvg = n => {
        const p = pos[n.id], on = sel === n.id, dim = sel && !on && !md.edges.some(e => touches(e) && (e.from === n.id || e.to === n.id));
        if (n.kind === "x") {
            const peers = new Set(); (n.dir === "in" ? n.ins : n.outs).concat(n.dir === "in" ? n.outs : n.ins).forEach(e => e.peers.forEach(x => peers.add(x)));
            return `<g class="fn x ${n.pub ? "pub" : "lan"}${dim ? " dim" : ""}"><rect x="${p.x}" y="${p.y}" width="${NW}" height="${NH}" rx="27"/>`
                + `<text x="${p.x + NW / 2}" y="${p.y + 23}" text-anchor="middle" class="nm">${n.pub ? "🌐 Internet" : "LAN อื่น"}</text>`
                + `<text x="${p.x + NW / 2}" y="${p.y + 40}" text-anchor="middle" class="ip">${n.dir === "in" ? "ต่อเข้ามา" : "ต่อออกไป"} · ${peers.size} IP</text>`
                + `<title>${esc([...peers].slice(0, 20).join(", "))}</title></g>`;
        }
        return machineNode(n.m, p.x, p.y, NW, NH, on, dim);
    };
    const nodesSvg = Object.values(md.nodes).map(nodeSvg).join("");
    /* เครื่องที่ไม่มีการเชื่อมต่อ: แถวล่าง */
    let loneSvg = "", H = maxH + 60 + (md.back.size ? 60 : 0);
    if (md.lone.length) {
        const per = Math.max(1, Math.floor((W - PAD * 2 + 16) / (NW + 16)));
        loneSvg += `<text x="${PAD}" y="${H + 10}" class="fh">ไม่มีการเชื่อมต่อกับเครื่องอื่นตอนเก็บข้อมูล (${md.lone.length})</text>`;
        md.lone.forEach((m, i) => { loneSvg += machineNode(m, PAD + (i % per) * (NW + 16), H + 22 + Math.floor(i / per) * (NH + 14), NW, NH, sel === m.web, sel && sel !== m.web); });
        H += 30 + Math.ceil(md.lone.length / per) * (NH + 14);
    }
    const heads = md.cols.length > 1 ? `<text x="${PAD}" y="22" class="fh">เริ่มเชื่อมต่อ →</text><text x="${W - PAD}" y="22" text-anchor="end" class="fh">→ ปลายทาง</text>` : "";
    const marker = (id, c) => `<marker id="${id}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" fill="${c}"/></marker>`;
    return `<div class="tbar"><label class="st-check"><input type="checkbox" data-ext ${N.showExt !== false ? "checked" : ""}> แสดง Internet / LAN อื่น</label>
<span class="st-mut">ลูกศร A → B = A ต่อเข้าพอร์ตของ B · เส้นหนา = เชื่อมต่อมาก · คลิกเครื่องเพื่อไฮไลต์และดูรายละเอียด${sel ? ` · <a href="#" data-clear>ยกเลิกเลือก</a>` : ""}</span></div>
<div class="topow"><svg class="flow" viewBox="0 0 ${W} ${H + 10}" role="img" aria-label="แผนผังการเชื่อมต่อ">
<defs>${marker("fAm", "#7c8aa5")}${marker("fAmo", "#1f7bff")}${marker("fApub", "#e08a00")}${marker("fApubo", "#e08a00")}${marker("fAlan", "#9aa6bd")}${marker("fAlano", "#1f7bff")}</defs>
${heads}${edgesSvg}${nodesSvg}${labels}${loneSvg}</svg></div>
<div class="nslegend"><span><i style="background:${STC.ok}"></i>ผ่านทุกข้อ</span><span><i style="background:${STC.warning}"></i>มีข้อควรระวัง</span><span><i style="background:${STC.error}"></i>มีปัญหา</span><span><i style="background:${STC.unknown}"></i>ไม่มีข้อมูล</span><span><b style="color:#7c8aa5">━</b> ระหว่างเครื่อง</span><span><b style="color:#e08a00">━</b> Internet</span><span><b style="color:#9aa6bd">┅</b> LAN อื่น</span></div>`;
}
function nodeName(n) { return n.kind === "m" ? n.m.web : `${n.pub ? "Internet" : "LAN อื่น"} (${n.dir === "in" ? "ต่อเข้ามา" : "ต่อออกไป"})`; }
function machineNode(m, x, y, w, h, on, dim) {
    const c = m.available ? STC[m.worst] || STC.unknown : STC.unknown;
    const sub = [m.ip || m.host || "", !m.available ? "ไม่มีข้อมูล" : m.firewall === "inactive" ? "FW ปิด" : ""].filter(Boolean).join(" · ");
    return `<g class="fn m${on ? " on" : ""}${dim ? " dim" : ""}" data-web="${esc(m.web)}" tabindex="0"><rect x="${x}" y="${y}" width="${w}" height="${h}" rx="10"/>`
        + `<rect x="${x}" y="${y + 1}" width="6" height="${h - 2}" rx="3" style="fill:${c}" class="bar"/>`
        + `<text x="${x + 18}" y="${y + 22}" class="nm">${esc(m.web.length > 20 ? m.web.slice(0, 19) + "…" : m.web)}</text>`
        + `<text x="${x + w - 10}" y="${y + 22}" text-anchor="end" class="os">${m.os === "windows" ? "Win" : "Linux"}</text>`
        + `<text x="${x + 18}" y="${y + 40}" class="ip${m.firewall === "inactive" || !m.available ? " cr" : ""}">${esc(sub)}</text>`
        + `<title>${esc(`${m.web} · ${m.ip || m.host || ""} · Firewall ${fwText(m.firewall)} · ขาเข้า ${m.in} · ขาออก ${m.out}`)}</title></g>`;
}

/* ---------------- ตารางทุกเครื่อง ---------------- */
function tableHtml() {
    const ms = N.d.machines.filter(m => match(m.web, m.host, m.group, m.fw_tool));
    const ov = N.ov;
    return `<div class="tblw"><table class="st-tbl nstbl"><thead><tr><th>เครื่อง</th><th>ผลตรวจ</th><th>Firewall</th><th>ขาเข้าปริยาย</th><th>พอร์ตเปิดรับ</th><th>ขาเข้า</th><th>ขาออก</th><th>Login ไม่สำเร็จ 24h</th><th>Traffic</th><th>Ping / loss</th><th>Availability</th></tr></thead><tbody>`
        + ms.map(m => {
            const o = ov[m.web] || {}, [wl, wc] = ST[m.worst] || ST.unknown;
            const fl = m.failed_logins;
            return `<tr data-web="${esc(m.web)}" tabindex="0"><td><b>${esc(m.web)}</b> <span class="ostag${m.os === "windows" ? "" : " lx"}">${m.os === "windows" ? "Win" : "Linux"}</span><small class="nssub">${esc([m.group, m.host].filter(Boolean).join(" · "))}</small></td>`
                + `<td>${m.available ? pill(wl, wc) : `<span class="st-mut">ไม่มีข้อมูล</span>`}</td><td>${!m.available ? "-" : m.firewall === "unknown" ? `<span class="st-mut">ดึงข้อมูลไม่ได้</span>` : `${pill(fwText(m.firewall), fwCls(m.firewall))} <small class="st-mut">${esc(m.fw_tool)}</small>`}</td>`
                + `<td>${esc(m.default_in || "-")}</td><td>${m.listening}</td><td>${m.in} <small class="st-mut">(${m.peers_in} IP)</small></td><td>${m.out} <small class="st-mut">(${m.peers_out} IP)</small></td>`
                + `<td class="ovv ${fl == null ? "" : fl >= 100 ? "cr" : fl >= 20 ? "wr" : "ok"}">${fl ?? (m.available ? `<span class="st-mut" style="font-weight:400">ดึงข้อมูลไม่ได้</span>` : "-")}</td>`
                + `<td>${o.net != null ? Number(o.net).toFixed(2) + " MB/s" : "-"}</td><td>${o.ping_ms != null ? Math.round(o.ping_ms) + " ms" : "-"}${o.packet_loss != null ? ` · ${Number(o.packet_loss).toFixed(0)}%` : ""}</td>`
                + `<td>${o.availability != null ? Number(o.availability).toFixed(2) + "%" : "-"}</td></tr>`;
        }).join("") + `</tbody></table></div>`;
}

function externalHtml() {
    const rows = N.d.external.filter(x => match(x.web, x.peer, x.port, ...(x.procs || [])));
    if (!rows.length) return `<div class="empty">ไม่มีการเชื่อมต่อไปนอกกลุ่มเครื่องที่ monitor</div>`;
    return `<div class="tblw"><table class="st-tbl nstbl"><thead><tr><th>เครื่อง</th><th>ทิศทาง</th><th>IP ปลายทาง / ต้นทาง</th><th>พอร์ต</th><th>จำนวน</th><th>โปรแกรม</th><th>ประเภท</th></tr></thead><tbody>`
        + rows.map(x => `<tr data-web="${esc(x.web)}" tabindex="0"><td><b>${esc(x.web)}</b></td><td>${x.dir === "in" ? "⬅ ขาเข้า" : "➡ ขาออก"}</td><td><code>${esc(x.peer)}</code></td><td>${esc(x.port)}</td><td>${x.count}</td><td>${esc((x.procs || []).join(", ") || "-")}</td><td>${x.public ? pill("IP สาธารณะ", x.dir === "in" ? "wr" : "na") : "ภายใน"}</td></tr>`).join("")
        + `</tbody></table></div>`;
}

function renderMain() {
    document.querySelectorAll("#nsView button").forEach(b => b.classList.toggle("on", b.dataset.v === N.view));
    $("nsMain").innerHTML = N.view === "table" ? tableHtml() : N.view === "external" ? externalHtml() : mapHtml();
}

/* ---------------- เครื่องเดียว ---------------- */
const TABS = [["checks", "ผลตรวจ"], ["conn", "การเชื่อมต่อ"], ["fw", "Firewall"], ["route", "Route"], ["listen", "พอร์ตที่เปิดรับ"], ["login", "Login"]];
function tbl(head, rows, empty) {
    if (!rows.length) return `<div class="empty">${empty || "ไม่มีข้อมูล"}</div>`;
    return `<div class="tblw"><table class="st-tbl nstbl"><thead><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
function detailBody(d) {
    if (N.tab === "checks") {
        if (!d.available) return `<div class="empty">ไม่มีข้อมูลจากเครื่องนี้</div>`;
        return `<div class="nschecks">${(d.checks || []).filter(c => c.status !== "unknown").map(c => { const [, cls] = ST[c.status] || ST.unknown;
            return `<div class="nsck ${cls}"><b>${c.status === "ok" ? "✓" : c.status === "unknown" ? "?" : "!"} ${esc(c.title)}</b><span>${esc(c.detail)}</span></div>`; }).join("") || `<div class="empty">ไม่มีข้อมูล</div>`}</div>`
            + (d.addrs && d.addrs.length ? `<div class="nsaddr">IP ของเครื่อง: ${d.addrs.map(a => `<code>${esc(a.ip)}/${a.prefix}</code> <small class="st-mut">${esc(a.if || "")}</small>`).join(" · ")}</div>` : "");
    }
    if (N.tab === "conn") {
        const row = c => match(c.peer, c.port, c.proc, c.peer_web) ? `<tr><td>${c.dir === "in" ? "⬅ ขาเข้า" : "➡ ขาออก"}</td><td><code>${esc(c.peer)}</code>${c.peer_web ? ` <a href="/network?web=${encodeURIComponent(c.peer_web)}">${esc(c.peer_web)}</a>` : c.external ? ` ${pill("สาธารณะ", "wr")}` : ""}</td><td>${esc(c.port)}</td><td>${esc(c.proc || "-")}</td><td>${c.count}</td></tr>` : "";
        const inn = (d.connections || []).filter(c => c.dir === "in").map(row).filter(Boolean), out = (d.connections || []).filter(c => c.dir === "out").map(row).filter(Boolean);
        return `<div class="nsh">⬅ ขาเข้า: เครื่องอื่นต่อเข้าพอร์ตของเครื่องนี้ <small>(พอร์ต = พอร์ตของเครื่องนี้)</small></div>${tbl(["ทิศทาง", "ต้นทาง", "พอร์ต", "โปรแกรม", "จำนวน"], inn, "ไม่มีใครต่อเข้ามาตอนเก็บข้อมูล")}`
            + `<div class="nsh">➡ ขาออก: เครื่องนี้ต่อออกไปหา <small>(พอร์ต = พอร์ตปลายทาง)</small></div>${tbl(["ทิศทาง", "ปลายทาง", "พอร์ต", "โปรแกรม", "จำนวน"], out, "ไม่มีการต่อออกตอนเก็บข้อมูล")}`;
    }
    if (N.tab === "fw") {
        const fw = d.firewall;
        if (!fw || fw.status === "unknown") return `<div class="empty">ไม่สามารถดึงข้อมูล Firewall ของเครื่องนี้ได้</div>`;
        const df = fw.defaults || {};
        const prof = fw.profiles ? `<div class="nsaddr">Profiles: ${fw.profiles.map(p => `<b>${esc(p.profile)}</b> ${p.enabled ? "เปิด" : "ปิด"} (in ${esc(p.in)} / out ${esc(p.out)})`).join(" · ")}</div>` : "";
        const rows = (fw.rules || []).filter(r => match(r.to, r.from, r.action, r.comment, r.program)).map(r => `<tr><td>${pill(r.action, /ALLOW/.test(r.action) ? "ok" : /LIMIT/.test(r.action) ? "wr" : "cr")}</td><td>${r.dir === "in" ? "⬅ เข้า" : r.dir === "out" ? "➡ ออก" : esc(r.dir)}</td><td><code>${esc(r.to)}</code></td><td>${esc(r.from)}</td><td>${esc([r.comment, r.program].filter(Boolean).join(" · ") || "-")}</td></tr>`);
        return `<div class="nsfw"><div>${pill(`Firewall ${fwText(fw.status)}`, fwCls(fw.status))} <span class="st-mut">${esc(fw.tool)}</span></div><div>ขาเข้าปริยาย <b>${esc(df.in || "-")}</b></div><div>ขาออกปริยาย <b>${esc(df.out || "-")}</b></div>${df.routed ? `<div>Forward <b>${esc(df.routed)}</b></div>` : ""}</div>`
            + prof
            + tbl(["Action", "ทิศทาง", "พอร์ต / ปลายทาง", "จาก", "ชื่อ / หมายเหตุ"], rows, (fw.rules || []).length ? "ไม่มีกฎที่ตรงคำค้น" : fw.note ? "ไม่สามารถดึงรายการกฎได้" : "ไม่มีกฎ");
    }
    if (N.tab === "route") {
        return tbl(["ปลายทาง", "Gateway", "Interface", "Metric"], (d.routes || []).filter(r => match(r.dest, r.gw, r.if)).map(r =>
            `<tr><td><code>${esc(r.dest)}</code>${r.dest === "0.0.0.0/0" ? " <small class=\"st-mut\">(default)</small>" : ""}</td><td>${esc(r.gw || "ต่อตรง (on-link)")}</td><td>${esc(r.if || "-")}</td><td>${esc(r.metric || "-")}</td></tr>`), "ไม่มีข้อมูล route");
    }
    if (N.tab === "listen") {
        return tbl(["พอร์ต", "Protocol", "ฟังที่", "โปรแกรม", "รับจาก"], (d.listening || []).filter(x => match(x.port, x.process, x.address)).map(x =>
            `<tr><td><b>${esc(x.port)}</b></td><td>${esc(x.proto)}</td><td><code>${esc(x.address)}</code></td><td>${esc(x.process || "-")}</td><td>${x.local_only ? "เฉพาะในเครื่อง" : pill("เครือข่าย", "wr")}</td></tr>`), "ไม่มีข้อมูล");
    }
    const lg = d.logins;
    if (!lg || lg.failed == null) return `<div class="empty">${d.available ? "ไม่สามารถดึงข้อมูล Login ของเครื่องนี้ได้" : "ไม่มีข้อมูล"}</div>`;
    return `<div class="nsfw"><div>ไม่สำเร็จ <b class="${lg.failed >= 100 ? "cr" : lg.failed >= 20 ? "wr" : ""}">${lg.failed ?? "-"}</b></div>${lg.invalid_users != null ? `<div>user ไม่มีจริง <b>${lg.invalid_users}</b></div>` : ""}${lg.success != null ? `<div>สำเร็จ <b>${lg.success}</b></div>` : ""}<div class="st-mut">${esc(lg.source || "")}</div></div>`
        + `<div class="nsh">ไม่สำเร็จ แยกตามต้นทาง</div>` + tbl(["IP ต้นทาง", "User", "ครั้ง"], (lg.failed_by || []).map(x => `<tr><td><code>${esc(x.ip)}</code></td><td>${esc(x.user)}</td><td>${x.count}</td></tr>`), "ไม่มี")
        + `<div class="nsh">เข้าสำเร็จล่าสุด</div>` + tbl(["เวลา", "User", "จาก", "วิธี"], (lg.recent_ok || []).map(x => `<tr><td>${fmtTime(x.time)}</td><td>${esc(x.user)}</td><td><code>${esc(x.ip || "-")}</code></td><td>${esc(x.type)}</td></tr>`), "ไม่มี");
}
function renderDetail() {
    const box = $("nsDetail");
    if (!N.web) { box.hidden = true; return; }
    box.hidden = false;
    const d = N.m;
    if (!d) { box.innerHTML = `<div class="empty">Loading...</div>`; return; }
    const o = N.ov[d.web] || {};
    const canOp = N.role === "operator" || N.role === "admin";
    box.innerHTML = `<div class="top"><h2>${esc(d.web)} <span class="ostag${d.os === "windows" ? "" : " lx"}">${d.os === "windows" ? "Win" : "Linux"}</span> <small class="st-mut">${esc([d.group, d.host].filter(Boolean).join(" · "))}</small></h2>
<span class="nsmeta">${d.collected_at ? `เก็บเมื่อ ${esc(ago(d.collected_at))} ที่แล้ว` : d.source === "windows" ? "อัปเดตทุก 5 นาที" : ""}${canOp && d.os !== "windows" ? ` <button type="button" class="svbtn sm" id="nsRefresh">ดึงใหม่</button>` : ""} <a class="svbtn sm" href="/?web=${encodeURIComponent(d.web)}">หน้าเครื่อง</a> <button type="button" class="svbtn sm" id="nsClose">ปิด</button></span></div>
<div class="nsnet">${[["Traffic", o.net != null ? Number(o.net).toFixed(2) + " MB/s" : "-"], ["Ping", o.ping_ms != null ? Math.round(o.ping_ms) + " ms" : "-"], ["Packet loss", o.packet_loss != null ? Number(o.packet_loss).toFixed(1) + "%" : "-"], ["Availability 24h", o.availability != null ? Number(o.availability).toFixed(2) + "%" : "-"], ["ขาเข้า", `${d.summary.in} (${d.summary.peers_in} IP)`], ["ขาออก", `${d.summary.out} (${d.summary.peers_out} IP)`]].map(([k, v]) => `<div><small>${k}</small><b>${esc(v)}</b></div>`).join("")}</div>
${!d.available ? `<div class="nsnote">ไม่สามารถดึงข้อมูล Network &amp; Security ของเครื่องนี้ได้</div>` : ""}
<div class="vseg nstabs">${TABS.map(([k, l]) => `<button type="button" data-tab="${k}" class="${N.tab === k ? "on" : ""}">${l}</button>`).join("")}</div>
<div class="nsbody">${detailBody(d)}</div>`;
    $("nsClose").onclick = () => selectWeb(null);
    const rf = $("nsRefresh");
    if (rf) rf.onclick = async () => {
        rf.disabled = true; rf.textContent = "กำลังดึง...";
        try { await getJson(`/api/netsec/${encodeURIComponent(d.web)}/refresh`, { method: "POST" }); setTimeout(() => loadMachine(true), 25000); rf.textContent = "สั่งแล้ว รอ ~30 วินาที"; }
        catch (e) { rf.textContent = "ไม่สำเร็จ"; }
    };
}
async function loadMachine() {
    if (!N.web) return renderDetail();
    try { N.m = await getJson(`/api/netsec/${encodeURIComponent(N.web)}`); } catch (e) { N.m = null; $("nsDetail").innerHTML = `<div class="empty">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`; return; }
    renderDetail();
}
function selectWeb(web) {
    N.web = web === N.web ? null : web; N.m = null;
    const u = new URL(location.href);
    if (N.web) u.searchParams.set("web", N.web); else u.searchParams.delete("web");
    history.replaceState(null, "", u.pathname + u.search);
    renderMain(); renderDetail(); loadMachine();
    if (N.web) setTimeout(() => $("nsDetail").scrollIntoView({ behavior: "smooth", block: "start" }), 50);
}

async function load() {
    try {
        const [d, ov] = await Promise.all([getJson("/api/netsec"), getJson("/api/overview").catch(() => ({}))]);
        N.d = d; N.ov = Object.fromEntries((ov.servers || []).map(s => [s.web, s]));
        renderTiles(); renderMain();
    } catch (e) { if (e.message !== "auth") $("nsMain").innerHTML = `<div class="empty">โหลดไม่สำเร็จ: ${esc(e.message)}</div>`; }
}

document.addEventListener("DOMContentLoaded", async () => {
    try { const me = await getJson("/api/me"); $("currentUsername").textContent = me.username || "?"; N.role = me.role || "viewer"; } catch (e) { return; }
    const p = new URLSearchParams(location.search);
    N.web = p.get("web") || null;
    try { N.view = sessionStorage.getItem("cm.nsView") || "map"; } catch (e) { /* ignore */ }
    $("nsView").addEventListener("click", e => { const b = e.target.closest("button[data-v]"); if (!b) return; N.view = b.dataset.v; try { sessionStorage.setItem("cm.nsView", N.view); } catch (err) { /* ignore */ } renderMain(); });
    $("nsFind").addEventListener("input", e => { N.q = e.target.value.trim().toLowerCase(); renderMain(); renderDetail(); });
    $("nsMain").addEventListener("click", e => {
        if (e.target.closest("[data-clear]")) { e.preventDefault(); selectWeb(N.web); return; }
        if (e.target.matches("[data-ext]")) { N.showExt = e.target.checked; renderMain(); return; }
        const n = e.target.closest("[data-web]"); if (n) selectWeb(n.dataset.web);
    });
    $("nsMain").addEventListener("keydown", e => { if (e.key === "Enter" && e.target.dataset.web) selectWeb(e.target.dataset.web); });
    $("nsDetail").addEventListener("click", e => { const b = e.target.closest("button[data-tab]"); if (b) { N.tab = b.dataset.tab; renderDetail(); } });
    $("logoutButton").addEventListener("click", async () => { try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } finally { location.href = "/login"; } });
    await load(); loadMachine();
    setInterval(() => { if (!document.hidden) { load(); if (N.web) loadMachine(); } }, 60000);
});
