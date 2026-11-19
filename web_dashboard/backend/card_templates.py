"""
card_templates.py - รูปแบบการ์ด Microsoft Teams ที่ปรับแต่งได้ (Settings > การ์ด Teams)
    ไฟล์นี้มี 2 ชุดที่ต้องเหมือนกันทุกตัวอักษร: start_push_log/puller/ และ web_dashboard/backend/
    (tests ตรวจให้แล้ว)  ค่าที่ตั้งเก็บใน puller.yaml: notify.teams.cards.<หัวข้อ> = {title, fields, labels, note}

การ์ดหน้าตาแบบ PRTG: หัวเรื่องตัวหนาสีตามระดับ แล้วบรรทัดละ 1 ช่อง "**หัวข้อ:** ค่า"
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

# เรื่องที่เลือก "ไม่ต้องแจ้ง" ได้ (ยังเก็บใน Alerts / History แต่ไม่ส่ง Teams และไม่เด้งป๊อปอัป)
MUTE = {"netstat": {"agent_stale": "Agent stopped (agent หยุดส่งข้อมูล)", "host_offline": "Host offline (เครื่องไม่ตอบ)",
                    "not_listening": "Port ไม่ LISTEN", "queue_spike": "Queue spike (คิวพุ่ง)",
                    "queue": "Queue เกินเกณฑ์", "conn": "Connections เกินเกณฑ์"}}

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
    mute = [m for m in c.get("mute") or [] if m in MUTE.get(cat, {})] if isinstance(c.get("mute"), list) else []
    return {"title": str(c.get("title") or d["title"]), "fields": fields, "labels": labels, "note": str(c.get("note") or ""),
            "mute": mute}


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
                    "note": str(c.get("note") or "").strip()[:MAX_NOTE],
                    "mute": [m for m in MUTE.get(cat, {}) if m in (c.get("mute") or [])] if isinstance(c.get("mute"), list) else []}
    return out, errors


def fact_line(label, value):
    """1 บรรทัด: **หัวข้อ:** ค่า  (หลายค่า = ขึ้นบรรทัดใหม่ใต้หัวข้อ)"""
    v = str(value)
    return {"type": "TextBlock", "text": f"**{label}:** " + ("\n\n" + v if "\n" in v else v), "wrap": True, "spacing": "Small"}


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
        body.append(fact_line(t["labels"].get(f) or FIELDS[f], v))
    note = render(t["note"], ctx)
    if note:
        body.append({"type": "TextBlock", "text": note, "wrap": True, "spacing": "Medium", "separator": True})
    content = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
               "msteams": {"width": "Full"}, "fallbackText": summary or title, "body": body}
    return {"type": "message", "summary": summary or title, "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": content}]}


SAMPLE = {   # การ์ดตัวอย่าง (ส่งทดสอบ): หลัง ":" เป็นชื่อช่อง ไม่ใช่ค่าจริง
    "alert": {"machine": "Device_Name", "host": "IP_Address", "sensor": "Sensor_Name", "level": "WARNING",
              "status": "Status", "value": "Last_Value", "threshold": "Threshold", "message": "Message"},
    "rule": {"machine": "Device_Name", "host": "IP_Address", "sensor": "Sensor_Name", "level": "ERROR",
             "status": "Status", "value": "Last_Value", "threshold": "Threshold", "message": "Message"},
    "queue": {"machine": "Device_Name", "host": "IP_Address", "sensor": "Sensor_Name", "port": "Port", "output": "Output_Name",
              "level": "WARNING", "status": "Status", "value": "Last_Value", "message": "Message"},
    "netstat": {"machine": "Device_Name", "host": "IP_Address", "sensor": "Sensor_Name", "port": "Port",
                "level": "ERROR", "status": "Status", "value": "Last_Value", "threshold": "Threshold"},
    "web": {"machine": "Device_Name", "sensor": "Sensor_Name", "level": "DOWN", "status": "Status",
            "url": "URL", "value": "Last_Value"},
    "software": {"machine": "Device_Name", "host": "IP_Address", "count": 2, "changes": ["Change_1", "Change_2"]},
}
