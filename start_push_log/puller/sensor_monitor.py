"""
sensor_monitor.py - "Sensor" ของแต่ละเครื่อง + สถานะ (แบบ PRTG) + ประวัติปัญหา (incident)
========================================================================================

ทำงานเป็น thread ใน central-puller ทุก INTERVAL (30) วินาที (ไม่ต้องเปิดหน้าเว็บค้างไว้ก็ตรวจ)

sensor ต่อเครื่อง
    availability   node_exporter ตอบไหม (Prometheus up)          -> down เมื่อไม่ตอบ
    cpu            CPU average 5m (%)                             -> warning / error ตามเกณฑ์
    memory         Memory Usage %                                 -> warning / error
    disk:<mount>   Disk Usage % ของแต่ละ mount point               -> warning / error
    logpull        puller ติดต่อเครื่องได้ล่าสุดเมื่อไหร่ (SSH)       -> warning เมื่อเกิน 2 รอบดึง
เครื่องที่ pause -> paused ; เครื่อง down -> มีแค่ availability

กันพลาด / กันแกว่ง (ตั้งใน puller.yaml -> sensors:)
    confirm_checks: 2   สถานะจะเปลี่ยนเมื่อเห็นค่าใหม่ติดกัน N รอบ (2 รอบ = ~1 นาที) ค่ากระตุกครั้งเดียวไม่นับ
    hysteresis: 2       ขึ้น Warning ที่ 80% แต่จะกลับเป็น OK เมื่อต่ำกว่า 78% (80 - 2) -> ไม่เด้ง OK/Warning ไปมาที่ 79-81%
    memory / disk ใช้ "ค่าสูงสุดใน 1 นาทีล่าสุด" (Prometheus max_over_time) ตัดสินสถานะ
        -> ค่าที่พุ่งขึ้นแล้วลงระหว่างรอบตรวจ (เช่น disk เต็มชั่วครู่) ก็ยังจับได้ ถ้า Prometheus scrape ทัน (ทุก 15 วินาที)

ประวัติปัญหา (incident)
    เริ่มเมื่อ sensor ออกจาก OK  จบเมื่อกลับเป็น OK  เก็บ: เริ่ม, จบ, นานเท่าไหร่, สถานะแย่สุด, ค่าสูงสุด
    ตอนจบเขียน System log "Recovered → OK after 25m (Error · peak 95.2%)"
    -> data/state/incidents.json  เก็บตาม storage.incident_retention (ปริยาย 30d)

เกณฑ์ (puller.yaml -> thresholds:  และ targets[].thresholds: เฉพาะเครื่อง)
    cpu 75/90 · memory 80/90 · disk 80/90  (warning/error)

ผลลัพธ์
    data/state/sensors.json    สถานะปัจจุบัน + since + incident ที่ยังไม่จบ  -> /api/sensors
    data/state/incidents.json  incident ที่จบแล้ว                          -> /api/incidents
    data/state/events.log      ทุกการเปลี่ยนสถานะ (events.py)              -> System log
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
INCIDENTS_FILE = os.path.join(STATE_DIR, "incidents.json")
INTERVAL = 30
MAX_INCIDENTS = 20000

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
RANK = {"ok": 0, "paused": 0, "unknown": 1, "warning": 2, "error": 3, "down": 4}
PROBLEM = ("warning", "error", "down", "unknown")


# ------------------------------------------------------------------
# Prometheus
# ------------------------------------------------------------------

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


def _peak(expr):
    """ค่าสูงสุดใน 1 นาทีล่าสุด (จับค่าที่พุ่งระหว่างรอบตรวจ)"""
    return f"max_over_time(({expr})[1m:15s])"


# ------------------------------------------------------------------
# เกณฑ์ + การตัดสินสถานะ
# ------------------------------------------------------------------

def _merge_thresholds(base, override):
    th = json.loads(json.dumps(base))
    for key, val in (override or {}).items():
        if key in th and isinstance(val, dict):
            for lv in ("warning", "error"):
                try:
                    th[key][lv] = float(val[lv])
                except (KeyError, TypeError, ValueError):
                    pass
    return th


def _thresholds(config):
    return _merge_thresholds(DEFAULT_THRESHOLDS, config.get("thresholds"))


def _options(config):
    s = config.get("sensors") or {}
    try:
        confirm = max(1, min(int(s.get("confirm_checks", 2)), 10))
    except (TypeError, ValueError):
        confirm = 2
    try:
        hyst = max(0.0, min(float(s.get("hysteresis", 2)), 20.0))
    except (TypeError, ValueError):
        hyst = 2.0
    try:
        retention = parse_interval((config.get("storage") or {}).get("incident_retention", "30d"))
    except Exception:
        retention = 30 * 86400
    return {"confirm": confirm, "hyst": hyst, "retention": retention}


def _grade(value, th, prev=None, hyst=0.0):
    """ระดับของค่า (ใช้ hysteresis: ต้องลดต่ำกว่าเกณฑ์ - hyst ก่อนจะลดระดับ)"""
    if value is None:
        return "unknown"
    if value >= th["error"]:
        return "error"
    if prev == "error" and value >= th["error"] - hyst:
        return "error"
    if value >= th["warning"]:
        return "warning"
    if prev in ("warning", "error") and value >= th["warning"] - hyst:
        return "warning"
    return "ok"


def _limit_msg(label, value, th, status, peak=None):
    pk = f" (peak {peak:.1f}% in last minute)" if peak is not None and peak - value >= 1 else ""
    if status == "error":
        return f"{value:.1f}% {label}{pk} - error limit {th['error']:g}%"
    if status == "warning":
        return f"{value:.1f}% {label}{pk} - warning limit {th['warning']:g}%"
    return f"{value:.1f}% {label}{pk}"


def _fmt_duration(sec):
    sec = int(max(0, sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m"
    return f"{s}s"


# ------------------------------------------------------------------
# อ่านค่า sensor ของ 1 เครื่อง
# ------------------------------------------------------------------

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


def _scrape_url(prom):
    """URL ที่ Prometheus ใช้ดึงเครื่องนี้ (ตาม scheme / metrics_path ใน puller.yaml)"""
    scheme = str(prom.get("scheme") or "http").lower()
    return f"{scheme}://{prom.get('instance')}{prom.get('metrics_path') or '/metrics'}"


def _target_sensors(t, global_th, interval, checkpoints, previous, hyst):
    """ค่าดิบของทุก sensor ของเครื่อง (status = ระดับจากค่าตอนนี้ ยังไม่ผ่านการยืนยัน N รอบ)"""
    name = t["name"]
    th = _merge_thresholds(global_th, t.get("thresholds"))
    base = {"web": name, "host": t.get("host"), "group": t.get("group") or ""}
    prom = t.get("prometheus") or {}
    labels = f'job="{prom.get("job")}",instance="{prom.get("instance")}"'
    out = []

    def prev(sid):
        return (previous.get(f"{name}:{sid}") or {}).get("status")

    def add(sid, kind, label, status, value=None, unit="%", message="", mount=None, peak=None):
        limits = th.get(kind)
        out.append({**base, "id": f"{name}:{sid}", "kind": kind, "name": label, "status": status,
                    "value": None if value is None else round(value, 2), "unit": unit,
                    "peak": None if peak is None else round(peak, 2),
                    "message": message, "mount": mount,
                    "warning": limits["warning"] if limits else None,
                    "error": limits["error"] if limits else None})

    if t.get("paused"):
        add("availability", "availability", "Availability", "paused", unit="", message="Paused by user")
        return out

    up = _one(f'up{{web="{name}"}}')
    if up is None:
        up = _one(f"up{{{labels}}}")
    if up != 1:
        add("availability", "availability", "Availability", "down", unit="",
            message=f"node_exporter is not responding: {_scrape_url(prom)} "
                    "(machine down, port blocked, DNS or TLS problem)")
        return out
    add("availability", "availability", "Availability", "ok", unit="", message="Up")

    cpu = _one(f'100 - (avg by(instance) (rate(node_cpu_seconds_total{{{labels},mode="idle"}}[5m])) * 100)')
    st = _grade(cpu, th["cpu"], prev("cpu"), hyst)
    add("cpu", "cpu", "CPU average 5m", st, cpu,
        message=_limit_msg("CPU", cpu, th["cpu"], st) if cpu is not None else "no data")

    mem_expr = f"(1 - node_memory_MemAvailable_bytes{{{labels}}} / node_memory_MemTotal_bytes{{{labels}}}) * 100"
    mem = _one(mem_expr)
    mem_pk = _one(_peak(mem_expr))
    judge = max(v for v in (mem, mem_pk) if v is not None) if (mem is not None or mem_pk is not None) else None
    st = _grade(judge, th["memory"], prev("memory"), hyst)
    add("memory", "memory", "Memory Usage", st, mem, peak=mem_pk,
        message=_limit_msg("memory used", mem, th["memory"], st, mem_pk) if mem is not None else "no data")

    disk_expr = (f"(1 - node_filesystem_avail_bytes{{{labels},{_REAL_FS}}} / "
                 f"node_filesystem_size_bytes{{{labels},{_REAL_FS}}}) * 100")
    peaks = {m.get("mountpoint"): v for m, v in (_query(_peak(disk_expr)) or [])}
    seen = set()
    for metric, val in sorted(_query(disk_expr) or [], key=lambda r: r[0].get("mountpoint", "")):
        mount = metric.get("mountpoint", "?")
        if mount in seen:
            continue
        seen.add(mount)
        pk = peaks.get(mount)
        st = _grade(max(val, pk if pk is not None else val), th["disk"], prev(f"disk:{mount}"), hyst)
        add(f"disk:{mount}", "disk", f"Disk Usage: {mount}", st, val, mount=mount, peak=pk,
            message=f"{_limit_msg('disk used', val, th['disk'], st, pk)} · free {100 - val:.1f}%")

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


# ------------------------------------------------------------------
# ไฟล์
# ------------------------------------------------------------------

def _read(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _save_incidents(new_items, retention, now):
    data = _read(INCIDENTS_FILE, [])
    if not isinstance(data, list):
        data = []
    cutoff = now - retention
    data = [i for i in data if (i.get("end") or now) >= cutoff] + new_items
    _write(INCIDENTS_FILE, data[-MAX_INCIDENTS:])


# ------------------------------------------------------------------
# 1 รอบ: อ่านค่า -> ยืนยัน N รอบ -> incident -> เขียนไฟล์ + System log
# ------------------------------------------------------------------

def run_once(previous):
    config = load_config()
    th = _thresholds(config)
    opts = _options(config)
    try:
        interval = parse_interval(config.get("scheduler", {}).get("interval", "1h"))
    except Exception:
        interval = 3600
    checkpoints = _read(CHECKPOINT_FILE, {})
    targets = [t for t in config.get("targets") or [] if isinstance(t, dict) and t.get("name")]

    with ThreadPoolExecutor(max_workers=8) as pool:
        sensors = [s for group in pool.map(
            lambda t: _target_sensors(t, th, interval, checkpoints, previous, opts["hyst"]), targets)
            for s in group]

    now = time.time()
    events, closed = [], []

    for s in sensors:
        old = previous.get(s["id"]) or {}
        raw = s["status"]
        confirmed = old.get("status")
        s["raw_status"] = raw
        s["incident"] = old.get("incident")

        # --- ยืนยันสถานะใหม่ N รอบติดกัน (paused / sensor ใหม่ = ทันที) ---
        first_seen = now                      # เวลาที่เห็นสถานะใหม่ครั้งแรก (ก่อนยืนยันครบ N รอบ)
        if confirmed is None or raw == "paused" or confirmed == "paused" or raw == confirmed:
            new = raw
            s["pending"] = None
        else:
            pend = old.get("pending") or {}
            same = pend.get("status") == raw
            count = pend.get("count", 0) + 1 if same else 1
            first_seen = pend.get("first", now) if same else now
            if count >= opts["confirm"]:
                new = raw
                s["pending"] = None
            else:
                new = confirmed
                s["pending"] = {"status": raw, "count": count, "need": opts["confirm"], "first": first_seen}
                s["message"] += f" · {STATUS_TEXT.get(raw, raw)} seen {count}/{opts['confirm']} checks"
        s["status"] = new
        s["since"] = old.get("since", now) if new == confirmed else first_seen

        # --- incident: เปิดเมื่อมีปัญหา, อัปเดตค่าสูงสุด/สถานะแย่สุด, ปิดเมื่อกลับเป็น OK ---
        inc = s["incident"]
        if new in PROBLEM:
            if not inc:
                inc = {"start": s["since"], "worst": new, "peak": None, "unit": s.get("unit")}
            if RANK[new] > RANK.get(inc["worst"], 0):
                inc["worst"] = new
            val = max(v for v in (s.get("value"), s.get("peak"), inc.get("peak")) if v is not None) \
                if any(v is not None for v in (s.get("value"), s.get("peak"), inc.get("peak"))) else None
            inc["peak"] = val
            s["incident"] = inc
        elif inc:
            end = s["since"]                      # เวลาที่เริ่มเห็นว่ากลับเป็นปกติ (ไม่ใช่เวลาที่ยืนยันครบ)
            dur = end - inc["start"]
            closed.append({**{k: s[k] for k in ("id", "web", "host", "group", "kind", "name")},
                           "start": inc["start"], "end": end, "duration": dur, "worst": inc["worst"],
                           "peak": inc.get("peak"), "unit": inc.get("unit"),
                           "ended_by": "paused" if new == "paused" else "recovered"})
            s["incident"] = None

        # --- System log เมื่อสถานะเปลี่ยน ---
        if new == confirmed:
            continue
        if confirmed is None and new == "ok":
            continue                          # sensor ใหม่ที่ปกติ ไม่ต้องบันทึก
        fields = {"source": "sensor", "level": new, "web": s["web"], "group": s["group"],
                  "sensor": s["name"], "from": confirmed, "to": new}
        if new == "ok" and closed and closed[-1]["id"] == s["id"]:
            c = closed[-1]
            pk = f" · peak {c['peak']:.1f}{'%' if c.get('unit') == '%' else ''}" if c.get("peak") is not None else ""
            fields.update(level="recovered", duration=c["duration"],
                          message=f"{s['web']} · {s['name']}: Recovered → OK after {_fmt_duration(c['duration'])} "
                                  f"({STATUS_TEXT.get(c['worst'], c['worst'])}{pk}) · {s['message']}")
        else:
            fields["message"] = (f"{s['web']} · {s['name']}: {STATUS_TEXT.get(confirmed, 'New')} → "
                                 f"{STATUS_TEXT.get(new, new)} · {s['message']}")
        events.append(fields)

    per_target = {t["name"]: _merge_thresholds(th, t.get("thresholds")) for t in targets}
    _write(SENSORS_FILE, {"updated_at": now, "thresholds": th, "targets": per_target,
                          "options": {"confirm_checks": opts["confirm"], "hysteresis": opts["hyst"],
                                      "interval": INTERVAL, "incident_retention": opts["retention"]},
                          "sensors": sensors})
    if closed or not os.path.exists(INCIDENTS_FILE):
        _save_incidents(closed, opts["retention"], now)
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
