"""
notify_service.py - ตั้งค่าการแจ้งเตือน Microsoft Teams (หน้า Settings > Notifications)
=====================================================================================

เก็บใน config/puller.yaml -> notify.teams  (ตัวส่งจริง: start_push_log/puller/notify.py ใน central-puller)
    enabled, webhook_url, levels [down, error, warning, recovered, checks]
    summary_times ["08:30", "17:00"]   ส่งการ์ดสรุปสถานะทุกเครื่องทุกวันตามเวลานี้ (เวลาของเครื่อง central)

webhook URL เป็นความลับ (ใครมี URL ก็โพสต์เข้าช่องได้)
    GET  ไม่ส่ง URL เต็มกลับไป แสดงแค่ host + 6 ตัวท้าย
    PUT  webhook_url ว่าง/ไม่ส่ง = ใช้ค่าเดิม · "-" = ลบ
    Activity log ไม่เก็บ URL
"""

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

import os

import settings
import card_templates
import targets_service

LEVELS = ("down", "error", "warning", "recovered", "checks", "software")
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
            "summary_times": [str(x) for x in t.get("summary_times") or []],
            "all_levels": list(LEVELS),
            # ป๊อปอัปบนหน้าเว็บ (popup.js): เรื่องเดียวกับ Teams ("ส่งเมื่อ") · ปริยายเปิด · ไม่ต้องมี webhook
            "popup": t.get("popup", True) is not False}


def popup_settings():
    """ค่าที่ popup.js ใช้ (ทุก role อ่านได้ ไม่มีความลับ)"""
    y = targets_service._yaml()
    t = _teams(targets_service._load_doc(y))
    levels = t.get("levels") if isinstance(t.get("levels"), list) else list(LEVELS)
    return {"enabled": t.get("popup", True) is not False, "levels": [str(x) for x in levels]}


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
    if "popup" in payload:
        vals["popup"] = bool(payload["popup"])
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
    if "summary_times" in payload:
        raw = payload["summary_times"]
        if isinstance(raw, str):
            raw = re.split(r"[,\s]+", raw)
        times = []
        for x in raw or []:
            x = str(x).strip()
            if not x:
                continue
            m = re.match(r"^(\d{1,2})[:.](\d{2})$", x)
            if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
                errors["summary_times"] = f"เวลาไม่ถูกต้อง: {x} (ใช้รูปแบบ 08:30)"
                continue
            times.append(f"{int(m.group(1)):02d}:{m.group(2)}")
        vals["summary_times"] = sorted(set(times))[:12]
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
        for old in ("kinds", "machines", "groups", "template_alert", "template_recovered"):   # ย้ายไป Custom alert rules
            t.pop(old, None)
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


def get_cards():
    """รูปแบบการ์ด Teams ทุกหัวข้อ (ที่ตั้งไว้ + ค่าปริยาย) สำหรับหน้า Settings"""
    t = _teams(targets_service._load_doc(targets_service._yaml()))
    cards = t.get("cards") if isinstance(t.get("cards"), dict) else {}
    return {"categories": card_templates.CATEGORIES, "fields": card_templates.FIELDS, "vars": list(card_templates.VARS),
            "defaults": card_templates.DEFAULTS, "samples": card_templates.SAMPLE, "mute_options": card_templates.MUTE,
            "cards": {c: card_templates.template(c, cards) for c in card_templates.CATEGORIES},
            "custom": [c for c in card_templates.CATEGORIES if c in cards]}


def save_cards(payload):
    """บันทึกรูปแบบการ์ด {cards: {หัวข้อ: {title, fields, labels, note}}, reset: [หัวข้อ]}"""
    from ruamel.yaml.comments import CommentedMap
    payload = payload or {}
    vals, errors = card_templates.clean(payload.get("cards") or {})
    reset = [c for c in payload.get("reset") or [] if c in card_templates.CATEGORIES]
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
        cards = t.get("cards") if isinstance(t.get("cards"), dict) else CommentedMap()
        for c in reset:
            cards.pop(c, None)
        for c, v in vals.items():
            cards[c] = v
        if cards:
            t["cards"] = cards
        else:
            t.pop("cards", None)
        targets_service._write_doc(y, doc)
    return True, get_cards()


def send_card_test(cat, card=None):
    """ส่งการ์ดตัวอย่างของหัวข้อไป Teams (ใช้รูปแบบที่ส่งมา ถ้าไม่ส่งใช้ที่บันทึกไว้)"""
    if cat not in card_templates.CATEGORIES:
        return False, "ไม่รู้จักหัวข้อนี้"
    t = _teams(targets_service._load_doc(targets_service._yaml()))
    url = str(t.get("webhook_url") or "")
    if not url:
        return False, "ยังไม่ได้ใส่ webhook URL"
    cards = t.get("cards") if isinstance(t.get("cards"), dict) else {}
    if card is not None:
        vals, errors = card_templates.clean({cat: card})
        if errors:
            return False, "; ".join(errors.values())
        cards = vals
    s = card_templates.SAMPLE[cat]
    ctx = dict(s, device=" ".join([s["machine"]] + ([f"[{s['host']}]"] if s.get("host") else [])),
               time=datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S"), duration="5 นาที")
    level = {"DOWN": "down", "ERROR": "error", "WARNING": "warning"}.get(s.get("level"), "software")
    return post(url, card_templates.card(cat, level, ctx, cards, f"[ทดสอบ] {card_templates.CATEGORIES[cat]}"))


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
    payload = fact_card("Central Monitor Notification: ทดสอบการแจ้งเตือน", "good", [
        ("Device", "Central Monitor"), ("Sensor", "Microsoft Teams"), ("Status", "OK – เชื่อมต่อ Teams สำเร็จ"),
        ("Time", datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S"))])
    return post(url, payload)


COLOR = {"attention": "Attention", "warning": "Warning", "good": "Good"}


def fact_card(title, style, facts, summary=None):
    """การ์ดแบบ PRTG เหมือนที่ puller ส่ง (notify.fact_card): หัวเรื่องสีตามระดับ + หัวข้อ : ค่า"""
    rows = [{"title": f"{t}:", "value": str(v)} for t, v in facts if v not in (None, "")]
    body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True,
             "color": COLOR.get(style, "Default")},
]
    for r in rows:                                   # แบบ PRTG: **หัวข้อ:** ค่า บรรทัดเดียว
        body.append(card_templates.fact_line(r["title"].rstrip(":"), r["value"]))
    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
            "msteams": {"width": "Full"}, "fallbackText": summary or title, "body": body}
    return {"type": "message", "summary": summary or title, "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def main_url():
    y = targets_service._yaml()
    return str(_teams(targets_service._load_doc(y)).get("webhook_url") or "")


def post(url, payload):
    """POST ไป Teams -> (ok, ข้อความ)"""
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


SUMMARY_REQUEST_FILE = os.path.join(settings.STATE_DIR, "notify_summary.request")


def request_summary():
    """ขอให้ puller ส่งการ์ดสรุปตอนนี้ (puller เห็นไฟล์นี้ในรอบตรวจถัดไป ~30 วินาที)"""
    if not get_settings()["url_set"]:
        return False, "ยังไม่ได้ใส่ webhook URL"
    with open(SUMMARY_REQUEST_FILE, "w") as f:
        f.write("1")
    return True, "จะส่งการ์ดสรุปภายใน ~30 วินาที"
