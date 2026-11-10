"""
agent_auth.py - ลงทะเบียนเครื่อง Windows แบบคำสั่งบรรทัดเดียว + token เฉพาะเครื่อง
==================================================================================

ขั้นตอน
    1) หน้า Settings (central-api) กด "สร้างคำสั่งติดตั้ง" -> enroll code ใช้ได้ 24 ชม.
       คำสั่ง = PowerShell 1 บรรทัด ตรวจใบรับรองของ central ด้วย thumbprint (กันเครื่องปลอม) แล้วโหลดสคริปต์
    2) เครื่อง Windows เรียก central-agent-enroll (:4319 HTTPS) ด้วย code
       -> ได้ token เฉพาะเครื่อง + ค่าตั้ง (log / Event channel) + ใบรับรอง + ไฟล์ติดตั้ง (Fluent Bit, windows_exporter)
    3) agent ถามค่าตั้งทุก 5 นาที (/agent/config + token) ค่าเปลี่ยน -> เขียน config ใหม่เอง

ไฟล์ (data/agent-auth/)
    tokens.json   {เครื่อง: {token, created, last_sync, host}}       (central-api / enroll เขียน)
    enroll.json   {code: {web, central, exp, used_by}}
    tokens.txt    token ที่ central-agent-gw ยอมรับ บรรทัดละ 1 ตัว (สร้างจาก tokens.json + token กลางเดิม)
                  gateway (bearertokenauth filename) อ่านใหม่เองเมื่อไฟล์เปลี่ยน
    settings.json {legacy: true}  ยอมรับ CENTRAL_AGENT_TOKEN (เครื่องที่ติดตั้งแบบเดิม) ไหม
"""

import fcntl
import hashlib
import json
import os
import secrets
import ssl
import time
from contextlib import contextmanager

AUTH_DIR = os.getenv("CENTRAL_AGENT_AUTH_DIR", "/data/agent-auth")
CERT = os.getenv("CENTRAL_AGENT_CA", "/run/agent-tls/cert.pem")
ENROLL_PORT = os.getenv("CENTRAL_ENROLL_PORT", "4319")
AGENT_PORT = os.getenv("CENTRAL_AGENT_PORT", "4318")
CODE_TTL = 24 * 3600
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"      # ไม่มี 0/O/1/I สับสน


def _path(name):
    return os.path.join(AUTH_DIR, name)


@contextmanager
def _lock():
    os.makedirs(AUTH_DIR, exist_ok=True)
    with open(_path(".lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def _read(name, default):
    try:
        with open(_path(name)) as f:
            v = json.load(f)
        return v if isinstance(v, type(default)) else default
    except (OSError, ValueError):
        return default


def _write(name, data):
    tmp = _path(name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _path(name))


def legacy_token():
    return os.getenv("CENTRAL_AGENT_TOKEN", "")


def legacy_enabled():
    return _read("settings.json", {}).get("legacy", True) is not False


def write_gateway_file():
    """tokens.txt ของ central-agent-gw  เขียนทับในไฟล์เดิม (ไม่ rename) ให้ตัวเฝ้าไฟล์ของ gateway เห็นการเปลี่ยน"""
    lines = []
    if legacy_enabled() and legacy_token():
        lines.append(f"{legacy_token()} # legacy CENTRAL_AGENT_TOKEN")
    for web, t in sorted(_read("tokens.json", {}).items()):
        if t.get("token"):
            lines.append(f"{t['token']} # {web}")
    if not lines:                              # ว่าง = gateway ปฏิเสธทุกอย่าง แต่ต้องมีอย่างน้อย 1 บรรทัด
        lines.append(secrets.token_hex(24) + " # placeholder")
    text = "\n".join(lines) + "\n"
    p = _path("tokens.txt")
    try:
        with open(p) as f:
            if f.read() == text:
                return
    except OSError:
        pass
    with open(p, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(p, 0o644)                         # gateway รันด้วย uid 10001 (โฟลเดอร์ 0711 ไม่ให้ list)
    os.chmod(AUTH_DIR, 0o711)


def init():
    """เรียกตอน central-api / enroll เริ่ม: ให้ tokens.txt ตรงกับ token กลางใน .env ปัจจุบัน"""
    try:
        with _lock():
            write_gateway_file()
    except OSError as e:
        print(f"[AGENT-AUTH] init: {e!r}")


def thumbprint():
    """SHA1 ของใบรับรอง :4318/:4319 (PowerShell: $cert.GetCertHashString())"""
    try:
        with open(CERT) as f:
            der = ssl.PEM_cert_to_DER_cert(f.read())
    except (OSError, ValueError):
        return ""
    return hashlib.sha1(der).hexdigest().upper()


def ca_pem():
    try:
        with open(CERT) as f:
            return f.read()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# enroll code (central-api)
# ---------------------------------------------------------------------------

def _new_code():
    raw = "".join(secrets.choice(CODE_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def create_code(web, central):
    now = time.time()
    with _lock():
        codes = {c: v for c, v in _read("enroll.json", {}).items() if v.get("exp", 0) > now}
        code = _new_code()
        codes[code] = {"web": web, "central": central, "exp": now + CODE_TTL, "used_by": None}
        _write("enroll.json", codes)
    return code, now + CODE_TTL


def one_liner(central, code):
    tp = thumbprint()
    base = f"https://{central}:{ENROLL_PORT}"
    # Windows PowerShell 5.1 เท่านั้น (PowerShell 7 ไม่ใช้ ServerCertificateValidationCallback -> ตรวจใบรับรองไม่ได้)
    return ("if($PSVersionTable.PSEdition -eq 'Core'){throw 'Use Windows PowerShell 5.1 (powershell.exe), not PowerShell 7'};"
            "[Net.ServicePointManager]::SecurityProtocol='Tls12';"
            f"[Net.ServicePointManager]::ServerCertificateValidationCallback={{param($s,$c)$c.GetCertHashString() -eq '{tp}'}};"
            f"$w=New-Object Net.WebClient;$w.Proxy=$null;$w.Encoding=[Text.Encoding]::UTF8;"
            f"iex $w.DownloadString('{base}/agent/i/{code}')")


def token_info(web):
    t = _read("tokens.json", {}).get(web)
    if not t:
        return None
    return {"created": t.get("created"), "last_sync": t.get("last_sync"), "host": t.get("host") or ""}


def revoke(web):
    with _lock():
        toks = _read("tokens.json", {})
        found = toks.pop(web, None) is not None
        _write("tokens.json", toks)
        codes = _read("enroll.json", {})
        codes = {c: v for c, v in codes.items() if v.get("web") != web}
        _write("enroll.json", codes)
        write_gateway_file()
    return found


def set_legacy(enabled):
    with _lock():
        s = _read("settings.json", {})
        s["legacy"] = bool(enabled)
        _write("settings.json", s)
        write_gateway_file()


# ---------------------------------------------------------------------------
# ฝั่ง Windows (central-agent-enroll)
# ---------------------------------------------------------------------------

def code_entry(code):
    v = _read("enroll.json", {}).get(str(code or "").upper())
    if not v or v.get("exp", 0) < time.time():
        return None
    return v


def register(code, host):
    """ใช้ code -> token เฉพาะเครื่อง  ใช้ซ้ำได้จากเครื่องเดิม (รันคำสั่งซ้ำ) ภายในอายุของ code
    -> (web, token) หรือ (None, ข้อความ)"""
    code = str(code or "").upper()
    host = str(host or "")[:64]
    with _lock():
        codes = _read("enroll.json", {})
        v = codes.get(code)
        if not v or v.get("exp", 0) < time.time():
            return None, "code หมดอายุหรือไม่ถูกต้อง สร้างคำสั่งใหม่ในหน้า Settings"
        if v.get("used_by") and v["used_by"] != host:
            return None, f"code นี้ถูกใช้แล้วโดยเครื่อง {v['used_by']} สร้างคำสั่งใหม่ในหน้า Settings"
        web = v["web"]
        toks = _read("tokens.json", {})
        cur = toks.get(web) or {}
        if v.get("used_by") == host and cur.get("code") == code:
            token = cur["token"]                                   # รันคำสั่งเดิมซ้ำ -> token เดิม
        else:
            token = secrets.token_hex(24)                          # ติดตั้งใหม่ -> token เดิมของเครื่องนี้ใช้ไม่ได้แล้ว
            toks[web] = {"token": token, "created": time.time(), "host": host, "code": code, "last_sync": None}
        v["used_by"] = host
        codes[code] = v
        _write("enroll.json", codes)
        _write("tokens.json", toks)
        write_gateway_file()
    return web, token


def web_of_token(token):
    if not token:
        return None
    for web, t in _read("tokens.json", {}).items():
        if t.get("token") and secrets.compare_digest(t["token"], token):
            return web
    return None


def touch_sync(web):
    with _lock():
        toks = _read("tokens.json", {})
        if web in toks:
            toks[web]["last_sync"] = time.time()
            _write("tokens.json", toks)


def config_of(target):
    """ค่าตั้งที่ agent ใช้ + hash (agent เทียบ hash แล้วเขียน config ใหม่เมื่อเปลี่ยน)"""
    a = (target or {}).get("agent") or {}
    cfg = {"name": target.get("name"), "logs": [str(x) for x in a.get("logs") or []],
           "channels": [str(x) for x in a.get("channels") or ["System", "Application"]]}
    cfg["hash"] = hashlib.sha1(json.dumps([cfg["logs"], cfg["channels"]], ensure_ascii=False).encode()).hexdigest()[:16]
    return cfg
