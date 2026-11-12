"""
overview_history.py - กราฟภาพรวมทุกเครื่องย้อนหลัง (หน้า Status & Logs แท็บ Overview, แบบกราฟ Overview ของ PRTG)
=================================================================================================

ช่วงเวลา: 2d (ทุก 5 นาที) · 30d (ทุก 1 ชม.) · 365d (ทุก 12 ชม.)
เส้นกราฟ (ห้ามใช้ค่าเฉลี่ยข้ามเครื่อง -> ใช้ค่าสูงสุด พร้อมชื่อเครื่อง หรือผลรวม):
    alarms   จำนวนปัญหาที่เปิดอยู่ ณ เวลานั้น (incidents.json + ปัญหาที่ยังไม่จบ)
    cpu      CPU สูงสุดในบรรดาเครื่องทั้งหมด (%) + ชื่อเครื่อง
    memory   Memory สูงสุด (%) + ชื่อเครื่อง
    disk     Disk สูงสุด (%) + ชื่อเครื่อง
    traffic  Network รวมทุกเครื่อง (รับ + ส่ง, MB/s)
ใช้โดย: api.py -> GET /api/overview/history?range=2d
"""

import time
from concurrent.futures import ThreadPoolExecutor

import sensors_service
from metrics_service import get_metrics_history
from settings import load_web_targets

RANGES = {"2d": (2 * 86400, 300), "30d": (30 * 86400, 3600), "365d": (365 * 86400, 43200)}
CACHE_TTL = 60
_CACHE = {}


def _bucket(t, step):
    return int(float(t) // step * step)


def _alarms(start, end, step):
    """จำนวนปัญหาที่เปิดอยู่ ณ แต่ละช่วงเวลา"""
    now = time.time()
    spans = []
    for i in sensors_service._load_incidents():
        if i.get("ended_by") == "paused":
            continue
        s, e = i.get("start"), i.get("end")
        if s:
            spans.append((float(s), float(e or now)))
    for s in sensors_service.load_sensors().get("sensors", []):
        inc = s.get("incident")
        if inc and inc.get("start"):
            spans.append((float(inc["start"]), now))
    out = []
    t = _bucket(start, step)
    while t <= end:
        out.append([t, sum(1 for a, b in spans if a <= t + step and b >= t)])
        t += step
    return out


def get_history(range_key="2d"):
    range_key = range_key if range_key in RANGES else "2d"
    hit = _CACHE.get(range_key)
    if hit and hit[0] > time.time():
        return hit[1]
    seconds, step = RANGES[range_key]
    end = int(time.time())
    start = end - seconds
    webs = [t.get("name") for t in load_web_targets() if isinstance(t, dict) and t.get("name") and not t.get("paused")]

    def one(web):
        try:
            return web, get_metrics_history(web, start, end, step, names=("cpu", "memory", "disk", "net_rx", "net_tx"))
        except Exception as e:                       # เครื่องเดียวพังไม่ทำให้กราฟรวมพัง
            print(f"[OVERVIEW HISTORY] {web}: {e}")
            return web, {}

    with ThreadPoolExecutor(max_workers=8) as pool:
        per_web = list(pool.map(one, webs))

    peaks = {k: {} for k in ("cpu", "memory", "disk")}   # bucket -> [ค่า, เครื่อง]
    traffic = {}
    for web, series in per_web:
        for k in peaks:
            for t, v in series.get(k) or []:
                b = _bucket(t, step)
                if b not in peaks[k] or v > peaks[k][b][0]:
                    peaks[k][b] = [v, web]
        for k in ("net_rx", "net_tx"):
            for t, v in series.get(k) or []:
                b = _bucket(t, step)
                traffic[b] = traffic.get(b, 0.0) + v
    data = {
        "range": range_key, "step": step, "start": start, "end": end, "machines": len(webs),
        "series": {
            "alarms": _alarms(start, end, step),
            **{k: [[b, round(v, 2), w] for b, (v, w) in sorted(peaks[k].items())] for k in peaks},
            "traffic": [[b, round(v, 3)] for b, v in sorted(traffic.items())],
        },
    }
    _CACHE[range_key] = (time.time() + CACHE_TTL, data)
    return data
