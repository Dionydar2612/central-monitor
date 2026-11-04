"""
sensors_service.py - อ่านสถานะ sensor ที่ puller (sensor_monitor.py) เขียนไว้ + กราฟย้อนหลังสั้น ๆ
=================================================================================================

data/state/sensors.json  <- start_push_log/puller/sensor_monitor.py (ทุก 30 วินาที)
ใช้กับ: แถบป้ายสถานะบนหัวหน้าเว็บ (▼ / W / ✓ / II) และหน้า Status & Logs
"""

import json
import os
import time

from clients import prometheus_query_range
from metrics_service import PROMQL, _REAL_FS, _labels, get_web_prometheus_config
from settings import STATE_DIR

SENSORS_FILE = os.path.join(STATE_DIR, "sensors.json")
STATUSES = ("down", "error", "warning", "unknown", "paused", "ok")


def load_sensors():
    try:
        with open(SENSORS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def summary():
    data = load_sensors()
    counts = {s: 0 for s in STATUSES}
    for s in data.get("sensors", []):
        counts[s.get("status", "unknown")] = counts.get(s.get("status", "unknown"), 0) + 1
    return {"counts": counts, "total": sum(counts.values()), "updated_at": data.get("updated_at")}


def _history_query(sensor, labels):
    kind = sensor.get("kind")
    if kind in ("cpu", "memory"):
        return PROMQL[kind](labels)
    if kind == "disk" and sensor.get("mount"):
        mount = str(sensor["mount"]).replace("\\", "\\\\").replace('"', '\\"')
        l = f'{labels},mountpoint="{mount}",{_REAL_FS}'
        return f"(1 - node_filesystem_avail_bytes{{{l}}} / node_filesystem_size_bytes{{{l}}}) * 100"
    return None


def get_sensors(statuses=None, web=None, history=False, limit=500):
    data = load_sensors()
    wanted = {s for s in (statuses or []) if s}
    rows = [s for s in data.get("sensors", [])
            if (not wanted or s.get("status") in wanted) and (not web or s.get("web") == web)]
    order = {s: i for i, s in enumerate(STATUSES)}
    rows.sort(key=lambda s: (order.get(s.get("status"), 9), s.get("group") or "", s.get("web") or "", s.get("name") or ""))
    rows = rows[:limit]

    if history:
        end = time.time()
        start = end - 3600
        for s in rows[:60]:                     # กราฟเล็ก 1 ชม. ล่าสุด (เฉพาะ 60 รายการแรก)
            config = get_web_prometheus_config(s.get("web"))
            q = _history_query(s, _labels(config)) if config else None
            if not q:
                continue
            try:
                res = prometheus_query_range(q, start, end, 60)["data"]["result"]
                s["history"] = [[float(t), round(float(v), 2)] for t, v in (res[0]["values"] if res else [])
                                if v not in ("NaN", "+Inf", "-Inf")]
            except Exception:
                s["history"] = []

    return {"updated_at": data.get("updated_at"), "thresholds": data.get("thresholds"),
            "sensors": rows, "summary": summary()}
