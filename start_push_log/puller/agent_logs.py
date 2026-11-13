"""
agent_logs.py - log ของเครื่อง Windows โหมด pull (central ดึงจาก Windows เอง)
---------------------------------------------------------------------------
เครื่อง Windows ต่อไปหา central ไม่ได้ -> ส่ง log เองไม่ได้ จึงให้ cm-logs.ps1 บนเครื่อง (ทุก 1 นาที)
เขียน ERROR / WARN ล่าสุดลง textfile ของ windows_exporter:

    cm_log_entry{seq="12",type="eventlog",body="2026-11-13T02:00:00Z [ERROR] [System] Disk (Event 7): ..."} 1763000000
    cm_log_seq 12

ทุกรอบ log (scheduler.log_interval) puller อ่าน http://<agent.exporter>/metrics
-> เอาเฉพาะ seq ที่ใหม่กว่าครั้งก่อน -> เขียนลง data/buffer แบบเดียวกับ log ของเครื่อง Linux
   ชื่อไฟล์ <site>__<เครื่อง>__<eventlog|application>__... -> otel -> OpenSearch (index เดิม)
checkpoint: state["<เครื่อง>::agent-pull::<exporter>"] = {"seq": ล่าสุดที่เก็บแล้ว}
  seq บนเครื่องเริ่มใหม่ (ลบ state บนเครื่อง / ติดตั้งใหม่) -> seq สูงสุดน้อยกว่าที่จำไว้ -> เริ่มนับใหม่
Prometheus ทิ้ง cm_log_entry (metric_relabel_configs ใน prometheus.yml) ไม่เก็บเป็น metric
"""

import re
import urllib.request
from datetime import datetime, timezone

from checkpoint import save_state
from log_filter import store_info_enabled

FETCH_TIMEOUT = 15
MAX_BYTES = 8 * 1024 * 1024
TYPES = ("eventlog", "application")

_LINE_RE = re.compile(r'^cm_log_entry\{(?P<labels>.*)\}\s+(?P<value>\S+)\s*$')
_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
_LEVEL_RE = re.compile(r'\[(ERROR|WARN|INFO)\]')


def _level(body):
    m = _LEVEL_RE.search(body[:80])
    return m.group(1) if m else ""


def is_pull_agent(target):
    """เครื่อง Windows ที่ตั้ง agent.mode = pull และมี exporter"""
    agent = target.get("agent") or {}
    return (str(target.get("type") or "") == "agent" and agent.get("mode") == "pull"
            and bool(agent.get("exporter")) and not target.get("paused"))


def _unescape(value):
    """ค่า label แบบ Prometheus: \\\\ \\" \\n"""
    return re.sub(r'\\(.)', lambda m: "\n" if m.group(1) == "n" else m.group(1), value)


def parse_entries(text):
    """อ่าน cm_log_entry จากหน้า /metrics -> list ของ {seq, type, body, ts} เรียงตาม seq"""
    out = []
    for line in str(text or "").splitlines():
        if not line.startswith("cm_log_entry{"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        labels = {k: _unescape(v) for k, v in _LABEL_RE.findall(m.group("labels"))}
        try:
            seq = int(labels.get("seq", ""))
            ts = float(m.group("value"))
        except ValueError:
            continue
        kind = labels.get("type") if labels.get("type") in TYPES else "application"
        body = " ".join(str(labels.get("body") or "").split())
        if body:
            out.append({"seq": seq, "type": kind, "body": body, "ts": ts})
    out.sort(key=lambda e: e["seq"])
    return out


def _url(exporter):
    exporter = str(exporter).strip()
    if "://" not in exporter:
        exporter = "http://" + exporter
    return exporter.rstrip("/") + ("" if exporter.rstrip("/").endswith("/metrics") else "/metrics")


def fetch_metrics(exporter):
    req = urllib.request.Request(_url(exporter), headers={"Accept": "text/plain"})
    # ไม่ผ่าน proxy (เครื่อง Windows อยู่ในวงภายใน)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=FETCH_TIMEOUT) as r:
        return r.read(MAX_BYTES).decode("utf-8", "replace")


def new_entries(entries, last_seq):
    """seq ที่ยังไม่เคยเก็บ  คืน (รายการใหม่, seq ล่าสุดที่ควรจำ)
    last_seq = None (ครั้งแรก) -> เก็บทั้งหมดที่เครื่องยังเก็บไว้ (ย้อนหลังไม่เกิน ~3 ชม.)
    seq สูงสุดบนเครื่อง < last_seq -> เครื่องเริ่มนับใหม่ -> เก็บทั้งหมด"""
    if not entries:
        return [], last_seq
    top = entries[-1]["seq"]
    if last_seq is None or top < last_seq:
        return entries, top
    return [e for e in entries if e["seq"] > last_seq], top


def pull_agent_logs(config, target, state, fetch=fetch_metrics, write=None, collapse=None):
    """ดึง log ของเครื่อง Windows โหมด pull 1 เครื่อง -> data/buffer  คืนจำนวนรายการที่เก็บ
    write / collapse = ใช้ของ log_puller (import ตอนเรียก: เทสต์ส่งตัวแทนได้โดยไม่ต้องมี prometheus_client)"""
    if write is None or collapse is None:
        from log_puller import _collapse, _write_buffer
        write, collapse = write or _write_buffer, collapse or _collapse
    name = target["name"]
    exporter = (target.get("agent") or {}).get("exporter")
    key = f"{name}::agent-pull::{exporter}"
    previous = state.get(key) or {}
    last = previous.get("seq")

    entries = parse_entries(fetch(exporter))
    fresh, top = new_entries(entries, last)
    if not fresh:
        print(f"[NO CHANGE] {key}")
        if top is not None and top != last:
            state[key] = dict(previous, target=name, seq=top)
            save_state(state)
        return 0

    store_info = store_info_enabled(config)
    stored = 0
    for kind in TYPES:
        rows = [e for e in fresh if e["type"] == kind]
        if not store_info:
            rows = [e for e in rows if _level(e["body"]) != "INFO"]
        if not rows:
            continue
        data = ("\n".join(e["body"] for e in rows) + "\n").encode("utf-8")
        data, _ = collapse(config, data, name, kind)
        if data:
            write(config, name, kind, f"{key}::{kind}", rows[0]["seq"], rows[-1]["seq"], data)
            stored += len(rows)

    state[key] = {"target": name, "source": "agent-pull", "path": str(exporter), "seq": top,
                  "updated_at": datetime.now(timezone.utc).isoformat()}
    save_state(state)
    print(f"[OK] agent-pull {name} new={len(fresh)} stored={stored}")
    return stored

