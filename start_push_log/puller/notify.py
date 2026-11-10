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

ส่งใน thread แยก (ไม่ทำให้รอบตรวจ sensor ช้า) ลองใหม่ 3 ครั้ง ส่งไม่สำเร็จ -> บันทึกใน System log
"""

import json
import os
import queue
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from config import load_config

ALL_LEVELS = ("down", "error", "warning", "recovered", "checks", "software")
STYLE = {"down": "attention", "error": "attention", "warning": "warning", "recovered": "good", "ok": "good",
         "up": "good", "unknown": "warning"}
LABEL = {"down": "DOWN", "error": "ERROR", "warning": "WARNING", "recovered": "RECOVERED", "ok": "OK",
         "up": "UP", "unknown": "UNKNOWN", "paused": "PAUSED"}
RANK = {"recovered": 0, "ok": 0, "up": 0, "unknown": 1, "warning": 2, "error": 3, "down": 4}
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
    times = t.get("summary_times")
    times = [str(x) for x in times] if isinstance(times, list) else []
    return {"enabled": t.get("enabled", True) is not False and url.startswith("https://"), "url": url,
            "levels": {str(x) for x in levels}, "summary_times": sorted({x for x in times if _hhmm(x)})}


DEFAULT_TEMPLATE = "{icon} {machine} · {text}"
TEMPLATE_KEYS = ("icon", "machine", "group", "host", "sensor", "text", "value", "unit", "threshold", "level",
                 "time", "duration")


def render(template, ctx):
    """แทน {ชื่อ} ในข้อความด้วยค่า (ชื่อที่ไม่รู้จักคงไว้ตามเดิม)"""
    return re.sub(r"\{(\w+)\}", lambda m: str(ctx[m.group(1)]) if ctx.get(m.group(1)) not in (None, "") else
                  ("" if m.group(1) in TEMPLATE_KEYS else m.group(0)), template).strip()


def wanted(e, s):
    """เรื่องนี้ต้องส่งไหม: กฎที่ปิด "ส่ง Teams" -> ไม่ส่ง
    (เลือกเครื่อง / เรื่อง / ข้อความเอง = Settings > Custom alert rules)"""
    return e.get("notify") is not False


def _hhmm(x):
    """"08:30" -> (8, 30)  ไม่ถูกต้อง -> None"""
    try:
        h, m = str(x).strip().split(":")
        h, m = int(h), int(m)
        return (h, m) if 0 <= h <= 23 and 0 <= m <= 59 else None
    except (ValueError, AttributeError):
        return None


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


def build_card(title, level, lines, footer=""):
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
    return {"type": "message", "summary": title, "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


COLOR = {"software": "Accent", "down": "Attention", "error": "Attention", "warning": "Warning", "recovered": "Good", "ok": "Good",
         "up": "Good", "unknown": "Warning"}
CARD_PREFIX = "Central Monitor Notification"


def fact_card(title, level, facts, summary=None):
    """การ์ดแบบ PRTG: หัวเรื่องตัวหนาสีตามระดับ + ตาราง หัวข้อ : ค่า
    facts = [(หัวข้อ, ค่า)] ค่าว่างจะไม่แสดง"""
    rows = [{"title": f"{t}:", "value": str(v)} for t, v in facts if v not in (None, "")]
    body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True,
             "color": COLOR.get(level, "Default")},
            {"type": "FactSet", "spacing": "Medium", "facts": rows}]
    card = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
            "version": "1.4", "msteams": {"width": "Full"}, "fallbackText": summary or title, "body": body}
    return {"type": "message", "summary": summary or title, "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def _time_full():
    return datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S")


def _device(machine, host="", group=""):
    return " ".join([str(machine)] + [f"[{x}]" for x in (host, group) if x])


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
    if k == "custom":
        u = e.get("unit") or ""
        if v is None:
            return sub                       # เช่นกฎ service: ชื่อกฎบอกอยู่แล้ว ("Spooler หยุด")
        if not lim:
            return f"{sub} {v}{u}"
        return f"{sub} {v}{u} ({'ต่ำกว่า' if e.get('low') else 'เกณฑ์'} {lim}{u})"
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
    """เหตุการณ์จาก sensor_monitor.run_once (สถานะที่ยืนยันแล้ว) -> 1 การ์ดต่อ 1 เรื่อง
    การ์ด = บรรทัดเดียว เช่น "🟡 host-window · RAM ใช้ไป 83% (เกณฑ์ 80%)" + เวลา"""
    s = settings(config)
    if not events:
        return
    cfg = config if config is not None else load_config()
    rule_urls = {str(r.get("id")): str(r.get("teams_url") or "").strip()
                 for r in cfg.get("alert_rules") or [] if isinstance(r, dict)}
    for e in events:
        k = _kind(e)
        if not k or not wanted(e, s):
            continue
        rid = e.get("rule")
        if rid:
            # กฎที่สร้างเอง: ส่งทั้งตอนมีปัญหาและตอนกลับปกติ ไปที่ช่องของกฎ (ถ้าตั้ง) หรือช่องหลัก
            url = rule_urls.get(rid) or (s["url"] if s["enabled"] else "")
        else:
            url = s["url"] if s["enabled"] and k in s["levels"] else ""
        if not url.startswith("https://"):
            continue
        web = e.get("web") or "?"
        lim = e.get("error") if e.get("to") == "error" else e.get("warning")
        ctx = {"icon": ICON.get(k, ""), "machine": web, "group": e.get("group"), "host": e.get("host"),
               "sensor": e.get("sensor"), "text": short_text(k, e), "value": _n(e.get("value")),
               "unit": e.get("unit") or "", "threshold": _n(lim), "level": LABEL.get(k, k), "time": _now_text(),
               "duration": _dur(e["duration"]) if e.get("duration") else ""}
        tpl = e.get("template") or DEFAULT_TEMPLATE         # ข้อความเฉพาะกฎ (Custom alert rules) หรือแบบปริยาย
        summary = render(tpl, ctx) or render(DEFAULT_TEMPLATE, ctx)   # ข้อความบนแถบแจ้งเตือนของ Teams
        sensor = str(e.get("sensor") or _subject(e))
        unit = e.get("unit") or ""
        try:
            val = f"{float(e.get('value')):.2f}".rstrip("0").rstrip(".")   # ค่าจริง 2 ตำแหน่ง เช่น 87.81
        except (TypeError, ValueError):
            val = None
        th = [f"{n} {'≤' if e.get('low') else '≥'} {_n(e.get(x))}{unit}" for n, x in (("Warning", "warning"), ("Error", "error"))
              if _n(e.get(x)) is not None]
        facts = [("Device", _device(web, e.get("host"), e.get("group"))),
                 ("Sensor", sensor),
                 ("Status", f"{LABEL.get(k, k)} – {short_text(k, e)}"),
                 ("Last Value", f"{val} {unit}".strip() if val is not None else ""),
                 ("Threshold", " · ".join(th)),
                 ("Message", render(e["template"], ctx).lstrip("🔴🟡🟢 ") if e.get("template") else ""),
                 ("Duration", ctx["duration"]),
                 ("Time", _time_full())]
        _enqueue(url, fact_card(f"{CARD_PREFIX}: {web} [{sensor}] {LABEL.get(k, k)}", k, facts, summary),
                 f"{web} {e.get('sensor')} {k}")


# ------------------------------------------------------------------
# สรุปประจำวัน: ส่งตามเวลาที่ตั้ง (notify.teams.summary_times เช่น ["08:30", "17:00"])
#   1 บรรทัดต่อเครื่อง: 🔴 Down / Error · 🟡 Warning · 🟢 ปกติ / กลับปกติ (วันนี้มีปัญหาแต่หายแล้ว) · ⏸ Paused
#   ปัญหาของเครื่อง = ข้อความสั้นของแต่ละ sensor ที่ไม่ปกติ เช่น "RAM ใช้ไป 83% (เกณฑ์ 80%)"
# ------------------------------------------------------------------

# กฎของรายงาน: ห้ามใช้ค่าเฉลี่ยข้ามเครื่อง (Average) ทุกกรณี -> แสดงค่าของแต่ละเครื่อง + ค่าสูงสุด (พร้อมชื่อเครื่อง)
SUMMARY_LATE = 15 * 60          # puller หยุดไปเกินเวลานี้หลังเวลาที่ตั้ง -> ไม่ส่งย้อนหลัง
SUMMARY_MAX_BYTES = 24000       # การ์ด Teams รับได้ ~28KB -> เกินนี้แบ่งเป็นหลายการ์ด
SUMMARY_MAX_INCIDENTS = 15


def _sensor_as_event(sn):
    return {"kind": sn.get("kind"), "value": sn.get("value"), "unit": sn.get("unit"), "warning": sn.get("warning"),
            "error": sn.get("error"), "mount": sn.get("mount"), "sensor": sn.get("name"), "sid": sn.get("id"),
            "to": sn.get("status")}


def _f(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v


def _pct(v):
    v = _f(v)
    if v is None:
        return None
    return f"{v:.0f}%" if v >= 10 else f"{v:.1f}".rstrip("0").rstrip(".") + "%"


def _mark(st):
    return f" {ICON[st]}" if st in ("warning", "error", "down") else ""


def _hm(ts):
    return datetime.fromtimestamp(ts).astimezone().strftime("%H:%M")


def _delta(cur, prev):
    """เทียบกับรอบก่อน: ▲5 / ▼3 / = """
    cur, prev = _f(cur), _f(prev)
    if cur is None or prev is None:
        return ""
    d = round(cur - prev)
    return f"▲{d}" if d > 0 else f"▼{-d}" if d < 0 else "="


def _machines(sensors):
    """sensors.json -> {เครื่อง: ค่าปัจจุบัน + สถานะ}"""
    out = {}
    for sn in sensors or []:
        web = sn.get("web") or "?"
        m = out.setdefault(web, {"group": str(sn.get("group") or "").strip("/"), "host": sn.get("host") or "",
                                 "avail": None, "paused": False, "worst": "ok", "cpu": None, "mem": None,
                                 "disks": {}, "other": []})
        st, k = sn.get("status"), sn.get("kind")
        if st == "paused":
            m["paused"] = True
            continue
        if k == "availability":
            m["avail"] = st
        if st in ("down", "error", "warning") and RANK.get(st, 0) > RANK.get(m["worst"], 0):
            m["worst"] = st
        if k == "cpu":
            m["cpu"] = (sn.get("value"), st)
        elif k == "memory":
            m["mem"] = (sn.get("value"), st)
        elif k == "disk":
            m["disks"][str(sn.get("mount") or "?")] = (sn.get("value"), st)
        elif st in ("error", "warning") and k not in ("bottleneck", "mem_available", "availability"):
            m["other"].append(f"{ICON[st]} {short_text(st, _sensor_as_event(sn))}")
    for m in out.values():
        if m["paused"]:
            m["state"] = "PAUSE"
        elif m["avail"] == "down" or m["worst"] == "down":
            m["state"] = "DOWN"
        elif m["avail"] == "unknown" or (m["avail"] is None and m["cpu"] is None and m["mem"] is None):
            m["state"] = "UNKNOWN"
        else:
            m["state"] = "UP"
    return out


def _period_incidents(incidents, sensors, start, now):
    """incident ที่เกิด / จบในช่วง [start, now] (ทั้งที่ปิดแล้ว และที่ยังเปิดอยู่)"""
    out = []
    for i in incidents or []:
        s0, e0 = _f(i.get("start")) or 0, _f(i.get("end")) or 0
        if e0 >= start and s0 <= now:
            out.append({**i, "open": False})
    for sn in sensors or []:
        inc = sn.get("incident")
        if isinstance(inc, dict) and (_f(inc.get("start")) or now) <= now:
            out.append({"web": sn.get("web"), "group": sn.get("group"), "kind": sn.get("kind"), "name": sn.get("name"),
                        "start": inc.get("start"), "end": None, "worst": inc.get("worst") or sn.get("status"),
                        "peak": inc.get("peak"), "unit": inc.get("unit"), "open": True})
    out.sort(key=lambda i: _f(i.get("start")) or 0)
    return out


def _overlap(i, start, now):
    s0 = max(_f(i.get("start")) or start, start)
    e0 = min(_f(i.get("end")) or now, now)
    return max(0, e0 - s0)


def _txt(text, **kw):
    return {"type": "TextBlock", "text": text, "wrap": True, **kw}


def _cell(lines, width, bold_first=False):
    items = []
    for n, t in enumerate(lines):
        if not t:
            continue
        b = {"type": "TextBlock", "text": t, "wrap": True, "spacing": "None"}
        if n == 0 and bold_first:
            b["weight"] = "Bolder"
        if n > 0:
            b.update(isSubtle=True, size="Small")
        items.append(b)
    return {"type": "Column", "width": width, "items": items or [_txt("-", spacing="None")]}


TABLE_W = ("stretch", 22, 22, 30, 18)        # Machine | CPU | RAM | Disk | Disk I/O


def _row(cells, header=False):
    cols = [_cell(c, w, bold_first=(n == 0)) for n, (c, w) in enumerate(zip(cells, TABLE_W))]
    if header:
        for c in cols:
            for it in c["items"]:
                it.update(weight="Bolder", isSubtle=False, size="Default")
    return {"type": "ColumnSet", "separator": True, "spacing": "Small", "columns": cols}


def _value_cell(cur, st, peak, prev, prev_label):
    if _pct(cur) is None:
        return ["-"]
    out = [f"{_pct(cur)}{_mark(st)}"]
    if _pct(peak) is not None:
        out.append(f"สูงสุด {_pct(peak)}")
    if prev_label and _pct(prev) is not None:
        out.append(f"{prev_label}: {_pct(prev)} {_delta(cur, prev)}")
    return out


def _max_of(items):
    """[(ค่า, ป้ายชื่อ)] -> "92% (PROD-03)" ของค่าที่มากที่สุด (ไม่เฉลี่ย)"""
    items = [(v, n) for v, n in items if _f(v) is not None]
    if not items:
        return "-"
    v, n = max(items, key=lambda x: _f(x[0]))
    return f"{_pct(v)} ({n})"


def build_summary(sensors, incidents, now=None, extra=None, start=None, prev=None, label=None):
    """รายงานสรุป (Morning / Evening / ส่งตอนนี้)  -> (payloads, snapshot)
    sensors = sensors.json["sensors"]  incidents = incidents.json
    extra   = {เครื่อง: {"io_util", "io_max", "cpu_max", "mem_max", "disk_max": {mount: %}}}  ค่าสูงสุดในช่วง
    start   = เริ่มช่วงรายงาน (ปริยาย 00:00 วันนี้)   prev = snapshot ของรายงานรอบก่อนวันเดียวกัน (ใช้เปรียบเทียบ)
    ไม่มีค่าเฉลี่ยข้ามเครื่อง: ทุกค่าเป็นของเครื่องนั้น ๆ  Group Summary = ค่าสูงสุดพร้อมชื่อเครื่อง"""
    now = now or time.time()
    extra = extra or {}
    midnight = datetime.fromtimestamp(now).astimezone().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    start = start if start is not None else midnight
    machines = _machines(sensors)
    prev_m = (prev or {}).get("machines") or {}
    prev_label = _hm(prev["time"]) if prev and prev.get("time") else ""

    # ---------- สถานะเครื่อง ----------
    count = {k: 0 for k in ("UP", "DOWN", "PAUSE", "UNKNOWN")}
    for m in machines.values():
        count[m["state"]] += 1
    n_warn = sum(1 for m in machines.values() if m["state"] == "UP" and m["worst"] in ("warning", "error"))

    # ---------- incident / downtime / recovery ในช่วง ----------
    incs = _period_incidents(incidents, sensors, start, now)
    down_incs = [i for i in incs if i.get("kind") == "availability"]
    downtime = {}
    for i in down_incs:
        d = downtime.setdefault(i.get("web") or "?", [0, 0, False])
        d[0] += _overlap(i, start, now)
        d[1] += 1
        d[2] = d[2] or i["open"]
    recovered = [i for i in down_incs if not i["open"] and i.get("ended_by", "recovered") == "recovered"
                 and (_f(i.get("end")) or 0) >= start]

    when = datetime.fromtimestamp(now).astimezone()
    label = label or ("Morning Report" if when.hour < 12 else "Evening Report")
    period = f"{_hm(start)}–{_hm(now)}"
    level = "error" if count["DOWN"] else "warning" if n_warn or count["UNKNOWN"] else "ok"
    title = f"📋 Central Monitor Daily Summary · {label} {when.strftime('%d/%m/%Y %H:%M')}"
    summary_line = (f"{label} {when.strftime('%H:%M')} · UP {count['UP']} · DOWN {count['DOWN']} · "
                    f"PAUSE {count['PAUSE']} · UNKNOWN {count['UNKNOWN']}")

    head = [
        {"type": "Container", "style": STYLE.get(level, "default"), "bleed": True, "items": [
            _txt(title, weight="Bolder", size="Medium"),
            _txt(f"ช่วงเวลา {period} · ค่าปัจจุบันของแต่ละเครื่อง + ค่าสูงสุดในช่วง (ไม่ใช้ค่าเฉลี่ย)"
                 + (f" · เทียบกับรอบ {prev_label}" if prev_label else ""), spacing="None", isSubtle=True, size="Small")]},
        {"type": "FactSet", "spacing": "Medium", "facts": [
            {"title": "🟢 UP:", "value": f"{count['UP']} เครื่อง" + (f" (มี Warning / Error {n_warn})" if n_warn else "")},
            {"title": "🔴 DOWN:", "value": f"{count['DOWN']} เครื่อง"},
            {"title": "⏸ PAUSE:", "value": f"{count['PAUSE']} เครื่อง"},
            {"title": "❔ UNKNOWN:", "value": f"{count['UNKNOWN']} เครื่อง"},
            {"title": "Incident:", "value": f"{len(incs)} ครั้ง" + (f" (ยังไม่หาย {sum(1 for i in incs if i['open'])})"
                                                                   if any(i["open"] for i in incs) else "")},
            {"title": "Downtime:", "value": ", ".join(f"{w} {_dur(d[0])}" + (" (ยังดับอยู่)" if d[2] else "")
                                                       for w, d in sorted(downtime.items())) or "ไม่มี"},
            {"title": "Recovery:", "value": (f"{len({i.get('web') for i in recovered})} เครื่อง · " + ", ".join(
                f"{i.get('web')} {_hm(i['end'])} (ดับ {_dur(i.get('duration') or (i['end'] - i['start']))})"
                for i in recovered)) if recovered else "ไม่มี"},
        ]},
    ]

    # ---------- รายละเอียด incident ----------
    inc_body = []
    if incs:
        inc_body.append(_txt(f"Incident ในช่วง {period}", weight="Bolder", separator=True, spacing="Medium"))
        for i in incs[-SUMMARY_MAX_INCIDENTS:]:
            st = i.get("worst") or "error"
            span = f"{_hm(i['start'])}–{'ตอนนี้' if i['open'] else _hm(i['end'])}"
            dur = _dur((_f(i.get("end")) or now) - (_f(i.get("start")) or now))
            pk = f" · สูงสุด {_pct(i['peak']) if i.get('unit') == '%' else _n(i['peak'])}" \
                if i.get("peak") is not None and i.get("kind") != "availability" else ""
            inc_body.append(_txt(f"{ICON.get(st, '•')} {span} ({dur}) **{i.get('web')}** · {i.get('name') or i.get('kind')}"
                                 f" · {LABEL.get(st, st)}{pk}" + (" · ยังไม่หาย" if i["open"] else ""), spacing="None"))
        if len(incs) > SUMMARY_MAX_INCIDENTS:
            inc_body.append(_txt(f"... และก่อนหน้านี้อีก {len(incs) - SUMMARY_MAX_INCIDENTS} ครั้ง",
                                 isSubtle=True, size="Small", spacing="None"))

    # ---------- ตารางต่อกลุ่ม ----------
    groups = {}
    for web, m in machines.items():
        groups.setdefault(m["group"], []).append(web)
    snap = {"time": now, "machines": {}}
    tables = []
    for g in sorted(groups):
        webs = sorted(groups[g], key=lambda w: (-{"DOWN": 9, "UNKNOWN": 5, "PAUSE": -1}.get(machines[w]["state"], 0)
                                                - RANK.get(machines[w]["worst"], 0), w))
        rows = []
        cpu_now, mem_now, disk_now, cpu_pk, mem_pk, disk_pk = [], [], [], [], [], []
        for web in webs:
            m, x, p = machines[web], extra.get(web) or {}, prev_m.get(web) or {}
            icon = {"DOWN": "🔴", "PAUSE": "⏸", "UNKNOWN": "❔"}.get(m["state"]) or ICON.get(m["worst"], "🟢")
            name_cell = [f"{icon} {web}", m["state"]] + m["other"][:3]
            if m["state"] in ("DOWN", "PAUSE", "UNKNOWN") and m["cpu"] is None:
                rows.append(_row([name_cell, ["-"], ["-"], ["-"], ["-"]]))
                continue
            cpu, mem = m["cpu"] or (None, None), m["mem"] or (None, None)
            disks = []
            for mt in sorted(m["disks"]):
                v, st = m["disks"][mt]
                if _pct(v) is None:
                    continue
                pk = (x.get("disk_max") or {}).get(mt)
                t = f"{mt} {_pct(v)}{_mark(st)}"
                if _f(pk) is not None and _f(pk) - _f(v) >= 1:
                    t += f" (สูงสุด {_pct(pk)})"
                if prev_label and (p.get("disks") or {}).get(mt) is not None:
                    t += f" {_delta(v, p['disks'][mt])}"
                disks.append(t)
                disk_now.append((v, f"{web} {mt}"))
                disk_pk.append((pk if _f(pk) is not None else v, f"{web} {mt}"))
            io = [f"{_pct(x['io_util'])}"] if _pct(x.get("io_util")) else ["-"]
            if _pct(x.get("io_max")):
                io.append(f"สูงสุด {_pct(x['io_max'])}")
            rows.append(_row([name_cell,
                              _value_cell(cpu[0], cpu[1], x.get("cpu_max"), p.get("cpu"), prev_label),
                              _value_cell(mem[0], mem[1], x.get("mem_max"), p.get("mem"), prev_label),
                              disks or ["-"], io]))
            cpu_now.append((cpu[0], web))
            mem_now.append((mem[0], web))
            cpu_pk.append((x.get("cpu_max") if _f(x.get("cpu_max")) is not None else cpu[0], web))
            mem_pk.append((x.get("mem_max") if _f(x.get("mem_max")) is not None else mem[0], web))
            snap["machines"][web] = {"cpu": _f(cpu[0]), "mem": _f(mem[0]),
                                     "disks": {mt: _f(v) for mt, (v, _) in m["disks"].items()}}
        gname = g or "ไม่ระบุกลุ่ม"
        head_g = [_txt(f"กลุ่ม {gname} · {len(webs)} เครื่อง", weight="Bolder", size="Medium", separator=True,
                       spacing="Large"),
                  {"type": "FactSet", "spacing": "Small", "facts": [
                      {"title": "CPU สูงสุด:", "value": f"{_max_of(cpu_now)} · ช่วง {period}: {_max_of(cpu_pk)}"},
                      {"title": "Memory สูงสุด:", "value": f"{_max_of(mem_now)} · ช่วง {period}: {_max_of(mem_pk)}"},
                      {"title": "Disk สูงสุด:", "value": f"{_max_of(disk_now)} · ช่วง {period}: {_max_of(disk_pk)}"}]},
                  _row([["Machine"], ["CPU"], ["RAM"], ["Disk (ต่อไดรฟ์)"], ["Disk I/O"]], header=True)]
        tables.append((head_g, rows))
    if not machines:
        tables.append(([_txt("ยังไม่มีเครื่อง")], []))

    # ---------- รวมเป็นการ์ด (เกินขนาด -> แบ่งหลายการ์ด) ----------
    def card(body, part=""):
        t = title + part
        c = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
             "msteams": {"width": "Full"}, "fallbackText": summary_line, "body": body}
        return {"type": "message", "summary": summary_line if not part else t, "attachments": [
            {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": c}]}

    def size(body):
        return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))

    bodies, cur = [], head + inc_body
    for head_g, rows in tables:
        chunk = list(head_g)
        for r in rows:
            if size(cur + chunk + [r]) > SUMMARY_MAX_BYTES and (cur or len(chunk) > len(head_g)):
                if len(chunk) > len(head_g) or not cur:
                    bodies.append(cur + chunk)
                    cur, chunk = [], [head_g[0], head_g[-1]]          # การ์ดถัดไป: ชื่อกลุ่ม + หัวตาราง
                else:
                    bodies.append(cur)
                    cur = []
            chunk.append(r)
        if size(cur + chunk) > SUMMARY_MAX_BYTES and cur:
            bodies.append(cur)
            cur = []
        cur = cur + chunk
    bodies.append(cur)
    bodies = [b for b in bodies if b]
    if len(bodies) == 1:
        return [card(bodies[0])], snap
    out = []
    for n, b in enumerate(bodies, 1):
        if n > 1:
            b = [_txt(f"{title} (ต่อ {n}/{len(bodies)})", weight="Bolder", isSubtle=True)] + b
        out.append(card(b, f" ({n}/{len(bodies)})" if n > 1 else ""))
    return out, snap


def _period_start(times, anchor):
    """ต้นช่วงรายงาน = เวลาสรุปรอบก่อนหน้าของวันเดียวกัน (ไม่มี = 00:00)
    เช่น times 08:00, 17:00 -> รอบ 08:00 = 00:00–08:00 · รอบ 17:00 = 08:00–17:00"""
    day = datetime.fromtimestamp(anchor).astimezone()
    start = day.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    for t in times:
        h, m = _hhmm(t)
        at = day.replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
        if start < at < anchor - 60:
            start = at
    return start


def maybe_summary(sensors, incidents, state_file, request_file, config=None, extra_fn=None):
    """เรียกทุกรอบ sensor (30 วินาที): ถึงเวลาที่ตั้ง หรือมีคำขอ "ส่งสรุปตอนนี้" จากหน้าเว็บ -> ส่งการ์ดสรุป
    extra_fn(start, end) -> ค่าสูงสุดในช่วงต่อเครื่อง (ถาม Prometheus เฉพาะตอนจะส่ง)"""
    s = settings(config)
    if not s["url"].startswith("https://"):
        return
    now = time.time()
    forced = os.path.exists(request_file)
    if forced:
        try:
            os.remove(request_file)
        except OSError:
            pass
    try:
        with open(state_file) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    due, due_ts = None, None
    if s["enabled"] and s["summary_times"]:
        today = datetime.fromtimestamp(now).astimezone()
        for t in s["summary_times"]:
            h, m = _hhmm(t)
            at = today.replace(hour=h, minute=m, second=0, microsecond=0)
            key = at.strftime("%Y-%m-%d %H:%M")
            if 0 <= now - at.timestamp() <= SUMMARY_LATE and key > state.get("last", ""):
                due, due_ts = key, at.timestamp()
    if not forced and not due:
        return
    start = _period_start(s["summary_times"], due_ts or now)
    prev = state.get("snap")
    same_day = prev and datetime.fromtimestamp(prev.get("time") or 0).date() == datetime.fromtimestamp(now).date()
    try:
        extra = extra_fn(start, now) if extra_fn else None
    except Exception as e:
        print(f"[NOTIFY ERROR] summary extra: {e!r}")
        extra = None
    label = None if due else "ส่งตอนนี้"
    payloads, snap = build_summary(sensors, incidents, now, extra, start, prev if same_day else None, label)
    if due:                       # จำเฉพาะรอบที่ตั้งเวลาไว้ (กด "ส่งตอนนี้" ไม่เปลี่ยนค่าที่ใช้เทียบ)
        tmp = state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"last": due, "snap": snap}, f)
        os.replace(tmp, state_file)
    for n, p in enumerate(payloads, 1):
        _enqueue(s["url"], p, f"daily summary {n}/{len(payloads)}" + (" (manual)" if forced else ""))


SOFTWARE_MAX = 15


def software_event(web, group, changes, config=None):
    """ติดตั้ง / ถอน / อัปเดตโปรแกรม (software_watch) -> การ์ดเดียวต่อเครื่องต่อรอบ"""
    s = settings(config)
    if not s["enabled"] or "software" not in s["levels"] or not changes:
        return
    label = {"installed": "ติดตั้ง", "removed": "ถอน", "updated": "อัปเดต"}
    facts = [("Device", _device(web, "", group))]
    for a, name, arch, ov, nv in changes[:SOFTWARE_MAX]:
        facts.append((label[a], f"{name} {ov} → {nv}" if a == "updated" else f"{name} {nv or ov}".strip()))
    if len(changes) > SOFTWARE_MAX:
        facts.append(("อื่นๆ", f"อีก {len(changes) - SOFTWARE_MAX} รายการ (ดูแท็บ Software ของเครื่อง)"))
    facts.append(("Time", _time_full()))
    n = len(changes)
    first = changes[0]
    summary = (f"💿 {web} · {label[first[0]]} {first[1]}" + (f" และอีก {n - 1} รายการ" if n > 1 else ""))
    _enqueue(s["url"], fact_card(f"{CARD_PREFIX}: {web} [Software] {n} change{'s' if n > 1 else ''}", "software",
                                 facts, summary), f"software {web} {n}")


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
    facts = [("Device", _device(name, "", group)), ("Sensor", "SSL certificate" if ssl_.get("days_left") is not None
                                                     and level == "warning" else "Web check"),
             ("Status", f"{LABEL.get(level, level)} – {text}"), ("URL", url),
             ("Last Value", f"HTTP {result['code']}" if result.get("code") else ""), ("Time", _time_full())]
    _enqueue(s["url"], fact_card(f"{CARD_PREFIX}: {name} [Web check] {LABEL.get(level, level)}", level, facts,
                                 f"{ICON.get(level, '')} {text}"), f"check {name} {new}")
