"""
notify.py - ส่งการ์ดแจ้งเตือนไป Microsoft Teams
================================================

ส่งเมื่อ (เลือกได้ที่หน้า Settings > Notifications -> puller.yaml notify.teams.levels)
    down       เครื่อง Down (agent ไม่ส่งข้อมูล / node_exporter ไม่ตอบ)
    error      sensor ถึงเกณฑ์ error (disk เต็ม, CPU/RAM สูง, service สำคัญหยุด, bottleneck, ping หาย ...)
    warning    sensor ถึงเกณฑ์ warning
    recovered  ปัญหาหายแล้ว (บอกว่าเป็นนานเท่าไหร่ / ค่าสูงสุด)
    checks     Web / SSL check: เว็บล่ม, ใบรับรองใกล้หมดอายุ, กลับมาปกติ
ส่งครั้งเดียวต่อการเปลี่ยนสถานะ (สถานะที่ยืนยันแล้วตาม sensors.confirm_checks) ไม่ส่งซ้ำระหว่างที่ยังเป็นอยู่
เปลี่ยนหลายอย่างในเครื่องเดียวกันในรอบเดียว -> รวมเป็นการ์ดเดียว

ปลายทาง: webhook ของ Teams
    แนะนำ  Teams > ช่อง > Workflows > "Send webhook alerts to a channel" (Power Automate)
    หรือ   Incoming Webhook แบบเดิม (Office 365 connector) ถ้าองค์กรยังเปิดใช้
    ทั้งสองแบบรับ payload เดียวกัน: {"type": "message", "attachments": [Adaptive Card]}

puller.yaml
    notify:
      teams:
        enabled: true
        webhook_url: https://....      # เป็นความลับ (ใครมี URL ก็ส่งข้อความเข้าช่องได้)
        levels: [down, error, warning, recovered, checks]
        dashboard_url: https://central.example.local:8090   # ปุ่ม "Open dashboard" บนการ์ด

ส่งใน thread แยก (ไม่ทำให้รอบตรวจ sensor ช้า) ลองใหม่ 3 ครั้ง ส่งไม่สำเร็จ -> บันทึกใน System log
"""

import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from config import load_config

ALL_LEVELS = ("down", "error", "warning", "recovered", "checks")
STYLE = {"down": "attention", "error": "attention", "warning": "warning", "recovered": "good", "ok": "good",
         "up": "good", "unknown": "warning"}
LABEL = {"down": "DOWN", "error": "ERROR", "warning": "WARNING", "recovered": "RECOVERED", "ok": "OK",
         "up": "UP", "unknown": "UNKNOWN", "paused": "PAUSED"}
RANK = {"recovered": 0, "ok": 0, "up": 0, "unknown": 1, "warning": 2, "error": 3, "down": 4}
MAX_CHANGES_PER_CARD = 15
RETRIES = (5, 30, 120)          # วินาทีที่รอก่อนลองใหม่

_queue = queue.Queue(maxsize=500)
_started = False
_lock = threading.Lock()


def settings(config=None):
    cfg = config if config is not None else load_config()
    t = ((cfg.get("notify") or {}).get("teams")) or {}
    url = str(t.get("webhook_url") or "").strip()
    levels = t.get("levels")
    if not isinstance(levels, list):
        levels = list(ALL_LEVELS)
    return {"enabled": t.get("enabled", True) is not False and url.startswith("https://"), "url": url,
            "levels": {str(x) for x in levels}, "dashboard_url": str(t.get("dashboard_url") or "").rstrip("/")}


def _now_text():
    return datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")


def _dur(sec):
    sec = int(max(0, sec or 0))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m" if m else f"{s}s"


def build_card(title, level, facts, lines, link=None, link_title="Open dashboard"):
    """Adaptive Card 1.4 สำหรับ Teams (ใช้ได้ทั้ง Workflows webhook และ Incoming Webhook)"""
    body = [{
        "type": "Container", "style": STYLE.get(level, "default"), "bleed": True,
        "items": [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}],
    }]
    if facts:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": str(v)} for k, v in facts if v not in (None, "")]})
    for text, lv in lines:
        color = {"attention": "Attention", "warning": "Warning", "good": "Good"}.get(STYLE.get(lv, ""), "Default")
        body.append({"type": "TextBlock", "text": text, "wrap": True, "spacing": "Small", "color": color})
    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
            "version": "1.4", "msteams": {"width": "Full"}, "body": body}
    if link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": link_title, "url": link}]
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def post(url, payload, timeout=15):
    """ส่ง 1 ครั้ง -> (ok, ข้อความ)  Teams ตอบ 200 (Incoming Webhook) หรือ 202 (Workflows)"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "central-monitor"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(300).decode("utf-8", "replace")
            if r.status in (200, 201, 202):
                return True, f"HTTP {r.status}"
            return False, f"HTTP {r.status} {body}"
    except urllib.error.HTTPError as e:
        try:
            body = e.read(300).decode("utf-8", "replace")
        except Exception:
            body = ""
        return False, f"HTTP {e.code} {body}".strip()
    except Exception as e:
        return False, repr(e)


def _host_of(url):
    try:
        return urllib.parse.urlsplit(url).hostname or "?"
    except Exception:
        return "?"


def _worker():
    from events import append_events
    while True:
        url, payload, what = _queue.get()
        ok, msg = post(url, payload)
        for wait in RETRIES:
            if ok:
                break
            time.sleep(wait)
            ok, msg = post(url, payload)
        if ok:
            print(f"[NOTIFY] Teams <- {what} ({msg})")
        else:
            print(f"[NOTIFY ERROR] Teams {_host_of(url)}: {what}: {msg}")
            try:
                append_events([{"source": "notify", "level": "warning",
                                "message": f"ส่งแจ้งเตือนไป Microsoft Teams ไม่สำเร็จ ({what}): {msg[:200]}"}])
            except Exception:
                pass
        _queue.task_done()


def _enqueue(url, payload, what):
    global _started
    with _lock:
        if not _started:
            threading.Thread(target=_worker, daemon=True, name="notify").start()
            _started = True
    try:
        _queue.put_nowait((url, payload, what))
    except queue.Full:
        print(f"[NOTIFY ERROR] queue full, drop: {what}")


def _kind(event):
    """ชนิดของเหตุการณ์ sensor สำหรับเทียบกับ levels  None = ไม่ส่ง"""
    if event.get("level") == "recovered":
        return "recovered"
    to = event.get("to")
    return to if to in ("down", "error", "warning") else None


def sensor_events(events, config=None):
    """เหตุการณ์จาก sensor_monitor.run_once (สถานะที่ยืนยันแล้ว) -> การ์ดละ 1 เครื่อง"""
    s = settings(config)
    if not s["enabled"] or not events:
        return
    by_web = {}
    for e in events:
        k = _kind(e)
        if k and k in s["levels"]:
            by_web.setdefault(e.get("web") or "?", []).append((k, e))
    for web, items in by_web.items():
        worst = max((k for k, _ in items), key=lambda k: RANK.get(k, 0))
        problems = [k for k, _ in items if k != "recovered"]
        if not problems:
            title = f"{web}: กลับมาปกติ ({len(items)} รายการ)" if len(items) > 1 else \
                    f"{web}: {items[0][1].get('sensor')} กลับมาปกติ"
            worst = "recovered"
        elif len(items) == 1:
            title = f"{web}: {items[0][1].get('sensor')} {LABEL[worst]}"
        else:
            title = f"{web}: {LABEL[worst]} ({len(items)} รายการ)"
        first = items[0][1]
        lines = []
        for k, e in items[:MAX_CHANGES_PER_CARD]:
            msg = str(e.get("message") or "")
            prefix = f"{e.get('web')} · "
            if msg.startswith(prefix):
                msg = msg[len(prefix):]
            if k == "recovered" and e.get("duration"):
                msg += f" (เป็นอยู่ {_dur(e['duration'])})"
            lines.append((f"**{LABEL[k]}** · {msg}", k))
        if len(items) > MAX_CHANGES_PER_CARD:
            lines.append((f"... และอีก {len(items) - MAX_CHANGES_PER_CARD} รายการ (ดูในหน้า Events)", "warning"))
        link = f"{s['dashboard_url']}/?web={urllib.parse.quote(web)}" if s["dashboard_url"] else None
        facts = [("เครื่อง", web), ("กลุ่ม", first.get("group") or "-"), ("เวลา", _now_text())]
        _enqueue(s["url"], build_card(title, worst, facts, lines, link), f"{web} {worst}")


def check_event(name, url, old, new, result, group="", config=None):
    """Web / SSL check เปลี่ยนสถานะ (up / warning / down)"""
    s = settings(config)
    if not s["enabled"] or "checks" not in s["levels"]:
        return
    level = "recovered" if new == "up" else new
    if level == "recovered" and old not in ("down", "warning"):
        return
    title = f"Web check {name}: {LABEL.get(level, new.upper())}"
    facts = [("URL", url), ("กลุ่ม", group or "-"), ("สถานะ", f"{LABEL.get(old, old or '-')} → {LABEL.get(new, new)}"),
             ("HTTP", result.get("code")), ("เวลาตอบ", f"{result['ms']} ms" if result.get("ms") is not None else None),
             ("เวลา", _now_text())]
    ssl_ = result.get("ssl") or {}
    if ssl_.get("days_left") is not None:
        facts.insert(3, ("SSL หมดอายุใน", f"{ssl_['days_left']} วัน"))
    lines = [(str(result["error"]), level)] if result.get("error") else []
    link = f"{s['dashboard_url']}/events" if s["dashboard_url"] else None
    _enqueue(s["url"], build_card(title, level, facts, lines, link), f"check {name} {new}")
