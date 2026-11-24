"""
apps_service.py - หน้า Apps & Web: โปรแกรม / เว็บ / พอร์ต ยังทำงานไหม (มุมมองแบบง่ายของ Custom alert rules)
=============================================================================================================

แต่ละรายการ = กฎใน puller.yaml alert_rules (metric http_url / http_local / tcp_port / docker)
สถานะมาจาก sensor ชนิด custom ที่ puller ตรวจทุก 30 วินาที (data/state/sensors.json)
    GET    /api/apps[?web=]      รายการ + สรุป
    POST   /api/apps             {type: web|port|docker, web, value, name?}  -> สร้างกฎ (admin)
    DELETE /api/apps/<rule id>   ลบกฎ (admin)
"""

import urllib.parse

import alert_rules_service
import sensors_service
import targets_service

TYPE_OF = {"http_url": "web", "http_local": "web", "tcp_port": "port", "docker": "docker"}
ORDER = {"error": 0, "down": 0, "warning": 1, "unknown": 2, "nodata": 3, "paused": 4, "ok": 5}


def _rules():
    rules = targets_service._plain(alert_rules_service._doc().get("alert_rules") or [])
    return [alert_rules_service.migrate_rule(r) for r in rules if isinstance(r, dict) and r.get("metric") in TYPE_OF]


def _what(r):
    m = r["metric"]
    if m in ("http_url", "http_local"):
        return r.get("url") or ""
    if m == "docker":
        return r.get("container") or ""
    return f"จาก central :{r.get('port')}"


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
            if m in ("http_url", "tcp_port") and "{host}" in str(rule.get("url") or "{host}") and not t.get("host"):
                return f"เครื่อง {name} ยังไม่มี Host (IP) ในหน้า Settings"
            if m in ("docker", "http_local"):
                return (f"central ยังตรวจผ่าน SSH ไม่ได้ (เครื่องต้องมี curl / user SSH ต้องใช้ docker ได้) หรือ {generic}")
    except Exception as e:
        print(f"[APPS] why {rule.get('id')}: {e!r}")
    return generic


# ---------------------------------------------------------------------------
# Apps & Web: uptime + กราฟ (จาก cm_app_* ที่ puller ส่งให้ Prometheus ทุก 30 วินาที) ทุกประเภท
#   uptime = สัดส่วนเวลาที่ตรวจแล้วตอบ 2xx / 3xx ของเว็บนั้นเอง (ไม่รวมกับเว็บอื่น)
# ---------------------------------------------------------------------------

def _key(m):
    return (m.get("rule"), m.get("web"))


def _web_stats(items):
    if not items:
        return
    from clients import prom_vector, prometheus_query_range
    import time as _t
    stats = {}
    try:
        for m, v in prom_vector("avg_over_time(cm_app_up[24h])"):
            stats.setdefault(_key(m), {})["uptime_24h"] = round(v * 100, 2)
        end = _t.time()
        res = prometheus_query_range("cm_app_response_ms", end - 3600, end, 120)["data"]["result"]
        for row in res:
            pts = [round(float(v)) for _t2, v in row.get("values") or [] if v not in ("NaN", "+Inf", "-Inf")]
            stats.setdefault(_key(row.get("metric") or {}), {})["spark"] = pts
    except Exception as e:
        print(f"[APPS] web stats: {e!r}")
    for x in items:
        st = stats.get((x["rule"], x["web"])) or {}
        x["uptime_24h"] = st.get("uptime_24h")
        x["spark"] = st.get("spark") or []


RANGES = {"1h": (3600, 30), "24h": (86400, 300), "7d": (7 * 86400, 1800), "30d": (30 * 86400, 7200)}


def detail(rule_id, web, range_key="24h"):
    """หน้ารายละเอียดของ Monitor เว็บ: สถานะ + กราฟเวลาตอบ / ขึ้น-ลง + uptime 24 ชม. / 7 วัน / 30 วัน + ประวัติปัญหา"""
    from clients import prom_value, prometheus_query_range
    import time as _t
    items = [x for x in list_apps(web, with_stats=True)["items"] if x["rule"] == rule_id and (not web or x["web"] == web)]
    if not items:
        return None
    item = items[0]
    sel = f'rule="{rule_id}",web="{item["web"]}"'
    seconds, step = RANGES.get(range_key, RANGES["24h"])
    end = _t.time()
    out = {"item": item, "range": range_key, "series": {}, "uptime": {}, "incidents": []}

    def rng(q):
        try:
            res = prometheus_query_range(q, end - seconds, end, step)["data"]["result"]
            return [[float(t), round(float(v), 2)] for t, v in (res[0]["values"] if res else [])
                    if v not in ("NaN", "+Inf", "-Inf")]
        except Exception as e:
            print(f"[APPS] detail {q}: {e!r}")
            return []
    out["series"]["ms"] = rng(f"cm_app_response_ms{{{sel}}}")
    out["series"]["up"] = rng(f"min_over_time(cm_app_up{{{sel}}}[{step}s])")
    out["series"]["code"] = rng(f"max_over_time(cm_app_http_code{{{sel}}}[{step}s])")
    for k, w in (("h24", "24h"), ("d7", "7d"), ("d30", "30d")):
        try:
            v = prom_value(f"avg_over_time(cm_app_up{{{sel}}}[{w}])")
            out["uptime"][k] = None if v is None else round(v * 100, 2)
        except Exception:
            out["uptime"][k] = None
    try:
        mx = prom_value(f"max_over_time(cm_app_response_ms{{{sel}}}[{range_key}])")
        out["max_ms"] = None if mx is None else round(mx)
    except Exception:
        out["max_ms"] = None
    out["incidents"] = [i for i in sensors_service.get_incidents(hours=24 * 30, web=item["web"]) if i.get("id") == item.get("id")][:50]
    try:
        out.update(_more(item, sel, range_key))
    except Exception as e:
        print(f"[APPS] more {rule_id}: {e!r}")
    return out


# ---------------------------------------------------------------------------
# รายละเอียดเพิ่ม (หน้าต่างรายละเอียดใน /apps): ข้อมูลการตรวจ + ข้อมูลเฉพาะประเภท + เหตุการณ์ล่าสุด
#   facts  = [[หัวข้อ, ค่า], ...]   tables = [{"title", "head": [...], "rows": [[...], ...]}]
# ---------------------------------------------------------------------------

def _bytes(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "-"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def _more(item, sel, range_key):
    from clients import prom_value
    import re as _re
    import time as _t
    sensors = sensors_service.load_sensors()
    sn = next((x for x in sensors.get("sensors", []) if x.get("id") == item.get("id")), {}) or {}
    d = sn.get("details") or {}
    web, m = item["web"], item["metric"]
    facts, tables = [], []

    # --- การตรวจ (ทุกประเภท) ---
    checks = prom_value(f"count_over_time(cm_app_up{{{sel}}}[{range_key}])")
    good = prom_value(f"sum_over_time(cm_app_up{{{sel}}}[{range_key}])")
    if checks:
        facts.append([f"ตรวจ ({range_key})", f"{int(checks)} ครั้ง · ไม่ผ่าน {int(checks - (good or 0))} ครั้ง"])
    upd = sensors.get("updated_at")
    if upd:
        facts.append(["ตรวจล่าสุด", f"{max(0, int(_t.time() - float(upd)))} วินาทีที่แล้ว (ทุก {(sensors.get('options') or {}).get('interval', 30)} วินาที)"])
    facts.append(["วิธีตรวจ", {"http_url": "central เรียก URL", "tcp_port": "central ต่อพอร์ต", "http_local": "เครื่องเรียก localhost (SSH)",
                               "docker": "docker ps บนเครื่อง (SSH)"}.get(m, m)])
    if sn.get("query"):
        facts.append(["ที่มา", str(sn["query"])[:160]])
    if sn.get("priority"):
        facts.append(["Priority", "★" * int(sn["priority"])])

    # --- เฉพาะประเภท ---
    if m == "docker":
        c = d.get("container") or {}
        for k, label in (("image", "Image"), ("state", "State"), ("status", "Status"), ("id", "Container ID"),
                         ("running_for", "สร้างเมื่อ"), ("ports", "Ports"), ("networks", "Networks"), ("mounts", "Mounts"),
                         ("command", "Command")):
            if c.get(k):
                facts.append([label, str(c[k])])
        st = c.get("stats") or {}
        if st:
            tables.append({"title": "การใช้ทรัพยากร (ตอนตรวจล่าสุด)", "head": ["CPU", "RAM", "RAM %", "Network I/O", "Disk I/O", "Process"],
                           "rows": [[st.get("cpu") or "-", st.get("mem") or "-", st.get("mem_pct") or "-", st.get("net") or "-",
                                     st.get("block") or "-", st.get("pids") or "-"]]})
    if m in ("tcp_port", "http_local", "http_url"):
        import netsec_service
        port = None
        mt = _re.search(r":(\d+)", str(item["what"]))
        if mt:
            port = int(mt.group(1))
        elif m in ("http_url", "http_local"):
            port = 443 if str(item["what"]).startswith("https") else 80
        ns = netsec_service.get_machine(web) if port else None
        if ns:
            who = [x for x in ns.get("listening") or [] if x.get("port") == port]
            if who:
                facts.append(["เปิดรับโดย", ", ".join(sorted({f"{x.get('process') or '?'} ({x.get('address')})" for x in who}))])
            conns = [c for c in ns.get("connections") or [] if c.get("dir") == "in" and c.get("port") == port]
            if conns:
                tables.append({"title": f"ใครต่อเข้าพอร์ต {port} อยู่", "head": ["ต้นทาง", "เครื่อง", "จำนวน"],
                               "rows": [[c.get("peer"), c.get("peer_web") or ("IP สาธารณะ" if c.get("external") else "-"), c.get("count")] for c in conns[:20]]})
    if m in ("http_url", "http_local"):
        mn = prom_value(f"min_over_time(cm_app_response_ms{{{sel}}}[{range_key}])")
        if mn is not None:
            facts.append([f"เร็วสุด ({range_key})", f"{round(mn)} ms"])
        if item.get("ssl_days_left") is not None:
            facts.append(["SSL เหลือ", f"{item['ssl_days_left']} วัน"])

    # --- เหตุการณ์ล่าสุดของรายการนี้ (System log) ---
    import audit_service
    ev = [e for e in audit_service.read_entries("system", limit=200, query=item.get("id") or "") if e.get("sid") == item.get("id")][:10]
    if ev:
        tables.append({"title": "เหตุการณ์ล่าสุด", "head": ["เวลา", "จาก → เป็น", "ข้อความ"], "time_col": 0,
                       "rows": [[e.get("time"), f"{e.get('from') or '-'} → {e.get('to') or e.get('level')}",
                                 str(e.get("message") or "").split(" · ", 1)[-1][:200]] for e in ev]})
    return {"facts": facts, "tables": tables}


def bulk_add(data):
    """เฝ้าดูหลายเว็บบนเครื่องเดียวทีเดียว: {web, ports: [8090, 9090]} -> http://localhost:<port> (ข้ามที่มีอยู่แล้ว)"""
    data = data or {}
    web = str(data.get("web") or "").strip()
    ports = []
    for p in data.get("ports") or []:
        try:
            p = int(p)
        except (TypeError, ValueError):
            continue
        if 1 <= p <= 65535 and p not in ports:
            ports.append(p)
    if not ports:
        return False, {"error": "เลือกพอร์ตอย่างน้อย 1 พอร์ต"}
    have = {(r.get("url"), tuple(r.get("targets") or [])) for r in _rules()}
    current = alert_rules_service.list_rules()["rules"]
    added, skipped = [], []
    for p in ports[:50]:
        url = f"http://localhost:{p}"
        if (url, (web,)) in have:
            skipped.append(p)
            continue
        rule, errors = _build({"type": "web", "web": web, "value": url, "name": f"เว็บ :{p} ({web})"})
        if rule is None or errors:
            return False, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
        current.append(rule)
        added.append(p)
    if added:
        ok, out = alert_rules_service.save_rules({"rules": current})
        if not ok:
            return False, out
    return True, {"added": added, "skipped": skipped}


def list_apps(web=None, with_stats=False):
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
        if base["type"] == "web":
            base.update({k: r.get(k) for k in ("keyword", "slow_ms", "ssl_days", "for_minutes", "level") if r.get(k) is not None})
        for s in sens:
            d = s.get("details") or {}
            items.append({**base, "id": s.get("id"), "web": s.get("web"), "group": s.get("group") or "",
                          "status": s.get("status") or "unknown", "message": s.get("message") or "",
                          "since": s.get("since"), "value": s.get("value"), "unit": s.get("unit") or "",
                          "code": d.get("code"), "ms": d.get("ms"), "ssl_days_left": d.get("ssl_days_left")})
        if not sens:
            targets = r.get("targets") or []
            if web and targets and web not in targets:
                continue
            items.append({**base, "id": None, "web": ", ".join(targets) or "-", "group": "",
                          "status": "paused" if not base["enabled"] else "nodata",
                          "message": "ปิดอยู่" if not base["enabled"] else _why_nodata(r, targets),
                          "since": None, "value": None, "unit": ""})
    items.sort(key=lambda x: (ORDER.get(x["status"], 9), x["name"].lower(), x["web"] or ""))
    if with_stats:
        _web_stats([x for x in items if x.get("id")])
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
    for k in ("process", "service", "url", "port", "container", "op", "warning", "error"):      # process / service = แบบเดิม
        rule.pop(k, None)
    if typ != "web":
        for k in ("keyword", "slow_ms", "ssl_days"):
            rule.pop(k, None)
    rule["targets"] = [web]
    if keep is None:
        rule["notify"] = data.get("notify", True) is not False
    if typ == "docker":
        rule.update(metric="docker", container=value)
        default = f"docker {value}"
    elif typ == "web":
        # โหมดเลือกจาก URL: localhost / 127.0.0.1 / ::1 = บนเครื่อง · อื่นๆ = จาก central (ไม่มีตัวเลือกในฟอร์มแล้ว)
        value = alert_rules_service.normalize_url(value)
        rule.update(metric=alert_rules_service.web_metric(value), url=value)
        for k in ("keyword", "slow_ms", "ssl_days"):              # ตั้งค่าเพิ่มเติม (ไม่ส่งมา = คงค่าเดิมตอนแก้ไข)
            if k in data:
                rule.pop(k, None)
                if str(data.get(k) or "").strip():
                    rule[k] = data[k]
        if str(data.get("for_minutes") or "").strip():
            rule["for_minutes"] = data["for_minutes"]
        u = urllib.parse.urlsplit(value.replace("{host}", web))
        default = (u.netloc + u.path.rstrip("/")) or value
    elif typ == "port":
        rule.update(metric="tcp_port", port=value)
        default = f"พอร์ต {value}"
    else:
        return None, {"type": "เลือกประเภท"}
    rule.setdefault("level", "error")
    name = str(data.get("name") or "").strip() or f"{default} ({web})"
    rule["name"] = name[:60]
    new, errs = alert_rules_service.clean_rule(rule, keep_id=(keep or {}).get("id"))
    for k, v in errs.items():
        errors["value" if k in ("url", "port", "container") else k] = v
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
    """ชื่อที่เลือกได้ในฟอร์ม: พอร์ตที่เครื่องเปิดรับ (จากหน้า Network ของเครื่องนี้)"""
    out = []
    try:
        if typ == "port":
            import netsec_service
            ns = netsec_service.get_machine(web) or {}
            out = [str(x.get("port")) for x in ns.get("listening") or [] if not x.get("local_only")]
    except Exception as e:
        print(f"[APPS] suggest {web} {typ}: {e!r}")
    return sorted({str(x) for x in out if x}, key=str.lower)[:300]
