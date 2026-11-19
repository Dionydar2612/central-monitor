"""
netstat_service.py - Netstat Monitor (Apps & Web > Netstat Monitor) ฝั่ง API
=========================================================================

ข้อมูล
    data/state/netstat/live.json    ค่าล่าสุดทุกเครื่อง Windows (puller/netstat.py เขียน ~ทุก 1 วินาที)
    data/state/netstat/alerts.json  alert ที่ยังเปิด + ประวัติ
    Prometheus                      ย้อนหลัง (cm_netstat_conn_peak / cm_netstat_conn / cm_netstat_accept_queue_*)
    puller.yaml targets[].netstat   เปิด / ปิด Monitoring, interval, พอร์ตที่เลือก + เกณฑ์ (set_host)

ไม่มีค่า = None (หน้าเว็บแสดง Unavailable / Unsupported) ไม่แปลงเป็น 0
"""

import json
import os
import time

import targets_service
from clients import prometheus_query_range
from settings import STATE_DIR, load_web_targets
from targets_service import ValidationError

NETSTAT_DIR = os.path.join(STATE_DIR, "netstat")
LIVE_FILE = os.path.join(NETSTAT_DIR, "live.json")
ALERTS_FILE = os.path.join(NETSTAT_DIR, "alerts.json")
INTERVALS = (1, 2, 5)
MAX_PORTS = 200
RANGES = {"15m": 900, "1h": 3600, "6h": 6 * 3600, "24h": 86400}
LIVE_STALE = 15          # live.json ไม่อัปเดตเกินกี่วินาที = puller ไม่ทำงาน

_CACHE = {}


def _read(path, default):
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return default
    hit = _CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return hit[1] if hit else default
    _CACHE[path] = (mtime, data)
    return data


def live_mtime():
    try:
        return os.path.getmtime(LIVE_FILE)
    except OSError:
        return 0.0


def load_live():
    d = _read(LIVE_FILE, {})
    return d if isinstance(d, dict) else {}


def load_alerts():
    d = _read(ALERTS_FILE, {})
    return d if isinstance(d, dict) else {}


def windows_targets():
    return [t for t in load_web_targets() if isinstance(t, dict) and str(t.get("type") or "") == "agent"]


def _ncfg(t):
    n = t.get("netstat") if isinstance(t.get("netstat"), dict) else {}
    return n


# ------------------------------------------------------------------
# Host inventory
# ------------------------------------------------------------------

def agent_status(h, live_updated, now=None):
    """online / offline / stale (agent หยุดส่ง) / no-data (ยังไม่มีข้อมูล Netstat) / unknown (puller ไม่ทำงาน)"""
    now = now or time.time()
    if not h or not live_updated or now - live_updated > LIVE_STALE:
        return "unknown"
    if not h.get("online"):
        return "offline"
    if not h.get("has_data"):
        return "no-data"
    if h.get("stale"):
        return "stale"
    return "online"


def host_rows():
    live = load_live()
    upd = live.get("updated")
    out = []
    for t in windows_targets():
        web, n, h = t.get("name"), _ncfg(t), (live.get("hosts") or {}).get(t.get("name")) or {}
        ports = [p for p in n.get("ports") or [] if isinstance(p, dict)]
        out.append({"web": web, "ip": t.get("host") or "", "group": t.get("group") or "", "os": h.get("os") or "",
                    "mode": (t.get("agent") or {}).get("mode") or "push", "paused": bool(t.get("paused")),
                    "status": "paused" if t.get("paused") else agent_status(h, upd),
                    "last_seen": h.get("last_seen"), "error": h.get("error") or "",
                    "enabled": bool(n.get("enabled")), "interval": int(n.get("interval") or 1),
                    "agent_interval": h.get("agent_interval"), "collect_ms": h.get("collect_ms"),
                    "ports_monitored": sum(1 for p in ports if p.get("enabled", True) is not False),
                    "ports_found": len(h.get("ports") or [])})
    return {"updated": upd, "hosts": out, "puller_ok": bool(upd and time.time() - upd <= LIVE_STALE)}


def host_detail(web):
    t = next((x for x in windows_targets() if x.get("name") == web), None)
    if t is None:
        return None
    live = load_live()
    h = (live.get("hosts") or {}).get(web) or {}
    n = _ncfg(t)
    cfg = {}
    for p in n.get("ports") or []:
        if isinstance(p, dict) and str(p.get("port") or "").isdigit():
            key = f"{'udp' if p.get('proto') == 'udp' else 'tcp'}/{'out' if p.get('dir') == 'out' else 'in'}/{int(p['port'])}"
            cfg[key] = p
    ports = []
    for p in h.get("ports") or []:
        c = cfg.get(p["key"])
        ports.append(dict(p, selected=c is not None and c.get("enabled", True) is not False, configured=c is not None,
                          queue_th=(c or {}).get("queue") or {}, conn_th=(c or {}).get("conn") or {}))
    seen = {p["key"] for p in ports}
    for key, c in cfg.items():                   # เลือกไว้แต่ตอนนี้ไม่พบบนเครื่อง (เช่น service หยุด)
        if key not in seen:
            proto, direction, port = key.split("/")
            ports.append({"key": key, "port": int(port), "proto": proto, "dir": direction, "listening": False,
                          "missing": True, "states": {}, "total": None, "selected": c.get("enabled", True) is not False,
                          "configured": True, "queue_th": c.get("queue") or {}, "conn_th": c.get("conn") or {},
                          "proc": "", "pid": None, "addr": "", "peers": []})
    row = next((r for r in host_rows()["hosts"] if r["web"] == web), {})
    alerts = load_alerts()
    return dict(row, support=h.get("support"), ports=ports, rows=h.get("rows") or [],
                active=[a for a in alerts.get("active") or [] if a.get("web") == web],
                history=[a for a in (alerts.get("history") or [])[-500:] if a.get("web") == web][-50:][::-1])


def _th(v, field, errors):
    v = v if isinstance(v, dict) else {}
    out = {}
    for k in ("warning", "critical"):
        x = v.get(k)
        if x in (None, ""):
            continue
        try:
            x = float(x)
        except (TypeError, ValueError):
            errors[f"{field}.{k}"] = "ใส่ตัวเลข"
            continue
        if x < 0 or x > 10_000_000 or x != x:
            errors[f"{field}.{k}"] = "ต้องเป็น 0 ขึ้นไป"
            continue
        out[k] = int(x) if x.is_integer() else x
    if "warning" in out and "critical" in out and out["critical"] <= out["warning"]:
        errors[f"{field}.critical"] = "Critical ต้องมากกว่า Warning"
    return out


def clean_host(payload):
    """ตรวจค่าที่ส่งมา -> {enabled, interval, ports}  ผิด -> ValidationError({ช่อง: ข้อความ})"""
    p = payload if isinstance(payload, dict) else {}
    errors = {}
    try:
        interval = int(p.get("interval") or 1)
    except (TypeError, ValueError):
        interval = 0
    if interval not in INTERVALS:
        errors["interval"] = "เลือก 1, 2 หรือ 5 วินาที"
    ports, keys = [], set()
    raw = p.get("ports") if isinstance(p.get("ports"), list) else []
    if len(raw) > MAX_PORTS:
        errors["ports"] = f"เลือกได้ไม่เกิน {MAX_PORTS} พอร์ตต่อเครื่อง"
    for i, x in enumerate(raw[:MAX_PORTS]):
        x = x if isinstance(x, dict) else {}
        f = f"ports.{i}"
        try:
            port = int(x.get("port"))
        except (TypeError, ValueError):
            port = 0
        if not 1 <= port <= 65535:
            errors[f"{f}.port"] = "พอร์ต 1-65535"
            continue
        proto = str(x.get("proto") or "tcp").lower()
        direction = str(x.get("dir") or "in")
        if proto not in ("tcp", "udp"):
            errors[f"{f}.proto"] = "tcp หรือ udp"
            continue
        if direction not in ("in", "out"):
            errors[f"{f}.dir"] = "in หรือ out"
            continue
        key = (proto, direction, port)
        if key in keys:
            errors[f"{f}.port"] = f"พอร์ต {port}/{proto} ซ้ำ"
            continue
        keys.add(key)
        item = {"port": port, "proto": proto}
        if direction != "in":
            item["dir"] = direction
        if x.get("enabled", True) is False:
            item["enabled"] = False
        for k in ("queue", "conn"):
            th = _th(x.get(k), f"{f}.{k}", errors)
            if th:
                item[k] = th
        ports.append(item)
    if errors:
        raise ValidationError(errors)
    return {"enabled": bool(p.get("enabled")), "interval": interval, "ports": ports}


def save_host(web, payload):
    if not any(t.get("name") == web for t in windows_targets()):
        return None
    clean = clean_host(payload)
    if targets_service.set_netstat(web, clean) is None:
        return None
    return clean


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------

def _pass(row, f):
    if f.get("host") and row["web"] != f["host"]:
        return False
    if f.get("port") and str(row["port"]) != str(f["port"]):
        return False
    if f.get("proto") and row["proto"] != f["proto"]:
        return False
    st = f.get("status")
    if st and st != "all":
        if st == "alert" and row["status"] not in ("warning", "critical"):
            return False
        if st not in ("alert",) and row["status"] != st:
            return False
    return True


def dashboard(filters=None):
    f = filters or {}
    now = time.time()
    live = load_live()
    upd = live.get("updated")
    hosts = live.get("hosts") or {}
    tmap = {t.get("name"): t for t in windows_targets() if not t.get("paused")}
    rows, states = [], {}
    total_conn, listening, online = 0, 0, 0
    for web, t in tmap.items():
        h = hosts.get(web) or {}
        st = agent_status(h, upd, now)
        if st == "online":
            online += 1
            for p in h.get("ports") or []:
                if p.get("listening"):
                    listening += 1
                total_conn += (p.get("states") or {}).get("established") or 0
        if not _ncfg(t).get("enabled"):
            continue
        for r in h.get("rows") or []:
            if not r.get("enabled", True):
                continue
            row = dict(r, web=web, ip=t.get("host") or "", host_status=st, last_update=h.get("last_seen"),
                       status=r.get("status") if st == "online" else ("offline" if st == "offline" else "stale" if st == "stale" else "unknown"))
            if st != "online":                          # ค่าค้างของเครื่องที่ไม่ตอบ -> ไม่แสดงเป็นค่าปัจจุบัน
                for k in ("conn", "peak", "established", "queue", "accept_q", "util", "syn_backlog"):
                    row[k] = None
                row["states"] = {}
            if _pass(row, f):
                rows.append(row)
                for s, v in (row.get("states") or {}).items():
                    states[s] = states.get(s, 0) + (v or 0)
    alerts = load_alerts()
    active = [a for a in live.get("active") or alerts.get("active") or []
              if (not f.get("host") or a.get("web") == f["host"])]
    recent = sorted(active + (alerts.get("history") or [])[-100:],
                    key=lambda a: -(a.get("last") or a.get("first") or 0))[:20]
    top_q = sorted([r for r in rows if r.get("queue") is not None], key=lambda r: -(r.get("util") or 0) * 1e6 - r["queue"])[:5]
    top_c = sorted([r for r in rows if r.get("conn") is not None], key=lambda r: -r["conn"])[:5]
    return {
        "updated": upd, "server_time": now, "live": bool(upd and now - upd <= LIVE_STALE),
        "summary": {"hosts": len(tmap), "online": online, "connections": total_conn, "listening": listening,
                    "warning": sum(1 for a in active if a.get("level") == "warning"),
                    "critical": sum(1 for a in active if a.get("level") == "critical")},
        "rows": rows, "states": states, "top_queue": top_q, "top_conn": top_c, "alerts": recent,
        "hosts": sorted(tmap), "active": active,
    }


# ------------------------------------------------------------------
# ย้อนหลัง (Prometheus)
# ------------------------------------------------------------------

def _range(spec, start, end, now=None):
    now = now or time.time()
    if start not in (None, ""):
        try:
            s, e = float(start), float(end) if end not in (None, "") else now
        except (TypeError, ValueError):
            raise ValidationError({"range": "ช่วงเวลาไม่ถูกต้อง"})
        if e <= s or e - s > 31 * 86400:
            raise ValidationError({"range": "ช่วงเวลาไม่ถูกต้อง (ไม่เกิน 31 วัน)"})
        return s, e
    sec = RANGES.get(spec or "1h")
    if not sec:
        raise ValidationError({"range": "เลือก 15m / 1h / 6h / 24h หรือกำหนดเอง"})
    return now - sec, now


def _esc(v):
    return str(v).replace("\\", "").replace('"', "")


def _series(query, s, e, step):
    try:
        res = prometheus_query_range(query, s, e, step).get("data", {}).get("result", [])
    except Exception:
        return None
    return res


def _points(res):
    if not res:
        return []
    return [[float(t), float(v)] for t, v in res[0].get("values") or []]


def history(web, key, range_spec="1h", start=None, end=None):
    if not any(t.get("name") == web for t in windows_targets()):
        return None
    try:
        proto, direction, port = str(key).split("/")
        port = int(port)
    except (ValueError, TypeError):
        raise ValidationError({"key": "พอร์ตไม่ถูกต้อง"})
    s, e = _range(range_spec, start, end)
    step = max(15, int((e - s) / 240))
    sel = f'cm_web="{_esc(web)}",dir="{_esc(direction)}",port="{port}"'
    qsel = f'cm_web="{_esc(web)}",port="{port}"'
    out = {"web": web, "key": key, "start": s, "end": e, "step": step}
    conn = _series(f"max(cm_netstat_conn_peak{{{sel}}})", s, e, step)
    out["available"] = conn is not None
    out["conn"] = _points(conn)
    out["established"] = _points(_series(f'sum(cm_netstat_conn{{{sel},state="established"}})', s, e, step))
    st = _series(f"sum by (state) (cm_netstat_conn{{{sel}}})", s, e, step) or []
    out["states"] = {r["metric"].get("state", "?"): [[float(t), float(v)] for t, v in r.get("values") or []] for r in st}
    q = _points(_series(f"max(cm_netstat_accept_queue_peak{{{qsel}}})", s, e, step)) if direction == "in" else []
    lim = _points(_series(f"max(cm_netstat_accept_queue_limit{{{qsel}}})", s, e, step)) if q else []
    out["queue"] = q
    lm = {t: v for t, v in lim}
    out["util"] = [[t, round(v * 100.0 / lm[t], 2)] for t, v in q if lm.get(t)]
    out["queue_supported"] = bool(q)
    hist = load_alerts().get("history") or []
    act = load_alerts().get("active") or []
    out["alerts"] = [a for a in hist + act if a.get("web") == web and (a.get("port") in (None, port))
                     and (a.get("recovered_at") or e) >= s and (a.get("first") or 0) <= e][-200:][::-1]
    return out


def alerts(status="active", web=None, limit=200):
    d = load_alerts()
    rows = [dict(a) for a in d.get("active") or []]
    if status != "active":
        rows += [dict(a) for a in d.get("history") or []]
    if web:
        rows = [a for a in rows if a.get("web") == web]
    now = time.time()
    for a in rows:
        a.setdefault("duration", round((a.get("recovered_at") or now) - (a.get("first") or now), 1))
    rows.sort(key=lambda a: -(a.get("last") or a.get("first") or 0))
    return rows[:limit]
