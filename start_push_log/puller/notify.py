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
    return datetime.now().astimezone().strftime("%d/%m/%Y %H:%M")


def _dur(sec):
    sec = int(max(0, sec or 0))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m, s = divmod(r, 60)
    return f"{d}d {h}h" if d else f"{h}h {m}m" if h else f"{m}m" if m else f"{s}s"


ICON = {"down": "🔴", "error": "🔴", "warning": "🟡", "recovered": "🟢", "up": "🟢", "ok": "🟢"}
BOTTLENECK_TH = {"cpu": "คอขวด CPU (งานรอคิว)", "memory": "คอขวด RAM (ใช้ page file / swap หนัก)",
                 "disk": "คอขวด Disk (ตอบช้า คิวยาว)", "network": "คอขวด Network"}


def build_card(title, level, lines, footer="", link=None, link_title="เปิด dashboard"):
    """Adaptive Card 1.4 แบบสั้น: หัวเรื่อง 1 บรรทัด + รายการละ 1 บรรทัด + บรรทัดเล็กท้าย
    (ใช้ได้ทั้ง Teams Workflows webhook และ Incoming Webhook)"""
    body = [{"type": "Container", "style": STYLE.get(level, "default"), "bleed": True,
             "items": [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}]}]
    for text in lines:
        body.append({"type": "TextBlock", "text": text, "wrap": True, "spacing": "Small"})
    if footer:
        body.append({"type": "TextBlock", "text": footer, "isSubtle": True, "size": "Small", "wrap": True, "spacing": "Medium"})
    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
            "version": "1.4", "msteams": {"width": "Full"}, "fallbackText": title, "body": body}
    if link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": link_title, "url": link}]
    return {"type": "message", "summary": title, "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def _n(v):
    """ตัวเลขอ่านง่าย: 82.8 -> 83, 7.5 -> 7.5"""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return f"{v:.0f}" if abs(v) >= 10 else f"{v:.1f}".rstrip("0").rstrip(".")


def _limit(e):
    lim = e.get("error") if e.get("to") == "error" else e.get("warning")
    return _n(lim)


def _subject(e):
    """ชื่อเรื่องสั้น ๆ ของ sensor"""
    k, name = e.get("kind"), str(e.get("sensor") or "")
    sid = str(e.get("sid") or "")
    if k == "availability":
        return "เครื่อง"
    if k == "cpu":
        return "CPU"
    if k == "memory":
        return "RAM"
    if k == "mem_available":
        return "RAM ว่าง"
    if k == "disk":
        return f"Disk {e.get('mount') or name.split(':', 1)[-1].strip()}"
    if k == "disk_latency":
        return "Disk latency"
    if k == "packet_loss":
        return "Ping loss"
    if k == "net_latency":
        return "Ping latency"
    if k == "service":
        return f"Service {name.split(':', 1)[-1].strip()}"
    if k == "bottleneck":
        return BOTTLENECK_TH.get(sid.rsplit(":", 1)[-1], "คอขวด")
    if k == "logpull":
        return "ดึง log (SSH)"
    return name


def short_text(kind, e, with_limit=True):
    """ข้อความ 1 บรรทัดที่อ่านแล้วรู้เลยว่าเกิดอะไร (with_limit=False: ไม่ใส่เกณฑ์ ใช้กับหัวการ์ด)"""
    k, v, lim = e.get("kind"), _n(e.get("value")), _limit(e)
    sub = _subject(e)
    if kind == "recovered":
        if k == "availability":
            txt = "เครื่องกลับมาออนไลน์"
        elif k == "service":
            txt = f"{sub} ทำงานแล้ว"
        else:
            txt = f"{sub} กลับปกติ" + (f" ({v}{'%' if e.get('unit') == '%' else ' ' + str(e.get('unit') or '')})".rstrip() if v else "")
        if e.get("duration"):
            txt += f" · เป็นอยู่ {_dur(e['duration'])}"
        return txt
    if not with_limit:
        lim = None
    gate = f" (เกณฑ์ {lim}{'%' if e.get('unit') == '%' else ' ms' if e.get('unit') == 'ms' else ''})" if lim else ""
    if k == "availability":
        return "เครื่องดับ หรือไม่ส่งข้อมูล"
    if k == "cpu":
        return f"CPU สูง {v}%{gate}" if v else "CPU สูง"
    if k == "memory":
        return f"RAM ใช้ไป {v}%{gate}" if v else "RAM ใกล้เต็ม"
    if k == "mem_available":
        return (f"RAM เหลือแค่ {v}%" + (f" (ต่ำกว่า {lim}%)" if lim else "")) if v else "RAM เหลือน้อย"
    if k == "disk":
        return f"{sub} เต็ม {v}%{gate}" if v else f"{sub} ใกล้เต็ม"
    if k == "disk_latency":
        return f"Disk ตอบช้า {v} ms{gate}" if v else "Disk ตอบช้า"
    if k == "packet_loss":
        return f"Ping หาย {v}%{gate}" if v else "Ping หาย"
    if k == "net_latency":
        return f"Ping ช้า {v} ms{gate}" if v else "Ping ไม่ตอบ"
    if k == "service":
        return f"{sub} หยุดทำงาน"
    if k == "bottleneck":
        return sub
    if k == "logpull":
        return "ดึง log ทาง SSH ไม่ได้"
    return f"{sub} {LABEL.get(e.get('to'), e.get('to') or '')}".strip()


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
    """เหตุการณ์จาก sensor_monitor.run_once (สถานะที่ยืนยันแล้ว) -> การ์ดละ 1 เครื่อง
    หัวการ์ด = เรื่องที่หนักที่สุด (เห็นได้จากหน้าแจ้งเตือนโดยไม่ต้องเปิด)  ตัวการ์ด = 1 บรรทัดต่อเรื่อง"""
    s = settings(config)
    if not s["enabled"] or not events:
        return
    by_web = {}
    for e in events:
        k = _kind(e)
        if k and k in s["levels"]:
            by_web.setdefault(e.get("web") or "?", []).append((k, e))
    for web, items in by_web.items():
        items.sort(key=lambda x: -RANK.get(x[0], 0))           # หนักสุดขึ้นก่อน
        worst = items[0][0]
        lines = [f"{ICON.get(k, '')} {short_text(k, e)}" for k, e in items[:MAX_CHANGES_PER_CARD]]
        if len(items) > MAX_CHANGES_PER_CARD:
            lines.append(f"... และอีก {len(items) - MAX_CHANGES_PER_CARD} เรื่อง")
        more = f" และอีก {len(items) - 1} เรื่อง" if len(items) > 1 else ""
        title = f"{ICON.get(worst, '')} {web} · {short_text(*items[0], with_limit=len(items) == 1)}{more}"
        group = items[0][1].get("group")
        footer = " · ".join(x for x in (f"กลุ่ม {group}" if group else "", _now_text()) if x)
        link = f"{s['dashboard_url']}/?web={urllib.parse.quote(web)}" if s["dashboard_url"] else None
        _enqueue(s["url"], build_card(title, worst, lines if len(items) > 1 else [], footer, link), f"{web} {worst}")


def check_event(name, url, old, new, result, group="", config=None):
    """Web / SSL check เปลี่ยนสถานะ (up / warning / down)"""
    s = settings(config)
    if not s["enabled"] or "checks" not in s["levels"]:
        return
    level = "recovered" if new == "up" else new
    if level == "recovered" and old not in ("down", "warning"):
        return
    ssl_ = result.get("ssl") or {}
    if level == "recovered":
        text = f"เว็บ {name} กลับมาปกติ"
    elif level == "warning" and ssl_.get("days_left") is not None:
        text = f"SSL ของ {name} หมดอายุใน {ssl_['days_left']} วัน"
    elif result.get("code"):
        text = f"เว็บ {name} ล่ม (HTTP {result['code']})"
    else:
        text = f"เว็บ {name} เข้าไม่ได้"
    title = f"{ICON.get(level, '')} {text}"
    lines = [url]
    footer = " · ".join(x for x in (f"กลุ่ม {group}" if group else "", _now_text()) if x)
    link = f"{s['dashboard_url']}/events" if s["dashboard_url"] else None
    _enqueue(s["url"], build_card(title, level, lines, footer, link), f"check {name} {new}")
