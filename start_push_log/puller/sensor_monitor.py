"""
sensor_monitor.py - "Sensor" ของแต่ละเครื่อง + สถานะ (แบบ PRTG) + บันทึกเมื่อสถานะเปลี่ยน
========================================================================================

ทำงานเป็น thread ใน central-puller ทุก INTERVAL วินาที (ไม่ต้องเปิดหน้าเว็บค้างไว้ก็ตรวจ)

sensor ต่อเครื่อง
    availability   node_exporter ตอบไหม (Prometheus up)          -> down เมื่อไม่ตอบ
    cpu            CPU average 5m (%)                             -> warning / error ตามเกณฑ์
    memory         Memory Usage %                                 -> warning / error
    disk:<mount>   Disk Usage % ของแต่ละ mount point               -> warning / error
    logpull        puller ติดต่อเครื่องได้ล่าสุดเมื่อไหร่ (SSH)       -> warning เมื่อเกิน 2 รอบดึง
เครื่องที่ pause -> ทุก sensor = paused ; เครื่อง down -> sensor อื่น = unknown

เกณฑ์ (ปรับได้ใน puller.yaml)
    thresholds:
      cpu:    {warning: 75, error: 90}
      memory: {warning: 80, error: 90}
      disk:   {warning: 80, error: 90}

ผลลัพธ์
    data/state/sensors.json   สถานะปัจจุบันของทุก sensor + ตั้งแต่เมื่อไหร่ (since) -> /api/sensors
    data/state/events.log     แถวใหม่ทุกครั้งที่ sensor เปลี่ยนสถานะ (events.py) -> System log
"""

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from config import CHECKPOINT_FILE, STATE_DIR, SYSTEM_DIR, load_config, parse_interval
from events import append_events

PROMETHEUS = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")
SENSORS_FILE = os.path.join(STATE_DIR, "sensors.json")
INTERVAL = 30

DEFAULT_THRESHOLDS = {
    "cpu": {"warning": 75, "error": 90},
    "memory": {"warning": 80, "error": 90},
    "disk": {"warning": 80, "error": 90},
}

_REAL_FS = (
    'fstype!~"tmpfs|devtmpfs|overlay|squashfs|ramfs|nsfs|autofs|efivarfs|fuse.lxcfs",'
    'mountpoint!~"/(run|snap|var/lib/docker|var/lib/kubelet|var/lib/containerd)(/.*)?"'
)
STATUS_TEXT = {"ok": "OK", "warning": "Warning", "error": "Error", "down": "Down",
               "paused": "Paused", "unknown": "Unknown"}


def _query(q):
    """instant query -> [(labels, float)]  ล้มเหลว -> None"""
    url = PROMETHEUS + "/api/v1/query?" + urllib.parse.urlencode({"query": q})
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            rows = json.loads(r.read().decode()).get("data", {}).get("result", [])
        out = []
        for row in rows:
            try:
                v = float(row["value"][1])
            except (KeyError, ValueError, IndexError):
                continue
            if v == v:                       # ตัด NaN
                out.append((row.get("metric", {}), v))
        return out
    except Exception:
        return None


def _one(q):
    rows = _query(q)
    return rows[0][1] if rows else None


def _thresholds(config):
    th = json.loads(json.dumps(DEFAULT_THRESHOLDS))
    for key, val in (config.get("thresholds") or {}).items():
        if key in th and isinstance(val, dict):
            for lv in ("warning", "error"):
                try:
                    th[key][lv] = float(val[lv])
                except (KeyError, TypeError, ValueError):
                    pass
    return th


def _grade(value, th):
    if value is None:
        return "unknown"
    if value >= th["error"]:
        return "error"
    if value >= th["warning"]:
        return "warning"
    return "ok"


def _limit_msg(label, value, th, status):
    if status == "error":
        return f"{value:.1f}% {label} is above the error limit of {th['error']:g}%"
    if status == "warning":
        return f"{value:.1f}% {label} is above the warning limit of {th['warning']:g}%"
    return f"{value:.1f}% {label}"


def _last_contact(name, checkpoints):
    """เวลา (unix) ที่ puller ติดต่อเครื่องสำเร็จล่าสุด: snapshot สถานะ หรือ checkpoint ของ log"""
    times = []
    try:
        with open(os.path.join(SYSTEM_DIR, f"{name}.json")) as f:
            ts = json.load(f).get("timestamp")
        if ts:
            times.append(datetime.fromisoformat(ts).timestamp())
    except (OSError, ValueError, TypeError):
        pass
    for item in checkpoints.values():
        if item.get("target") == name and item.get("updated_at"):
            try:
                times.append(datetime.fromisoformat(item["updated_at"]).timestamp())
            except ValueError:
                pass
    return max(times) if times else None


def _target_sensors(t, th, interval, checkpoints):
    name = t["name"]
    base = {"web": name, "host": t.get("host"), "group": t.get("group") or ""}
    prom = t.get("prometheus") or {}
    labels = f'job="{prom.get("job")}",instance="{prom.get("instance")}"'
    out = []

    def add(sid, kind, label, status, value=None, unit="%", message="", mount=None):
        out.append({**base, "id": f"{name}:{sid}", "kind": kind, "name": label, "status": status,
                    "value": None if value is None else round(value, 2), "unit": unit,
                    "message": message, "mount": mount})

    if t.get("paused"):
        add("availability", "availability", "Availability", "paused", unit="", message="Paused by user")
        return out

    up = _one(f'up{{web="{name}"}}')
    if up is None:
        up = _one(f"up{{{labels}}}")
    if up != 1:
        add("availability", "availability", "Availability", "down", unit="",
            message="node_exporter is not responding (machine down or port 9100 blocked)")
        return out
    add("availability", "availability", "Availability", "ok", unit="", message="Up")

    cpu = _one(f'100 - (avg by(instance) (rate(node_cpu_seconds_total{{{labels},mode="idle"}}[5m])) * 100)')
    st = _grade(cpu, th["cpu"])
    add("cpu", "cpu", "CPU average 5m", st, cpu,
        message=_limit_msg("CPU", cpu, th["cpu"], st) if cpu is not None else "no data")

    mem = _one(f"(1 - node_memory_MemAvailable_bytes{{{labels}}} / node_memory_MemTotal_bytes{{{labels}}}) * 100")
    st = _grade(mem, th["memory"])
    add("memory", "memory", "Memory Usage", st, mem,
        message=_limit_msg("memory used", mem, th["memory"], st) if mem is not None else "no data")

    seen = set()
    for metric, val in sorted(_query(
            f"(1 - node_filesystem_avail_bytes{{{labels},{_REAL_FS}}} / "
            f"node_filesystem_size_bytes{{{labels},{_REAL_FS}}}) * 100") or [],
            key=lambda r: r[0].get("mountpoint", "")):
        mount = metric.get("mountpoint", "?")
        if mount in seen:
            continue
        seen.add(mount)
        st = _grade(val, th["disk"])
        add(f"disk:{mount}", "disk", f"Disk Usage: {mount}", st, val, mount=mount,
            message=f"{_limit_msg('disk used', val, th['disk'], st)} (free {100 - val:.1f}%)")

    last = _last_contact(name, checkpoints)
    if last is None:
        add("logpull", "logpull", "Log pull (SSH)", "warning", unit="",
            message="puller has not reached this machine yet (check SSH key / port 22)")
    else:
        age = time.time() - last
        if age > 2 * interval + 300:
            add("logpull", "logpull", "Log pull (SSH)", "warning", age / 3600, unit="h",
                message=f"no successful pull for {age / 3600:.1f} h (limit {2 * interval / 3600:g} h) - check SSH")
        else:
            add("logpull", "logpull", "Log pull (SSH)", "ok", age / 60, unit="min",
                message=f"last pull {age / 60:.0f} min ago")
    return out


def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def run_once(previous):
    config = load_config()
    th = _thresholds(config)
    try:
        interval = parse_interval(config.get("scheduler", {}).get("interval", "1h"))
    except Exception:
        interval = 3600
    checkpoints = _read(CHECKPOINT_FILE, {})
    targets = [t for t in config.get("targets") or [] if isinstance(t, dict) and t.get("name")]

    with ThreadPoolExecutor(max_workers=8) as pool:
        sensors = [s for group in pool.map(lambda t: _target_sensors(t, th, interval, checkpoints), targets)
                   for s in group]

    now = time.time()
    events = []
    for s in sensors:
        old = previous.get(s["id"])
        if old and old.get("status") == s["status"]:
            s["since"] = old.get("since", now)
            continue
        s["since"] = now
        # sensor ใหม่ที่ OK ไม่ต้องบันทึก (เพิ่งเริ่มเห็น) ; เปลี่ยนสถานะ / ใหม่แต่มีปัญหา -> บันทึก
        if old or s["status"] != "ok":
            events.append({
                "source": "sensor", "level": s["status"], "web": s["web"], "group": s["group"],
                "sensor": s["name"], "from": (old or {}).get("status"), "to": s["status"],
                "message": f"{s['web']} · {s['name']}: {STATUS_TEXT.get((old or {}).get('status'), 'New')} → "
                           f"{STATUS_TEXT.get(s['status'], s['status'])} · {s['message']}",
            })

    tmp = SENSORS_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"updated_at": now, "thresholds": th, "sensors": sensors}, f)
    os.replace(tmp, SENSORS_FILE)
    append_events(events)
    return {s["id"]: s for s in sensors}


def _loop():
    previous = {s["id"]: s for s in _read(SENSORS_FILE, {}).get("sensors", [])}
    while True:
        try:
            previous = run_once(previous)
        except Exception as e:
            print(f"[SENSORS ERROR] {e}")
        time.sleep(INTERVAL)


def start():
    threading.Thread(target=_loop, daemon=True, name="sensors").start()
    print(f"[SENSORS] sensor monitor started (Prometheus {PROMETHEUS})")
