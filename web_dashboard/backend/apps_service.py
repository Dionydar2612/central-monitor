"""
apps_service.py - หน้า Apps & Web: โปรแกรม / เว็บ / พอร์ต ยังทำงานไหม (มุมมองแบบง่ายของ Custom alert rules)
=============================================================================================================

แต่ละรายการ = กฎใน puller.yaml alert_rules (metric process / service / http_url / tcp_port / port_listen)
สถานะมาจาก sensor ชนิด custom ที่ puller ตรวจทุก 30 วินาที (data/state/sensors.json)
    GET    /api/apps[?web=]      รายการ + สรุป
    POST   /api/apps             {type: program|web|port|service, web, value, name?, local?}  -> สร้างกฎ (admin)
    DELETE /api/apps/<rule id>   ลบกฎ (admin)
"""

import re
import urllib.parse

import alert_rules_service
import sensors_service
import targets_service

TYPE_OF = {"process": "program", "service": "service", "http_url": "web", "tcp_port": "port", "port_listen": "port"}
ORDER = {"error": 0, "down": 0, "warning": 1, "unknown": 2, "nodata": 3, "paused": 4, "ok": 5}


def _rules():
    rules = targets_service._plain(alert_rules_service._doc().get("alert_rules") or [])
    return [r for r in rules if isinstance(r, dict) and r.get("metric") in TYPE_OF]


def _what(r):
    m = r["metric"]
    if m == "process":
        return r.get("process") or ""
    if m == "service":
        return r.get("service") or ""
    if m == "http_url":
        return r.get("url") or ""
    return f"{'บนเครื่อง' if m == 'port_listen' else 'จาก central'} :{r.get('port')}"


def list_apps(web=None):
    rules = _rules()
    by_rule = {}
    for s in sensors_service.load_sensors().get("sensors", []):
        rid = (s.get("details") or {}).get("rule") if s.get("kind") == "custom" else None
        if rid:
            by_rule.setdefault(rid, []).append(s)
    items = []
    for r in rules:
        base = {"rule": r["id"], "name": r.get("name") or r["id"], "type": TYPE_OF[r["metric"]], "metric": r["metric"],
                "what": _what(r), "enabled": r.get("enabled", True) is not False}
        sens = by_rule.get(r["id"]) or []
        if web:
            sens = [s for s in sens if s.get("web") == web]
        for s in sens:
            items.append({**base, "id": s.get("id"), "web": s.get("web"), "group": s.get("group") or "",
                          "status": s.get("status") or "unknown", "message": s.get("message") or "",
                          "since": s.get("since"), "value": s.get("value"), "unit": s.get("unit") or ""})
        if not sens:
            targets = r.get("targets") or []
            if web and targets and web not in targets:
                continue
            items.append({**base, "id": None, "web": ", ".join(targets) or "-", "group": "",
                          "status": "paused" if not base["enabled"] else "nodata",
                          "message": "ปิดอยู่" if not base["enabled"] else "ยังไม่มีข้อมูล (รอรอบตรวจ ~30 วินาที หรือเครื่องยังไม่ส่งข้อมูลนี้)",
                          "since": None, "value": None, "unit": ""})
    items.sort(key=lambda x: (ORDER.get(x["status"], 9), x["name"].lower(), x["web"] or ""))
    bad = sum(1 for x in items if x["status"] in ("error", "down", "warning"))
    ok = sum(1 for x in items if x["status"] == "ok")
    return {"items": items, "summary": {"total": len(items), "ok": ok, "problem": bad, "other": len(items) - ok - bad}}


def add_app(data):
    """สร้างกฎจากฟอร์มแบบง่าย -> (ok, ผล | {error, fields})"""
    data = data or {}
    typ = str(data.get("type") or "")
    web = str(data.get("web") or "").strip()
    value = str(data.get("value") or "").strip()
    errors = {}
    names = {t.get("name"): t for t in targets_service._plain(alert_rules_service._doc().get("targets") or [])
             if isinstance(t, dict)}
    if web not in names:
        errors["web"] = "เลือกเครื่อง"
    rule = {"enabled": True, "targets": [web], "groups": [], "os": "any", "level": "error",
            "for_minutes": 2, "notify": data.get("notify", True) is not False, "message": ""}
    if typ == "program":
        rule.update(metric="process", process=value[:-4] if value.lower().endswith(".exe") else value)
        default = rule["process"]
    elif typ == "service":
        rule.update(metric="service", service=value)
        default = value
    elif typ == "web":
        if value and not re.match(r"^https?://", value):
            value = "http://" + value
        rule.update(metric="http_url", url=value)
        u = urllib.parse.urlsplit(value.replace("{host}", web))
        default = (u.netloc + u.path.rstrip("/")) or value
    elif typ == "port":
        rule.update(metric="port_listen" if data.get("local", True) is not False else "tcp_port", port=value)
        default = f"พอร์ต {value}"
    else:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": {"type": "เลือกประเภท"}}
    name = str(data.get("name") or "").strip() or f"{default} ({web})"
    rule["name"] = name[:60]
    new, errs = alert_rules_service.clean_rule(rule)
    for k, v in errs.items():
        errors["value" if k in ("process", "service", "url", "port") else k] = v
    if errors:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
    current = alert_rules_service.list_rules()["rules"]           # teams_url ถูกซ่อน -> save_rules คงค่าเดิมให้
    ok, out = alert_rules_service.save_rules({"rules": current + [rule]})
    if not ok:
        return False, out
    return True, {"rule": out["rules"][-1]}


def delete_app(rule_id):
    current = alert_rules_service.list_rules()["rules"]
    left = [r for r in current if r.get("id") != rule_id]
    if len(left) == len(current):
        return False, {"error": "ไม่พบรายการนี้"}
    return alert_rules_service.save_rules({"rules": left})
