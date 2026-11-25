"""
windows_pull.py - เก็บข้อมูลเครื่อง Windows แบบไม่ลง agent (WinRM -> JEA endpoint CentralMonitor)
==================================================================================================

เครื่องใน puller.yaml ที่ type: winrm  (เพิ่ม / แก้จากหน้า Settings ได้ทั้งหมด)

    - name: alben-2016
      type: winrm
      host: 192.168.181.101
      winrm: {port: 5986, user: cm-monitor, thumbprint: AB12..., event_logs: [System, Application]}
      prometheus: {job: alben-2016-win, instance: 192.168.181.101:5986}     # สร้างให้อัตโนมัติ
      logs:                                       # ไฟล์ log บน Windows (ต้องอยู่ใน allowed-paths.txt ของเครื่องนั้น)
        - {name: application, path: 'D:\\THMux\\logs\\*.log', type: THMux}

    รหัสผ่านไม่อยู่ในไฟล์นี้: data/state/secrets/winrm/<ชื่อ>.json (เข้ารหัส, cm_secrets.py)

ทำงานอย่างไร
    1 thread ต่อเครื่อง ถือ WinRM session ค้างไว้ (เปิดใหม่ทุก 6 ชม.) เรียกฟังก์ชันอ่านตามรอบ
        Get-CMMetrics     ทุก 30 วิ   -> แปลงเป็น metric ชื่อเดียวกับ node_exporter (ด้านล่าง)
        Get-CMEvents      ทุก log_interval (2 นาที)  Event log ระดับ Warning ขึ้นไป -> data/buffer -> OpenSearch
        Get-CMLogTail     ทุก log_interval            ไฟล์ log ที่ตั้งไว้ (ไม่เก็บ INFO) -> data/buffer
        Get-CMServices -All + Get-CMProcesses  ทุก 5 นาที -> data/state/system/<ชื่อ>.json (หน้า Dashboard)
        Get-CMInventory   ทุก 1 ชม. (งานเดียวที่ใช้ WMI)
    CPU ของเครื่อง >= 90% -> ข้าม event / log / process รอบนั้น (เก็บแค่ metric) ไม่ไปเพิ่มภาระ
    ล้ม -> รอ 30 / 60 / 120 / 300 วินาที แล้วลองใหม่ (ไม่ยิงซ้ำตอนเครื่องมีปัญหา)

metric แบบ node_exporter
    puller เปิด HTTP พอร์ต 9110 (ภายใน docker network) ที่ /win/<ชื่อ>  Prometheus มา scrape
    ผ่าน web-nodes.json (prometheus_targets.py) โดยติด label job / instance / web ของเครื่องนั้น
    -> หน้า Dashboard / sensor / bottleneck / custom rules / Daily Summary ใช้ query เดิมของ Linux ได้ทันที
    ค่าแบบอัตรา (ต่อวินาที) ของ Windows ถูกสะสมเป็น counter (*_total) ให้ rate() ใช้ได้ตามปกติ
    ข้อมูลเก่ากว่า 120 วินาที -> ตอบ 503 -> up = 0 (เครื่อง DOWN / ติดต่อไม่ได้)
"""

import fnmatch
import json
import math
import os
import random
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cm_secrets
import winrm_client
from config import STATE_DIR, SYSTEM_DIR, load_config, parse_interval

PORT = int(os.environ.get("CENTRAL_WINRM_METRICS_PORT", "9110"))
STATE_FILE = os.path.join(STATE_DIR, "winrm-state.json")
METRICS_EVERY = 30
SNAPSHOT_EVERY = 300
INVENTORY_EVERY = 3600
STALE_AFTER = 120
BACKOFF = (30, 60, 120, 300)
BUSY_CPU = 90.0
MAX_EVENT_PAGES = 3
MAX_TAIL_BYTES = 1048576
DEFAULT_EVENT_LOGS = ("System", "Application")
MIB = 1048576
GIB = 1073741824

_workers = {}
_lock = threading.Lock()
_state_lock = threading.Lock()


def is_winrm(t):
    return isinstance(t, dict) and str(t.get("type") or "ssh") == "winrm"


def winrm_settings(t):
    w = t.get("winrm") if isinstance(t.get("winrm"), dict) else {}
    try:
        port = int(w.get("port") or 5986)
    except (TypeError, ValueError):
        port = 5986
    logs = [str(x) for x in (w.get("event_logs") or DEFAULT_EVENT_LOGS) if str(x).strip()]
    return {"port": port, "user": str(w.get("user") or "cm-monitor"),
            "thumbprint": winrm_client.norm_thumb(w.get("thumbprint")), "event_logs": logs[:10]}


# ---------------------------------------------------------------------------
# state ของ event bookmark / offset ไฟล์ (แยกจาก checkpoints.json ของ SSH)
# ---------------------------------------------------------------------------

def _load_state():
    try:
        with open(STATE_FILE) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_target_state(name, st):
    with _state_lock:
        doc = _load_state()
        doc[name] = st
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=1)
        os.replace(tmp, STATE_FILE)


def prune_state(valid_names):
    with _state_lock:
        doc = _load_state()
        drop = [k for k in doc if k not in set(valid_names)]
        if not drop:
            return []
        for k in drop:
            doc.pop(k, None)
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(doc, f, indent=1)
        os.replace(tmp, STATE_FILE)
        return drop


# ---------------------------------------------------------------------------
# แปลงค่า Windows -> metric แบบ node_exporter
# ---------------------------------------------------------------------------

def _esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class NodeMetrics:
    """เก็บ metric ของเครื่องเดียว (counter สะสมจากค่าอัตรา x เวลา)"""

    def __init__(self):
        self.counters = {}         # (metric, labels-tuple) -> float
        self.gauges = {}
        self.prev_t = None
        self.load = [None, None, None]
        self.ok_at = 0.0
        self.info = {}             # จาก inventory
        self.module = ""
        self.lock = threading.Lock()

    def _inc(self, metric, labels, amount):
        k = (metric, tuple(sorted(labels.items())))
        self.counters[k] = self.counters.get(k, 0.0) + max(0.0, float(amount))

    def _g(self, gauges, metric, labels, value):
        if value is None or (isinstance(value, float) and math.isnan(value)):
            return
        gauges[(metric, tuple(sorted(labels.items())))] = float(value)

    def update(self, m, now=None):
        now = now or time.time()
        vals = {}
        for row in m.get("values") or []:
            vals[(row.get("k"), row.get("i") or "")] = row.get("v")
        v = lambda k, i="": vals.get((k, i))          # noqa: E731
        dt = (now - self.prev_t) if self.prev_t else 0.0
        integrate = 0 < dt <= 300                     # ช่องว่างนาน (ติดต่อไม่ได้) ไม่สะสมย้อนหลัง
        g = {}
        with self.lock:
            # --- CPU ---
            user, priv = v("cpu_user_pct") or 0.0, v("cpu_priv_pct") or 0.0
            ratio_user = user / (user + priv) if (user + priv) > 0 else 0.5
            cores = sorted({i for (k, i) in vals if k == "core_pct" and i not in ("", "_Total")},
                           key=lambda x: (len(x), x))
            per_core = [(c, v("core_pct", c)) for c in cores] or [("0", v("cpu_pct"))]
            busy_sum = 0.0
            for idx, (c, pct) in enumerate(per_core):
                busy = min(max((pct or 0.0) / 100.0, 0.0), 1.0)
                busy_sum += busy
                cpu = str(idx)
                for mode, part in (("idle", 1 - busy), ("user", busy * ratio_user), ("system", busy * (1 - ratio_user)),
                                   ("iowait", 0.0)):
                    if integrate:
                        self._inc("node_cpu_seconds_total", {"cpu": cpu, "mode": mode}, part * dt)
                    else:
                        self._inc("node_cpu_seconds_total", {"cpu": cpu, "mode": mode}, 0)
            if integrate:
                self._inc("node_context_switches_total", {}, (v("ctx_switch_s") or 0) * dt)
                self._inc("node_vmstat_pswpin", {}, (v("mem_pagein_s") or 0) * dt)
                self._inc("node_vmstat_pswpout", {}, (v("mem_pageout_s") or 0) * dt)
                self._inc("node_vmstat_pgmajfault", {}, (v("mem_fault_s") or 0) * dt)
                self._inc("node_netstat_Tcp_RetransSegs", {}, (v("tcp_retrans_s") or 0) * dt)
                self._inc("node_netstat_Tcp_OutSegs", {}, (v("tcp_sent_s") or 0) * dt)
            # load = งานที่รอ CPU + core ที่กำลังทำงาน (เทียบ load average ของ Linux) เฉลี่ยแบบ 1 / 5 / 15 นาที
            runnable = (v("cpu_queue") or 0.0) + busy_sum
            for i, span in enumerate((60, 300, 900)):
                if self.load[i] is None or not integrate:
                    self.load[i] = runnable if self.load[i] is None else self.load[i]
                else:
                    a = math.exp(-dt / span)
                    self.load[i] = self.load[i] * a + runnable * (1 - a)
            self._g(g, "node_load1", {}, self.load[0])
            self._g(g, "node_load5", {}, self.load[1])
            self._g(g, "node_load15", {}, self.load[2])

            # --- memory ---
            total = m.get("mem_total_mb")
            if not total or total <= 0:           # module เก่า / อ่านไม่ได้ -> ใช้ค่าจาก inventory
                total = (self.info.get("system") or {}).get("ram_mb") if isinstance(self.info, dict) else None
            if total and total > 0:
                self._g(g, "node_memory_MemTotal_bytes", {}, total * MIB)
            if v("mem_avail_mb") is not None:
                self._g(g, "node_memory_MemAvailable_bytes", {}, v("mem_avail_mb") * MIB)
                self._g(g, "node_memory_MemFree_bytes", {}, v("mem_avail_mb") * MIB)
            self._g(g, "node_memory_Cached_bytes", {}, v("mem_cache_b"))
            self._g(g, "node_memory_Committed_AS_bytes", {}, v("mem_committed_b"))
            self._g(g, "node_memory_CommitLimit_bytes", {}, v("mem_limit_b"))

            # --- filesystem (ไดรฟ์) ---
            for d in m.get("drives") or []:
                lab = {"device": d.get("drive"), "mountpoint": d.get("drive"), "fstype": "ntfs"}
                self._g(g, "node_filesystem_size_bytes", lab, (d.get("size_gb") or 0) * GIB)
                self._g(g, "node_filesystem_avail_bytes", lab, (d.get("free_gb") or 0) * GIB)
                self._g(g, "node_filesystem_free_bytes", lab, (d.get("free_gb") or 0) * GIB)
                self._g(g, "node_filesystem_readonly", lab, 0)

            # --- disk (PhysicalDisk) ---
            disks = sorted({i for (k, i) in vals if k.startswith("disk_") and i})
            for dev in disks:
                lab = {"device": dev}
                self._g(g, "node_disk_info", lab, 1)
                if not integrate:
                    for mt in ("reads_completed_total", "writes_completed_total", "read_bytes_total", "written_bytes_total",
                               "read_time_seconds_total", "write_time_seconds_total", "io_time_seconds_total",
                               "io_time_weighted_seconds_total"):
                        self._inc("node_disk_" + mt, lab, 0)
                    continue
                r_s, w_s = v("disk_reads_s", dev) or 0, v("disk_writes_s", dev) or 0
                self._inc("node_disk_reads_completed_total", lab, r_s * dt)
                self._inc("node_disk_writes_completed_total", lab, w_s * dt)
                self._inc("node_disk_read_bytes_total", lab, (v("disk_read_bps", dev) or 0) * dt)
                self._inc("node_disk_written_bytes_total", lab, (v("disk_write_bps", dev) or 0) * dt)
                self._inc("node_disk_read_time_seconds_total", lab, r_s * dt * (v("disk_read_lat_s", dev) or 0))
                self._inc("node_disk_write_time_seconds_total", lab, w_s * dt * (v("disk_write_lat_s", dev) or 0))
                idle = min(max(v("disk_idle_pct", dev) if v("disk_idle_pct", dev) is not None else 100.0, 0.0), 100.0)
                self._inc("node_disk_io_time_seconds_total", lab, (1 - idle / 100.0) * dt)
                self._inc("node_disk_io_time_weighted_seconds_total", lab, (v("disk_avg_queue", dev) or 0) * dt)

            # --- network ---
            nics = sorted({i for (k, i) in vals if k.startswith("net_") and i})
            for dev in nics:
                lab = {"device": dev}
                self._g(g, "node_network_info", {"device": dev, "operstate": "up"}, 1)
                bw = v("net_bw_bps", dev)
                if bw:
                    self._g(g, "node_network_speed_bytes", lab, bw / 8.0)
                if integrate:
                    self._inc("node_network_receive_bytes_total", lab, (v("net_rx_bps", dev) or 0) * dt)
                    self._inc("node_network_transmit_bytes_total", lab, (v("net_tx_bps", dev) or 0) * dt)
                else:
                    self._inc("node_network_receive_bytes_total", lab, 0)
                    self._inc("node_network_transmit_bytes_total", lab, 0)
                # ค่าสะสมจริงของ Windows (นับตั้งแต่ boot) -> ใช้ตรง ๆ
                for src, dst in (("net_rx_err", "node_network_receive_errs_total"), ("net_tx_err", "node_network_transmit_errs_total"),
                                 ("net_rx_discard", "node_network_receive_drop_total"),
                                 ("net_tx_discard", "node_network_transmit_drop_total")):
                    if v(src, dev) is not None:
                        self.counters[(dst, tuple(sorted(lab.items())))] = float(v(src, dev))

            # --- TCP ---
            self._g(g, "node_netstat_Tcp_CurrEstab", {}, v("tcp_estab"))
            for src, dst in (("tcp_fail", "node_netstat_Tcp_AttemptFails"), ("tcp_reset", "node_netstat_Tcp_EstabResets")):
                if v(src) is not None:
                    self.counters[(dst, ())] = float(v(src))

            # --- เวลา / ข้อมูลเครื่อง ---
            if v("uptime_s"):
                self._g(g, "node_boot_time_seconds", {}, now - v("uptime_s"))
            self._g(g, "node_time_seconds", {}, now)
            inv = self.info
            osi = inv.get("os") or {}
            self._g(g, "node_uname_info", {"sysname": "Windows", "nodename": m.get("host") or "",
                                           "release": osi.get("build") or "", "version": osi.get("version") or "",
                                           "machine": "x86_64"}, 1)
            if osi.get("caption"):
                self._g(g, "node_os_info", {"id": "windows", "name": osi.get("caption"), "pretty_name": osi.get("caption"),
                                            "version_id": osi.get("version") or ""}, 1)
            for i, model in enumerate(inv.get("cpu") or []):
                self._g(g, "node_cpu_info", {"cpu": str(i), "model_name": model, "vendor_id": ""}, 1)
            self._g(g, "node_exporter_build_info", {"version": f"winrm-{m.get('v') or ''}", "goversion": "agentless"}, 1)
            selfb = m.get("self") or {}
            self._g(g, "cm_winrm_session_memory_mb", {}, selfb.get("ws_mb"))
            self._g(g, "cm_winrm_session_cpu_seconds", {}, selfb.get("cpu_s"))
            self._g(g, "cm_winrm_last_success_timestamp", {}, now)

            self.gauges = g
            self.prev_t = now
            self.ok_at = now
            self.module = m.get("v") or ""

    def exposition(self):
        with self.lock:
            lines = []
            seen = set()
            for (metric, labels), value in sorted(list(self.gauges.items()) + list(self.counters.items()),
                                                  key=lambda x: x[0][0]):
                if metric not in seen:
                    seen.add(metric)
                    kind = "counter" if metric.endswith("_total") or metric.startswith(("node_vmstat_", "node_netstat_Tcp_R",
                                                                                         "node_netstat_Tcp_O", "node_netstat_Tcp_A",
                                                                                         "node_netstat_Tcp_E")) else "gauge"
                    lines.append(f"# TYPE {metric} {kind}")
                lab = ",".join(f'{k}="{_esc(v)}"' for k, v in labels)
                lines.append(f"{metric}{{{lab}}} {value:.6f}" if lab else f"{metric} {value:.6f}")
            return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# log -> data/buffer (otel -> OpenSearch)
# ---------------------------------------------------------------------------

_LEVEL = {1: "CRITICAL", 2: "ERROR", 3: "WARN", 4: "INFO", 5: "INFO"}


def event_source(log_name):
    """ชื่อ source ใน buffer (เลือก parser ของ otel): System -> system, อื่น ๆ -> application"""
    return "system" if log_name.lower() == "system" else "application"


def event_line(ev, computer):
    msg = str(ev.get("msg") or "").replace("\r", "").replace("\n", " ⏎ ").strip()
    lvl = _LEVEL.get(int(ev.get("level") or 4), "INFO")
    if lvl == "CRITICAL":
        lvl, msg = "ERROR", "Critical: " + msg
    prov = str(ev.get("provider") or "-").replace(" ", "_")
    return f"{ev.get('time')} [{lvl}] {computer} {prov}[{ev.get('id')}]: {msg}"


def _win_path_ok(path):
    return bool(re.match(r"^[A-Za-z]:\\[^:*?\"<>|]*[^\\]$", path.replace("*", "x").replace("?", "x")))


# ---------------------------------------------------------------------------
# worker ต่อเครื่อง
# ---------------------------------------------------------------------------

class Worker(threading.Thread):

    def __init__(self, name):
        super().__init__(daemon=True, name=f"winrm-{name}")
        self.target_name = name
        self.target = {}
        self.config = {}
        self.metrics = NodeMetrics()
        self.stop_evt = threading.Event()
        self.wake = threading.Event()
        self.session = None
        self.session_key = None
        self.fails = 0
        self.last_error = ""
        self.last_ok = 0.0
        self.next = {}
        self.services = []
        self.events_15m = []
        self.inventory = {}
        self.history_at = 0.0
        self.busy = False
        self.last_metrics = {}
        self.task_errors = {}                 # งานที่ล้มแต่ session ยังดี (เช่น สิทธิ์ไม่พอ) -> เตือน ไม่หยุดงานอื่น
        self.call_lock = threading.Lock()     # session ใช้ได้ทีละงาน (รอบปกติ / ปุ่มบนหน้าเว็บ)

    # --- ตั้งค่า / session ---
    def configure(self, config, target):
        self.config, self.target = config, target

    def request_now(self):
        self.next = {}
        self.fails = 0
        self.wake.set()

    def _due(self, key, every):
        now = time.time()
        if now >= self.next.get(key, 0):
            self.next[key] = now + every
            return True
        return False

    def _session(self):
        w = winrm_settings(self.target)
        name = self.target_name
        password = cm_secrets.get_password(name)
        # thumbprint: ตั้งใน puller.yaml (ติดตั้งจากหน้าเว็บ / กรอกเอง) หรือที่จำไว้ตอนเชื่อมต่อสำเร็จครั้งแรก (zip)
        thumb = w["thumbprint"] or cm_secrets.get_pin(name)
        key = (self.target.get("host"), w["port"], w["user"], thumb, password)
        if self.session is not None and key != self.session_key and not (
                self.session_key and self.session_key[3] == "" and thumb == self.session.thumbprint
                and key[:3] + key[4:] == self.session_key[:3] + self.session_key[4:]):
            self.session.close()
            self.session = None
        if self.session is None:
            def learn(tp):
                cm_secrets.set_pin(name, tp)
                print(f"[WINRM] {name} certificate {tp} remembered (first successful login)")
            self.session = winrm_client.Session(self.target.get("host"), w["port"], w["user"], password, thumb,
                                                on_learn=None if thumb else learn)
        self.session_key = key
        return self.session

    def _soft(self, key, fn, *args):
        """ทำงานหนึ่งอย่าง: คำสั่งบนเครื่องล้ม (session ยังต่ออยู่) = จำเป็นคำเตือนแล้วทำงานอื่นต่อ
        ต่อไม่ได้ / session หลุด = ส่งต่อให้รอบหลักรอแล้วต่อใหม่"""
        try:
            out = fn(*args)
            self.task_errors.pop(key, None)
            return out
        except winrm_client.WinRMError as e:
            if self.session is None or not self.session.is_open:
                raise
            msg = str(e)[:300]
            if self.task_errors.get(key) != msg:
                print(f"[WINRM WARN] {self.target_name} {key}: {msg}")
            self.task_errors[key] = msg
            return None

    def call(self, cmd, params=None, wait=25):
        """เรียกจากหน้าเว็บ (ondemand) ผ่าน session เดิมของเครื่อง ไม่เปิด session ใหม่"""
        if not self.call_lock.acquire(timeout=wait):
            raise winrm_client.WinRMError("เครื่องนี้กำลังเก็บข้อมูลรอบปกติอยู่ ลองใหม่อีกครั้ง")
        try:
            return self._session().call(cmd, params)
        except Exception:
            self.close()
            raise
        finally:
            self.call_lock.release()

    def close(self):
        if self.session:
            self.session.close()
        self.session = None
        self.session_key = None

    # --- งานแต่ละชนิด ---
    def do_metrics(self, s):
        m = s.call("Get-CMMetrics")
        self.metrics.update(m)
        self.last_metrics = m
        cpu = next((r.get("v") for r in m.get("values") or [] if r.get("k") == "cpu_pct"), None)
        self.busy = cpu is not None and cpu >= BUSY_CPU
        self.last_ok = time.time()
        return m

    def do_inventory(self, s):
        inv = s.call("Get-CMInventory")
        self.inventory = inv
        self.metrics.info = inv

    def do_events(self, s, st):
        from log_filter import store_info_enabled
        from log_puller import _collapse, _write_buffer
        name = self.target_name
        computer = self.inventory.get("host") or name
        max_level = 4 if store_info_enabled(self.config) else 3
        marks = st.setdefault("events", {})
        recent = []
        for log_name in winrm_settings(self.target)["event_logs"]:
            for _ in range(MAX_EVENT_PAGES):
                after = int(marks.get(log_name, -1))
                r = self._soft(f"eventlog {log_name}", s.call, "Get-CMEvents",
                               {"LogName": log_name, "AfterRecordId": after, "MaxEvents": 200, "MaxLevel": max_level})
                if r is None:
                    break
                newest = int(r.get("newest") or 0)
                evs = r.get("events") or []
                if after < 0 or newest < after:            # ครั้งแรก / log ถูกล้าง -> เริ่มจากตอนนี้
                    marks[log_name] = newest
                    break
                if not evs:
                    break
                src = event_source(log_name)
                lines = [event_line(e, computer) for e in evs]
                data = ("\n".join(lines) + "\n").encode("utf-8")
                data, _ = _collapse(self.config, data, name, src)
                start, end = after, int(evs[-1]["rid"])
                if data:
                    _write_buffer(self.config, name, src, f"{name}::eventlog::{log_name}", start, end, data,
                                  label=f"EventLog-{log_name}"[:32])
                marks[log_name] = end
                recent += [{"type": _event_type(e), "timestamp": e.get("time"), "message": event_line(e, computer)}
                           for e in evs if int(e.get("level") or 4) <= 3]
                if not r.get("more"):
                    break
        cutoff = time.time() - 900
        keep = [e for e in self.events_15m + recent if _iso_ts(e.get("timestamp")) >= cutoff]
        self.events_15m = keep[-100:]

    def do_logs(self, s, st):
        from log_filter import drop_info, store_info_enabled
        from log_puller import _collapse, _write_buffer
        name = self.target_name
        tails = st.setdefault("tails", {})
        sources = [x for x in self.target.get("logs") or [] if isinstance(x, dict) and x.get("path")]
        if not sources:
            return
        listing = None
        for src in sources:
            pattern = str(src["path"])
            if any(ch in pattern for ch in "*?"):
                if listing is None:
                    listing = s.call("Get-CMLogTail", {"List": True, "Days": 2}).get("files") or []
                paths = [f["path"] for f in listing if fnmatch.fnmatch(f["path"].lower(), pattern.lower())]
            else:
                paths = [pattern]
            for path in paths[:50]:
                key = f"{src['name']}::{path}"
                prev = tails.get(key) or {}
                r = self._soft(f"log {path}", s.call, "Get-CMLogTail", {"Path": path, "Offset": int(prev.get("offset", -1)),
                                                                     "Fingerprint": prev.get("fp", ""), "MaxBytes": MAX_TAIL_BYTES,
                                                                     "Encoding": src.get("encoding") or "utf8"})
                if r is None:
                    continue
                text = r.get("text") or ""
                start, end = int(r.get("start") or 0), int(r.get("next_offset") or 0)
                data = text.encode("utf-8")
                if data:
                    if not store_info_enabled(self.config):
                        data, _, _ = drop_info(data)
                    data, _ = _collapse(self.config, data, name, src["name"])
                    if data:
                        _write_buffer(self.config, name, src["name"], f"{name}::{src['name']}::{path}", start, end, data,
                                      label=src.get("type"))
                tails[key] = {"offset": end, "fp": r.get("fingerprint") or "", "size": r.get("size"),
                              "updated_at": datetime.now(timezone.utc).isoformat(), "rotated": bool(r.get("rotated"))}

    def do_snapshot(self, s):
        svc = self._soft("services", s.call, "Get-CMServices", {"All": True})
        if svc is not None:
            self.services = [{"name": x.get("name"), "status": _svc_status(x), "display": x.get("display"),
                              "start": x.get("start")} for x in svc.get("services") or []]
        procs = {}
        if not self.busy:
            procs = self._soft("processes", s.call, "Get-CMProcesses", {"Top": 10}) or {}
        self.write_snapshot(procs)

    def write_snapshot(self, procs):
        from system_info import HISTORY_TS_FORMAT, _write_json_atomic, history_dir, prune_history
        g = self.metrics.gauges
        mem_t = g.get(("node_memory_MemTotal_bytes", ()))
        mem_a = g.get(("node_memory_MemAvailable_bytes", ()))
        res = {}
        if mem_t and mem_a is not None:
            res["memory"] = round((mem_t - mem_a) / mem_t * 100, 2)
            res["memory_used"] = round((mem_t - mem_a) / GIB, 2)
            res["memory_available"] = round(mem_a / GIB, 2)
        drives = {dict(k[1]).get("mountpoint"): v for k, v in g.items() if k[0] == "node_filesystem_size_bytes"}
        frees = {dict(k[1]).get("mountpoint"): v for k, v in g.items() if k[0] == "node_filesystem_avail_bytes"}
        sysd = "C:" if "C:" in drives else (sorted(drives)[0] if drives else None)
        if sysd and drives.get(sysd):
            res["disk"] = round((1 - frees.get(sysd, 0) / drives[sysd]) * 100, 1)
            res["disk_available"] = round(frees.get(sysd, 0) / GIB, 2)
        boot = g.get(("node_boot_time_seconds", ()))
        if boot:
            sec = time.time() - boot
            res["uptime"] = f"{int(sec // 86400)}d {int(sec % 86400 // 3600)}h {int(sec % 3600 // 60)}m"
        if self.metrics.load[0] is not None:
            res["load"] = res["load_1m"] = round(self.metrics.load[0], 2)
            res["load_5m"] = round(self.metrics.load[1], 2)
            res["load_15m"] = round(self.metrics.load[2], 2)
        ram_mb = (mem_t or 0) / MIB
        processes = []
        for p in (procs.get("top_cpu") or []):
            processes.append({"pid": p.get("pid"), "core": 0, "name": p.get("name"), "cpu": p.get("cpu"),
                              "memory": round(p.get("ws_mb", 0) / ram_mb * 100, 2) if ram_mb else 0,
                              "rss_mb": p.get("ws_mb"), "self": p.get("pid") == procs.get("self_pid")})
        cores = [(dict(k[1]).get("cpu")) for k in self.metrics.counters if k[0] == "node_cpu_seconds_total"
                 and dict(k[1]).get("mode") == "idle"]
        now = datetime.now(timezone.utc)
        result = {
            "web": self.target_name,
            "timestamp": now.isoformat(),
            "os": "windows",
            "collector": "winrm",
            "resource": res,
            "services": self.services,
            "processes": processes,
            "memory_top": [{"pid": p.get("pid"), "name": p.get("name"), "rss_mb": p.get("ws_mb"),
                            "memory": round(p.get("ws_mb", 0) / ram_mb * 100, 2) if ram_mb else 0}
                           for p in (procs.get("top_ram") or [])],
            "cpu_cores": {"count": len(set(cores)), "interval": procs.get("interval"), "per_core": [], "processes": []},
            "system_events": list(self.events_15m),
            "inventory": self.inventory,
            "netsec": {},
        }
        _write_json_atomic(os.path.join(SYSTEM_DIR, f"{self.target_name}.json"), result)
        try:
            every = parse_interval((self.config.get("scheduler") or {}).get("interval", "1h"))
        except Exception:  # noqa: BLE001
            every = 3600
        if time.time() - self.history_at >= max(every, 60):
            self.history_at = time.time()
            _write_json_atomic(os.path.join(history_dir(self.target_name), now.strftime(HISTORY_TS_FORMAT) + ".json"), result)
            retention = (self.config.get("storage") or {}).get("system_history_retention", "7d")
            prune_history(self.target_name, parse_interval(retention), now)

    # --- loop ---
    def run(self):
        name = self.target_name
        time.sleep(random.uniform(0, 5))               # ไม่ให้ทุกเครื่องเริ่มพร้อมกัน
        while not self.stop_evt.is_set():
            if self.target.get("paused"):
                self.close()
                self.wake.wait(10)
                self.wake.clear()
                continue
            try:
                log_every = max(parse_interval((self.config.get("scheduler") or {}).get("log_interval", "2m")), 30)
            except Exception:  # noqa: BLE001
                log_every = 120
            self.call_lock.acquire()
            try:
                s = self._session()
                st = _load_state().get(name) or {}
                changed = False
                if self._due("inventory", INVENTORY_EVERY):
                    self._soft("inventory", self.do_inventory, s)
                if self._due("metrics", METRICS_EVERY):
                    self._soft("metrics", self.do_metrics, s)
                if not self.busy and self._due("events", log_every):
                    self._soft("events", self.do_events, s, st)
                    self._soft("logs", self.do_logs, s, st)
                    changed = True
                if self._due("snapshot", SNAPSHOT_EVERY):
                    self._soft("snapshot", self.do_snapshot, s)
                if changed:
                    st["updated_at"] = datetime.now(timezone.utc).isoformat()
                    _save_target_state(name, st)
                if self.fails:
                    print(f"[WINRM] {name} reconnected")
                self.fails = 0
                self.last_error = ""
            except Exception as e:  # noqa: BLE001
                wait = BACKOFF[min(self.fails, len(BACKOFF) - 1)]
                self.fails += 1
                self.last_error = str(e)[:400]
                print(f"[WINRM ERROR] {name} {self.last_error} -> retry in {wait}s")
                self.close()
                retry_at = time.time() + wait
                self.next = {k: retry_at for k in ("metrics", "events", "snapshot", "inventory")}
            finally:
                self.call_lock.release()
            nxt = min([self.next.get(k, 0) for k in ("metrics", "events", "snapshot", "inventory")] or [time.time() + 5])
            delay = min(max(nxt - time.time(), 1), 30)
            if self.wake.wait(delay):
                self.wake.clear()
        self.close()


def _svc_status(x):
    st, start = str(x.get("status") or ""), str(x.get("start") or "")
    if st == "Running":
        return "active"
    if st in ("StartPending", "ContinuePending"):
        return "activating"
    if st in ("StopPending", "PausePending"):
        return "deactivating"
    if st == "Paused":
        return "inactive"
    if st == "Stopped":
        return "failed" if start == "Automatic" else "inactive"
    if st == "NotFound":
        return "not-found"
    return st.lower() or "unknown"


def _event_type(ev):
    from system_info import classify_event
    return classify_event(f"{ev.get('provider')} {ev.get('msg')}")


def _iso_ts(text):
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# manager: sync worker กับ puller.yaml ทุก 15 วินาที
# ---------------------------------------------------------------------------

def sync(config=None):
    config = config or load_config()
    targets = {t["name"]: t for t in config.get("targets") or [] if is_winrm(t) and t.get("name")}
    with _lock:
        for name in list(_workers):
            if name not in targets:
                w = _workers.pop(name)
                w.stop_evt.set()
                w.wake.set()
        for name, t in targets.items():
            w = _workers.get(name)
            if w is None:
                w = Worker(name)
                w.configure(config, t)
                _workers[name] = w
                w.start()
            else:
                was_paused = bool(w.target.get("paused"))
                w.configure(config, t)
                if was_paused != bool(t.get("paused")):
                    w.wake.set()
    return list(targets)


def request_now(names):
    """ปุ่มบันทึก / ดึงเดี๋ยวนี้จากหน้า Settings"""
    with _lock:
        for n in names or []:
            if n in _workers:
                _workers[n].request_now()


def status(name):
    w = _workers.get(name)
    if not w:
        return None
    return {"connected": bool(w.session and w.session.is_open), "last_ok": w.last_ok or None,
            "last_error": w.last_error, "fails": w.fails, "module": w.metrics.module,
            "warnings": dict(w.task_errors)}


def close_all():
    """central กำลังปิด (docker stop / cm-update): ปิด session ทุกเครื่อง ไม่ทิ้ง shell ค้างบน Windows"""
    with _lock:
        workers = list(_workers.values())
    for w in workers:
        w.stop_evt.set()
        try:
            if w.call_lock.acquire(timeout=5):
                try:
                    w.close()
                finally:
                    w.call_lock.release()
            else:
                w.close()
        except Exception:  # noqa: BLE001
            pass
    if workers:
        print(f"[WINRM] closed {len(workers)} session(s)")


def get_worker(name):
    return _workers.get(name)


def _manager_loop():
    while True:
        try:
            sync()
        except Exception as e:  # noqa: BLE001
            print(f"[WINRM] sync failed: {e}")
        time.sleep(15)


class _MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        m = re.match(r"^/win/([a-z0-9][a-z0-9-]{0,39})$", self.path.split("?")[0])
        w = _workers.get(m.group(1)) if m else None
        if not w:
            return self._send(404, "unknown machine\n")
        if w.target.get("paused") or time.time() - w.metrics.ok_at > STALE_AFTER:
            return self._send(503, f"no fresh data: {w.last_error or 'not connected yet'}\n")
        return self._send(200, w.metrics.exposition())

    def _send(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start():
    """เรียกจาก puller.main: เปิด /win/<ชื่อ> (พอร์ต 9110) + thread จัดการ worker"""
    if not winrm_client.HAVE_PYPSRP:
        print("[WINRM] pypsrp not installed - Windows (WinRM) machines are not monitored (rebuild the puller image)")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), _MetricsHandler)
    threading.Thread(target=server.serve_forever, daemon=True, name="winrm-metrics").start()
    threading.Thread(target=_manager_loop, daemon=True, name="winrm-manager").start()
    print(f"[WINRM] metrics http://0.0.0.0:{PORT}/win/<name>")
