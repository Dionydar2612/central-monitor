"""
checkpoint.py - จำว่า "ดึง log ของแต่ละไฟล์ถึงตำแหน่งไหนแล้ว"
=============================================================

ทำไมต้องมี
    puller ดึงเฉพาะส่วนที่ไฟล์ log "โตขึ้น" เท่านั้น (ไม่ดึงซ้ำทั้งไฟล์)
    จึงต้องจำ byte offset ล่าสุดของทุกไฟล์ไว้ ถ้าหายไป puller จะเริ่มดึงใหม่
    ตั้งแต่ต้นไฟล์ (log ซ้ำเข้า OpenSearch)

รูปแบบไฟล์ data/state/checkpoints.json
    {
      "<เครื่อง>::<source>::<path>": {
          "offset": 12345,          # ดึงถึง byte นี้แล้ว
          "last_pull_id": "...",    # id ของก้อนล่าสุด
          "last_file": ".../buffer/....log",
          "updated_at": "2026-10-02T01:23:45+00:00"   # web_dashboard ใช้โชว์ "Last pull"
      }, ...
    }

เชื่อมกับอะไร
    - ถูกใช้โดย log_puller.py (อ่านก่อนดึง, เขียนหลังเขียนไฟล์ buffer สำเร็จ)
    - web_dashboard/backend/api.py อ่านไฟล์นี้เพื่อหาเวลา "Last pull" ของแต่ละเครื่อง
    - ต้องสำรองไว้ (ดู docs/INSTALL-TH.md หัวข้อสำรองข้อมูล)
"""

import json
import os
import threading

from config import CHECKPOINT_FILE, STATE_DIR

# scheduler ดึงหลายเครื่องพร้อมกัน (หลาย thread) -> เขียนไฟล์ทีละ thread
_WRITE_LOCK = threading.Lock()


def load_state():
    """อ่าน checkpoint ทั้งหมด (ไม่มีไฟล์/อ่านไม่ได้ = เริ่มจากว่าง)"""
    os.makedirs(STATE_DIR, exist_ok=True)

    if not os.path.exists(CHECKPOINT_FILE):
        return {}

    try:
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    except Exception:
        print("[WARN] checkpoint file unreadable, starting with empty state")
        return {}


def save_state(state):
    """เขียน checkpoint แบบ atomic

    เขียนลงไฟล์ .tmp แล้ว os.replace ทับ เพื่อไม่ให้ไฟล์เสียครึ่ง ๆ กลาง ๆ
    ถ้า container ดับระหว่างเขียน
    """
    os.makedirs(STATE_DIR, exist_ok=True)

    tmp_file = CHECKPOINT_FILE + ".tmp"

    with _WRITE_LOCK:
        snapshot = dict(state)          # คัดลอกก่อน (thread อื่นอาจกำลังเพิ่ม key)
        with open(tmp_file, "w") as f:
            json.dump(snapshot, f, indent=2, sort_keys=True)

        os.replace(tmp_file, CHECKPOINT_FILE)


def prune_state(state, valid_names):
    """ลบ checkpoint ของเครื่องที่ไม่อยู่ใน puller.yaml แล้ว คืนจำนวนที่ลบ

    ไม่ลบ -> ถ้าเพิ่มเครื่องชื่อเดิมกลับมาทีหลัง puller จะดึงต่อจาก offset เก่า
    (ข้าม log ช่วงที่หายไป หรือสับสนกับไฟล์ใหม่)
    """
    valid = set(valid_names)
    stale = [k for k, v in state.items() if v.get("target") not in valid]

    for key in stale:
        del state[key]

    if stale:
        save_state(state)

    return len(stale)
