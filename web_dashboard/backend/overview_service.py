"""
overview_service.py - ภาพรวมทุกเครื่อง (การ์ด WEB status บนสุดของ dashboard)
============================================================================

หน้าที่
    รวมสถานะ "ทุกเครื่องใน puller.yaml" ในคำขอเดียว ให้หน้าเว็บแสดง:
        ไทล์: Total / Up / Down / Warning / Critical + ค่าเฉลี่ย CPU / RAM / Disk ของเครื่องที่ online
        สรุปต่อกลุ่ม (groups): จำนวน + ค่าเฉลี่ย
        1 แถวต่อเครื่อง: ชื่อ, OS, IP, สถานะ (OK / WARNING / PROBLEM / DOWN), CPU, Memory, Disk (mount ที่เต็มสุด),
                        Network (MB/s รับ+ส่ง), Ping (ms / loss %), Alerts (sensor ที่มีปัญหา), Last pull
    เกณฑ์ OK / WARNING / PROBLEM = สถานะจาก sensor_monitor (เกณฑ์ของ OS + เฉพาะเครื่อง)
        ไม่มีผล sensor -> metrics_service.classify_state

ประสิทธิภาพ (เครื่องเยอะ)
    ถามแบบรวมครั้งเดียวต่อ metric: ... by(web) สำหรับ Linux (label จาก web-nodes.json) และ by(cm_web) สำหรับ Windows
    -> จำนวน query คงที่ (~15) ไม่ขึ้นกับจำนวนเครื่อง
    เครื่องที่ไม่มี label web (Prometheus ภายนอกที่ไม่ได้ใช้ web-nodes.json) -> ถามทีละเครื่องแบบเดิม
    cache สั้น ๆ (CACHE_TTL) เพราะหน้าเว็บรีเฟรชทุก 10 วินาที
ใช้โดย: api.py -> GET /api/overview
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import agent_service
from checks_service import get_checks
from clients import prom_value, prom_vector
from metrics_service import PROMQL, _labels, _round, classify_state, get_web_prometheus_config
from settings import load_web_targets
from sensors_service import target_states
from system_state import last_pull_time, pull_interval_seconds

CACHE_TTL = 5
_CACHE = {"until": 0, "data": None}

_SENSOR_STATE = {"down": "DOWN", "error": "PROBLEM", "warning": "WARNING", "unknown": "WARNING", "ok": "HEALTHY"}

_FS = ('fstype!~"tmpfs|devtmpfs|overlay|squashfs|ramfs|nsfs|autofs|efivarfs|fuse.lxcfs",'
       'mountpoint!~"/(run|snap|var/lib/docker|var/lib/kubelet|var/lib/containerd)(/.*)?"')
_DEV = 'device!~"(lo|veth.*|docker.*|br-.*|virbr.*|cni.*|flannel.*|cali.*)"'
_VOL = 'volume!~"HarddiskVolume.*|_Total"'
_NIC = 'nic!~"(?i).*(isatap|loopback|teredo|pseudo).*"'
_WCPU = '{__name__=~"windows_cpu_time(_seconds)?_total",mode="idle"}'

# query รวม: (ชื่อค่า, PromQL, label ที่ใช้แยกเครื่อง)
BATCH = [
    ("up", 'max by(web) (up{web!=""})', "web"),
    ("cpu", '100 - avg by(web) (rate(node_cpu_seconds_total{web!="",mode="idle"}[5m])) * 100', "web"),
    ("memory", '(1 - max by(web) (node_memory_MemAvailable_bytes{web!=""}) / '
               'max by(web) (node_memory_MemTotal_bytes{web!=""})) * 100', "web"),
    ("disk", f'max by(web) ((1 - node_filesystem_avail_bytes{{web!="",{_FS}}} / '
             f'node_filesystem_size_bytes{{web!="",{_FS}}}) * 100)', "web"),
    ("net", f'sum by(web) (rate(node_network_receive_bytes_total{{web!="",{_DEV}}}[5m]) + '
            f'rate(node_network_transmit_bytes_total{{web!="",{_DEV}}}[5m])) / 1048576', "web"),
    ("cpu", f"100 - avg by(cm_web) (rate({_WCPU}[5m])) * 100", "cm_web"),
    ("memory", '(1 - (max by(cm_web) (windows_memory_available_bytes) or max by(cm_web) (windows_memory_physical_free_bytes)'
               ' or max by(cm_web) (windows_os_physical_memory_free_bytes)) / '
               'max by(cm_web) ({__name__=~"windows_memory_physical_total_bytes|windows_cs_physical_memory_bytes"})) * 100',
     "cm_web"),
    ("disk", f"max by(cm_web) ((1 - windows_logical_disk_free_bytes{{{_VOL}}} / "
             f"windows_logical_disk_size_bytes{{{_VOL}}}) * 100)", "cm_web"),
    ("net", f"sum by(cm_web) (rate(windows_net_bytes_received_total{{{_NIC}}}[5m]) + "
            f"rate(windows_net_bytes_sent_total{{{_NIC}}}[5m])) / 1048576", "cm_web"),
    ("hb_age", 'time() - max by(cm_web) (timestamp({__name__=~"fluentbit_uptime.*",cm_web!=""} or (up{cm_web!=""} == 1)))', "cm_web"),
    ("ping_ms", 'sum by(cm_target) (sum_over_time(probe_icmp_duration_seconds{phase="rtt"}[5m])) / '
                'sum by(cm_target) (sum_over_time(probe_success[5m])) * 1000', "cm_target"),
    ("loss", "(1 - avg by(cm_target) (avg_over_time(probe_success[5m]))) * 100", "cm_target"),
]


def _batch():
    """{(ชื่อค่า, label): {เครื่อง: ค่า}}  query ล้มเหลว -> ว่าง (ถามทีละเครื่องแทน)"""
    def one(item):
        key, q, label = item
        try:
            return (key, label), {m.get(label): v for m, v in prom_vector(q) if m.get(label) and v == v}
        except Exception as e:
            print(f"[OVERVIEW] batch {key}: {e!r}")
            return (key, label), {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        return dict(pool.map(one, BATCH))


def _get(batch, key, label, name):
    return (batch.get((key, label)) or {}).get(name)


def _alerts(sensor):
    return len((sensor or {}).get("problems") or [])


def _ping(batch, name):
    return {"ping_ms": _round(_get(batch, "ping_ms", "cm_target", name)),
            "packet_loss": _round(_get(batch, "loss", "cm_target", name))}


def _server(target, sensor_states, batch):
    name = target.get("name")
    base = {"web": name, "host": target.get("host"), "group": target.get("group") or "",
            "paused": bool(target.get("paused")), "os": "linux", "kind": "ssh"}

    if base["paused"]:          # pause = ไม่ถาม Prometheus ไม่นับเป็นปัญหา
        return {**base, "instance": (target.get("prometheus") or {}).get("instance"),
                "status": "paused", "state": "PAUSED", "cpu": None, "memory": None, "disk": None, "net": None,
                "ping_ms": None, "packet_loss": None, "alerts": 0, "last_pull": last_pull_time(name)}

    if agent_service.is_agent(target):
        return _agent_server(target, base, sensor_states, batch)

    config = get_web_prometheus_config(name)

    # up{web=...} มาจาก web-nodes.json (Prometheus ที่มากับ stack) ; ไม่มี -> ใช้ job/instance ใน puller.yaml
    up = _get(batch, "up", "web", name)
    in_batch = up is not None
    if up is None:
        up = prom_value(f'up{{web="{name}"}}')
    if up is None and config:
        up = prom_value(f"up{{{_labels(config)}}}")

    values = {"cpu": None, "memory": None, "disk": None, "net": None}
    if config:
        if in_batch:
            for key in values:
                values[key] = _round(_get(batch, key, "web", name))
        else:
            labels = _labels(config)
            for key in ("cpu", "memory", "disk"):
                values[key] = _round(prom_value(PROMQL[key](labels)))
            values["net"] = _round(prom_value(f"({PROMQL['net_rx'](labels)}) + ({PROMQL['net_tx'](labels)})"))

    online = up == 1
    state = classify_state(values) if online else "DOWN"

    # มีผลจาก sensor_monitor -> ใช้สถานะนั้น (เกณฑ์เฉพาะเครื่อง + ข้าม sensor ที่ acknowledge แล้ว)
    sensor = (sensor_states or {}).get(name)
    if online and sensor:
        state = _SENSOR_STATE.get(sensor["worst"], state)
    extra = {"thresholds": (sensor or {}).get("thresholds"), "acked": (sensor or {}).get("acked", 0),
             "problems": (sensor or {}).get("problems", []), "alerts": _alerts(sensor) if online else 1}

    return {
        **extra,
        **base,
        "instance": (config or {}).get("instance"),
        "status": "online" if online else "offline",
        "state": state,
        **values,
        **_ping(batch, name),
        "last_pull": last_pull_time(name),
    }


def _agent_server(target, base, sensor_states, batch):
    """เครื่อง Windows (agent): online = ได้สัญญาณจาก agent ภายใน 3 นาที"""
    name = base["web"]
    age = _get(batch, "hb_age", "cm_web", name)
    if age is not None and age <= agent_service.ONLINE_WITHIN:
        online, last_seen = True, datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat()
    else:
        st = agent_service.status(name)          # ไม่มีใน batch -> ดูละเอียด (รวม log ล่าสุด)
        online, last_seen = st["online"], st["last_seen"]
    sensor = (sensor_states or {}).get(name)
    values = {k: (_round(_get(batch, k, "cm_web", name)) if online else None) for k in ("cpu", "memory", "disk", "net")}
    if online and values["cpu"] is None and values["memory"] is None:
        labels = _labels(get_web_prometheus_config(name))
        for key in ("cpu", "memory", "disk"):
            values[key] = _round(prom_value(PROMQL[key](labels)))
    state = "DOWN"
    if online:
        state = classify_state(values)
        if sensor:
            state = _SENSOR_STATE.get(sensor["worst"], state)
    return {
        **base,
        "kind": "agent", "os": target.get("os") or "windows",
        "instance": None,
        "status": "online" if online else "offline",
        "state": state,
        **values,
        **_ping(batch, name),
        "last_pull": last_seen,
        "thresholds": (sensor or {}).get("thresholds"), "acked": (sensor or {}).get("acked", 0),
        "problems": (sensor or {}).get("problems", []) if online else ["no data from agent"],
        "alerts": _alerts(sensor) if online else 1,
    }


def _avg(rows, key):
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def _counts(rows):
    return {
        "total": len(rows),
        "up": sum(1 for s in rows if s["status"] == "online"),
        "down": sum(1 for s in rows if s["status"] == "offline"),
        "paused": sum(1 for s in rows if s["status"] == "paused"),
        "warning": sum(1 for s in rows if s["state"] == "WARNING"),
        "problem": sum(1 for s in rows if s["state"] == "PROBLEM"),
        "not_ok": sum(1 for s in rows if s["state"] in ("WARNING", "PROBLEM", "DOWN")),
        "avg_cpu": _avg(rows, "cpu"), "avg_memory": _avg(rows, "memory"), "avg_disk": _avg(rows, "disk"),
        "windows": sum(1 for s in rows if s.get("os") == "windows"),
        "linux": sum(1 for s in rows if s.get("os") != "windows"),
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

    batch = _batch()
    with ThreadPoolExecutor(max_workers=8) as pool:
        servers = list(pool.map(lambda t: _server(t, states, batch), targets))

    try:
        checks = get_checks()
    except Exception as e:
        print(f"[OVERVIEW] checks error: {e}")
        checks = []

    groups = {}
    for s in servers:
        top = (s.get("group") or "").split("/")[0] or "(no group)"
        groups.setdefault(top, []).append(s)

    data = {
        "time": datetime.now(timezone.utc).isoformat(),
        "pull_interval": pull_interval_seconds(),
        **_counts(servers),
        "groups": [{"group": g, **_counts(rows)} for g, rows in sorted(groups.items())],
        "servers": servers,
        "checks": checks,
    }

    _CACHE.update(until=now + CACHE_TTL, data=data)
    return data
