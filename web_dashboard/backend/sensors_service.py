"""
sensors_service.py - สถานะ sensor (จาก puller/sensor_monitor.py) + Acknowledge + กราฟย้อนหลัง
=============================================================================================

data/state/sensors.json  <- start_push_log/puller/sensor_monitor.py (ทุก 30 วินาที)
data/state/acks.json     <- Acknowledge จากหน้าเว็บ (operator ขึ้นไป) ไฟล์นี้เขียนเอง

Acknowledge (แบบ PRTG)
    "รับทราบแล้ว กำลังจัดการ" + บันทึกโน้ต -> sensor นั้นแสดงเป็น "Warning (Acknowledged)" และ
    ไม่ถูกนับในป้าย ▼ / W บนหัวหน้าเว็บ (นับแยกเป็น ACK)  และไม่ทำให้สถานะเครื่องเป็น WARNING/PROBLEM
    ยกเลิกอัตโนมัติเมื่อสถานะเปลี่ยน (หายเป็น OK หรือแย่ลง) -> ต้องรับทราบใหม่
ใช้กับ: ป้ายบนหัวหน้าเว็บ, หน้า Status & Logs, ภาพรวม (overview_service)
"""

import contextlib
import fcntl
import json
import os
import time

from clients import prometheus_query_range
from metrics_service import PROMQL, _REAL_FS, _labels, get_web_prometheus_config
from settings import STATE_DIR

SENSORS_FILE = os.path.join(STATE_DIR, "sensors.json")
INCIDENTS_FILE = os.path.join(STATE_DIR, "incidents.json")      # <- puller/sensor_monitor.py
ACKS_FILE = os.path.join(STATE_DIR, "acks.json")
ACK_LOCK = os.path.join(STATE_DIR, ".acks.lock")
STATUSES = ("down", "error", "warning", "unknown", "paused", "ok")
ACKABLE = ("down", "error", "warning", "unknown")
MAX_NOTE = 500
HISTORY_RANGES = {"live": 2 * 3600, "1h": 3600, "6h": 6 * 3600, "24h": 86400, "2d": 2 * 86400, "7d": 7 * 86400,
                  "30d": 30 * 86400, "365d": 365 * 86400}


def load_sensors():
    try:
        with open(SENSORS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


# ------------------------------------------------------------------
# Acknowledge
# ------------------------------------------------------------------

@contextlib.contextmanager
def _locked():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(ACK_LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _load_acks():
    try:
        with open(ACKS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_acks(acks):
    tmp = ACKS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(acks, f, ensure_ascii=False, indent=1)
    os.replace(tmp, ACKS_FILE)


def _attach_acks(sensors):
    """ใส่ s["ack"] ให้ sensor ที่ ack ยังใช้ได้ (สถานะเดิม) ack ที่หมดอายุไม่ถูกใช้"""
    acks = _load_acks()
    for s in sensors:
        a = acks.get(s.get("id"))
        if a and a.get("status") == s.get("status") and s.get("status") in ACKABLE:
            s["ack"] = a
    return sensors


def acknowledge(sensor_id, user, note):
    """คืน ack ที่บันทึก  ไม่มี sensor / สถานะ OK -> ValueError"""
    current = next((s for s in load_sensors().get("sensors", []) if s.get("id") == sensor_id), None)
    if current is None:
        raise ValueError("ไม่พบ sensor นี้")
    if current.get("status") not in ACKABLE:
        raise ValueError("acknowledge ได้เฉพาะ sensor ที่ Down / Error / Warning / Unknown")
    ack = {"by": user, "at": time.time(), "note": str(note or "").strip()[:MAX_NOTE],
           "status": current["status"], "web": current.get("web"), "sensor": current.get("name")}
    with _locked():
        acks = _load_acks()
        # ล้าง ack ที่สถานะเปลี่ยนไปแล้ว (หมดอายุ) ไปด้วย
        live = {s["id"]: s.get("status") for s in load_sensors().get("sensors", [])}
        acks = {k: v for k, v in acks.items() if live.get(k) == v.get("status")}
        acks[sensor_id] = ack
        _save_acks(acks)
    return ack


def unacknowledge(sensor_id):
    with _locked():
        acks = _load_acks()
        if sensor_id not in acks:
            return False
        del acks[sensor_id]
        _save_acks(acks)
    return True


# ------------------------------------------------------------------
# อ่าน / สรุป
# ------------------------------------------------------------------

def _load_incidents():
    try:
        with open(INCIDENTS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def get_incidents(hours=24, web=None, include_ongoing=True, limit=1000):
    """ปัญหาที่เกิดในช่วง hours ชั่วโมงล่าสุด: ที่จบแล้ว (incidents.json) + ที่ยังเป็นอยู่ (sensors.json) ใหม่สุดก่อน"""
    now = time.time()
    since = now - float(hours) * 3600
    rows = [dict(i, state="recovered" if i.get("ended_by") != "paused" else "paused")
            for i in _load_incidents() if (i.get("end") or 0) >= since and (not web or i.get("web") == web)]
    if include_ongoing:
        for s in load_sensors().get("sensors", []):
            inc = s.get("incident")
            if inc and (not web or s.get("web") == web):
                rows.append({"id": s["id"], "web": s["web"], "host": s.get("host"), "group": s.get("group"),
                             "kind": s.get("kind"), "name": s.get("name"), "start": inc.get("start"),
                             "end": None, "duration": now - (inc.get("start") or now), "worst": inc.get("worst"),
                             "peak": inc.get("peak"), "unit": inc.get("unit"), "state": "ongoing",
                             "status": s.get("status")})
    rows.sort(key=lambda r: (r["state"] != "ongoing", -(r.get("end") or r.get("start") or 0)))
    return rows[:limit]


def summary():
    data = load_sensors()
    counts = {s: 0 for s in STATUSES}
    counts["acknowledged"] = 0
    since = time.time() - 86400
    counts["recovered_24h"] = sum(1 for i in _load_incidents()
                                  if i.get("ended_by") == "recovered" and (i.get("end") or 0) >= since)
    for s in _attach_acks(data.get("sensors", [])):
        if s.get("ack"):
            counts["acknowledged"] += 1
        else:
            counts[s.get("status", "unknown")] = counts.get(s.get("status", "unknown"), 0) + 1
    total = sum(v for k, v in counts.items() if k != "recovered_24h")
    return {"counts": counts, "total": total, "updated_at": data.get("updated_at"),
            "options": data.get("options")}


def target_states():
    """สถานะรวมต่อเครื่องจาก sensor ที่ยังไม่ acknowledge + เกณฑ์ของเครื่อง  {web: {...}}"""
    data = load_sensors()
    out = {}
    rank = {"down": 4, "error": 3, "warning": 2, "unknown": 1, "ok": 0, "paused": 0}
    for s in _attach_acks(data.get("sensors", [])):
        w = out.setdefault(s["web"], {"worst": "ok", "acked": 0, "problems": []})
        if s.get("ack"):
            w["acked"] += 1
            continue
        if rank.get(s["status"], 0) > rank.get(w["worst"], 0):
            w["worst"] = s["status"]
        if s["status"] in ACKABLE:
            w["problems"].append(s["name"])
    for name, th in (data.get("targets") or {}).items():
        out.setdefault(name, {"worst": "ok", "acked": 0, "problems": []})["thresholds"] = th
    return out


def _history_query(sensor, labels):
    kind = sensor.get("kind")
    if kind in ("cpu", "memory"):
        return PROMQL[kind](labels)
    if kind == "disk" and sensor.get("mount") and labels.startswith("cm_web="):     # Windows: ไดรฟ์ C: D:
        vol = str(sensor["mount"]).replace('"', "")
        l = f'{labels},volume="{vol}"'
        return f"(1 - windows_logical_disk_free_bytes{{{l}}} / windows_logical_disk_size_bytes{{{l}}}) * 100"
    if kind == "availability" and labels.startswith("cm_web="):
        return f'(count(time() - timestamp({{__name__=~"fluentbit_uptime.*",{labels}}}) < 180) or vector(0)) * 100'
    if kind == "disk" and sensor.get("mount"):
        mount = str(sensor["mount"]).replace("\\", "\\\\").replace('"', '\\"')
        l = f'{labels},mountpoint="{mount}",{_REAL_FS}'
        return f"(1 - node_filesystem_avail_bytes{{{l}}} / node_filesystem_size_bytes{{{l}}}) * 100"
    if kind == "availability":
        return f"up{{{labels}}} * 100"
    # sensor ชนิดใหม่ (disk_latency, packet_loss, net_latency, mem_available ...) เก็บ PromQL ที่ใช้ไว้เอง
    return sensor.get("query") or None


def _series(sensor, seconds, points=120):
    config = get_web_prometheus_config(sensor.get("web"))
    q = _history_query(sensor, _labels(config)) if config else None
    if not q:
        return []
    end = time.time()
    step = max(15, int(seconds / points))
    try:
        res = prometheus_query_range(q, end - seconds, end, step)["data"]["result"]
        return [[float(t), round(float(v), 2)] for t, v in (res[0]["values"] if res else [])
                if v not in ("NaN", "+Inf", "-Inf")]
    except Exception:
        return []


def get_sensors(statuses=None, web=None, history=False, limit=500):
    """statuses มี "acknowledged" ได้ (= sensor ที่รับทราบแล้ว)  ไม่ระบุ = ทั้งหมด"""
    data = load_sensors()
    wanted = {s for s in (statuses or []) if s}
    rows = []
    for s in _attach_acks(data.get("sensors", [])):
        if web and s.get("web") != web:
            continue
        if wanted:
            if s.get("ack"):
                if "acknowledged" not in wanted:
                    continue
            elif s.get("status") not in wanted:
                continue
        rows.append(s)
    order = {s: i for i, s in enumerate(STATUSES)}
    rows.sort(key=lambda s: (order.get(s.get("status"), 9), s.get("group") or "", s.get("web") or "", s.get("name") or ""))
    rows = rows[:limit]

    if history:
        for s in rows[:60]:                     # กราฟเล็ก 1 ชม. ล่าสุด (เฉพาะ 60 รายการแรก)
            s["history"] = _series(s, 3600, 60)

    return {"updated_at": data.get("updated_at"), "thresholds": data.get("thresholds"), "options": data.get("options"),
            "sensors": rows, "summary": summary()}


def get_history(sensor_id, range_key="24h"):
    """กราฟย้อนหลังของ sensor เดียว (ช่วง 1h / 6h / 24h / 7d / 30d)  ไม่พบ -> None"""
    sensor = next((s for s in load_sensors().get("sensors", []) if s.get("id") == sensor_id), None)
    if sensor is None:
        return None
    seconds = HISTORY_RANGES.get(range_key, 86400)
    series = _series(sensor, seconds, 240)
    vals = [v for _, v in series]
    return {"id": sensor_id, "range": range_key, "warning": sensor.get("warning"), "error": sensor.get("error"),
            "unit": sensor.get("unit"), "kind": sensor.get("kind"), "name": sensor.get("name"),
            "web": sensor.get("web"), "series": series,
            "min": min(vals) if vals else None, "max": max(vals) if vals else None,   # ไม่มีค่าเฉลี่ย (กฎของระบบ)
            "last": vals[-1] if vals else None}


def get_detail(sensor_id, days=30):
    """หน้า sensor (แบบ PRTG): ค่าปัจจุบัน + Uptime / Downtime ย้อนหลัง days วัน + Last up / Last down + ประวัติปัญหา
    downtime = ช่วงที่เป็น down / error  ไม่พบ -> None"""
    data = load_sensors()
    sensor = next((s for s in _attach_acks(data.get("sensors", [])) if s.get("id") == sensor_id), None)
    if sensor is None:
        return None
    now = time.time()
    since = now - days * 86400
    rows = [i for i in _load_incidents() if i.get("id") == sensor_id]
    inc = sensor.get("incident")
    if inc and inc.get("start"):
        rows.append({"id": sensor_id, "start": inc["start"], "end": None, "worst": inc.get("worst"), "peak": inc.get("peak"),
                     "unit": inc.get("unit"), "state": "ongoing"})
    down = 0.0
    last_down = None
    for i in rows:
        if i.get("worst") not in ("down", "error"):
            continue
        a, b = float(i.get("start") or now), float(i.get("end") or now)
        last_down = max(last_down or 0, a)
        a, b = max(a, since), min(b, now)
        if b > a:
            down += b - a
    status = sensor.get("status")
    last_up = now if status in ("ok", "warning") else (max((float(i["end"]) for i in rows if i.get("end")), default=None))
    history = sorted(rows, key=lambda i: -(i.get("start") or 0))[:100]
    for i in history:
        i["duration"] = (i.get("end") or now) - (i.get("start") or now)
        i.setdefault("state", "recovered")
    return {"sensor": sensor, "updated_at": data.get("updated_at"), "interval": (data.get("options") or {}).get("interval", 30),
            "days": days, "downtime_pct": round(down / (days * 86400) * 100, 4), "uptime_pct": round(100 - down / (days * 86400) * 100, 4),
            "last_up": last_up, "last_down": last_down, "incidents": history,
            "has_graph": sensor.get("kind") in ("cpu", "memory", "disk", "availability") or bool(sensor.get("query"))}
