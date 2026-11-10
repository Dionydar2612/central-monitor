"""
custom_rules.py - กฎแจ้งเตือนที่ผู้ใช้สร้างเอง (หน้า Settings > Custom alert rules)
==================================================================================

puller.yaml
    alert_rules:
      - id: disk-d-host-window          # สร้างให้อัตโนมัติจากชื่อ
        name: Disk D ของ host-window
        enabled: true
        metric: disk                    # ดู METRICS ด้านล่าง หรือ promql
        mount: "D:"                     # (disk / disk_free_gb / disk_latency) เว้นว่าง = ทุกไดรฟ์
        service: ""                     # (service) ชื่อ service ของ Windows
        promql: ""                      # (promql) ใช้ $labels แทนตัวเลือกเครื่อง เช่น node_load1{$labels}
        unit: ""                        # (promql) หน่วยที่แสดง
        targets: [host-window]          # เว้นว่าง = ทุกเครื่อง
        groups: []                      # เฉพาะกลุ่ม (รวมกลุ่มย่อย)
        os: any                         # any / windows / linux
        op: ">"                         # ">" ยิ่งมากยิ่งแย่ · "<" ยิ่งน้อยยิ่งแย่
        warning: 85
        error: 95                       # ใส่อย่างใดอย่างหนึ่งก็ได้
        for_minutes: 10                 # ต้องเกินต่อเนื่องนานเท่านี้ถึงแจ้ง
        notify: true                    # ส่ง Microsoft Teams
        message: ""                     # ข้อความบนการ์ดของกฎนี้ (ว่าง = ใช้แบบทั้งระบบ) เช่น "{icon} {machine} · D: เหลือ {value}{unit}"

แต่ละกฎ = sensor ชนิด "custom" ของแต่ละเครื่อง (และแต่ละไดรฟ์ ถ้าเป็นกฎของ disk)
-> สถานะ / ประวัติปัญหา / Acknowledge / System log / Teams เหมือน sensor ปกติ
ไม่มีข้อมูลของเครื่องนั้น -> ไม่สร้าง sensor (ไม่เดา)
"""

import math

INTERVAL = 30

# metric ที่เลือกได้  (key -> ชื่อ, หน่วย, ต่อไดรฟ์?, ทิศ ปริยาย, OS)
METRICS = {
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
    "promql": ("PromQL เอง", "", False, ">", "any"),
}
_EXPR_KEY = {"cpu": "cpu", "memory": "memory", "mem_available": "mem_available", "disk": "disk",
             "disk_free_gb": "disk_free_gb", "disk_latency": "disk_latency_vec", "io_util": "disk_busy",
             "cpu_queue": "cpu_queue_per_core", "net_util": "net_util", "tcp_retx": "tcp_retx"}


def rules(config):
    out = []
    for r in config.get("alert_rules") or []:
        if isinstance(r, dict) and r.get("id") and r.get("enabled", True) is not False and r.get("metric") in METRICS:
            out.append(r)
    return out


def applies(rule, target, windows):
    os_ = str(rule.get("os") or "any")
    if os_ == "windows" and not windows or os_ == "linux" and windows:
        return False
    if METRICS[rule["metric"]][4] == "windows" and not windows:
        return False
    names = [str(x) for x in rule.get("targets") or []]
    groups = [str(x).strip("/") for x in rule.get("groups") or [] if str(x).strip("/")]
    if not names and not groups:
        return True
    if target.get("name") in names:
        return True
    g = str(target.get("group") or "")
    return any(g == x or g.startswith(x + "/") for x in groups)


def _num(v):
    try:
        v = float(v)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _confirm(rule):
    f = _num(rule.get("for_minutes"))
    return max(1, math.ceil((f if f is not None else 3) * 60 / INTERVAL))


def _status(value, rule, prev, hyst):
    """ระดับจากค่า + hysteresis (ต้องพ้นเกณฑ์ - hyst ก่อนลดระดับ)  op "<" = ยิ่งต่ำยิ่งแย่"""
    sign = -1 if rule.get("op") == "<" else 1
    w, e = _num(rule.get("warning")), _num(rule.get("error"))

    def over(th, margin=0.0):
        return th is not None and value * sign >= th * sign - margin

    if over(e) or (prev == "error" and over(e, hyst)):
        return "error"
    if over(w) or (prev in ("warning", "error") and over(w, hyst)):
        return "warning"
    return "ok"


def _fmt(v):
    return f"{v:.0f}" if abs(v) >= 100 else f"{v:.1f}".rstrip("0").rstrip(".") if abs(v) >= 1 else f"{v:.2f}"


def _values(rule, ex, labels, name, windows, query, ping_exprs):
    """-> [(ส่วนต่อท้ายชื่อ เช่น "C:" หรือ None, ค่า, PromQL)]"""
    m = rule["metric"]
    if m == "promql":
        q = str(rule.get("promql") or "").replace("$labels", labels)
        rows = query(q) or []
        vals = [v for _, v in rows]
        if not vals:
            return []
        return [(None, min(vals) if rule.get("op") == "<" else max(vals), q)]
    if m in ("packet_loss", "net_latency"):
        pq = ping_exprs(name)
        rows = query(pq[m]) or []
        return [(None, rows[0][1], pq[m])] if rows else []
    if m == "service":
        svc = str(rule.get("service") or "").strip()
        if not svc:
            return []
        q = f"windows_service_state{{{labels}}} == 1"
        state = None
        for lb, _v in query(q) or []:
            n = lb.get("service") or lb.get("name") or ""
            if n.lower() == svc.lower():
                state = lb.get("state")
                break
        if state is None:
            return []                        # ไม่มี service นี้บนเครื่อง
        return [(None, 0.0 if state == "running" else 1.0, q)]
    expr = ex.get(_EXPR_KEY.get(m, ""))
    if not expr:
        return []
    rows = query(expr) or []
    if METRICS[m][2]:                        # ต่อไดรฟ์
        want = str(rule.get("mount") or "").strip().lower()
        seen, out = set(), []
        for lb, v in sorted(rows, key=lambda r: r[0].get("mountpoint", "")):
            mt = lb.get("mountpoint") or lb.get("device") or "?"
            if mt in seen or (want and mt.lower().rstrip("\\/") != want.rstrip("\\/")):
                continue
            seen.add(mt)
            out.append((mt, v, expr))
        return out
    return [(None, rows[0][1], expr)] if rows else []


def evaluate(target, rule_list, ex, labels, windows, prev_status, hyst, base, query, ping_exprs):
    """sensor ของกฎที่ใช้กับเครื่องนี้"""
    name = target["name"]
    out = []
    for r in rule_list:
        if not applies(r, target, windows):
            continue
        meta = METRICS[r["metric"]]
        unit = str(r.get("unit") or "") if r["metric"] == "promql" else meta[1]
        try:
            vals = _values(r, ex, labels, name, windows, query, ping_exprs)
        except Exception as e:
            print(f"[RULE ERROR] {r.get('id')} {name}: {e!r}")
            continue
        for part, value, q in vals:
            sid = f"{name}:rule:{r['id']}" + (f":{part}" if part else "")
            label = str(r.get("name") or r["id"]) + (f" ({part})" if part and not r.get("mount") else "")
            if r["metric"] == "service":
                level = "error" if _num(r.get("error")) is not None or _num(r.get("warning")) is None else "warning"
                st = level if value >= 1 else "ok"
                msg = f"Service {r.get('service')} {'หยุดทำงาน' if value >= 1 else 'ทำงานอยู่'}"
                shown, warning, error = None, None, None
            else:
                st = _status(value, r, prev_status(sid), hyst)
                cmp = "<" if r.get("op") == "<" else ">"
                lim = r.get("error") if st == "error" else r.get("warning") if st == "warning" else None
                msg = f"{_fmt(value)}{unit}" + (f" ({cmp} {lim}{unit})" if lim is not None else "")
                shown, warning, error = value, _num(r.get("warning")), _num(r.get("error"))
            out.append({**base, "id": sid, "kind": "custom", "name": label, "status": st,
                        "value": None if shown is None else round(shown, 3), "unit": unit, "peak": None,
                        "message": msg, "mount": part, "query": q, "low_is_bad": r.get("op") == "<",
                        "warning": warning, "error": error,
                        "confirm": _confirm(r), "notify": r.get("notify", True) is not False,
                        "template": str(r.get("message") or "") or None,
                        "details": {"rule": r["id"], "metric": r["metric"]}})
    return out
