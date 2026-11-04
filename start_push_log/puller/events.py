"""
events.py - บันทึก "System log" ของ central-monitor (ไม่ใช่ log ของเครื่องปลายทาง)
=================================================================================

เก็บเหตุการณ์ของระบบ monitor เอง เป็น JSON 1 บรรทัดต่อ 1 เหตุการณ์ที่ data/state/events.log
    - sensor เปลี่ยนสถานะ (OK -> Warning -> Error / Down / Paused)  <- sensor_monitor.py
    - puller เข้าเครื่องไม่ได้ / กลับมาเข้าได้                         <- sensor "Log pull"
หน้าเว็บอ่านไปแสดงในหน้า Status & Logs -> แท็บ System log (และนับเป็น New Log Entries)
ไฟล์ยาวเกิน MAX_BYTES จะถูกตัดเหลือ KEEP_LINES บรรทัดล่าสุด
"""

import fcntl
import json
import os
import time

from config import STATE_DIR

EVENTS_FILE = os.path.join(STATE_DIR, "events.log")
MAX_BYTES = 4 * 1024 * 1024
KEEP_LINES = 10000


def append_events(items):
    """items = [{level, message, ...}] เติม time ให้ แล้วต่อท้ายไฟล์ (ล็อกไฟล์กันเขียนชน)"""
    if not items:
        return
    os.makedirs(STATE_DIR, exist_ok=True)
    now = time.time()
    with open(EVENTS_FILE, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            for item in items:
                f.write(json.dumps({"time": now, **item}, ensure_ascii=False) + "\n")
            f.flush()
            if f.tell() > MAX_BYTES:
                f.seek(0)
                lines = f.readlines()[-KEEP_LINES:]
                f.seek(0)
                f.truncate()
                f.writelines(lines)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
