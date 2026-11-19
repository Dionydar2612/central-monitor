"""
netstat.py - Netstat Monitor (Windows Server): TCP / UDP / คิว ต่อพอร์ต แบบ real-time + ตัวแจ้งเตือน
=====================================================================================================

ข้อมูลมาจากไหน
    agent บนเครื่อง Windows (agent/windows/cm-netstat.ps1) รันค้าง 1 process เก็บทุก 1 / 2 / 5 วินาที
    -> เขียน cm_netstat_* ลง textfile ของ windows_exporter
    puller (ไฟล์นี้) อ่านต่อเนื่องในเธรดแยก:
      - โหมด pull (central เข้าถึงพอร์ต exporter ได้): GET http://<exporter>/metrics?collect[]=textfile ทุก netstat.interval วินาที
      - โหมด push (เครื่องส่งมาเอง): ถาม Prometheus (Fluent Bit ส่ง cm_netstat_* ทุก 5 วินาที)
    เครื่องที่ยังไม่เปิด Monitoring: อ่านทุก 30 วินาทีเพื่อค้นพบพอร์ต (ไม่แจ้งเตือน)

ตั้งค่า (puller.yaml ต่อเครื่อง)  แก้จากหน้า Apps & Web > Netstat Monitor
    netstat:
      enabled: true
      interval: 1                    # 1 / 2 / 5 วินาที (central อ่านถี่เท่านี้)
      ports:
        - {port: 1433, proto: tcp, dir: in, enabled: true,
           queue: {warning: 50, critical: 80}, conn: {warning: 200, critical: 300}}

เขียนออก (data/state/netstat/)
    live.json    ค่าล่าสุดทุกเครื่อง (หน้าเว็บส่งต่อแบบ Server-Sent Events) เขียนไม่เกินวินาทีละครั้ง
    alerts.json  {"active": [...], "history": [...]}  เก็บ ALERT_RETENTION วัน
    ย้อนหลัง (กราฟ) = Prometheus (cm_netstat_conn_peak / accept_queue_peak ฯลฯ) ไม่สร้างฐานข้อมูลเพิ่ม

แจ้งเตือน (ผ่าน System log + Teams + ป๊อปอัป แบบเดียวกับ sensor)
    queue / conn เกิน warning / critical, พอร์ตที่เลือกไม่ LISTEN, เครื่องไม่ตอบ, agent หยุดส่ง, คิวพุ่งผิดปกติ
    ต้องเป็นต่อเนื่อง CONFIRM ครั้งก่อนเปิด alert · เปิดแล้วไม่สร้างซ้ำ (อัปเดต last / value) · ปกติ RECOVER ครั้ง -> Recovery
    ค่าที่อ่านไม่ได้ = None (หน้าเว็บแสดง Unavailable / Unsupported) ไม่ใช่ 0 และไม่แจ้งเตือน
"""

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from config import STATE_DIR, load_config

NETSTAT_DIR = os.path.join(STATE_DIR, "netstat")
LIVE_FILE = os.path.join(NETSTAT_DIR, "live.json")
ALERTS_FILE = os.path.join(NETSTAT_DIR, "alerts.json")
PROMETHEUS = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090").rstrip("/")

INTERVALS = (1, 2, 5)
DISCOVERY_EVERY = 30          # วินาที: เครื่องที่ยังไม่เปิด Monitoring
PUSH_EVERY = 2                # วินาที: ถาม Prometheus (ข้อมูล push มาทุก 5 วิ)
FETCH_TIMEOUT = 3
CONFIRM = 3                   # ต้องผิดปกติต่อเนื่องกี่ครั้งก่อนเปิด alert
RECOVER = 3                   # ต้องปกติต่อเนื่องกี่ครั้งก่อนปิด alert
STALE_MIN = 10                # วินาที: heartbeat เก่ากว่า max(STALE_MIN, 3 x interval) = agent หยุดส่ง
PUSH_STALE = 45               # วินาที: โหมด push (ผ่าน Prometheus)
ALERT_RETENTION = 30 * 86400
MAX_HISTORY = 5000
MAX_PORTS = 400               # ต่อเครื่องใน live.json
SPIKE_FACTOR = 3.0            # คิวพุ่ง: ค่าปัจจุบัน >= 3 เท่าของค่าเฉลี่ยเคลื่อนที่
SPIKE_MIN = 10
STATES = ("established", "syn_sent", "syn_received", "fin_wait1", "fin_wait2", "time_wait",
          "close_wait", "last_ack", "closing")
LEVEL_RANK = {None: 0, "warning": 1, "critical": 2}

_LINE = re.compile(r'^(cm_netstat_\w+)(?:\{(.*)\})?\s+(\S+)')
_LBL = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')


# ------------------------------------------------------------------
# ตั้งค่า
# ------------------------------------------------------------------

def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def port_key(proto, direction, port):
    return f"{proto}/{direction}/{int(port)}"


def host_config(target):
    """netstat: ของเครื่อง -> {enabled, interval, ports: [{key, port, proto, dir, enabled, queue, conn}]}"""
    n = target.get("netstat") if isinstance(target.get("netstat"), dict) else {}
    try:
        interval = int(n.get("interval") or 1)
    except (TypeError, ValueError):
        interval = 1
    ports = []
    for p in n.get("ports") or []:
        if not isinstance(p, dict):
            continue
        try:
            port = int(p.get("port"))
        except (TypeError, ValueError):
            continue
        proto = "udp" if str(p.get("proto") or "tcp").lower() == "udp" else "tcp"
        direction = "out" if str(p.get("dir") or "in") == "out" else "in"
        th = {}
        for k in ("queue", "conn"):
            t = p.get(k) if isinstance(p.get(k), dict) else {}
            w, c = _num(t.get("warning")), _num(t.get("critical"))
            th[k] = {"warning": w, "critical": c}
        ports.append({"key": port_key(proto, direction, port), "port": port, "proto": proto, "dir": direction,
                      "enabled": p.get("enabled", True) is not False, **th})
    return {"enabled": bool(n.get("enabled")), "interval": interval if interval in INTERVALS else 1, "ports": ports}


def windows_targets(config):
    return [t for t in config.get("targets") or []
            if isinstance(t, dict) and str(t.get("type") or "") == "agent" and not t.get("paused")]


# ------------------------------------------------------------------
# อ่านค่า
# ------------------------------------------------------------------

def _unescape(v):
    return re.sub(r'\\(.)', lambda m: "\n" if m.group(1) == "n" else m.group(1), v)


def parse_text(text):
    """หน้า /metrics -> [(ชื่อ, labels, ค่า)] เฉพาะ cm_netstat_*"""
    out = []
    for line in str(text or "").splitlines():
        if not line.startswith("cm_netstat_"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        v = _num(m.group(3))
        if v is None:
            continue
        out.append((m.group(1), {k: _unescape(x) for k, x in _LBL.findall(m.group(2) or "")}, v))
    return out


def snapshot(samples):
    """[(ชื่อ, labels, ค่า)] -> ค่าของเครื่อง  None = ไม่มีข้อมูล Netstat เลย (agent ยังไม่ติดตั้ง / ไม่ส่ง)"""
    if not any(n == "cm_netstat_heartbeat_seconds" for n, _, _ in samples):
        return None
    snap = {"heartbeat": None, "interval": None, "os": "", "version": "", "collect_ms": None,
            "support": {"recv": False, "send": False, "accept": False}, "ports": {}}
    ports = snap["ports"]

    def P(proto, direction, port):
        try:
            port = int(port)
        except (TypeError, ValueError):
            return None
        k = port_key(proto, direction, port)
        if k not in ports:
            ports[k] = {"key": k, "port": port, "proto": proto, "dir": direction, "listening": False, "addr": "",
                        "pid": None, "proc": "", "states": {}, "total": None, "peak": None, "peers": [],
                        "accept_queue": None, "accept_peak": None, "accept_limit": None, "queue_source": ""}
        return ports[k]

    for name, lb, v in samples:
        if name == "cm_netstat_heartbeat_seconds":
            snap["heartbeat"] = v
        elif name == "cm_netstat_info":
            snap["os"], snap["version"] = lb.get("os", ""), lb.get("version", "")
            try:
                snap["interval"] = int(lb.get("interval") or 0) or None
            except ValueError:
                pass
        elif name == "cm_netstat_collect_ms":
            snap["collect_ms"] = v
        elif name == "cm_netstat_queue_supported":
            if lb.get("kind") in snap["support"]:
                snap["support"][lb["kind"]] = v >= 1
        elif name == "cm_netstat_listen":
            p = P(lb.get("proto", "tcp"), "in", lb.get("port"))
            if p:
                p["listening"], p["addr"], p["proc"] = True, lb.get("addr", ""), lb.get("proc", "")
                p["pid"] = int(lb["pid"]) if str(lb.get("pid") or "").isdigit() else None
                if p["proto"] == "tcp" and p["total"] is None:
                    p["total"] = 0                     # LISTEN แล้วยังไม่มีใครต่อ = 0 จริง
        elif name == "cm_netstat_conn":
            p = P("tcp", lb.get("dir", "in"), lb.get("port"))
            if p:
                st = lb.get("state", "other")
                p["states"][st] = p["states"].get(st, 0) + int(v)
        elif name == "cm_netstat_conn_total":
            p = P("tcp", lb.get("dir", "in"), lb.get("port"))
            if p:
                p["total"] = int(v)
        elif name == "cm_netstat_conn_peak":
            p = P("tcp", lb.get("dir", "in"), lb.get("port"))
            if p:
                p["peak"] = int(v)
        elif name == "cm_netstat_peer":
            p = P("tcp", lb.get("dir", "in"), lb.get("port"))
            if p:
                p["peers"].append({"remote": lb.get("remote", ""), "count": int(v)})
        elif name == "cm_netstat_proc":
            p = P("tcp", lb.get("dir", "out"), lb.get("port"))
            if p and not p["proc"]:
                p["proc"] = lb.get("proc", "")
                p["pid"] = int(lb["pid"]) if str(lb.get("pid") or "").isdigit() else None
        elif name in ("cm_netstat_accept_queue", "cm_netstat_accept_queue_peak", "cm_netstat_accept_queue_limit"):
            p = P(lb.get("proto", "tcp"), "in", lb.get("port"))
            if p:
                field = {"cm_netstat_accept_queue": "accept_queue", "cm_netstat_accept_queue_peak": "accept_peak",
                         "cm_netstat_accept_queue_limit": "accept_limit"}[name]
                p[field] = int(v)
                p["queue_source"] = lb.get("source", "")
    for p in ports.values():
        p["peers"].sort(key=lambda x: -x["count"])
        if p["proto"] == "tcp" and p["total"] is not None:
            for st in STATES:
                p["states"].setdefault(st, 0)
    return snap


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))      # เครื่อง Windows อยู่วงภายใน


def exporter_url(exporter):
    e = str(exporter).strip()
    if "://" not in e:
        e = "http://" + e
    e = e.rstrip("/")
    if not e.endswith("/metrics"):
        e += "/metrics"
    return e + "?" + urllib.parse.urlencode({"collect[]": "textfile"})


def fetch_exporter(exporter):
    with _opener().open(urllib.request.Request(exporter_url(exporter), headers={"Accept": "text/plain"}),
                        timeout=FETCH_TIMEOUT) as r:
        return parse_text(r.read(4 * 1024 * 1024).decode("utf-8", "replace"))


def fetch_prometheus(web):
    q = '{__name__=~"cm_netstat_.+",cm_web="%s"}' % str(web).replace('"', "")
    url = PROMETHEUS + "/api/v1/query?" + urllib.parse.urlencode({"query": q})
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT + 2) as r:
        rows = json.loads(r.read().decode()).get("data", {}).get("result", [])
    out = []
    for row in rows:
        m = dict(row.get("metric") or {})
        v = _num((row.get("value") or [None, None])[1])
        if v is not None:
            out.append((m.pop("__name__", ""), m, v))
    return out


# ------------------------------------------------------------------
# ประเมินค่า (ไม่มี I/O -> เทสต์ได้)
# ------------------------------------------------------------------

def level_of(value, th):
    """ค่า >= critical -> critical, >= warning -> warning, ต่ำกว่า -> None  ค่า / เกณฑ์ไม่มี -> None"""
    if value is None or not th:
        return None
    c, w = th.get("critical"), th.get("warning")
    if c is not None and value >= c:
        return "critical"
    if w is not None and value >= w:
        return "warning"
    return None


def port_row(cfg_port, snap):
    """1 พอร์ตที่เลือก Monitor -> แถวของตาราง (ค่าที่อ่านไม่ได้ = None)"""
    p = (snap or {}).get("ports", {}).get(cfg_port["key"]) if snap else None
    support = (snap or {}).get("support") or {}
    row = {"key": cfg_port["key"], "port": cfg_port["port"], "proto": cfg_port["proto"], "dir": cfg_port["dir"],
           "enabled": cfg_port["enabled"], "queue_th": cfg_port["queue"], "conn_th": cfg_port["conn"],
           "listening": None, "proc": "", "pid": None, "conn": None, "peak": None, "established": None, "states": {},
           "recv_q": None, "send_q": None, "accept_q": None, "accept_peak": None, "accept_limit": None, "util": None,
           "queue": None, "queue_kind": "", "queue_note": "", "syn_backlog": None}
    if snap is None:
        return row
    row["recv_q_note"] = "Unsupported (Windows)" if not support.get("recv") else ""
    row["send_q_note"] = "Unsupported (Windows)" if not support.get("send") else ""
    if p is None:
        row["listening"] = False if cfg_port["dir"] == "in" else None
        if cfg_port["proto"] == "tcp" and cfg_port["dir"] == "out":
            row["conn"] = 0                                   # ไม่มีการเชื่อมต่อขาออกไปพอร์ตนี้ = 0 จริง
        row["queue_note"] = "Unavailable" if cfg_port["dir"] == "in" else ""
        return row
    row.update(listening=p["listening"] if cfg_port["dir"] == "in" else None, proc=p["proc"], pid=p["pid"],
               conn=p["total"], peak=p["peak"], states=dict(p["states"]),
               established=p["states"].get("established") if p["total"] is not None else None,
               syn_backlog=p["states"].get("syn_received") if p["total"] is not None else None,
               peers=p["peers"][:3], addr=p["addr"])
    if p["accept_queue"] is not None:
        row.update(accept_q=p["accept_queue"], accept_peak=p["accept_peak"], accept_limit=p["accept_limit"],
                   queue=p["accept_queue"], queue_kind=f"accept ({p['queue_source'] or 'listen'})")
        if p["accept_limit"]:
            row["util"] = round(p["accept_queue"] * 100.0 / p["accept_limit"], 1)
    else:
        row["queue_note"] = ("Unsupported: Windows ไม่มีคิวต่อ socket / พอร์ตนี้ไม่ใช่ HTTP.sys"
                             if cfg_port["dir"] == "in" else "Unsupported")
    return row


def evaluate(web, ip, cfg, snap, online, stale, now, baselines):
    """แถวของพอร์ตที่เลือก + เงื่อนไขแจ้งเตือน (list ของ dict) ของเครื่องเดียว
    baselines = {key: ค่าเฉลี่ยเคลื่อนที่ของคิว} (ถูกแก้ในฟังก์ชันนี้)"""
    rows, conds = [], []
    base = {"web": web, "ip": ip}
    if not cfg["enabled"]:
        return rows, conds
    conds.append(dict(base, akey=f"{web}::host", kind="host_offline", port=None, process="",
                      level="critical" if not online else None, value=None, threshold=None,
                      message="เครื่องไม่ตอบ / ดึงข้อมูล Netstat ไม่ได้"))
    conds.append(dict(base, akey=f"{web}::stale", kind="agent_stale", port=None, process="",
                      level="critical" if online and stale else None, value=None, threshold=None,
                      message="agent หยุดส่งข้อมูล Netstat (heartbeat ไม่อัปเดต)"))
    usable = online and not stale
    for cp in cfg["ports"]:
        row = port_row(cp, snap if usable else None)
        rows.append(row)
        if not cp["enabled"] or not usable:
            continue
        pre = dict(base, port=cp["port"], proto=cp["proto"], dir=cp["dir"], process=row["proc"])
        ql, cl = level_of(row["queue"], cp["queue"]), level_of(row["conn"], cp["conn"])
        row["queue_level"], row["conn_level"] = ql, cl
        conds.append(dict(pre, akey=f"{web}::{cp['key']}::queue", kind="queue", level=ql, value=row["queue"],
                          threshold=cp["queue"].get("critical" if ql == "critical" else "warning"),
                          message=f"คิว {row['queue_kind'] or ''} {row['queue']}".strip()))
        conds.append(dict(pre, akey=f"{web}::{cp['key']}::conn", kind="conn", level=cl, value=row["conn"],
                          threshold=cp["conn"].get("critical" if cl == "critical" else "warning"),
                          message=f"การเชื่อมต่อ {row['conn']}"))
        if cp["dir"] == "in":
            conds.append(dict(pre, akey=f"{web}::{cp['key']}::listen", kind="not_listening",
                              level="critical" if row["listening"] is False else None, value=None, threshold=None,
                              message=f"พอร์ต {cp['port']}/{cp['proto']} ไม่ได้ LISTEN"))
        # คิวพุ่งผิดปกติ: คิว accept (ถ้าอ่านได้) ไม่งั้นใช้ SYN_RECEIVED (คิว handshake ของ listen)
        sv, sname = (row["queue"], "คิว") if row["queue"] is not None else (row["syn_backlog"], "SYN backlog")
        bk = cp["key"]
        spike = None
        if sv is not None:
            b = baselines.get(bk)
            if b is not None and sv >= SPIKE_MIN and sv >= b * SPIKE_FACTOR:
                spike = "warning"
            baselines[bk] = sv if b is None else b * 0.97 + sv * 0.03
        conds.append(dict(pre, akey=f"{web}::{cp['key']}::spike", kind="queue_spike", level=spike, value=sv,
                          threshold=round(baselines.get(bk, 0) * SPIKE_FACTOR, 1) if spike else None,
                          message=f"{sname} เพิ่มสูงผิดปกติ ({sv})"))
        row["status"] = "critical" if "critical" in (ql, cl) or row["listening"] is False else \
            "warning" if "warning" in (ql, cl, spike) else "normal"
    for row in rows:
        if "status" not in row:
            row["status"] = "offline" if not online else "stale" if stale else "disabled"
    return rows, conds


class AlertEngine:
    """เปิด / อัปเดต / ปิด alert จากเงื่อนไขรอบละครั้ง  ไม่สร้างซ้ำทุกวินาที  คืน (เหตุการณ์ที่ต้องบันทึก, มีการเปลี่ยนไหม)"""

    def __init__(self, data=None):
        data = data if isinstance(data, dict) else {}
        self.active = {a["akey"]: a for a in data.get("active") or [] if isinstance(a, dict) and a.get("akey")}
        self.history = [h for h in data.get("history") or [] if isinstance(h, dict)]
        self.abnormal = {}         # akey -> ครั้งที่ผิดปกติต่อเนื่อง (ระดับใดก็ได้)
        self.same = {}             # akey -> [ระดับ, ครั้งที่เป็นระดับนี้ต่อเนื่อง]
        self.okcount = {}          # akey -> ครั้งที่ปกติต่อเนื่อง

    def dump(self):
        return {"active": list(self.active.values()), "history": self.history[-MAX_HISTORY:]}

    def _close(self, a, now, why, events):
        a = dict(a, status="recovered" if why == "recovered" else why, recovered_at=now,
                 duration=round(now - a["first"], 1))
        self.history.append(a)
        if why == "recovered":
            events.append(("recovered", a))

    def step(self, conds, now, scope=None):
        """conds = เงื่อนไขของรอบนี้  scope = ชุดเครื่องที่ประเมินรอบนี้ (alert ของเครื่องอื่นไม่แตะ)"""
        events, changed = [], False
        seen = set()
        for c in conds:
            k = c["akey"]
            seen.add(k)
            a = self.active.get(k)
            lv = c["level"]
            if lv:
                self.okcount.pop(k, None)
                self.abnormal[k] = self.abnormal.get(k, 0) + 1
                sm = self.same.get(k)
                if sm and sm[0] == lv:
                    sm[1] += 1
                else:
                    self.same[k] = sm = [lv, 1]
                if a is None:
                    if self.abnormal[k] >= CONFIRM:
                        a = {k2: c[k2] for k2 in ("akey", "kind", "web", "ip", "port", "proto", "dir", "process",
                                                  "value", "threshold", "message") if k2 in c}
                        a.update(id=f"{k}@{int(now)}", level=lv, first=now, last=now, peak=c.get("value"), status="active")
                        self.active[k] = a
                        events.append(("open", a))
                        changed = True
                    continue
                a.update(last=now, value=c.get("value"), process=c.get("process") or a.get("process"),
                         message=c.get("message"), duration=round(now - a["first"], 1))
                if c.get("value") is not None and (a.get("peak") is None or c["value"] > a["peak"]):
                    a["peak"] = c["value"]
                if lv != a["level"] and sm[1] >= CONFIRM:
                    old = a["level"]
                    a.update(level=lv, threshold=c.get("threshold"))
                    events.append(("escalate" if LEVEL_RANK[lv] > LEVEL_RANK[old] else "lower", a))
                    changed = True
            else:
                self.abnormal.pop(k, None)
                self.same.pop(k, None)
                if a is None:
                    continue
                n = self.okcount.get(k, 0) + 1
                self.okcount[k] = n
                if n >= RECOVER:
                    del self.active[k]
                    self.okcount.pop(k, None)
                    self._close(a, now, "recovered", events)
                    changed = True
        # เงื่อนไขที่หายไป (เลิกเลือกพอร์ต / ปิด Monitoring / ลบเครื่อง) -> ปิดเงียบ ๆ
        for k in [k for k in self.active if k not in seen and (scope is None or self.active[k]["web"] in scope)]:
            a = self.active.pop(k)
            self._close(a, now, "removed", events)
            changed = True
        cutoff = now - ALERT_RETENTION
        if self.history and (self.history[0].get("recovered_at") or now) < cutoff:
            self.history = [h for h in self.history if (h.get("recovered_at") or now) >= cutoff]
            changed = True
        return events, changed


KIND_TEXT = {"queue": "Queue", "conn": "Connections", "not_listening": "Not listening", "host_offline": "Host offline",
             "agent_stale": "Agent stopped", "queue_spike": "Queue spike"}


def to_system_events(events, groups=None, priorities=None):
    """เหตุการณ์ของ AlertEngine -> รายการ System log (+ ข้อมูลการ์ด Teams แบบเดียวกับ sensor)"""
    out = []
    for what, a in events:
        lv = {"critical": "error", "warning": "warning"}.get(a["level"], "warning")
        if a["kind"] in ("host_offline", "agent_stale"):
            lv = "down" if a["level"] == "critical" else lv
        port = f" :{a['port']}/{a.get('proto', 'tcp')}" if a.get("port") else ""
        name = f"Netstat{port} {KIND_TEXT.get(a['kind'], a['kind'])}"
        val = a.get("value")
        th = f" (เกณฑ์ {a['threshold']:g})" if isinstance(a.get("threshold"), (int, float)) else ""
        if what == "recovered":
            msg = f"{a['web']} · {name} กลับปกติ · เป็นอยู่ {int(a.get('duration') or 0)} วินาที"
        else:
            msg = f"{a['web']} · {name}: {a.get('message') or ''}{th}" + (f" · {a['process']}" if a.get("process") else "")
        e = {"source": "netstat", "level": "recovered" if what == "recovered" else lv, "web": a["web"],
             "group": (groups or {}).get(a["web"], ""), "sensor": name, "kind": "custom", "sid": a["akey"],
             "from": lv if what == "recovered" else "ok", "to": "ok" if what == "recovered" else lv,
             "value": val, "unit": "", "warning": a.get("threshold") if a["level"] == "warning" else None,
             "error": a.get("threshold") if a["level"] == "critical" else None, "host": a.get("ip"),
             "priority": (priorities or {}).get(a["web"], 4 if a["level"] == "critical" else 3),
             "message": msg, "netstat_event": what}
        if what == "recovered":
            e["duration"] = a.get("duration")
        out.append(e)
    return out


# ------------------------------------------------------------------
# ลูปหลัก (เธรดแยกใน puller)
# ------------------------------------------------------------------

def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


class Monitor:
    def __init__(self, fetch_pull=fetch_exporter, fetch_push=fetch_prometheus, emit=None):
        self.fetch_pull, self.fetch_push = fetch_pull, fetch_push
        self.emit = emit
        self.engine = AlertEngine(_read(ALERTS_FILE, {}))
        self.hosts = {}            # web -> สถานะล่าสุด
        self.inflight = {}         # web -> future
        self.next_at = {}
        self.baselines = {}
        self.pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="netstat")
        self.config, self.config_at = {}, 0.0
        self.dirty = True
        self.written_at = 0.0

    def _config(self, now):
        if now - self.config_at > 5:
            try:
                self.config = load_config()
            except Exception as e:
                print(f"[NETSTAT] config error {e}")
            self.config_at = now
        return self.config

    def _poll(self, t, pull):
        web = t["name"]
        try:
            samples = self.fetch_pull((t.get("agent") or {}).get("exporter")) if pull else self.fetch_push(web)
            return web, samples, None
        except Exception as e:
            return web, None, str(e)[:200]

    def tick(self, now=None):
        now = now or time.time()
        cfg_all = self._config(now)
        targets = windows_targets(cfg_all)
        names = {t["name"] for t in targets}
        for web in [w for w in self.hosts if w not in names]:
            self.hosts.pop(web, None)
            self.dirty = True
        # เก็บผลที่เสร็จแล้ว
        for web, fut in list(self.inflight.items()):
            if fut.done():
                del self.inflight[web]
                w, samples, err = fut.result()
                self._update(w, samples, err, now)
        # สั่งอ่านเครื่องที่ถึงเวลา
        for t in targets:
            web, hc = t["name"], host_config(t)
            agent = t.get("agent") or {}
            pull = agent.get("mode") == "pull" and bool(agent.get("exporter"))
            every = hc["interval"] if hc["enabled"] else DISCOVERY_EVERY
            if not pull:
                every = max(every, PUSH_EVERY)
            if web in self.inflight or now < self.next_at.get(web, 0):
                continue
            self.next_at[web] = now + every
            h = self.hosts.setdefault(web, {"web": web})
            h.update(ip=t.get("host") or "", group=t.get("group") or "", mode="pull" if pull else "push",
                     enabled=hc["enabled"], interval=hc["interval"], priority=t.get("priority") or 3)
            self.inflight[web] = self.pool.submit(self._poll, t, pull)
        # ประเมิน + แจ้งเตือน (เฉพาะเครื่องที่เปิด Monitoring)
        conds = []
        tmap = {t["name"]: t for t in targets}
        for web, h in self.hosts.items():
            t = tmap.get(web)
            if not t:
                continue
            hc = host_config(t)
            rows, c = evaluate(web, h.get("ip", ""), hc, h.get("snap"), h.get("online", False), h.get("stale", False),
                               now, self.baselines.setdefault(web, {}))
            h["rows"] = rows
            if hc["enabled"] and h.get("checked"):
                conds += c
        # เครื่องที่ประเมินรอบนี้ (+ เครื่องที่ถูกลบออกจาก puller.yaml -> ปิด alert ที่ค้าง)
        scope = {w for w, h in self.hosts.items() if h.get("checked")}
        scope |= {a["web"] for a in self.engine.active.values()} - names
        events, changed = self.engine.step(conds, now, scope=scope)
        if changed:
            _write(ALERTS_FILE, self.engine.dump())
            self.dirty = True
        if events and self.emit:
            groups = {w: h.get("group", "") for w, h in self.hosts.items()}
            prios = {w: h.get("priority", 3) for w, h in self.hosts.items()}
            try:
                self.emit(to_system_events(events, groups, prios), cfg_all)
            except Exception as e:
                print(f"[NETSTAT] emit error {e}")
        if self.dirty and now - self.written_at >= 1:
            self.write_live(now)
        return events

    def _update(self, web, samples, err, now):
        h = self.hosts.get(web)
        if h is None:
            return
        h["checked"] = True
        h["fetched_at"] = now
        if err is not None:
            h.update(online=False, error=err)
        else:
            snap = snapshot(samples)
            h["error"] = "" if snap else "ไม่พบข้อมูล Netstat (ยังไม่ได้ติดตั้ง agent รุ่นใหม่ หรือ cm-netstat ไม่ทำงาน)"
            h["online"] = True
            if snap:
                h["snap"] = snap
                h["last_seen"] = snap["heartbeat"]
            # agent หยุดส่ง = heartbeat ไม่เปลี่ยนนานเกิน limit (วัดด้วยนาฬิกาของ central -> ไม่ขึ้นกับเวลาของเครื่อง Windows)
            limit = PUSH_STALE if h.get("mode") == "push" else max(STALE_MIN, 3 * ((snap or {}).get("interval") or 3))
            hb = (snap or {}).get("heartbeat")
            if hb is not None and hb != h.get("hb"):
                h["hb"], h["hb_changed_at"] = hb, now
            h["stale"] = hb is None or now - h.get("hb_changed_at", now) > limit
            if snap is None and h.get("mode") == "push":
                h["online"] = False            # Prometheus ไม่มีข้อมูลของเครื่องนี้เลย = ไม่ได้รับจากเครื่อง
        self.dirty = True

    def write_live(self, now):
        hosts = {}
        for web, h in self.hosts.items():
            snap = h.get("snap") or {}
            ports = sorted(snap.get("ports", {}).values(), key=lambda p: (p["dir"] != "in", p["proto"], p["port"]))[:MAX_PORTS]
            hosts[web] = {k: h.get(k) for k in ("web", "ip", "group", "mode", "enabled", "interval", "online", "stale",
                                                 "error", "fetched_at", "last_seen", "rows")}
            hosts[web].update(os=snap.get("os", ""), agent_version=snap.get("version", ""),
                              agent_interval=snap.get("interval"), collect_ms=snap.get("collect_ms"),
                              support=snap.get("support"), ports=ports, has_data=bool(snap))
        _write(LIVE_FILE, {"updated": now, "hosts": hosts, "active": list(self.engine.active.values())})
        self.written_at, self.dirty = now, False


def _emit(events, config):
    from events import append_events
    import notify
    append_events(events)
    try:
        notify.sensor_events(events, config)
    except Exception as e:
        print(f"[NETSTAT] notify error {e}")


def _loop():
    m = Monitor(emit=_emit)
    while True:
        try:
            m.tick()
        except Exception as e:
            print(f"[NETSTAT] tick error {e!r}")
        time.sleep(0.25)


def start():
    threading.Thread(target=_loop, daemon=True, name="netstat").start()
    print("[NETSTAT] netstat monitor started")
