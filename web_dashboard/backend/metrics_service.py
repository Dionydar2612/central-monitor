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

from clients import prom_value, prometheus_query_range
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

        prometheus = target.get("prometheus", {})
        job = prometheus.get("job")
        instance = prometheus.get("instance")

        if not job or not instance:
            return None

        return {"job": job, "instance": instance}

    return None


def _labels(config):
    """ส่วน {...} ที่ใช้กรอง series ของเครื่องนี้ใน PromQL"""
    return f'job="{config["job"]}",instance="{config["instance"]}"'


# ---------------------------------------------------------------------------
# PromQL ของแต่ละ metric (นิยามที่เดียว ใช้ร่วมกันทั้ง 3 รูปแบบ)
#   ทุก query ใช้ rate(...[5m]) เฉลี่ยย้อนหลัง 5 นาที
#   หน่วย: CPU/Memory/Disk/io_util = เปอร์เซ็นต์, disk_*/net_*/io = MB/s
# ---------------------------------------------------------------------------

# "/" ที่เป็นดิสก์จริง (ตัด tmpfs และ overlay ของ container ออก)
_ROOT_FS = 'mountpoint="/",fstype!~"tmpfs|overlay"'

PROMQL = {
    # CPU % = 100 - เวลา idle เฉลี่ย
    "cpu": lambda l: (
        f'100 - (avg by(instance) (rate(node_cpu_seconds_total{{{l},mode="idle"}}[5m])) * 100)'
    ),
    # Memory % = 1 - available/total
    "memory": lambda l: (
        f"(1 - node_memory_MemAvailable_bytes{{{l}}} / node_memory_MemTotal_bytes{{{l}}}) * 100"
    ),
    # Disk % ของ "/"
    "disk": lambda l: (
        f"(1 - node_filesystem_avail_bytes{{{l},{_ROOT_FS}}} / "
        f"node_filesystem_size_bytes{{{l},{_ROOT_FS}}}) * 100"
    ),
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
}

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

    # io = อ่าน + เขียน (ถ้ามีฝั่งใดฝั่งหนึ่ง)
    if metrics["disk_read"] is not None or metrics["disk_write"] is not None:
        metrics["io"] = round(
            (metrics["disk_read"] or 0) + (metrics["disk_write"] or 0), 2
        )

    # เสริมด้วยสถานะเครื่องที่ puller เก็บ (Prometheus ยังเป็นแหล่งหลักของค่าสด)
    system = load_system_snapshot(web)

    for key in SNAPSHOT_METRIC_KEYS:
        if system.get(key) is not None:
            metrics[key] = system[key]

    metrics["services"] = system.get("services", [])
    metrics["processes"] = system.get("processes", [])
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


def get_metrics_history(web, start, end, step):
    """อนุกรมเวลาของ HISTORY_SERIES คืน {"cpu": [[unix, value], ...], ...}

    เครื่องที่ไม่รู้จัก -> {} ; series ที่ query ล้มเหลว -> [] (กราฟอื่นยังแสดงได้)
    """
    config = get_web_prometheus_config(web)

    if not config:
        return {}

    labels = _labels(config)
    series = {}

    for name in HISTORY_SERIES:
        try:
            data = prometheus_query_range(PROMQL[name](labels), start, end, step)
            values = data["data"]["result"][0]["values"]

            series[name] = [
                [float(t), round(float(v), 2)]
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
