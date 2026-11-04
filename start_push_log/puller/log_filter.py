"""
log_filter.py - แยก log เป็น "รายการ" แล้วคัดตามระดับ (ERROR / WARN / INFO)
==========================================================================

ทำไมต้องมี
    ระบบนี้ "ไม่เก็บ log ระดับ INFO" (ปริมาณเยอะแต่แทบไม่ได้ใช้)
    puller จะตัด INFO ทิ้งก่อนเขียนลง data/buffer -> INFO ไม่ถูกส่งเข้า OpenSearch เลย
    ถ้าอยากดู INFO ให้กดปุ่ม "Load INFO log" บนหน้าเว็บ ระบบจะดึงสดจากเครื่องปลายทาง
    เฉพาะตอนที่สั่ง (ondemand.py) แสดงแล้วจบ ไม่เก็บไว้ที่ไหน

    ถ้าต้องการกลับไปเก็บ INFO เหมือนเดิม: puller.yaml -> storage.store_info_logs: true

"รายการ" (record) คืออะไร
    บรรทัดที่ขึ้นต้นด้วยเวลา ISO (2026-10-05T09:12:58...) หรือแบบ syslog (Oct  5 09:12:58) = เริ่มรายการใหม่
    บรรทัดที่ไม่ขึ้นต้นด้วยเวลา และรายการก่อนหน้าขึ้นต้นด้วยเวลา = บรรทัดต่อ (เช่น stack trace)
    ไฟล์ที่ไม่มีเวลาต้นบรรทัดเลย = 1 บรรทัด 1 รายการ
    -> stack trace ทั้งก้อนถูกตัดสินระดับพร้อมกัน ไม่ถูกตัดทิ้งครึ่ง ๆ กลาง ๆ

ตัวจัดระดับ = log_levels.detect_log_level (ไฟล์เดียวกับฝั่งหน้าเว็บ ผลจึงตรงกัน)
"""

import re

from log_levels import detect_log_level

_TS_START = re.compile(
    rb"^(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"          # 2026-10-05T09:12:58
    rb"|[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2})"            # Oct  5 09:12:58
)


def store_info_enabled(config):
    """storage.store_info_logs ใน puller.yaml (ปริยาย false = ไม่เก็บ INFO)"""
    return bool((config.get("storage") or {}).get("store_info_logs", False))


def split_records(data):
    """bytes ของ log (จบด้วย \\n) -> list ของ bytes ทีละรายการ (คง \\n ไว้)"""
    records = []
    current = None
    current_has_ts = False

    for line in data.splitlines(keepends=True):
        has_ts = bool(_TS_START.match(line))

        if current is not None and current_has_ts and not has_ts:
            current += line
            continue

        if current is not None:
            records.append(current)

        current = line
        current_has_ts = has_ts

    if current is not None:
        records.append(current)

    return records


def record_level(record):
    return detect_log_level(record.decode("utf-8", errors="replace"))


def drop_info(data):
    """ตัดรายการระดับ INFO ทิ้ง  คืน (bytes ที่เหลือ, จำนวนที่เก็บ, จำนวนที่ตัดทิ้ง)"""
    kept = []
    dropped = 0

    for record in split_records(data):
        if record_level(record) == "INFO":
            dropped += 1
        else:
            kept.append(record)

    return b"".join(kept), len(kept), dropped


def only_info(data):
    """เฉพาะรายการระดับ INFO (ใช้ตอนกดปุ่ม Load INFO log) -> list ของข้อความ"""
    out = []

    for record in split_records(data):
        if record_level(record) == "INFO":
            out.append(record.decode("utf-8", errors="replace").rstrip("\n"))

    return out
