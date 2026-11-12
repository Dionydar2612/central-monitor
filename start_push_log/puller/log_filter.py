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

ยุบรายการซ้ำ (collapse_repeats)  ** ค่าเริ่มต้นปิด **
    error เดียวพ่นพันบรรทัดในรอบดึงเดียว -> เก็บต่อ fingerprint (fingerprint.py) ไม่เกิน keep รายการ
    ที่เหลือยุบเป็นบรรทัดสรุป 1 บรรทัดต่อชนิด ต่อท้ายข้อมูลของรอบนั้น
        "<เวลา> [LEVEL] (collapsed) ซ้ำอีก N ครั้ง: <บรรทัดแรก>"
        [LEVEL] หลังเวลา = รูปแบบที่ log_levels ตัดสินแม่นที่สุด -> ระดับตรงกับต้นฉบับ
        เวลา = เวลาของรายการสุดท้ายที่ถูกยุบ (ถ้าต้นฉบับไม่มีเวลาแบบ ISO ใช้เวลาตอนดึง)
    ข้อดี: _scan ของหน้าเว็บมีเพดานจำนวนเอกสาร error ที่ซ้ำเยอะจะไม่ดัน error ชนิดอื่นออกไป + ประหยัด OpenSearch
    ข้อควรระวัง: บรรทัดดิบส่วนเกินจะไม่ถูกเก็บในระบบกลาง (ยังอยู่บนเครื่องปลายทาง)
    เปิดที่ puller.yaml:  storage.collapse_repeats: {enabled: true, keep: 20}
"""

import re

from datetime import datetime

from fingerprint import fingerprint
from log_levels import detect_log_level

_TS_START = re.compile(
    rb"^(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"          # 2026-10-05T09:12:58
    rb"|[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2})"            # Oct  5 09:12:58
)


def store_info_enabled(config):
    """storage.store_info_logs ใน puller.yaml (ปริยาย false = ไม่เก็บ INFO)"""
    return bool((config.get("storage") or {}).get("store_info_logs", False))


def collapse_config(config):
    """storage.collapse_repeats ใน puller.yaml -> (เปิดไหม, keep)  ปริยาย (False, 20)"""
    c = (config.get("storage") or {}).get("collapse_repeats") or {}
    if not isinstance(c, dict):
        return False, 20
    try:
        keep = max(1, min(int(c.get("keep", 20)), 100000))
    except (TypeError, ValueError):
        keep = 20
    return c.get("enabled") is True, keep


_ISO_LEAD = re.compile(rb"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")
_LEAD_TS = re.compile(rb"^\s*\[?\s*(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"
                      rb"|[A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2})\s*\]?\s*")


def collapse_repeats(data, keep=20):
    """เก็บต่อ fingerprint ไม่เกิน keep รายการ ที่เหลือยุบเป็นบรรทัดสรุป
    คืน (bytes ใหม่, จำนวนรายการที่ถูกยุบ, {fp: จำนวนที่ยุบ})"""
    seen = {}
    kept = []
    extra = {}                      # fp -> [จำนวน, รายการสุดท้ายที่ถูกยุบ]
    for record in split_records(data):
        fp = fingerprint(record.decode("utf-8", errors="replace"))
        n = seen.get(fp, 0) + 1
        seen[fp] = n
        if not fp or n <= keep:
            kept.append(record)
            continue
        e = extra.setdefault(fp, [0, record])
        e[0] += 1
        e[1] = record
    for fp, (count, last) in extra.items():
        level = record_level(last)
        m = _ISO_LEAD.match(last)
        ts = m.group(1) if m else datetime.now().astimezone().isoformat(timespec="seconds").encode()
        first = _LEAD_TS.sub(b"", last.split(b"\n", 1)[0], count=1).strip()[:500]
        kept.append(ts.replace(b" ", b"T", 1) + b" [" + level.encode() + b"] (collapsed) "
                    + f"ซ้ำอีก {count} ครั้ง: ".encode("utf-8") + first + b"\n")
    return b"".join(kept), sum(c for c, _ in extra.values()), {fp: c for fp, (c, _) in extra.items()}


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
