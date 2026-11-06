"""
web_checks.py - เช็คเว็บ (uptime) + วันหมดอายุของใบรับรอง SSL
==============================================================

ทำงานเป็น thread แยกใน central-puller (ไม่ขึ้นกับรอบดึง log)
    ทุก TICK วินาที อ่าน puller.yaml -> checks: แล้วเช็คตัวที่ถึงเวลา (ตาม interval ของแต่ละตัว)
    ขนานกันสูงสุด MAX_WORKERS ตัว

เช็คอะไร (ต่อ 1 URL)
    - HTTP(S) GET ตาม redirect -> status code อยู่ใน expect_status ไหม, เจอ keyword ไหม, ใช้เวลากี่ ms
    - https: วันหมดอายุของใบรับรอง (เหลือกี่วัน), ผู้ออก, ใบรับรองถูกต้องไหม
    สถานะ
        up       ผ่านทุกข้อ
        warning  ผ่าน แต่ใบรับรองเหลือ <= ssl_warn_days วัน
        down     เข้าไม่ได้ / code ไม่ตรง / ไม่เจอ keyword / ใบรับรองไม่ถูกต้องหรือหมดอายุ

ผลลัพธ์ (หน้าเว็บอ่านผ่าน web_dashboard/backend/checks_service.py)
    data/state/checks.json          ผลล่าสุด + uptime 24 ชม.
    data/state/checks-history.json  ประวัติ 24 ชม. [เวลา, ผ่านไหม, ms] ใช้คำนวณ uptime
"""

import json
import os
import socket
import ssl
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlparse

from config import STATE_DIR, load_config
import notify

RESULTS_FILE = os.path.join(STATE_DIR, "checks.json")
HISTORY_FILE = os.path.join(STATE_DIR, "checks-history.json")

TICK = 10
MAX_WORKERS = 8
HISTORY_SECONDS = 86400
MAX_BODY = 1024 * 1024
USER_AGENT = "central-monitor-check/1.0"

DEFAULTS = {"interval": 60, "timeout": 10, "expect_status": "200-399", "ssl_warn_days": 14,
            "verify_tls": True, "keyword": ""}


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def status_matches(code, expect):
    """code อยู่ใน "200" / "200,301" / "200-399" ไหม"""
    for part in str(expect).split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            if int(lo) <= code <= int(hi):
                return True
        elif part and int(part) == code:
            return True
    return False


def _cert_fields(cert):
    def name(seq, key):
        for rdn in seq or []:
            for k, v in rdn:
                if k == key:
                    return v
        return None
    issuer = name(cert.get("issuer"), "organizationName") or name(cert.get("issuer"), "commonName")
    return cert.get("notAfter"), issuer, name(cert.get("subject"), "commonName")


def _decode_der(der):
    """อ่านใบรับรองที่ตรวจไม่ผ่าน (self-signed/หมดอายุ) เพื่อเอาวันหมดอายุ ใช้ตัวอ่านภายในของ Python"""
    pem = ssl.DER_cert_to_PEM_cert(der)
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as f:
        f.write(pem)
        path = f.name
    try:
        return ssl._ssl._test_decode_cert(path)      # noqa: SLF001 (มีใน CPython ทุกรุ่น)
    finally:
        os.unlink(path)


def ssl_info(host, port, timeout):
    """{expires_at, days_left, issuer, subject, valid, error}"""
    info = {"valid": True, "error": None}
    cert = None
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                cert = s.getpeercert()
    except ssl.SSLCertVerificationError as e:
        info["valid"] = False
        info["error"] = getattr(e, "verify_message", None) or str(e)
        try:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with socket.create_connection((host, port), timeout=timeout) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as s:
                    cert = _decode_der(s.getpeercert(binary_form=True))
        except Exception:
            cert = None
    except Exception as e:
        return {"valid": False, "error": str(e)}

    if cert:
        not_after, issuer, subject = _cert_fields(cert)
        if not_after:
            expires = ssl.cert_time_to_seconds(not_after)
            info["expires_at"] = _iso(expires)
            info["days_left"] = int((expires - time.time()) // 86400)
        info["issuer"] = issuer
        info["subject"] = subject
    return info


def run_check(c):
    """เช็ค 1 ตัว -> dict ผลลัพธ์"""
    url = c["url"]
    started = time.time()
    code, body, error = None, b"", None

    ctx = ssl.create_default_context()
    if not c["verify_tls"]:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=c["timeout"], context=ctx) as resp:
            code = resp.status
            if c["keyword"]:
                body = resp.read(MAX_BODY)
    except urllib.error.HTTPError as e:
        code = e.code
        if c["keyword"]:
            body = e.read(MAX_BODY) if e.fp else b""
    except urllib.error.URLError as e:
        error = str(getattr(e, "reason", e))
    except Exception as e:                       # timeout / reset / ...
        error = str(e) or type(e).__name__

    ms = round((time.time() - started) * 1000)
    ok, reason = True, None
    if code is None:
        ok, reason = False, error or "no response"
    elif not status_matches(code, c["expect_status"]):
        ok, reason = False, f"HTTP {code} (expected {c['expect_status']})"
    elif c["keyword"] and c["keyword"].encode("utf-8") not in body:
        ok, reason = False, f'keyword "{c["keyword"]}" not found'

    result = {"url": url, "code": code, "ms": ms, "error": reason, "checked_at": _iso(time.time())}

    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.hostname:
        cert = ssl_info(parsed.hostname, parsed.port or 443, c["timeout"])
        result["ssl"] = cert
        days = cert.get("days_left")
        if c["verify_tls"] and not cert.get("valid", True) and ok:
            ok, reason = False, f"SSL: {cert.get('error')}"
            result["error"] = reason
        if days is not None and days < 0:
            ok, result["error"] = False, "SSL certificate expired"

    if not ok:
        result["status"] = "down"
    elif result.get("ssl", {}).get("days_left") is not None and result["ssl"]["days_left"] <= c["ssl_warn_days"]:
        result["status"] = "warning"
        result["error"] = f"SSL expires in {result['ssl']['days_left']} days"
    else:
        result["status"] = "up"
    return result


def _normalize(raw):
    c = dict(DEFAULTS)
    c.update({k: v for k, v in raw.items() if v is not None})
    c["verify_tls"] = c.get("verify_tls", True) is not False
    c["keyword"] = str(c.get("keyword") or "")
    c["interval"] = max(30, int(c["interval"]))
    c["timeout"] = max(1, min(30, int(c["timeout"])))
    return c


def _safe(c):
    try:
        return c["name"], run_check(c)
    except Exception as e:
        return c["name"], {"status": "down", "error": f"check failed: {e}", "url": c.get("url"),
                           "checked_at": _iso(time.time())}


def _notify_change(c, r, notified, streak):
    """แจ้ง Microsoft Teams เมื่อสถานะเปลี่ยนและเห็นซ้ำ 2 รอบติด (กันเว็บสะดุดครั้งเดียว)"""
    name, st = c["name"], r.get("status")
    if st == notified.get(name):
        streak.pop(name, None)
        return
    prev_st, cnt = streak.get(name, (st, 0))
    cnt = cnt + 1 if prev_st == st else 1
    streak[name] = (st, cnt)
    if cnt < 2:
        return
    old = notified.get(name)
    notified[name] = st
    streak.pop(name, None)
    if old is None and st == "up":
        return                                # check ใหม่ที่ปกติ ไม่ต้องแจ้ง
    try:
        notify.check_event(name, c.get("url"), old, st, r, c.get("group") or "")
    except Exception as e:
        print(f"[NOTIFY ERROR] check {name}: {e!r}")


def _loop():
    last_run = {}
    history = _read_json(HISTORY_FILE) or {}
    results = (_read_json(RESULTS_FILE) or {}).get("checks", {})
    notified = {n: r.get("status") for n, r in results.items()}    # สถานะล่าสุดที่แจ้งไปแล้ว (เริ่มใหม่ไม่แจ้งซ้ำ)
    streak = {}

    while True:
        try:
            raw = load_config().get("checks") or []
            checks = [_normalize(c) for c in raw if isinstance(c, dict) and c.get("name") and c.get("url")]
            names = {c["name"] for c in checks}
            now = time.time()
            due = [c for c in checks
                   if not c.get("paused") and now - last_run.get(c["name"], 0) >= c["interval"]]

            changed = False
            if due:
                by_name = {c["name"]: c for c in due}
                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    for name, r in pool.map(_safe, due):
                        _notify_change(by_name[name], r, notified, streak)
                        last_run[name] = now
                        h = [x for x in history.get(name, []) if x[0] >= now - HISTORY_SECONDS]
                        h.append([int(now), r["status"] != "down", r.get("ms")])
                        history[name] = h
                        r["uptime_24h"] = round(sum(1 for x in h if x[1]) / len(h) * 100, 2)
                        r["checks_24h"] = len(h)
                        oks = [x[2] for x in h if x[1] and x[2] is not None]
                        r["avg_ms_24h"] = round(sum(oks) / len(oks)) if oks else None
                        results[name] = r
                changed = True

            # check ที่ถูกลบออกจาก puller.yaml -> ลบผลทิ้ง
            for name in [n for n in list(results) if n not in names]:
                results.pop(name, None)
                history.pop(name, None)
                changed = True

            if changed:
                _write_json(RESULTS_FILE, {"updated_at": _iso(now), "checks": results})
                _write_json(HISTORY_FILE, history)

        except Exception as e:
            print(f"[CHECKS ERROR] {e}")

        time.sleep(TICK)


def start():
    threading.Thread(target=_loop, daemon=True, name="web-checks").start()
    print("[CHECKS] web/SSL checker started")
