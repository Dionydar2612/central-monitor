"""
ntp_service.py - หน้า NTP Monitor (/ntp)
=========================================

ข้อมูล
    NTP server      ผลล่าสุดจาก data/state/ntp.json (puller/ntp_monitor.py ตรวจทุก ntp.interval วินาที)
                    กราฟย้อนหลังจาก Prometheus: cm_ntp_offset_seconds / cm_ntp_rtt_seconds / cm_ntp_up / cm_ntp_stratum
    เครื่อง Linux    sensor "Time sync (NTP)" (sensor_monitor) + กราฟเวลาคลาดจาก node_exporter
                    node_time_seconds - timestamp(node_time_seconds) = นาฬิกาเครื่อง - นาฬิกา central (ms, มีเครื่องหมาย)

ตั้งค่า (admin) -> puller.yaml
    ntp: {interval, offset_warning_ms, offset_error_ms, rtt_warning_ms, servers: [{name, host, port, group, paused, notify}]}
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from ruamel.yaml.comments import CommentedMap, CommentedSeq

import sensors_service
import targets_service
from clients import prometheus_query_range
from metrics_service import _labels, get_web_prometheus_config
from settings import STATE_DIR, load_web_targets

STATE_FILE = os.path.join(STATE_DIR, "ntp.json")
DEFAULTS = {"interval": 30, "offset_warning_ms": 500, "offset_error_ms": 2000, "rtt_warning_ms": 200}
LIMITS = {"interval": (15, 600), "offset_warning_ms": (1, 3600000), "offset_error_ms": (1, 3600000), "rtt_warning_ms": (1, 60000)}
RANGES = {"1h": (3600, 30), "6h": (6 * 3600, 60), "24h": (86400, 300), "3d": (3 * 86400, 900),
          "7d": (7 * 86400, 1800), "30d": (30 * 86400, 7200)}
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$")
HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
MAX_SERVERS = 50
MAX_MACHINES_GRAPH = 40


def _doc():
    return targets_service._load_doc(targets_service._yaml())


def _ntp_cfg(doc=None):
    n = targets_service._plain((doc if doc is not None else _doc()).get("ntp") or {})
    n = n if isinstance(n, dict) else {}
    out = {k: n.get(k, v) for k, v in DEFAULTS.items()}
    out["servers"] = [s for s in n.get("servers") or [] if isinstance(s, dict) and s.get("name")]
    return out


def _state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def get_overview():
    cfg = _ntp_cfg()
    st = _state()
    res = st.get("servers") or {}
    servers = []
    for s in cfg["servers"]:
        r = res.get(s["name"]) or {}
        if r.get("host") not in (None, str(s.get("host"))):
            r = {}
        servers.append({
            "name": s["name"], "host": s.get("host"), "port": s.get("port") or 123, "group": s.get("group") or "",
            "paused": bool(s.get("paused")), "notify": s.get("notify") is not False,
            "status": "paused" if s.get("paused") else (r.get("status") or "pending"),
            "raw_status": r.get("raw_status"), "pending": r.get("pending"), "since": r.get("since"),
            "message": r.get("message") or ("Paused" if s.get("paused") else "waiting for first check"),
            "ok": r.get("ok"), "offset_ms": r.get("offset_ms"), "rtt_ms": r.get("rtt_ms"), "stratum": r.get("stratum"),
            "leap": r.get("leap"), "refid": r.get("refid"), "ref_age_s": r.get("ref_age_s"),
            "root_delay_ms": r.get("root_delay_ms"), "root_dispersion_ms": r.get("root_dispersion_ms"),
            "checked_at": r.get("checked_at"),
        })
    machines = []
    for s in sensors_service.get_sensors().get("sensors") or []:
        if s.get("kind") != "time_offset":
            continue
        det = s.get("details") or {}
        machines.append({"web": s.get("web"), "host": s.get("host"), "group": s.get("group") or "", "id": s.get("id"),
                         "status": s.get("status"), "offset_ms": s.get("value"), "synced": det.get("ntp_synced"),
                         "message": s.get("message"), "since": s.get("since"), "warning": s.get("warning"), "error": s.get("error")})
    machines.sort(key=lambda m: str(m["web"]))
    return {"settings": {k: cfg[k] for k in DEFAULTS}, "servers": servers, "machines": machines,
            "updated_at": st.get("updated_at"), "now": time.time()}


def _series(query, start, end, step, scale=1.0, nd=2):
    try:
        res = prometheus_query_range(query, start, end, step)["data"]["result"]
    except Exception as e:  # noqa: BLE001
        print(f"[NTP HISTORY] {query[:80]}: {e}")
        return []
    if not res:
        return []
    return [[float(t), round(float(v) * scale, nd)] for t, v in res[0]["values"] if v not in ("NaN", "+Inf", "-Inf")]


def _range(key):
    seconds, step = RANGES.get(key, RANGES["24h"])
    end = int(time.time())
    return end - seconds, end, step


def server_history(name, range_key="24h"):
    s = next((x for x in _ntp_cfg()["servers"] if x["name"] == name), None)
    if not s:
        return None
    start, end, step = _range(range_key)
    sel = 'server="%s"' % str(name).replace("\\", "\\\\").replace('"', '\\"')
    q = {"offset": (f"max(cm_ntp_offset_seconds{{{sel}}})", 1000, 2), "rtt": (f"max(cm_ntp_rtt_seconds{{{sel}}})", 1000, 2),
         "up": (f"max(cm_ntp_up{{{sel}}})", 1, 0), "stratum": (f"max(cm_ntp_stratum{{{sel}}})", 1, 0)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        out = dict(zip(q, pool.map(lambda v: _series(v[0], start, end, step, v[1], v[2]), q.values())))
    up = out["up"]
    return {"server": name, "range": range_key if range_key in RANGES else "24h", "start": start, "end": end, "step": step,
            "series": out, "uptime_pct": round(sum(1 for _, v in up if v >= 1) / len(up) * 100, 2) if up else None}


def machines_history(range_key="24h"):
    """เวลาคลาดของทุกเครื่อง Linux (ms, มีเครื่องหมาย: + = เครื่องเดินเร็วกว่า central)"""
    start, end, step = _range(range_key)
    targets = [t for t in load_web_targets() if str(t.get("type") or "ssh") != "winrm" and not t.get("paused")][:MAX_MACHINES_GRAPH]

    def one(t):
        cfg = get_web_prometheus_config(t["name"])
        if not cfg:
            return t["name"], []
        l = _labels(cfg)
        return t["name"], _series(f"max(node_time_seconds{{{l}}} - timestamp(node_time_seconds{{{l}}}))", start, end, step, 1000, 1)

    with ThreadPoolExecutor(max_workers=6) as pool:
        series = {n: s for n, s in pool.map(one, targets) if s}
    return {"range": range_key if range_key in RANGES else "24h", "start": start, "end": end, "step": step, "series": series}


# ------------------------------------------------------------------
# แก้ค่าใน puller.yaml (admin)
# ------------------------------------------------------------------

def _clean_server(data, existing, old_name=None):
    errors = {}
    name = str(data.get("name") or "").strip()
    host = str(data.get("host") or "").strip()
    if not NAME_RE.match(name):
        errors["name"] = "ตัวอักษร / ตัวเลข / . _ - ไม่เกิน 60 ตัว"
    elif name != old_name and name in existing:
        errors["name"] = "มีชื่อนี้แล้ว"
    if not HOST_RE.match(host):
        errors["host"] = "ใส่ IP หรือชื่อเครื่อง เช่น 10.0.0.1 หรือ ntp.local"
    try:
        port = int(data.get("port") or 123)
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        errors["port"] = "1-65535"
        port = 123
    group = targets_service.clean_group(data.get("group") or "", errors) if data.get("group") else ""
    node = CommentedMap()
    node["name"], node["host"] = name, host
    if port != 123:
        node["port"] = port
    if group:
        node["group"] = group
    if data.get("paused"):
        node["paused"] = True
    if data.get("notify") is False:
        node["notify"] = False
    node.fa.set_flow_style()
    return node, errors


def _edit(fn):
    y = targets_service._yaml()
    with targets_service._locked():
        doc = targets_service._load_doc(y)
        if not isinstance(doc.get("ntp"), dict):
            doc["ntp"] = CommentedMap()
        ntp = doc["ntp"]
        if not isinstance(ntp.get("servers"), list):
            ntp["servers"] = CommentedSeq()
        seq = ntp["servers"]
        if isinstance(seq, CommentedSeq) and seq.fa.flow_style():
            seq.fa.set_block_style()
        ok, err = fn(ntp, seq)
        if ok:
            targets_service._write_doc(y, doc)
    return (True, get_overview()) if ok else (False, err)


def add_server(data):
    def fn(ntp, seq):
        names = {str(s.get("name")) for s in seq if isinstance(s, dict)}
        if len(names) >= MAX_SERVERS:
            return False, {"error": f"เพิ่มได้ไม่เกิน {MAX_SERVERS} server"}
        node, errors = _clean_server(data or {}, names)
        if errors:
            return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
        seq.append(node)
        return True, None
    return _edit(fn)


def update_server(name, data):
    def fn(ntp, seq):
        idx = next((i for i, s in enumerate(seq) if isinstance(s, dict) and str(s.get("name")) == name), None)
        if idx is None:
            return False, {"error": "not found", "status": 404}
        merged = dict(targets_service._plain(seq[idx]), **(data or {}))
        node, errors = _clean_server(merged, {str(s.get("name")) for s in seq if isinstance(s, dict)}, old_name=name)
        if errors:
            return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
        seq[idx] = node
        return True, None
    return _edit(fn)


def delete_server(name):
    def fn(ntp, seq):
        idx = next((i for i, s in enumerate(seq) if isinstance(s, dict) and str(s.get("name")) == name), None)
        if idx is None:
            return False, {"error": "not found", "status": 404}
        del seq[idx]
        return True, None
    return _edit(fn)


def update_settings(data):
    data = data or {}
    vals, errors = {}, {}
    for k, (lo, hi) in LIMITS.items():
        if k not in data or data[k] in (None, ""):
            continue
        try:
            v = float(data[k])
            if not lo <= v <= hi:
                raise ValueError
            vals[k] = int(v) if v.is_integer() else v
        except (TypeError, ValueError):
            errors[k] = f"{lo:g}-{hi:g}"
    cur = {**DEFAULTS, **{k: v for k, v in _ntp_cfg().items() if k in DEFAULTS}, **vals}
    if "offset_warning_ms" not in errors and "offset_error_ms" not in errors and float(cur["offset_warning_ms"]) >= float(cur["offset_error_ms"]):
        errors["offset_warning_ms"] = "warning ต้องน้อยกว่า error"
    if errors:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}

    def fn(ntp, seq):
        for k, v in vals.items():
            ntp[k] = v
        return True, None
    return _edit(fn)
