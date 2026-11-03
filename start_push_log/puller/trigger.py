"""
trigger.py - รับคำสั่ง "ดึงเครื่องนี้เดี๋ยวนี้" จากหน้า Settings
==============================================================

หน้าที่
    หน้า Settings (web_dashboard/backend/targets_service.py) เขียนรายชื่อเครื่องที่เพิ่ง
    เพิ่ม/แก้ลงไฟล์ data/state/pull-now.json  (เช่น ["web-03"])
    scheduler.py เรียก take_pull_request() ระหว่างรอรอบถัดไป ถ้ามีไฟล์ -> ดึงเฉพาะเครื่องนั้นทันที
    (ไม่ต้องรอ scheduler.interval และไม่ต้อง restart)
"""

import json
import os

from config import STATE_DIR

TRIGGER_FILE = os.path.join(STATE_DIR, "pull-now.json")


def take_pull_request():
    """อ่านแล้วลบไฟล์คำสั่ง  คืน list ชื่อเครื่อง หรือ None ถ้าไม่มีคำสั่ง"""
    try:
        with open(TRIGGER_FILE) as f:
            names = json.load(f)
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        names = []

    try:
        os.remove(TRIGGER_FILE)
    except OSError:
        pass

    names = [n for n in names if isinstance(n, str)] if isinstance(names, list) else []
    return names or None
