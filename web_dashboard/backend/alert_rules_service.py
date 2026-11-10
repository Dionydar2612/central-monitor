"""
alert_rules_service.py - กฎแจ้งเตือนที่ผู้ใช้สร้างเอง (หน้า Settings > Custom alert rules)
==========================================================================================

เก็บใน config/puller.yaml -> alert_rules: [...]   ตัวตรวจจริง: start_push_log/puller/custom_rules.py
(รายชื่อ metric ด้านล่างต้องตรงกับ custom_rules.METRICS)
"""

import hashlib
import json
import re

import notify_service
import targets_service
from clients import puller_get

METRICS = {   # key: (ชื่อ, หน่วย, ต่อไดรฟ์?, ทิศปริยาย, OS)
    "cpu": ("CPU %", "%", False, ">", "any"),
    "memory": ("RAM ใช้ไป %", "%", False, ">", "any"),
    "mem_available": ("RAM ว่าง %", "%", False, "<", "any"),
    "disk": ("Disk ใช้ไป % (ต่อไดรฟ์ / mount)", "%", True, ">", "any"),
    "disk_free_gb": ("Disk ว่าง GB (ต่อไดรฟ์ / mount)", "GB", True, "<", "any"),
    "disk_latency": ("Disk latency ms (ต่อไดรฟ์ / disk)", "ms", True, ">", "any"),
    "io_util": ("Disk I/O utilization %", "%", False, ">", "any"),
    "cpu_queue": ("CPU queue ต่อ core", "", False, ">", "any"),
    "net_util": ("Network bandwidth %", "%", False, ">", "any"),
    "tcp_retx": ("TCP retransmit %", "%", False, ">", "any"),
    "packet_loss": ("Ping loss %", "%", False, ">", "any"),
    "net_latency": ("Ping latency ms", "ms", False, ">", "any"),
    "service": ("Service หยุด (Windows)", "", False, ">", "windows"),
    "process": ("โปรแกรมไม่ทำงาน (Windows)", "", False, ">", "windows"),
    "process_cpu": ("CPU % ของโปรแกรม (Windows)", "%", False, ">", "windows"),
    "process_mem": ("RAM ของโปรแกรม MB (Windows)", "MB", False, ">", "windows"),
    "port_listen": ("พอร์ตไม่เปิดรับบนเครื่อง (Windows)", "", False, ">", "windows"),
    "tcp_port": ("พอร์ตเชื่อมต่อจาก central ไม่ได้", "", False, ">", "any"),
    "http_url": ("เว็บของโปรแกรมไม่ตอบ (HTTP จาก central)", "", False, ">", "any"),
    "http_local": ("เว็บบนเครื่องไม่ตอบ (localhost)", "", False, ">", "any"),
    "docker": ("Docker container ไม่ทำงาน", "", False, ">", "any"),
    "promql": ("PromQL เอง", "", False, ">", "any"),
}
NAME_RE = re.compile(r"^[^\x00-\x1f]{1,60}$")
STATE_METRICS = ("service", "process", "port_listen", "tcp_port", "http_url", "http_local", "docker")
CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")   # ไม่มีเกณฑ์ตัวเลข ผิดปกติ = level
PROCESS_RE = re.compile(r"^[A-Za-z0-9_.+ ()-]{1,80}$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9_.$@ -]{1,80}$")
MOUNT_RE = re.compile(r"^[^\x00-\x1f\"']{1,120}$")


def _catalog():
    return [{"key": k, "label": v[0], "unit": v[1], "per_mount": v[2], "op": v[3], "os": v[4]} for k, v in METRICS.items()]


def _doc():
    y = targets_service._yaml()
    return targets_service._load_doc(y)


def list_rules():
    doc = _doc()
    rules = targets_service._plain(doc.get("alert_rules") or [])
    rules = rules if isinstance(rules, list) else []
    for r in rules:                              # webhook ของกฎเป็นความลับ: ไม่ส่ง URL เต็มกลับไป
        url = str(r.pop("teams_url", "") or "")
        r["teams_url_set"] = bool(url)
        r["teams_url_hint"] = notify_service._hint(url)
    targets = [{"name": t.get("name"), "group": t.get("group") or "", "host": t.get("host") or "",
                "os": "windows" if str(t.get("type") or "") == "agent" else "linux"}
               for t in targets_service._plain(doc.get("targets") or []) if isinstance(t, dict) and t.get("name")]
    groups = sorted({t["group"] for t in targets if t["group"]})
    return {"rules": rules, "metrics": _catalog(), "main_url_set": bool(notify_service.main_url()), "targets": targets, "groups": groups,
            "template_keys": ["icon", "machine", "group", "host", "sensor", "text", "value", "unit", "threshold",
                              "level", "time", "duration"]}


def _num(v, field, errors, lo=-1e12, hi=1e12):
    if v in (None, ""):
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        errors[field] = "ต้องเป็นตัวเลข"
        return None
    if not lo <= v <= hi:
        errors[field] = f"ต้องอยู่ระหว่าง {lo:g} - {hi:g}"
        return None
    return int(v) if v.is_integer() else v


def _slug(name):
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:30]
    return (base or "rule") + "-" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]


def clean_rule(data, existing_ids=(), keep_id=None, old_url=""):
    """ตรวจ + จัดรูปกฎ 1 ข้อ -> (rule, errors)"""
    data = data or {}
    errors = {}
    name = str(data.get("name") or "").strip()
    if not NAME_RE.match(name):
        errors["name"] = "ใส่ชื่อกฎ (ไม่เกิน 60 ตัวอักษร)"
    metric = str(data.get("metric") or "")
    if metric not in METRICS:
        errors["metric"] = "เลือก metric"
    meta = METRICS.get(metric, ("", "", False, ">", "any"))
    rule = {"id": keep_id or _slug(name or "rule"), "name": name, "enabled": data.get("enabled", True) is not False,
            "metric": metric}
    if keep_id is None:
        i, rid = 2, rule["id"]
        while rule["id"] in existing_ids:
            rule["id"] = f"{rid}-{i}"
            i += 1

    if meta[2]:
        m = str(data.get("mount") or "").strip()
        if m and not MOUNT_RE.match(m):
            errors["mount"] = "ชื่อไดรฟ์ / mount ไม่ถูกต้อง"
        rule["mount"] = m
    if metric == "service":
        sv = str(data.get("service") or "").strip()
        if not SERVICE_RE.match(sv):
            errors["service"] = "ใส่ชื่อ service (เช่น W3SVC, MSSQLSERVER)"
        rule["service"] = sv
    if metric in ("process", "process_cpu", "process_mem"):
        pr = str(data.get("process") or "").strip()
        if pr.lower().endswith(".exe"):
            pr = pr[:-4]
        if not PROCESS_RE.match(pr):
            errors["process"] = "ใส่ชื่อโปรแกรมตามที่เห็นในแท็บ Processes (เช่น sqlservr, java, w3wp)"
        rule["process"] = pr
    if metric in ("port_listen", "tcp_port"):
        port = _num(data.get("port"), "port", errors, 1, 65535)
        if port is None and "port" not in errors:
            errors["port"] = "ใส่หมายเลขพอร์ต 1 - 65535"
        rule["port"] = int(port or 0)
    if metric == "http_url":
        u = str(data.get("url") or "").strip()
        if not re.match(r"^https?://[^\s\"'<>]{1,500}$", u):
            errors["url"] = "ใส่ URL เช่น http://{host}:8080/health ({host} = Host ของเครื่อง)"
        rule["url"] = u
    if metric == "http_local":
        u = str(data.get("url") or "").strip()
        if u and not re.match(r"^https?://", u):
            u = "http://" + u
        if not re.match(r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?(/[^\s\"'<>]*)?$", u):
            errors["url"] = "ใส่ URL บนเครื่อง เช่น http://localhost:8080/health (Windows ตรวจ http://127.0.0.1:<พอร์ต>/)"
        rule["url"] = u
    if metric == "docker":
        cn = str(data.get("container") or "").strip().lstrip("/")
        if not CONTAINER_RE.match(cn):
            errors["container"] = "ใส่ชื่อ container (ตามที่เห็นใน docker ps)"
        rule["container"] = cn
    if metric in STATE_METRICS:
        rule["level"] = "warning" if data.get("level") == "warning" else "error"
        rule["error" if rule["level"] == "error" else "warning"] = 1
    if metric == "promql":
        q = str(data.get("promql") or "").strip()
        if not q or len(q) > 2000:
            errors["promql"] = "ใส่ PromQL (ไม่เกิน 2000 ตัวอักษร) ใช้ $labels แทนตัวเลือกเครื่อง"
        rule["promql"] = q
        u = str(data.get("unit") or "").strip()[:10]
        rule["unit"] = u

    rule["targets"] = [str(x).strip() for x in (data.get("targets") or []) if str(x).strip()][:200]
    rule["groups"] = [str(x).strip().strip("/") for x in (data.get("groups") or []) if str(x).strip().strip("/")][:50]
    os_ = str(data.get("os") or "any")
    rule["os"] = os_ if os_ in ("any", "windows", "linux") else "any"

    if metric not in STATE_METRICS:
        op = str(data.get("op") or meta[3])
        rule["op"] = "<" if op == "<" else ">"
        w = _num(data.get("warning"), "warning", errors)
        e = _num(data.get("error"), "error", errors)
        if w is None and e is None and "warning" not in errors and "error" not in errors:
            errors["warning"] = "ใส่ warning หรือ error อย่างน้อย 1 ค่า"
        if w is not None and e is not None:
            if rule["op"] == ">" and w >= e:
                errors["warning"] = "warning ต้องน้อยกว่า error (ค่ามากยิ่งแย่)"
            if rule["op"] == "<" and w <= e:
                errors["warning"] = "warning ต้องมากกว่า error (ค่าน้อยยิ่งแย่)"
        if w is not None:
            rule["warning"] = w
        if e is not None:
            rule["error"] = e

    f = _num(data.get("for_minutes"), "for_minutes", errors, 0.5, 1440)
    rule["for_minutes"] = f if f is not None else 3
    rule["notify"] = data.get("notify", True) is not False
    # ช่อง Teams ของกฎนี้ (ว่าง = เก็บค่าเดิม / ใช้ช่องหลัก · "-" = ลบ)
    tu = str(data.get("teams_url") or "").strip()
    if tu == "-":
        tu = ""
    elif tu:
        tu = notify_service._clean_url(tu, errors, "teams_url") or ""
    else:
        tu = old_url
    if tu:
        rule["teams_url"] = tu
    msg = str(data.get("message") or "").strip()
    if len(msg) > 300:
        errors["message"] = "ไม่เกิน 300 ตัวอักษร"
    rule["message"] = msg
    return rule, errors


def save_rules(payload):
    """payload: {rules: [...]} (ทั้งรายการ)  -> (ok, ผล | {error, fields})"""
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    items = (payload or {}).get("rules")
    if not isinstance(items, list) or len(items) > 200:
        return False, {"error": "rules ต้องเป็นรายการ (ไม่เกิน 200 กฎ)"}
    old = {str(r.get("id")): str(r.get("teams_url") or "")
           for r in targets_service._plain(_doc().get("alert_rules") or []) if isinstance(r, dict)}
    out, fields, ids = [], {}, set()
    for i, item in enumerate(items):
        rid = str((item or {}).get("id") or "").strip() or None
        if rid and (rid in ids or not re.match(r"^[a-z0-9-]{1,60}$", rid)):
            rid = None
        rule, errors = clean_rule(item, ids, keep_id=rid, old_url=old.get(rid or "", ""))
        for k, v in errors.items():
            fields[f"rules.{i}.{k}"] = v
        ids.add(rule["id"])
        out.append(rule)
    if fields:
        return False, {"error": "ค่าไม่ถูกต้อง", "fields": fields}

    y = targets_service._yaml()
    with targets_service._locked():
        doc = targets_service._load_doc(y)
        if not out:
            doc.pop("alert_rules", None)
        else:
            seq = CommentedSeq()
            for r in out:
                node = CommentedMap()
                for k, v in r.items():
                    if isinstance(v, list):
                        v2 = CommentedSeq(v)
                        v2.fa.set_flow_style()
                        node[k] = v2
                    else:
                        node[k] = v
                seq.append(node)
            doc["alert_rules"] = seq
        targets_service._write_doc(y, doc)
    return True, list_rules()


def preview(data):
    """ทดลองกฎกับทุกเครื่องตอนนี้ (ให้ puller คำนวณด้วยโค้ดเดียวกับตอนตรวจจริง)"""
    rule, errors = clean_rule(data, keep_id="preview")
    if errors:
        return 400, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
    try:
        return puller_get("/rule-preview", {"rule": json.dumps(rule, ensure_ascii=False)}, timeout=60)
    except Exception as e:
        return 502, {"error": f"ติดต่อ puller ไม่ได้: {e}"}


def send_test(data):
    """ส่งการ์ดตัวอย่างของกฎนี้ไป Teams (ช่องของกฎ หรือช่องหลัก) -> (status, ผล)"""
    rid = str((data or {}).get("id") or "")
    old = {str(r.get("id")): str(r.get("teams_url") or "")
           for r in targets_service._plain(_doc().get("alert_rules") or []) if isinstance(r, dict)}
    rule, errors = clean_rule(data, keep_id=rid or "test", old_url=old.get(rid, ""))
    if errors:
        return 400, {"error": "ค่าไม่ถูกต้อง", "fields": errors}
    url = rule.get("teams_url") or notify_service.main_url()
    if not url:
        return 400, {"error": "ยังไม่มีช่อง Teams: ใส่ Teams webhook ของกฎนี้ หรือตั้งช่องหลักในการ์ด Notifications"}
    meta = METRICS.get(rule["metric"], ("", "", False, ">", "any"))
    unit = rule.get("unit", "") if rule["metric"] == "promql" else meta[1]
    level = "error" if rule.get("error") is not None else "warning"
    lim = rule.get("error") if level == "error" else rule.get("warning")
    machine = (rule.get("targets") or ["เครื่องตัวอย่าง"])[0]
    if rule["metric"] in STATE_METRICS:
        text = rule["name"]
        value = ""
    else:
        value = lim
        text = f"{rule['name']} {lim}{unit} ({'ต่ำกว่า' if rule.get('op') == '<' else 'เกณฑ์'} {lim}{unit})"
    icon = "🔴" if level == "error" else "🟡"
    ctx = {"icon": icon, "machine": machine, "group": (rule.get("groups") or [""])[0], "host": "", "sensor": rule["name"],
           "text": text, "value": value, "unit": unit, "threshold": lim, "level": level.upper(), "time": "", "duration": ""}
    tpl = rule.get("message") or "{icon} {machine} · {text}"
    title = re.sub(r"\{(\w+)\}", lambda m: str(ctx.get(m.group(1), m.group(0)) or ""), tpl).strip()
    th = [f"{n} {'≤' if rule.get('op') == '<' else '≥'} {rule[x]}{unit}" for n, x in (("Warning", "warning"), ("Error", "error"))
          if rule.get(x) is not None and rule["metric"] not in STATE_METRICS]
    facts = [("Device", " ".join([machine] + ([f"[{ctx['group']}]"] if ctx["group"] else []))),
             ("Sensor", rule["name"]), ("Status", f"{level.upper()} – {text}"),
             ("Last Value", f"{value} {unit}".strip() if value not in ("", None) else ""),
             ("Threshold", " · ".join(th)),
             ("Message", title.lstrip("🔴🟡🟢 ") if rule.get("message") else ""),
             ("Time", __import__("datetime").datetime.now().astimezone().strftime("%d/%m/%Y %H:%M:%S"))]
    ok, msg = notify_service.post(url, notify_service.fact_card(
        f"[ทดสอบ] Central Monitor Notification: {machine} [{rule['name']}] {level.upper()}",
        "attention" if level == "error" else "warning", facts, f"[ทดสอบ] {title}"))
    where = "ช่องของกฎนี้" if rule.get("teams_url") else "ช่องหลัก"
    return (200, {"ok": True, "message": f"ส่งไป{where}แล้ว ({msg})"}) if ok else (502, {"error": msg})
