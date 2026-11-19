"""
card_templates.py - รูปแบบการ์ด Microsoft Teams ที่ปรับแต่งได้ (Settings > การ์ด Teams)
    ไฟล์นี้มี 2 ชุดที่ต้องเหมือนกันทุกตัวอักษร: start_push_log/puller/ และ web_dashboard/backend/
    (tests ตรวจให้แล้ว)  ค่าที่ตั้งเก็บใน puller.yaml: notify.teams.cards.<หัวข้อ> = {title, fields, labels, note}

การ์ดหน้าตาแบบ PRTG: หัวเรื่องตัวหนาสีตามระดับ แล้วตามด้วย "หัวข้อ:" ตัวหนา และค่าบรรทัดถัดไป
"""
import re

PREFIX = "Central Monitor Notification"

# หัวข้อของการ์ด -> ชื่อที่แสดงในหน้า Settings
CATEGORIES = {
    "alert": "Alert ของเครื่อง (CPU / RAM / Disk / Down / Service ...)",
    "rule": "Custom alert rules",
    "queue": "Netstat · Queue / Log ของโปรแกรม (เขียว / เหลือง / แดง)",
    "netstat": "Netstat · พอร์ต (Connections / Queue / ไม่ LISTEN / เครื่องไม่ตอบ)",
    "web": "Web / SSL check",
    "software": "Software ติดตั้ง / ถอน / อัปเดต",
}

# ช่องข้อมูลที่ใส่ในการ์ดได้ -> หัวข้อปริยาย
FIELDS = {
    "device": "Device", "sensor": "Sensor", "status": "Status", "value": "Last Value", "threshold": "Threshold",
    "port": "Port", "output": "Output", "message": "Message", "url": "URL", "changes": "Changes",
    "duration": "Duration", "group": "Group", "host": "IP Address", "time": "Time",
}

_T = "{prefix}: {machine} [{sensor}] {level}"
DEFAULTS = {
    "alert": {"title": _T, "fields": ["device", "sensor", "status", "value", "threshold", "message", "duration", "time"]},
    "rule": {"title": _T, "fields": ["device", "sensor", "status", "value", "threshold", "message", "duration", "time"]},
    "queue": {"title": _T, "fields": ["device", "sensor", "port", "status", "message", "value", "time"]},
    "netstat": {"title": _T, "fields": ["device", "sensor", "port", "status", "value", "threshold", "duration", "time"]},
    "web": {"title": "{prefix}: {machine} [{sensor}] {level}", "fields": ["device", "sensor", "status", "url", "value", "time"]},
    "software": {"title": "{prefix}: {machine} [Software] {count} change(s)", "fields": ["device", "changes", "time"]},
}

# ตัวแปรที่ใช้ในหัวเรื่อง / ข้อความท้ายการ์ดได้
VARS = ("prefix", "machine", "host", "group", "sensor", "level", "status", "value", "threshold", "port", "output",
        "message", "url", "duration", "time", "count", "icon")

COLOR = {"software": "Accent", "down": "Attention", "error": "Attention", "warning": "Warning", "recovered": "Good",
         "ok": "Good", "up": "Good", "unknown": "Warning", "good": "Good", "attention": "Attention"}

MAX_TITLE, MAX_NOTE, MAX_LABEL = 200, 1000, 40


def render(text, ctx):
    """แทน {ชื่อ} ด้วยค่า (ไม่มีค่า = ว่าง) แล้วตัดช่องว่างซ้ำ"""
    out = re.sub(r"\{(\w+)\}", lambda m: "" if ctx.get(m.group(1)) in (None, "") else str(ctx[m.group(1)]), text or "")
    return re.sub(r"[ \t]{2,}", " ", out).replace("[]", "").strip()


def template(cat, cards=None):
    """รูปแบบที่ใช้จริงของหัวข้อ: ที่ตั้งไว้ทับค่าปริยาย"""
    d = DEFAULTS[cat]
    c = (cards or {}).get(cat) if isinstance(cards, dict) else None
    c = c if isinstance(c, dict) else {}
    fields = [f for f in c.get("fields") or [] if f in FIELDS] if isinstance(c.get("fields"), list) else list(d["fields"])
    labels = {k: str(v) for k, v in (c.get("labels") or {}).items() if k in FIELDS and str(v).strip()} \
        if isinstance(c.get("labels"), dict) else {}
    return {"title": str(c.get("title") or d["title"]), "fields": fields, "labels": labels, "note": str(c.get("note") or "")}


def clean(cards):
    """ตรวจค่าที่ส่งมาจากหน้า Settings -> (ค่าที่สะอาด, errors)"""
    out, errors = {}, {}
    if not isinstance(cards, dict):
        return {}, {"cards": "ต้องเป็น object"}
    for cat, c in cards.items():
        if cat not in CATEGORIES:
            errors[str(cat)] = "ไม่รู้จักหัวข้อนี้"
            continue
        if not isinstance(c, dict):
            errors[cat] = "ต้องเป็น object"
            continue
        title = str(c.get("title") or "").strip()[:MAX_TITLE]
        fields = [f for f in c.get("fields") or [] if f in FIELDS] if isinstance(c.get("fields"), list) else None
        if fields is None:
            errors[cat] = "fields ต้องเป็นรายการ"
            continue
        labels = {k: str(v).strip()[:MAX_LABEL] for k, v in (c.get("labels") or {}).items()
                  if k in FIELDS and str(v).strip() and str(v).strip() != FIELDS[k]} if isinstance(c.get("labels"), dict) else {}
        out[cat] = {"title": title, "fields": list(dict.fromkeys(fields)), "labels": labels,
                    "note": str(c.get("note") or "").strip()[:MAX_NOTE]}
    return out, errors


def card(cat, level, ctx, cards=None, summary=None):
    """สร้าง payload ของ Teams (Adaptive Card 1.4) ตามรูปแบบของหัวข้อ"""
    t = template(cat, cards)
    ctx = dict(ctx, prefix=ctx.get("prefix") or PREFIX)
    title = render(t["title"], ctx) or render(DEFAULTS[cat]["title"], ctx)
    body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True,
             "color": COLOR.get(level, "Default")}]
    for f in t["fields"]:
        v = ctx.get(f)
        if isinstance(v, (list, tuple)):
            v = "\n\n".join(str(x) for x in v if x not in (None, ""))
        if v in (None, ""):
            continue
        body.append({"type": "TextBlock", "text": f"{t['labels'].get(f) or FIELDS[f]}:", "weight": "Bolder",
                     "wrap": True, "spacing": "Medium"})
        body.append({"type": "TextBlock", "text": str(v), "wrap": True, "spacing": "None"})
    note = render(t["note"], ctx)
    if note:
        body.append({"type": "TextBlock", "text": note, "wrap": True, "spacing": "Medium", "separator": True})
    content = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
               "msteams": {"width": "Full"}, "fallbackText": summary or title, "body": body}
    return {"type": "message", "summary": summary or title, "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": content}]}


SAMPLE = {
    "alert": {"machine": "DEV_ARKHEION", "host": "172.25.20.51", "sensor": "CPU", "level": "WARNING",
              "status": "WARNING – CPU ใช้ไป 82% (เกณฑ์ 80%)", "value": "82 %", "threshold": "Warning ≥ 80% · Error ≥ 95%"},
    "rule": {"machine": "web-01", "host": "10.0.0.5", "sensor": "RAM สูง (กฎ)", "level": "ERROR",
             "status": "ERROR – RAM ใช้ไป 93%", "value": "93 %", "threshold": "Error ≥ 90%", "message": "แจ้งทีม DBA"},
    "queue": {"machine": "abc86", "host": "172.25.16.94", "sensor": "THMux Output[1]", "port": "16100/TCP", "output": "Output[1]",
              "level": "WARNING", "status": "WARNING – เหลือง (WARN)", "value": "511",
              "message": "queue exceed <Set On Stage> (size=511, CRITICAL SIZE=500)"},
    "netstat": {"machine": "abc86", "host": "172.25.16.94", "sensor": "Netstat :16100/tcp Connections", "port": "16100/TCP",
                "level": "ERROR", "status": "ERROR – Connections 520", "value": "520", "threshold": "500"},
    "web": {"machine": "Intranet", "sensor": "Web check", "level": "DOWN", "status": "DOWN – เว็บ Intranet ล่ม (HTTP 502)",
            "url": "https://intranet.local", "value": "HTTP 502"},
    "software": {"machine": "abc86", "host": "172.25.16.94", "count": 2,
                 "changes": ["ติดตั้ง: 7-Zip 23.01", "อัปเดต: Google Chrome 129 → 130"]},
}
