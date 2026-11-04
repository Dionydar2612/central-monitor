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

from clients import prom_value
from metrics_service import PROMQL, _labels, _round, classify_state, get_web_prometheus_config
from settings import load_web_targets
from system_state import last_pull_time

CACHE_TTL = 5
_CACHE = {"until": 0, "data": None}


def _server(target):
    name = target.get("name")
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

    return {
        "web": name,
        "host": target.get("host"),
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

    with ThreadPoolExecutor(max_workers=8) as pool:
        servers = list(pool.map(_server, targets))

    data = {
        "time": datetime.now(timezone.utc).isoformat(),
        "total": len(servers),
        "up": sum(1 for s in servers if s["status"] == "online"),
        "down": sum(1 for s in servers if s["status"] != "online"),
        "not_ok": sum(1 for s in servers if s["state"] != "HEALTHY"),
        "servers": servers,
    }

    _CACHE.update(until=now + CACHE_TTL, data=data)
    return data
