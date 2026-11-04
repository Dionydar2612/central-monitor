"""
overview_service.py - ภาพรวมทุกเครื่อง (การ์ด WEB status บนสุดของ dashboard)
============================================================================

หน้าที่
    รวมสถานะ "ทุกเครื่องใน puller.yaml" ในคำขอเดียว ให้หน้าเว็บแสดงแบบตาราง Server Status:
        Total / Up / Down / Not OK  +  1 แถวต่อเครื่อง:
        ชื่อ, IP, สถานะ (OK / WARNING / PROBLEM / DOWN), CPU average 5m, Memory %, Disk % (mount ที่เต็มสุด),
        Last pull
    เกณฑ์ OK / WARNING / PROBLEM = metrics_service.classify_state (ตัวเดียวกับ State ของเครื่องที่เลือก)

ถาม Prometheus ทีละเครื่องแบบขนาน (ThreadPool) และ cache สั้น ๆ (CACHE_TTL) เพราะหน้าเว็บรีเฟรชทุก 10 วินาที
ใช้โดย: api.py -> GET /api/overview
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from checks_service import get_checks
from clients import prom_value
from metrics_service import PROMQL, _labels, _round, classify_state, get_web_prometheus_config
from settings import load_web_targets
from sensors_service import target_states
from system_state import last_pull_time, pull_interval_seconds

CACHE_TTL = 5
_CACHE = {"until": 0, "data": None}


_SENSOR_STATE = {"down": "DOWN", "error": "PROBLEM", "warning": "WARNING", "unknown": "WARNING", "ok": "HEALTHY"}


def _server(target, sensor_states=None):
    name = target.get("name")
    base = {"web": name, "host": target.get("host"), "group": target.get("group") or "",
            "paused": bool(target.get("paused"))}

    if base["paused"]:          # pause = ไม่ถาม Prometheus ไม่นับเป็นปัญหา
        return {**base, "instance": (target.get("prometheus") or {}).get("instance"),
                "status": "paused", "state": "PAUSED", "cpu": None, "memory": None, "disk": None,
                "last_pull": last_pull_time(name)}

    config = get_web_prometheus_config(name)

    # up{web=...} มาจาก web-nodes.json (Prometheus ที่มากับ stack) ; ไม่มี -> ใช้ job/instance ใน puller.yaml
    up = prom_value(f'up{{web="{name}"}}')
    if up is None and config:
        up = prom_value(f"up{{{_labels(config)}}}")

    values = {"cpu": None, "memory": None, "disk": None}
    if config:
        labels = _labels(config)
        for key in values:
            values[key] = _round(prom_value(PROMQL[key](labels)))

    online = up == 1
    state = classify_state(values) if online else "DOWN"

    # มีผลจาก sensor_monitor -> ใช้สถานะนั้น (เกณฑ์เฉพาะเครื่อง + ข้าม sensor ที่ acknowledge แล้ว)
    sensor = (sensor_states or {}).get(name)
    if online and sensor:
        state = _SENSOR_STATE.get(sensor["worst"], state)
    extra = {"thresholds": (sensor or {}).get("thresholds"), "acked": (sensor or {}).get("acked", 0),
             "problems": (sensor or {}).get("problems", [])}

    return {
        **extra,
        **base,
        "instance": (config or {}).get("instance"),
        "status": "online" if online else "offline",
        "state": state,
        **values,
        "last_pull": last_pull_time(name),
    }


def get_overview():
    now = time.time()
    if _CACHE["data"] is not None and _CACHE["until"] > now:
        return _CACHE["data"]

    targets = [t for t in load_web_targets() if t.get("name")]

    try:
        states = target_states()
    except Exception as e:
        print(f"[OVERVIEW] sensors error: {e}")
        states = {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        servers = list(pool.map(lambda t: _server(t, states), targets))

    try:
        checks = get_checks()
    except Exception as e:
        print(f"[OVERVIEW] checks error: {e}")
        checks = []

    data = {
        "time": datetime.now(timezone.utc).isoformat(),
        "pull_interval": pull_interval_seconds(),
        "total": len(servers),
        "up": sum(1 for s in servers if s["status"] == "online"),
        "down": sum(1 for s in servers if s["status"] == "offline"),
        "paused": sum(1 for s in servers if s["status"] == "paused"),
        "warning": sum(1 for s in servers if s["state"] == "WARNING"),
        "problem": sum(1 for s in servers if s["state"] == "PROBLEM"),
        "not_ok": sum(1 for s in servers if s["state"] in ("WARNING", "PROBLEM", "DOWN")),
        "servers": servers,
        "checks": checks,
    }

    _CACHE.update(until=now + CACHE_TTL, data=data)
    return data
