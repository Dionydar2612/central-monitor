"""
netsec_service.py - หน้า Network & Security (/network)
======================================================
ต่อเครื่อง (GET /api/netsec/<เครื่อง>):
    การเชื่อมต่อขาเข้า / ขาออก (ใครต่อหาใคร), พอร์ตที่เปิดรับ, route, firewall (สถานะ / ค่าเริ่มต้น / กฎ), login (สำเร็จ / ไม่สำเร็จ)
    + ผลตรวจความปลอดภัยพื้นฐาน (checks) เช่น firewall เปิดไหม, ขาเข้าปริยายเป็น deny ไหม, login ไม่สำเร็จเยอะผิดปกติไหม
ภาพรวม (GET /api/netsec):
    ทุกเครื่อง: สรุปต่อเครื่อง + เส้นเชื่อมระหว่างเครื่องที่ monitor (จับคู่ IP ปลายทางกับ host / IP ของเครื่อง) + ปลายทางภายนอก

ที่มาของข้อมูล
    Linux   puller/netsec.py ผ่าน SSH ทุกรอบสถานะ (scheduler.interval) -> data/state/system/<เครื่อง>.json "netsec"
            (กด "ดึงใหม่" ในหน้าเว็บ = สั่ง puller ดึงเครื่องนั้นทันที)
อ่านอย่างเดียว ไม่แก้ firewall / route บนเครื่อง
"""

import ipaddress
import os
import time
from concurrent.futures import ThreadPoolExecutor

from settings import load_web_targets
from system_state import SYSTEM_DIR, _load_json

FAILED_LOGIN_WARN = 20          # login ไม่สำเร็จใน 24 ชม. >= ค่านี้ = เตือน
FAILED_LOGIN_ERROR = 100
_CACHE = {"at": 0, "data": None}
CACHE_TTL = 20


def _private(ip):
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return False


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ Linux (snapshot ของ puller)

def _linux(web):
    raw = _load_json(os.path.join(SYSTEM_DIR, f"{web}.json"))
    n = raw.get("netsec") or {}
    out = {"addrs": n.get("addrs") or [], "routes": n.get("routes") or [], "listening": n.get("listening") or [],
           "connections": n.get("connections") or [], "firewall": n.get("firewall"), "logins": n.get("logins"),
           "source": "ssh", "collected_at": raw.get("timestamp"), "available": bool(n)}
    if not n:
        out["note"] = "ยังไม่มีข้อมูล · puller เก็บทุกรอบสถานะ (scheduler.interval) หรือกด ดึงใหม่"
    return out


# ------------------------------------------------------------------ ผลตรวจ

def _checks(d, target):
    out = []
    fw = d.get("firewall")

    def add(key, status, title, detail=""):
        out.append({"key": key, "status": status, "title": title, "detail": detail})
    if not fw:
        add("firewall", "unknown", "Firewall", "อ่านสถานะไม่ได้")
    else:
        st = fw.get("status")
        add("firewall", "ok" if st == "active" else "error" if st == "inactive" else "unknown",
            f"Firewall ({fw.get('tool') or '-'})", {"active": "เปิดอยู่", "inactive": "ปิดอยู่"}.get(st, "ไม่ทราบ") + (f" · {fw['note']}" if fw.get("note") else ""))
        din = (fw.get("defaults") or {}).get("in")
        if din:
            add("default_in", "ok" if din in ("deny", "reject", "block", "drop") else "warning", "ขาเข้าปริยาย",
                f"{din} (ควรเป็น deny แล้วเปิดเฉพาะพอร์ตที่ใช้)")
    lg = d.get("logins") or {}
    if lg.get("failed") is not None:
        f = lg["failed"]
        add("failed_logins", "error" if f >= FAILED_LOGIN_ERROR else "warning" if f >= FAILED_LOGIN_WARN else "ok",
            "Login ไม่สำเร็จ", f"{f} ครั้ง ({lg.get('source')})"
            + (f" · มากสุดจาก {lg['failed_by'][0]['ip']} ({lg['failed_by'][0]['count']})" if lg.get("failed_by") else ""))
    pub = [x for x in d.get("listening") or [] if not x.get("local_only")]
    risky = [x for x in pub if x.get("port") in (21, 23, 445, 3389, 3306, 5432, 6379, 9200, 27017, 11211)]
    if pub:
        add("open_ports", "warning" if risky else "ok", "พอร์ตที่เปิดรับจากภายนอก",
            f"{len(pub)} พอร์ต" + (f" · ควรจำกัดต้นทาง: {', '.join(str(x['port']) for x in risky[:8])}" if risky else ""))
    ext_in = [c for c in d.get("connections") or [] if c.get("dir") == "in" and c.get("peer") and not _private(c["peer"])]
    if ext_in:
        add("public_inbound", "warning", "มี IP สาธารณะต่อเข้ามา",
            ", ".join(sorted({c['peer'] for c in ext_in})[:6]) + (" ..." if len({c['peer'] for c in ext_in}) > 6 else ""))
    return out


def get_machine(web):
    target = next((t for t in load_web_targets() if t.get("name") == web), None)
    if target is None:
        return None
    d = _linux(web)
    d.update(web=web, os="linux", host=target.get("host"), group=target.get("group") or "")
    names = _ip_index(load_web_targets())
    for c in d["connections"]:
        c["peer_web"] = names.get(c.get("peer"))
        c["external"] = bool(c.get("peer")) and not _private(c["peer"])
    d["checks"] = _checks(d, target)
    d["summary"] = _summary(d)
    return d


def _summary(d):
    c = d.get("connections") or []
    fw = d.get("firewall") or {}
    lg = d.get("logins") or {}
    return {"in": sum(x["count"] for x in c if x.get("dir") == "in"), "out": sum(x["count"] for x in c if x.get("dir") == "out"),
            "peers_in": len({x["peer"] for x in c if x.get("dir") == "in"}), "peers_out": len({x["peer"] for x in c if x.get("dir") == "out"}),
            "listening": len([x for x in d.get("listening") or [] if not x.get("local_only")]),
            "firewall": fw.get("status") or "unknown", "fw_tool": fw.get("tool") or "", "default_in": (fw.get("defaults") or {}).get("in") or "",
            "failed_logins": lg.get("failed"), "routes": len(d.get("routes") or []),
            "worst": _worst(d)}


def _worst(d):
    """ผลตรวจแย่สุด ไม่นับข้อที่ "ไม่ทราบ" (เช่นอ่านกฎ firewall ไม่ได้) ถ้ามีข้ออื่นที่รู้ผล · ไม่มีข้อมูลเลย = unknown"""
    if not d.get("available"):
        return "unknown"
    rank = {"ok": 0, "warning": 2, "error": 3}
    known = [x["status"] for x in d.get("checks") or [] if x["status"] in rank]
    return max(known, key=rank.get) if known else "unknown"


def _ip_index(targets, snaps=None):
    """IP -> ชื่อเครื่อง (host ใน puller.yaml + IPv4 ที่เก็บได้จากเครื่อง)"""
    idx = {}
    for t in targets:
        if t.get("host"):
            idx[str(t["host"])] = t["name"]
    for name, d in (snaps or {}).items():
        for a in d.get("addrs") or []:
            if a.get("ip"):
                idx.setdefault(a["ip"], name)
    return idx


_VIRTUAL_IF = ("lo", "docker", "br-", "veth", "virbr", "cni", "flannel", "cali", "tun", "tap", "isatap", "teredo", "loopback")


def _primary(d, host):
    """IP หลัก / subnet / gateway ของเครื่อง (ใช้วาดแผนผังเครือข่าย)
    เลือก IPv4 ที่อยู่วงเดียวกับ default gateway ก่อน -> ตรงกับ Host ใน Settings -> การ์ดจริงตัวแรก (ไม่นับ docker / bridge / loopback)
    ไม่มีข้อมูลจากเครื่อง: ใช้ Host (ถ้าเป็น IP) + /24"""
    gw = next((r.get("gw") for r in d.get("routes") or [] if r.get("dest") in ("0.0.0.0/0", "default") and r.get("gw")), "") or ""
    addrs = [a for a in d.get("addrs") or [] if a.get("ip") and not str(a["ip"]).startswith(("127.", "169.254."))
             and not str(a.get("if") or "").lower().startswith(_VIRTUAL_IF)]
    pick = None
    try:
        g = ipaddress.ip_address(gw) if gw else None
        for a in addrs:
            if g and g in ipaddress.ip_network(f"{a['ip']}/{a.get('prefix') or 24}", strict=False):
                pick = a
                break
    except ValueError:
        pick = None
    if not pick and host:
        pick = next((a for a in addrs if a["ip"] == host), None)
    if not pick and addrs:
        pick = addrs[0]
    if not pick:
        try:
            ipaddress.ip_address(str(host or ""))
            pick = {"ip": str(host), "prefix": 24, "if": ""}
        except ValueError:
            return {"ip": "", "subnet": "", "gateway": gw}
    try:
        net = str(ipaddress.ip_network(f"{pick['ip']}/{pick.get('prefix') or 24}", strict=False))
    except ValueError:
        net = ""
    return {"ip": pick["ip"], "subnet": net, "gateway": gw, "iface": pick.get("if") or ""}


def get_overview():
    """ทุกเครื่อง: สรุป + เส้น ใครต่อหาใคร (เครื่องที่ monitor) + ปลายทางภายนอกที่ต่อบ่อย"""
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["at"] < CACHE_TTL:
        return _CACHE["data"]
    targets = [t for t in load_web_targets() if t.get("name") and not t.get("paused")]
    with ThreadPoolExecutor(max_workers=8) as pool:
        snaps = dict(zip([t["name"] for t in targets],
                         pool.map(lambda t: _linux(t["name"]), targets)))
    idx = _ip_index(targets, snaps)
    edges, ext = {}, {}
    machines = []
    for t in targets:
        name, d = t["name"], snaps[t["name"]]
        d.update(web=name, os="linux")
        d["checks"] = _checks(d, t)
        machines.append({"web": name, "os": d["os"], "host": t.get("host"), "group": t.get("group") or "",
                         "available": d.get("available"), **_summary(d), **_primary(d, t.get("host"))})
        for c in d.get("connections") or []:
            peer = idx.get(c.get("peer"))
            if peer == name:
                continue
            if peer:
                # in บนเครื่องนี้ = peer ต่อเข้ามา (peer -> name) · out = name -> peer  (พอร์ต = พอร์ตบริการของปลายทาง)
                src, dst = (peer, name) if c["dir"] == "in" else (name, peer)
                e = edges.setdefault((src, dst, c.get("port")), {"from": src, "to": dst, "port": c.get("port"), "by": {}, "procs": set()})
                e["by"][name] = e["by"].get(name, 0) + c["count"]
                if c.get("proc"):
                    e["procs"].add(c["proc"])
            else:
                k = (name, c["dir"], c.get("peer"), c.get("port"))
                x = ext.setdefault(k, {"web": name, "dir": c["dir"], "peer": c.get("peer"), "port": c.get("port"), "count": 0,
                                       "public": not _private(c.get("peer") or ""), "procs": set()})
                x["count"] += c["count"]
                if c.get("proc"):
                    x["procs"].add(c["proc"])
    # เส้นเดียวกันที่เห็นจากทั้ง 2 ฝั่ง (A out -> B และ B in <- A) = นับครั้งเดียว (ค่ามากสุดของ 2 ฝั่ง)
    out_edges = [{"from": e["from"], "to": e["to"], "port": e["port"], "count": max(e["by"].values()),
                  "procs": sorted(e["procs"])[:5], "both_sides": len(e["by"]) > 1} for e in edges.values()]
    externals = sorted(({**x, "procs": sorted(x["procs"])[:5]} for x in ext.values()), key=lambda x: (-x["public"], -x["count"]))[:300]
    data = {"time": now, "machines": machines, "edges": sorted(out_edges, key=lambda e: -e["count"]), "external": externals,
            "totals": {"machines": len(machines), "fw_off": sum(1 for m in machines if m["firewall"] == "inactive"),
                       "fw_unknown": sum(1 for m in machines if m["firewall"] == "unknown"),
                       "failed_logins": sum(m["failed_logins"] or 0 for m in machines),
                       "conn_in": sum(m["in"] for m in machines), "conn_out": sum(m["out"] for m in machines),
                       "public_peers": len({x["peer"] for x in externals if x["public"]})}}
    _CACHE.update(at=now, data=data)
    return data
