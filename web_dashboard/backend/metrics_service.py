"""
metrics_service.py - ตัวชี้วัด CPU / Memory / Disk / Network ของเครื่อง
======================================================================

หน้าที่
    ถาม Prometheus ว่าเครื่องหนึ่ง ๆ ตอนนี้ (หรือเมื่อเวลาที่ผ่านมา) ใช้ทรัพยากรเท่าไหร่
    แล้วประกอบเป็นก้อนข้อมูลให้ api.py ส่งต่อไปหน้าเว็บ

3 รูปแบบการใช้งาน
    get_current_metrics(web)           ค่า ณ ตอนนี้ + สถานะเครื่อง   -> /api/metrics, /api/summary
    get_metrics_history(web,s,e,step)  อนุกรมเวลาสำหรับกราฟ          -> /api/metrics/history
    get_metrics_at_time(ts, web)       ค่า ณ เวลาที่ log เกิดขึ้น      -> แสดงข้างแถว log ("Metrics at time")

รู้ได้อย่างไรว่าเครื่องไหนคือ series ไหนใน Prometheus
    ดูจาก puller.yaml: targets[].prometheus.{job, instance}
    (เครื่อง "central" ใช้ค่าจาก CENTRAL_NODE_JOB / CENTRAL_NODE_INSTANCE)
    ตัวกรอง label ทั้งหมดสร้างจากคู่ job+instance นี้ จึงต้องตรงกับที่ Prometheus scrape จริง

เชื่อมกับอะไร
    - clients.py       : ยิง query ไป Prometheus
    - settings.py      : รายชื่อเครื่องจาก puller.yaml
    - system_state.py  : ข้อมูลที่ Prometheus ไม่มี (load, uptime, service, process)
    - ผู้เรียก: api.py, logs_service.py
"""

from clients import prom_value, prom_vector, prometheus_query_range
from settings import (
    CENTRAL_NODE_INSTANCE,
    CENTRAL_NODE_JOB,
    load_web_targets,
    resolve_web_name,
)
from system_state import load_system_snapshot
from timeutil import to_unix

# ---------------------------------------------------------------------------
# รู้จักเครื่อง: เครื่องนี้ใน Prometheus คือ job/instance อะไร
# ---------------------------------------------------------------------------

def get_web_prometheus_config(web):
    """คืน {"job":..., "instance":...} ของเครื่อง หรือ None ถ้าไม่รู้จัก/ตั้งค่าไม่ครบ"""

    # เครื่อง central แยกต่างหากจากเครื่อง web ใน puller.yaml
    if web == "central":
        return {"job": CENTRAL_NODE_JOB, "instance": CENTRAL_NODE_INSTANCE}

    # hostname จริงจาก log -> ชื่อ target (ตั้ง aliases ใน puller.yaml)
    web = resolve_web_name(web)

    for target in load_web_targets():
        if target.get("name") != web:
            continue

        # เครื่อง Windows (agent): metrics เข้ามาทาง remote write พร้อม label cm_web="<ชื่อเครื่อง>"
        if str(target.get("type") or "ssh") == "agent":
            return {"job": "cm-agent", "instance": web, "os": str(target.get("os") or "windows"), "web": web}

        prometheus = target.get("prometheus", {})
        job = prometheus.get("job")
        instance = prometheus.get("instance")

        if not job or not instance:
            return None

        return {"job": job, "instance": instance}

    return None


def _labels(config):
    """ส่วน {...} ที่ใช้กรอง series ของเครื่องนี้ใน PromQL
    Windows (agent) -> cm_web="<ชื่อ>"  (PROMQL เลือกชุด query ของ Windows จาก label นี้)"""
    if config.get("os") == "windows":
        return f'cm_web="{config["web"]}"'
    return f'job="{config["job"]}",instance="{config["instance"]}"'


# ---------------------------------------------------------------------------
# PromQL ของแต่ละ metric (นิยามที่เดียว ใช้ร่วมกันทั้ง 3 รูปแบบ)
#   ทุก query ใช้ rate(...[5m]) เฉลี่ยย้อนหลัง 5 นาที
#   หน่วย: CPU/Memory/Disk/io_util = เปอร์เซ็นต์, disk_*/net_*/io = MB/s
# ---------------------------------------------------------------------------

# mount point ที่เป็นดิสก์จริง (ตัด filesystem เสมือน/ชั่วคราว และ mount ของ container/snap ออก)
_REAL_FS = (
    'fstype!~"tmpfs|devtmpfs|overlay|squashfs|ramfs|nsfs|autofs|efivarfs|fuse.lxcfs",'
    'mountpoint!~"/(run|snap|var/lib/docker|var/lib/kubelet|var/lib/containerd)(/.*)?"'
)


def _disk_used_pct(l):
    return (
        f"(1 - node_filesystem_avail_bytes{{{l},{_REAL_FS}}} / "
        f"node_filesystem_size_bytes{{{l},{_REAL_FS}}}) * 100"
    )


LINUX_PROMQL = {
    # CPU average 5m (%) = 100 - เวลา idle เฉลี่ยทุก core ย้อนหลัง 5 นาที
    "cpu": lambda l: (
        f'100 - (avg by(instance) (rate(node_cpu_seconds_total{{{l},mode="idle"}}[5m])) * 100)'
    ),
    # CPU % แยกทีละ core (เฉลี่ย 5 นาที) -> การ์ด "CPU process per CPU core"
    "cpu_core": lambda l: (
        f'100 - (avg by(cpu) (rate(node_cpu_seconds_total{{{l},mode="idle"}}[5m])) * 100)'
    ),
    # Memory % = 1 - available/total
    "memory": lambda l: (
        f"(1 - node_memory_MemAvailable_bytes{{{l}}} / node_memory_MemTotal_bytes{{{l}}}) * 100"
    ),
    # Disk Usage % = mount point ที่เต็มที่สุด (ใช้กับตัวเลขใหญ่ / กราฟ / สถานะเครื่อง)
    "disk": lambda l: f"max({_disk_used_pct(l)})",
    # Disk Usage % แยกทีละ mount point
    "disk_mount": _disk_used_pct,
    "disk_read": lambda l: (
        f"sum(rate(node_disk_read_bytes_total{{{l}}}[5m])) / 1024 / 1024"
    ),
    "disk_write": lambda l: (
        f"sum(rate(node_disk_written_bytes_total{{{l}}}[5m])) / 1024 / 1024"
    ),
    # อ่าน+เขียนรวม (ใช้กับกราฟ ที่ต้องการเส้นเดียว)
    "io": lambda l: (
        f"(sum(rate(node_disk_read_bytes_total{{{l}}}[5m])) + "
        f"sum(rate(node_disk_written_bytes_total{{{l}}}[5m]))) / 1024 / 1024"
    ),
    # ความอิ่มตัวของดิสก์ที่ยุ่งที่สุด (เหมือน %util ของ iostat)
    "io_util": lambda l: (
        f"max(rate(node_disk_io_time_seconds_total{{{l}}}[5m])) * 100"
    ),
    "net_rx": lambda l: (
        f'sum(rate(node_network_receive_bytes_total{{{l},device!="lo"}}[5m])) / 1024 / 1024'
    ),
    "net_tx": lambda l: (
        f'sum(rate(node_network_transmit_bytes_total{{{l},device!="lo"}}[5m])) / 1024 / 1024'
    ),
    # CPU แยกประเภท (%): user = โปรแกรม, system = kernel, iowait = รอดิสก์
    "cpu_user": lambda l: (
        f'avg(rate(node_cpu_seconds_total{{{l},mode="user"}}[5m])) * 100'
    ),
    "cpu_system": lambda l: (
        f'avg(rate(node_cpu_seconds_total{{{l},mode="system"}}[5m])) * 100'
    ),
    "cpu_iowait": lambda l: (
        f'avg(rate(node_cpu_seconds_total{{{l},mode="iowait"}}[5m])) * 100'
    ),
    # Load average และจำนวน core (เส้นอ้างอิง: load เกินจำนวน core = มีงานรอคิว CPU)
    "load1": lambda l: f"node_load1{{{l}}}",
    "load5": lambda l: f"node_load5{{{l}}}",
    "load15": lambda l: f"node_load15{{{l}}}",
    "cores": lambda l: f'count(node_cpu_seconds_total{{{l},mode="idle"}})',
}

# ---------------------------------------------------------------------------
# Windows (agent: Fluent Bit windows_exporter_metrics -> remote write) ชื่อ metric แบบ windows_exporter
#   ไดรฟ์ = volume (C:, D:) ตัด HarddiskVolumeN (พาร์ทิชันระบบที่ไม่มีอักษรไดรฟ์) และ _Total
#   cpu_iowait ไม่มีบน Windows -> ใช้เวลา interrupt + DPC (งานของ driver/ฮาร์ดแวร์) แทน
#   load average ไม่มีบน Windows -> load1 = processor queue length (จำนวนงานที่รอ CPU)
# ---------------------------------------------------------------------------
_WIN_VOL = 'volume!~"HarddiskVolume.*|_Total"'
_WIN_NIC = 'nic!~"(?i).*(isatap|loopback|teredo|pseudo).*"'


def _win_disk_used_pct(l):
    return (
        f'label_replace((1 - windows_logical_disk_free_bytes{{{l},{_WIN_VOL}}} / '
        f'windows_logical_disk_size_bytes{{{l},{_WIN_VOL}}}) * 100, "mountpoint", "$1", "volume", "(.*)")'
    )


def _none(l):
    return f"cm_no_such_metric{{{l}}}"           # ไม่มีค่า (หน้าเว็บแสดง "-")


WIN_PROMQL = {
    "cpu": lambda l: f'100 - (avg(rate(windows_cpu_time_total{{{l},mode="idle"}}[5m])) * 100)',
    "cpu_core": lambda l: (
        f'label_replace(100 - (avg by(core) (rate(windows_cpu_time_total{{{l},mode="idle"}}[5m])) * 100),'
        f' "cpu", "$1", "core", "(?:.*,)?([0-9]+)")'
    ),
    "memory": lambda l: (
        f"(1 - max(windows_os_physical_memory_free_bytes{{{l}}}) / max(windows_cs_physical_memory_bytes{{{l}}})) * 100"
    ),
    "disk": lambda l: f"max({_win_disk_used_pct(l)})",
    "disk_mount": _win_disk_used_pct,
    "disk_read": lambda l: f"sum(rate(windows_logical_disk_read_bytes_total{{{l},{_WIN_VOL}}}[5m])) / 1024 / 1024",
    "disk_write": lambda l: f"sum(rate(windows_logical_disk_write_bytes_total{{{l},{_WIN_VOL}}}[5m])) / 1024 / 1024",
    "io": lambda l: (
        f"(sum(rate(windows_logical_disk_read_bytes_total{{{l},{_WIN_VOL}}}[5m])) + "
        f"sum(rate(windows_logical_disk_write_bytes_total{{{l},{_WIN_VOL}}}[5m]))) / 1024 / 1024"
    ),
    "io_util": lambda l: (
        f"clamp_min(max(100 - rate(windows_logical_disk_idle_seconds_total{{{l},{_WIN_VOL}}}[5m]) * 100), 0)"
    ),
    "net_rx": lambda l: f"sum(rate(windows_net_bytes_received_total{{{l},{_WIN_NIC}}}[5m])) / 1024 / 1024",
    "net_tx": lambda l: f"sum(rate(windows_net_bytes_sent_total{{{l},{_WIN_NIC}}}[5m])) / 1024 / 1024",
    "cpu_user": lambda l: f'avg(rate(windows_cpu_time_total{{{l},mode="user"}}[5m])) * 100',
    "cpu_system": lambda l: f'avg(rate(windows_cpu_time_total{{{l},mode="privileged"}}[5m])) * 100',
    "cpu_iowait": lambda l: f'sum(avg by(mode) (rate(windows_cpu_time_total{{{l},mode=~"interrupt|dpc"}}[5m]))) * 100',
    "load1": lambda l: f"max(windows_system_processor_queue_length{{{l}}})",
    "load5": _none,
    "load15": _none,
    "cores": lambda l: f'count(windows_cpu_time_total{{{l},mode="idle"}})',
}


def _is_windows(labels):
    return str(labels).startswith("cm_web=")


def _pick(key):
    return lambda l: (WIN_PROMQL.get(key, _none) if _is_windows(l) else LINUX_PROMQL[key])(l)


# ใช้ PROMQL[key](labels) ได้ทั้ง Linux และ Windows (เลือกจาก labels)
PROMQL = {key: _pick(key) for key in LINUX_PROMQL}

# ค่าที่ดึงจาก snapshot ของ puller มาเสริม (Prometheus ไม่มี) -> system_state.py
SNAPSHOT_METRIC_KEYS = (
    "load",
    "load_1m",
    "load_5m",
    "load_15m",
    "memory_used",
    "memory_available",
    "swap",
    "disk_available",
    "inode",
    "net_rx_errors",
    "net_tx_errors",
    "uptime",
)


def _round(value):
    return round(value, 2) if value is not None else None


def _finite(value):
    return value is not None and value == value and value not in (float("inf"), float("-inf"))


def get_disk_mounts(labels):
    """[{mountpoint, device, fstype, used}] เรียงจากเต็มมากไปน้อย (mount ซ้ำ -> เอาอันแรก)"""
    seen, out = set(), []
    for metric, value in prom_vector(PROMQL["disk_mount"](labels)):
        mount = metric.get("mountpoint", "?")
        if mount in seen or not _finite(value):
            continue
        seen.add(mount)
        out.append({
            "mountpoint": mount,
            "device": metric.get("device"),
            "fstype": metric.get("fstype"),
            "used": round(value, 2),
        })
    return sorted(out, key=lambda m: m["used"], reverse=True)


def get_cpu_per_core(labels):
    """[{core, usage}] CPU % ต่อ core (เฉลี่ย 5 นาที จาก Prometheus) เรียงตามเลข core"""
    out = []
    for metric, value in prom_vector(PROMQL["cpu_core"](labels)):
        try:
            core = int(metric.get("cpu"))
        except (TypeError, ValueError):
            continue
        if _finite(value):
            out.append({"core": core, "usage": round(value, 2)})
    return sorted(out, key=lambda c: c["core"])


# ---------------------------------------------------------------------------
# Windows: service / process จาก Prometheus (agent ไม่มี snapshot ของ puller)
# ---------------------------------------------------------------------------

def get_windows_services(labels):
    """[{name, status}] จาก windows_service_state (ค่า 1 = สถานะปัจจุบันของ service)"""
    out = {}
    for metric, value in prom_vector(f"windows_service_state{{{labels}}} == 1"):
        name = metric.get("name")
        if name:
            out[name] = str(metric.get("state") or "unknown").replace(" ", "-")
    return [{"name": n, "status": s} for n, s in sorted(out.items(), key=lambda x: x[0].lower())]


def get_windows_processes(labels, limit=15):
    """[{name, pid, cpu %, memory %, rss_mb}] process ที่ใช้ CPU / RAM สูงสุด (windows_process_*)"""
    skip = 'process!~"_Total|Idle"'
    cores = f'scalar(count(windows_cpu_time_total{{{labels},mode="idle"}}))'
    cpu = prom_vector(f"topk({limit}, sum by(process, process_id) "
                      f"(rate(windows_process_cpu_time_total{{{labels},{skip}}}[2m])) * 100 / {cores})")
    mem = prom_vector(f"topk({limit}, sum by(process, process_id) "
                      f"(windows_process_working_set_private_bytes{{{labels},{skip}}}))")
    total = prom_value(f"max(windows_cs_physical_memory_bytes{{{labels}}})")
    rows = {}
    for metric, value in cpu:
        key = (metric.get("process"), metric.get("process_id"))
        rows.setdefault(key, {"name": key[0], "pid": key[1]})["cpu"] = round(value, 2) if _finite(value) else None
    for metric, value in mem:
        key = (metric.get("process"), metric.get("process_id"))
        r = rows.setdefault(key, {"name": key[0], "pid": key[1]})
        if _finite(value):
            r["rss_mb"] = round(value / 1048576, 1)
            r["memory"] = round(value / total * 100, 2) if total else None
    return list(rows.values())


# ---------------------------------------------------------------------------
# 1) ค่าตอนนี้
# ---------------------------------------------------------------------------

def get_current_metrics(web):
    """ค่าล่าสุดของเครื่อง + service/process/event จาก snapshot

    คีย์ที่ไม่มีข้อมูลจะเป็น None (หน้าเว็บโชว์ "-") ไม่ทำให้ error
    web_requests / worker_queue / db_connections / edge_connections ยังไม่มีแหล่งข้อมูล
    ในระบบนี้ จึงเป็น None เสมอ (หน้าเว็บซ่อนช่องที่ไม่มีค่า)
    """
    metrics = {
        "cpu": None,
        "memory": None,
        "disk": None,
        "io": None,
        "io_util": None,
        "disk_read": None,
        "disk_write": None,
        "net_rx": None,
        "net_tx": None,
        "web_requests": None,
        "worker_queue": None,
        "db_connections": None,
        "edge_connections": None,
    }

    config = get_web_prometheus_config(web)

    if not config:
        return metrics

    labels = _labels(config)

    for name in ("cpu", "memory", "disk", "disk_read", "disk_write",
                 "net_rx", "net_tx", "io_util"):
        metrics[name] = _round(prom_value(PROMQL[name](labels)))

    metrics["disk_mounts"] = get_disk_mounts(labels)
    metrics["cpu_per_core"] = get_cpu_per_core(labels)

    # io = อ่าน + เขียน (ถ้ามีฝั่งใดฝั่งหนึ่ง)
    if metrics["disk_read"] is not None or metrics["disk_write"] is not None:
        metrics["io"] = round(
            (metrics["disk_read"] or 0) + (metrics["disk_write"] or 0), 2
        )

    if config.get("os") == "windows":
        from datetime import datetime, timezone
        metrics["load_1m"] = _round(prom_value(PROMQL["load1"](labels)))
        metrics["services"] = get_windows_services(labels)
        metrics["processes"] = get_windows_processes(labels)
        metrics["cpu_cores"] = {}
        metrics["system_events"] = []
        metrics["system_timestamp"] = datetime.now(timezone.utc).isoformat()
        return metrics

    # เสริมด้วยสถานะเครื่องที่ puller เก็บ (Prometheus ยังเป็นแหล่งหลักของค่าสด)
    system = load_system_snapshot(web)

    for key in SNAPSHOT_METRIC_KEYS:
        if system.get(key) is not None:
            metrics[key] = system[key]

    metrics["services"] = system.get("services", [])
    metrics["processes"] = system.get("processes", [])
    metrics["cpu_cores"] = system.get("cpu_cores") or {}
    metrics["system_events"] = system.get("system_events", [])
    metrics["system_timestamp"] = system.get("system_timestamp")

    return metrics


def classify_state(metrics):
    """สรุปสถานะเครื่อง: PROBLEM ถ้า CPU/Memory/Disk ตัวใดตัวหนึ่ง >= 90%,
    WARNING ถ้า >= 80%, ไม่งั้น HEALTHY"""
    values = [metrics.get(k) for k in ("cpu", "memory", "disk")]
    values = [v for v in values if v is not None]

    if any(v >= 90 for v in values):
        return "PROBLEM"

    if any(v >= 80 for v in values):
        return "WARNING"

    return "HEALTHY"


# ---------------------------------------------------------------------------
# 2) กราฟย้อนหลัง
# ---------------------------------------------------------------------------

HISTORY_SERIES = ("cpu", "memory", "disk", "io", "io_util")

# ชุดกราฟที่หน้าเว็บเลือกได้ (แท็บของกราฟ System Load)  /api/metrics/history?set=<ชื่อ>
#   main = ค่าเริ่มต้น (การ์ด KPI ใช้ชุดนี้ด้วย)
HISTORY_SETS = {
    "main": HISTORY_SERIES,
    "cpu": ("cpu_user", "cpu_system", "cpu_iowait"),
    "io": ("disk_read", "disk_write"),
    "net": ("net_rx", "net_tx"),
    "load": ("load1", "load5", "load15"),
}


def get_cpu_cores(web):
    """จำนวน core ของเครื่อง (จาก node_exporter) ไม่รู้ -> None"""
    config = get_web_prometheus_config(web)
    if not config:
        return None
    value = prom_value(PROMQL["cores"](_labels(config)))
    return int(value) if value else None


def get_metrics_history(web, start, end, step, names=HISTORY_SERIES, shift=0):
    """อนุกรมเวลาของ names คืน {"cpu": [[unix, value], ...], ...}

    shift = เลื่อนเวลาย้อนหลังกี่วินาที (เช่น 86400 = "เมื่อวานเวลาเดียวกัน")
            ผลลัพธ์ถูกเลื่อนเวลากลับมาให้ทับกับช่วงปัจจุบัน วาดซ้อนกันได้ทันที
    เครื่องที่ไม่รู้จัก -> {} ; series ที่ query ล้มเหลว -> [] (กราฟอื่นยังแสดงได้)
    """
    config = get_web_prometheus_config(web)

    if not config:
        return {}

    labels = _labels(config)
    series = {}

    for name in names:
        try:
            data = prometheus_query_range(PROMQL[name](labels), start - shift, end - shift, step)
            values = data["data"]["result"][0]["values"]

            series[name] = [
                [float(t) + shift, round(float(v), 2)]
                for t, v in values
                if v not in ("NaN", "+Inf", "-Inf")
            ]

        except Exception as error:
            print(f"[HISTORY ERROR] web={web} metric={name} error={error}")
            series[name] = []

    return series


# ---------------------------------------------------------------------------
# 3) ค่า ณ เวลาที่ log เกิด
# ---------------------------------------------------------------------------

_EMPTY_AT_TIME = {
    "cpu": None,
    "memory": None,
    "disk": None,
    "io": None,
    "disk_read": None,
    "disk_write": None,
    "net_rx": None,
    "net_tx": None,
}


def get_metrics_at_time(timestamp, web):
    """ค่า metric ของเครื่อง ณ เวลา timestamp (ข้อความ ISO) เพื่อดูว่าตอน log นั้น
    เครื่องเป็นอย่างไร  ไม่มีเวลา/ไม่รู้จักเครื่อง/แปลงเวลาไม่ได้ -> ทุกค่า None"""
    metrics = dict(_EMPTY_AT_TIME)

    config = get_web_prometheus_config(web)
    unix_time = to_unix(timestamp)

    if not config or unix_time is None:
        return metrics

    labels = _labels(config)

    raw = {
        name: prom_value(PROMQL[name](labels), unix_time)
        for name in ("cpu", "memory", "disk", "disk_read", "disk_write", "net_rx", "net_tx")
    }

    # รวม io จากค่าดิบก่อนปัดเศษ (ปัดทีหลังเพื่อไม่ให้เศษซ้อนกัน)
    if raw["disk_read"] is not None and raw["disk_write"] is not None:
        raw["io"] = raw["disk_read"] + raw["disk_write"]

    for name, value in raw.items():
        metrics[name] = _round(value)

    return metrics
