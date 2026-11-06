"""
notify_service.py - ตั้งค่าการแจ้งเตือน Microsoft Teams (หน้า Settings > Notifications)
=====================================================================================

เก็บใน config/puller.yaml -> notify.teams  (ตัวส่งจริง: start_push_log/puller/notify.py ใน central-puller)
    enabled, webhook_url, levels [down, error, warning, recovered, checks]

webhook URL เป็นความลับ (ใครมี URL ก็โพสต์เข้าช่องได้)
    GET  ไม่ส่ง URL เต็มกลับไป แสดงแค่ host + 6 ตัวท้าย
    PUT  webhook_url ว่าง/ไม่ส่ง = ใช้ค่าเดิม · "-" = ลบ
    Activity log ไม่เก็บ URL
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import targets_service

LEVELS = ("down", "error", "warning", "recovered", "checks")


def _teams(doc):
    return dict(((doc.get("notify") or {}).get("teams")) or {})


def _hint(url):
    if not url:
        return ""
    host = urllib.parse.urlsplit(url).hostname or ""
    return f"https://{host}/…{url[-6:]}"


def get_settings():
    y = targets_service._yaml()
    t = _teams(targets_service._load_doc(y))
    url = str(t.get("webhook_url") or "")
    levels = t.get("levels") if isinstance(t.get("levels"), list) else list(LEVELS)
    return {"enabled": t.get("enabled", True) is not False and bool(url), "url_set": bool(url), "url_hint": _hint(url),
            "levels": [str(x) for x in levels],
            "all_levels": list(LEVELS)}


def _clean_url(value, errors, field="webhook_url"):
    v = str(value or "").strip()
    p = urllib.parse.urlsplit(v)
    if p.scheme != "https" or not p.hostname or len(v) > 2000 or any(c in v for c in " \"'<>"):
        errors[field] = "ต้องเป็น https://... ที่คัดลอกมาจาก Teams (Workflows / Incoming Webhook)"
        return None
    return v


def update_settings(payload):
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    payload = payload or {}
    errors, vals = {}, {}
    if "enabled" in payload:
        vals["enabled"] = bool(payload["enabled"])
    url = payload.get("webhook_url")
    if url not in (None, ""):
        if str(url).strip() == "-":
            vals["webhook_url"] = None
        else:
            u = _clean_url(url, errors)
            if u:
                vals["webhook_url"] = u
    if "levels" in payload:
        lv = payload["levels"] if isinstance(payload["levels"], list) else []
        bad = [x for x in lv if x not in LEVELS]
        if bad:
            errors["levels"] = f"ไม่รู้จัก: {', '.join(map(str, bad))}"
        vals["levels"] = [x for x in LEVELS if x in lv]
    if errors:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}

    y = targets_service._yaml()
    with targets_service._locked():
        doc = targets_service._load_doc(y)
        if not isinstance(doc.get("notify"), dict):
            doc["notify"] = CommentedMap()
        if not isinstance(doc["notify"].get("teams"), dict):
            doc["notify"]["teams"] = CommentedMap()
        t = doc["notify"]["teams"]
        for k, v in vals.items():
            if v is None:
                t.pop(k, None)
            elif isinstance(v, list):
                seq = CommentedSeq(v)
                seq.fa.set_flow_style()
                t[k] = seq
            else:
                t[k] = v
        targets_service._write_doc(y, doc)
    return True, get_settings()


def send_test(webhook_url=None):
    """ส่งการ์ดทดสอบ (ใช้ URL ที่ส่งมา หรือที่บันทึกไว้) -> (ok, ข้อความ)"""
    y = targets_service._yaml()
    t = _teams(targets_service._load_doc(y))
    errors = {}
    url = _clean_url(webhook_url, errors) if webhook_url else str(t.get("webhook_url") or "")
    if errors:
        return False, errors["webhook_url"]
    if not url:
        return False, "ยังไม่ได้ใส่ webhook URL"
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
        "msteams": {"width": "Full"},
        "body": [
            {"type": "Container", "style": "good", "bleed": True, "items": [
                {"type": "TextBlock", "text": "Central Monitor: ทดสอบการแจ้งเตือน", "weight": "Bolder", "size": "Medium", "wrap": True}]},
            {"type": "FactSet", "facts": [{"title": "เวลา", "value": datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")},
                                          {"title": "สถานะ", "value": "เชื่อมต่อ Microsoft Teams สำเร็จ"}]},
            {"type": "TextBlock", "wrap": True, "spacing": "Small",
             "text": "จากนี้ช่องนี้จะได้รับการ์ดเมื่อเครื่อง Down, disk เต็ม, service สำคัญหยุด ฯลฯ ตามระดับที่เลือกไว้"},
        ],
    }
    payload = {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "central-monitor"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status in (200, 201, 202), f"Teams ตอบ HTTP {r.status}"
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read(200).decode("utf-8", "replace")
        except Exception:
            pass
        return False, f"Teams ตอบ HTTP {e.code} {body}".strip()
    except Exception as e:
        return False, f"ติดต่อ Teams ไม่ได้ (central ออกอินเทอร์เน็ตได้ไหม / proxy): {e}"
