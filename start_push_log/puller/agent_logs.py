"""
agent_logs.py - log ของเครื่อง cm-agent โหมด pull (central อ่านจากเครื่อง Windows เอง)
=====================================================================================
เครื่อง Windows ต่อไปหา central ไม่ได้ -> ส่ง log เองไม่ได้ จึงให้ cm-logs.ps1 บนเครื่อง (task CentralMonitorLogs)
เขียน ERROR / WARN ล่าสุดลง textfile ของ windows_exporter:

    cm_log_entry{seq="12",type="eventlog",body="2026-11-13T02:00:00Z [ERROR] [System] Disk (Event 7): ..."} 1763000000

agent_bridge.py อ่านหน้า /metrics ของเครื่องอยู่แล้ว (รอบ metrics) -> ส่งข้อความเดียวกันมาที่นี่
-> เอาเฉพาะ seq ที่ใหม่กว่าครั้งก่อน -> เขียนลง data/buffer แบบเดียวกับ log ของเครื่อง Linux (otel -> OpenSearch)
seq ล่าสุดที่เก็บแล้ว: data/state/agent-pull.json {"<เครื่อง>": seq}
seq บนเครื่องเริ่มใหม่ (ติดตั้งใหม่) -> seq สูงสุดน้อยกว่าที่จำไว้ -> เริ่มนับใหม่
cm_log_entry ไม่เข้า Prometheus (Prometheus อ่าน /win/<เครื่อง> ที่ agent_bridge สร้าง ไม่ใช่หน้า exporter ตรง ๆ)
"""

import json
import os
import re
import tempfile
import threading

from config import STATE_DIR

SEQ_FILE = os.path.join(STATE_DIR, "agent-pull.json")
TYPES = ("eventlog", "application")
_lock = threading.Lock()

_LINE_RE = re.compile(r'^cm_log_entry\{(?P<labels>.*)\}\s+(?P<value>\S+)\s*$')
_LABEL_RE = re.compile(r'(\w+)="((?:[^"\\]|\\.)*)"')
_LEVEL_RE = re.compile(r'\[(ERROR|WARN|INFO)\]')


def _level(body):
    m = _LEVEL_RE.search(body[:80])
    return m.group(1) if m else ""


def _unescape(value):
    return re.sub(r'\\(.)', lambda m: "\n" if m.group(1) == "n" else m.group(1), value)


def parse_entries(text):
    """cm_log_entry จากหน้า /metrics -> list ของ {seq, type, body, ts} เรียงตาม seq"""
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


def new_entries(entries, last_seq):
    """seq ที่ยังไม่เคยเก็บ -> (รายการใหม่, seq ล่าสุดที่ควรจำ)"""
    if not entries:
        return [], last_seq
    top = entries[-1]["seq"]
    if last_seq is None or top < last_seq:
        return entries, top
    return [e for e in entries if e["seq"] > last_seq], top


def _load():
    try:
        with open(SEQ_FILE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(d):
    os.makedirs(STATE_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, prefix=".agent-pull.")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, SEQ_FILE)


def store(config, name, text, write=None, collapse=None):
    """เก็บ log ใหม่ของเครื่อง name จากข้อความ /metrics -> จำนวนที่เก็บ"""
    entries = parse_entries(text)
    if not entries:
        return 0
    if write is None or collapse is None:
        from log_puller import _collapse, _write_buffer
        write, collapse = write or _write_buffer, collapse or _collapse
    from log_filter import store_info_enabled
    with _lock:
        seqs = _load()
        last = seqs.get(name)
        fresh, top = new_entries(entries, last)
        stored = 0
        store_info = store_info_enabled(config)
        for kind in TYPES:
            rows = [e for e in fresh if e["type"] == kind]
            if not store_info:
                rows = [e for e in rows if _level(e["body"]) != "INFO"]
            if not rows:
                continue
            data = ("\n".join(e["body"] for e in rows) + "\n").encode("utf-8")
            data, _ = collapse(config, data, name, kind)
            if data:
                write(config, name, kind, f"{name}::agent-pull::{kind}", rows[0]["seq"], rows[-1]["seq"], data)
                stored += len(rows)
        if top != last:
            seqs[name] = top
            _save(seqs)
    if stored:
        print(f"[AGENT PULL] {name} logs stored={stored}")
    return stored


def forget(names):
    """เครื่องที่ถูกลบ -> ลืม seq"""
    with _lock:
        seqs = _load()
        if any(n in seqs for n in names):
            for n in names:
                seqs.pop(n, None)
            _save(seqs)
