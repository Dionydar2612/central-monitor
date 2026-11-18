"""
ui_order_service.py - ลำดับเครื่อง / กลุ่มที่ผู้ใช้ลากจัดในหน้า Dashboard (มุมมอง ตาราง / ไทล์ / การ์ด / สไลด์ทีละกลุ่ม)
ใช้ร่วมกันทุกคน เก็บที่ data/state/ui_order.json  {"machines": [ชื่อ...], "groups": [path...]}
    GET /api/ui-order   (ทุก role)      PUT /api/ui-order   (operator ขึ้นไป)
ไม่แตะ puller.yaml · ชื่อที่ไม่มีแล้วถูกข้ามตอนแสดงเอง
"""

import json
import os
import threading

from settings import STATE_DIR

ORDER_FILE = os.path.join(STATE_DIR, "ui_order.json")
MAX_ITEMS = 5000
_LOCK = threading.Lock()


def _clean(v):
    out = []
    for x in v if isinstance(v, list) else []:
        x = str(x or "").strip()[:300]
        if x and x not in out:
            out.append(x)
    return out[:MAX_ITEMS]


def get_order():
    try:
        with open(ORDER_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    return {"machines": _clean(d.get("machines")), "groups": _clean(d.get("groups")), "items": _clean(d.get("items"))}


def set_order(payload):
    payload = payload if isinstance(payload, dict) else {}
    d = {"machines": _clean(payload.get("machines")), "groups": _clean(payload.get("groups")), "items": _clean(payload.get("items"))}
    os.makedirs(STATE_DIR, exist_ok=True)
    with _LOCK:
        tmp = ORDER_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, ORDER_FILE)
    return d
