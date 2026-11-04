"""
system_state.py - อ่านไฟล์สถานะที่ puller เขียนไว้ (read-only)
==============================================================

ข้อมูลที่ dashboard ได้จาก "ไฟล์" (ไม่ใช่ Prometheus/OpenSearch)
    1) สถานะเครื่อง: service, process, system event, load, uptime, swap, inode
       ผู้เขียน: start_push_log/puller/system_info.py
         data/state/system/<เครื่อง>.json                     snapshot ล่าสุด
         data/state/system/history/<เครื่อง>/<เวลา>.json      ประวัติ (เก็บตาม system_history_retention)
    2) เวลาดึง log ล่าสุด: data/state/checkpoints.json (ผู้เขียน: puller/checkpoint.py)

รูปแบบข้อมูลที่ฟังก์ชันในไฟล์นี้คืน ("แบน" แล้ว เพื่อให้ api ใช้ง่าย)
    {
      "load": 0.52, "load_1m": .., "load_5m": .., "load_15m": ..,
      "memory_used": 11.4, "memory_available": 4.6,      # GB
      "swap": 25.0, "disk_available": 38.1, "inode": 10.0,
      "net_rx_errors": 0, "net_tx_errors": 0, "uptime": "2d 3h 5m",
      "services": [...], "processes": [...], "system_events": [...],
      "system_timestamp": "2026-10-02T01:00:00+00:00"      # เวลาที่เก็บ snapshot นี้
    }
    (ในไฟล์จริงค่าตัวเลขอยู่ใต้คีย์ "resource" และเวลาอยู่ที่ "timestamp"
     ฟังก์ชัน _flatten ด้านล่างย้ายขึ้นมาให้ -- เดิมไม่ได้ย้าย ทำให้ค่าเหล่านี้ว่างตลอด)

เชื่อมกับอะไร
    - ใช้โดย metrics_service.py (หน้าปัจจุบัน) และ logs_service.py (รายละเอียด log)
    - ชื่อไฟล์ history ต้องตรงกับ HISTORY_TS_FORMAT ใน puller/system_info.py
"""

import json
import os
from datetime import datetime, timezone

import yaml

from settings import CONFIG_FILE, STATE_DIR
from timeutil import parse_iso, to_unix

SYSTEM_DIR = os.path.join(STATE_DIR, "system")
CHECKPOINT_FILE = os.path.join(STATE_DIR, "checkpoints.json")

# ต้องเหมือน HISTORY_TS_FORMAT ใน start_push_log/puller/system_info.py
HISTORY_TS_FORMAT = "%Y%m%dT%H%M%SZ"

# ถ้าไม่ได้อ่าน scheduler.interval จาก puller.yaml (อ่านไม่ได้) ให้ยอมห่างได้เท่านี้
DEFAULT_MAX_GAP_SECONDS = 2 * 3600


# ---------------------------------------------------------------------------
# ตัวช่วย
# ---------------------------------------------------------------------------

def _load_json(path):
    """อ่านไฟล์ JSON ไม่มี/พัง -> {} (ไม่ให้หน้าเว็บล่ม)"""
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"[SYSTEM STATE ERROR] file={path} error={e}")
        return {}


def _flatten(raw):
    """ย้ายค่าใน raw["resource"] ขึ้นระดับบน และเปลี่ยนชื่อ timestamp -> system_timestamp"""
    if not raw:
        return {}

    flat = dict(raw.get("resource") or {})

    flat["services"] = raw.get("services", [])
    flat["processes"] = raw.get("processes", [])
    flat["cpu_cores"] = raw.get("cpu_cores") or {}
    flat["system_events"] = raw.get("system_events", [])
    flat["system_timestamp"] = raw.get("timestamp")

    return flat


def _interval_seconds():
    """scheduler.interval (วินาที) จาก puller.yaml ไม่ได้ -> None"""
    try:
        with open(CONFIG_FILE, "r") as f:
            value = (yaml.safe_load(f) or {}).get("scheduler", {}).get("interval", "1h")

        value = str(value).strip().lower()
        units = {"s": 1, "m": 60, "h": 3600, "d": 86400}

        if value and value[-1] in units:
            return int(value[:-1]) * units[value[-1]]

        return int(value)

    except Exception:
        return None


def _max_gap_seconds():
    """snapshot ที่ห่างจากเวลา log เกินค่านี้ ถือว่า "ไม่เกี่ยวกับ log นั้น"

    = 2 เท่าของรอบดึง (อย่างน้อย 30 นาที) เช่น ดึงทุก 1 ชม. -> ยอมห่างได้ 2 ชม.
    """
    interval = _interval_seconds()

    if interval is None:
        return DEFAULT_MAX_GAP_SECONDS

    return max(2 * interval, 1800)


# ---------------------------------------------------------------------------
# สถานะเครื่อง
# ---------------------------------------------------------------------------

def load_system_snapshot(web):
    """snapshot ล่าสุดของเครื่อง (ไม่มี -> {})"""
    if not web:
        return {}

    return _flatten(_load_json(os.path.join(SYSTEM_DIR, f"{web}.json")))


def load_system_snapshot_at(web, timestamp=None):
    """snapshot ที่ "ใกล้เวลา timestamp ที่สุด" ของเครื่อง

    ใช้ตอนคลิกดูรายละเอียด log เก่า: อยากเห็น service/process ณ เวลานั้น
    ไม่ใช่ตอนนี้

    - ไม่ใส่ timestamp หรือแปลงเวลาไม่ได้  -> snapshot ล่าสุด
    - หาจาก history/<เครื่อง>/ โดยดูเวลาจาก "ชื่อไฟล์" (ไม่ต้องเปิดทุกไฟล์ -> เร็ว)
      รวมกับ snapshot ล่าสุดเป็นตัวเลือกด้วย
    - ถ้าตัวใกล้ที่สุดยังห่างเกิน _max_gap_seconds() -> คืน {} (ดีกว่าโชว์ข้อมูลผิดเวลา)
    """
    if not web:
        return {}

    if not timestamp:
        return load_system_snapshot(web)

    target = to_unix(timestamp)

    if target is None:
        return load_system_snapshot(web)

    candidates = []    # (เวลา unix, path)

    history_dir = os.path.join(SYSTEM_DIR, "history", web)

    if os.path.isdir(history_dir):
        for name in os.listdir(history_dir):
            if not name.endswith(".json"):
                continue

            try:
                taken = (
                    datetime.strptime(name[: -len(".json")], HISTORY_TS_FORMAT)
                    .replace(tzinfo=timezone.utc)
                    .timestamp()
                )
            except ValueError:
                continue

            candidates.append((taken, os.path.join(history_dir, name)))

    latest_path = os.path.join(SYSTEM_DIR, f"{web}.json")
    latest = _load_json(latest_path)
    latest_dt = parse_iso(latest.get("timestamp")) if latest else None

    if latest_dt:
        candidates.append((latest_dt.timestamp(), latest_path))

    if not candidates:
        return {}

    taken, path = min(candidates, key=lambda c: abs(c[0] - target))

    if abs(taken - target) > _max_gap_seconds():
        return {}

    return _flatten(_load_json(path))


# ---------------------------------------------------------------------------
# เวลาดึง log ล่าสุด
# ---------------------------------------------------------------------------

def last_pull_time(web):
    """เวลา (ISO) ที่ puller เขียน log ของเครื่องนี้ลง buffer ล่าสุด ไม่มี -> None

    ดูจาก checkpoints.json: ค่า updated_at ล่าสุดของทุกไฟล์ log ของเครื่องนั้น
    (โชว์เป็น "Last pull" บน dashboard)
    """
    try:
        state = _load_json(CHECKPOINT_FILE)

        times = [
            item["updated_at"]
            for item in state.values()
            if item.get("target") == web and item.get("updated_at")
        ]

        return max(times) if times else None

    except Exception:
        return None
