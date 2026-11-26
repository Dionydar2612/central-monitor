"""
agent_bridge.py - เครื่อง Windows แบบ cm-agent (type: agent): แปลงข้อมูลที่ agent ส่งมาให้ใช้ร่วมกับระบบเดิม
=========================================================================================================

cm-agent บนเครื่อง Windows (Fluent Bit + windows_exporter) ส่ง metrics ทาง OTLP/HTTPS -> central-agent-gw
central-agent-gw เปิดค่าล่าสุดของทุกเครื่องที่ http://agent-gw:8889/metrics (label cm_web="<เครื่อง>")

ไฟล์นี้ (thread ใน central-puller) อ่านทุก POLL วินาที แล้วต่อเครื่อง:
    1. แปลงชื่อ windows_exporter -> ค่าแบบเดียวกับที่ module WinRM ส่ง (Get-CMMetrics) แล้วใช้ windows_pull.NodeMetrics
       -> ได้ metric ชื่อแบบ node_exporter ชุดเดียวกับเครื่อง WinRM / Linux (หน้าเว็บ / sensor / กฎ ใช้ PromQL เดิมได้)
       -> Prometheus scrape ที่ central-puller:9110/win/<เครื่อง> (windows_pull เปิดพอร์ตให้)
    2. สัญญาณ "agent ยังทำงาน" = fluentbit_uptime เปลี่ยนค่า · ไม่เปลี่ยนเกินเวลา stale = Down
    3. snapshot ทุก SNAPSHOT_EVERY วินาที (data/state/system/<เครื่อง>.json): service, process (ถ้าเปิด), inventory
       inventory = ข้อมูลเครื่อง / โปรแกรมที่ติดตั้ง / พอร์ตที่เปิด จากไฟล์ textfile ของ cm-inventory.ps1

log (Event Log / ไฟล์ log) ไม่ผ่านที่นี่: central-agent-gw ส่งตรงเข้า OpenSearch

โหมด pull (agent.mode: pull): เครื่อง Windows ส่งออกมาไม่ได้ แต่ central เข้าไปได้
    central อ่าน http://<host>:<exporter_port>/metrics ของเครื่องเองทุก metrics_interval (windows_exporter ฟัง 0.0.0.0
    + firewall ให้เฉพาะ IP ของ central) แล้วแปลงแบบเดียวกัน · log = cm_log_entry ในหน้าเดียวกัน -> agent_logs.py
"""

import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from config import load_config

GW_URL = os.environ.get("CENTRAL_AGENT_GW_METRICS", "http://agent-gw:8889/metrics")
POLL = 15
PULL_MAX = 60
SNAPSHOT_EVERY = 300
MIB = 1048576

_states = {}
_lock = threading.Lock()
_last_error = ""


def is_agent(t):
    return isinstance(t, dict) and str(t.get("type") or "ssh") == "agent"


def is_pull(t):
    a = t.get("agent") if isinstance(t.get("agent"), dict) else {}
    return str(a.get("mode") or "push") == "pull"


def pull_url(t):
    a = t.get("agent") if isinstance(t.get("agent"), dict) else {}
    host = str(t.get("host") or "").strip()
    try:
        port = int(a.get("exporter_port") or 9182)
    except (TypeError, ValueError):
        port = 9182
    if not host:
        return None
    return f"http://[{host}]:{port}/metrics" if ":" in host else f"http://{host}:{port}/metrics"


def fetch_pull(url, timeout=10, max_bytes=16 * 1048576):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))      # วงภายใน ไม่ผ่าน proxy
    with opener.open(urllib.request.Request(url, headers={"Accept": "text/plain"}), timeout=timeout) as r:
        return r.read(max_bytes).decode("utf-8", "replace")


def parse_plain(text):
    """หน้า /metrics ของ windows_exporter ตรง ๆ (ไม่มี cm_web) -> [(ชื่อ, labels, ค่า)] · ข้าม cm_log_entry (log)"""
    from prometheus_client.parser import text_string_to_metric_families
    lines = [ln for ln in text.splitlines() if not ln.startswith("cm_log_entry")]
    out = []
    for fam in text_string_to_metric_families("\n".join(lines) + "\n"):
        for s in fam.samples:
            out.append((s.name, s.labels, s.value))
    return out


def agent_settings(t):
    a = t.get("agent") if isinstance(t.get("agent"), dict) else {}
    try:
        hb = max(15, min(int(a.get("heartbeat_interval") or 60), 600))
    except (TypeError, ValueError):
        hb = 60
    try:
        mi = max(15, min(int(a.get("metrics_interval") or 60), 600))
    except (TypeError, ValueError):
        mi = 60
    # โหมด pull: central อ่านหน้า /metrics เองไม่เกินทุก PULL_MAX วิ (อ่าน 1 หน้าเบามาก) เพราะ sensor / กราฟใช้ rate(...[5m])
    # อ่านห่างกว่านี้ (เช่น 600 วิ) = ในช่วง 5 นาทีไม่มีค่าใหม่ -> CPU idle ไม่เพิ่ม -> CPU 100% ปลอม
    return {"heartbeat_interval": hb, "metrics_interval": mi, "pull_interval": min(mi, PULL_MAX)}


# ---------------------------------------------------------------------------
# อ่าน / แยกค่า
# ---------------------------------------------------------------------------

def parse(text):
    """exposition -> {cm_web: [(ชื่อ, labels, ค่า), ...]}"""
    from prometheus_client.parser import text_string_to_metric_families
    out = {}
    for fam in text_string_to_metric_families(text):
        for s in fam.samples:
            web = s.labels.get("cm_web")
            if not web:
                continue
            out.setdefault(web, []).append((s.name, s.labels, s.value))
    return out


class _Idx:
    """ค้นค่าตามชื่อ metric"""

    def __init__(self, samples):
        self.by = {}
        for name, labels, value in samples:
            self.by.setdefault(name, []).append((labels, value))

    def rows(self, *names):
        for n in names:
            if self.by.get(n):
                return self.by[n]
        return []

    def one(self, *names):
        r = self.rows(*names)
        return sum(v for _, v in r) if r else None

    def label_one(self, name):
        r = self.rows(name)
        return r[0][0] if r else {}


CPU_BUSY = ("user", "privileged", "interrupt", "dpc")


class AgentState:
    """ข้อมูลของเครื่อง cm-agent 1 เครื่อง (หน้าตาเหมือน windows_pull.Worker เท่าที่ handler / snapshot ใช้)"""

    def __init__(self, name):
        import windows_pull
        self.target_name = name
        self.target = {}
        self.config = {}
        self.metrics = windows_pull.NodeMetrics()
        self.prev = {}                 # ค่าสะสมรอบก่อน
        self.prev_t = None
        self.hb = None                 # fluentbit_uptime ล่าสุด
        self.last_seen = 0.0
        self.last_error = ""
        self.services = []
        self.events_15m = []
        self.inventory = {}
        self.procs = {}
        self.history_at = 0.0
        self.snap_at = 0.0
        self.stale_after = 120
        self.pulled_at = 0.0

    # --- รอบอ่านค่า ---
    def feed(self, samples, now):
        ix = _Idx(samples)
        hb = ix.one("fluentbit_uptime", "fluentbit_uptime_total", "fluentbit_uptime_seconds")
        if hb is not None and hb != self.hb:
            self.hb = hb
            self.last_seen = now
        cur = self._counters(ix)
        changed = cur != self.prev.get("_counters")
        if changed and cur:
            self.last_seen = now
            m = self._to_module(ix, cur, now)
            self.metrics.info = self.inventory
            self.metrics.update(m, now)
            self.prev["_counters"] = cur
            self.prev_t = now
        if now - self.snap_at >= SNAPSHOT_EVERY or not self.snap_at:
            self._snapshot_parts(ix, cur, now)
        self.metrics.ok_at = self.last_seen

    @staticmethod
    def _counters(ix):
        """ค่าสะสมทั้งหมดที่ใช้คำนวณอัตรา {key: value}"""
        c = {}
        for labels, v in ix.rows("windows_cpu_time_total"):
            c[("cpu", labels.get("core", "0"), labels.get("mode", ""))] = v
        for name, key in (("windows_system_context_switches_total", "ctx"), ("windows_memory_swap_page_reads_total", "pin"),
                          ("windows_memory_swap_page_writes_total", "pout"), ("windows_memory_page_faults_total", "fault"),
                          ("windows_tcp_segments_retransmitted_total", "retx"), ("windows_tcp_segments_sent_total", "sent")):
            v = ix.one(name)
            if v is not None:
                c[(key,)] = v
        pref = "windows_physical_disk_" if ix.rows("windows_physical_disk_reads_total") else "windows_logical_disk_"
        lab = "disk" if pref == "windows_physical_disk_" else "volume"
        for suf in ("reads_total", "writes_total", "read_bytes_total", "write_bytes_total", "read_seconds_total",
                    "write_seconds_total", "idle_seconds_total"):
            for labels, v in ix.rows(pref + suf):
                dev = labels.get(lab, "")
                if lab == "volume" and not _drive(dev):
                    continue
                c[("disk", dev, suf)] = v
        for suf in ("bytes_received_total", "bytes_sent_total"):
            for labels, v in ix.rows("windows_net_" + suf):
                if _nic_ok(labels.get("nic", "")):
                    c[("net", labels.get("nic", ""), suf)] = v
        return c

    def _to_module(self, ix, cur, now):
        """ค่ารอบนี้ -> รูปแบบเดียวกับผล Get-CMMetrics ของ module WinRM"""
        prev = self.prev.get("_counters") or {}
        dt = (now - self.prev_t) if self.prev_t else 0.0
        vals = []

        def put(k, v, i=""):
            if v is not None:
                vals.append({"k": k, "i": i, "v": v})

        def d(key):
            if key not in prev or key not in cur:
                return None
            return max(0.0, cur[key] - prev[key])

        def rate(key):
            x = d(key)
            return x / dt if x is not None and dt > 0 else None

        # CPU: สัดส่วนเวลาที่ไม่ idle ต่อ core
        cores = sorted({k[1] for k in cur if k[0] == "cpu"}, key=_core_key)
        tot_busy = tot_all = tot_user = tot_priv = 0.0
        times = []
        for idx, core in enumerate(cores):
            # CPU time สะสมจริงจาก windows_exporter -> node_cpu_seconds_total ตรง ๆ (ไม่ขึ้นกับช่วงห่างของการอ่าน)
            raw = {mode: cur.get(("cpu", core, mode)) for mode in ("idle",) + CPU_BUSY}
            if raw["idle"] is not None:
                g = lambda m: raw.get(m) or 0.0          # noqa: E731  รุ่นที่ไม่มีบาง mode = 0
                times.append({"i": idx, "idle": g("idle"), "user": g("user"), "irq": g("interrupt"), "softirq": g("dpc"),
                              "system": max(0.0, g("privileged") - g("interrupt") - g("dpc"))})
            dm = {mode: d(("cpu", core, mode)) for mode in ("idle",) + CPU_BUSY}
            if dm["idle"] is None:
                continue
            dm = {k: (v or 0.0) for k, v in dm.items()}
            allt = sum(dm.values())
            if allt <= 0:
                continue
            busy = sum(dm[m] for m in CPU_BUSY)
            put("core_pct", busy / allt * 100, str(idx))
            tot_busy += busy
            tot_all += allt
            tot_user += dm["user"]
            tot_priv += dm["privileged"] + dm["interrupt"] + dm["dpc"]
        if tot_all > 0:
            put("cpu_pct", tot_busy / tot_all * 100)
            put("cpu_user_pct", tot_user / tot_all * 100)
            put("cpu_priv_pct", tot_priv / tot_all * 100)
        put("cpu_queue", ix.one("windows_system_processor_queue_length"))
        put("ctx_switch_s", rate(("ctx",)))
        put("mem_pagein_s", rate(("pin",)))
        put("mem_pageout_s", rate(("pout",)))
        put("mem_fault_s", rate(("fault",)))
        put("tcp_retrans_s", rate(("retx",)))
        put("tcp_sent_s", rate(("sent",)))
        boot = ix.one("windows_system_boot_time_timestamp_seconds", "windows_system_system_up_time")
        if boot:
            put("uptime_s", max(0.0, now - boot))

        # memory
        total = ix.one("windows_memory_physical_total_bytes", "windows_cs_physical_memory_bytes", "windows_os_visible_memory_bytes")
        avail = ix.one("windows_memory_available_bytes", "windows_os_physical_memory_free_bytes")
        put("mem_avail_mb", avail / MIB if avail is not None else None)
        put("mem_cache_b", ix.one("windows_memory_cache_bytes"))
        put("mem_committed_b", ix.one("windows_memory_committed_bytes"))
        put("mem_limit_b", ix.one("windows_memory_commit_limit"))

        # TCP
        put("tcp_estab", ix.one("windows_tcp_connections_established"))
        put("tcp_fail", ix.one("windows_tcp_connection_failures_total"))
        put("tcp_reset", ix.one("windows_tcp_connections_reset_total"))

        # disk I/O ต่อดิสก์
        for dev in sorted({k[1] for k in cur if k[0] == "disk"}):
            reads, writes = d(("disk", dev, "reads_total")), d(("disk", dev, "writes_total"))
            if dt <= 0 or reads is None or writes is None:
                continue
            put("disk_reads_s", reads / dt, dev)
            put("disk_writes_s", writes / dt, dev)
            put("disk_read_bps", (d(("disk", dev, "read_bytes_total")) or 0) / dt, dev)
            put("disk_write_bps", (d(("disk", dev, "write_bytes_total")) or 0) / dt, dev)
            rs, ws = d(("disk", dev, "read_seconds_total")), d(("disk", dev, "write_seconds_total"))
            put("disk_read_lat_s", rs / reads if reads and rs is not None else 0.0, dev)
            put("disk_write_lat_s", ws / writes if writes and ws is not None else 0.0, dev)
            idle = d(("disk", dev, "idle_seconds_total"))
            put("disk_idle_pct", min(100.0, idle / dt * 100) if idle is not None else None, dev)
            q = [v for lab, v in ix.rows("windows_physical_disk_requests_queued", "windows_logical_disk_requests_queued")
                 if lab.get("disk", lab.get("volume")) == dev]
            put("disk_avg_queue", q[0] if q else None, dev)

        # network ต่อการ์ด
        speed = {lab.get("nic"): v for lab, v in ix.rows("windows_net_current_bandwidth_bytes")}
        for nic in sorted({k[1] for k in cur if k[0] == "net"}):
            put("net_rx_bps", rate(("net", nic, "bytes_received_total")), nic)
            put("net_tx_bps", rate(("net", nic, "bytes_sent_total")), nic)
            if speed.get(nic):
                put("net_bw_bps", speed[nic] * 8, nic)
            for src, name in (("net_rx_err", "windows_net_packets_received_errors_total"),
                              ("net_tx_err", "windows_net_packets_outbound_errors_total"),
                              ("net_rx_discard", "windows_net_packets_received_discarded_total"),
                              ("net_tx_discard", "windows_net_packets_outbound_discarded_total")):
                r = [v for lab, v in ix.rows(name) if lab.get("nic") == nic]
                if r:
                    put(src, r[0], nic)

        drives = []
        frees = {lab.get("volume"): v for lab, v in ix.rows("windows_logical_disk_free_bytes")}
        for lab, size in ix.rows("windows_logical_disk_size_bytes"):
            vol = lab.get("volume", "")
            if _drive(vol) and size > 0:
                drives.append({"drive": vol, "size_gb": size / 1073741824, "free_gb": (frees.get(vol) or 0) / 1073741824})
        host = (ix.label_one("windows_cs_hostname").get("hostname") or self.target_name)
        build = ix.label_one("windows_exporter_build_info").get("version") or ""
        return {"values": vals, "cpu_times": times, "mem_total_mb": total / MIB if total else None, "drives": drives, "host": host,
                "v": f"agent {build}".strip(), "self": {}}

    # --- service / process / inventory ---
    def _snapshot_parts(self, ix, cur, now):
        import windows_pull
        self.snap_at = now
        start = {lab.get("name"): lab.get("start_mode") for lab, v in ix.rows("windows_service_start_mode") if v == 1}
        disp = {lab.get("name"): lab.get("display_name") for lab, _ in ix.rows("windows_service_info")}
        svcs = []
        for lab, v in ix.rows("windows_service_state"):
            if v != 1:
                continue
            name = lab.get("name")
            st = SVC_STATE.get(str(lab.get("state") or "").lower(), "Unknown")
            sm = SVC_START.get(str(start.get(name) or "").lower(), "")
            svcs.append({"name": name, "status": windows_pull._svc_status({"status": st, "start": sm}),
                         "display": disp.get(name) or name, "start": sm})
        if svcs:
            self.services = sorted(svcs, key=lambda x: str(x["name"]).lower())
        self.procs = self._processes(ix, now)
        self.inventory = inventory(ix, self.inventory)
        if self.last_seen and now - self.last_seen < self.stale_after and not self.target.get("paused"):
            try:
                windows_pull.Worker.write_snapshot(self, self.procs)
            except Exception as e:  # noqa: BLE001
                print(f"[AGENT] {self.target_name} snapshot: {e!r}")

    def _processes(self, ix, now):
        cpu = {}
        for lab, v in ix.rows("windows_process_cpu_time_total"):
            key = (lab.get("process", ""), lab.get("process_id", lab.get("pid", "")))
            cpu[key] = cpu.get(key, 0.0) + v
        if not cpu:
            return {}
        ws = {(lab.get("process", ""), lab.get("process_id", lab.get("pid", ""))): v
              for lab, v in ix.rows("windows_process_working_set_private_bytes", "windows_process_working_set_bytes")}
        prev, pt = self.prev.get("_proc"), self.prev.get("_proc_t")
        self.prev["_proc"], self.prev["_proc_t"] = cpu, now
        ncores = max(1, len({k[1] for k in self.prev.get("_counters") or {} if k[0] == "cpu"}))
        rows = []
        for key, total in cpu.items():
            if key[0] in ("Idle", "_Total"):
                continue
            pct = None
            if prev and pt and now > pt and key in prev:
                pct = max(0.0, total - prev[key]) / (now - pt) / ncores * 100
            rows.append({"pid": _int(key[1]), "name": key[0], "cpu": round(pct, 1) if pct is not None else 0.0,
                         "ws_mb": round((ws.get(key) or 0) / MIB, 1)})
        return {"top_cpu": sorted(rows, key=lambda r: -r["cpu"])[:10], "top_ram": sorted(rows, key=lambda r: -r["ws_mb"])[:10],
                "interval": round(now - pt) if pt else None}


SVC_STATE = {"running": "Running", "stopped": "Stopped", "start pending": "StartPending", "stop pending": "StopPending",
             "continue pending": "ContinuePending", "pause pending": "PausePending", "paused": "Paused", "unknown": "Unknown"}
SVC_START = {"auto": "Automatic", "automatic": "Automatic", "manual": "Manual", "disabled": "Disabled",
             "boot": "Boot", "system": "System"}


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _core_key(core):
    parts = str(core).split(",")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return (999,)


def _drive(vol):
    return len(str(vol)) == 2 and str(vol)[1] == ":" and str(vol)[0].isalpha()


def _nic_ok(nic):
    low = str(nic).lower()
    return bool(nic) and not any(x in low for x in ("isatap", "loopback", "teredo", "6to4", "pseudo"))


def inventory(ix, old=None):
    """ข้อมูลเครื่อง (รูปแบบเดียวกับ inventory ของ module WinRM) + software / ports จาก cm-inventory.ps1"""
    inv = dict(old or {})
    osi = ix.label_one("windows_os_info")
    host = ix.label_one("cm_host_info")
    cs = ix.label_one("windows_cs_hostname")
    if osi or host:
        inv["os"] = {"caption": host.get("caption") or osi.get("product") or "", "edition": host.get("edition") or "",
                     "release": host.get("release") or "", "version": osi.get("version") or host.get("version") or "",
                     "build": host.get("build") or osi.get("build_number") or "", "install": host.get("install") or None}
    total = ix.one("windows_memory_physical_total_bytes", "windows_cs_physical_memory_bytes")
    logical = ix.one("windows_cs_logical_processors")
    inv["system"] = {"manufacturer": host.get("manufacturer") or "", "model": host.get("model") or "",
                     "domain": host.get("domain") or cs.get("domain") or "", "ram_mb": round(total / MIB) if total else None,
                     "logical_cpu": int(logical) if logical else None}
    cpus = sorted({lab.get("name") for lab, _ in ix.rows("windows_cpu_info") if lab.get("name")})
    if cpus:
        inv["cpu"] = cpus
    ips = {}
    for lab, _ in ix.rows("cm_nic_ip"):
        ips.setdefault(lab.get("nic"), []).append(lab.get("ip"))
    speed = {lab.get("nic"): v for lab, v in ix.rows("windows_net_current_bandwidth_bytes")}
    nics = [{"name": n, "status": "Up", "speed": int(speed.get(n) or 0) * 8, "ips": ips.get(n) or []}
            for n in sorted(set(ips) | {n for n in speed if _nic_ok(n)}) if n]
    if nics:
        inv["nics"] = nics
    hf = [{"id": lab.get("id"), "date": lab.get("date")} for lab, _ in ix.rows("cm_hotfix") if lab.get("id")]
    if hf:
        inv["hotfix"] = sorted(hf, key=lambda h: str(h.get("date")), reverse=True)[:5]
    if host.get("tz"):
        inv["tz"] = host["tz"]
    sw = [{"name": lab.get("name"), "version": lab.get("version"), "publisher": lab.get("publisher"),
           "installed": lab.get("installed"), "arch": lab.get("arch")} for lab, _ in ix.rows("cm_software_installed")]
    if sw:
        inv["software"] = sorted(sw, key=lambda x: str(x["name"]).lower())
    ports = [{"port": _int(lab.get("port")), "address": lab.get("address"), "process": lab.get("process")}
             for lab, _ in ix.rows("cm_port_listening")]
    if ports:
        inv["ports"] = sorted(ports, key=lambda x: (x["port"] or 0, str(x["address"])))
    ts = ix.one("cm_inventory_timestamp_seconds")
    if ts:
        inv["inventory_ts"] = ts
    inv["collector"] = "cm-agent"
    return inv


# ---------------------------------------------------------------------------
# loop / ให้ windows_pull เรียก
# ---------------------------------------------------------------------------

def get(name):
    return _states.get(name)


def status(name):
    st = _states.get(name)
    if not st:
        return None
    pull = is_pull(st.target)
    return {"last_seen": st.last_seen or None, "stale_after": st.stale_after, "mode": "pull" if pull else "push",
            "url": pull_url(st.target) if pull else None,
            "last_error": st.last_error or ("" if pull else _last_error)}


def _pull_one(st, t, config, now, fetch):
    url = pull_url(t)
    if not url:
        st.last_error = "no host"
        return
    try:
        text = fetch(url)
    except Exception as e:  # noqa: BLE001
        st.last_error = f"{url}: {getattr(e, 'reason', None) or e}"
        return
    st.last_error = ""
    st.feed(parse_plain(text), now)
    st.last_seen = now
    st.metrics.ok_at = now
    try:
        import agent_logs
        agent_logs.store(config, t["name"], text)
    except Exception as e:  # noqa: BLE001
        print(f"[AGENT PULL] {t['name']} logs: {e!r}")


def run_once(text=None, config=None, now=None, fetch=None):
    """อ่าน gateway 1 รอบ + เครื่องโหมด pull ที่ถึงรอบ (text = ข้อความของ gateway สำหรับ tests)"""
    global _last_error
    now = now or time.time()
    config = config or load_config()
    targets = {t["name"]: t for t in config.get("targets") or [] if is_agent(t) and t.get("name")}
    pulls = {n: t for n, t in targets.items() if is_pull(t)}
    if text is None and len(pulls) == len(targets):
        text = ""                                   # ไม่มีเครื่องโหมด push -> ไม่ต้องอ่าน gateway
    if text is None:
        try:
            with urllib.request.urlopen(GW_URL, timeout=10) as r:
                text = r.read().decode("utf-8", "replace")
            _last_error = ""
        except Exception as e:  # noqa: BLE001
            _last_error = f"central-agent-gw: {e}"
            text = ""
    data = parse(text) if text else {}
    with _lock:
        for name in [n for n in _states if n not in targets]:
            _states.pop(name, None)
        for name, t in targets.items():
            st = _states.get(name) or AgentState(name)
            st.target, st.config = t, config
            s = agent_settings(t)
            every = s["pull_interval"] if is_pull(t) else s["metrics_interval"]
            st.stale_after = max(120, 3 * (every if is_pull(t) else max(s["heartbeat_interval"], every)) + 30)
            st.metrics.max_gap = max(300, 3 * every)
            _states[name] = st
            if name in data and name not in pulls:
                st.feed(data[name], now)
    due = [(st, pulls[n]) for n, st in list(_states.items())
           if n in pulls and not pulls[n].get("paused") and now - st.pulled_at >= agent_settings(pulls[n])["pull_interval"] - 1]
    for st, _ in due:
        st.pulled_at = now
    if due:
        with ThreadPoolExecutor(max_workers=min(8, len(due))) as pool:
            for st, t in due:
                pool.submit(_pull_one, st, t, config, now, fetch or fetch_pull)
    return _states


def _loop():
    while True:
        try:
            if any(is_agent(t) for t in load_config().get("targets") or []):
                run_once()
        except Exception as e:  # noqa: BLE001
            print(f"[AGENT] {e!r}")
        time.sleep(POLL)


def start():
    threading.Thread(target=_loop, daemon=True, name="agent-bridge").start()
    print(f"[AGENT] cm-agent bridge: {GW_URL} -> /win/<name>")
