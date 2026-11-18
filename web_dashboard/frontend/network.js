/* =====================================================================
   network.js - หน้า Network & Security (ดู network.html)
     แผนผัง: วงกลม = เครื่องที่ monitor (สีตามผลตรวจแย่สุด) · ลูกศร A → B = A ต่อเข้าพอร์ตของ B (ป้าย = พอร์ต)
             Internet = IP สาธารณะ · LAN อื่น = IP ภายในที่ไม่ได้ monitor
     ข้อมูลมาจากตัวเครื่องเอง (ไม่ใช่ flow จาก switch / router): Linux ทุกรอบสถานะของ puller · Windows ทุก 5 นาที
   ===================================================================== */
"use strict";
const $ = id => document.getElementById(id);
const N = { d: null, ov: {}, view: "map", web: null, tab: "checks", m: null, q: "" };
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
const fwText = s => ({ active: "เปิด", inactive: "ปิด", unknown: "ไม่ทราบ" }[s] || s || "-");
const fwCls = s => s === "active" ? "ok" : s === "inactive" ? "cr" : "na";
const pill = (txt, cls) => `<span class="ovst ${cls}">${esc(txt)}</span>`;
const match = (...v) => !N.q || v.some(x => String(x ?? "").toLowerCase().includes(N.q));

/* ---------------- ไทล์ ---------------- */
function renderTiles() {
    const t = N.d.totals || {};
    const tile = (label, n, cls, sub) => `<div class="ovtile ${cls}"><small>${label}</small><b>${n ?? "-"}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
    $("nsTiles").innerHTML = tile("เครื่อง", t.machines, "tot", `${N.d.edges.length} เส้นเชื่อมระหว่างเครื่อง`)
        + tile("Firewall ปิด", t.fw_off, t.fw_off ? "dn" : "up", t.fw_unknown ? `อ่านไม่ได้ ${t.fw_unknown} เครื่อง` : "ทุกเครื่องอ่านได้")
        + tile("Login ไม่สำเร็จ 24h", t.failed_logins, t.failed_logins >= 100 ? "dn" : t.failed_logins >= 20 ? "nok" : "up", "SSH / Windows logon")
        + tile("ขาเข้า", t.conn_in, "tot", "การเชื่อมต่อที่ค้างอยู่")
        + tile("ขาออก", t.conn_out, "tot", "การเชื่อมต่อที่ค้างอยู่")
        + tile("IP สาธารณะ", t.public_peers, t.public_peers ? "nok" : "up", "ที่คุยกับเครื่องเรา");
}

/* ---------------- แผนผัง ---------------- */
function mapHtml() {
    const ms = N.d.machines.filter(m => match(m.web, m.host, m.group));
    if (!ms.length) return `<div class="empty">ไม่มีเครื่อง</div>`;
    const W = 1000, H = 600, cx = 390, cy = 300, R = ms.length === 1 ? 0 : Math.min(250, 70 + ms.length * 22);
    const pos = {};
    ms.forEach((m, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / ms.length; pos[m.web] = [cx + R * Math.cos(a), cy + R * Math.sin(a)]; });
    pos.__inet = [880, 170]; pos.__lan = [880, 430];
    /* รวมเส้นต่อคู่เครื่อง */
    const pairs = {};
    N.d.edges.filter(e => pos[e.from] && pos[e.to]).forEach(e => {
        const k = e.from + "→" + e.to, p = pairs[k] || (pairs[k] = { from: e.from, to: e.to, ports: new Set(), count: 0, procs: new Set() });
        p.ports.add(e.port); p.count += e.count; (e.procs || []).forEach(x => p.procs.add(x));
    });
    N.d.external.filter(x => pos[x.web]).forEach(x => {
        const node = x.public ? "__inet" : "__lan", from = x.dir === "in" ? node : x.web, to = x.dir === "in" ? x.web : node;
        const k = from + "→" + to, p = pairs[k] || (pairs[k] = { from, to, ports: new Set(), count: 0, procs: new Set(), ext: true, pub: x.public, peers: new Set() });
        p.ports.add(x.port); p.count += x.count; p.peers.add(x.peer);
    });
    const sel = N.web;
    const r = 26;
    const edge = p => {
        const [x1, y1] = pos[p.from], [x2, y2] = pos[p.to], dx = x2 - x1, dy = y2 - y1, L = Math.hypot(dx, dy) || 1;
        const nx = -dy / L, ny = dx / L, bend = pairs[p.to + "→" + p.from] ? 28 : 12;   /* มีทั้ง 2 ทาง -> โค้งแยกกัน */
        const mx = (x1 + x2) / 2 + nx * bend, my = (y1 + y2) / 2 + ny * bend;
        const sx = x1 + dx / L * r, sy = y1 + dy / L * r, ex = x2 - dx / L * (r + 6), ey = y2 - dy / L * (r + 6);
        const on = sel && (p.from === sel || p.to === sel), dim = sel && !on;
        const cls = `nsE${p.ext ? (p.pub ? " pub" : " lan") : ""}${on ? " on" : ""}${dim ? " dim" : ""}`;
        const ports = [...p.ports].sort((a, b) => a - b), lab = ports.slice(0, 4).map(x => ":" + x).join(" ") + (ports.length > 4 ? ` +${ports.length - 4}` : "");
        const tip = `${p.from.replace(/^__inet$/, "Internet").replace(/^__lan$/, "LAN อื่น")} → ${p.to.replace(/^__inet$/, "Internet").replace(/^__lan$/, "LAN อื่น")} · พอร์ต ${ports.join(", ")} · ${p.count} การเชื่อมต่อ`
            + (p.procs && p.procs.size ? ` · ${[...p.procs].join(", ")}` : "") + (p.peers ? ` · ${[...p.peers].slice(0, 8).join(", ")}${p.peers.size > 8 ? " ..." : ""}` : "");
        return `<g class="${cls}"><path d="M${sx.toFixed(1)},${sy.toFixed(1)} Q${mx.toFixed(1)},${my.toFixed(1)} ${ex.toFixed(1)},${ey.toFixed(1)}" marker-end="url(#nsArr${p.ext && p.pub ? "P" : on ? "O" : ""})"><title>${esc(tip)}</title></path>`
            + `<text x="${mx.toFixed(1)}" y="${(my - 4).toFixed(1)}" text-anchor="middle">${esc(lab)}</text></g>`;
    };
    const node = m => {
        const [x, y] = pos[m.web], w = m.worst || "unknown";
        const sub = !m.available ? "ยังไม่มีข้อมูล" : [m.firewall === "inactive" ? "FW ปิด" : m.firewall === "unknown" ? "FW อ่านไม่ได้" : "", m.failed_logins >= 20 ? `login ✕${m.failed_logins}` : ""].filter(Boolean).join(" · ");
        return `<g class="nsN${sel === m.web ? " on" : ""}${sel && sel !== m.web ? " dim" : ""}" data-web="${esc(m.web)}" tabindex="0"><circle cx="${x}" cy="${y}" r="${r}" fill="${COL[w]}"/>`
            + `<text x="${x}" y="${y + 5}" text-anchor="middle" class="os">${m.os === "windows" ? "Win" : "Linux"}</text>`
            + `<text x="${x}" y="${y + r + 16}" text-anchor="middle" class="nm">${esc(m.web)}</text>`
            + (sub ? `<text x="${x}" y="${y + r + 30}" text-anchor="middle" class="sb">${esc(sub)}</text>` : "")
            + `<title>${esc(`${m.web} ${m.host || ""} · Firewall ${fwText(m.firewall)} · ขาเข้า ${m.in} · ขาออก ${m.out}${m.available ? "" : " · ยังไม่มีข้อมูล"}`)}</title></g>`;
    };
    const virt = (k, label, sub) => `<g class="nsV"><rect x="${pos[k][0] - 62}" y="${pos[k][1] - 24}" width="124" height="48" rx="24"/><text x="${pos[k][0]}" y="${pos[k][1] - 2}" text-anchor="middle" class="nm">${label}</text><text x="${pos[k][0]}" y="${pos[k][1] + 14}" text-anchor="middle" class="sb">${sub}</text></g>`;
    const ext = N.d.external, inet = new Set(ext.filter(x => x.public).map(x => x.peer)).size, lan = new Set(ext.filter(x => !x.public).map(x => x.peer)).size;
    return `<svg class="nsmap" viewBox="0 0 ${W} ${H}" role="img" aria-label="แผนผังการเชื่อมต่อ">
<defs>${[["", "#9aa6bd"], ["O", "#1f7bff"], ["P", "#e08a00"]].map(([k, c]) => `<marker id="nsArr${k}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0L10,5L0,10z" fill="${c}"/></marker>`).join("")}</defs>
${Object.values(pairs).map(edge).join("")}
${virt("__inet", "Internet", `${inet} IP สาธารณะ`)}${virt("__lan", "LAN อื่น", `${lan} IP ที่ไม่ได้ monitor`)}
${ms.map(node).join("")}</svg>
<div class="nslegend"><span><i style="background:${COL.ok}"></i>ผ่านทุกข้อ</span><span><i style="background:${COL.warning}"></i>มีข้อควรระวัง</span><span><i style="background:${COL.error}"></i>มีปัญหา (เช่น firewall ปิด)</span><span><i style="background:${COL.unknown}"></i>ยังไม่มีข้อมูล (คลิกดูสาเหตุ)</span><span>A → B = A ต่อเข้าพอร์ตของ B · ชี้ที่เส้นเพื่อดูรายละเอียด · คลิกเครื่องเพื่อดูทั้งหมด</span></div>`;
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
                + `<td>${m.available ? pill(wl, wc) : `<span class="st-mut">ยังไม่มีข้อมูล</span>`}</td><td>${pill(fwText(m.firewall), fwCls(m.firewall))} <small class="st-mut">${esc(m.fw_tool)}</small></td>`
                + `<td>${esc(m.default_in || "-")}</td><td>${m.listening}</td><td>${m.in} <small class="st-mut">(${m.peers_in} IP)</small></td><td>${m.out} <small class="st-mut">(${m.peers_out} IP)</small></td>`
                + `<td class="ovv ${fl == null ? "" : fl >= 100 ? "cr" : fl >= 20 ? "wr" : "ok"}">${fl ?? "-"}</td>`
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
        return `<div class="nschecks">${(d.checks || []).map(c => { const [, cls] = ST[c.status] || ST.unknown;
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
        if (!fw) return `<div class="empty">อ่าน firewall ไม่ได้</div>`;
        const df = fw.defaults || {};
        const prof = fw.profiles ? `<div class="nsaddr">Profiles: ${fw.profiles.map(p => `<b>${esc(p.profile)}</b> ${p.enabled ? "เปิด" : "ปิด"} (in ${esc(p.in)} / out ${esc(p.out)})`).join(" · ")}</div>` : "";
        const rows = (fw.rules || []).filter(r => match(r.to, r.from, r.action, r.comment, r.program)).map(r => `<tr><td>${pill(r.action, /ALLOW/.test(r.action) ? "ok" : /LIMIT/.test(r.action) ? "wr" : "cr")}</td><td>${r.dir === "in" ? "⬅ เข้า" : r.dir === "out" ? "➡ ออก" : esc(r.dir)}</td><td><code>${esc(r.to)}</code></td><td>${esc(r.from)}</td><td>${esc([r.comment, r.program].filter(Boolean).join(" · ") || "-")}</td></tr>`);
        return `<div class="nsfw"><div>${pill(`Firewall ${fwText(fw.status)}`, fwCls(fw.status))} <span class="st-mut">${esc(fw.tool)}</span></div><div>ขาเข้าปริยาย <b>${esc(df.in || "-")}</b></div><div>ขาออกปริยาย <b>${esc(df.out || "-")}</b></div>${df.routed ? `<div>Forward <b>${esc(df.routed)}</b></div>` : ""}</div>`
            + (fw.note ? `<div class="nsnote">${esc(fw.note)}</div>` : "") + prof
            + tbl(["Action", "ทิศทาง", "พอร์ต / ปลายทาง", "จาก", "ชื่อ / หมายเหตุ"], rows, "ไม่มีกฎ (หรืออ่านรายการกฎไม่ได้)");
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
    if (!lg) return `<div class="empty">ไม่มีข้อมูล login</div>`;
    return `<div class="nsfw"><div>ไม่สำเร็จ <b class="${lg.failed >= 100 ? "cr" : lg.failed >= 20 ? "wr" : ""}">${lg.failed ?? "-"}</b></div>${lg.invalid_users != null ? `<div>user ไม่มีจริง <b>${lg.invalid_users}</b></div>` : ""}${lg.success != null ? `<div>สำเร็จ <b>${lg.success}</b></div>` : ""}<div class="st-mut">${esc(lg.source || "")}</div></div>`
        + (lg.note ? `<div class="nsnote">${esc(lg.note)}</div>` : "")
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
${d.note ? `<div class="nsnote">${esc(d.note)}</div>` : ""}
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
    $("nsMain").addEventListener("click", e => { const n = e.target.closest("[data-web]"); if (n) selectWeb(n.dataset.web); });
    $("nsMain").addEventListener("keydown", e => { if (e.key === "Enter" && e.target.dataset.web) selectWeb(e.target.dataset.web); });
    $("nsDetail").addEventListener("click", e => { const b = e.target.closest("button[data-tab]"); if (b) { N.tab = b.dataset.tab; renderDetail(); } });
    $("logoutButton").addEventListener("click", async () => { try { await fetch("/api/logout", { method: "POST", credentials: "same-origin" }); } finally { location.href = "/login"; } });
    await load(); loadMachine();
    setInterval(() => { if (!document.hidden) { load(); if (N.web) loadMachine(); } }, 60000);
});
