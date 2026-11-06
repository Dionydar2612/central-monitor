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
    confirm_checks: 6   สถานะจะเปลี่ยนเมื่อเห็นค่าใหม่ติดกัน N รอบ (6 รอบ x 30 วินาที = 3 นาที) ค่ากระตุกไม่นับ
                        ตั้งได้ที่หน้า Settings > Alerts ("แจ้งเตือนเมื่อเกินนาน X นาที")
    hysteresis: 2       ขึ้น Warning ที่ 80% แต่จะกลับเป็น OK เมื่อต่ำกว่า 78% (80 - 2) -> ไม่เด้ง OK/Warning ไปมาที่ 79-81%
    memory / disk ใช้ "ค่าสูงสุดใน 1 นาทีล่าสุด" (Prometheus max_over_time) ตัดสินสถานะ
        -> ค่าที่พุ่งขึ้นแล้วลงระหว่างรอบตรวจ (เช่น disk เต็มชั่วครู่) ก็ยังจับได้ ถ้า Prometheus scrape ทัน (ทุก 15 วินาที)

ประวัติปัญหา (incident)
    เริ่มเมื่อ sensor ออกจาก OK  จบเมื่อกลับเป็น OK  เก็บ: เริ่ม, จบ, นานเท่าไหร่, สถานะแย่สุด, ค่าสูงสุด
    ตอนจบเขียน System log "Recovered → OK after 25m (Error · peak 95.2%)"
    -> data/state/incidents.json  เก็บตาม storage.incident_retention (ปริยาย 30d)

เกณฑ์ (puller.yaml -> thresholds: (Linux)  thresholds_windows: (Windows)  targets[].thresholds: เฉพาะเครื่อง)
    Linux    cpu 75/90 · memory 80/90 · disk 80/90
    Windows  cpu 80/95 · memory 80/95 · disk 80/90 · RAM ว่าง <20/<10 %
    ทั้งคู่   disk latency 20/50 ms · packet loss 1/5 % · ping latency 100/250 ms  (warning/error)

sensor เพิ่มเติม
    mem_available        RAM ว่างเหลือ % (Windows)
    disk_latency         latency เฉลี่ยต่อ I/O ของไดรฟ์ที่ช้าที่สุด (ms)
    packet_loss / net_latency   ping จาก central (blackbox_exporter, เฉพาะเครื่องที่มี host)
    service:<ชื่อ>        service สำคัญของ Windows (sensors.critical_services) หยุดทั้งที่ตั้ง Automatic -> Error
    bottleneck:<cpu|memory|disk|network>   ดูหลายค่าพร้อมกัน เกิน >= 2 ค่า = Bottleneck, ค่าเดียว = trend (OK)

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

# เกณฑ์ปริยาย (warning / error)  ปรับได้ที่หน้า Settings > Alerts (puller.yaml thresholds / thresholds_windows)
#   และเฉพาะเครื่องที่ targets[].thresholds
#   mem_available = "RAM ว่างเหลือ %" ยิ่งต่ำยิ่งแย่ (warning เมื่อต่ำกว่า 20, error ต่ำกว่า 10)
DEFAULT_THRESHOLDS = {                     # Linux (ค่าเดิม) + ชนิดใหม่
    "cpu": {"warning": 75, "error": 90},
    "memory": {"warning": 80, "error": 90},
    "disk": {"warning": 80, "error": 90},
    "disk_latency": {"warning": 20, "error": 50},       # ms
    "packet_loss": {"warning": 1, "error": 5},          # %
    "net_latency": {"warning": 100, "error": 250},      # ms
    "mem_available": {"warning": 20, "error": 10},      # % (ต่ำ = แย่)
}
WINDOWS_DEFAULT_THRESHOLDS = {             # ตามสเปก Windows Monitoring
    "cpu": {"warning": 80, "error": 95},
    "memory": {"warning": 80, "error": 95},
    "disk": {"warning": 80, "error": 90},
    "disk_latency": {"warning": 20, "error": 50},
    "packet_loss": {"warning": 1, "error": 5},
    "net_latency": {"warning": 100, "error": 250},
    "mem_available": {"warning": 20, "error": 10},
}
LOW_IS_BAD = ("mem_available",)
UNITS = {"cpu": "%", "memory": "%", "disk": "%", "disk_latency": "ms", "packet_loss": "%",
         "net_latency": "ms", "mem_available": "%"}

# service สำคัญของ Windows (แจ้งเตือนเมื่อหยุด และตั้งเป็น Automatic)  ปรับได้ที่ sensors.critical_services
DEFAULT_CRITICAL_SERVICES = ["WinDefend", "wuauserv", "TermService", "Dnscache", "Dhcp", "EventLog"]

# สัญญาณประกอบของ Bottleneck (ค่าคงที่ ไม่ขึ้นกับเครื่อง)
BOTTLENECK_LIMITS = {
    "cpu_queue_per_core": 2.0,       # งานที่รอ CPU ต่อ core (Windows: Processor Queue Length / Linux: load1)
    "pages_input_per_sec": 100.0,    # Windows: หน้าหน่วยความจำที่ต้องอ่านจากดิสก์ (hard fault) ต่อวินาที
    "swap_pages_per_sec": 100.0,     # Linux: swap in + out ต่อวินาที
    "commit_pct": 90.0,              # committed / commit limit
    "disk_queue": 2.0,               # คำขอที่รอดิสก์
    "disk_busy_pct": 90.0,           # % เวลาที่ดิสก์ไม่ว่าง
    "net_util_pct": 80.0,            # % ของความเร็วการ์ด
    "net_errors_per_sec": 1.0,       # error + discard ต่อวินาที
    "net_output_queue": 2.0,         # แพ็กเก็ตรอส่ง
    "tcp_retransmit_pct": 2.0,       # % segment ที่ส่งซ้ำ
}

_REAL_FS = (
    'fstype!~"tmpfs|devtmpfs|overlay|squashfs|ramfs|nsfs|autofs|efivarfs|fuse.lxcfs",'
    'mountpoint!~"/(run|snap|var/lib/docker|var/lib/kubelet|var/lib/containerd)(/.*)?"'
)
_LINUX_DISK = 'device!~"(loop|ram|fd|sr|dm-).*"'
_LINUX_DEV = 'device!~"(lo|veth.*|docker.*|br-.*|virbr.*|cni.*|flannel.*|cali.*)"'
_WIN_VOL = 'volume!~"HarddiskVolume.*|_Total"'
_WIN_NIC = 'nic!~"(?i).*(isatap|loopback|teredo|pseudo).*"'
STATUS_TEXT = {"ok": "OK", "warning": "Warning", "error": "Error", "down": "Down",
               "paused": "Paused", "unknown": "Unknown"}
RANK = {"ok": 0, "paused": 0, "unknown": 1, "warning": 2, "error": 3, "down": 4}
PROBLEM = ("warning", "error", "down", "unknown")
AGENT_TIMEOUT = 180          # วินาที: agent Windows ไม่ส่งสัญญาณนานกว่านี้ = Down


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
            if v == v and v not in (float("inf"), float("-inf")):      # ตัด NaN / Inf
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
# PromQL ต่อ OS  (Windows รองรับชื่อ metric ทั้ง windows_exporter >= 0.31 และชื่อเก่า)
# ------------------------------------------------------------------

def _win_exprs(l):
    cpu = f'{{__name__=~"windows_cpu_time(_seconds)?_total",{l}'
    total = f'max({{__name__=~"windows_memory_physical_total_bytes|windows_cs_physical_memory_bytes",{l}}})'
    avail = (f"(max(windows_memory_available_bytes{{{l}}}) or max(windows_memory_physical_free_bytes{{{l}}})"
             f" or max(windows_os_physical_memory_free_bytes{{{l}}}))")
    vol = f"{l},{_WIN_VOL}"
    nic = f"{l},{_WIN_NIC}"
    ops = f"(rate(windows_logical_disk_reads_total{{{vol}}}[5m]) + rate(windows_logical_disk_writes_total{{{vol}}}[5m]))"
    busy_t = (f"(rate(windows_logical_disk_read_seconds_total{{{vol}}}[5m]) + "
              f"rate(windows_logical_disk_write_seconds_total{{{vol}}}[5m]))")
    cores = f'count(count by(core) ({cpu},mode="idle"}}))'
    return {
        "cpu": f'100 - (avg(rate({cpu},mode="idle"}}[5m])) * 100)',
        "memory": f"(1 - {avail} / {total}) * 100",
        "mem_available": f"{avail} / {total} * 100",
        "disk": (f'label_replace((1 - windows_logical_disk_free_bytes{{{vol}}} / windows_logical_disk_size_bytes{{{vol}}})'
                 f' * 100, "mountpoint", "$1", "volume", "(.*)")'),
        # latency เฉลี่ยต่อ I/O (ms) ต่อไดรฟ์ เฉพาะไดรฟ์ที่มี I/O >= 1 ครั้ง/วินาที (ไดรฟ์ว่างค่าไม่นิ่ง)
        "disk_latency_vec": f'label_replace({busy_t} / {ops} * 1000 and {ops} >= 1, "mountpoint", "$1", "volume", "(.*)")',
        "disk_queue": f"max(windows_logical_disk_requests_queued{{{vol}}})",
        "disk_busy": f"max(100 - clamp_max(rate(windows_logical_disk_idle_seconds_total{{{vol}}}[5m]), 1) * 100)",
        "cpu_queue_per_core": f'max({{__name__=~"windows_system_processor_queue(_length)?",{l}}}) / {cores}',
        "pages_in": f"sum(rate(windows_memory_swap_pages_read_total{{{l}}}[5m]))",
        "commit_pct": f"max(windows_memory_committed_bytes{{{l}}}) / max(windows_memory_commit_limit_bytes{{{l}}}) * 100",
        "net_util": (f"max((rate(windows_net_bytes_received_total{{{nic}}}[5m]) > rate(windows_net_bytes_sent_total{{{nic}}}[5m])"
                     f" or rate(windows_net_bytes_sent_total{{{nic}}}[5m])) / (windows_net_current_bandwidth_bytes{{{nic}}} > 0) * 100)"),
        "net_errors": (f"sum(rate(windows_net_packets_received_errors_total{{{nic}}}[5m]) + rate(windows_net_packets_outbound_errors_total{{{nic}}}[5m])"
                       f" + rate(windows_net_packets_received_discarded_total{{{nic}}}[5m]) + rate(windows_net_packets_outbound_discarded_total{{{nic}}}[5m]))"),
        "net_oq": f"max(windows_net_output_queue_length_packets{{{nic}}})",
        "tcp_retx": (f"sum(rate(windows_tcp_segments_retransmitted_total{{{l}}}[5m])) / "
                     f"sum(rate(windows_tcp_segments_sent_total{{{l}}}[5m])) * 100"),
    }


def _linux_exprs(l):
    fs = f"{l},{_REAL_FS}"
    dk = f"{l},{_LINUX_DISK}"
    dev = f"{l},{_LINUX_DEV}"
    ops = f"(rate(node_disk_reads_completed_total{{{dk}}}[5m]) + rate(node_disk_writes_completed_total{{{dk}}}[5m]))"
    busy_t = (f"(rate(node_disk_read_time_seconds_total{{{dk}}}[5m]) + "
              f"rate(node_disk_write_time_seconds_total{{{dk}}}[5m]))")
    return {
        "cpu": f'100 - (avg by(instance) (rate(node_cpu_seconds_total{{{l},mode="idle"}}[5m])) * 100)',
        "memory": f"(1 - node_memory_MemAvailable_bytes{{{l}}} / node_memory_MemTotal_bytes{{{l}}}) * 100",
        "mem_available": f"node_memory_MemAvailable_bytes{{{l}}} / node_memory_MemTotal_bytes{{{l}}} * 100",
        "disk": (f"(1 - node_filesystem_avail_bytes{{{fs}}} / "
                 f"node_filesystem_size_bytes{{{fs}}}) * 100"),
        "disk_latency_vec": f'label_replace({busy_t} / {ops} * 1000 and {ops} >= 1, "mountpoint", "$1", "device", "(.*)")',
        "disk_queue": f"max(rate(node_disk_io_time_weighted_seconds_total{{{dk}}}[5m]))",
        "disk_busy": f"max(rate(node_disk_io_time_seconds_total{{{dk}}}[5m]) * 100)",
        "cpu_queue_per_core": f'max(node_load1{{{l}}}) / count(node_cpu_seconds_total{{{l},mode="idle"}})',
        "pages_in": f"sum(rate(node_vmstat_pswpin{{{l}}}[5m])) + sum(rate(node_vmstat_pswpout{{{l}}}[5m]))",
        "commit_pct": f"max(node_memory_Committed_AS_bytes{{{l}}}) / max(node_memory_CommitLimit_bytes{{{l}}}) * 100",
        "net_util": (f"max((rate(node_network_receive_bytes_total{{{dev}}}[5m]) > rate(node_network_transmit_bytes_total{{{dev}}}[5m])"
                     f" or rate(node_network_transmit_bytes_total{{{dev}}}[5m])) / (node_network_speed_bytes{{{dev}}} > 0) * 100)"),
        "net_errors": (f"sum(rate(node_network_receive_errs_total{{{dev}}}[5m]) + rate(node_network_transmit_errs_total{{{dev}}}[5m])"
                       f" + rate(node_network_receive_drop_total{{{dev}}}[5m]) + rate(node_network_transmit_drop_total{{{dev}}}[5m]))"),
        "net_oq": None,
        "tcp_retx": (f"sum(rate(node_netstat_Tcp_RetransSegs{{{l}}}[5m])) / "
                     f"sum(rate(node_netstat_Tcp_OutSegs{{{l}}}[5m])) * 100"),
    }


def _ping_exprs(name):
    sel = f'cm_target="{name}"'
    return {
        "net_latency": f'avg(avg_over_time(probe_icmp_duration_seconds{{{sel},phase="rtt"}}[5m])) * 1000',
        "packet_loss": f"(1 - avg(avg_over_time(probe_success{{{sel}}}[5m]))) * 100",
        "samples": f"sum(count_over_time(probe_success{{{sel}}}[5m]))",
    }


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
    """(เกณฑ์ Linux, เกณฑ์ Windows) ทั้งระบบ"""
    return (_merge_thresholds(DEFAULT_THRESHOLDS, config.get("thresholds")),
            _merge_thresholds(WINDOWS_DEFAULT_THRESHOLDS, config.get("thresholds_windows")))


def _is_windows_target(t):
    return str(t.get("type") or "ssh") == "agent" or str(t.get("os") or "").lower() == "windows"


def _options(config):
    s = config.get("sensors") or {}
    try:
        confirm = max(1, min(int(s.get("confirm_checks", 6)), 120))     # 6 รอบ x 30 วินาที = 3 นาที
    except (TypeError, ValueError):
        confirm = 6
    try:
        hyst = max(0.0, min(float(s.get("hysteresis", 2)), 20.0))
    except (TypeError, ValueError):
        hyst = 2.0
    try:
        retention = parse_interval((config.get("storage") or {}).get("incident_retention", "30d"))
    except Exception:
        retention = 30 * 86400
    crit = s.get("critical_services")
    if not isinstance(crit, list):
        crit = DEFAULT_CRITICAL_SERVICES
    return {"confirm": confirm, "hyst": hyst, "retention": retention,
            "critical_services": [str(x) for x in crit if str(x).strip()],
            "bottleneck": s.get("bottleneck", True) is not False}


def _group(status):
    """กลุ่มของสถานะสำหรับการนับยืนยัน: ok / problem (warning, error, down) / unknown"""
    if status in ("warning", "error", "down"):
        return "problem"
    return status


def _grade(value, th, prev=None, hyst=0.0, low=False):
    """ระดับของค่า (ใช้ hysteresis: ต้องลดต่ำกว่าเกณฑ์ - hyst ก่อนจะลดระดับ)  low=True: ค่ายิ่งต่ำยิ่งแย่"""
    if value is None:
        return "unknown"
    if low:
        return _grade(-value, {"warning": -th["warning"], "error": -th["error"]}, prev, hyst)
    if value >= th["error"]:
        return "error"
    if prev == "error" and value >= th["error"] - hyst:
        return "error"
    if value >= th["warning"]:
        return "warning"
    if prev in ("warning", "error") and value >= th["warning"] - hyst:
        return "warning"
    return "ok"


def _limit_msg(label, value, th, status, peak=None, unit="%", low=False):
    pk = f" (peak {peak:.1f}{unit} in last minute)" if peak is not None and peak - value >= 1 else ""
    cmp = "below" if low else "limit"
    if status == "error":
        return f"{value:.1f}{unit} {label}{pk} - error {cmp} {th['error']:g}{unit}"
    if status == "warning":
        return f"{value:.1f}{unit} {label}{pk} - warning {cmp} {th['warning']:g}{unit}"
    return f"{value:.1f}{unit} {label}{pk}"


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
# Bottleneck: ดูหลายค่าพร้อมกัน
#   สัญญาณเกินเกณฑ์ >= 2 ตัว  -> "bottleneck"  (Warning / Error ถ้ามีตัวใดถึงระดับ error)
#   เกินตัวเดียว              -> "trend"       (สถานะ OK แต่แสดงในแท็บ Bottleneck ว่ามีแนวโน้ม)
#   ไม่มีข้อมูลเลย             -> ไม่สร้าง sensor (ไม่เดา)
# ------------------------------------------------------------------

ADVICE = {
    "cpu": {
        "bottleneck": "CPU ไม่พอ: งานรอคิว CPU มาก ดูแท็บ Processes ว่า process ไหนกิน CPU สูง "
                      "ลด/ย้ายงาน, ตรวจ process ที่ค้าง หรือเพิ่มจำนวน core",
        "trend": "CPU สูงแต่ยังไม่มีงานรอคิว ติดตามต่อ ถ้าค่าสูงต่อเนื่องให้ดู process ที่ใช้ CPU",
    },
    "memory": {
        "bottleneck": "RAM ไม่พอ: ระบบเริ่มใช้ page file/swap (ช้าลงมาก) ดู process ที่ใช้ RAM สูง "
                      "ตรวจ memory leak, รีสตาร์ต service ที่บวม หรือเพิ่ม RAM",
        "trend": "การใช้ RAM สูง แต่ยังไม่มีการ swap/page หนัก ติดตามแนวโน้ม",
    },
    "disk": {
        "bottleneck": "ดิสก์ตอบช้า: latency สูงพร้อมคิวรอ/ดิสก์ไม่ว่าง ดู process ที่อ่าน/เขียนมาก (แท็บ Processes) "
                      "ย้ายงานหนักออกนอกเวลา, ตรวจ antivirus scan / backup, หรือย้ายไปดิสก์ที่เร็วกว่า (SSD)",
        "trend": "ค่าดิสก์ตัวหนึ่งสูงชั่วคราว ยังไม่ถึงขั้นคอขวด ติดตามต่อ",
    },
    "network": {
        "bottleneck": "เครือข่ายมีปัญหา: ตรวจ bandwidth ของการ์ด/สวิตช์, สาย LAN, error/discard ที่การ์ด, "
                      "และเส้นทางระหว่าง central กับเครื่องนี้ (ping loss / latency)",
        "trend": "ค่าเครือข่ายตัวหนึ่งผิดปกติ ยังไม่ยืนยันว่าเป็นคอขวด ติดตามต่อ",
    },
}
BOTTLENECK_NAMES = {"cpu": "Bottleneck: CPU", "memory": "Bottleneck: Memory",
                    "disk": "Bottleneck: Disk", "network": "Bottleneck: Network"}


def _signal(metric, value, unit, warning, error=None, low=False, note="", same=None):
    """1 สัญญาณ: hit = เกิน warning, level = warning/error"""
    if value is None:
        return None
    if low:
        hit = value <= warning
        level = "error" if (error is not None and value <= error) else ("warning" if hit else "ok")
    else:
        hit = value >= warning
        level = "error" if (error is not None and value >= error) else ("warning" if hit else "ok")
    return {"metric": metric, "value": round(value, 2), "unit": unit, "threshold": warning,
            "error_threshold": error, "hit": hit, "level": level, "low": low, "note": note, "same": same or metric}


def _bottleneck(category, signals):
    """-> (status, details) หรือ None ถ้าไม่มีข้อมูล"""
    signals = [s for s in signals if s]
    if not signals:
        return None
    hits = [s for s in signals if s["hit"]]
    # ค่าที่วัดสิ่งเดียวกัน (Memory used % กับ Available RAM %) นับเป็นสัญญาณเดียว
    if len({s["same"] for s in hits}) >= 2:
        state = "bottleneck"
        status = "error" if any(s["level"] == "error" for s in hits) else "warning"
    elif hits:
        state, status = "trend", "ok"
    else:
        state, status = "normal", "ok"
    advice = ADVICE[category].get(state, "")
    return status, {"state": state, "category": category, "signals": signals,
                    "hits": [s["metric"] for s in hits], "advice": advice}


def _bottleneck_message(details):
    if details["state"] == "normal":
        return "normal"
    parts = [f"{s['metric']} {s['value']:g}{s['unit']} ({'<=' if s['low'] else '>='} {s['threshold']:g}{s['unit']})"
             for s in details["signals"] if s["hit"]]
    return f"{details['state']}: " + ", ".join(parts)


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


def _target_sensors(t, global_th, interval, checkpoints, previous, opts):
    """ค่าดิบของทุก sensor ของเครื่อง (status = ระดับจากค่าตอนนี้ ยังไม่ผ่านการยืนยัน N รอบ)"""
    name = t["name"]
    hyst = opts["hyst"]
    windows = _is_windows_target(t)
    th = _merge_thresholds(global_th[1] if windows else global_th[0], t.get("thresholds"))
    base = {"web": name, "host": t.get("host"), "group": t.get("group") or "", "os": "windows" if windows else "linux"}
    prom = t.get("prometheus") or {}
    labels = f'job="{prom.get("job")}",instance="{prom.get("instance")}"'
    out = []

    def prev(sid):
        return (previous.get(f"{name}:{sid}") or {}).get("status")

    def add(sid, kind, label, status, value=None, unit="%", message="", mount=None, peak=None, query=None,
            details=None):
        limits = th.get(kind)
        out.append({**base, "id": f"{name}:{sid}", "kind": kind, "name": label, "status": status,
                    "value": None if value is None else round(value, 2), "unit": unit,
                    "peak": None if peak is None else round(peak, 2),
                    "message": message, "mount": mount, "query": query, "details": details,
                    "low_is_bad": kind in LOW_IS_BAD,
                    "warning": limits["warning"] if limits else None,
                    "error": limits["error"] if limits else None})

    if t.get("paused"):
        add("availability", "availability", "Availability", "paused", unit="", message="Paused by user")
        return out

    agent = str(t.get("type") or "ssh") == "agent"
    if agent:
        # Windows agent: สัญญาณทุก 30 วินาที + metrics แบบ windows_exporter ผ่าน remote write
        labels = f'cm_web="{name}"'
        age = _one(f'time() - max(timestamp({{__name__=~"fluentbit_uptime.*",{labels}}}))')
        if age is None or age > AGENT_TIMEOUT:
            add("availability", "availability", "Availability", "down", unit="",
                message="agent is not sending data (service cm-agent stopped, machine off, or port 4318 blocked)")
            return out
        add("availability", "availability", "Availability", "ok", unit="", message=f"Agent sending ({age:.0f}s ago)")
    else:
        up = _one(f'up{{web="{name}"}}')
        if up is None:
            up = _one(f"up{{{labels}}}")
        if up != 1:
            add("availability", "availability", "Availability", "down", unit="",
                message="node_exporter is not responding (machine down or port 9100 blocked)")
            return out
        add("availability", "availability", "Availability", "ok", unit="", message="Up")
    ex = _win_exprs(labels) if windows and agent else _linux_exprs(labels)

    # --- CPU / Memory / Disk usage ---
    cpu = _one(ex["cpu"])
    st = _grade(cpu, th["cpu"], prev("cpu"), hyst)
    add("cpu", "cpu", "CPU average 5m", st, cpu, query=ex["cpu"],
        message=_limit_msg("CPU", cpu, th["cpu"], st) if cpu is not None else "no data")

    mem = _one(ex["memory"])
    mem_pk = _one(_peak(ex["memory"]))
    judge = max(v for v in (mem, mem_pk) if v is not None) if (mem is not None or mem_pk is not None) else None
    st = _grade(judge, th["memory"], prev("memory"), hyst)
    add("memory", "memory", "Memory Usage", st, mem, peak=mem_pk, query=ex["memory"],
        message=_limit_msg("memory used", mem, th["memory"], st, mem_pk) if mem is not None else "no data")

    mem_av = None
    if windows:
        mem_av = _one(ex["mem_available"])
        if mem_av is not None:
            st = _grade(mem_av, th["mem_available"], prev("mem_available"), hyst, low=True)
            add("mem_available", "mem_available", "Available RAM", st, mem_av, query=ex["mem_available"],
                message=_limit_msg("RAM available", mem_av, th["mem_available"], st, low=True))

    peaks = {m.get("mountpoint"): v for m, v in (_query(_peak(ex["disk"])) or [])}
    seen = set()
    for metric, val in sorted(_query(ex["disk"]) or [], key=lambda r: r[0].get("mountpoint", "")):
        mount = metric.get("mountpoint", "?")
        if mount in seen:
            continue
        seen.add(mount)
        pk = peaks.get(mount)
        st = _grade(max(val, pk if pk is not None else val), th["disk"], prev(f"disk:{mount}"), hyst)
        add(f"disk:{mount}", "disk", f"Disk Usage: {mount}", st, val, mount=mount, peak=pk,
            message=f"{_limit_msg('disk used', val, th['disk'], st, pk)} · free {100 - val:.1f}%")

    # --- Disk latency (ไดรฟ์ที่ช้าที่สุด) ---
    lat_rows = _query(ex["disk_latency_vec"]) or []
    disk_lat, lat_mount = None, None
    if lat_rows:
        m, disk_lat = max(lat_rows, key=lambda r: r[1])
        lat_mount = m.get("mountpoint")
        st = _grade(disk_lat, th["disk_latency"], prev("disk_latency"), hyst)
        add("disk_latency", "disk_latency", "Disk latency", st, disk_lat, unit="ms",
            query=f"max({ex['disk_latency_vec']})",
            message=_limit_msg(f"avg I/O latency ({lat_mount})", disk_lat, th["disk_latency"], st, unit="ms"))

    # --- Ping (blackbox_exporter) ---
    loss = rtt = None
    if t.get("host"):
        pq = _ping_exprs(name)
        samples = _one(pq["samples"])
        if samples:
            loss, rtt = _one(pq["packet_loss"]), _one(pq["net_latency"])
            if loss is not None:
                st = _grade(loss, th["packet_loss"], prev("packet_loss"), hyst)
                add("packet_loss", "packet_loss", "Packet loss (ping)", st, loss, query=pq["packet_loss"],
                    message=_limit_msg(f"loss of {int(samples)} pings in 5m", loss, th["packet_loss"], st))
            if rtt is not None:
                st = _grade(rtt, th["net_latency"], prev("net_latency"), hyst)
                add("net_latency", "net_latency", "Network latency (ping)", st, rtt, unit="ms", query=pq["net_latency"],
                    message=_limit_msg("avg round trip", rtt, th["net_latency"], st, unit="ms"))
            elif loss is not None and loss >= 100:
                add("net_latency", "net_latency", "Network latency (ping)", "error", None, unit="ms",
                    message="no ping reply in 5m")

    # --- Critical services (Windows) ---
    if windows and agent:
        out.extend(_service_sensors(t, labels, opts, add_base=base))

    # --- Bottleneck ---
    if opts["bottleneck"]:
        _bottleneck_sensors(t, ex, th, cpu, mem, mem_av, disk_lat, lat_mount, loss, rtt, windows, add)

    if agent:
        return out                       # agent ไม่ใช้ SSH -> ไม่มี sensor Log pull
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


def _service_sensors(t, labels, opts, add_base):
    """service สำคัญ: หยุด + ตั้ง Automatic -> Error   ไม่มีบนเครื่อง -> ไม่สร้าง sensor"""
    wanted = t.get("critical_services")
    if not isinstance(wanted, list):
        wanted = opts["critical_services"]
    if not wanted:
        return []
    state = {}
    for m, _ in _query(f"windows_service_state{{{labels}}} == 1") or []:
        n = m.get("service") or m.get("name")
        if n:
            state[n.lower()] = (n, m.get("state"))
    mode = {}
    for m, _ in _query(f"windows_service_start_mode{{{labels}}} == 1") or []:
        n = m.get("service") or m.get("name")
        if n:
            mode[n.lower()] = m.get("start_mode")
    if not state:
        return []                        # ไม่มีข้อมูล service (collector ปิด) -> ไม่เดา
    out = []
    for svc in wanted:
        key = str(svc).lower()
        if key not in state:
            continue
        real, st = state[key]
        sm = mode.get(key)
        if st == "running":
            status, msg = "ok", f"running ({sm or 'start mode ?'})"
        elif sm in ("auto", "boot", "system") or sm is None:
            status, msg = "error", f"{st} - start mode {sm or 'unknown'} (ควรทำงานอยู่)"
        else:
            status, msg = "ok", f"{st} - start mode {sm} (เริ่มเมื่อมีงาน/ปิดไว้ ไม่ถือเป็นปัญหา)"
        out.append({**add_base, "id": f"{t['name']}:service:{real}", "kind": "service", "name": f"Service: {real}",
                    "status": status, "value": None, "unit": "", "peak": None, "message": msg, "mount": None,
                    "query": None, "details": {"service": real, "state": st, "start_mode": sm},
                    "low_is_bad": False, "warning": None, "error": None})
    return out


def _bottleneck_sensors(t, ex, th, cpu, mem, mem_av, disk_lat, lat_mount, loss, rtt, windows, add):
    L = BOTTLENECK_LIMITS
    q = {k: ex.get(k) for k in ("cpu_queue_per_core", "pages_in", "commit_pct", "disk_queue", "disk_busy",
                                "net_util", "net_errors", "net_oq", "tcp_retx")}
    val = {k: (_one(v) if v else None) for k, v in q.items()}
    if mem_av is None and mem is not None:
        mem_av = 100 - mem

    groups = {
        "cpu": [
            _signal("CPU usage", cpu, "%", th["cpu"]["warning"], th["cpu"]["error"]),
            _signal("CPU queue per core", val["cpu_queue_per_core"], "", L["cpu_queue_per_core"],
                    note="Processor Queue Length / cores" if windows else "load1 / cores"),
        ],
        "memory": [
            _signal("Memory usage", mem, "%", th["memory"]["warning"], th["memory"]["error"], same="ram"),
            _signal("Available RAM", mem_av, "%", th["mem_available"]["warning"], th["mem_available"]["error"], low=True,
                    same="ram", note="นับรวมกับ Memory usage"),
            _signal("Pages input/sec" if windows else "Swap pages/sec", val["pages_in"], "/s",
                    L["pages_input_per_sec"] if windows else L["swap_pages_per_sec"]),
            _signal("Committed memory", val["commit_pct"], "%", L["commit_pct"]),
        ],
        "disk": [
            _signal(f"Disk latency{f' ({lat_mount})' if lat_mount else ''}", disk_lat, "ms",
                    th["disk_latency"]["warning"], th["disk_latency"]["error"]),
            _signal("Disk queue", val["disk_queue"], "", L["disk_queue"]),
            _signal("Disk busy", val["disk_busy"], "%", L["disk_busy_pct"]),
        ],
        "network": [
            _signal("Bandwidth used", val["net_util"], "%", L["net_util_pct"]),
            _signal("NIC errors+discards", val["net_errors"], "/s", L["net_errors_per_sec"]),
            _signal("Output queue", val["net_oq"], "", L["net_output_queue"]),
            _signal("TCP retransmit", val["tcp_retx"], "%", L["tcp_retransmit_pct"]),
            _signal("Packet loss (ping)", loss, "%", th["packet_loss"]["warning"], th["packet_loss"]["error"]),
            _signal("Latency (ping)", rtt, "ms", th["net_latency"]["warning"], th["net_latency"]["error"]),
        ],
    }
    for cat, sigs in groups.items():
        res = _bottleneck(cat, sigs)
        if res is None:
            continue
        status, details = res
        add(f"bottleneck:{cat}", "bottleneck", BOTTLENECK_NAMES[cat], status, unit="",
            message=_bottleneck_message(details), details=details)


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
            lambda t: _target_sensors(t, th, interval, checkpoints, previous, opts), targets)
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
        #   - sensor ใหม่ / ยังไม่เคยมีข้อมูล (unknown) -> ใช้สถานะแรกที่ได้ทันที
        #   - นับต่อเนื่องตาม "กลุ่ม" (ok / มีปัญหา) ค่าที่แกว่ง Warning <-> Error ยังนับรวมกัน แล้วใช้ระดับล่าสุด
        #     (เดิมนับแยกระดับ: CPU 88% <-> 92% สลับกัน ไม่เคยครบ 2 รอบ ค้าง "seen 1/2")
        if confirmed in (None, "unknown") or raw == "paused" or confirmed == "paused" or raw == confirmed:
            new = raw
            s["pending"] = None
        else:
            pend = old.get("pending") or {}
            same = _group(pend.get("status")) == _group(raw)
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

    per_target = {t["name"]: _merge_thresholds(th[1] if _is_windows_target(t) else th[0], t.get("thresholds"))
                  for t in targets}
    _write(SENSORS_FILE, {"updated_at": now, "thresholds": th[0], "thresholds_windows": th[1], "targets": per_target,
                          "options": {"confirm_checks": opts["confirm"], "hysteresis": opts["hyst"],
                                      "interval": INTERVAL, "incident_retention": opts["retention"],
                                      "alert_after_seconds": opts["confirm"] * INTERVAL,
                                      "critical_services": opts["critical_services"]},
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
