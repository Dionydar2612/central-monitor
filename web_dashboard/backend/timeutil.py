"""
timeutil.py - แปลงข้อความเวลา (ISO 8601) เป็น unix time
=======================================================

ใช้โดย metrics_service.py (ถาม Prometheus ณ เวลาของ log) และ system_state.py
(หา snapshot ที่ใกล้เวลา log ที่สุด)

เวลาที่มาจาก OpenSearch มีหน้าตา เช่น "2026-10-02T01:23:45.123456789Z"
(ลงท้าย Z และอาจมีทศนิยมเกิน 6 หลัก) จึงต้องจัดรูปก่อน fromisoformat
"""

import re
from datetime import datetime, timezone


def parse_iso(value):
    """แปลงข้อความเวลาเป็น datetime (มี timezone เสมอ) ไม่ได้ -> คืน None

    - "Z" ถือเป็น UTC
    - ไม่มี timezone -> ถือเป็น UTC
    - ทศนิยมวินาทีเกิน 6 หลัก (nanosecond) -> ตัดเหลือ 6
    """
    if not value:
        return None

    text = str(value).strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def to_unix(value):
    """ข้อความเวลา -> unix seconds (float) ไม่ได้ -> None"""
    dt = parse_iso(value)
    return dt.timestamp() if dt else None
