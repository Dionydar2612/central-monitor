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
        process: ""                     # (process / process_cpu / process_mem) ชื่อโปรแกรม เช่น sqlservr (ไม่ต้องมี .exe)
        port: 0                         # (port_listen / tcp_port) หมายเลขพอร์ต
        url: ""                         # (http_url) เช่น http://{host}:8080/health  ({host} = Host ของเครื่อง)
                                        # (http_local) เช่น http://localhost:8080/health  ตรวจจากในเครื่องเอง
        container: ""                   # (docker) ชื่อ container
        level: error                    # (กฎแบบ "หยุด / ไม่ตอบ") ระดับเมื่อผิดปกติ: error / warning
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

import json
import math
import re
import shlex
import subprocess
import urllib.parse
import socket
import ssl
import time
import urllib.error
import urllib.request

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
_EXPR_KEY = {"cpu": "cpu", "memory": "memory", "mem_available": "mem_available", "disk": "disk",
             "disk_free_gb": "disk_free_gb", "disk_latency": "disk_latency_vec", "io_util": "disk_busy",
             "cpu_queue": "cpu_queue_per_core", "net_util": "net_util", "tcp_retx": "tcp_retx"}


# กฎแบบ "หยุด / ไม่ตอบ": ไม่มีเกณฑ์ตัวเลข ผิดปกติ = ระดับที่เลือก (level)
STATE_METRICS = ("service", "process", "port_listen", "tcp_port", "http_url", "http_local", "docker")
TCP_TIMEOUT = 5
HTTP_TIMEOUT = 10


def proc_regex(name):
    """ชื่อโปรแกรม -> regex ของ label process ใน windows_exporter (ไม่มี .exe, ไม่สนตัวพิมพ์)"""
    n = str(name or "").strip()
    if n.lower().endswith(".exe"):
        n = n[:-4]
    rx = "(?i)" + re.sub(r"([.\\+*?^$()\[\]{}|])", r"\\\1", n)
    return rx.replace("\\", "\\\\").replace('"', '\\"')        # อยู่ใน "..." ของ PromQL -> \ ต้องเป็น \\


# ---------------------------------------------------------------------------
# URL ของ Monitor เว็บ: normalize + เลือกโหมดจาก host (ต้องตรงกับ web_dashboard/backend/alert_rules_service.py)
#   "8090" / ":8090" / "localhost:8090" -> http://localhost:8090
#   host = localhost / 127.0.0.1 / ::1 -> ตรวจบนเครื่อง (http_local)   อย่างอื่น -> ตรวจจาก central (http_url)
# ---------------------------------------------------------------------------
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
PROBE_STALE = 300               # ผล cm-probe เก่ากว่านี้ = agent อาจหยุดทำงาน
PROBE_FRESH = 120               # series ที่ไม่มีค่าใหม่เกินนี้ (เทียบกับตัวล่าสุด) = พอร์ตนั้นหายไปแล้ว


def normalize_url(value):
    v = str(value or "").strip()
    if re.match(r"^:?\d{1,5}$", v):
        v = "localhost:" + v.lstrip(":")
    elif v.startswith(":") and re.match(r"^:\d{1,5}(/.*)?$", v):
        v = "localhost" + v
    if v and not re.match(r"^[a-z][a-z0-9+.-]*://", v, re.I):
        v = "http://" + v
    return v


def url_host(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def url_port(url):
    """พอร์ตที่ระบุใน URL (ไม่ระบุ / ผิด -> None)"""
    try:
        return urllib.parse.urlsplit(url).port
    except ValueError:
        return None


def is_local_url(url):
    return url_host(url) in LOCAL_HOSTS


def web_metric(url):
    return "http_local" if is_local_url(url) else "http_url"


def migrate_rule(r):
    """Monitor เว็บที่บันทึกแบบเก่า (URL ไม่มี http:// / เลือกโหมดผิด) -> แบบใหม่ ตอนโหลด (ไม่แก้ไฟล์)"""
    if r.get("metric") in ("http_local", "http_url") and r.get("url"):
        url = normalize_url(r["url"])
        r = dict(r, url=url, metric=web_metric(url))
    return r


def up_code(code):
    """2xx / 3xx = ทำงาน · 4xx / 5xx / 0 (ไม่ตอบ) = ไม่ทำงาน"""
    return 200 <= int(code or 0) < 400


def probe_ports(query, labels, keep=PROBE_FRESH):
    """ผล cm-probe ล่าสุดของเครื่อง Windows -> [{port, scheme, code, ms}] (เฉพาะ series ที่ยังส่งมาอยู่)"""
    stamps = {(lb.get("port"), lb.get("scheme")): ts for lb, ts in query(f"timestamp(cm_http_local_code{{{labels}}})") or []}
    if not stamps:
        return []
    newest = max(stamps.values())
    codes = {(lb.get("port"), lb.get("scheme")): v for lb, v in query(f"cm_http_local_code{{{labels}}}") or []}
    ms = {(lb.get("port"), lb.get("scheme")): v for lb, v in query(f"cm_http_local_ms{{{labels}}}") or []}
    out = []
    for k, ts in stamps.items():
        if ts < newest - keep or k not in codes or not str(k[0] or "").isdigit():
            continue
        out.append({"port": int(k[0]), "scheme": k[1] or "http", "code": int(codes[k]),
                    "ms": round(ms[k]) if k in ms else None})
    return sorted(out, key=lambda x: x["port"])


def ports_text(ports, limit=12):
    web = [str(p["port"]) for p in ports if p["code"]]
    return (", ".join(web[:limit]) + (" ..." if len(web) > limit else "")) if web else "ไม่พบเว็บบนเครื่อง"


def local_web_state(url, query, labels, now=None, rule=None):
    """Monitor เว็บโหมดบนเครื่อง (Windows / cm-probe) -> (ผิดปกติ?, ข้อความ, ms, หน่วย, ที่มา)
    ผิดปกติ = None -> ยังบอกไม่ได้ (สถานะ unknown) เช่น agent ยังไม่ส่ง / ผลเก่า / URL ไม่มีพอร์ต"""
    now = now or time.time()
    q = f"max_over_time(cm_probe_timestamp_seconds{{{labels}}}[1d])"
    rows = query(q) or []
    if not rows:
        return (None, "agent ยังไม่ส่งผลตรวจเว็บ · ติดตั้ง agent ซ้ำ (Settings > เครื่อง > Download zip + Make command)",
                None, "", q)
    age = now - rows[0][1]
    if age > PROBE_STALE:
        return None, f"ผลตรวจจากเครื่องล่าสุด {int(age // 60)} นาทีที่แล้ว · ตรวจว่า agent ยังทำงานอยู่", None, "", q
    ports = probe_ports(query, labels)
    port = url_port(url)
    if port is None:
        return None, f"{url} ไม่ได้ระบุพอร์ต · แก้ไข Monitor แล้วเลือกพอร์ต (พอร์ตที่พบ: {ports_text(ports)})", None, "", q
    hit = next((p for p in ports if p["port"] == port), None)
    q = f'cm_http_local_code{{{labels},port="{port}"}}'
    if hit is None:
        return True, f"ไม่พบเว็บที่พอร์ต {port} บนเครื่องนี้ · พอร์ตที่พบ: {ports_text(ports)}", None, "", q
    return web_result(url, hit["code"], hit["ms"], rule) + (q, {"code": hit["code"], "ms": hit["ms"]})


def web_result(url, code, ms, rule=None, body=None, ssl_days_left=None):
    """ข้อความ + สถานะจากรหัส HTTP (แสดงรหัสเสมอ) -> (ผิดปกติ, ข้อความ, ms, หน่วย)
    ผิดปกติ: True = ไม่ทำงาน · "warning" = ทำงานแต่ช้า / SSL ใกล้หมดอายุ · False = ปกติ
    rule (ตั้งค่าเพิ่มเติม): keyword = คำที่ต้องมีในหน้า · slow_ms = เตือนเมื่อตอบช้ากว่า · ssl_days = เตือน SSL ก่อนหมดอายุ"""
    rule = rule or {}
    code = int(code or 0)
    ms_t = f" ({ms:.0f} ms)" if ms is not None else ""
    shown, unit = (round(ms, 1) if ms is not None else None), ("ms" if ms is not None else "")
    if not code:
        return True, f"{url} ไม่ตอบ (HTTP 0)", None, ""
    msg = f"{url} ตอบ HTTP {code}{ms_t}"
    if not up_code(code):
        return True, msg, shown, unit
    kw = str(rule.get("keyword") or "").strip()
    if kw and body is not None and kw.lower() not in body.lower():
        return True, f"{msg} · ไม่พบคำ \"{kw}\" ในหน้า", shown, unit
    if ssl_days_left is not None and ssl_days_left < 0:
        return True, f"{msg} · SSL หมดอายุแล้ว", shown, unit
    notes = []
    slow = _num(rule.get("slow_ms"))
    if slow and ms is not None and ms >= slow:
        notes.append(f"ช้ากว่า {slow:.0f} ms")
    sd = _num(rule.get("ssl_days"))
    if sd and ssl_days_left is not None and ssl_days_left <= sd:
        notes.append(f"SSL หมดอายุใน {ssl_days_left} วัน")
    if notes:
        return "warning", f"{msg} · " + " · ".join(notes), shown, unit
    return False, msg, shown, unit


_ssl_cache = {}
SSL_TTL = 3600


def _ssl_days_left(url):
    """วันที่เหลือก่อนใบรับรอง HTTPS หมดอายุ (cache 1 ชม.)  อ่านไม่ได้ -> None"""
    u = urllib.parse.urlsplit(url)
    if u.scheme != "https" or not u.hostname:
        return None
    key = (u.hostname, u.port or 443)
    hit = _ssl_cache.get(key)
    if hit and time.time() - hit[0] < SSL_TTL:
        return hit[1]
    try:
        import web_checks
        days = web_checks.ssl_info(u.hostname, u.port or 443, 5).get("days_left")
    except Exception as e:
        print(f"[RULE SSL] {url}: {e!r}")
        days = None
    _ssl_cache[key] = (time.time(), days)
    return days


def rules(config):
    out = []
    for r in config.get("alert_rules") or []:
        if isinstance(r, dict) and r.get("id") and r.get("enabled", True) is not False and r.get("metric") in METRICS:
            out.append(migrate_rule(r))
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
    if m in ("process_cpu", "process_mem"):
        name = str(rule.get("process") or "").strip()
        if not name:
            return []
        sel = f'{labels},process=~"{proc_regex(name)}"'
        if m == "process_cpu":
            cores = f'count(count by(core) ({{__name__=~"windows_cpu_time_total|windows_cpu_processor_utility_total",{labels}}}))'
            q = (f'sum(rate({{__name__=~"windows_process_cpu_time(_seconds)?_total",{sel}}}[2m])) * 100 / scalar({cores})')
        else:
            q = f"sum(windows_process_working_set_private_bytes{{{sel}}}) / 1048576"
        rows = query(q) or []
        return [(None, rows[0][1], q)] if rows else []
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


# ---------------------------------------------------------------------------
# เครื่อง Linux: docker + เว็บ localhost ผ่าน SSH (1 คำสั่งต่อเครื่อง ทุก ~1 นาที)
# เครื่อง Windows: cm-probe.ps1 บนเครื่อง (ทุก 1 นาที) -> cm_docker_container / cm_http_local_code (Prometheus)
# ---------------------------------------------------------------------------
PROBE_TTL = 55
_probe_cache = {}


def parse_probe(out):
    """ผลคำสั่ง probe ของ Linux -> {containers: {ชื่อ: {...}}, docker_error, http: {url: (code, ms)}}"""
    res = {"containers": {}, "docker_error": None, "http": {}}
    part = None
    for line in out.splitlines():
        if line.startswith("##"):
            part = line.strip("#").strip()
            continue
        if part == "DOCKER" and line.strip():
            if line.startswith("{"):
                try:
                    c = json.loads(line)
                except ValueError:
                    continue
                for n in str(c.get("Names") or "").split(","):
                    if n:
                        res["containers"][n.strip().lstrip("/")] = {"state": str(c.get("State") or "").lower(),
                                                                    "status": c.get("Status") or "", "image": c.get("Image") or ""}
            elif not res["containers"]:
                res["docker_error"] = line.strip()[:200]
        elif part == "HTTP" and "\t" in line:
            url, _, rest = line.partition("\t")
            bits = rest.split()
            try:
                res["http"][url] = (int(bits[0]), float(bits[1]) * 1000)
            except (IndexError, ValueError):
                res["http"][url] = (0, 0.0)
    return res


def linux_probe(target, rules, runner=None):
    """docker ps + curl localhost บนเครื่อง Linux (ผ่าน SSH) cache ~1 นาทีต่อเครื่อง"""
    name = target.get("name")
    urls = sorted({str(r.get("url") or "") for r in rules if r.get("metric") == "http_local" and r.get("url")})
    want_docker = any(r.get("metric") == "docker" for r in rules)
    key = (name, tuple(urls), want_docker)
    hit = _probe_cache.get(key)
    if hit and time.time() - hit[0] < PROBE_TTL:
        return hit[1]
    cmd = []
    if want_docker:
        cmd.append("echo '##DOCKER'; (docker ps -a --format '{{json .}}' 2>&1 || sudo -n docker ps -a --format '{{json .}}' 2>&1) | head -500")
    if urls:
        cmd.append("echo '##HTTP'")
        for u in urls:
            q = shlex.quote(u)
            cmd.append(f"printf '%s\\t' {q}; curl -sk -o /dev/null -m 5 -w '%{{http_code}} %{{time_total}}\\n' {q} 2>/dev/null || echo '000 0'")
    try:
        if runner is None:
            from ssh_client import _build_command
            out = subprocess.run(_build_command(target, "; ".join(cmd)), capture_output=True, timeout=40).stdout.decode(errors="replace")
        else:
            out = runner(target, "; ".join(cmd))
        res = parse_probe(out)
    except Exception as e:
        print(f"[RULE PROBE] {name}: {e!r}")
        res = None
    _probe_cache[key] = (time.time(), res)
    return res


def _container_state(c):
    if not c:
        return True, "ไม่พบ container"
    st = c.get("state") or ""
    status = c.get("status") or ""
    bad = st != "running" or "unhealthy" in status.lower()
    return bad, (status or st)


def _state(rule, target, labels, query, windows=True, rules=None):
    """กฎแบบ "หยุด / ไม่ตอบ" -> (ผิดปกติ?, ข้อความ, ค่าที่แสดง, หน่วย, ที่มา) หรือ None = ไม่มีข้อมูล (ไม่สร้าง sensor)"""
    m = rule["metric"]
    if m == "service":
        svc = str(rule.get("service") or "").strip()
        if not svc:
            return None
        q = f"windows_service_state{{{labels}}} == 1"
        state = None
        for lb, _v in query(q) or []:
            n = lb.get("service") or lb.get("name") or ""
            if n.lower() == svc.lower():
                state = lb.get("state")
                break
        if state is None:
            return None                      # ไม่มี service นี้บนเครื่อง
        return state != "running", f"Service {svc} {'ทำงานอยู่' if state == 'running' else 'หยุดทำงาน (' + str(state) + ')'}", None, "", q
    if m == "process":
        name = str(rule.get("process") or "").strip()
        if not name:
            return None
        # มี metric ของ process ของเครื่องนี้ไหม (ไม่มีเลย = agent ไม่ส่ง / ไม่มี collector process -> ไม่เดา)
        if not query(f"count(windows_process_working_set_private_bytes{{{labels}}})"):
            return None
        q = f'count(windows_process_working_set_private_bytes{{{labels},process=~"{proc_regex(name)}"}})'
        rows = query(q) or []
        n = int(rows[0][1]) if rows else 0
        return n == 0, (f"{name} ทำงานอยู่ ({n} process)" if n else f"{name} ไม่ทำงาน"), n, "", q
    if m == "port_listen":
        port = int(rule.get("port") or 0)
        if not query(f"cm_inventory_timestamp_seconds{{{labels}}}"):
            return None                      # ไม่มีข้อมูลพอร์ตจากเครื่อง (agent รุ่นเก่า)
        q = f'cm_port_listening{{{labels},port="{port}"}}'
        rows = query(q) or []
        who = sorted({lb.get("process") or "?" for lb, _ in rows})
        return not rows, (f"พอร์ต {port} เปิดรับอยู่ ({', '.join(who)})" if rows else f"พอร์ต {port} ไม่ได้เปิดรับ"), \
            None, "", q
    if m == "docker":
        cname = str(rule.get("container") or "").strip().lstrip("/")
        if not cname:
            return None
        if windows:
            if not query(f"cm_probe_timestamp_seconds{{{labels}}}"):
                return None                  # agent รุ่นเก่า / ไม่มี cm-probe
            q = f"cm_docker_container{{{labels}}}"
            found = None
            for lb, _v in query(q) or []:
                if str(lb.get("name") or "").lower() == cname.lower():
                    found = {"state": str(lb.get("state") or "").lower(), "status": lb.get("status") or ""}
                    break
            if found is None and not query(f"cm_docker_up{{{labels}}} == 1"):
                return None                  # เครื่องนี้ไม่มี docker / อ่านไม่ได้
        else:
            p = linux_probe(target, rules or [rule])
            if not p or (p.get("docker_error") and not p.get("containers")):
                return None
            q = "ssh: docker ps -a"
            found = p["containers"].get(cname) or next((v for k, v in p["containers"].items() if k.lower() == cname.lower()), None)
        bad, st = _container_state(found)
        return bad, f"{cname}: {st}", None, "", q
    if m == "http_local":
        url = normalize_url(rule.get("url"))
        if not url:
            return None
        if windows:
            return local_web_state(url, query, labels, rule=rule)
        p = linux_probe(target, rules or [rule])
        if not p or url not in p["http"]:
            return None
        code, ms = p["http"][url]
        return web_result(url, code, ms, rule) + (f"ssh: curl {url}", {"code": code, "ms": ms})
    host = str(target.get("host") or "").strip()
    if m == "tcp_port":
        port = int(rule.get("port") or 0)
        if not host or not port:
            return None
        t0 = time.time()
        try:
            socket.create_connection((host, port), timeout=TCP_TIMEOUT).close()
        except OSError as e:
            return True, f"ต่อ {host}:{port} ไม่ได้ ({e.strerror or e.__class__.__name__})", None, "", f"tcp://{host}:{port}"
        ms = (time.time() - t0) * 1000
        return False, f"ต่อ {host}:{port} ได้ ({ms:.0f} ms)", round(ms, 1), "ms", f"tcp://{host}:{port}"
    if m == "http_url":
        url = normalize_url(rule.get("url"))
        if "{host}" in url:
            if not host:
                return None
            url = url.replace("{host}", host)
        if not url:
            return None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE        # เว็บภายในมักใช้ใบรับรองของตัวเอง (ตรวจแค่ว่าตอบไหม)
        t0 = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "central-monitor"})
            opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), _NoRedirect)
            body = None
            with opener.open(req, timeout=HTTP_TIMEOUT) as r:
                code = r.status
                if rule.get("keyword"):
                    body = r.read(512 * 1024).decode(r.headers.get_content_charset() or "utf-8", "replace")
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:
            reason = getattr(e, "reason", None) or e.__class__.__name__
            return True, f"{url} ไม่ตอบ (HTTP 0 · {reason})", None, "", url
        ms = (time.time() - t0) * 1000
        days = _ssl_days_left(url) if rule.get("ssl_days") and up_code(code) else None
        return web_result(url, code, ms, rule, body, days) + (url, {"code": code, "ms": round(ms), "ssl_days_left": days})
    return None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """ไม่ตาม redirect: 3xx = เว็บตอบแล้ว (เหมือน cm-probe บนเครื่อง)"""
    def redirect_request(self, *a, **k):
        return None


def evaluate(target, rule_list, ex, labels, windows, prev_status, hyst, base, query, ping_exprs):
    """sensor ของกฎที่ใช้กับเครื่องนี้"""
    name = target["name"]
    out = []
    mine = [r for r in rule_list if applies(r, target, windows)]
    for r in mine:
        meta = METRICS[r["metric"]]
        unit = str(r.get("unit") or "") if r["metric"] == "promql" else meta[1]
        try:
            if r["metric"] in STATE_METRICS:
                res = _state(r, target, labels, query, windows, mine)
                vals = [] if res is None else [(None, res[:4], res[4], res[5] if len(res) > 5 else {})]
            else:
                vals = [(p, v, q, {}) for p, v, q in _values(r, ex, labels, name, windows, query, ping_exprs)]
        except Exception as e:
            print(f"[RULE ERROR] {r.get('id')} {name}: {e!r}")
            continue
        for part, value, q, extra in vals:
            sid = f"{name}:rule:{r['id']}" + (f":{part}" if part else "")
            label = str(r.get("name") or r["id"]) + (f" ({part})" if part and not r.get("mount") else "")
            if r["metric"] in STATE_METRICS:
                level = r.get("level") if r.get("level") in ("warning", "error") else \
                    "error" if _num(r.get("error")) is not None or _num(r.get("warning")) is None else "warning"
                bad, msg, shown, sunit = value
                st = "unknown" if bad is None else "warning" if bad == "warning" else level if bad else "ok"
                if r["metric"] in ("http_local", "http_url") and bad is not None:
                    extra = dict(extra or {}, up=bad is not True)          # ช้า / SSL ใกล้หมด = ยังทำงาน
                unit = sunit
                warning, error = None, None
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
                        "details": {"rule": r["id"], "metric": r["metric"], **(extra or {})}})
    return out
