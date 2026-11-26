"""
winrm_client.py - ช่องทาง WinRM (HTTPS) ไปเครื่อง Windows แบบไม่ลง agent
=========================================================================

เชื่อมต่อ JEA endpoint "CentralMonitor" บนเครื่อง Windows ด้วยบัญชี cm-monitor (ไม่ใช่ admin)
แล้วเรียกได้เฉพาะฟังก์ชันอ่าน Get-CM* (agent/windows/CentralMonitor.psm1) ผลเป็น JSON string

ความปลอดภัย
    - HTTPS เสมอ + ตรวจ thumbprint (SHA-1 ของใบรับรอง = ค่า Thumbprint ใน Windows) ก่อนส่งรหัสทุกครั้ง
      ไม่ตรง -> ไม่เชื่อมต่อ (กันเครื่องปลอม) · ไม่ใช้ Basic / ไม่ส่งแบบไม่เข้ารหัส (NTLM + TLS)
    - ไม่มี add_script: เรียกได้แค่ cmdlet ตามชื่อ + พารามิเตอร์ (JEA ปฏิเสธอย่างอื่นอยู่แล้ว)
ไม่รบกวนเครื่องปลายทาง
    - 1 session ค้างไว้ต่อเครื่อง (ไม่เปิด PowerShell ใหม่ทุกรอบ = ไม่มี CPU กระตุก) เปิดใหม่ทุก 6 ชม.
    - timeout ต่อคำสั่ง · เครื่องฝั่งนั้นจำกัด RAM 150 MB / 5 session / idle 5 นาที (setup-cm-monitor) · ฝั่งนี้ตั้ง idle 3 นาที + no_profile
"""

import hashlib
import json
import socket
import ssl
import time

ENDPOINT = "CentralMonitor"
SESSION_MAX_AGE = 6 * 3600
# session ที่ central ทิ้งไว้โดยไม่ได้ปิด (central restart / เน็ตหลุด) ให้ Windows ปิดเองเมื่อไม่มีคำสั่ง 3 นาที
# (รอบปกติเรียกทุก 30 วิ จึงไม่หมดอายุระหว่างใช้งาน) -> ไม่ค้างจนเต็มโควตา MaxShellsPerUser
SHELL_IDLE_TIMEOUT = 180

try:
    from pypsrp.powershell import PowerShell, RunspacePool
    from pypsrp.wsman import WSMan
    HAVE_PYPSRP = True
except ImportError:          # image เก่าที่ยังไม่ได้ build ใหม่
    HAVE_PYPSRP = False


class WinRMError(Exception):
    pass


def norm_thumb(t):
    return "".join(ch for ch in str(t or "") if ch.isalnum()).upper()


def server_thumbprint(host, port, timeout=10):
    """SHA-1 ของใบรับรองที่เครื่องปลายทางส่งมา (ตัวพิมพ์ใหญ่ ไม่มีช่องว่าง) = Thumbprint ใน Windows"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE          # self-signed: ตรวจด้วย thumbprint แทน CA
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                der = tls.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError) as e:
        hint = ""
        if isinstance(e, (TimeoutError, ConnectionRefusedError, socket.timeout)):
            hint = (f" - เครื่องยังไม่มี WinRM HTTPS ที่พอร์ต {port} (ยังไม่ได้ติดตั้ง: กด ติดตั้งให้เครื่องนี้ ก่อน"
                    " ระบบตั้ง Thumbprint ให้เอง) หรือ firewall บล็อก")
        raise WinRMError(f"เชื่อมต่อ {host}:{port} ไม่ได้ ({e.__class__.__name__}: {e}){hint}") from e
    if not der:
        raise WinRMError(f"{host}:{port} ไม่ได้ส่งใบรับรองมา")
    return hashlib.sha1(der).hexdigest().upper()


class Session:
    """session WinRM 1 ตัวต่อเครื่อง (ไม่ thread-safe: ใช้จาก worker ของเครื่องนั้นเท่านั้น)"""

    def __init__(self, host, port, user, password, thumbprint, timeout=20, on_learn=None):
        self.host, self.port, self.user = host, int(port), user
        self.password, self.thumbprint = password, norm_thumb(thumbprint)
        self.timeout = int(timeout)
        self.pool = None
        self.opened = 0.0
        # ไม่มี thumbprint (ติดตั้งด้วย zip ที่ไม่ต้องคัดลอกอะไร): จำใบรับรองที่เห็นครั้งแรก "หลัง" ล็อกอินผ่าน
        # แล้วใช้ตรวจทุกครั้งต่อไป (trust on first use) -> on_learn(thumbprint) เก็บไว้ที่ cm_secrets
        self.on_learn = on_learn

    @property
    def is_open(self):
        return self.pool is not None

    def open(self):
        if not HAVE_PYPSRP:
            raise WinRMError("puller image ไม่มี pypsrp - รัน cm-update --rebuild")
        if not self.thumbprint and self.on_learn is None:
            raise WinRMError("ยังไม่ได้ตั้ง Certificate thumbprint ของเครื่องนี้ (Settings > การเชื่อมต่อ > Scan certificate)")
        if not self.password:
            raise WinRMError("ยังไม่ได้ใส่รหัสผ่านของ cm-monitor (Settings > การเชื่อมต่อ)")
        self.close()
        got = server_thumbprint(self.host, self.port)
        learning = not self.thumbprint
        if learning:
            self.thumbprint = got
        if got != self.thumbprint:
            raise WinRMError(f"ใบรับรองไม่ตรง: เครื่องส่ง {got} แต่ตั้งไว้ {self.thumbprint} - ไม่เชื่อมต่อ "
                             "(ถ้าติดตั้งใหม่ ให้ Scan certificate อีกครั้ง)")
        wsman = WSMan(
            self.host, port=self.port, ssl=True, auth="ntlm", encryption="auto",
            username=self.user, password=self.password, cert_validation=False,
            connection_timeout=10, operation_timeout=self.timeout, read_timeout=self.timeout + 10,
        )
        # no_profile: ไม่โหลด / สร้าง profile ของ cm-monitor (เบากว่า และไม่ทิ้ง C:\\Users\\cm-monitor ไว้บนเครื่อง)
        pool = RunspacePool(wsman, configuration_name=ENDPOINT, idle_timeout=SHELL_IDLE_TIMEOUT, no_profile=True)
        try:
            pool.open()
        except Exception as e:  # noqa: BLE001
            if learning:
                self.thumbprint = ""
            raise WinRMError(_friendly(e)) from e
        self.pool = pool
        self.opened = time.time()
        if learning and self.on_learn:
            self.on_learn(got)

    def close(self):
        if self.pool is not None:
            try:
                self.pool.close()
            except Exception:  # noqa: BLE001
                pass
        self.pool = None

    def expired(self):
        return self.pool is not None and time.time() - self.opened > SESSION_MAX_AGE

    def call(self, cmd, params=None):
        """เรียก Get-CM* 1 ครั้ง คืน dict (JSON ที่ฟังก์ชันคืนมา)"""
        if self.pool is None or self.expired():
            self.open()
        ps = PowerShell(self.pool)
        ps.add_cmdlet(cmd)
        for k, v in (params or {}).items():
            if v is True:
                ps.add_parameter(k)
            elif v is not None and v is not False:
                ps.add_parameter(k, v)
        try:
            out = ps.invoke()
        except Exception as e:  # noqa: BLE001
            self.close()
            raise WinRMError(_friendly(e)) from e
        if ps.had_errors:
            msg = "; ".join(str(x) for x in ps.streams.error)[:500] or "error"
            raise WinRMError(f"{cmd}: {msg}")
        text = "".join(str(o) for o in out if o is not None)
        try:
            return json.loads(text) if text else {}
        except ValueError as e:
            raise WinRMError(f"{cmd}: คำตอบไม่ใช่ JSON ({text[:120]!r})") from e


def _friendly(e):
    s = f"{e.__class__.__name__}: {e}"
    low = s.lower()
    if "concurrent shells" in low or "maximum number of" in low and "shell" in low:
        return ("มี session ของ cm-monitor ค้างบนเครื่องเกินโควตา (เช่นหลัง central restart) - จะหมดอายุเองภายในไม่กี่นาที"
                " (หรือรัน setup-cm-monitor.bat ซ้ำเพื่อ restart WinRM)")
    if "401" in s or "unauthorized" in low or "logon failure" in low or "access is denied" in low:
        return "รหัสผ่าน / user ไม่ถูกต้อง หรือ cm-monitor ไม่มีสิทธิ์เข้า endpoint CentralMonitor (รัน check-cm-monitor.bat)"
    if "configurationname" in low or "endpoint" in low and "not" in low or "microsoft.powershell" in low:
        return "ไม่พบ endpoint CentralMonitor บนเครื่องนี้ (รัน setup-cm-monitor.bat)"
    if "timed out" in low or "timeout" in low:
        return "หมดเวลารอ (เครื่องช้า / firewall) " + s[:160]
    if "refused" in low:
        return "พอร์ตถูกปฏิเสธ (WinRM HTTPS listener ไม่ได้เปิด หรือ firewall บล็อก)"
    return s[:300]


def test_connection(host, port, user, password, thumbprint):
    """ทดสอบจากหน้า Settings: เชื่อมต่อ + เรียก Get-CMVersion / Get-CMInventory แล้วปิด"""
    s = Session(host, port, user, password, thumbprint, timeout=30)
    t0 = time.time()
    try:
        s.open()
        ver = s.call("Get-CMVersion")
        inv = s.call("Get-CMInventory")
        return {"ok": True, "seconds": round(time.time() - t0, 2), "module": ver.get("v"), "computer": ver.get("host"),
                "ps": ver.get("ps"), "os": (inv.get("os") or {}).get("caption"), "build": (inv.get("os") or {}).get("build"),
                "session_mb": (ver.get("self") or {}).get("ws_mb"), "priority": (ver.get("self") or {}).get("priority")}
    finally:
        s.close()
