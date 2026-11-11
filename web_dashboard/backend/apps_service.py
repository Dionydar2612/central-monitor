"""
apps_service.py - หน้า Apps & Web: โปรแกรม / เว็บ / พอร์ต ยังทำงานไหม (มุมมองแบบง่ายของ Custom alert rules)
=============================================================================================================

แต่ละรายการ = กฎใน puller.yaml alert_rules (metric process / service / http_url / tcp_port / port_listen)
สถานะมาจาก sensor ชนิด custom ที่ puller ตรวจทุก 30 วินาที (data/state/sensors.json)
    GET    /api/apps[?web=]      รายการ + สรุป
    POST   /api/apps             {type: program|web|port|service, web, value, name?, local?}  -> สร้างกฎ (admin)
    DELETE /api/apps/<rule id>   ลบกฎ (admin)
"""

import urllib.parse

import alert_rules_service
import sensors_service
import targets_service

TYPE_OF = {"process": "program", "service": "service", "http_url": "web", "http_local": "web", "tcp_port": "port",
           "port_listen": "port", "docker": "docker"}
ORDER = {"error": 0, "down": 0, "warning": 1, "unknown": 2, "nodata": 3, "paused": 4, "ok": 5}


def _rules():
    rules = targets_service._plain(alert_rules_service._doc().get("alert_rules") or [])
    return [alert_rules_service.migrate_rule(r) for r in rules if isinstance(r, dict) and r.get("metric") in TYPE_OF]


def _what(r):
    m = r["metric"]
    if m == "process":
        return r.get("process") or ""
    if m == "service":
        return r.get("service") or ""
    if m in ("http_url", "http_local"):
        return r.get("url") or ""
    if m == "docker":
        return r.get("container") or ""
    return f"{'บนเครื่อง' if m == 'port_listen' else 'จาก central'} :{r.get('port')}"


# ข้อมูลที่ต้องมีจากเครื่อง Windows (agent) ของแต่ละประเภท -> ไม่มี = บอกให้ติดตั้ง agent ซ้ำ
_NEEDS = {"http_local": ("cm_probe_timestamp_seconds", "ตัวตรวจเว็บบนเครื่อง (cm-probe)"),
          "docker": ("cm_probe_timestamp_seconds", "ตัวตรวจ Docker (cm-probe)"),
          "port_listen": ("cm_inventory_timestamp_seconds", "รายการพอร์ต (cm-inventory)"),
          "process": ("windows_process_working_set_private_bytes", "ข้อมูล process (windows_exporter)")}


def _why_nodata(rule, targets):
    """เหตุผลที่ยังไม่มีสถานะ (แทนข้อความกว้าง ๆ)"""
    generic = "รอรอบตรวจแรก (~30 วินาที)"
    try:
        alltargets = {t.get("name"): t for t in targets_service._plain(alert_rules_service._doc().get("targets") or [])
                      if isinstance(t, dict)}
        m = rule.get("metric")
        for name in targets or []:
            t = alltargets.get(name)
            if not t:
                return f"ไม่พบเครื่อง {name} ในหน้า Settings"
            if t.get("paused"):
                return f"เครื่อง {name} ถูก Pause อยู่"
            agent = str(t.get("type") or "ssh") == "agent"
            if m in ("http_url", "tcp_port") and "{host}" in str(rule.get("url") or "{host}") and not t.get("host"):
                return f"เครื่อง {name} ยังไม่มี Host (IP) ในหน้า Settings"
            if agent and m in _NEEDS:
                metric, what = _NEEDS[m]
                from clients import prom_value
                if prom_value(f'count({metric}{{cm_web="{name}"}})') is None:
                    return (f"เครื่อง {name} ยังไม่ส่ง{what} · รันคำสั่งติดตั้ง agent ซ้ำบนเครื่อง "
                            f"(Settings > เครื่อง > Download zip + Make command) แล้วรอ 1-2 นาที")
            if not agent and m in ("docker", "http_local"):
                return (f"central ยังตรวจผ่าน SSH ไม่ได้ (เครื่องต้องมี curl / user SSH ต้องใช้ docker ได้) หรือ {generic}")
    except Exception as e:
        print(f"[APPS] why {rule.get('id')}: {e!r}")
    return generic


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
                          "message": "ปิดอยู่" if not base["enabled"] else _why_nodata(r, targets),
                          "since": None, "value": None, "unit": ""})
    items.sort(key=lambda x: (ORDER.get(x["status"], 9), x["name"].lower(), x["web"] or ""))
    bad = sum(1 for x in items if x["status"] in ("error", "down", "warning"))
    # หมายเหตุ: รายการ "รอข้อมูล" ไม่นับเป็นทำงานปกติ
    ok = sum(1 for x in items if x["status"] == "ok")
    return {"items": items, "summary": {"total": len(items), "ok": ok, "problem": bad, "other": len(items) - ok - bad}}


def _build(data, keep=None):
    """ฟอร์มแบบง่าย -> กฎ (เติมจากกฎเดิม keep ถ้าแก้)  -> (rule, errors)"""
    data = data or {}
    typ = str(data.get("type") or "")
    web = str(data.get("web") or "").strip()
    value = str(data.get("value") or "").strip()
    errors = {}
    names = {t.get("name") for t in targets_service._plain(alert_rules_service._doc().get("targets") or [])
             if isinstance(t, dict)}
    if web not in names:
        errors["web"] = "เลือกเครื่อง"
    rule = dict(keep or {"enabled": True, "groups": [], "os": "any", "level": "error", "for_minutes": 2, "notify": True,
                         "message": ""})
    for k in ("process", "service", "url", "port", "container", "op", "warning", "error"):
        rule.pop(k, None)
    rule["targets"] = [web]
    if keep is None:
        rule["notify"] = data.get("notify", True) is not False
    local = data.get("local", True) is not False
    if typ == "program":
        rule.update(metric="process", process=value[:-4] if value.lower().endswith(".exe") else value)
        default = rule["process"]
    elif typ == "service":
        rule.update(metric="service", service=value)
        default = value
    elif typ == "docker":
        rule.update(metric="docker", container=value)
        default = f"docker {value}"
    elif typ == "web":
        # โหมดเลือกจาก URL: localhost / 127.0.0.1 / ::1 = บนเครื่อง · อื่นๆ = จาก central (ไม่มีตัวเลือกในฟอร์มแล้ว)
        value = alert_rules_service.normalize_url(value)
        rule.update(metric=alert_rules_service.web_metric(value), url=value)
        u = urllib.parse.urlsplit(value.replace("{host}", web))
        default = (u.netloc + u.path.rstrip("/")) or value
    elif typ == "port":
        rule.update(metric="port_listen" if local else "tcp_port", port=value)
        default = f"พอร์ต {value}"
    else:
        return None, {"type": "เลือกประเภท"}
    rule.setdefault("level", "error")
    name = str(data.get("name") or "").strip() or f"{default} ({web})"
    rule["name"] = name[:60]
    new, errs = alert_rules_service.clean_rule(rule, keep_id=(keep or {}).get("id"))
    for k, v in errs.items():
        errors["value" if k in ("process", "service", "url", "port", "container") else k] = v
    return rule, errors


def add_app(data):
    """สร้างกฎจากฟอร์มแบบง่าย -> (ok, ผล | {error, fields})"""
    rule, errors = _build(data)
    if rule is None:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
    if errors:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
    current = alert_rules_service.list_rules()["rules"]           # teams_url ถูกซ่อน -> save_rules คงค่าเดิมให้
    ok, out = alert_rules_service.save_rules({"rules": current + [rule]})
    if not ok:
        return False, out
    return True, {"rule": out["rules"][-1]}


def update_app(rule_id, data):
    """แก้รายการ (ประเภท / เครื่อง / ค่า / ชื่อ) คงค่าอื่นของกฎเดิม (ระดับ, เวลา, Teams, ข้อความ)"""
    current = alert_rules_service.list_rules()["rules"]
    idx = next((i for i, r in enumerate(current) if r.get("id") == rule_id), None)
    if idx is None:
        return False, {"error": "ไม่พบรายการนี้"}
    old = {k: v for k, v in current[idx].items() if k not in ("teams_url_set", "teams_url_hint")}
    rule, errors = _build(data, keep=old)
    if rule is None or errors:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
    current[idx] = rule
    ok, out = alert_rules_service.save_rules({"rules": current})
    if not ok:
        return False, out
    return True, {"rule": next((r for r in out["rules"] if r.get("id") == rule_id), rule)}


def delete_app(rule_id):
    current = alert_rules_service.list_rules()["rules"]
    left = [r for r in current if r.get("id") != rule_id]
    if len(left) == len(current):
        return False, {"error": "ไม่พบรายการนี้"}
    return alert_rules_service.save_rules({"rules": left})


def suggest(web, typ):
    """ชื่อที่เลือกได้ในฟอร์ม: โปรแกรมที่ทำงาน / พอร์ตที่เปิด / container / service ของเครื่องนี้"""
    import host_details
    import system_state
    out = []
    try:
        if typ in ("program", "port", "docker", "web"):
            d = host_details.software(web) or {}
            if typ == "program":
                out = [p["name"] for p in d.get("running") or []]
            elif typ == "port":
                out = [str(p["port"]) for p in d.get("ports") or []]
            elif typ == "docker":
                out = [c["name"] for c in d.get("containers") or []]
            else:
                out = [f"http://localhost:{w['port']}" for w in d.get("local_http") or [] if w.get("code")]
        if typ == "service":
            snap = system_state.load_system_snapshot(web)
            out = [s.get("name") for s in (snap or {}).get("services") or [] if s.get("name")]
            if not out:                                  # Windows: จาก windows_exporter
                from clients import prom_vector
                labels, win = host_details._context(web)
                if labels and win:
                    out = [m.get("name") for m, _ in prom_vector(f"windows_service_state{{{labels}}} == 1") if m.get("name")]
    except Exception as e:
        print(f"[APPS] suggest {web} {typ}: {e!r}")
    return sorted({str(x) for x in out if x}, key=str.lower)[:300]
