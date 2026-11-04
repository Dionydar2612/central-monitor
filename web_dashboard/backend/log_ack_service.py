"""
log_ack_service.py - Acknowledge log (รับทราบแล้ว) เพื่อไม่ให้ตาราง Logs รก
==========================================================================

log ที่ acknowledge แล้ว
    - ถูกซ่อนจากตาราง Logs, ไม่นับในตัวเลข All / Error / Warn, Problem logs, Errors 15m และจุดบนกราฟ
    - ยังอยู่ใน OpenSearch ตามเดิม (ลบเองตามอายุ OPENSEARCH_LOG_RETENTION)
    - ดูได้อีกโดยติ๊ก "Show acknowledged" (มีป้าย ACK + ใครรับทราบ + โน้ต) และยกเลิกได้
เก็บที่ data/state/log_acks.json  {OpenSearch _id: {by, at, note, web}}
    เก็บไม่เกิน MAX_DAYS วัน / MAX_ITEMS รายการ (log หมดอายุใน OpenSearch แล้วก็ไม่ต้องจำ)
ใช้โดย: logs_service.py (กรอง), api.py (/api/logs/ack, /api/logs/unack)  สิทธิ์ operator ขึ้นไป
"""

import contextlib
import fcntl
import json
import os
import time

from settings import STATE_DIR

ACK_FILE = os.path.join(STATE_DIR, "log_acks.json")
LOCK_FILE = os.path.join(STATE_DIR, ".log_acks.lock")
MAX_DAYS = 40
MAX_ITEMS = 50000
MAX_PER_REQUEST = 1000
MAX_NOTE = 500

_CACHE = {"mtime": None, "data": {}}


@contextlib.contextmanager
def _locked():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def load():
    """{id: ack}  อ่านซ้ำเฉพาะเมื่อไฟล์เปลี่ยน (หน้าเว็บเรียกบ่อย)"""
    try:
        mtime = os.path.getmtime(ACK_FILE)
    except OSError:
        return {}
    if _CACHE["mtime"] != mtime:
        try:
            with open(ACK_FILE) as f:
                data = json.load(f)
            _CACHE.update(mtime=mtime, data=data if isinstance(data, dict) else {})
        except (OSError, ValueError):
            return {}
    return _CACHE["data"]


def _save(data):
    tmp = ACK_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, ACK_FILE)


def acknowledge(ids, user, note="", web=None):
    ids = [str(i) for i in (ids or []) if str(i).strip()][:MAX_PER_REQUEST]
    if not ids:
        raise ValueError("ไม่มีรายการที่เลือก")
    now = time.time()
    entry = {"by": user, "at": now, "note": str(note or "").strip()[:MAX_NOTE], "web": web}
    with _locked():
        data = dict(load())
        cutoff = now - MAX_DAYS * 86400
        data = {k: v for k, v in data.items() if v.get("at", 0) >= cutoff}
        for i in ids:
            data[i] = entry
        if len(data) > MAX_ITEMS:
            data = dict(sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-MAX_ITEMS:])
        _save(data)
    return len(ids)


def unacknowledge(ids):
    ids = {str(i) for i in (ids or [])}
    with _locked():
        data = dict(load())
        removed = [i for i in ids if i in data]
        for i in removed:
            del data[i]
        _save(data)
    return len(removed)
