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

/* ---------------- แผนผังเครือข่าย (แบบ topology) ----------------
   Internet ── Gateway (router) ── Switch ของแต่ละวง (subnet หรือ กลุ่ม) ── เครื่อง (ไอคอน server = Linux, PC = Windows)
   ในกรอบของแต่ละวง: เครื่องที่ monitor (สีตามผลตรวจ) + IP ในวงเดียวกันที่คุยกับเครื่องเราแต่ไม่ได้ monitor (สีเทา)
   เส้นระหว่างเครื่อง = การเชื่อมต่อ (A → B = A ต่อเข้าพอร์ตของ B) · คลิกเครื่อง = แสดงเส้นทั้งหมดของเครื่องนั้น + รายละเอียด
   ข้อมูล IP / subnet / gateway มาจากตัวเครื่อง (ip addr / ip route, Get-NetIPAddress / Get-NetRoute) ไม่ใช่จาก switch จริง */
function inNet(ip, cidr) {
    const n = s => s.split(".").reduce((a, x) => a * 256 + Number(x), 0);
    const [base, bits] = String(cidr || "").split("/");
    if (!base || !/^\d+\.\d+\.\d+\.\d+$/.test(ip || "")) return false;
    const m = Number(bits) === 0 ? 0 : 2 ** 32 - 2 ** (32 - Number(bits));
    return Math.floor(n(ip) / 2 ** (32 - Number(bits))) === Math.floor(n(base) / 2 ** (32 - Number(bits))) && m >= 0;
}
const ICON = {
    server: (x, y, c) => `<g class="ico"><rect x="${x - 17}" y="${y - 24}" width="34" height="48" rx="4" class="body"/><line x1="${x - 11}" x2="${x + 11}" y1="${y - 14}" y2="${y - 14}"/><line x1="${x - 11}" x2="${x + 11}" y1="${y - 6}" y2="${y - 6}"/><line x1="${x - 11}" x2="${x + 11}" y1="${y + 2}" y2="${y + 2}"/><circle cx="${x + 8}" cy="${y + 14}" r="4" fill="${c}"/></g>`,
    pc: (x, y, c) => `<g class="ico"><rect x="${x - 24}" y="${y - 22}" width="48" height="32" rx="3" class="body"/><rect x="${x - 19}" y="${y - 17}" width="38" height="22" rx="1" class="scr" fill="${c}"/><path d="M${x - 6},${y + 10}L${x - 9},${y + 20}H${x + 9}L${x + 6},${y + 10}Z" class="body"/><rect x="${x - 15}" y="${y + 19}" width="30" height="4" rx="2" class="body"/></g>`,
    peer: (x, y) => `<g class="ico peer"><rect x="${x - 13}" y="${y - 11}" width="26" height="17" rx="2" class="body"/><path d="M${x - 4},${y + 6}L${x - 5},${y + 11}H${x + 5}L${x + 4},${y + 6}Z" class="body"/></g>`,
    sw: (x, y, w) => `<g class="ico sw"><path d="M${x - w / 2 + 10},${y - 9}H${x + w / 2}L${x + w / 2 - 10},${y + 9}H${x - w / 2}Z" class="body"/>${Array.from({ length: Math.floor(w / 14) - 2 }, (_, i) => `<rect x="${x - w / 2 + 14 + i * 12}" y="${y - 2}" width="6" height="4" rx="1" class="port"/>`).join("")}</g>`,
    router: (x, y) => `<g class="ico router"><ellipse cx="${x}" cy="${y + 8}" rx="34" ry="12" class="body"/><rect x="${x - 34}" y="${y - 6}" width="68" height="14" class="body side"/><ellipse cx="${x}" cy="${y - 6}" rx="34" ry="12" class="top"/><path d="M${x - 18},${y - 10}l8,4l-8,4M${x + 18},${y - 2}l-8,-4l8,-4M${x - 10},${y - 6}H${x - 22}M${x + 10},${y - 6}H${x + 22}" class="arr"/></g>`,
    cloud: (x, y) => `<g class="ico cloud"><path d="M${x - 70},${y + 22}a26,26 0 0 1 8,-44a34,34 0 0 1 58,-14a30,30 0 0 1 52,12a24,24 0 0 1 22,46z" class="body"/></g>`,
};
const STC = { ok: "#0a9a60", warning: "#f5b400", error: "#e5484d", unknown: "#9aa6bd" };
function mapHtml() {
    const ms = N.d.machines.filter(m => match(m.web, m.host, m.group, m.ip, m.subnet));
    if (!ms.length) return `<div class="empty">ไม่มีเครื่อง</div>`;
    const bySubnet = N.layout !== "group";
    /* วง (กรอบ): subnet หรือ กลุ่ม */
    const boxes = new Map();
    const boxOf = m => bySubnet ? (m.subnet || "?") : (m.group || "");
    ms.forEach(m => { const k = boxOf(m); if (!boxes.has(k)) boxes.set(k, { key: k, ms: [], peers: new Set(), gws: new Set() }); const b = boxes.get(k); b.ms.push(m); if (m.gateway) b.gws.add(m.gateway); });
    /* IP ภายในที่ไม่ได้ monitor -> ใส่ในวงที่ IP อยู่ (subnet) ไม่งั้นกรอบ "LAN อื่น" */
    const lanOther = new Set();
    N.d.external.filter(x => !x.public && x.peer).forEach(x => {
        const b = bySubnet ? [...boxes.values()].find(b => inNet(x.peer, b.key)) : null;
        if (b) b.peers.add(x.peer); else lanOther.add(x.peer);
    });
    if (lanOther.size) boxes.set("__lan", { key: "__lan", ms: [], peers: lanOther, gws: new Set() });
    const list = [...boxes.values()].sort((a, b) => (a.key === "__lan") - (b.key === "__lan") || (a.key === "?") - (b.key === "?") || b.ms.length - a.ms.length || a.key.localeCompare(b.key));
    /* ขนาด / ตำแหน่ง */
    const W = 1120, CW = 140, CH = 112, PEERMAX = 8, X0 = 44, TOP = 280, GAP = 56;
    let x = X0, y = TOP, rowH = 0, row = 0;
    list.forEach(b => {
        b.peerList = [...b.peers].sort();
        const shownPeers = Math.min(b.peerList.length, PEERMAX);
        const n = b.ms.length + shownPeers + (b.peerList.length > PEERMAX ? 1 : 0);
        b.cols = Math.max(1, Math.min(n, n > 6 ? 4 : 3));
        b.rows = Math.ceil(n / b.cols);
        b.w = Math.max(260, b.cols * CW + 24); b.h = 80 + b.rows * CH;
        if (x + b.w > W - 16 && x > X0) { x = X0; y += rowH + GAP; rowH = 0; row++; }
        b.x = x; b.y = y; b.row = row; x += b.w + 22; rowH = Math.max(rowH, b.h);
        b.sx = b.x + b.w / 2; b.sy = b.y + 52;
    });
    const H = y + rowH + 24;
    const pos = {};
    list.forEach(b => {
        let i = 0;
        const cell = () => { const c = i % b.cols, r = Math.floor(i / b.cols); i++; return [b.x + 12 + c * CW + CW / 2, b.y + 80 + r * CH + 34]; };
        b.cells = [];
        b.ms.forEach(m => { const p = cell(); pos[m.web] = p; b.cells.push({ m, p }); });
        b.peerList.slice(0, PEERMAX).forEach(ip => { const p = cell(); pos["ip:" + ip] = p; b.cells.push({ ip, p }); });
        if (b.peerList.length > PEERMAX) b.cells.push({ more: b.peerList.length - PEERMAX, p: cell() });
    });
    /* gateway / Internet */
    const gws = [...new Set(list.flatMap(b => [...b.gws]))];
    const gwPos = {};
    gws.forEach(g => {
        const bs = list.filter(b => b.gws.has(g));
        const first = bs.filter(b => b.row === Math.min(...bs.map(x => x.row)));
        gwPos[g] = [first.reduce((a, b) => a + b.sx, 0) / first.length, 175];
    });
    const xs = Object.values(gwPos).sort((a, b) => a[0] - b[0]);
    for (let k = 1; k < xs.length; k++) if (xs[k][0] - xs[k - 1][0] < 170) xs[k][0] = xs[k - 1][0] + 170;   /* กัน router ทับกัน */
    const CLX = W - 150, CLY = 62;
    const sel = N.web;
    /* เส้นโครงสร้าง */
    let lines = "";
    /* gateway -> switch ของวง: แถวแรกลงตรง ๆ · แถวถัดไปอ้อมทางช่องซ้าย (ไม่ทับกรอบ) แล้วเลี้ยวเข้าช่องว่างเหนือแถวนั้น */
    gws.forEach((g, gi) => {
        const [gx, gy] = gwPos[g];
        lines += `<path class="tl wan" d="M${gx},${gy - 18} C${gx},${CLY + 40} ${CLX - 120},${CLY} ${CLX - 70},${CLY + 6}"/>`;
        list.filter(b => b.gws.has(g)).forEach(b => {
            const lane = b.y - 18 - gi * 5;
            if (b.row === 0) lines += `<path class="tl" d="M${gx},${gy + 20} V${lane} H${b.sx} V${b.sy - 10}"/>`;
            else {
                const cx = 14 + gi * 7;
                lines += `<path class="tl" d="M${gx},${gy + 20} V${TOP - 30 - gi * 5} H${cx} V${lane} H${b.sx} V${b.sy - 10}"/>`;
            }
        });
    });
    /* เส้นการเชื่อมต่อ */
    let conns = "";
    const curve = (a, b, cls, tip, lab) => {
        const [x1, y1] = a, [x2, y2] = b, mx = (x1 + x2) / 2, my = Math.min(y1, y2) - 30 - Math.abs(x2 - x1) * 0.08;
        return `<g class="tc ${cls}"><path d="M${x1},${y1 - 26} Q${mx},${my} ${x2},${y2 - 30}" marker-end="url(#tArr${cls.includes("on") ? "O" : ""})"><title>${esc(tip)}</title></path>${lab ? `<text x="${mx}" y="${(my + Math.min(y1, y2)) / 2 - 4}" text-anchor="middle">${esc(lab)}</text>` : ""}</g>`;
    };
    const pairs = {};
    N.d.edges.filter(e => pos[e.from] && pos[e.to]).forEach(e => { const k = e.from + "→" + e.to, p = pairs[k] || (pairs[k] = { from: e.from, to: e.to, ports: new Set(), count: 0 }); p.ports.add(e.port); p.count += e.count; });
    Object.values(pairs).forEach(p => {
        const on = sel && (p.from === sel || p.to === sel);
        if (sel ? !on : !N.allLines) return;               /* ปกติแสดงเฉพาะเส้นของเครื่องที่เลือก (กดแสดงทั้งหมดได้) */
        const ports = [...p.ports].sort((a, b) => a - b);
        conns += curve(pos[p.from], pos[p.to], on ? "on" : "", `${p.from} → ${p.to} · พอร์ต ${ports.join(", ")} · ${p.count} การเชื่อมต่อ`, ports.slice(0, 3).map(x => ":" + x).join(" "));
    });
    if (sel && pos[sel]) {          /* เครื่องที่เลือก: เส้นไป IP ที่ไม่ได้ monitor / Internet */
        const pub = { in: new Set(), out: new Set() };
        N.d.external.filter(x => x.web === sel).forEach(x => {
            if (x.public) { pub[x.dir].add(x.port); return; }
            const pp = pos["ip:" + x.peer];
            if (pp) conns += x.dir === "in" ? curve(pp, pos[sel], "on lan", `${x.peer} → ${sel} :${x.port}`, ":" + x.port) : curve(pos[sel], pp, "on lan", `${sel} → ${x.peer} :${x.port}`, ":" + x.port);
        });
        const [sx, sy] = pos[sel];
        if (pub.in.size || pub.out.size) conns += `<g class="tc on pub"><path d="M${sx},${sy - 26} C${sx},${CLY + 120} ${CLX - 40},${CLY + 90} ${CLX - 20},${CLY + 30}"><title>Internet: ขาออก ${[...pub.out].join(", ")} · ขาเข้า ${[...pub.in].join(", ")}</title></path></g>`;
    }
    /* กรอบ + ไอคอน */
    const boxHtml = b => {
        const title = b.key === "__lan" ? "LAN อื่น (ไม่ได้ monitor)" : b.key === "?" ? "ไม่ทราบ subnet" : bySubnet ? b.key : (b.key || "ไม่มีกลุ่ม");
        const sub = b.key === "__lan" ? `${b.peerList.length} IP` : [b.gws.size ? `Gateway ${[...b.gws].join(", ")}` : "", `${b.ms.length} เครื่อง`].filter(Boolean).join(" · ");
        return `<g class="tbox${b.key === "__lan" ? " lan" : ""}"><rect x="${b.x}" y="${b.y}" width="${b.w}" height="${b.h}" rx="10"/><text x="${b.x + 12}" y="${b.y + 20}" class="bt">${esc(title)}</text><text x="${b.x + 12}" y="${b.y + 36}" class="bs">${esc(sub)}</text>`
            + (b.key === "__lan" || b.key === "?" ? "" : ICON.sw(b.sx, b.sy, Math.min(150, b.w - 40)))
            + b.cells.map(({ m, ip, more, p }) => {
                const [cx, cy] = p;
                if (more) return `<text x="${cx}" y="${cy}" text-anchor="middle" class="ml">+${more} IP</text>`;
                if (ip) return `<g class="tpeer">${ICON.peer(cx, cy)}<text x="${cx}" y="${cy + 26}" text-anchor="middle" class="ip">${esc(ip)}</text><title>${esc(ip)} · คุยกับเครื่องที่ monitor แต่ไม่ได้ monitor</title></g>`;
                const c = m.available ? STC[m.worst] || STC.unknown : STC.unknown;
                const subl = !m.available ? "ไม่มีข้อมูล" : [m.firewall === "inactive" ? "FW ปิด" : "", m.failed_logins >= 20 ? `login ✕${m.failed_logins}` : "", m.peers_out ? `↗${m.peers_out}` : "", m.peers_in ? `↙${m.peers_in}` : ""].filter(Boolean).join(" ");
                return `<g class="tnode${sel === m.web ? " on" : ""}${sel && sel !== m.web ? " dim" : ""}" data-web="${esc(m.web)}" tabindex="0">`
                    + `<rect x="${cx - 62}" y="${cy - 36}" width="124" height="104" rx="8" class="hit"/>`
                    + (m.os === "windows" ? ICON.pc(cx, cy, c) : ICON.server(cx, cy, c))
                    + `<text x="${cx}" y="${cy + 40}" text-anchor="middle" class="nm">${esc(m.web)}</text><text x="${cx}" y="${cy + 54}" text-anchor="middle" class="ip">${esc(m.ip || m.host || "-")}</text>`
                    + (subl ? `<text x="${cx}" y="${cy + 67}" text-anchor="middle" class="sb${m.firewall === "inactive" || !m.available ? " cr" : ""}">${esc(subl)}</text>` : "")
                    + `<title>${esc(`${m.web} · ${m.os === "windows" ? "Windows" : "Linux"} · ${m.ip || m.host || ""}${m.subnet ? " (" + m.subnet + ")" : ""} · Firewall ${fwText(m.firewall)} · ขาเข้า ${m.in} · ขาออก ${m.out}`)}</title></g>`;
            }).join("") + `</g>`;
    };
    const pubN = new Set(N.d.external.filter(x => x.public).map(x => x.peer)).size;
    return `<div class="tbar"><span class="vseg"><button type="button" data-lay="subnet" class="${bySubnet ? "on" : ""}">จัดตาม Subnet</button><button type="button" data-lay="group" class="${bySubnet ? "" : "on"}">จัดตามกลุ่ม</button></span>
<label class="st-check"><input type="checkbox" data-all ${N.allLines ? "checked" : ""}> แสดงเส้นการเชื่อมต่อระหว่างเครื่องทั้งหมด</label>
<span class="st-mut">คลิกเครื่องเพื่อดูเส้นของเครื่องนั้น (ขาเข้า / ขาออก / Internet)${sel ? ` · <a href="#" data-clear>ยกเลิกเลือก</a>` : ""}</span></div>
<svg class="topo" viewBox="0 0 ${W} ${H}" role="img" aria-label="แผนผังเครือข่าย">
<defs>${[["", "#9aa6bd"], ["O", "#1f7bff"]].map(([k, c]) => `<marker id="tArr${k}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="${c}"/></marker>`).join("")}</defs>
${ICON.cloud(CLX, CLY)}<text x="${CLX}" y="${CLY + 4}" text-anchor="middle" class="cl">Internet</text><text x="${CLX}" y="${CLY + 20}" text-anchor="middle" class="cls">${pubN} IP สาธารณะ</text>
${lines}
${gws.map(g => `<g class="tgw">${ICON.router(gwPos[g][0], gwPos[g][1])}<text x="${gwPos[g][0]}" y="${gwPos[g][1] + 40}" text-anchor="middle" class="nm">Gateway</text><text x="${gwPos[g][0]}" y="${gwPos[g][1] + 54}" text-anchor="middle" class="ip">${esc(g)}</text></g>`).join("")}
${list.map(boxHtml).join("")}
${conns}
</svg>
<div class="nslegend"><span><i style="background:${STC.ok}"></i>ผ่านทุกข้อ</span><span><i style="background:${STC.warning}"></i>มีข้อควรระวัง</span><span><i style="background:${STC.error}"></i>มีปัญหา</span><span><i style="background:${STC.unknown}"></i>ไม่มีข้อมูล / ไม่ได้ monitor</span><span>↗ ต่อออกไปกี่ IP · ↙ มีกี่ IP ต่อเข้ามา · A → B = A ต่อเข้าพอร์ตของ B</span></div>`;
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
    try { N.layout = localStorage.getItem("cm.nsLayout") || "subnet"; } catch (e) { /* ignore */ }
    $("nsMain").addEventListener("click", e => {
        const l = e.target.closest("[data-lay]");
        if (l) { N.layout = l.dataset.lay; try { localStorage.setItem("cm.nsLayout", N.layout); } catch (err) { /* ignore */ } renderMain(); return; }
        if (e.target.closest("[data-clear]")) { e.preventDefault(); selectWeb(N.web); return; }
        if (e.target.matches("[data-all]")) { N.allLines = e.target.checked; renderMain(); return; }
        const n = e.target.closest("[data-web]"); if (n) selectWeb(n.dataset.web);
    });
    $("nsMain").addEventListener("keydown", e => { if (e.key === "Enter" && e.target.dataset.web) selectWeb(e.target.dataset.web); });
    $("nsDetail").addEventListener("click", e => { const b = e.target.closest("button[data-tab]"); if (b) { N.tab = b.dataset.tab; renderDetail(); } });
    $("logoutButton").addEventListener("click", async () => { try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } finally { location.href = "/login"; } });
    await load(); loadMachine();
    setInterval(() => { if (!document.hidden) { load(); if (N.web) loadMachine(); } }, 60000);
});
