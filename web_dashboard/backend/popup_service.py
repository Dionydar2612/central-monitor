"""
popup_service.py - ตั้งค่าป๊อปอัปแจ้งเตือนบนหน้าเว็บ (popup.js)
================================================================

เก็บที่   data/state/popup-settings.json   {"enabled": true, "levels": ["down", "error", "warning", "recovered"]}
อ่าน     GET /api/popup-settings  (ทุก role - popup.js ทุกหน้าใช้)
แก้      PUT /api/popup-settings  (admin - หน้า Settings > Popup notifications)

popup.js ดึง System log (GET /api/events?kind=system&since=...) ทุก 15 วินาที
แล้วเด้งเฉพาะเหตุการณ์ sensor ที่ level อยู่ใน "levels"
(sensor_monitor.py เขียน level = down / error / warning / unknown / recovered ตอนสถานะเปลี่ยน)
"""

import json
import os

import settings

POPUP_FILE = os.path.join(settings.STATE_DIR, "popup-settings.json")
LEVELS = ("down", "error", "warning", "recovered")
DEFAULTS = {"enabled": True, "levels": list(LEVELS)}


def get_settings():
    try:
        with open(POPUP_FILE, "r", encoding="utf-8") as f:
            d = json.load(f) or {}
    except (OSError, ValueError):
        d = {}
    levels = d.get("levels")
    return {
        "enabled": bool(d.get("enabled", True)),
        "levels": [lv for lv in levels if lv in LEVELS] if isinstance(levels, list) else list(LEVELS),
    }


def update_settings(body):
    """คืน (ok, ผลลัพธ์ หรือ error)"""
    if not isinstance(body, dict):
        return False, {"error": "invalid body"}
    cur = get_settings()
    if "enabled" in body:
        cur["enabled"] = bool(body["enabled"])
    if "levels" in body:
        lv = body["levels"]
        if not isinstance(lv, list) or any(x not in LEVELS for x in lv):
            return False, {"error": "validation", "fields": {"levels": "เลือกได้: " + ", ".join(LEVELS)}}
        cur["levels"] = [x for x in LEVELS if x in lv]
    os.makedirs(os.path.dirname(POPUP_FILE), exist_ok=True)
    tmp = POPUP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cur, f, indent=2)
    os.replace(tmp, POPUP_FILE)
    return True, cur
