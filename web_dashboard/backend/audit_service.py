"""
audit_service.py - Activity log (ใครทำอะไรในหน้าเว็บ) + อ่าน System log
======================================================================

Activity log   data/state/audit.log   (เขียนที่นี่)
    ทุกการเปลี่ยนแปลงผ่านหน้าเว็บ: ล็อกอิน (สำเร็จ/ไม่สำเร็จ), ออกจากระบบ, เพิ่ม/แก้/ลบเครื่อง,
    Pause/Resume, Deploy key, เก็บ host key, เลือก service, เพิ่ม/แก้/ลบผู้ใช้, web checks
    เก็บ: เวลา, ผู้ใช้, role, IP, การกระทำ, เป้าหมาย, สำเร็จไหม, รายละเอียด (ไม่เก็บรหัสผ่านเด็ดขาด)
    ดูได้เฉพาะ admin

System log     data/state/events.log  (เขียนโดย puller: sensor_monitor.py / events.py)
    sensor เปลี่ยนสถานะ ฯลฯ  ดูได้ทุก role

ทั้งสองไฟล์เป็น JSON 1 บรรทัดต่อ 1 รายการ ยาวเกิน MAX_BYTES ตัดเหลือ KEEP_LINES บรรทัดล่าสุด
"""

import fcntl
import json
import os
import time

from settings import STATE_DIR

AUDIT_FILE = os.path.join(STATE_DIR, "audit.log")
EVENTS_FILE = os.path.join(STATE_DIR, "events.log")
MAX_BYTES = 4 * 1024 * 1024
KEEP_LINES = 10000
FILES = {"activity": AUDIT_FILE, "system": EVENTS_FILE}


def append_audit(entry):
    os.makedirs(STATE_DIR, exist_ok=True)
    entry = {"time": time.time(), **entry}
    try:
        with open(AUDIT_FILE, "a+", encoding="utf-8") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                if f.tell() > MAX_BYTES:
                    f.seek(0)
                    lines = f.readlines()[-KEEP_LINES:]
                    f.seek(0)
                    f.truncate()
                    f.writelines(lines)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except OSError as e:
        print(f"[AUDIT ERROR] {e}")


def read_entries(kind, limit=300, since=None, query=None, level=None):
    """รายการล่าสุดก่อน  since = unix (เอาเฉพาะใหม่กว่า)  query = ข้อความที่ต้องมี  level = กรอง level/ok"""
    path = FILES[kind]
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    q = (query or "").lower().strip()
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if since is not None and e.get("time", 0) <= since:
            break
        if q and q not in line.lower():
            continue
        if level and e.get("level") != level:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


def count_since(kind, since):
    """จำนวนรายการที่ใหม่กว่า since (นับจากท้ายไฟล์ หยุดเมื่อเจอรายการเก่ากว่า)"""
    return len(read_entries(kind, limit=10000, since=since or 0))
